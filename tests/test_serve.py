# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from sf_agg import cli
from sf_agg.agg_creds import agg_creds_path
from sf_agg.app import CLAIM_PATH_PREFIX
from sf_agg.provider_access_urls import provider_creds_path, save_access_url

from .support import add_client_and_exchange


if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import Result


runner = CliRunner()

VALID_TOML = """
bind_host = "127.0.0.2"
bind_port = 9998
base_url = "http://127.0.0.1:9999"

[[custom_providers]]
key = "my-bank"
origin = "https://provider.example.com"

[[providers]]
key = "my-bank"
"""


ACCESS_URL = "https://user:s3cret-provider-password@provider.example.com/simplefin"


SECOND_PROVIDER_TOML = (
    VALID_TOML
    + """
[[custom_providers]]
key = "other-bank"
origin = "https://other.example.com"

[[providers]]
key = "other-bank"
"""
)


def _write_config(tmp_path: Path, contents: str) -> Path:
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(contents)
    config_path.chmod(0o600)
    return config_path


def _claim(tmp_path: Path, access_url: str = ACCESS_URL) -> None:
    """Put an access URL in the store, as `claim` would have."""
    save_access_url(provider_creds_path(tmp_path), "my-bank", SecretStr(access_url))


def _run_serve(tmp_path: Path) -> Result:
    # SF_AGG_DIR is never omitted in tests: without it serve would read the
    # developer's own config and credentials.
    return runner.invoke(cli.app, ["serve"], env={"SF_AGG_DIR": str(tmp_path)})


def _record_start(monkeypatch: pytest.MonkeyPatch, started: list[object]) -> None:
    """Stand in for uvicorn, recording whether the server would have come up."""

    def fake_run(app: object, **_kwargs: object) -> None:
        started.append(app)

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)  # pyright: ignore[reportPrivateLocalImportUsage]


def test_serve_runs_uvicorn_with_configured_bind_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = _write_config(tmp_path, VALID_TOML)

    calls: list[dict[str, object]] = []

    def fake_run(app: object, **kwargs: object) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)  # pyright: ignore[reportPrivateLocalImportUsage]

    _claim(tmp_path)

    result = _run_serve(tmp_path)

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0]["host"] == "127.0.0.2"
    assert calls[0]["port"] == 9998  # noqa: PLR2004


def test_serve_names_the_config_directory_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The directory is invisible state, so `serve` says which one it picked."""
    _ = _write_config(tmp_path, VALID_TOML)
    _claim(tmp_path)

    def fake_run(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)  # pyright: ignore[reportPrivateLocalImportUsage]

    result = _run_serve(tmp_path)

    assert result.exit_code == 0
    assert str(tmp_path) in result.stderr


def test_serve_wires_up_real_setup_token_redaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Serve's install_access_log_redaction() call uses app.py's real CLAIM_PATH_PREFIX."""
    _ = _write_config(tmp_path, VALID_TOML)

    def fake_run(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)  # pyright: ignore[reportPrivateLocalImportUsage]

    _claim(tmp_path)

    result = _run_serve(tmp_path)
    assert result.exit_code == 0

    logger = logging.getLogger("uvicorn.access")
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:1", "POST", f"{CLAIM_PATH_PREFIX}claim-secret", "1.1", 200),
        exc_info=None,
    )
    _ = logger.filter(record)

    assert "claim-secret" not in record.getMessage()


def test_serve_fails_without_starting_uvicorn_on_invalid_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = _write_config(tmp_path, "this is not [valid toml")

    calls: list[object] = []

    def fake_run(*args: object, **_kwargs: object) -> None:
        calls.append(args)

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)  # pyright: ignore[reportPrivateLocalImportUsage]

    result = _run_serve(tmp_path)

    assert result.exit_code == 1
    assert calls == []
    assert "error" in result.stderr


def _fake_uvicorn(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Replace uvicorn.run, returning the list it records its calls in."""
    calls: list[object] = []

    def fake_run(*args: object, **_kwargs: object) -> None:
        calls.append(args)

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)  # pyright: ignore[reportPrivateLocalImportUsage]
    return calls


def test_serve_fails_when_the_provider_has_not_been_claimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = _write_config(tmp_path, VALID_TOML)
    calls = _fake_uvicorn(monkeypatch)

    result = _run_serve(tmp_path)

    assert result.exit_code == 1
    assert calls == []
    assert "no access URL stored for provider 'my-bank'" in result.stderr


def test_serve_fails_when_one_of_several_providers_has_not_been_claimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any unclaimed provider stops the server from starting."""
    _ = _write_config(tmp_path, SECOND_PROVIDER_TOML)
    _claim(tmp_path)
    calls = _fake_uvicorn(monkeypatch)

    result = _run_serve(tmp_path)

    assert result.exit_code == 1
    assert calls == [], "the claimed provider gets no server of its own"
    assert "no access URL stored for provider 'other-bank'" in result.stderr


def test_serve_reports_every_unresolved_provider_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither an unclaimed provider nor an origin mismatch hides the other's report."""
    moved_origin = SECOND_PROVIDER_TOML.replace(
        'origin = "https://provider.example.com"', 'origin = "https://moved.example.com"'
    )
    _ = _write_config(tmp_path, moved_origin)
    _claim(tmp_path)  # claimed for 'my-bank', whose origin just moved
    calls = _fake_uvicorn(monkeypatch)

    result = _run_serve(tmp_path)

    assert result.exit_code == 1
    assert calls == []
    assert "expected https://moved.example.com" in result.stderr
    assert "no access URL stored for provider 'other-bank'" in result.stderr
    assert "s3cret-provider-password" not in result.stderr


def test_serve_fails_when_the_stored_access_url_no_longer_matches_the_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Config drift: the entry's origin was edited after the URL was claimed."""
    moved_origin = VALID_TOML.replace(
        'origin = "https://provider.example.com"', 'origin = "https://other.example.com"'
    )
    _ = _write_config(tmp_path, moved_origin)
    _claim(tmp_path)
    calls = _fake_uvicorn(monkeypatch)

    result = _run_serve(tmp_path)

    assert result.exit_code == 1
    assert calls == []
    assert "expected https://other.example.com" in result.stderr
    assert "s3cret-provider-password" not in result.stderr


def test_serve_fails_on_a_malformed_access_url_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = _write_config(tmp_path, VALID_TOML)
    _ = provider_creds_path(tmp_path).write_text("{not json")
    calls = _fake_uvicorn(monkeypatch)

    result = _run_serve(tmp_path)

    assert result.exit_code == 1
    assert calls == []
    assert "malformed JSON" in result.stderr


def test_serve_starts_with_no_clients_yet_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The legitimate state between installing the server and adding the first client.

    A failure here would make the order of two setup steps load-bearing for no
    reason; the server runs and refuses every request until a client exchanges
    a setup token.
    """
    _ = _write_config(tmp_path, VALID_TOML)
    _claim(tmp_path)
    started: list[object] = []
    _record_start(monkeypatch, started)

    result = _run_serve(tmp_path)

    assert result.exit_code == 0
    assert len(started) == 1
    assert "no clients yet" in result.stderr


def test_serve_refuses_to_start_on_a_malformed_aggregator_creds_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found before uvicorn binds, not by the first client that tries to authenticate."""
    _ = _write_config(tmp_path, VALID_TOML)
    _claim(tmp_path)
    _ = agg_creds_path(tmp_path).write_text("{not json")
    started: list[object] = []
    _record_start(monkeypatch, started)

    result = _run_serve(tmp_path)

    assert result.exit_code == 1
    assert started == []
    assert "malformed JSON" in result.stderr


@pytest.mark.skipif(
    os.name == "posix" and os.geteuid() == 0,
    reason="root writes a directory whatever its mode says",
)
def test_serve_refuses_to_start_when_it_could_not_record_a_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The server writes the store on every claim, so a read-only directory loses them."""
    _ = _write_config(tmp_path, VALID_TOML)
    _claim(tmp_path)
    _ = add_client_and_exchange(tmp_path)
    started: list[object] = []
    _record_start(monkeypatch, started)
    tmp_path.chmod(0o500)

    try:
        result = _run_serve(tmp_path)
    finally:
        tmp_path.chmod(0o700)

    assert result.exit_code == 1
    assert started == []
    assert "not writable" in result.stderr
