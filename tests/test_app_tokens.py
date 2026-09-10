"""Tests for the app token store."""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from simplefin_aggregator.app_tokens import (
    APP_TOKENS_FILENAME,
    ClaimedAppToken,
    UnclaimedAppToken,
    app_tokens_path,
    claim_app_token,
    load_app_tokens,
    matches,
    new_app_token,
    update_app_tokens,
)
from simplefin_aggregator.state_file import StateFileError


def _store_a_new_app(path: Path, key: str = "actual-budget", label: str = "Actual Budget") -> str:
    """Issue one app, returning the setup token secret it minted."""
    with update_app_tokens(path) as apps:
        secret, apps[key] = new_app_token(label)
    return secret


def _unclaimed(path: Path, key: str = "actual-budget") -> UnclaimedAppToken:
    record = load_app_tokens(path)[key]
    assert isinstance(record, UnclaimedAppToken)
    return record


def _claimed(path: Path, key: str = "actual-budget") -> ClaimedAppToken:
    record = load_app_tokens(path)[key]
    assert isinstance(record, ClaimedAppToken)
    return record


def test_app_tokens_path_uses_the_given_config_dir(tmp_path: Path) -> None:
    assert app_tokens_path(tmp_path) == tmp_path / APP_TOKENS_FILENAME


def test_app_tokens_path_defaults_to_the_platform_config_dir() -> None:
    path = app_tokens_path()

    assert path.name == APP_TOKENS_FILENAME
    assert "simplefin-aggregator" in str(path)


def test_missing_file_loads_as_empty(tmp_path: Path) -> None:
    assert load_app_tokens(app_tokens_path(tmp_path)) == {}


def test_a_new_app_is_stored_unclaimed_with_its_label(tmp_path: Path) -> None:
    path = app_tokens_path(tmp_path)

    _ = _store_a_new_app(path)

    record = _unclaimed(path)
    assert record.label == "Actual Budget"
    assert record.status == "unclaimed"


def test_an_update_keeps_the_apps_it_did_not_touch(tmp_path: Path) -> None:
    path = app_tokens_path(tmp_path)

    _ = _store_a_new_app(path, key="first", label="First")
    _ = _store_a_new_app(path, key="second", label="Second")

    assert set(load_app_tokens(path)) == {"first", "second"}


def test_the_setup_token_secret_is_nowhere_in_the_store(tmp_path: Path) -> None:
    """Nothing persisted can be turned back into a credential.

    This is the whole point of storing digests: there is no plaintext in the
    file for a listing to print, a stray repr to show, or a reader of the file
    to use.
    """
    path = app_tokens_path(tmp_path)

    secret = _store_a_new_app(path)

    assert secret not in path.read_text()
    assert secret not in repr(load_app_tokens(path))


def test_the_stored_app_recognises_its_own_setup_token_and_no_other(tmp_path: Path) -> None:
    path = app_tokens_path(tmp_path)

    secret = _store_a_new_app(path)

    record = _unclaimed(path)
    assert matches(secret, record.claim_token_sha256)
    assert not matches(secret + "x", record.claim_token_sha256)


def test_two_apps_get_different_setup_tokens(tmp_path: Path) -> None:
    path = app_tokens_path(tmp_path)

    first = _store_a_new_app(path, key="first", label="First")
    second = _store_a_new_app(path, key="second", label="Second")

    assert first != second
    assert not matches(first, _unclaimed(path, "second").claim_token_sha256)


def test_a_claim_issues_credentials_the_stored_app_recognises(tmp_path: Path) -> None:
    path = app_tokens_path(tmp_path)
    _ = _store_a_new_app(path)

    with update_app_tokens(path) as apps:
        credentials, apps["actual-budget"] = claim_app_token(_unclaimed(path))

    record = _claimed(path)
    assert matches(credentials.username.get_secret_value(), record.username_sha256)
    assert matches(credentials.password.get_secret_value(), record.password_sha256)


def test_a_claim_keeps_the_label_and_the_creation_time(tmp_path: Path) -> None:
    path = app_tokens_path(tmp_path)
    _ = _store_a_new_app(path)
    before = _unclaimed(path)

    with update_app_tokens(path) as apps:
        _, apps["actual-budget"] = claim_app_token(before)

    after = _claimed(path)
    assert after.label == before.label
    assert after.created_at == before.created_at
    assert before.created_at <= after.claimed_at


def test_timestamps_round_trip_as_utc(tmp_path: Path) -> None:
    path = app_tokens_path(tmp_path)
    _ = _store_a_new_app(path)

    record = _unclaimed(path)
    assert record.created_at.tzinfo is not None
    assert record.created_at.utcoffset() == datetime.now(UTC).utcoffset()


def test_a_claimed_app_no_longer_answers_to_its_setup_token(tmp_path: Path) -> None:
    """A replayed setup token has to fail the lookup an unknown one fails.

    The claim replaces the record rather than marking it spent, so there is no
    spent-token branch to get wrong and nothing to tell the two cases apart.
    """
    path = app_tokens_path(tmp_path)
    secret = _store_a_new_app(path)

    with update_app_tokens(path) as apps:
        _, apps["actual-budget"] = claim_app_token(_unclaimed(path))

    unclaimed = [
        record for record in load_app_tokens(path).values() if isinstance(record, UnclaimedAppToken)
    ]
    assert unclaimed == []
    assert secret not in path.read_text()


def test_the_issued_credentials_are_nowhere_in_the_store(tmp_path: Path) -> None:
    path = app_tokens_path(tmp_path)
    _ = _store_a_new_app(path)

    with update_app_tokens(path) as apps:
        credentials, apps["actual-budget"] = claim_app_token(_unclaimed(path))

    written = path.read_text()
    assert credentials.username.get_secret_value() not in written
    assert credentials.password.get_secret_value() not in written
    assert credentials.password.get_secret_value() not in repr(load_app_tokens(path))


def test_the_issued_credentials_are_redacted_when_rendered(tmp_path: Path) -> None:
    """The pair is live until the claim that issued it has answered.

    Nothing is meant to render it, but a traceback showing its frame's locals
    does so without being asked, and that is the one moment both halves of a
    working credential exist in this process.
    """
    path = app_tokens_path(tmp_path)
    _ = _store_a_new_app(path)

    with update_app_tokens(path) as apps:
        credentials, apps["actual-budget"] = claim_app_token(_unclaimed(path))

    rendered = f"{credentials!r} {credentials}"
    assert credentials.username.get_secret_value() not in rendered
    assert credentials.password.get_secret_value() not in rendered


def test_a_timestamp_without_an_offset_is_rejected(tmp_path: Path) -> None:
    """A naive timestamp loads clean and then raises at the first subtraction.

    Rejecting it on load is the same rule the digest fields follow: a value
    this store could not have written fails where the file is read, not at the
    unrelated operation it later breaks.
    """
    path = app_tokens_path(tmp_path)
    _ = _store_a_new_app(path)
    naive = _unclaimed(path).model_dump(mode="json") | {"created_at": "2026-01-01T00:00:00"}
    _ = path.write_text(json.dumps({"tokens": {"actual-budget": naive}}))

    with pytest.raises(StateFileError, match="invalid contents"):
        _ = load_app_tokens(path)


def test_a_key_this_application_could_not_have_written_is_rejected(tmp_path: Path) -> None:
    """What a listing prints comes from the file, so the file's keys are constrained.

    A key is checked where a command accepts one, but a person can edit the
    store, and a key holding a newline or a terminal escape would be rendered
    by whatever prints it.
    """
    path = app_tokens_path(tmp_path)
    _ = _store_a_new_app(path)
    record = _unclaimed(path).model_dump(mode="json")
    odd_key = "we" + chr(27) + "[31mird" + chr(10) + "key"
    _ = path.write_text(json.dumps({"tokens": {odd_key: record}}))

    with pytest.raises(StateFileError, match="invalid contents"):
        _ = load_app_tokens(path)


def test_an_update_that_raises_writes_nothing(tmp_path: Path) -> None:
    """An update with nothing to do -- a duplicate key, an unknown one -- leaves the file alone."""
    path = app_tokens_path(tmp_path)
    _ = _store_a_new_app(path)
    before = path.read_text()

    def add_an_app_and_then_fail() -> None:
        with update_app_tokens(path) as apps:
            _, apps["second"] = new_app_token("Second")
            raise RuntimeError

    with pytest.raises(RuntimeError):
        add_an_app_and_then_fail()

    assert path.read_text() == before


def test_concurrent_updates_do_not_lose_each_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An app issued while another is being claimed must survive, and vice versa.

    An update rewrites the whole file, and the server claiming an app runs
    alongside whatever the operator is doing, so the read and the write have
    to be one indivisible step. The sleep widens the window a writer would
    otherwise have to be unlucky to land in.
    """
    path = app_tokens_path(tmp_path)
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
        threading.Thread(target=_store_a_new_app, args=(path, key, key.title()))
        for key in ("first", "second")
    ]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join(timeout=10)

    monkeypatch.undo()
    assert set(load_app_tokens(path)) == {"first", "second"}


def test_malformed_json_is_a_clear_error(tmp_path: Path) -> None:
    path = app_tokens_path(tmp_path)
    _ = path.write_text("{not json")

    with pytest.raises(StateFileError, match="malformed JSON"):
        _ = load_app_tokens(path)


def test_a_record_in_neither_state_is_rejected(tmp_path: Path) -> None:
    """The two states are the whole vocabulary; a third would have no defined meaning."""
    path = app_tokens_path(tmp_path)
    _ = _store_a_new_app(path)
    # A record that is otherwise complete, so that the status is the only
    # thing left to reject it for.
    renamed = _unclaimed(path).model_dump(mode="json") | {"status": "revoked"}
    _ = path.write_text(json.dumps({"tokens": {"actual-budget": renamed}}))

    with pytest.raises(StateFileError, match="invalid contents"):
        _ = load_app_tokens(path)


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
    path = app_tokens_path(tmp_path)
    _ = _store_a_new_app(path)
    corrupted = _unclaimed(path).model_dump(mode="json") | {"claim_token_sha256": digest}
    _ = path.write_text(json.dumps({"tokens": {"actual-budget": corrupted}}))

    with pytest.raises(StateFileError, match="invalid contents"):
        _ = load_app_tokens(path)
