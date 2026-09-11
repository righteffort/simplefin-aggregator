from __future__ import annotations

import logging
import threading
from base64 import b64decode
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, cast, override

import httpx2
from fastapi.testclient import TestClient

from .support import (
    echoing_provider,
    install_provider_transport,
    make_access_urls,
    make_app,
    make_claimed_app,
    make_config,
)


if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    import pytest


def test_accounts_without_basic_auth_is_rejected(tmp_path: Path) -> None:
    _ = make_claimed_app(tmp_path)
    app = make_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/simplefin/accounts")

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_accounts_relays_every_key_a_provider_sent_inside_an_account(tmp_path: Path) -> None:
    """Requirement: this aggregator rebuilds the response, and account contents survive that."""
    account = {"id": "acc-1", "name": "Checking", "balance": "12.34", "unknown-key": [1, 2]}

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(HTTPStatus.OK, json={"accounts": [account]})

    auth = make_claimed_app(tmp_path)
    app = make_app(tmp_path)

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts", auth=auth)

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"accounts": [account], "errors": []}


def test_accounts_forwards_repeated_account_params(tmp_path: Path) -> None:
    received_params: list[tuple[str, str]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        received_params.extend(request.url.params.multi_items())
        return httpx2.Response(HTTPStatus.OK, json={"accounts": []})

    auth = make_claimed_app(tmp_path)
    app = make_app(tmp_path)

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts?account=acc-1&account=acc-2", auth=auth)

    assert response.status_code == HTTPStatus.OK
    assert [v for k, v in received_params if k == "account"] == ["acc-1", "acc-2"]


def test_accounts_only_forwards_allowed_query_params(tmp_path: Path) -> None:
    received_params: list[tuple[str, str]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        received_params.extend(request.url.params.multi_items())
        return httpx2.Response(HTTPStatus.OK, json={"accounts": []})

    auth = make_claimed_app(tmp_path)
    app = make_app(tmp_path)

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts?version=2&unexpected-param=nope", auth=auth)

    assert response.status_code == HTTPStatus.OK
    forwarded_keys = {k for k, _ in received_params}
    assert forwarded_keys == {"version"}


def test_accounts_does_not_relay_a_providers_403_as_its_own(tmp_path: Path) -> None:
    """Requirement: 403 here is about the client app's credentials, not a provider's.

    Relaying it would tell the client app to re-authenticate against the wrong
    party -- and there is no answer at all once two providers disagree.
    """

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(HTTPStatus.FORBIDDEN, content=b"forbidden by provider")

    auth = make_claimed_app(tmp_path)
    app = make_app(tmp_path)

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts", auth=auth)

    assert response.status_code == HTTPStatus.OK
    body = response.json()  # pyright: ignore[reportAny]
    assert body["accounts"] == []
    assert b"forbidden by provider" not in response.content


def test_accounts_provider_redirect_is_not_relayed_to_the_client_app(tmp_path: Path) -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            HTTPStatus.FOUND, headers={"location": "https://attacker.example.net/accounts"}
        )

    auth = make_claimed_app(tmp_path)
    app = make_app(tmp_path)

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts", auth=auth, follow_redirects=False)

    assert response.status_code == HTTPStatus.OK
    assert "location" not in response.headers
    assert b"attacker.example.net" not in response.content


def test_accounts_answers_a_v1_body_when_a_provider_is_unreachable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Requirement: a dead provider still gets the client app a well-formed v1 response.

    Most client apps stop parsing a body they got with a failing status, so a
    non-2xx here would cost the user everything the other providers returned.
    """

    async def handler(request: httpx2.Request) -> httpx2.Response:
        msg = "connection refused"
        raise httpx2.ConnectError(msg, request=request)

    auth = make_claimed_app(tmp_path)
    app = make_app(tmp_path)

    with TestClient(app) as client, caplog.at_level(logging.WARNING):
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts", auth=auth)

    assert response.status_code == HTTPStatus.OK
    body = cast("dict[str, object]", response.json())
    assert set(body) == {"accounts", "errors"}, "errlist is a v2 field this application never emits"
    assert body["accounts"] == []
    assert "my-bank" in caplog.text
    assert "ConnectError" in caplog.text, "the operator is told what kind of failure it was"
    assert "connection refused" not in response.text, "not the client app's business"


@contextmanager
def _loopback_provider() -> Generator[int]:
    """A provider the app can really reach, answering every GET with a redirect.

    A live server rather than a mock transport, so the client exercised is the
    whole of what `build_provider_client` returns. Substituting a transport
    would replace the part that renders the outbound URL and sends it, which is
    the part this test is about.
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


def _echo_the_basic_auth_password(request_text: str) -> str:
    """Compose a status line carrying back the password the request just sent.

    The shortest way for a provider to put a credential where a rendered
    exception would carry it.
    """
    credentials = "no-credentials-seen"
    for line in request_text.split("\r\n"):
        if line.lower().startswith("authorization: basic "):
            credentials = b64decode(line.split(" ", 2)[2]).decode()
    return f"NOT-HTTP {credentials}"


def test_a_provider_cannot_get_its_credential_into_a_log_by_echoing_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Requirement: nothing a provider puts on the wire is rendered by this application.

    A provider is the attacker in this project's threat model -- a claim made
    against a lookalike domain leaves one holding the aggregator's Basic Auth
    password -- and here it hands that password straight back inside a
    malformed status line, which is a position the HTTP library quotes.
    """
    password = "s3cret-provider-password"  # noqa: S105
    auth = make_claimed_app(tmp_path)

    with echoing_provider(_echo_the_basic_auth_password) as (port, echoed):
        app = make_app(
            tmp_path,
            make_config(root=f"http://127.0.0.1:{port}/simplefin"),
            make_access_urls(f"http://user:{password}@127.0.0.1:{port}/simplefin"),
        )
        with TestClient(app) as client:
            response = client.get("/simplefin/accounts", auth=auth)

    assert echoed == [f"NOT-HTTP user:{password}"], (
        "the provider did hand the credential back, which is what the rest of this tests"
    )
    assert response.status_code == HTTPStatus.OK
    assert "RemoteProtocolError" in caplog.text, "and this application saw the malformed reply"
    assert password not in response.text
    assert password not in caplog.text


def test_a_provider_request_names_no_credentials_in_the_logs_or_the_body(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Everything the app does with a stored access URL, with nothing stubbed out.

    The provider is reached rather than merely attempted, because the one line
    that renders the outbound URL in full -- httpx2's own request log -- is
    written only when a response comes back. That line is what would carry the
    credentials if they were ever in the URL instead of in `auth=`. The span
    also covers validating the stored URL at startup, building the client from
    it, and rendering the failure to the client app.
    """
    password = "s3cret-provider-password"  # noqa: S105
    auth = make_claimed_app(tmp_path)

    with _loopback_provider() as port, caplog.at_level(logging.INFO):
        app = make_app(
            tmp_path,
            make_config(root=f"http://127.0.0.1:{port}/simplefin"),
            make_access_urls(f"http://user:{password}@127.0.0.1:{port}/simplefin"),
        )
        with TestClient(app) as client:
            response = client.get("/simplefin/accounts", auth=auth)

    assert response.status_code == HTTPStatus.OK
    # Named exactly, since the test client logs a request line of its own: this
    # is the provider request, rendered as httpx2 rendered it.
    assert f"HTTP Request: GET http://127.0.0.1:{port}/simplefin/accounts" in caplog.text
    assert "provider request: my-bank" in caplog.text
    assert password not in caplog.text
    assert password not in response.text
