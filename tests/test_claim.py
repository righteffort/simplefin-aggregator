import base64

import httpx
import respx
from typer.testing import CliRunner

from simplefin_aggregator.cli import app


runner = CliRunner()

CLAIM_URL = "https://bridge.example.com/simplefin/claim/some-token"
SETUP_TOKEN = base64.b64encode(CLAIM_URL.encode("ascii")).decode("ascii")


@respx.mock
def test_claim_prints_access_url_on_success() -> None:
    access_url = "https://user:pass@bridge.example.com/simplefin"
    route = respx.post(CLAIM_URL).mock(return_value=httpx.Response(200, text=access_url))

    result = runner.invoke(app, ["claim", SETUP_TOKEN])

    assert result.exit_code == 0
    assert result.stdout == access_url + "\n"
    assert "one-time-use" in result.stderr
    assert route.called


@respx.mock
def test_claim_is_repeatable() -> None:
    access_url = "https://user:pass@bridge.example.com/simplefin"
    route = respx.post(CLAIM_URL).mock(return_value=httpx.Response(200, text=access_url))

    first = runner.invoke(app, ["claim", SETUP_TOKEN])
    second = runner.invoke(app, ["claim", SETUP_TOKEN])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert first.stdout == second.stdout == access_url + "\n"
    assert route.call_count == 2


@respx.mock
def test_claim_with_rejected_token_fails() -> None:
    _ = respx.post(CLAIM_URL).mock(
        return_value=httpx.Response(403, text="claim token does not exist or was already used")
    )

    result = runner.invoke(app, ["claim", SETUP_TOKEN])

    assert result.exit_code == 1
    assert "403" in result.stderr
    assert result.stdout == ""


def test_claim_with_invalid_base64_fails() -> None:
    result = runner.invoke(app, ["claim", "not valid base64!!"])

    assert result.exit_code == 1
    assert "base64" in result.stderr
    assert result.stdout == ""
