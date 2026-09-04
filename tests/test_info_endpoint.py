from __future__ import annotations

from http import HTTPStatus

import httpx2
from fastapi.testclient import TestClient

from .support import install_provider_transport, make_app


def test_info_proxies_provider_response_unchanged() -> None:
    provider_body = b'{"versions": ["1.0", "2.0"]}'

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            HTTPStatus.OK, content=provider_body, headers={"content-type": "application/json"}
        )

    app = make_app()

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/info")

    assert response.status_code == HTTPStatus.OK
    assert response.headers["content-type"] == "application/json"
    assert response.content == provider_body


def test_info_does_not_require_basic_auth() -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(HTTPStatus.OK, json={"versions": ["2.0"]})

    app = make_app()

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/info")

    assert response.status_code == HTTPStatus.OK
