from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from simplefin_aggregator import cli
from simplefin_aggregator.app import CLAIM_PATH_PREFIX


if TYPE_CHECKING:
    from pathlib import Path

    import pytest


runner = CliRunner()

VALID_TOML = """
bind_host = "127.0.0.1"
bind_port = 9999
base_url = "http://127.0.0.1:9999"
claim_token = "claim-token"

[client]
username = "client-username"
password = "s3cret-password"

[[providers]]
name = "my-bank"
access_url = "https://user:pass@provider.example.com/simplefin"
"""


def _write_config(tmp_path: Path, contents: str) -> Path:
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(contents)
    config_path.chmod(0o600)
    return config_path


def test_serve_runs_uvicorn_with_configured_bind_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _write_config(tmp_path, VALID_TOML)

    calls: list[dict[str, object]] = []

    def fake_run(app: object, **kwargs: object) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)  # pyright: ignore[reportPrivateLocalImportUsage]

    result = runner.invoke(cli.app, ["serve", "--config", str(config_path)])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 9999


def test_serve_wires_up_real_claim_token_redaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """serve's install_access_log_redaction() call uses app.py's real CLAIM_PATH_PREFIX."""
    config_path = _write_config(tmp_path, VALID_TOML)

    def fake_run(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)  # pyright: ignore[reportPrivateLocalImportUsage]

    result = runner.invoke(cli.app, ["serve", "--config", str(config_path)])
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

    result = runner.invoke(cli.app, ["serve", "--config", str(config_path)])

    assert result.exit_code == 1
    assert calls == []
    assert "error" in result.stderr
