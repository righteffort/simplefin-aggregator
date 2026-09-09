"""The whole flow, checked for the one thing this design exists to prevent.

An app is issued a setup token, claims it, uses what it gets back, and is
listed -- and none of the three secrets involved appears in anything a person
or a log file would see. This reads as a confirmation rather than a defense:
the store holds digests, so there is nothing in it to leak in the first place.
"""

from __future__ import annotations

import base64
import logging
from http import HTTPStatus
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

import httpx2
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from simplefin_aggregator import cli
from simplefin_aggregator.app_tokens import app_tokens_path

from .support import PROVIDER_KEY, install_provider_transport, make_app


if TYPE_CHECKING:
    from pathlib import Path

    import pytest

runner = CliRunner()

BASE_URL = "http://127.0.0.1:9999"
CONFIG_TOML = f"""
base_url = "{BASE_URL}"

[[custom_providers]]
key = "{PROVIDER_KEY}"
label = "Test Provider"
root = "https://provider.example.com/simplefin"

[[providers]]
key = "{PROVIDER_KEY}"
"""


def test_a_full_flow_puts_no_credential_anywhere_a_person_would_see(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(CONFIG_TOML)
    config_path.chmod(0o600)

    with caplog.at_level(logging.DEBUG):
        issued = runner.invoke(
            cli.app,
            [
                "app",
                "new",
                "--key",
                "actual-budget",
                "--label",
                "AB",
                "--config-dir",
                str(tmp_path),
            ],
        )
        setup_token = issued.stdout.strip()
        claim_url = base64.b64decode(setup_token, validate=True).decode("ascii")
        claim_secret = claim_url.rsplit("/", maxsplit=1)[1]

        app = make_app(tmp_path)
        with TestClient(app) as client:
            claimed = client.post(claim_url.removeprefix(BASE_URL))
            access_url = claimed.text
            parsed = urlsplit(access_url)
            assert parsed.username is not None
            assert parsed.password is not None
            username, password = unquote(parsed.username), unquote(parsed.password)

            install_provider_transport(
                app, PROVIDER_KEY, lambda _request: httpx2.Response(200, json={"accounts": []})
            )
            accounts = client.get("/simplefin/accounts", auth=(username, password))

        listed = runner.invoke(cli.app, ["app", "list", "--config-dir", str(tmp_path)])

    assert claimed.status_code == HTTPStatus.OK
    assert accounts.status_code == HTTPStatus.OK
    assert listed.exit_code == 0

    stored = app_tokens_path(tmp_path).read_text()
    # Everything the flow put somewhere a person or a log file would see,
    # except the one stdout line the setup token is deliberately printed on.
    # This application's own log records. The others captured here belong to
    # the test client, standing in for a client app that would run in another
    # process: what this server logs for that same request is uvicorn's access
    # line, and `test_serve.py` pins that the claim token is redacted out of
    # it. A provider request's log line is pinned in `test_accounts_endpoint`.
    logged = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("simplefin_aggregator")
    )
    surfaces = [
        issued.stderr,
        listed.stdout,
        listed.stderr,
        logged,
        stored,
        config_path.read_text(),
    ]
    everything_else = "\n".join(surfaces)

    for secret in (claim_secret, username, password):
        assert secret not in everything_else

    # The setup token is on stdout once and that is the whole of its life; the
    # credentials are minted later and by the server, so `app new`'s output is
    # not where they could have gone.
    assert claim_secret not in listed.stdout
    assert issued.stdout == f"{setup_token}\n"
