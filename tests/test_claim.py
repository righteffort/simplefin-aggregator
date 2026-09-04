from __future__ import annotations

import base64
from typing import TYPE_CHECKING

import httpx2
from typer.testing import CliRunner

from simplefin_aggregator import cli


if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

runner = CliRunner()

CLAIM_URL = "https://bridge.example.com/simplefin/claim/some-token"
SETUP_TOKEN = base64.b64encode(CLAIM_URL.encode("ascii")).decode("ascii")


def _mock_client(handler: Callable[[httpx2.Request], httpx2.Response]) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(handler), follow_redirects=True)


def test_claim_prints_access_url_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    access_url = "https://user:pass@bridge.example.com/simplefin"
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        assert str(request.url) == CLAIM_URL
        return httpx2.Response(200, text=access_url)

    monkeypatch.setattr(cli, "_build_claim_client", lambda: _mock_client(handler))

    result = runner.invoke(cli.app, ["claim", SETUP_TOKEN])

    assert result.exit_code == 0
    assert result.stdout == access_url + "\n"
    assert "one-time-use" in result.stderr
    assert calls == 1


def test_claim_is_repeatable(monkeypatch: pytest.MonkeyPatch) -> None:
    access_url = "https://user:pass@bridge.example.com/simplefin"
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        assert str(request.url) == CLAIM_URL
        return httpx2.Response(200, text=access_url)

    monkeypatch.setattr(cli, "_build_claim_client", lambda: _mock_client(handler))

    first = runner.invoke(cli.app, ["claim", SETUP_TOKEN])
    second = runner.invoke(cli.app, ["claim", SETUP_TOKEN])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert first.stdout == second.stdout == access_url + "\n"
    assert calls == 2  # noqa: PLR2004


def test_claim_with_rejected_token_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == CLAIM_URL
        return httpx2.Response(403, text="claim token does not exist or was already used")

    monkeypatch.setattr(cli, "_build_claim_client", lambda: _mock_client(handler))

    result = runner.invoke(cli.app, ["claim", SETUP_TOKEN])

    assert result.exit_code == 1
    assert "403" in result.stderr
    assert result.stdout == ""


def test_claim_with_unreachable_provider_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        msg = "connection refused"
        raise httpx2.ConnectError(msg, request=request)

    monkeypatch.setattr(cli, "_build_claim_client", lambda: _mock_client(handler))

    result = runner.invoke(cli.app, ["claim", SETUP_TOKEN])

    assert result.exit_code == 1
    assert "could not reach" in result.stderr
    assert result.stdout == ""


def test_claim_with_invalid_base64_fails() -> None:
    result = runner.invoke(cli.app, ["claim", "not valid base64!!"])

    assert result.exit_code == 1
    assert "base64" in result.stderr
    assert result.stdout == ""
