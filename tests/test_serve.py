from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from simplefin_aggregator import cli
from simplefin_aggregator.app import CLAIM_PATH_PREFIX
from simplefin_aggregator.provider_access_urls import access_urls_path, save_access_url


if TYPE_CHECKING:
    from pathlib import Path

    import pytest


runner = CliRunner()

VALID_TOML = """
bind_host = "127.0.0.2"
bind_port = 9998
base_url = "http://127.0.0.1:9999"
claim_token = "claim-token"

[client]
username = "client-username"
password = "s3cret-password"

[[allowlist]]
slug = "my-bank"
label = "My Bank"
root = "https://provider.example.com/simplefin"

[[providers]]
provider_key = "my-bank"
"""


ACCESS_URL = "https://user:s3cret-provider-password@provider.example.com/simplefin"


def _write_config(tmp_path: Path, contents: str) -> Path:
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(contents)
    config_path.chmod(0o600)
    return config_path


def _claim(tmp_path: Path, access_url: str = ACCESS_URL) -> None:
    """Put an access URL in the store, as `claim` would have."""
    save_access_url(access_urls_path(tmp_path), "my-bank", access_url)


def _serve_args(tmp_path: Path, config_path: Path) -> list[str]:
    # --cachedir is never omitted in tests: without it serve would read the
    # real store in the developer's cache directory.
    return ["serve", "--config", str(config_path), "--cachedir", str(tmp_path)]


def test_serve_runs_uvicorn_with_configured_bind_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _write_config(tmp_path, VALID_TOML)

    calls: list[dict[str, object]] = []

    def fake_run(app: object, **kwargs: object) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)  # pyright: ignore[reportPrivateLocalImportUsage]

    _claim(tmp_path)

    result = runner.invoke(cli.app, _serve_args(tmp_path, config_path))

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0]["host"] == "127.0.0.2"
    assert calls[0]["port"] == 9998  # noqa: PLR2004


def test_serve_wires_up_real_claim_token_redaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Serve's install_access_log_redaction() call uses app.py's real CLAIM_PATH_PREFIX."""
    config_path = _write_config(tmp_path, VALID_TOML)

    def fake_run(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)  # pyright: ignore[reportPrivateLocalImportUsage]

    _claim(tmp_path)

    result = runner.invoke(cli.app, _serve_args(tmp_path, config_path))
    assert result.exit_code == 0

    logger = logging.getLogger("uvicorn.access")
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:1", "POST", f"{CLAIM_PATH_PREFIX}claim-token", "1.1", 200),
        exc_info=None,
    )
    _ = logger.filter(record)

    assert "claim-token" not in record.getMessage()


def test_serve_fails_without_starting_uvicorn_on_invalid_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _write_config(tmp_path, "this is not [valid toml")

    calls: list[object] = []

    def fake_run(*args: object, **_kwargs: object) -> None:
        calls.append(args)

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)  # pyright: ignore[reportPrivateLocalImportUsage]

    result = runner.invoke(cli.app, _serve_args(tmp_path, config_path))

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
    config_path = _write_config(tmp_path, VALID_TOML)
    calls = _fake_uvicorn(monkeypatch)

    result = runner.invoke(cli.app, _serve_args(tmp_path, config_path))

    assert result.exit_code == 1
    assert calls == []
    assert "no access URL stored for provider 'my-bank'" in result.stderr


def test_serve_fails_when_the_stored_access_url_no_longer_matches_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Config drift: the entry's root was edited after the URL was claimed."""
    moved_root = VALID_TOML.replace(
        'root = "https://provider.example.com/simplefin"',
        'root = "https://other.example.com/simplefin"',
    )
    config_path = _write_config(tmp_path, moved_root)
    _claim(tmp_path)
    calls = _fake_uvicorn(monkeypatch)

    result = runner.invoke(cli.app, _serve_args(tmp_path, config_path))

    assert result.exit_code == 1
    assert calls == []
    assert "https://other.example.com/simplefin/" in result.stderr
    assert "s3cret-provider-password" not in result.stderr


def test_serve_fails_on_a_malformed_access_url_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _write_config(tmp_path, VALID_TOML)
    _ = access_urls_path(tmp_path).write_text("{not json")
    calls = _fake_uvicorn(monkeypatch)

    result = runner.invoke(cli.app, _serve_args(tmp_path, config_path))

    assert result.exit_code == 1
    assert calls == []
    assert "malformed JSON" in result.stderr
