# SPDX-License-Identifier: GPL-3.0-only

"""A minimal SimpleFIN server on loopback, for use in end-to-end tests.

Supports setup token exchange, basic auth credential check, `GET /accounts` with
optional `account` query parameter, and nothing else. Setup tokens can be
exchanged multiple times, no other access checks are enforced, and every other
query parameter is ignored.
"""

from __future__ import annotations

import base64
import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, cast, override
from urllib.parse import parse_qs, quote, urlsplit


if TYPE_CHECKING:
    from collections.abc import Generator, Mapping, Sequence

BASE_PATH = "/simplefin"


@dataclass(frozen=True)
class FakeUser:
    """One set of credentials: the setup token's secret, and the access URL's Basic Auth."""

    claim_secret: str
    username: str
    password: str


@dataclass(frozen=True)
class RunningFake:
    origin: str

    def setup_token(self, user: FakeUser) -> str:
        claim_url = f"{self.origin}{BASE_PATH}/claim/{user.claim_secret}"
        return base64.b64encode(claim_url.encode("ascii")).decode("ascii")


def _access_url(origin: str, user: FakeUser) -> str:
    scheme, host = origin.split("://", 1)
    userinfo = f"{quote(user.username, safe='')}:{quote(user.password, safe='')}"
    return f"{scheme}://{userinfo}@{host}{BASE_PATH}"


@contextmanager
def sf_server_fake(
    users: Sequence[FakeUser], accounts: Sequence[Mapping[str, object]]
) -> Generator[RunningFake]:
    """Serve on a free loopback port for the duration of the block.

    Every user sees the same `accounts`, each a SimpleFIN account object.
    """
    access_urls: dict[str, str] = {}
    basic_auth = {
        "Basic " + base64.b64encode(f"{user.username}:{user.password}".encode()).decode()
        for user in users
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            secret = self.path.removeprefix(f"{BASE_PATH}/claim/")
            if secret == self.path:
                self._reply(HTTPStatus.NOT_FOUND, b"")
            elif secret in access_urls:
                self._reply(HTTPStatus.OK, access_urls[secret].encode())
            else:
                self._reply(HTTPStatus.FORBIDDEN, b"")

        def do_GET(self) -> None:
            url = urlsplit(self.path)
            if url.path != f"{BASE_PATH}/accounts":
                self._reply(HTTPStatus.NOT_FOUND, b"")
            elif self.headers.get("Authorization") not in basic_auth:
                self._reply(HTTPStatus.FORBIDDEN, b"")
            else:
                wanted = parse_qs(url.query).get("account")
                answered = [
                    account for account in accounts if wanted is None or account["id"] in wanted
                ]
                body = json.dumps({"errors": [], "accounts": answered})
                self._reply(HTTPStatus.OK, body.encode())

        def _reply(self, status: HTTPStatus, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            _ = self.wfile.write(body)

        @override
        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = cast(tuple[str, int], server.server_address)[1]
    running = RunningFake(origin=f"http://127.0.0.1:{port}")
    access_urls.update({user.claim_secret: _access_url(running.origin, user) for user in users})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield running
    finally:
        server.shutdown()
        server.server_close()
