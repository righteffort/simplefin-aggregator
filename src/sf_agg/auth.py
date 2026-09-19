# SPDX-License-Identifier: GPL-3.0-only

"""Authentication of the client app's requests to the aggregator."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.concurrency import run_in_threadpool

from .agg_creds import ExchangedAggCreds, load_agg_creds, matches
from .state_file import StateFileError


if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from pathlib import Path


_security = HTTPBasic(auto_error=False)


def build_client_auth_dependency(
    store_path: Path,
) -> Callable[[HTTPBasicCredentials | None], Coroutine[None, None, None]]:
    """Return a FastAPI dependency that rejects requests lacking valid client credentials.

    Valid means the request's Basic Auth matches credentials some client
    received by exchanging its setup token, as recorded in the store at
    `store_path`. Anything else gets a 403.
    """

    async def require_client_auth(
        credentials: Annotated[HTTPBasicCredentials | None, Depends(_security)],
    ) -> None:
        if credentials is None:
            raise HTTPException(status_code=403, detail="invalid client app credentials")

        # Read on every request, with no cache and no reload signal: that is
        # what makes `client revoke` take effect, and this is a few-KB file on a
        # loopback server a client app polls a few times a day.
        try:
            # Without the permission warning: this runs per request, and
            # `serve` has already made that check once at startup.
            creds = await run_in_threadpool(partial(load_agg_creds, store_path, warn=False))
        except StateFileError:
            # Fail closed. A store that cannot be read is one that names no
            # client, and an unreadable store must not admit anyone.
            creds = {}

        if not any(
            matches(credentials.username, record.username_sha256)
            and matches(credentials.password, record.password_sha256)
            for record in creds.values()
            if isinstance(record, ExchangedAggCreds)
        ):
            raise HTTPException(status_code=403, detail="invalid client app credentials")

    return require_client_auth
