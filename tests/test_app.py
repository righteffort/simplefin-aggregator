from http import HTTPStatus

from fastapi.testclient import TestClient

from simplefin_aggregator.access_url import build_access_url

from .support import make_app
from .support import make_config as _config


def test_claim_with_correct_token_returns_access_url() -> None:
    config = _config()
    client = TestClient(make_app(config))

    response = client.post("/simplefin/claim/the-claim-token")

    assert response.status_code == HTTPStatus.OK
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == build_access_url(config)
    assert not response.text.endswith("\n")


def test_claim_with_wrong_token_is_rejected() -> None:
    client = TestClient(make_app())

    response = client.post("/simplefin/claim/wrong-token")

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_claim_is_repeatable() -> None:
    config = _config()
    client = TestClient(make_app(config))

    first = client.post("/simplefin/claim/the-claim-token")
    second = client.post("/simplefin/claim/the-claim-token")

    assert first.status_code == second.status_code == HTTPStatus.OK
    assert first.text == second.text == build_access_url(config)


def test_claim_accepts_post_with_no_body_and_no_content_type() -> None:
    client = TestClient(make_app())

    request = client.build_request("POST", "/simplefin/claim/the-claim-token")
    assert "content-type" not in request.headers

    response = client.send(request)

    assert response.status_code == HTTPStatus.OK
