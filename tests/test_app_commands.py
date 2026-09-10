"""Tests for the `app` commands that issue and manage client app credentials."""

from __future__ import annotations

import base64
import errno
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from simplefin_aggregator import cli
from simplefin_aggregator.app_tokens import (
    ClaimedAppToken,
    ClientCredentials,
    UnclaimedAppToken,
    app_tokens_path,
    claim_app_token,
    load_app_tokens,
    matches,
    update_app_tokens,
)


if TYPE_CHECKING:
    from datetime import datetime

    from typer.testing import Result

runner = CliRunner()

BASE_URL = "http://127.0.0.1:9999"
CONFIG_TOML = f"""
base_url = "{BASE_URL}"
[[providers]]
key = "redbark"
"""


def _write_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(CONFIG_TOML)
    config_path.chmod(0o600)


def _run(tmp_path: Path, *arguments: str) -> Result:
    return runner.invoke(cli.app, ["app", *arguments, "--config-dir", str(tmp_path)])


def _new(tmp_path: Path, key: str = "actual-budget", label: str = "Actual Budget") -> Result:
    return _run(tmp_path, "new", "--key", key, "--label", label)


def _claim_secret(result: Result) -> str:
    """The secret inside the setup token stdout carries."""
    claim_url = base64.b64decode(result.stdout.strip(), validate=True).decode("ascii")
    return claim_url.rsplit("/", maxsplit=1)[1]


def _unclaimed(tmp_path: Path, key: str = "actual-budget") -> UnclaimedAppToken:
    record = load_app_tokens(app_tokens_path(tmp_path))[key]
    assert isinstance(record, UnclaimedAppToken)
    return record


def _back_date(tmp_path: Path, key: str = "actual-budget") -> datetime:
    """Move creation into the past, and return when it now says the app was created.

    Creating and claiming inside one test happen inside one second, so a store
    left as it lands renders both times identically and a check for one of
    them is answered by the other.
    """
    with update_app_tokens(app_tokens_path(tmp_path)) as apps:
        record = apps[key]
        aged = record.model_copy(update={"created_at": record.created_at - timedelta(days=1)})
        apps[key] = aged
    return aged.created_at


def _claim_in_the_store(tmp_path: Path, key: str = "actual-budget") -> ClientCredentials:
    """Do to the record what the claim route will do in the next step."""
    with update_app_tokens(app_tokens_path(tmp_path)) as apps:
        unclaimed = apps[key]
        assert isinstance(unclaimed, UnclaimedAppToken)
        credentials, apps[key] = claim_app_token(unclaimed)
    return credentials


def test_new_records_the_app_as_unclaimed_under_its_label(tmp_path: Path) -> None:
    _write_config(tmp_path)

    result = _new(tmp_path)

    assert result.exit_code == 0
    record = _unclaimed(tmp_path)
    assert record.label == "Actual Budget"


def test_new_puts_the_setup_token_on_stdout_and_nothing_else(tmp_path: Path) -> None:
    """`$(...)` around the command has to yield a token and only a token."""
    _write_config(tmp_path)

    result = _new(tmp_path)

    token = result.stdout.rstrip("\n")
    assert "\n" not in token
    assert base64.b64decode(token, validate=True).decode("ascii").startswith(BASE_URL)
    assert "shown once" not in result.stdout


def test_the_token_decodes_to_this_aggregators_claim_url(tmp_path: Path) -> None:
    _write_config(tmp_path)

    result = _new(tmp_path)

    claim_url = base64.b64decode(result.stdout.strip(), validate=True).decode("ascii")
    assert claim_url == f"{BASE_URL}/simplefin/claim/{_claim_secret(result)}"


def test_the_store_recognises_the_secret_without_holding_it(tmp_path: Path) -> None:
    _write_config(tmp_path)

    result = _new(tmp_path)

    secret = _claim_secret(result)
    assert matches(secret, _unclaimed(tmp_path).claim_token_sha256)
    assert secret not in app_tokens_path(tmp_path).read_text()


def test_the_note_that_the_token_is_shown_once_goes_to_stderr(tmp_path: Path) -> None:
    _write_config(tmp_path)

    result = _new(tmp_path)

    assert "once" in result.stderr
    assert "regen" in result.stderr


def test_new_refuses_a_key_that_already_exists_and_says_how_to_reissue(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _ = _new(tmp_path)
    before = app_tokens_path(tmp_path).read_bytes()

    result = _new(tmp_path, label="A Different Label")

    assert result.exit_code == 1
    assert "app regen" in result.stderr
    assert result.stdout == ""
    assert app_tokens_path(tmp_path).read_bytes() == before


def test_new_refuses_a_key_the_stores_are_not_keyed_by(tmp_path: Path) -> None:
    """The same shape a provider key has, since both name a record and are typed by hand."""
    _write_config(tmp_path)

    result = _new(tmp_path, key="Actual Budget!")

    assert result.exit_code == 1
    assert not app_tokens_path(tmp_path).exists()


def test_list_shows_each_app_with_its_label_and_status(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _ = _new(tmp_path)
    _ = _new(tmp_path, key="beancount", label="Beancount importer")
    _ = _claim_in_the_store(tmp_path, "beancount")

    result = _run(tmp_path, "list")

    assert result.exit_code == 0
    rows = {line.split()[0]: line for line in result.stdout.splitlines()[1:]}
    assert "Actual Budget" in rows["actual-budget"]
    assert "unclaimed" in rows["actual-budget"]
    assert "Beancount importer" in rows["beancount"]
    # Checked as a whole word: "claimed" is a substring of "unclaimed", so a
    # containment check on the output is answered by the other row.
    assert "claimed" in rows["beancount"].split()
    assert "unclaimed" not in rows["beancount"]


def test_list_shows_when_each_app_was_created_and_claimed(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _ = _new(tmp_path)
    created = _back_date(tmp_path)
    _ = _claim_in_the_store(tmp_path)
    record = load_app_tokens(app_tokens_path(tmp_path))["actual-budget"]
    assert isinstance(record, ClaimedAppToken)

    result = _run(tmp_path, "list")

    assert created.isoformat(timespec="seconds") in result.stdout
    assert record.claimed_at.isoformat(timespec="seconds") in result.stdout


def test_list_can_show_no_secret_because_the_store_holds_none(tmp_path: Path) -> None:
    """The digest-only store is what makes this a confirmation rather than a defense."""
    _write_config(tmp_path)
    issued = _new(tmp_path)
    secret = _claim_secret(issued)
    credentials = _claim_in_the_store(tmp_path)
    record = load_app_tokens(app_tokens_path(tmp_path))["actual-budget"]
    assert isinstance(record, ClaimedAppToken)

    result = _run(tmp_path, "list")

    for absent in (
        secret,
        credentials.username.get_secret_value(),
        credentials.password.get_secret_value(),
        record.username_sha256,
        record.password_sha256,
    ):
        assert absent not in result.stdout
        assert absent not in result.stderr


def test_list_of_an_empty_store_keeps_stdout_empty(tmp_path: Path) -> None:
    _write_config(tmp_path)

    result = _run(tmp_path, "list")

    assert result.exit_code == 0
    assert result.stdout == ""
    assert "app new" in result.stderr


def test_revoke_removes_the_app(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _ = _new(tmp_path)
    _ = _new(tmp_path, key="beancount", label="Beancount importer")

    result = _run(tmp_path, "revoke", "--key", "actual-budget")

    assert result.exit_code == 0
    assert set(load_app_tokens(app_tokens_path(tmp_path))) == {"beancount"}


def test_revoke_removes_a_claimed_app_as_readily_as_an_unclaimed_one(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _ = _new(tmp_path)
    _ = _claim_in_the_store(tmp_path)

    result = _run(tmp_path, "revoke", "--key", "actual-budget")

    assert result.exit_code == 0
    assert load_app_tokens(app_tokens_path(tmp_path)) == {}


def test_revoking_an_unknown_key_fails_rather_than_reporting_success(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _ = _new(tmp_path)
    before = app_tokens_path(tmp_path).read_bytes()

    result = _run(tmp_path, "revoke", "--key", "beancount")

    assert result.exit_code == 1
    assert app_tokens_path(tmp_path).read_bytes() == before


def test_regen_returns_a_claimed_app_to_unclaimed_and_keeps_its_label(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _ = _new(tmp_path)
    _ = _claim_in_the_store(tmp_path)

    result = _run(tmp_path, "regen", "--key", "actual-budget")

    assert result.exit_code == 0
    record = _unclaimed(tmp_path)
    assert record.label == "Actual Budget"


def test_regen_prints_a_setup_token_the_new_record_recognises(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _ = _new(tmp_path)
    _ = _claim_in_the_store(tmp_path)

    result = _run(tmp_path, "regen", "--key", "actual-budget")

    assert matches(_claim_secret(result), _unclaimed(tmp_path).claim_token_sha256)


def test_regen_discards_what_the_app_held_before(tmp_path: Path) -> None:
    """Both a live credential and an unspent token stop working immediately."""
    _write_config(tmp_path)
    first = _new(tmp_path)
    _ = _claim_in_the_store(tmp_path)
    before = load_app_tokens(app_tokens_path(tmp_path))["actual-budget"]
    assert isinstance(before, ClaimedAppToken)

    _ = _run(tmp_path, "regen", "--key", "actual-budget")

    written = app_tokens_path(tmp_path).read_text()
    assert before.username_sha256 not in written
    assert before.password_sha256 not in written
    assert not matches(_claim_secret(first), _unclaimed(tmp_path).claim_token_sha256)


def test_regen_of_an_unknown_key_fails_and_issues_nothing(tmp_path: Path) -> None:
    _write_config(tmp_path)

    result = _run(tmp_path, "regen", "--key", "actual-budget")

    assert result.exit_code == 1
    assert result.stdout == ""


def test_a_failure_to_write_the_store_is_reported_rather_than_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full disk has to reach the user as a message, and no token with it.

    Every other way the store can refuse a write already arrives as one kind
    of error, and a caller that had to catch a second kind separately is one
    missing `except` away from a traceback.
    """
    _write_config(tmp_path)

    def no_space(_self: Path, _target: str | Path) -> Path:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Path, "replace", no_space)

    result = _new(tmp_path)

    assert result.exit_code == 1
    assert "cannot write" in result.stderr
    assert result.stdout == ""


def test_a_base_url_no_token_could_carry_is_refused_before_a_record_exists(tmp_path: Path) -> None:
    """Otherwise the record is written and then the token cannot be built for it.

    `app new` writes before it prints, so a base URL that only fails at the
    point of building the token leaves an app the operator must regenerate to
    get anything usable.
    """
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(CONFIG_TOML.replace(BASE_URL, "https://ex\u00e4mple.test"))
    config_path.chmod(0o600)

    result = _new(tmp_path)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert not app_tokens_path(tmp_path).exists()


def test_a_key_that_is_a_pasted_secret_does_not_come_back_on_stderr(tmp_path: Path) -> None:
    """A mistyped `--key` is most often a setup token, and the constraint rejects it.

    Repeating the value would put a secret on stderr in order to tell the user
    what they had just typed.
    """
    _write_config(tmp_path)
    pasted = base64.b64encode(f"{BASE_URL}/simplefin/claim/s3cret-marker".encode()).decode()

    for arguments in (
        ("new", "--key", pasted, "--label", "Actual Budget"),
        ("revoke", "--key", pasted),
        ("regen", "--key", pasted),
    ):
        result = _run(tmp_path, *arguments)
        assert result.exit_code == 1, arguments
        assert pasted not in result.stderr, arguments
        assert "s3cret-marker" not in result.stderr, arguments
        assert result.stdout == "", arguments


SHARED_DIRECTORY_COMMANDS = {
    "new": ("new", "--key", "second", "--label", "Second"),
    "revoke": ("revoke", "--key", "actual-budget"),
    "regen": ("regen", "--key", "actual-budget"),
}


@pytest.mark.parametrize(
    "arguments", SHARED_DIRECTORY_COMMANDS.values(), ids=list(SHARED_DIRECTORY_COMMANDS)
)
def test_a_config_directory_others_can_write_is_reported(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    """Every command that writes into that directory says so, not just one.

    A directory another local user can write is the premise of everything the
    store's write path defends against, and the operator hears it from
    whichever command they happen to run.
    """
    _write_config(tmp_path)
    _ = _new(tmp_path)
    tmp_path.chmod(0o777)

    try:
        result = _run(tmp_path, *arguments)
    finally:
        tmp_path.chmod(0o700)

    assert result.exit_code == 0
    assert "chmod 700" in result.stderr


def test_the_commands_report_a_malformed_store_rather_than_replacing_it(tmp_path: Path) -> None:
    _write_config(tmp_path)
    path = app_tokens_path(tmp_path)
    _ = path.write_text("{not json")

    for arguments in (
        ("new", "--key", "actual-budget", "--label", "Actual Budget"),
        ("list",),
        ("revoke", "--key", "actual-budget"),
        ("regen", "--key", "actual-budget"),
    ):
        result = _run(tmp_path, *arguments)
        assert result.exit_code == 1, arguments
        assert "malformed JSON" in result.stderr, arguments
    assert path.read_text() == "{not json"
