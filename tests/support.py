# SPDX-License-Identifier: GPL-3.0-only

"""Shared test helpers."""

from __future__ import annotations

import socket
import threading
from base64 import b64decode
from contextlib import contextmanager
from typing import TYPE_CHECKING, NamedTuple, cast

import httpx2
from pydantic import SecretStr

from sf_agg.agg_creds import agg_creds_path, exchange_agg_creds, new_agg_creds, update_agg_creds
from sf_agg.app import _AppState, create_app  # pyright: ignore[reportPrivateUsage]
from sf_agg.config import Config, config_from_mapping


if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Generator, Mapping
    from pathlib import Path

    from fastapi import FastAPI

    MockHandler = (
        Callable[[httpx2.Request], httpx2.Response]
        | Callable[[httpx2.Request], Coroutine[None, None, httpx2.Response]]
    )

# The provider every test shares: a config-supplied custom provider, its key
# used as the configured key, and an access URL under its root.
PROVIDER_KEY = "my-bank"
PROVIDER_ROOT = "https://provider.example.com/simplefin"
PROVIDER_ACCESS_URL = "https://user:pass@provider.example.com/simplefin"


def install_provider_transport(app: FastAPI, key: str, handler: MockHandler) -> None:
    """Replace a provider's AsyncClient with one backed by a MockTransport.

    Must be called after the app's lifespan has started (e.g. inside a
    `with TestClient(app) as client:` block), since that's what creates
    app.state.provider_clients in the first place.

    The replacement is built here rather than derived from the real one, so it
    carries none of `build_provider_client`'s configuration -- no `auth=`, and
    a `base_url` that never held credentials. A test asserting where
    credentials end up passes vacuously against this. Give it a real socket
    instead, the way `_loopback_provider` does.
    """
    state = cast(_AppState, app.state.app_state)
    if key not in state.provider_clients:
        # A mock under a key the app never reads leaves the real client in
        # place, free to make the outbound request the mock was meant to stop.
        msg = f"no provider client under {key!r}; the app has {sorted(state.provider_clients)}"
        raise ValueError(msg)
    state.provider_clients[key] = httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        base_url=f"https://{key}.example.com/simplefin",
        follow_redirects=False,
    )


class ProviderSpec(NamedTuple):
    """One provider for `make_config` and `make_access_urls` to build.

    A `prefix` of None leaves the field out of the config entry, so the test
    gets whatever the model defaults it to; "" is an explicit blank prefix. The
    access URL is spelled out rather than derived from the root, because
    `create_app` checks one against the other and a test that means them to
    disagree is entitled to say so.
    """

    key: str = PROVIDER_KEY
    root: str = PROVIDER_ROOT
    prefix: str | None = None
    access_url: str = PROVIDER_ACCESS_URL


def make_config(
    *providers: ProviderSpec,
    base_url: str = "http://127.0.0.1:8080",
    allow_multiple_blank_prefixes: bool = False,
) -> Config:
    """Build a Config the same way load_config does: from an untyped dict.

    Given no providers, the one shared fixture provider.
    """
    specs = providers or (ProviderSpec(),)
    return config_from_mapping(
        {
            "base_url": base_url,
            "providers": [
                {"key": spec.key}
                if spec.prefix is None
                else {"key": spec.key, "prefix": spec.prefix}
                for spec in specs
            ],
            "custom_providers": [{"key": spec.key, "root": spec.root} for spec in specs],
            "allow_multiple_blank_prefixes": allow_multiple_blank_prefixes,
        }
    )


def add_client_and_exchange(config_dir: Path, key: str = "test-client") -> tuple[str, str]:
    """Add a client and exchange its setup token, returning the credentials issued.

    They exist nowhere else: the store keeps only their digests.
    """
    with update_agg_creds(agg_creds_path(config_dir)) as creds:
        _, unexchanged = new_agg_creds()
        credentials, creds[key] = exchange_agg_creds(unexchanged)
    return credentials.username.get_secret_value(), credentials.password.get_secret_value()


def add_client(config_dir: Path, key: str = "test-client") -> str:
    """Add a client, returning its claim secret, which the store keeps only a digest of."""
    with update_agg_creds(agg_creds_path(config_dir)) as creds:
        secret, creds[key] = new_agg_creds()
    return secret


def make_access_urls(*providers: ProviderSpec) -> dict[str, SecretStr]:
    """The access URL store's contents for a config built from the same specs."""
    specs = providers or (ProviderSpec(),)
    return {spec.key: SecretStr(spec.access_url) for spec in specs}


def make_app(
    config_dir: Path,
    config: Config | None = None,
    access_urls: Mapping[str, SecretStr] | None = None,
) -> FastAPI:
    """create_app with the shared test config, its one claimed provider, and a store.

    The store is named rather than passed, because the server reads it per
    request: a test that revokes a client mid-run writes the file and the next
    request sees it.
    """
    return create_app(
        config if config is not None else make_config(),
        access_urls if access_urls is not None else make_access_urls(),
        agg_creds_path(config_dir),
    )


@contextmanager
def echoing_provider(compose: Callable[[str], str]) -> Generator[tuple[int, list[str]]]:
    """Serve one request from a loopback socket, replying with a malformed status line.

    `compose` builds that line from the raw request text, so the caller
    chooses what comes back -- a header the request carried, the path it was
    made on.

    Yields the port to aim a client at, and the status lines the socket
    accepted. Assert on those first: they say the fake did its part, which is
    the premise of whatever the test then asserts about the application. They
    do not say the bytes arrived, and nothing here can -- a client cannot tell
    a malformed status line from a plain disconnect.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    echoed: list[str] = []

    def serve() -> None:
        try:
            connection = listener.accept()[0]
        except OSError:
            return
        request = bytearray()
        while b"\r\n\r\n" not in request:
            chunk = connection.recv(65535)
            if not chunk:
                break
            request.extend(chunk)
        status_line = compose(request.decode("latin-1"))
        payload = f"{status_line}\r\n\r\n".encode()
        # `sendall(payload)` followed by `echoed.append(status_line)` would
        # record a reply the socket never took. Slicing by what `send` returned
        # records only what went out, and the `int` annotation is what stops
        # `sendall` coming back: it returns None, and payload[:None] is the
        # whole payload.
        written: int = connection.send(payload)
        echoed.append(payload[:written].decode().rstrip("\r\n"))
        connection.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield cast(tuple[str, int], listener.getsockname())[1], echoed
    finally:
        listener.close()
        thread.join(timeout=1)


def echo_the_basic_auth_password(request_text: str) -> str:
    """Compose a status line carrying back the password the request just sent.

    The shortest way for a provider to put a credential where a rendered
    exception would carry it. Pass this to `echoing_provider` for both a
    normal request and a probe request -- the credential leaks the same way
    either time.
    """
    credentials = "no-credentials-seen"
    for line in request_text.split("\r\n"):
        if line.lower().startswith("authorization: basic "):
            credentials = b64decode(line.split(" ", 2)[2]).decode()
    return f"NOT-HTTP {credentials}"
