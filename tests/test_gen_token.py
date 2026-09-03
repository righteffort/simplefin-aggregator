from __future__ import annotations

import base64
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from simplefin_aggregator import cli
from simplefin_aggregator.access_url import build_access_url
from simplefin_aggregator.app import create_app
from simplefin_aggregator.config import load_config


if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()

VALID_TOML = """
bind_host = "127.0.0.1"
bind_port = 9999
base_url = "http://127.0.0.1:9999"
claim_token = "my-secret-token"

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


def test_gen_token_prints_a_setup_token_that_decodes_to_the_claim_url(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, VALID_TOML)

    result = runner.invoke(cli.app, ["gen-token", "--config", str(config_path)])

    assert result.exit_code == 0
    decoded = base64.b64decode(result.stdout.strip(), validate=True).decode("ascii")
    assert decoded == "http://127.0.0.1:9999/simplefin/claim/my-secret-token"


def test_gen_token_output_actually_claims_successfully_against_the_real_app(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, VALID_TOML)
    config = load_config(config_path)

    result = runner.invoke(cli.app, ["gen-token", "--config", str(config_path)])
    claim_url = base64.b64decode(result.stdout.strip(), validate=True).decode("ascii")
    claim_path = claim_url.removeprefix(config.base_url)

    client = TestClient(create_app(config))
    response = client.post(claim_path)

    assert response.status_code == 200
    assert response.text == build_access_url(config)


def test_gen_token_fails_on_invalid_config(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "this is not [valid toml")

    result = runner.invoke(cli.app, ["gen-token", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "error" in result.stderr
    assert result.stdout == ""
