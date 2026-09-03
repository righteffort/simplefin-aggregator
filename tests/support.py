"""Shared test helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import httpx2

from simplefin_aggregator.app import _AppState  # pyright: ignore[reportPrivateUsage]
from simplefin_aggregator.config import Config


if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from fastapi import FastAPI

    MockHandler = (
        Callable[[httpx2.Request], httpx2.Response]
        | Callable[[httpx2.Request], Coroutine[None, None, httpx2.Response]]
    )


def install_provider_transport(app: FastAPI, provider_name: str, handler: MockHandler) -> None:
    """Swap a provider's real AsyncClient for one backed by a MockTransport.

    Must be called after the app's lifespan has started (e.g. inside a
    `with TestClient(app) as client:` block), since that's what creates
    app.state.provider_clients in the first place.
    """
    state = cast(_AppState, app.state.app_state)
    state.provider_clients[provider_name] = httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        base_url=f"https://{provider_name}.example.com/simplefin",
    )


def make_config(
    *,
    base_url: str = "http://127.0.0.1:8080",
    claim_token: str = "the-claim-token",
    username: str = "app-username",
    password: str = "s3cret-password",
    access_url: str = "https://user:pass@provider.example.com/simplefin",
) -> Config:
    """Build a Config the same way load_config does: from an untyped dict."""
    return Config.model_validate(
        {
            "base_url": base_url,
            "claim_token": claim_token,
            "consumer": {"username": username, "password": password},
            "providers": [{"name": "my-bank", "access_url": access_url}],
        }
    )
