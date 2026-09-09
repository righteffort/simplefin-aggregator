"""HTTP Basic Auth dependency for endpoints the client app calls."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.concurrency import run_in_threadpool

from .app_tokens import ClaimedAppToken, load_app_tokens, matches
from .state_file import StateFileError


if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from pathlib import Path


_security = HTTPBasic(auto_error=False)


def build_client_auth_dependency(
    store_path: Path,
) -> Callable[[HTTPBasicCredentials | None], Coroutine[None, None, None]]:
    """Make the dependency that admits a claimed client app's credentials and nothing else.

    A factory over the path rather than another untyped attribute on
    `app.state` for the dependency to cast back out of.
    """

    async def require_client_auth(
        credentials: Annotated[HTTPBasicCredentials | None, Depends(_security)],
    ) -> None:
        """Raise 403 unless the request's Basic Auth matches an app token that claimed."""
        if credentials is None:
            raise HTTPException(status_code=403, detail="invalid client app credentials")

        # Read on every request, with no cache and no reload signal: that is
        # what makes `app revoke` take effect, and this is a few-KB file on a
        # loopback server a client app polls a few times a day.
        try:
            # Without the permission warning: this runs per request, and
            # `serve` has already made that check once at startup.
            apps = await run_in_threadpool(partial(load_app_tokens, store_path, warn=False))
        except StateFileError:
            # Fail closed. A store that cannot be read is one that names no
            # app token, and an unreadable store must not admit anyone.
            apps = {}

        if not any(
            matches(credentials.username, record.username_sha256)
            and matches(credentials.password, record.password_sha256)
            for record in apps.values()
            if isinstance(record, ClaimedAppToken)
        ):
            raise HTTPException(status_code=403, detail="invalid client app credentials")

    return require_client_auth
