from __future__ import annotations

import httpx2
from fastapi.testclient import TestClient

from simplefin_aggregator.app import create_app

from .support import install_provider_transport, make_config


def test_info_proxies_provider_response_unchanged() -> None:
    provider_body = b'{"versions": ["1.0", "2.0"]}'

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, content=provider_body, headers={"content-type": "application/json"}
        )

    app = create_app(make_config())

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/info")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.content == provider_body


def test_info_does_not_require_basic_auth() -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"versions": ["2.0"]})

    app = create_app(make_config())

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/info")

    assert response.status_code == 200
