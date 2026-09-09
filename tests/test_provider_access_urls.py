"""Tests for the provider access URL store."""

from __future__ import annotations

import errno
import os
import stat
import threading
import time
from pathlib import Path

import pytest
from pydantic import SecretStr

from simplefin_aggregator.provider_access_urls import (
    PROVIDER_CREDS_FILENAME,
    load_access_urls,
    provider_creds_path,
    save_access_url,
)
from simplefin_aggregator.state_file import StateFileError, check_can_save


ACCESS_URL = "https://user:s3cret-provider-password@provider.invalid/simplefin"
OTHER_ACCESS_URL = "https://other:other-password@other.invalid/simplefin"
# Deliberately short: a long rejected value can be reported with its middle
# elided, which would hide the password on its own and make the leak test below
# pass for the wrong reason.
SHORT_ACCESS_URL = "https://u:s3cret-short@h.invalid/x"
# The writers' sidecar lock. It sits next to the store and outlives any one
# update, so every check on what the directory holds has to account for it.
LOCK_FILENAME = "provider_creds.lock"


def _directory_contents(directory: Path) -> set[str]:
    return {child.name for child in directory.iterdir()}


def test_provider_creds_path_uses_the_given_config_dir(tmp_path: Path) -> None:
    assert provider_creds_path(tmp_path) == tmp_path / PROVIDER_CREDS_FILENAME


def test_provider_creds_path_defaults_to_the_platform_config_dir() -> None:
    path = provider_creds_path()

    assert path.name == PROVIDER_CREDS_FILENAME
    assert "simplefin-aggregator" in str(path)


def test_missing_file_loads_as_empty(tmp_path: Path) -> None:
    assert load_access_urls(provider_creds_path(tmp_path)) == {}


def test_round_trip(tmp_path: Path) -> None:
    path = provider_creds_path(tmp_path)

    save_access_url(path, "redbark", SecretStr(ACCESS_URL))

    stored = load_access_urls(path)
    assert stored["redbark"].get_secret_value() == ACCESS_URL


def test_save_creates_missing_directories(tmp_path: Path) -> None:
    path = provider_creds_path(tmp_path / "nested" / "config")

    save_access_url(path, "redbark", SecretStr(ACCESS_URL))

    assert load_access_urls(path)["redbark"].get_secret_value() == ACCESS_URL


def test_save_keeps_other_providers(tmp_path: Path) -> None:
    path = provider_creds_path(tmp_path)

    save_access_url(path, "redbark", SecretStr(ACCESS_URL))
    save_access_url(path, "lunchflow", SecretStr(OTHER_ACCESS_URL))

    stored = load_access_urls(path)
    assert stored["redbark"].get_secret_value() == ACCESS_URL
    assert stored["lunchflow"].get_secret_value() == OTHER_ACCESS_URL


def test_save_replaces_the_entry_for_one_provider(tmp_path: Path) -> None:
    path = provider_creds_path(tmp_path)

    save_access_url(path, "redbark", SecretStr(ACCESS_URL))
    save_access_url(path, "redbark", SecretStr(OTHER_ACCESS_URL))

    assert load_access_urls(path)["redbark"].get_secret_value() == OTHER_ACCESS_URL


def test_file_is_created_owner_read_write_only(tmp_path: Path) -> None:
    path = provider_creds_path(tmp_path)

    save_access_url(path, "redbark", SecretStr(ACCESS_URL))

    assert stat.S_IMODE(path.stat().st_mode) == 0o600  # noqa: PLR2004


def test_save_tightens_the_mode_of_an_existing_permissive_file(tmp_path: Path) -> None:
    path = provider_creds_path(tmp_path)
    save_access_url(path, "redbark", SecretStr(ACCESS_URL))
    path.chmod(0o644)

    save_access_url(path, "lunchflow", SecretStr(OTHER_ACCESS_URL))

    assert stat.S_IMODE(path.stat().st_mode) == 0o600  # noqa: PLR2004


def test_no_temporary_file_is_left_behind(tmp_path: Path) -> None:
    path = provider_creds_path(tmp_path)

    save_access_url(path, "redbark", SecretStr(ACCESS_URL))

    assert _directory_contents(tmp_path) == {PROVIDER_CREDS_FILENAME, LOCK_FILENAME}


def test_save_does_not_follow_a_symlink_at_a_guessable_temporary_path(tmp_path: Path) -> None:
    """The config directory may be one another local user can write."""
    decoy = tmp_path / "decoy"
    _ = decoy.write_text("")
    (tmp_path / f"{PROVIDER_CREDS_FILENAME}.tmp").symlink_to(decoy)
    path = provider_creds_path(tmp_path)

    save_access_url(path, "redbark", SecretStr(ACCESS_URL))

    assert decoy.read_text() == ""
    assert load_access_urls(path)["redbark"].get_secret_value() == ACCESS_URL


def test_save_does_not_follow_a_symlink_at_the_guessable_lock_path(tmp_path: Path) -> None:
    """The lock path is as guessable as the temporary one, and in the same directory.

    Followed, a symlink here would have this create a file wherever its owner
    can write, and -- pointed at a file the planter already holds a lock on --
    would wedge every writer with no timeout rather than failing.
    """
    decoy = tmp_path / "decoy"
    (tmp_path / LOCK_FILENAME).symlink_to(decoy)
    path = provider_creds_path(tmp_path)

    with pytest.raises(StateFileError, match="cannot open"):
        save_access_url(path, "redbark", SecretStr(ACCESS_URL))

    assert not decoy.exists()


def _fsync_target(fd: int) -> str:
    """Which of the two things a save fsyncs this descriptor is."""
    return "directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"


def test_save_fsyncs_the_file_before_the_rename_and_the_directory_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither the data blocks nor the rename itself may be what power loss drops.

    A rename that reaches disk before the data blocks yields an empty store; a
    rename that never reaches disk at all yields the previous one.
    """
    path = provider_creds_path(tmp_path)
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = Path.replace

    def recording_fsync(fd: int) -> None:
        events.append(f"fsync {_fsync_target(fd)}")
        real_fsync(fd)

    def recording_replace(self: Path, target: str | Path) -> Path:
        events.append("replace")
        return real_replace(self, target)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    monkeypatch.setattr(Path, "replace", recording_replace)

    save_access_url(path, "redbark", SecretStr(ACCESS_URL))

    assert events == ["fsync file", "replace", "fsync directory"]


def test_a_directory_fsync_failure_does_not_report_a_save_that_succeeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rename has already happened, so failing here would claim a lost credential.

    `claim` reports a failed save as a spent setup token, which is the most
    expensive thing this application can wrongly tell a user. Reachable
    without an exotic filesystem: a config directory that is writable but not
    readable takes every write the store makes and still refuses this open.
    """
    path = provider_creds_path(tmp_path)
    real_fsync = os.fsync

    def fsync_failing_on_directories(fd: int) -> None:
        if _fsync_target(fd) == "directory":
            raise OSError(errno.EINVAL, "fsync is not supported on this directory")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync_failing_on_directories)

    save_access_url(path, "redbark", SecretStr(ACCESS_URL))

    assert load_access_urls(path)["redbark"].get_secret_value() == ACCESS_URL


def test_save_removes_the_temporary_file_when_the_rename_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup must survive a non-OSError failure, which `except OSError` missed."""
    path = provider_creds_path(tmp_path)

    def exploding_replace(_self: Path, _target: str | Path) -> Path:
        raise KeyboardInterrupt

    monkeypatch.setattr(Path, "replace", exploding_replace)

    with pytest.raises(KeyboardInterrupt):
        save_access_url(path, "redbark", SecretStr(ACCESS_URL))

    assert _directory_contents(tmp_path) == {LOCK_FILENAME}


def test_a_cleanup_failure_does_not_replace_the_error_that_caused_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever stops the write usually stops the tidying too.

    A directory that turns read-only mid-save fails the rename and then fails
    the removal of the file the rename left behind, and it is the first of
    those the caller has to hear about -- the second reaching them instead is
    an unhandled error where every other write failure is a message.
    """
    path = provider_creds_path(tmp_path)
    cleanups = 0

    def denied(_self: Path, *_arguments: object, **_options: object) -> None:
        raise PermissionError(errno.EACCES, "Permission denied")

    def denied_cleanup(self: Path, *arguments: object, **options: object) -> None:
        nonlocal cleanups
        cleanups += 1
        denied(self, *arguments, **options)

    monkeypatch.setattr(Path, "replace", denied)
    monkeypatch.setattr(Path, "unlink", denied_cleanup)

    with pytest.raises(StateFileError, match="cannot write"):
        save_access_url(path, "redbark", SecretStr(ACCESS_URL))

    # Without this the test's premise can evaporate: a save that stopped
    # cleaning up at all would raise the same error and pass while pinning
    # nothing.
    assert cleanups == 1


def test_check_can_save_creates_the_store_directory(tmp_path: Path) -> None:
    path = provider_creds_path(tmp_path / "nested" / "config")

    check_can_save(path)

    assert path.parent.is_dir()
    assert list(path.parent.iterdir()) == []


@pytest.mark.skipif(
    os.name == "posix" and os.geteuid() == 0,
    reason="root writes a directory whatever its mode says",
)
def test_check_can_save_rejects_a_directory_it_cannot_write_in(tmp_path: Path) -> None:
    read_only = tmp_path / "read-only"
    read_only.mkdir(mode=0o500)

    with pytest.raises(StateFileError, match="cannot write"):
        check_can_save(provider_creds_path(read_only))


def test_check_can_save_warns_about_a_config_directory_others_can_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The premise of everything the write path defends against.

    A directory another local user can write lets them replace a store
    outright, plant a symlink where this application will open one, or swap
    the file a lock is held on. The defenses narrow what that buys; none of
    them makes it safe, so it is worth saying out loud.
    """
    shared = tmp_path / "shared"
    shared.mkdir()
    # chmod rather than mkdir's mode, which the umask filters: with the usual
    # 022 the directory would come out 0755 and this would be a test about
    # readability rather than about the write access it claims to be about.
    shared.chmod(0o777)

    check_can_save(provider_creds_path(shared))

    warning = capsys.readouterr().err
    assert str(shared) in warning
    assert "chmod 700" in warning


def test_check_can_save_is_quiet_about_an_owner_only_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    check_can_save(provider_creds_path(tmp_path / "private"))

    assert capsys.readouterr().err == ""


# One per way the mode can be too open, since a value that is loose in both
# ways is caught by whichever half runs first and pins neither.
TOO_OPEN = {"group can read": 0o640, "other can read": 0o604, "both can": 0o644}


@pytest.mark.parametrize("mode", TOO_OPEN.values(), ids=list(TOO_OPEN))
def test_load_warns_on_permissive_file_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mode: int
) -> None:
    path = provider_creds_path(tmp_path)
    save_access_url(path, "redbark", SecretStr(ACCESS_URL))
    path.chmod(mode)

    _ = load_access_urls(path)

    assert "chmod 600" in capsys.readouterr().err


def test_a_mode_that_cannot_be_read_does_not_fail_the_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check exists only to warn, so failing it must not deny what was read.

    It matters because the caller that reads a store per request catches one
    kind of error to fail closed; a raw one from the advisory check would
    reach the client as a server error instead.
    """
    path = provider_creds_path(tmp_path)
    save_access_url(path, "redbark", SecretStr(ACCESS_URL))
    real_stat = Path.stat

    def unreadable_mode(self: Path, **options: object) -> os.stat_result:
        if self == path:
            raise OSError(errno.EIO, "Input/output error")
        return real_stat(self, **options)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(Path, "stat", unreadable_mode)

    assert load_access_urls(path)["redbark"].get_secret_value() == ACCESS_URL


def test_the_permission_warning_does_not_say_what_the_file_holds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One warning covers three files, and only one of them holds a credential.

    Modification matters for all of them; disclosure only for this one. Saying
    so per file would mean a flag on the check or a sentence hedging about
    which case it is in, and the asymmetry belongs in the architecture notes.
    """
    path = provider_creds_path(tmp_path)
    save_access_url(path, "redbark", SecretStr(ACCESS_URL))
    path.chmod(0o644)

    _ = load_access_urls(path)

    warning = capsys.readouterr().err
    assert "chmod 600" in warning
    assert "credential" not in warning


def test_load_does_not_warn_on_owner_only_file_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = provider_creds_path(tmp_path)
    save_access_url(path, "redbark", SecretStr(ACCESS_URL))

    _ = load_access_urls(path)

    assert capsys.readouterr().err == ""


def test_malformed_json_is_a_clear_error(tmp_path: Path) -> None:
    path = provider_creds_path(tmp_path)
    _ = path.write_text("{not json")

    with pytest.raises(StateFileError, match="malformed JSON"):
        _ = load_access_urls(path)


def test_a_byte_order_mark_is_a_clear_error(tmp_path: Path) -> None:
    """Pins current behavior, not a requirement: the store is machine-written.

    `config.toml` is read in the encoding that drops a leading byte order
    mark, because a person edits it and an editor may add one. Nothing edits a
    store in the ordinary course, so a mark there is reported rather than
    silently stripped.
    """
    path = provider_creds_path(tmp_path)
    save_access_url(path, "redbark", SecretStr(ACCESS_URL))
    _ = path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())

    with pytest.raises(StateFileError, match="malformed JSON"):
        _ = load_access_urls(path)


def test_a_file_that_is_not_utf8_is_a_clear_error(tmp_path: Path) -> None:
    path = provider_creds_path(tmp_path)
    _ = path.write_bytes(b'{"access_urls": {"redbark": "\xff\xfe"}}')

    with pytest.raises(StateFileError, match="not UTF-8"):
        _ = load_access_urls(path)


def test_a_key_the_store_rejects_is_kept_out_of_the_error(tmp_path: Path) -> None:
    """A credential in key position must not reach the message either.

    The rejected *value* is excluded by building the message from the failure
    location and its description. The location names the key that located the
    failure, and a key comes from the file, so only the parts the schema
    declares as fields are rendered.
    """
    path = provider_creds_path(tmp_path)
    _ = path.write_text(f'{{"access_urls": {{"{SHORT_ACCESS_URL}": ["wrong shape"]}}}}')

    with pytest.raises(StateFileError) as excinfo:
        _ = load_access_urls(path)

    message = str(excinfo.value)
    assert "s3cret-short" not in message
    assert "access_urls" in message


def test_wrong_shape_is_a_clear_error_without_the_access_url(tmp_path: Path) -> None:
    path = provider_creds_path(tmp_path)
    # A value in the wrong shape, so the error has to name the problem without
    # quoting back the input it rejected -- credentials and all.
    _ = path.write_text(f'{{"access_urls": {{"redbark": ["{SHORT_ACCESS_URL}"]}}}}')

    with pytest.raises(StateFileError) as excinfo:
        _ = load_access_urls(path)

    message = str(excinfo.value)
    assert "invalid contents in" in message
    assert "s3cret-short" not in message


def test_stored_access_url_is_redacted_in_repr(tmp_path: Path) -> None:
    path = provider_creds_path(tmp_path)
    save_access_url(path, "redbark", SecretStr(ACCESS_URL))

    assert "s3cret-provider-password" not in repr(load_access_urls(path))


def test_stored_file_holds_the_real_access_url(tmp_path: Path) -> None:
    """The written file must hold the usable URL, not SecretStr's asterisks."""
    path = provider_creds_path(tmp_path)

    save_access_url(path, "redbark", SecretStr(ACCESS_URL))

    assert ACCESS_URL in path.read_text()


def test_concurrent_saves_do_not_lose_each_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two providers claimed at once must both end up in the store.

    A save rewrites the whole file, so a writer that reads it before another
    writer's save would store a version missing that provider -- losing an
    access URL whose one-time setup token has already been spent. The sleep
    widens the read-modify-write window that a writer would otherwise have to
    be unlucky to land in.
    """
    path = provider_creds_path(tmp_path)
    real_read_text = Path.read_text

    def slow_read_text(self: Path, *args: object, **kwargs: object) -> str:
        try:
            return real_read_text(self, *args, **kwargs)  # pyright: ignore[reportArgumentType]
        finally:
            # In a finally, so that the very first save -- whose read raises,
            # because the store is not there yet -- is widened too.
            time.sleep(0.2)

    monkeypatch.setattr(Path, "read_text", slow_read_text)
    savers = [
        threading.Thread(target=save_access_url, args=(path, key, SecretStr(access_url)))
        for key, access_url in (("redbark", ACCESS_URL), ("lunchflow", OTHER_ACCESS_URL))
    ]
    for saver in savers:
        saver.start()
    for saver in savers:
        saver.join(timeout=10)

    monkeypatch.undo()
    stored = load_access_urls(path)
    assert stored["redbark"].get_secret_value() == ACCESS_URL
    assert stored["lunchflow"].get_secret_value() == OTHER_ACCESS_URL
