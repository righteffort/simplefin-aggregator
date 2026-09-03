from __future__ import annotations

import httpx2
from fastapi.testclient import TestClient

from simplefin_aggregator.app import create_app

from .support import install_provider_transport, make_config


AUTH = ("client-username", "s3cret-password")


def test_accounts_without_basic_auth_is_rejected() -> None:
    app = create_app(make_config())

    with TestClient(app) as client:
        response = client.get("/simplefin/accounts")

    assert response.status_code == 403


def test_accounts_response_body_is_byte_identical_to_provider() -> None:
    provider_body = b'{"accounts": [{"id": "acc-1", "name": "Checking", "balance": "12.34"}]}'

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, content=provider_body, headers={"content-type": "application/json"}
        )

    app = create_app(make_config())

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts", auth=AUTH)

    assert response.status_code == 200
    assert response.content == provider_body


def test_accounts_forwards_repeated_account_params() -> None:
    received_params: list[tuple[str, str]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        received_params.extend(request.url.params.multi_items())
        return httpx2.Response(200, json={"accounts": []})

    app = create_app(make_config())

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts?account=acc-1&account=acc-2", auth=AUTH)

    assert response.status_code == 200
    assert [v for k, v in received_params if k == "account"] == ["acc-1", "acc-2"]


def test_accounts_only_forwards_allowed_query_params() -> None:
    received_params: list[tuple[str, str]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        received_params.extend(request.url.params.multi_items())
        return httpx2.Response(200, json={"accounts": []})

    app = create_app(make_config())

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts?version=2&unexpected-param=nope", auth=AUTH)

    assert response.status_code == 200
    forwarded_keys = {k for k, _ in received_params}
    assert forwarded_keys == {"version"}


def test_accounts_provider_403_passes_through() -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(403, content=b"forbidden by provider")

    app = create_app(make_config())

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts", auth=AUTH)

    assert response.status_code == 403
    assert response.content == b"forbidden by provider"


def test_accounts_unreachable_provider_returns_502_with_simplefin_shaped_body() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        msg = "connection refused"
        raise httpx2.ConnectError(msg, request=request)

    app = create_app(make_config())

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts", auth=AUTH)

    assert response.status_code == 502
    body = response.json()  # pyright: ignore[reportAny]
    assert "errlist" in body
    assert body["errlist"][0]["msg"] == "connection refused"
