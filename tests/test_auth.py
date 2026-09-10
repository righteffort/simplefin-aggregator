"""Tests for the Basic Auth dependency the client app's requests go through."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from simplefin_aggregator.app_tokens import app_tokens_path, update_app_tokens
from simplefin_aggregator.auth import build_client_auth_dependency

from .support import make_claimed_app, make_unclaimed_app


if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _protected_client(config_dir: Path) -> TestClient:
    app = FastAPI()

    @app.get(
        "/protected",
        dependencies=[Depends(build_client_auth_dependency(app_tokens_path(config_dir)))],
    )
    async def protected() -> dict[str, bool]:  # pyright: ignore [reportUnusedFunction]
        return {"ok": True}

    return TestClient(app)


def test_an_app_that_claimed_is_admitted(tmp_path: Path) -> None:
    credentials = make_claimed_app(tmp_path)

    response = _protected_client(tmp_path).get("/protected", auth=credentials)

    assert response.status_code == HTTPStatus.OK


def test_the_wrong_password_is_refused(tmp_path: Path) -> None:
    username, _ = make_claimed_app(tmp_path)

    response = _protected_client(tmp_path).get("/protected", auth=(username, "wrong-password"))

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_a_username_no_app_answers_to_is_refused(tmp_path: Path) -> None:
    """Well-formed and unknown, which is the shape a guess takes."""
    _, password = make_claimed_app(tmp_path)

    response = _protected_client(tmp_path).get("/protected", auth=("someone-else", password))

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_credentials_from_one_app_do_not_admit_another_apps(tmp_path: Path) -> None:
    username, _ = make_claimed_app(tmp_path, key="first", label="First")
    _, other_password = make_claimed_app(tmp_path, key="second", label="Second")

    response = _protected_client(tmp_path).get("/protected", auth=(username, other_password))

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_no_credentials_at_all_are_refused(tmp_path: Path) -> None:
    _ = make_claimed_app(tmp_path)

    response = _protected_client(tmp_path).get("/protected")

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_an_empty_store_admits_nobody(tmp_path: Path) -> None:
    response = _protected_client(tmp_path).get("/protected", auth=("someone", "something"))

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_an_app_that_has_not_claimed_yet_admits_nobody(tmp_path: Path) -> None:
    """An unclaimed record holds a setup token, not credentials to authenticate with."""
    secret = make_unclaimed_app(tmp_path)

    response = _protected_client(tmp_path).get("/protected", auth=(secret, secret))

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_revoking_an_app_takes_effect_without_a_restart(tmp_path: Path) -> None:
    """A revocation that needed a restart would not be a revocation.

    This is what the per-request read buys, and the only reason it is not
    cached.
    """
    credentials = make_claimed_app(tmp_path)
    client = _protected_client(tmp_path)
    assert client.get("/protected", auth=credentials).status_code == HTTPStatus.OK

    with update_app_tokens(app_tokens_path(tmp_path)) as apps:
        del apps["test-app"]

    assert client.get("/protected", auth=credentials).status_code == HTTPStatus.FORBIDDEN


def test_revoking_one_app_leaves_another_working(tmp_path: Path) -> None:
    first = make_claimed_app(tmp_path, key="first", label="First")
    second = make_claimed_app(tmp_path, key="second", label="Second")
    client = _protected_client(tmp_path)

    with update_app_tokens(app_tokens_path(tmp_path)) as apps:
        del apps["first"]

    assert client.get("/protected", auth=first).status_code == HTTPStatus.FORBIDDEN
    assert client.get("/protected", auth=second).status_code == HTTPStatus.OK


def test_a_store_that_cannot_be_read_admits_nobody(tmp_path: Path) -> None:
    """Fail closed: a store nobody can parse is a store that names no app."""
    credentials = make_claimed_app(tmp_path)
    _ = app_tokens_path(tmp_path).write_text("{not json")

    response = _protected_client(tmp_path).get("/protected", auth=credentials)

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_the_per_request_read_does_not_warn_about_the_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A warning worth making once is noise made on every request.

    `serve` checks the mode at startup, which is where the operator can act on
    it; repeating it per request writes to stderr in a loop, unfilterable,
    interleaved with the access log.
    """
    credentials = make_claimed_app(tmp_path)
    app_tokens_path(tmp_path).chmod(0o644)
    client = _protected_client(tmp_path)
    _ = capsys.readouterr()

    for _ in range(3):
        assert client.get("/protected", auth=credentials).status_code == HTTPStatus.OK

    assert capsys.readouterr().err == ""
