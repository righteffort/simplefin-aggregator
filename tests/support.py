"""Shared test helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import httpx2
from pydantic import SecretStr

from simplefin_aggregator.app import _AppState, create_app  # pyright: ignore[reportPrivateUsage]
from simplefin_aggregator.config import Config


if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Mapping

    from fastapi import FastAPI

    MockHandler = (
        Callable[[httpx2.Request], httpx2.Response]
        | Callable[[httpx2.Request], Coroutine[None, None, httpx2.Response]]
    )

# The provider every test shares: a self-hosted allowlist entry, its slug used
# as the configured provider_key, and an access URL under its root.
PROVIDER_KEY = "my-bank"
PROVIDER_ROOT = "https://provider.example.com/simplefin"
PROVIDER_ACCESS_URL = "https://user:pass@provider.example.com/simplefin"


def install_provider_transport(app: FastAPI, provider_key: str, handler: MockHandler) -> None:
    """Swap a provider's real AsyncClient for one backed by a MockTransport.

    Must be called after the app's lifespan has started (e.g. inside a
    `with TestClient(app) as client:` block), since that's what creates
    app.state.provider_clients in the first place.
    """
    state = cast(_AppState, app.state.app_state)
    state.provider_clients[provider_key] = httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        base_url=f"https://{provider_key}.example.com/simplefin",
    )


def make_config(  # noqa: PLR0913
    *,
    base_url: str = "http://127.0.0.1:8080",
    claim_token: str = "the-claim-token",
    username: str = "client-username",
    password: str = "s3cret-password",
    provider_key: str = PROVIDER_KEY,
    root: str = PROVIDER_ROOT,
) -> Config:
    """Build a Config the same way load_config does: from an untyped dict."""
    return Config.model_validate(
        {
            "base_url": base_url,
            "claim_token": claim_token,
            "client": {"username": username, "password": password},
            "providers": [{"provider_key": provider_key}],
            "allowlist": [{"slug": provider_key, "label": "Test Provider", "root": root}],
        }
    )


def make_access_urls(
    access_url: str = PROVIDER_ACCESS_URL, *, provider_key: str = PROVIDER_KEY
) -> dict[str, SecretStr]:
    """The access URL store's contents for a config with one claimed provider."""
    return {provider_key: SecretStr(access_url)}


def make_app(
    config: Config | None = None, access_urls: Mapping[str, SecretStr] | None = None
) -> FastAPI:
    """create_app with the shared test config and its one claimed provider."""
    return create_app(
        config if config is not None else make_config(),
        access_urls if access_urls is not None else make_access_urls(),
    )
