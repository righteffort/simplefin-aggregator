# SPDX-License-Identifier: GPL-3.0-only

"""Tests for `POST /simplefin/claim/{claim_secret}`, which exchanges a setup token once."""

from __future__ import annotations

import errno
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

import httpx2
from fastapi.testclient import TestClient

from sf_agg.agg_creds import UnexchangedAggCreds, agg_creds_path, load_agg_creds

from .support import PROVIDER_KEY, add_client, install_provider_transport, make_app


if TYPE_CHECKING:
    import pytest

UNKNOWN_CLAIM_SECRET = "a-claim-secret-that-was-never-issued"  # noqa: S105


def _credentials_from(access_url: str) -> tuple[str, str]:
    parsed = urlsplit(access_url)
    assert parsed.username is not None
    assert parsed.password is not None
    return unquote(parsed.username), unquote(parsed.password)


def test_a_valid_setup_token_returns_an_access_url(tmp_path: Path) -> None:
    secret = add_client(tmp_path)
    client = TestClient(make_app(tmp_path))

    response = client.post(f"/simplefin/claim/{secret}")

    assert response.status_code == HTTPStatus.OK
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text.startswith("http://")
    assert not response.text.endswith("\n")


def test_the_access_url_it_returns_authenticates_the_accounts_endpoint(tmp_path: Path) -> None:
    """The one end-to-end property the whole exchange exists for."""
    secret = add_client(tmp_path)
    app = make_app(tmp_path)

    with TestClient(app) as client:
        access_url = client.post(f"/simplefin/claim/{secret}").text
        install_provider_transport(
            app, PROVIDER_KEY, lambda _request: httpx2.Response(200, json={"accounts": []})
        )

        response = client.get("/simplefin/accounts", auth=_credentials_from(access_url))

    assert response.status_code == HTTPStatus.OK


def test_an_exchange_replaces_the_record_it_matched(tmp_path: Path) -> None:
    secret = add_client(tmp_path)
    client = TestClient(make_app(tmp_path))

    _ = client.post(f"/simplefin/claim/{secret}")

    record = load_agg_creds(agg_creds_path(tmp_path))["test-client"]
    assert not isinstance(record, UnexchangedAggCreds)


def test_a_second_exchange_of_the_same_setup_token_is_refused(tmp_path: Path) -> None:
    secret = add_client(tmp_path)
    client = TestClient(make_app(tmp_path))

    first = client.post(f"/simplefin/claim/{secret}")
    second = client.post(f"/simplefin/claim/{secret}")

    assert first.status_code == HTTPStatus.OK
    assert second.status_code == HTTPStatus.FORBIDDEN


def test_an_exchanged_token_and_a_token_that_never_existed_are_answered_alike(
    tmp_path: Path,
) -> None:
    """The protocol conflates them deliberately, and so does the store.

    An exchange replaces the record it matched, so a replayed setup token fails
    the lookup an unknown one fails, by the same code path -- there is no
    branch that could tell them apart and no answer that could differ.
    """
    secret = add_client(tmp_path)
    client = TestClient(make_app(tmp_path))
    _ = client.post(f"/simplefin/claim/{secret}")

    exchanged = client.post(f"/simplefin/claim/{secret}")
    never_issued = client.post(f"/simplefin/claim/{UNKNOWN_CLAIM_SECRET}")

    assert exchanged.status_code == never_issued.status_code == HTTPStatus.FORBIDDEN
    assert exchanged.text == never_issued.text


def test_an_unknown_token_is_refused(tmp_path: Path) -> None:
    _ = add_client(tmp_path)
    client = TestClient(make_app(tmp_path))

    response = client.post(f"/simplefin/claim/{UNKNOWN_CLAIM_SECRET}")

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_an_empty_store_answers_every_token_alike(tmp_path: Path) -> None:
    client = TestClient(make_app(tmp_path))

    response = client.post(f"/simplefin/claim/{UNKNOWN_CLAIM_SECRET}")

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_a_claim_that_cannot_be_recorded_issues_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Persist, then respond. A client app holding credentials this server has
    no record of looks like a working setup that silently never syncs.
    """
    secret = add_client(tmp_path)
    client = TestClient(make_app(tmp_path))

    def no_space(_self: Path, _target: str | Path) -> Path:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Path, "replace", no_space)

    response = client.post(f"/simplefin/claim/{secret}")

    assert response.status_code != HTTPStatus.OK
    assert "://" not in response.text
    monkeypatch.undo()
    assert isinstance(load_agg_creds(agg_creds_path(tmp_path))["test-client"], UnexchangedAggCreds)


def test_a_claim_needs_no_body_and_no_content_type(tmp_path: Path) -> None:
    secret = add_client(tmp_path)
    client = TestClient(make_app(tmp_path))

    request = client.build_request("POST", f"/simplefin/claim/{secret}")
    assert "content-type" not in request.headers

    response = client.send(request)

    assert response.status_code == HTTPStatus.OK
