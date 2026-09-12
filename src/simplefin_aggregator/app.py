"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, cast

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.concurrency import run_in_threadpool

from .access_url import build_access_url
from .app_tokens import (
    ClientCredentials,
    UnclaimedAppToken,
    claim_app_token,
    matches,
    update_app_tokens,
)
from .auth import build_client_auth_dependency
from .merge import merge
from .provider_clients import build_provider_client
from .provider_registry import find_provider
from .provider_resolution import resolve_provider_for_account
from .request_counter import RequestCounter
from .state_file import StateFileError
from .transport import fetch_all
from .url_validation import validate_access_url


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping, Sequence
    from pathlib import Path

    import httpx2
    from pydantic import SecretStr

    from .config import Config, Provider
    from .url_validation import NormalizedUrl

logger = logging.getLogger(__name__)

# v1's five. `version` is absent deliberately: a provider that honoured a
# forwarded one could answer in a protocol this application does not parse.
ACCOUNTS_FORWARDED_PARAMS = frozenset(
    {"start-date", "end-date", "pending", "account", "balances-only"}
)

# The protocol version this application supports for clients. A `version` a
# client app sends is ignored.
PROTOCOL_VERSION = "1.0"

# The path segment before {token}. Also used to redact the claim token from
# uvicorn's access log (see access_log.py) -- keeping both derived from this
# one constant means the route and the redaction can't silently drift apart.
CLAIM_PATH_PREFIX = "/simplefin/claim/"


class _UnknownSetupTokenError(Exception):
    """No unclaimed record holds this token.

    Raised the same way whether the token never existed or has already been
    claimed, because the store cannot tell them apart: a claim replaces the
    record it spent, so there is nothing left that answers to a spent token.
    """


def _spend_setup_token(store_path: Path, token: str) -> ClientCredentials:
    """Exchange a setup token for the credentials it buys, once.

    The whole exchange is one locked update, so two clients presenting the
    same token cannot both be issued credentials for it.
    """
    with update_app_tokens(store_path) as apps:
        holder = next(
            (
                (key, record)
                for key, record in apps.items()
                if isinstance(record, UnclaimedAppToken)
                and matches(token, record.claim_token_sha256)
            ),
            None,
        )
        if holder is None:
            # Raised out of the update rather than returned from it, so that a
            # token matching nothing does not rewrite the store on its way to a
            # 403.
            raise _UnknownSetupTokenError
        key, unclaimed = holder
        credentials, apps[key] = claim_app_token(unclaimed)
    return credentials


@dataclass
class _AppState:
    """The one dynamically-typed attribute we hang off app.state."""

    provider_clients: dict[str, httpx2.AsyncClient]
    request_counter: RequestCounter


def _get_app_state(request: Request) -> _AppState:
    return cast(_AppState, request.app.state.app_state)  # pyright: ignore[reportAny]


def _resolve_access_url(
    config: Config, access_urls: Mapping[str, SecretStr], key: str
) -> NormalizedUrl:
    """Validate one stored access URL against that provider's current root.

    A single-entry comparison, not a scan: the URL was claimed from one
    specific provider, so it is that provider's root it has to still match.
    """
    entry = find_provider(config.provider_entries(), key)
    stored = access_urls.get(key)
    if stored is None:
        msg = f"no access URL stored for provider {key!r}; claim one first"
        raise StateFileError(msg)
    return validate_access_url(entry.root, stored.get_secret_value(), provider=key)


def _forwarded_accounts_params(request: Request) -> list[tuple[str, str]]:
    """Narrow the client app's query parameters to the ones v1 defines."""
    return [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key in ACCOUNTS_FORWARDED_PARAMS
    ]


def _route_requests(
    config: Config, params: Sequence[tuple[str, str]]
) -> list[tuple[Provider, Sequence[tuple[str, str]]]]:
    """Split one client request into the per-provider requests that answer it."""
    shared = [(key, value) for key, value in params if key != "account"]
    account_ids = [value for key, value in params if key == "account"]
    if not account_ids:
        return [(provider, shared) for provider in config.providers]

    owned: defaultdict[str, list[str]] = defaultdict(list)
    for account_id in account_ids:
        resolved = resolve_provider_for_account(account_id, config.providers)
        if resolved is None:
            # %r so that a newline in an id cannot forge a log line of its own.
            logger.warning("account id %r matches no configured provider prefix", account_id)
            continue
        provider, provider_account_id = resolved
        owned[provider.key].append(provider_account_id)
    # Iterating in configured provider order so that the order of accounts in
    # the response is consistent, regardless of the order in the request.
    return [
        (provider, [*shared, *(("account", account_id) for account_id in owned[provider.key])])
        for provider in config.providers
        if provider.key in owned
    ]


def create_app(
    config: Config, access_urls: Mapping[str, SecretStr], app_tokens_path: Path
) -> FastAPI:
    """Build the FastAPI app for a config, the access URLs claimed so far, and the app token store.

    Raises rather than starting a server that cannot work: an unclaimed
    provider or a stored access URL that no longer matches its provider's root
    is reported here, before uvicorn starts, not on the first request.

    The app token store is named by path rather than read here, because unlike
    the config it is live state: every request that authenticates reads it, so
    that `app revoke` takes effect without a restart.
    """
    provider_access_urls = {
        provider.key: _resolve_access_url(config, access_urls, provider.key)
        for provider in config.providers
    }

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        provider_clients = {
            key: build_provider_client(access_url)
            for key, access_url in provider_access_urls.items()
        }
        app.state.app_state = _AppState(
            provider_clients=provider_clients, request_counter=RequestCounter()
        )
        try:
            yield
        finally:
            for provider_client in provider_clients.values():
                await provider_client.aclose()

    app = FastAPI(lifespan=lifespan)
    require_client_auth = build_client_auth_dependency(app_tokens_path)

    @app.post(f"{CLAIM_PATH_PREFIX}{{token}}")
    async def claim(token: str) -> PlainTextResponse:  # pyright: ignore [reportUnusedFunction]
        try:
            # Persisted before it is answered: crashing after the write costs a
            # setup token the operator replaces with `app regen`, while
            # crashing after the response leaves the client app holding
            # credentials this server does not recognise -- a setup that looks
            # complete and silently never syncs.
            credentials = await run_in_threadpool(_spend_setup_token, app_tokens_path, token)
        except _UnknownSetupTokenError:
            raise HTTPException(status_code=403, detail="unknown claim token") from None
        except StateFileError as exc:
            raise HTTPException(status_code=500, detail="could not record the claim") from exc
        return PlainTextResponse(build_access_url(config.base_url, credentials))

    @app.get("/simplefin/accounts", dependencies=[Depends(require_client_auth)])
    async def accounts(request: Request) -> Response:  # pyright: ignore [reportUnusedFunction]
        state = _get_app_state(request)
        requests = _route_requests(config, _forwarded_accounts_params(request))

        responses = await fetch_all(
            state.provider_clients, requests, "/accounts", state.request_counter
        )
        merged = merge(
            [
                (provider.prefix, response)
                for (provider, _), response in zip(requests, responses, strict=True)
            ]
        )
        # Always respond 200, even if a provider did not. A provider's failure is reported via the
        # body's `errors`, which is what v1 provides that array for.
        return Response(
            content=merged.body, status_code=HTTPStatus.OK, media_type="application/json"
        )

    @app.get("/simplefin/info")
    async def info() -> JSONResponse:  # pyright: ignore [reportUnusedFunction]
        """Answer locally: this names the protocol this server speaks, not its providers."""
        return JSONResponse({"versions": [PROTOCOL_VERSION]})

    return app
