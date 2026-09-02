"""FastAPI application factory."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from .access_url import build_access_url


if TYPE_CHECKING:
    from .config import Config


def create_app(config: Config) -> FastAPI:
    """Build the FastAPI app for a given config. No provider proxying yet."""
    app = FastAPI()
    app.state.config = config

    @app.post("/simplefin/claim/{token}")
    async def claim(token: str) -> PlainTextResponse:  # pyright: ignore [reportUnusedFunction]
        if not secrets.compare_digest(token, config.claim_token.get_secret_value()):
            raise HTTPException(status_code=403, detail="unknown claim token")
        return PlainTextResponse(build_access_url(config))

    return app
