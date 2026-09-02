from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from simplefin_aggregator.auth import require_consumer_auth

from .support import make_config as _config


if TYPE_CHECKING:
    from simplefin_aggregator.config import Config


def _protected_client(config: Config) -> TestClient:
    app = FastAPI()
    app.state.config = config

    @app.get("/protected", dependencies=[Depends(require_consumer_auth)])
    async def protected() -> dict[str, bool]:  # pyright: ignore [reportUnusedFunction]
        return {"ok": True}

    return TestClient(app)


def test_require_consumer_auth_accepts_correct_credentials() -> None:
    client = _protected_client(_config())

    response = client.get("/protected", auth=("app-username", "s3cret-password"))

    assert response.status_code == 200


def test_require_consumer_auth_rejects_wrong_password() -> None:
    client = _protected_client(_config())

    response = client.get("/protected", auth=("app-username", "wrong-password"))

    assert response.status_code == 403


def test_require_consumer_auth_rejects_wrong_username() -> None:
    client = _protected_client(_config())

    response = client.get("/protected", auth=("someone-else", "s3cret-password"))

    assert response.status_code == 403


def test_require_consumer_auth_rejects_missing_credentials() -> None:
    client = _protected_client(_config())

    response = client.get("/protected")

    assert response.status_code == 403
