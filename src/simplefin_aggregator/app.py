"""FastAPI application factory."""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from .access_url import build_access_url
from .auth import require_consumer_auth
from .id_rewriting import rewrite_ids, unrewrite_ids
from .merge import merge
from .provider_clients import build_provider_client
from .provider_resolution import resolve_provider_for_account
from .request_counter import RequestCounter
from .transport import fetch_all


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    import httpx2

    from .config import Config, Provider

ACCOUNTS_FORWARDED_PARAMS = frozenset(
    {"start-date", "end-date", "pending", "account", "balances-only", "version"}
)


@dataclass
class _AppState:
    """The one dynamically-typed attribute we hang off app.state."""

    provider_clients: dict[str, httpx2.AsyncClient]
    request_counter: RequestCounter


def _get_app_state(request: Request) -> _AppState:
    return cast(_AppState, request.app.state.app_state)  # pyright: ignore[reportAny]


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


def create_app(config: Config) -> FastAPI:
    """Build the FastAPI app for a given config."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        clients = {provider.name: build_provider_client(provider) for provider in config.providers}
        app.state.app_state = _AppState(provider_clients=clients, request_counter=RequestCounter())
        try:
            yield
        finally:
            for client in clients.values():
                await client.aclose()

    app = FastAPI(lifespan=lifespan)
    app.state.config = config

    @app.post("/simplefin/claim/{token}")
    async def claim(token: str) -> PlainTextResponse:  # pyright: ignore [reportUnusedFunction]
        if not secrets.compare_digest(token, config.claim_token.get_secret_value()):
            raise HTTPException(status_code=403, detail="unknown claim token")
        return PlainTextResponse(build_access_url(config))

    @app.get("/simplefin/accounts", dependencies=[Depends(require_consumer_auth)])
    async def accounts(request: Request) -> Response:  # pyright: ignore [reportUnusedFunction]
        state = _get_app_state(request)

        params = _forwarded_accounts_params(request)
        account_ids = [value for key, value in params if key == "account"]

        providers_to_query: list[Provider]
        if account_ids:
            providers_to_query = []
            seen_provider_names: set[str] = set()
            for account_id in account_ids:
                provider = resolve_provider_for_account(account_id, config.providers)
                if provider.name not in seen_provider_names:
                    seen_provider_names.add(provider.name)
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
