from __future__ import annotations

import base64
import os
from typing import TYPE_CHECKING

import httpx2
import pytest
import typer
from typer.testing import CliRunner

from simplefin_aggregator import cli
from simplefin_aggregator.provider_access_urls import load_access_urls, provider_creds_path
from simplefin_aggregator.provider_registry import KNOWN_PROVIDERS


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from typer.testing import Result

runner = CliRunner()

PROVIDER_KEY = "my-bank"
PROVIDER_LABEL = "My Bank"
PROVIDER_ROOT = "https://provider.example.com/simplefin"
CLAIM_URL = f"{PROVIDER_ROOT}/claim/some-setup-token"
SETUP_TOKEN = base64.b64encode(CLAIM_URL.encode("ascii")).decode("ascii")
PROVIDER_PASSWORD = "s3cret-provider-password"  # noqa: S105
# Derived, so that changing the password cannot leave the leak assertions
# below testing for a string no URL in this file contains.
ACCESS_URL = f"https://user:{PROVIDER_PASSWORD}@provider.example.com/simplefin"

# The config's own custom provider entry is offered after the built-in ones.
MENU_CHOICE = str(len(KNOWN_PROVIDERS) + 1)

CONFIG_TOML = f"""
base_url = "http://127.0.0.1:9999"
[[custom_providers]]
key = "{PROVIDER_KEY}"
label = "{PROVIDER_LABEL}"
root = "{PROVIDER_ROOT}"

[[providers]]
key = "{PROVIDER_KEY}"
"""


def _write_config(tmp_path: Path, contents: str = CONFIG_TOML) -> Path:
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(contents)
    config_path.chmod(0o600)
    return config_path


def _install_provider(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx2.Request], httpx2.Response]
) -> list[str]:
    """Answer the claim POST with `handler`, returning the list of URLs it is asked for."""
    requested: list[str] = []

    def recording_handler(request: httpx2.Request) -> httpx2.Response:
        requested.append(str(request.url))
        return handler(request)

    def build_client() -> httpx2.Client:
        return httpx2.Client(
            transport=httpx2.MockTransport(recording_handler), follow_redirects=False
        )

    monkeypatch.setattr(cli, "_build_claim_client", build_client)
    return requested


def _responds(
    status: int, text: str = "", headers: Mapping[str, str] | None = None
) -> Callable[[httpx2.Request], httpx2.Response]:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, text=text, headers=headers)

    return handler


def _run_claim(
    tmp_path: Path,
    token: str = SETUP_TOKEN,
    *,
    config: str = CONFIG_TOML,
    choice: str = MENU_CHOICE,
) -> Result:
    """Invoke `claim` through the provider menu, then feed it the token."""
    _ = _write_config(tmp_path, config)
    return runner.invoke(
        cli.app, ["claim", "--config-dir", str(tmp_path)], input=f"{choice}\n{token}\n"
    )


def _run_claim_with_provider(tmp_path: Path, provider: str, token: str = SETUP_TOKEN) -> Result:
    """Invoke `claim --provider`, which skips the menu, then feed it the token."""
    _ = _write_config(tmp_path, CONFIG_TOML)
    return runner.invoke(
        cli.app,
        ["claim", "--provider", provider, "--config-dir", str(tmp_path)],
        input=f"{token}\n",
    )


def _stored(tmp_path: Path) -> dict[str, str]:
    return {
        key: secret.get_secret_value()
        for key, secret in load_access_urls(provider_creds_path(tmp_path)).items()
    }


@pytest.fixture
def claim_succeeds(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    return _install_provider(monkeypatch, _responds(200, ACCESS_URL))


def test_claim_stores_the_access_url_under_the_selected_providers_key(
    tmp_path: Path, claim_succeeds: list[str]
) -> None:
    result = _run_claim(tmp_path, SETUP_TOKEN)

    assert result.exit_code == 0
    assert claim_succeeds == [CLAIM_URL]
    assert _stored(tmp_path) == {PROVIDER_KEY: ACCESS_URL}


@pytest.mark.usefixtures("claim_succeeds")
def test_claim_does_not_print_the_access_url_it_stored(tmp_path: Path) -> None:
    result = _run_claim(tmp_path, SETUP_TOKEN)

    assert result.exit_code == 0
    assert PROVIDER_PASSWORD not in result.output
    assert PROVIDER_KEY in result.stdout
    assert "warning" not in result.stderr


@pytest.mark.usefixtures("claim_succeeds")
def test_claim_offers_the_built_in_providers_alongside_the_configured_one(tmp_path: Path) -> None:
    result = _run_claim(tmp_path, SETUP_TOKEN)

    assert result.exit_code == 0
    for provider in KNOWN_PROVIDERS:
        assert f"{provider.label} ({provider.root.origin_and_path})" in result.stdout
    assert f"{PROVIDER_LABEL} ({PROVIDER_ROOT}/)" in result.stdout


@pytest.mark.usefixtures("claim_succeeds")
def test_claim_does_not_echo_the_token_typed_at_a_real_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prompt hides what is typed when stdin is a real terminal.

    CliRunner's stdin is never a real terminal, so the check is faked here to
    exercise that branch.
    """
    monkeypatch.setattr(cli, "_stdin_is_a_terminal", lambda: True)

    result = _run_claim(tmp_path)

    assert result.exit_code == 0
    assert SETUP_TOKEN not in result.output


def test_claim_does_not_hide_the_token_when_stdin_is_not_a_terminal(
    tmp_path: Path, claim_succeeds: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hiding unconditionally would break reading the token from a redirected file.

    Checked against the prompt call itself, not echoed output: CliRunner's
    stand-in for a visible (non-hidden) prompt echoes unconditionally to let
    tests see it, but a real terminal's own line discipline is what would
    actually echo a typed value, and there is no terminal here to reproduce
    that distinction. (Confirmed by hand: reading via plain `input()` from a
    redirected file, as this branch does, prints only the prompt text, never
    the value -- so there is nothing for a real run to leak either way.)
    """
    hide_input_seen: list[bool] = []

    def fake_prompt(_text: str, *, hide_input: bool = False) -> str:
        hide_input_seen.append(hide_input)
        return SETUP_TOKEN

    monkeypatch.setattr(typer, "prompt", fake_prompt)

    result = _run_claim_with_provider(tmp_path, PROVIDER_KEY)

    assert result.exit_code == 0
    assert hide_input_seen == [False]
    assert claim_succeeds == [CLAIM_URL]


@pytest.mark.usefixtures("claim_succeeds")
def test_claim_leaves_other_providers_in_the_store_alone(tmp_path: Path) -> None:
    other = "https://user:other-password@beta-bridge.simplefin.org/simplefin"
    _ = provider_creds_path(tmp_path).write_text(
        f'{{"access_urls": {{"simplefin-bridge": "{other}"}}}}'
    )

    result = _run_claim(tmp_path, SETUP_TOKEN)

    assert result.exit_code == 0
    assert _stored(tmp_path) == {"simplefin-bridge": other, PROVIDER_KEY: ACCESS_URL}


def test_claim_fails_on_an_invalid_config_without_reaching_the_provider(
    tmp_path: Path, claim_succeeds: list[str]
) -> None:
    result = _run_claim(tmp_path, SETUP_TOKEN, config="this is not [valid toml")

    assert result.exit_code == 1
    assert claim_succeeds == []
    assert "error" in result.stderr


def test_claim_fails_on_an_unreadable_store_before_spending_the_token(
    tmp_path: Path, claim_succeeds: list[str]
) -> None:
    """A store that cannot be read must fail while the token is still claimable."""
    _ = provider_creds_path(tmp_path).write_text("{not json")

    result = _run_claim(tmp_path, SETUP_TOKEN)

    assert result.exit_code == 1
    assert claim_succeeds == []
    assert "malformed JSON" in result.stderr


@pytest.mark.skipif(
    os.name == "posix" and os.geteuid() == 0,
    reason="root writes a directory whatever its mode says",
)
def test_claim_fails_on_an_unwritable_config_directory_before_spending_the_token(
    tmp_path: Path, claim_succeeds: list[str]
) -> None:
    """The store is written only after the POST, so its directory is checked before it."""
    config_dir = tmp_path / "read-only"
    config_dir.mkdir(mode=0o700)
    _ = _write_config(config_dir)
    # Still readable, so the config loads and the store reads as empty; it is
    # the writability check that has to catch this.
    config_dir.chmod(0o500)

    result = runner.invoke(cli.app, ["claim", "--config-dir", str(config_dir)])

    assert result.exit_code == 1
    assert claim_succeeds == []
    assert "is not writable" in result.stderr


@pytest.mark.usefixtures("claim_succeeds")
def test_claim_warns_when_no_providers_entry_names_the_claimed_key(tmp_path: Path) -> None:
    """Serve looks the store up by the key its config names, not by what was claimed."""
    # Only the [[providers]] key -- the last one in the file -- so the custom
    # provider stays claimable while nothing in config refers to it.
    head, _, tail = CONFIG_TOML.rpartition(f'key = "{PROVIDER_KEY}"')
    config = f'{head}key = "simplefin-bridge"{tail}'

    result = _run_claim(tmp_path, SETUP_TOKEN, config=config)

    assert result.exit_code == 0
    assert _stored(tmp_path) == {PROVIDER_KEY: ACCESS_URL}
    assert f"no [[providers]] entry in {tmp_path / 'config.toml'} names" in result.stderr
    assert PROVIDER_KEY in result.stderr


def test_claim_rejects_a_menu_choice_outside_the_offered_range(
    tmp_path: Path, claim_succeeds: list[str]
) -> None:
    result = _run_claim(tmp_path, SETUP_TOKEN, choice=str(len(KNOWN_PROVIDERS) + 2))

    assert result.exit_code == 1
    assert claim_succeeds == []
    assert "enter a number" in result.stderr


def test_claim_rejects_a_menu_choice_that_is_not_a_number(
    tmp_path: Path, claim_succeeds: list[str]
) -> None:
    result = _run_claim(tmp_path, SETUP_TOKEN, choice="my-bank")

    assert result.exit_code == 1
    assert claim_succeeds == []
    assert "enter a number" in result.stderr


def test_claim_with_invalid_base64_fails(tmp_path: Path, claim_succeeds: list[str]) -> None:
    result = _run_claim(tmp_path, "not valid base64!!")

    assert result.exit_code == 1
    assert claim_succeeds == []
    assert "base64" in result.stderr


def test_claim_accepts_a_token_that_strict_base64_would_reject(
    tmp_path: Path, claim_succeeds: list[str]
) -> None:
    """Matching the reference implementation's plain b64decode, which ignores stray characters.

    A space rather than a newline: the token arrives as one line read from
    stdin, and a newline embedded in it is not something a real paste can
    produce.
    """
    result = _run_claim(tmp_path, f"{SETUP_TOKEN[:8]} {SETUP_TOKEN[8:]}")

    assert result.exit_code == 0
    assert claim_succeeds == [CLAIM_URL]


def test_claim_says_the_token_is_unspent_when_the_claim_url_is_unparseable(
    tmp_path: Path, claim_succeeds: list[str]
) -> None:
    """The advice has to hold for every rejection, not just a root mismatch."""
    result = _run_claim(tmp_path, base64.b64encode(b"not-a-url").decode("ascii"))

    assert result.exit_code == 1
    assert claim_succeeds == []
    assert "still unspent" in result.stderr


def test_claim_with_a_token_decoding_to_non_ascii_fails(
    tmp_path: Path, claim_succeeds: list[str]
) -> None:
    token = base64.b64encode("https://provider.example.com/ünicode".encode()).decode("ascii")

    result = _run_claim(tmp_path, token)

    assert result.exit_code == 1
    assert claim_succeeds == []
    assert "not ASCII" in result.stderr


def test_claim_rejects_a_claim_url_outside_the_selected_root_before_any_request(
    tmp_path: Path, claim_succeeds: list[str]
) -> None:
    lookalike = "https://provider.example.com.evil.test/simplefin/claim/some-setup-token"
    token = base64.b64encode(lookalike.encode("ascii")).decode("ascii")

    result = _run_claim(tmp_path, token)

    assert result.exit_code == 1
    assert claim_succeeds == [], "a hostile host must not learn the token is live"
    assert "https://provider.example.com.evil.test is not valid" in result.stderr
    assert "some-setup-token" not in result.output, "the token is still unclaimed"
    assert "still unspent" in result.stderr
    assert "[[custom_providers]]" in result.stderr
    assert str(tmp_path / "config.toml") in result.stderr


def test_claim_does_not_follow_a_redirect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    requested = _install_provider(
        monkeypatch, _responds(302, headers={"location": "https://attacker.example.net/claim"})
    )

    result = _run_claim(tmp_path, SETUP_TOKEN)

    assert result.exit_code == 1
    assert requested == [CLAIM_URL], "the setup token must not be replayed at the redirect target"
    assert "302" in result.stderr
    assert _stored(tmp_path) == {}


def test_claim_reports_a_rejected_token_as_possibly_compromised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested = _install_provider(
        monkeypatch, _responds(403, "claim token does not exist or was already used")
    )

    result = _run_claim(tmp_path, SETUP_TOKEN)

    assert result.exit_code == 1
    assert requested == [CLAIM_URL]
    assert "403" in result.stderr
    assert "revoked at the provider" in result.stderr
    assert "already used" not in result.output, "a provider's response body is not echoed"


def test_claim_with_unreachable_provider_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        msg = "connection refused"
        raise httpx2.ConnectError(msg, request=request)

    _ = _install_provider(monkeypatch, handler)

    result = _run_claim(tmp_path, SETUP_TOKEN)

    assert result.exit_code == 1
    assert "could not reach" in result.stderr
    assert _stored(tmp_path) == {}


@pytest.mark.parametrize(
    ("access_url", "expected"),
    [
        (f"https://user:{PROVIDER_PASSWORD}@other.example.com/simplefin", "not valid for provider"),
        ("https://provider.example.com/simplefin", "must contain credentials"),
        (f"{ACCESS_URL}#frag", "query string or fragment"),
        (f"{ACCESS_URL}?", "query string or fragment"),
    ],
    ids=["other-host", "no-credentials", "fragment", "trailing-question-mark"],
)
def test_claim_rejects_an_invalid_access_url_and_stores_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, access_url: str, expected: str
) -> None:
    _ = _install_provider(monkeypatch, _responds(200, access_url))

    result = _run_claim(tmp_path, SETUP_TOKEN)

    assert result.exit_code == 1
    assert expected in result.stderr
    assert _stored(tmp_path) == {}
    assert PROVIDER_PASSWORD not in result.output


def test_claim_tolerates_a_provider_that_ends_the_access_url_with_a_newline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = _install_provider(monkeypatch, _responds(200, f"{ACCESS_URL}\n"))

    result = _run_claim(tmp_path, SETUP_TOKEN)

    assert result.exit_code == 0
    assert _stored(tmp_path) == {PROVIDER_KEY: ACCESS_URL}


def test_claim_client_does_not_follow_redirects() -> None:
    # The other tests replace this factory, so only reaching into it checks the
    # setting the CLI actually runs with.
    with cli._build_claim_client() as claim_client:  # pyright: ignore[reportPrivateUsage]
        assert claim_client.follow_redirects is False


def test_naming_the_provider_skips_the_menu(tmp_path: Path, claim_succeeds: list[str]) -> None:
    """`--provider` is as deliberate an answer as choosing from the menu."""
    result = _run_claim_with_provider(tmp_path, PROVIDER_KEY)

    assert result.exit_code == 0
    assert "Which provider" not in result.stdout
    assert _stored(tmp_path) == {PROVIDER_KEY: ACCESS_URL}
    assert claim_succeeds == [CLAIM_URL]


def test_a_provider_no_entry_defines_is_refused_before_the_token_is_spent(
    tmp_path: Path, claim_succeeds: list[str]
) -> None:
    result = _run_claim_with_provider(tmp_path, "not-a-provider")

    assert result.exit_code == 1
    # Named as the reason: a key that resolved to the wrong provider would
    # also stop here, on the root the setup token fails to match.
    assert "unknown provider" in result.stderr
    assert claim_succeeds == []
    assert _stored(tmp_path) == {}


def test_a_provider_key_that_is_a_pasted_secret_does_not_come_back_on_stderr(
    tmp_path: Path, claim_succeeds: list[str]
) -> None:
    """A mistyped `--provider` is as likely to be a pasted setup token as `--key` is."""
    result = _run_claim_with_provider(tmp_path, SETUP_TOKEN)

    assert result.exit_code == 1
    assert SETUP_TOKEN not in result.stderr
    assert SETUP_TOKEN not in result.stdout
    assert claim_succeeds == []
