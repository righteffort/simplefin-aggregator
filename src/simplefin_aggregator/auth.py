"""HTTP Basic Auth dependency for endpoints the consumer calls."""

from __future__ import annotations

import secrets
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import Config


_security = HTTPBasic(auto_error=False)


def require_consumer_auth(
    request: Request, credentials: Annotated[HTTPBasicCredentials | None, Depends(_security)]
) -> None:
    """Raise 403 unless the request's Basic Auth matches the configured consumer."""
    config = cast(Config, request.app.state.config)  # pyright: ignore [reportAny]
    valid = credentials is not None and (
        secrets.compare_digest(credentials.username, config.consumer.username)
        and secrets.compare_digest(
            credentials.password, config.consumer.password.get_secret_value()
        )
    )
    if not valid:
        raise HTTPException(status_code=403, detail="invalid consumer credentials")
