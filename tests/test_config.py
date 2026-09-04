from __future__ import annotations

import stat
from typing import TYPE_CHECKING

import pytest

from simplefin_aggregator.config import Config, ConfigError, load_config
from simplefin_aggregator.provider_allowlist import KNOWN_PROVIDERS


if TYPE_CHECKING:
    from pathlib import Path


VALID_TOML = """
bind_host = "127.0.0.2"
bind_port = 9999
base_url = "http://127.0.0.1:8080"
claim_token = "s3cret-claim-token"

[client]
username = "client-username"
password = "s3cret-password"

# Before [[providers]] so that a test can cut the providers off the end.
[[allowlist]]
slug = "my-bank"
label = "My Bank"
root = "https://provider.example.com/simplefin"

[[providers]]
provider_key = "my-bank"
"""

ROOT_LINE = 'root = "https://provider.example.com/simplefin"'


def _write(tmp_path: Path, contents: str) -> Path:
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(contents)
    config_path.chmod(0o600)
    return config_path


def test_load_config_parses_valid_file(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID_TOML)

    config = load_config(path)

    assert isinstance(config, Config)
    assert config.bind_host == "127.0.0.2"
    assert config.bind_port == 9999  # noqa: PLR2004
    assert len(config.providers) == 1
    assert config.providers[0].provider_key == "my-bank"
    assert config.client.username == "client-username"


def test_load_config_accepts_http_base_url_on_loopback_ipv6(tmp_path: Path) -> None:
    ok_toml = VALID_TOML.replace(
        'base_url = "http://127.0.0.1:8080"', 'base_url = "http://[::1]:8080"'
    )
    path = _write(tmp_path, ok_toml)

    config = load_config(path)

    assert config.base_url == "http://[::1]:8080"


def test_load_config_rejects_http_base_url_on_non_loopback_ip(tmp_path: Path) -> None:
    bad_toml = VALID_TOML.replace(
        'base_url = "http://127.0.0.1:8080"', 'base_url = "http://192.168.1.10:8080"'
    )
    path = _write(tmp_path, bad_toml)

    with pytest.raises(ConfigError, match="https"):
        _ = load_config(path)


def test_load_config_rejects_http_base_url_on_hostname(tmp_path: Path) -> None:
    bad_toml = VALID_TOML.replace(
        'base_url = "http://127.0.0.1:8080"', 'base_url = "http://localhost:8080"'
    )
    path = _write(tmp_path, bad_toml)

    with pytest.raises(ConfigError, match="https"):
        _ = load_config(path)


def test_load_config_accepts_https_base_url_on_non_loopback_host(tmp_path: Path) -> None:
    ok_toml = VALID_TOML.replace(
        'base_url = "http://127.0.0.1:8080"', 'base_url = "https://aggregator.example.com"'
    )
    path = _write(tmp_path, ok_toml)

    config = load_config(path)

    assert config.base_url == "https://aggregator.example.com"


def test_load_config_rejects_base_url_with_userinfo(tmp_path: Path) -> None:
    bad_toml = VALID_TOML.replace(
        'base_url = "http://127.0.0.1:8080"', 'base_url = "http://olduser:oldpass@127.0.0.1:8080"'
    )
    path = _write(tmp_path, bad_toml)

    with pytest.raises(ConfigError, match="base_url"):
        _ = load_config(path)


def test_load_config_rejects_base_url_with_username_only(tmp_path: Path) -> None:
    bad_toml = VALID_TOML.replace(
        'base_url = "http://127.0.0.1:8080"', 'base_url = "http://olduser@127.0.0.1:8080"'
    )
    path = _write(tmp_path, bad_toml)

    with pytest.raises(ConfigError, match="base_url"):
        _ = load_config(path)


def test_load_config_rejects_claim_token_with_slash(tmp_path: Path) -> None:
    bad_toml = VALID_TOML.replace(
        'claim_token = "s3cret-claim-token"', 'claim_token = "s3cret/claim/token"'
    )
    path = _write(tmp_path, bad_toml)

    with pytest.raises(ConfigError, match="claim_token"):
        _ = load_config(path)


def test_load_config_error_does_not_leak_rejected_claim_token_value(tmp_path: Path) -> None:
    bad_toml = VALID_TOML.replace(
        'claim_token = "s3cret-claim-token"', 'claim_token = "s3cret/claim/token"'
    )
    path = _write(tmp_path, bad_toml)

    with pytest.raises(ConfigError) as exc_info:
        _ = load_config(path)

    assert "s3cret/claim/token" not in str(exc_info.value)


def test_load_config_rejects_empty_claim_token(tmp_path: Path) -> None:
    bad_toml = VALID_TOML.replace('claim_token = "s3cret-claim-token"', 'claim_token = ""')
    path = _write(tmp_path, bad_toml)

    with pytest.raises(ConfigError, match="claim_token"):
        _ = load_config(path)


def test_load_config_accepts_url_safe_claim_token_characters(tmp_path: Path) -> None:
    ok_toml = VALID_TOML.replace(
        'claim_token = "s3cret-claim-token"', 'claim_token = "abc123._~-XYZ"'
    )
    path = _write(tmp_path, ok_toml)

    config = load_config(path)

    assert config.claim_token.get_secret_value() == "abc123._~-XYZ"


def test_load_config_rejects_malformed_toml(tmp_path: Path) -> None:
    path = _write(tmp_path, "this is not [valid toml")

    with pytest.raises(ConfigError, match="malformed TOML"):
        _ = load_config(path)


def test_load_config_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        _ = load_config(tmp_path / "does-not-exist.toml")


def test_load_config_rejects_zero_providers(tmp_path: Path) -> None:
    no_providers = VALID_TOML.split("[[providers]]", maxsplit=1)[0]
    path = _write(tmp_path, no_providers)

    with pytest.raises(ConfigError):
        _ = load_config(path)


def test_load_config_rejects_more_than_one_provider(tmp_path: Path) -> None:
    two_providers = (
        VALID_TOML
        + """
[[providers]]
provider_key = "redbark"
"""
    )
    path = _write(tmp_path, two_providers)

    with pytest.raises(ConfigError):
        _ = load_config(path)


def test_load_config_warns_on_permissive_file_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, VALID_TOML)
    path.chmod(0o644)

    _ = load_config(path)

    assert "chmod 600" in capsys.readouterr().err


def test_load_config_does_not_warn_on_owner_only_file_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, VALID_TOML)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    _ = load_config(path)

    assert capsys.readouterr().err == ""


def test_secrets_are_redacted_in_repr(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID_TOML)

    config = load_config(path)

    assert "s3cret-claim-token" not in repr(config)
    assert "s3cret-password" not in repr(config)


def test_provider_entries_are_the_built_in_ones_plus_the_config_s(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, VALID_TOML))

    entries = config.provider_entries()

    assert [entry.slug for entry in entries] == [
        *(entry.slug for entry in KNOWN_PROVIDERS),
        "my-bank",
    ]
    assert entries[-1].root.origin_and_path == "https://provider.example.com/simplefin/"


def test_load_config_accepts_a_provider_key_naming_a_built_in_provider(tmp_path: Path) -> None:
    """A built-in provider needs no allowlist entry of its own."""
    built_in_only = VALID_TOML.replace('provider_key = "my-bank"', 'provider_key = "redbark"')
    config = load_config(_write(tmp_path, built_in_only))

    assert config.providers[0].provider_key == "redbark"


def test_load_config_rejects_a_provider_key_no_entry_defines(tmp_path: Path) -> None:
    dangling = VALID_TOML.replace('provider_key = "my-bank"', 'provider_key = "no-such-bank"')
    path = _write(tmp_path, dangling)

    with pytest.raises(ConfigError, match="unknown provider 'no-such-bank'"):
        _ = load_config(path)


def test_load_config_rejects_an_allowlist_slug_that_shadows_a_built_in_provider(
    tmp_path: Path,
) -> None:
    shadowing = VALID_TOML.replace('slug = "my-bank"', 'slug = "redbark"').replace(
        'provider_key = "my-bank"', 'provider_key = "redbark"'
    )
    path = _write(tmp_path, shadowing)

    with pytest.raises(ConfigError, match="duplicate provider slug 'redbark'"):
        _ = load_config(path)


def test_load_config_rejects_a_malformed_allowlist_slug(tmp_path: Path) -> None:
    bad_slug = VALID_TOML.replace('slug = "my-bank"', 'slug = "My Bank"').replace(
        'provider_key = "my-bank"', 'provider_key = "My Bank"'
    )
    path = _write(tmp_path, bad_slug)

    with pytest.raises(ConfigError, match="must match"):
        _ = load_config(path)


ALLOWLIST_ROOT_CASES = [
    ("http://provider.example.com/simplefin", "must use https"),
    ("https://user:pass@provider.example.com/simplefin", "must not contain credentials"),
    ("https://provider.example.com/simplefin?x=1", "query string or fragment"),
    ("https://provider.example.com/simplefin#f", "query string or fragment"),
    ("/simplefin", "has no host"),
]


@pytest.mark.parametrize(("root", "expected"), ALLOWLIST_ROOT_CASES)
def test_load_config_rejects_a_bad_allowlist_root(tmp_path: Path, root: str, expected: str) -> None:
    bad_root = VALID_TOML.replace(ROOT_LINE, f'root = "{root}"')
    path = _write(tmp_path, bad_root)

    with pytest.raises(ConfigError, match=expected) as exc_info:
        _ = load_config(path)

    # The rejected root is reported through UrlValidationError's message, which
    # strips credentials; pydantic's own rendering of the input is suppressed.
    assert "user:pass" not in str(exc_info.value)


def test_load_config_accepts_a_loopback_http_allowlist_root(tmp_path: Path) -> None:
    """The one non-https root allowed: a self-hosted provider on the loopback interface."""
    loopback = VALID_TOML.replace(ROOT_LINE, 'root = "http://127.0.0.1:8081/simplefin"')

    config = load_config(_write(tmp_path, loopback))

    assert config.provider_entries()[-1].root.origin_and_path == "http://127.0.0.1:8081/simplefin/"
