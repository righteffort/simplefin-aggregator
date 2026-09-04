"""FastAPI application factory."""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from .access_url import build_access_url
from .auth import require_client_auth
from .id_rewriting import rewrite_ids, unrewrite_ids
from .merge import merge
from .provider_access_urls import AccessUrlStoreError
from .provider_allowlist import find_provider
from .provider_clients import build_provider_client
from .provider_resolution import resolve_provider_for_account
from .request_counter import RequestCounter
from .transport import fetch_all
from .url_validation import validate_access_url


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping

    import httpx2
    from pydantic import SecretStr

    from .config import Config, Provider
    from .url_validation import NormalizedUrl

ACCOUNTS_FORWARDED_PARAMS = frozenset(
    {"start-date", "end-date", "pending", "account", "balances-only", "version"}
)

# The path segment before {token}. Also used to redact the claim token from
# uvicorn's access log (see access_log.py) -- keeping both derived from this
# one constant means the route and the redaction can't silently drift apart.
CLAIM_PATH_PREFIX = "/simplefin/claim/"


@dataclass
class _AppState:
    """The one dynamically-typed attribute we hang off app.state."""

    provider_clients: dict[str, httpx2.AsyncClient]
    request_counter: RequestCounter


def _get_app_state(request: Request) -> _AppState:
    return cast(_AppState, request.app.state.app_state)  # pyright: ignore[reportAny]


def _resolve_access_url(
    config: Config, access_urls: Mapping[str, SecretStr], provider_key: str
) -> NormalizedUrl:
    """Validate one stored access URL against that provider's current allowlist root.

    A single-entry comparison, not a scan: the URL was claimed from one
    specific provider, so it is that provider's root it has to still match.
    """
    entry = find_provider(config.provider_entries(), provider_key)
    stored = access_urls.get(provider_key)
    if stored is None:
        msg = f"no access URL stored for provider {provider_key!r}; claim one first"
        raise AccessUrlStoreError(msg)
    return validate_access_url(entry.root, stored.get_secret_value(), provider=provider_key)


def _forwarded_accounts_params(request: Request) -> list[tuple[str, str]]:
    allowed = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key in ACCOUNTS_FORWARDED_PARAMS
    ]
    raw_account_ids = [value for key, value in allowed if key == "account"]
    provider_account_ids = iter(unrewrite_ids(raw_account_ids))
    return [
        (key, next(provider_account_ids)) if key == "account" else (key, value)
        for key, value in allowed
    ]


def create_app(config: Config, access_urls: Mapping[str, SecretStr]) -> FastAPI:
    """Build the FastAPI app for a given config and the access URLs claimed so far.

    Raises rather than starting a server that cannot work: an unclaimed
    provider or a stored access URL that no longer matches its provider's root
    is reported here, before uvicorn starts, not on the first request.
    """
    provider_access_urls = {
        provider.provider_key: _resolve_access_url(config, access_urls, provider.provider_key)
        for provider in config.providers
    }

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        provider_clients = {
            provider_key: build_provider_client(access_url)
            for provider_key, access_url in provider_access_urls.items()
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
    app.state.config = config

    @app.post(f"{CLAIM_PATH_PREFIX}{{token}}")
    async def claim(token: str) -> PlainTextResponse:  # pyright: ignore [reportUnusedFunction]
        if not secrets.compare_digest(token, config.claim_token.get_secret_value()):
            raise HTTPException(status_code=403, detail="unknown claim token")
        return PlainTextResponse(build_access_url(config))

    @app.get("/simplefin/accounts", dependencies=[Depends(require_client_auth)])
    async def accounts(request: Request) -> Response:  # pyright: ignore [reportUnusedFunction]
        state = _get_app_state(request)

        params = _forwarded_accounts_params(request)
        account_ids = [value for key, value in params if key == "account"]

        providers_to_query: list[Provider]
        if account_ids:
            providers_to_query = []
            seen_provider_keys: set[str] = set()
            for account_id in account_ids:
                provider = resolve_provider_for_account(account_id, config.providers)
                if provider.provider_key not in seen_provider_keys:
                    seen_provider_keys.add(provider.provider_key)
                    providers_to_query.append(provider)
        else:
            providers_to_query = list(config.providers)

        responses = await fetch_all(
            state.provider_clients, providers_to_query, "/accounts", params, state.request_counter
        )
        merged = merge(responses)
        return Response(
            content=rewrite_ids(merged.body),
            status_code=merged.status,
            media_type=merged.content_type,
        )

    @app.get("/simplefin/info")
    async def info(request: Request) -> Response:  # pyright: ignore [reportUnusedFunction]
        state = _get_app_state(request)

        responses = await fetch_all(
            state.provider_clients, config.providers, "/info", [], state.request_counter
        )
        merged = merge(responses)
        return Response(
            content=merged.body, status_code=merged.status, media_type=merged.content_type
        )

    return app
