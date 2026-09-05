"""Tests for the provider access URL store."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from simplefin_aggregator.provider_access_urls import (
    ACCESS_URLS_FILENAME,
    AccessUrlStoreError,
    access_urls_path,
    load_access_urls,
    save_access_url,
)


ACCESS_URL = "https://user:s3cret-provider-password@provider.invalid/simplefin"
OTHER_ACCESS_URL = "https://other:other-password@other.invalid/simplefin"
# Deliberately short: pydantic elides the middle of a long rejected input, so a
# long URL can hide its own password and make the leak test below pass for the
# wrong reason.
SHORT_ACCESS_URL = "https://u:s3cret-short@h.invalid/x"


def test_access_urls_path_uses_the_given_cache_dir(tmp_path: Path) -> None:
    assert access_urls_path(tmp_path) == tmp_path / ACCESS_URLS_FILENAME


def test_access_urls_path_defaults_to_the_platform_cache_dir() -> None:
    path = access_urls_path()

    assert path.name == ACCESS_URLS_FILENAME
    assert "simplefin-aggregator" in str(path)


def test_missing_file_loads_as_empty(tmp_path: Path) -> None:
    assert load_access_urls(access_urls_path(tmp_path)) == {}


def test_round_trip(tmp_path: Path) -> None:
    path = access_urls_path(tmp_path)

    save_access_url(path, "redbark", ACCESS_URL)

    stored = load_access_urls(path)
    assert stored["redbark"].get_secret_value() == ACCESS_URL


def test_save_creates_missing_directories(tmp_path: Path) -> None:
    path = access_urls_path(tmp_path / "nested" / "cache")

    save_access_url(path, "redbark", ACCESS_URL)

    assert load_access_urls(path)["redbark"].get_secret_value() == ACCESS_URL


def test_save_keeps_other_providers(tmp_path: Path) -> None:
    path = access_urls_path(tmp_path)

    save_access_url(path, "redbark", ACCESS_URL)
    save_access_url(path, "lunchflow", OTHER_ACCESS_URL)

    stored = load_access_urls(path)
    assert stored["redbark"].get_secret_value() == ACCESS_URL
    assert stored["lunchflow"].get_secret_value() == OTHER_ACCESS_URL


def test_save_replaces_the_entry_for_one_provider(tmp_path: Path) -> None:
    path = access_urls_path(tmp_path)

    save_access_url(path, "redbark", ACCESS_URL)
    save_access_url(path, "redbark", OTHER_ACCESS_URL)

    assert load_access_urls(path)["redbark"].get_secret_value() == OTHER_ACCESS_URL


def test_file_is_created_owner_read_write_only(tmp_path: Path) -> None:
    path = access_urls_path(tmp_path)

    save_access_url(path, "redbark", ACCESS_URL)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600  # noqa: PLR2004


def test_save_tightens_the_mode_of_an_existing_permissive_file(tmp_path: Path) -> None:
    path = access_urls_path(tmp_path)
    save_access_url(path, "redbark", ACCESS_URL)
    path.chmod(0o644)

    save_access_url(path, "lunchflow", OTHER_ACCESS_URL)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600  # noqa: PLR2004


def test_no_temporary_file_is_left_behind(tmp_path: Path) -> None:
    path = access_urls_path(tmp_path)

    save_access_url(path, "redbark", ACCESS_URL)

    assert [child.name for child in tmp_path.iterdir()] == [ACCESS_URLS_FILENAME]


def test_save_does_not_follow_a_symlink_at_a_guessable_temporary_path(tmp_path: Path) -> None:
    """`--cachedir` may be a directory another local user can write."""
    decoy = tmp_path / "decoy"
    _ = decoy.write_text("")
    (tmp_path / f"{ACCESS_URLS_FILENAME}.tmp").symlink_to(decoy)
    path = access_urls_path(tmp_path)

    save_access_url(path, "redbark", ACCESS_URL)

    assert decoy.read_text() == ""
    assert load_access_urls(path)["redbark"].get_secret_value() == ACCESS_URL


def test_save_fsyncs_before_renaming(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A rename that reaches disk before the data blocks yields an empty store."""
    path = access_urls_path(tmp_path)
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = Path.replace

    def recording_fsync(fd: int) -> None:
        events.append("fsync")
        real_fsync(fd)

    def recording_replace(self: Path, target: str | Path) -> Path:
        events.append("replace")
        return real_replace(self, target)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    monkeypatch.setattr(Path, "replace", recording_replace)

    save_access_url(path, "redbark", ACCESS_URL)

    assert events == ["fsync", "replace"]


def test_save_removes_the_temporary_file_when_the_rename_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup must survive a non-OSError failure, which `except OSError` missed."""
    path = access_urls_path(tmp_path)

    def exploding_replace(_self: Path, _target: str | Path) -> Path:
        raise KeyboardInterrupt

    monkeypatch.setattr(Path, "replace", exploding_replace)

    with pytest.raises(KeyboardInterrupt):
        save_access_url(path, "redbark", ACCESS_URL)

    assert list(tmp_path.iterdir()) == []


def test_load_warns_on_permissive_file_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = access_urls_path(tmp_path)
    save_access_url(path, "redbark", ACCESS_URL)
    path.chmod(0o644)

    _ = load_access_urls(path)

    assert "chmod 600" in capsys.readouterr().err


def test_load_does_not_warn_on_owner_only_file_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = access_urls_path(tmp_path)
    save_access_url(path, "redbark", ACCESS_URL)

    _ = load_access_urls(path)

    assert capsys.readouterr().err == ""


def test_malformed_json_is_a_clear_error(tmp_path: Path) -> None:
    path = access_urls_path(tmp_path)
    _ = path.write_text("{not json")

    with pytest.raises(AccessUrlStoreError, match="malformed JSON"):
        _ = load_access_urls(path)


def test_wrong_shape_is_a_clear_error_without_the_access_url(tmp_path: Path) -> None:
    path = access_urls_path(tmp_path)
    # A value in the wrong shape: pydantic's default rendering of the error
    # would quote the rejected input, credentials and all.
    _ = path.write_text(f'{{"access_urls": {{"redbark": ["{SHORT_ACCESS_URL}"]}}}}')

    with pytest.raises(AccessUrlStoreError) as excinfo:
        _ = load_access_urls(path)

    message = str(excinfo.value)
    assert "invalid access URL file" in message
    assert "s3cret-short" not in message


def test_stored_access_url_is_redacted_in_repr(tmp_path: Path) -> None:
    path = access_urls_path(tmp_path)
    save_access_url(path, "redbark", ACCESS_URL)

    assert "s3cret-provider-password" not in repr(load_access_urls(path))


def test_stored_file_holds_the_real_access_url(tmp_path: Path) -> None:
    """The written file must hold the usable URL, not SecretStr's asterisks."""
    path = access_urls_path(tmp_path)

    save_access_url(path, "redbark", ACCESS_URL)

    assert ACCESS_URL in path.read_text()
