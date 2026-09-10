from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from .support import make_app


if TYPE_CHECKING:
    from pathlib import Path


def test_info_reports_the_protocol_version_this_server_speaks(tmp_path: Path) -> None:
    """Requirement: the version named is this server's own, not any provider's."""
    app = make_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/simplefin/info")

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"versions": ["1.0"]}


def test_info_does_not_require_basic_auth(tmp_path: Path) -> None:
    """Requirement: /info is the one route a client app reaches before it has credentials."""
    app = make_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/simplefin/info")

    assert response.status_code == HTTPStatus.OK
