# SPDX-License-Identifier: GPL-3.0-only

"""Tests for the `client` commands that add and manage clients."""

from __future__ import annotations

import base64
import errno
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from sf_agg import cli
from sf_agg.agg_creds import (
    AccessUrlAuth,
    ExchangedAggCreds,
    UnexchangedAggCreds,
    agg_creds_path,
    exchange_agg_creds,
    load_agg_creds,
    matches,
    update_agg_creds,
)


if TYPE_CHECKING:
    from typer.testing import Result

runner = CliRunner()

BASE_URL = "http://127.0.0.1:9999"
CONFIG_TOML = f"""
base_url = "{BASE_URL}"
[[providers]]
key = "redbark"
"""

# 2026-01-01T00:00:00Z: a creation time no test run could produce by itself.
NEW_YEAR = 1767225600


def _write_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(CONFIG_TOML)
    config_path.chmod(0o600)


def _run(tmp_path: Path, *arguments: str) -> Result:
    return runner.invoke(cli.app, ["client", *arguments], env={"SF_AGG_DIR": str(tmp_path)})


def _add(tmp_path: Path, key: str = "actual-budget") -> Result:
    return _run(tmp_path, "add", key)


def _claim_secret(result: Result) -> str:
    """The secret inside the setup token stdout carries."""
    claim_url = base64.b64decode(result.stdout.strip(), validate=True).decode("ascii")
    return claim_url.rsplit("/", maxsplit=1)[1]


def _assert_unexchanged(tmp_path: Path, key: str = "actual-budget") -> UnexchangedAggCreds:
    record = load_agg_creds(agg_creds_path(tmp_path))[key]
    assert isinstance(record, UnexchangedAggCreds)
    return record


def _set_created_at(tmp_path: Path, created_at: int, key: str = "actual-budget") -> None:
    with update_agg_creds(agg_creds_path(tmp_path)) as creds:
        creds[key] = creds[key].model_copy(update={"created_at": created_at})


def _exchange_in_the_store(tmp_path: Path, key: str = "actual-budget") -> AccessUrlAuth:
    """Exchange the client's setup token as the claim route does, without a request."""
    with update_agg_creds(agg_creds_path(tmp_path)) as creds:
        unexchanged = creds[key]
        assert isinstance(unexchanged, UnexchangedAggCreds)
        credentials, creds[key] = exchange_agg_creds(unexchanged)
    return credentials


def test_add_records_the_client_as_unexchanged(tmp_path: Path) -> None:
    _write_config(tmp_path)

    result = _add(tmp_path)

    assert result.exit_code == 0
    _ = _assert_unexchanged(tmp_path)


def test_add_puts_the_setup_token_on_stdout_and_nothing_else(tmp_path: Path) -> None:
    """`$(...)` around the command has to yield a token and only a token."""
    _write_config(tmp_path)

    result = _add(tmp_path)

    token = result.stdout.rstrip("\n")
    assert "\n" not in token
    assert base64.b64decode(token, validate=True).decode("ascii").startswith(BASE_URL)
    assert "shown once" not in result.stdout


def test_the_token_decodes_to_this_aggregators_claim_url(tmp_path: Path) -> None:
    _write_config(tmp_path)

    result = _add(tmp_path)

    claim_url = base64.b64decode(result.stdout.strip(), validate=True).decode("ascii")
    assert claim_url == f"{BASE_URL}/simplefin/claim/{_claim_secret(result)}"


def test_the_store_recognizes_the_claim_secret_without_holding_it(tmp_path: Path) -> None:
    _write_config(tmp_path)

    result = _add(tmp_path)

    secret = _claim_secret(result)
    assert matches(secret, _assert_unexchanged(tmp_path).claim_secret_sha256)
    assert secret not in agg_creds_path(tmp_path).read_text()


def test_the_note_that_the_token_is_shown_once_goes_to_stderr(tmp_path: Path) -> None:
    _write_config(tmp_path)

    result = _add(tmp_path)

    assert "once" in result.stderr
    assert "client reset" in result.stderr


def test_add_names_the_store_it_wrote(tmp_path: Path) -> None:
    """The directory is invisible state, so `client add` says where it landed."""
    _write_config(tmp_path)

    result = _add(tmp_path)

    assert str(agg_creds_path(tmp_path)) in result.stderr


def test_add_refuses_a_key_that_already_exists_and_says_how_to_reset(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _ = _add(tmp_path)
    before = agg_creds_path(tmp_path).read_bytes()

    result = _add(tmp_path)

    assert result.exit_code == 1
    assert "client reset" in result.stderr
    assert result.stdout == ""
    assert agg_creds_path(tmp_path).read_bytes() == before


def test_add_refuses_a_key_the_stores_are_not_keyed_by(tmp_path: Path) -> None:
    """The same shape a provider key has, since both name a record and are typed by hand."""
    _write_config(tmp_path)

    result = _add(tmp_path, key="Actual Budget!")

    assert result.exit_code == 1
    assert not agg_creds_path(tmp_path).exists()


def test_list_shows_each_client_by_key_and_creation_time_only(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _ = _add(tmp_path)
    _ = _add(tmp_path, key="beancount")
    _set_created_at(tmp_path, NEW_YEAR, key="beancount")
    _ = _exchange_in_the_store(tmp_path, "beancount")

    result = _run(tmp_path, "list")

    assert result.exit_code == 0
    header, *lines = result.stdout.splitlines()
    assert header.split() == ["KEY", "CREATED"]
    rows = {line.split()[0]: line.split()[1:] for line in lines}
    assert set(rows) == {"actual-budget", "beancount"}
    assert rows["beancount"] == ["2026-01-01T00:00:00+00:00"]


def test_list_shows_the_latest_creation_time_the_store_accepts(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _ = _add(tmp_path)
    _set_created_at(tmp_path, 253_370_764_799)

    result = _run(tmp_path, "list")

    assert result.exit_code == 0
    assert "9998-12-31T23:59:59+00:00" in result.stdout


def test_list_can_show_no_secret_because_the_store_holds_none(tmp_path: Path) -> None:
    """The digest-only store is what makes this a confirmation rather than a defense."""
    _write_config(tmp_path)
    issued = _add(tmp_path)
    secret = _claim_secret(issued)
    credentials = _exchange_in_the_store(tmp_path)
    record = load_agg_creds(agg_creds_path(tmp_path))["actual-budget"]
    assert isinstance(record, ExchangedAggCreds)

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
    assert "client add" in result.stderr


def test_revoke_removes_the_client(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _ = _add(tmp_path)
    _ = _add(tmp_path, key="beancount")

    result = _run(tmp_path, "revoke", "actual-budget")

    assert result.exit_code == 0
    assert set(load_agg_creds(agg_creds_path(tmp_path))) == {"beancount"}


def test_revoke_removes_a_client_after_exchange_as_readily_as_before(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _ = _add(tmp_path)
    _ = _exchange_in_the_store(tmp_path)

    result = _run(tmp_path, "revoke", "actual-budget")

    assert result.exit_code == 0
    assert load_agg_creds(agg_creds_path(tmp_path)) == {}


def test_revoke_does_not_name_the_directory(tmp_path: Path) -> None:
    """Unlike `client add`/`client reset`, `client revoke` says only what it already said."""
    _write_config(tmp_path)
    _ = _add(tmp_path)

    result = _run(tmp_path, "revoke", "actual-budget")

    assert str(tmp_path) not in result.stderr


def test_list_does_not_name_the_directory(tmp_path: Path) -> None:
    """Unlike `client add`/`client reset`, `client list` says only what it already said."""
    _write_config(tmp_path)
    _ = _add(tmp_path)

    result = _run(tmp_path, "list")

    assert str(tmp_path) not in result.stdout
    assert str(tmp_path) not in result.stderr


def test_revoking_an_unknown_key_fails_rather_than_reporting_success(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _ = _add(tmp_path)
    before = agg_creds_path(tmp_path).read_bytes()

    result = _run(tmp_path, "revoke", "beancount")

    assert result.exit_code == 1
    assert agg_creds_path(tmp_path).read_bytes() == before


def test_reset_after_exchange_leaves_a_setup_token_to_exchange(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _ = _add(tmp_path)
    _ = _exchange_in_the_store(tmp_path)

    result = _run(tmp_path, "reset", "actual-budget")

    assert result.exit_code == 0
    _ = _assert_unexchanged(tmp_path)


def test_reset_keeps_the_creation_time(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _ = _add(tmp_path)
    _set_created_at(tmp_path, NEW_YEAR)
    _ = _exchange_in_the_store(tmp_path)

    _ = _run(tmp_path, "reset", "actual-budget")

    assert _assert_unexchanged(tmp_path).created_at == NEW_YEAR


def test_reset_prints_a_setup_token_the_new_record_recognizes(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _ = _add(tmp_path)
    _ = _exchange_in_the_store(tmp_path)

    result = _run(tmp_path, "reset", "actual-budget")

    assert matches(_claim_secret(result), _assert_unexchanged(tmp_path).claim_secret_sha256)


def test_reset_names_the_store_it_wrote(tmp_path: Path) -> None:
    """The directory is invisible state, so `client reset` says where it landed."""
    _write_config(tmp_path)
    _ = _add(tmp_path)

    result = _run(tmp_path, "reset", "actual-budget")

    assert str(agg_creds_path(tmp_path)) in result.stderr


def test_reset_after_exchange_revokes_the_credentials(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _ = _add(tmp_path)
    _ = _exchange_in_the_store(tmp_path)
    before = load_agg_creds(agg_creds_path(tmp_path))["actual-budget"]
    assert isinstance(before, ExchangedAggCreds)

    _ = _run(tmp_path, "reset", "actual-budget")

    written = agg_creds_path(tmp_path).read_text()
    assert before.username_sha256 not in written
    assert before.password_sha256 not in written


def test_reset_before_exchange_revokes_the_setup_token(tmp_path: Path) -> None:
    _write_config(tmp_path)
    first = _claim_secret(_add(tmp_path))
    assert matches(first, _assert_unexchanged(tmp_path).claim_secret_sha256)

    _ = _run(tmp_path, "reset", "actual-budget")

    assert not matches(first, _assert_unexchanged(tmp_path).claim_secret_sha256)


def test_reset_of_an_unknown_key_fails_and_issues_nothing(tmp_path: Path) -> None:
    _write_config(tmp_path)

    result = _run(tmp_path, "reset", "actual-budget")

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

    result = _add(tmp_path)

    assert result.exit_code == 1
    assert "cannot write" in result.stderr
    assert result.stdout == ""


def test_a_base_url_no_token_could_carry_is_refused_before_a_record_exists(tmp_path: Path) -> None:
    """Otherwise the record is written and then the token cannot be built for it.

    `client add` writes before it prints, so a base URL that only fails at the
    point of building the token leaves a client the operator must reset to get
    anything usable.
    """
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(CONFIG_TOML.replace(BASE_URL, "https://ex\u00e4mple.test"))
    config_path.chmod(0o600)

    result = _add(tmp_path)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert not agg_creds_path(tmp_path).exists()


SHARED_DIRECTORY_COMMANDS = {
    "add": ("add", "second"),
    "revoke": ("revoke", "actual-budget"),
    "reset": ("reset", "actual-budget"),
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
    _ = _add(tmp_path)
    tmp_path.chmod(0o777)

    try:
        result = _run(tmp_path, *arguments)
    finally:
        tmp_path.chmod(0o700)

    assert result.exit_code == 0
    assert "chmod 700" in result.stderr


def test_the_commands_report_a_malformed_store_rather_than_replacing_it(tmp_path: Path) -> None:
    _write_config(tmp_path)
    path = agg_creds_path(tmp_path)
    _ = path.write_text("{not json")

    for arguments in (
        ("add", "actual-budget"),
        ("list",),
        ("revoke", "actual-budget"),
        ("reset", "actual-budget"),
    ):
        result = _run(tmp_path, *arguments)
        assert result.exit_code == 1, arguments
        assert "malformed JSON" in result.stderr, arguments
    assert path.read_text() == "{not json"
