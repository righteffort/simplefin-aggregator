# SPDX-License-Identifier: GPL-3.0-only

"""Tests for the aggregator creds store."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from sf_agg.agg_creds import (
    AGG_CREDS_FILENAME,
    AccessUrlAuth,
    ExchangedAggCreds,
    UnexchangedAggCreds,
    agg_creds_path,
    exchange_agg_creds,
    load_agg_creds,
    matches,
    new_agg_creds,
    update_agg_creds,
)
from sf_agg.config import DIR_ENV_VAR
from sf_agg.state_file import StateFileError


def _add_a_client(path: Path, key: str = "actual-budget") -> str:
    """Add one client, returning the claim secret it minted."""
    with update_agg_creds(path) as creds:
        secret, creds[key] = new_agg_creds()
    return secret


def _exchange(path: Path, key: str = "actual-budget") -> AccessUrlAuth:
    with update_agg_creds(path) as creds:
        credentials, creds[key] = exchange_agg_creds(_assert_unexchanged(path, key))
    return credentials


def _assert_unexchanged(path: Path, key: str = "actual-budget") -> UnexchangedAggCreds:
    record = load_agg_creds(path)[key]
    assert isinstance(record, UnexchangedAggCreds)
    return record


def _assert_exchanged(path: Path, key: str = "actual-budget") -> ExchangedAggCreds:
    record = load_agg_creds(path)[key]
    assert isinstance(record, ExchangedAggCreds)
    return record


def test_agg_creds_path_uses_the_given_config_dir(tmp_path: Path) -> None:
    assert agg_creds_path(tmp_path) == tmp_path / AGG_CREDS_FILENAME


def test_agg_creds_path_defaults_to_the_platform_config_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DIR_ENV_VAR, raising=False)

    path = agg_creds_path()

    assert path.name == AGG_CREDS_FILENAME
    assert "sf-agg" in str(path)


def test_missing_file_loads_as_empty(tmp_path: Path) -> None:
    assert load_agg_creds(agg_creds_path(tmp_path)) == {}


def test_a_new_client_is_stored_unexchanged(tmp_path: Path) -> None:
    path = agg_creds_path(tmp_path)

    _ = _add_a_client(path)

    assert json.loads(path.read_text())["creds"]["actual-budget"]["exchanged"] is False
    assert isinstance(load_agg_creds(path)["actual-budget"], UnexchangedAggCreds)


def test_the_creation_time_is_in_epoch_seconds(tmp_path: Path) -> None:
    path = agg_creds_path(tmp_path)
    before = int(time.time())

    _ = _add_a_client(path)

    assert before <= _assert_unexchanged(path).created_at <= int(time.time())


def test_an_update_keeps_the_clients_it_did_not_touch(tmp_path: Path) -> None:
    path = agg_creds_path(tmp_path)

    _ = _add_a_client(path, key="first")
    _ = _add_a_client(path, key="second")

    assert set(load_agg_creds(path)) == {"first", "second"}


def test_the_claim_secret_is_nowhere_in_the_store(tmp_path: Path) -> None:
    """Nothing persisted can be turned back into a credential.

    This is the whole point of storing digests: there is no plaintext in the
    file for a listing to print, a stray repr to show, or a reader of the file
    to use.
    """
    path = agg_creds_path(tmp_path)

    secret = _add_a_client(path)

    assert secret not in path.read_text()
    assert secret not in repr(load_agg_creds(path))


def test_the_stored_client_recognizes_its_own_claim_secret_and_no_other(tmp_path: Path) -> None:
    path = agg_creds_path(tmp_path)

    secret = _add_a_client(path)

    record = _assert_unexchanged(path)
    assert matches(secret, record.claim_secret_sha256)
    assert not matches(secret + "x", record.claim_secret_sha256)


def test_two_clients_get_different_claim_secrets(tmp_path: Path) -> None:
    path = agg_creds_path(tmp_path)

    first = _add_a_client(path, key="first")
    second = _add_a_client(path, key="second")

    assert first != second
    assert not matches(first, _assert_unexchanged(path, "second").claim_secret_sha256)


def test_an_exchange_issues_credentials_the_stored_client_recognizes(tmp_path: Path) -> None:
    path = agg_creds_path(tmp_path)
    _ = _add_a_client(path)

    credentials = _exchange(path)

    record = _assert_exchanged(path)
    assert matches(credentials.username.get_secret_value(), record.username_sha256)
    assert matches(credentials.password.get_secret_value(), record.password_sha256)


def test_an_exchange_keeps_the_creation_time(tmp_path: Path) -> None:
    path = agg_creds_path(tmp_path)
    _ = _add_a_client(path)
    before = _assert_unexchanged(path)

    _ = _exchange(path)

    after = _assert_exchanged(path)
    assert after.created_at == before.created_at
    assert before.created_at <= after.exchanged_at


def test_after_exchange_the_claim_secret_matches_nothing(tmp_path: Path) -> None:
    """A replayed setup token has to fail the lookup an unknown one fails."""
    path = agg_creds_path(tmp_path)
    secret = _add_a_client(path)

    _ = _exchange(path)

    unexchanged = [
        record
        for record in load_agg_creds(path).values()
        if isinstance(record, UnexchangedAggCreds)
    ]
    assert unexchanged == []
    assert secret not in path.read_text()


def test_the_issued_credentials_are_nowhere_in_the_store(tmp_path: Path) -> None:
    path = agg_creds_path(tmp_path)
    _ = _add_a_client(path)

    credentials = _exchange(path)

    written = path.read_text()
    assert credentials.username.get_secret_value() not in written
    assert credentials.password.get_secret_value() not in written
    assert credentials.password.get_secret_value() not in repr(load_agg_creds(path))


def test_the_issued_credentials_are_redacted_when_rendered(tmp_path: Path) -> None:
    """The pair is live until the exchange that issued it has answered.

    Nothing is meant to render it, but a traceback showing its frame's locals
    does so without being asked, and that is the one moment both halves of a
    working credential exist in this process.
    """
    path = agg_creds_path(tmp_path)
    _ = _add_a_client(path)

    credentials = _exchange(path)

    rendered = f"{credentials!r} {credentials}"
    assert credentials.username.get_secret_value() not in rendered
    assert credentials.password.get_secret_value() not in rendered


# Each one a timestamp a person might plausibly write, and none of them one this
# store could have written.
NOT_A_TIMESTAMP = {
    "a fraction of a second": 1767225600.5,
    "a boolean": True,
    "a number in a string": "1767225600",
    "an ISO 8601 string": "2026-01-01T00:00:00+00:00",
    "before the epoch": -1,
    "after the year 9998": 253_370_764_800,
}


@pytest.mark.parametrize("timestamp", NOT_A_TIMESTAMP.values(), ids=list(NOT_A_TIMESTAMP))
def test_a_timestamp_this_store_could_not_have_written_is_rejected(
    tmp_path: Path, timestamp: object
) -> None:
    path = agg_creds_path(tmp_path)
    _ = _add_a_client(path)
    corrupted = _assert_unexchanged(path).model_dump(mode="json") | {"created_at": timestamp}
    _ = path.write_text(json.dumps({"creds": {"actual-budget": corrupted}}))

    with pytest.raises(StateFileError, match="invalid contents"):
        _ = load_agg_creds(path)


def test_a_key_this_application_could_not_have_written_is_rejected(tmp_path: Path) -> None:
    """What a listing prints comes from the file, so the file's keys are constrained.

    A key is checked where a command accepts one, but a person can edit the
    store, and a key holding a newline or a terminal escape would be rendered
    by whatever prints it.
    """
    path = agg_creds_path(tmp_path)
    _ = _add_a_client(path)
    record = _assert_unexchanged(path).model_dump(mode="json")
    odd_key = "we" + chr(27) + "[31mird" + chr(10) + "key"
    _ = path.write_text(json.dumps({"creds": {odd_key: record}}))

    with pytest.raises(StateFileError, match="invalid contents"):
        _ = load_agg_creds(path)


def test_an_update_that_raises_writes_nothing(tmp_path: Path) -> None:
    """An update with nothing to do -- a duplicate key, an unknown one -- leaves the file alone."""
    path = agg_creds_path(tmp_path)
    _ = _add_a_client(path)
    before = path.read_text()

    def add_a_client_and_then_fail() -> None:
        with update_agg_creds(path) as creds:
            _, creds["second"] = new_agg_creds()
            raise RuntimeError

    with pytest.raises(RuntimeError):
        add_a_client_and_then_fail()

    assert path.read_text() == before


def test_concurrent_updates_do_not_lose_each_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A client added while another's setup token is exchanged must survive, and vice versa.

    An update rewrites the whole file, and the server exchanging a setup token
    runs alongside whatever the operator is doing, so the read and the write have
    to be one indivisible step. The sleep widens the window a writer would
    otherwise have to be unlucky to land in.
    """
    path = agg_creds_path(tmp_path)
    real_read_text = Path.read_text

    def slow_read_text(self: Path, *args: object, **kwargs: object) -> str:
        try:
            return real_read_text(self, *args, **kwargs)  # pyright: ignore[reportArgumentType]
        finally:
            # In a finally, so that the very first update -- whose read raises,
            # because the store is not there yet -- is widened too.
            time.sleep(0.2)

    monkeypatch.setattr(Path, "read_text", slow_read_text)
    writers = [
        threading.Thread(target=_add_a_client, args=(path, key)) for key in ("first", "second")
    ]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join(timeout=10)

    monkeypatch.undo()
    assert set(load_agg_creds(path)) == {"first", "second"}


def test_a_top_level_key_this_store_does_not_write_is_rejected(tmp_path: Path) -> None:
    """Rejected, not read as an empty store."""
    path = agg_creds_path(tmp_path)
    _ = _add_a_client(path)
    record = _assert_unexchanged(path).model_dump(mode="json")
    _ = path.write_text(json.dumps({"stray-key": {"actual-budget": record}}))

    with pytest.raises(StateFileError, match="invalid contents") as excinfo:
        _ = load_agg_creds(path)
    assert "stray-key" not in str(excinfo.value)


def test_a_record_field_this_store_does_not_write_is_rejected(tmp_path: Path) -> None:
    path = agg_creds_path(tmp_path)
    _ = _add_a_client(path)
    record = _assert_unexchanged(path).model_dump(mode="json") | {"note": "hand-added"}
    _ = path.write_text(json.dumps({"creds": {"actual-budget": record}}))

    with pytest.raises(StateFileError, match="invalid contents"):
        _ = load_agg_creds(path)


def test_malformed_json_is_a_clear_error(tmp_path: Path) -> None:
    path = agg_creds_path(tmp_path)
    _ = path.write_text("{not json")

    with pytest.raises(StateFileError, match="malformed JSON"):
        _ = load_agg_creds(path)


def _with_state(path: Path, state: object) -> None:
    record = load_agg_creds(path)["actual-budget"].model_dump(mode="json")
    _ = path.write_text(json.dumps({"creds": {"actual-budget": record | {"exchanged": state}}}))


# Each table is tried on the record whose other fields match what its values
# would pass for, so that `exchanged` is the only thing left to reject.
NOT_FALSE = {"zero": 0, "a string": "false", "null": None}
NOT_TRUE = {"one": 1, "a string": "true"}


@pytest.mark.parametrize("state", NOT_FALSE.values(), ids=list(NOT_FALSE))
def test_an_unexchanged_record_whose_state_is_not_false_is_rejected(
    tmp_path: Path, state: object
) -> None:
    """`exchanged` is a JSON boolean, or the record fails on load."""
    path = agg_creds_path(tmp_path)
    _ = _add_a_client(path)
    _with_state(path, state)

    with pytest.raises(StateFileError, match="invalid contents"):
        _ = load_agg_creds(path)


@pytest.mark.parametrize("state", NOT_TRUE.values(), ids=list(NOT_TRUE))
def test_an_exchanged_record_whose_state_is_not_true_is_rejected(
    tmp_path: Path, state: object
) -> None:
    """`exchanged` is a JSON boolean, or the record fails on load."""
    path = agg_creds_path(tmp_path)
    _ = _add_a_client(path)
    _ = _exchange(path)
    _with_state(path, state)

    with pytest.raises(StateFileError, match="invalid contents"):
        _ = load_agg_creds(path)


# One entry per clause of the digest constraint. A value that breaks two
# clauses at once is rejected by whichever runs first, so it pins neither.
NOT_A_DIGEST = {
    "the wrong length": "abcd",
    "not hexadecimal": "z" * 64,
    # The clause that matters most: a comparison against a non-ASCII string
    # raises, so this is the value that would fail at the comparison rather
    # than at the load.
    "the right length, but not ASCII": "ó" + "a" * 63,
}


@pytest.mark.parametrize("digest", NOT_A_DIGEST.values(), ids=list(NOT_A_DIGEST))
def test_a_digest_that_could_not_have_been_written_here_is_rejected(
    tmp_path: Path, digest: str
) -> None:
    """A hand-edited digest must fail on load, not at the comparison it would break."""
    path = agg_creds_path(tmp_path)
    _ = _add_a_client(path)
    corrupted = _assert_unexchanged(path).model_dump(mode="json") | {"claim_secret_sha256": digest}
    _ = path.write_text(json.dumps({"creds": {"actual-budget": corrupted}}))

    with pytest.raises(StateFileError, match="invalid contents"):
        _ = load_agg_creds(path)
