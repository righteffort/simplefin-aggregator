from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, cast, override

import httpx2
from fastapi.testclient import TestClient

from .support import install_provider_transport, make_access_urls, make_app, make_config


if TYPE_CHECKING:
    from collections.abc import Generator

    import pytest

AUTH = ("client-username", "s3cret-password")


def test_accounts_without_basic_auth_is_rejected() -> None:
    app = make_app()

    with TestClient(app) as client:
        response = client.get("/simplefin/accounts")

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_accounts_response_body_is_byte_identical_to_provider() -> None:
    provider_body = b'{"accounts": [{"id": "acc-1", "name": "Checking", "balance": "12.34"}]}'

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            HTTPStatus.OK, content=provider_body, headers={"content-type": "application/json"}
        )

    app = make_app()

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts", auth=AUTH)

    assert response.status_code == HTTPStatus.OK
    assert response.content == provider_body


def test_accounts_forwards_repeated_account_params() -> None:
    received_params: list[tuple[str, str]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        received_params.extend(request.url.params.multi_items())
        return httpx2.Response(HTTPStatus.OK, json={"accounts": []})

    app = make_app()

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts?account=acc-1&account=acc-2", auth=AUTH)

    assert response.status_code == HTTPStatus.OK
    assert [v for k, v in received_params if k == "account"] == ["acc-1", "acc-2"]


def test_accounts_only_forwards_allowed_query_params() -> None:
    received_params: list[tuple[str, str]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        received_params.extend(request.url.params.multi_items())
        return httpx2.Response(HTTPStatus.OK, json={"accounts": []})

    app = make_app()

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts?version=2&unexpected-param=nope", auth=AUTH)

    assert response.status_code == HTTPStatus.OK
    forwarded_keys = {k for k, _ in received_params}
    assert forwarded_keys == {"version"}


def test_accounts_provider_403_passes_through() -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(HTTPStatus.FORBIDDEN, content=b"forbidden by provider")

    app = make_app()

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts", auth=AUTH)

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.content == b"forbidden by provider"


def test_accounts_provider_redirect_is_not_relayed_to_the_client_app() -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            HTTPStatus.FOUND, headers={"location": "https://attacker.example.net/accounts"}
        )

    app = make_app()

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts", auth=AUTH, follow_redirects=False)

    assert response.status_code == HTTPStatus.BAD_GATEWAY
    assert "location" not in response.headers
    assert b"attacker.example.net" not in response.content


def test_accounts_unreachable_provider_returns_502_with_simplefin_shaped_body() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        msg = "connection refused"
        raise httpx2.ConnectError(msg, request=request)

    app = make_app()

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts", auth=AUTH)

    assert response.status_code == HTTPStatus.BAD_GATEWAY
    body = response.json()  # pyright: ignore[reportAny]
    assert "errlist" in body
    assert body["errlist"][0]["msg"] == "connection refused"


@contextmanager
def _loopback_provider() -> Generator[int]:
    """A provider the app can really reach, answering every GET with a redirect.

    A live server rather than a mock transport: the client under test is the
    one `build_provider_client` builds, and the URL it is asked for only
    reaches a log record once a response comes back.
    """

    class _Redirecting(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("location", "https://attacker.example.net/accounts")
            self.end_headers()

        @override
        def log_message(self, format: str, *args: object) -> None:
            """Silence the server's own request logging, which is not what is under test."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Redirecting)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield cast(tuple[str, int], server.server_address)[1]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_a_provider_request_names_no_credentials_in_the_logs_or_the_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Everything the app does with a stored access URL, with nothing stubbed out.

    The provider is reached rather than merely attempted, because the one line
    that renders the outbound URL in full -- httpx2's own request log -- is
    written only when a response comes back. That line is what would carry the
    credentials if they were ever in the URL instead of in `auth=`. The span
    also covers validating the stored URL at startup, building the client from
    it, and rendering the failure to the client app.
    """
    password = "s3cret-provider-password"

    with _loopback_provider() as port, caplog.at_level(logging.INFO):
        app = make_app(
            make_config(root=f"http://127.0.0.1:{port}/simplefin"),
            make_access_urls(f"http://user:{password}@127.0.0.1:{port}/simplefin"),
        )
        with TestClient(app) as client:
            response = client.get("/simplefin/accounts", auth=AUTH)

    assert response.status_code == HTTPStatus.BAD_GATEWAY
    # Named exactly, since the test client logs a request line of its own: this
    # is the provider request, rendered as httpx2 rendered it.
    assert f"HTTP Request: GET http://127.0.0.1:{port}/simplefin/accounts" in caplog.text
    assert "provider request: my-bank" in caplog.text
    assert password not in caplog.text
    assert password not in response.text
