# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, cast, override

import httpx2
from fastapi.testclient import TestClient

from .support import (
    ProviderSpec,
    add_client_and_exchange,
    echo_the_basic_auth_password,
    echoing_provider,
    install_provider_transport,
    make_access_urls,
    make_app,
    make_config,
)


if TYPE_CHECKING:
    from collections.abc import Generator, Mapping, Sequence
    from pathlib import Path

    import pytest

    from .support import MockHandler

    Account = dict[str, object]
    Params = list[tuple[str, str]]

BANK_A = ProviderSpec(
    key="bank-a",
    origin="https://bank-a.example.com",
    access_url="https://user:pass@bank-a.example.com/simplefin",
)
BANK_B = ProviderSpec(
    key="bank-b",
    origin="https://bank-b.example.com",
    access_url="https://user:pass@bank-b.example.com/simplefin",
)


def _recording(calls: list[Params], accounts: Sequence[Account]) -> MockHandler:
    """A provider that answers with `accounts` and records the parameters it was asked with."""

    async def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(list(request.url.params.multi_items()))
        return httpx2.Response(HTTPStatus.OK, json={"accounts": list(accounts)})

    return handler


@contextmanager
def _aggregating(
    tmp_path: Path,
    specs: Sequence[ProviderSpec] = (BANK_A, BANK_B),
    accounts: Mapping[str, Sequence[Account]] | None = None,
    *,
    allow_multiple_blank_prefixes: bool = False,
) -> Generator[tuple[TestClient, tuple[str, str], dict[str, list[Params]]]]:
    """A client app, its credentials, and what each provider behind the aggregator was asked.

    Each provider's entry lists one set of query parameters per request it
    received, so a test can assert both what a provider was asked for and that
    it was asked once.
    """
    reported: Mapping[str, Sequence[Account]] = accounts if accounts is not None else {}
    auth = add_client_and_exchange(tmp_path)
    config = make_config(*specs, allow_multiple_blank_prefixes=allow_multiple_blank_prefixes)
    app = make_app(tmp_path, config, make_access_urls(*specs))
    calls: dict[str, list[Params]] = {spec.key: [] for spec in specs}
    with TestClient(app) as client:
        for spec in specs:
            install_provider_transport(
                app, spec.key, _recording(calls[spec.key], reported.get(spec.key, ()))
            )
        yield client, auth, calls


def test_accounts_without_basic_auth_is_rejected(tmp_path: Path) -> None:
    _ = add_client_and_exchange(tmp_path)
    app = make_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/simplefin/accounts")

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_accounts_relays_every_key_a_provider_sent_inside_an_account(tmp_path: Path) -> None:
    """Requirement: this aggregator rebuilds the response, and account contents survive that."""
    account = {"id": "acc-1", "name": "Checking", "balance": "12.34", "unknown-key": [1, 2]}

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(HTTPStatus.OK, json={"accounts": [account]})

    auth = add_client_and_exchange(tmp_path)
    app = make_app(tmp_path)

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts", auth=auth)

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"accounts": [{**account, "id": "my-bank:acc-1"}], "errors": []}, (
        "only the id changes, and it carries this provider's prefix"
    )


def test_accounts_forwards_multiple_account_params(tmp_path: Path) -> None:
    """Requirement: a filter naming multiple accounts reaches the provider naming all of them."""
    received_params: list[tuple[str, str]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        received_params.extend(request.url.params.multi_items())
        return httpx2.Response(HTTPStatus.OK, json={"accounts": []})

    auth = add_client_and_exchange(tmp_path)
    app = make_app(tmp_path)

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get(
            "/simplefin/accounts?account=my-bank:acc-1&account=my-bank:acc-2", auth=auth
        )

    assert response.status_code == HTTPStatus.OK
    assert [v for k, v in received_params if k == "account"] == ["acc-1", "acc-2"], (
        "the provider is asked about the ids it issued, not the ones the client app holds"
    )


def test_accounts_only_forwards_allowed_query_params(tmp_path: Path) -> None:
    received_params: list[tuple[str, str]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        received_params.extend(request.url.params.multi_items())
        return httpx2.Response(HTTPStatus.OK, json={"accounts": []})

    auth = add_client_and_exchange(tmp_path)
    app = make_app(tmp_path)

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get(
            "/simplefin/accounts?balances-only=1&unexpected-param=nope", auth=auth
        )

    assert response.status_code == HTTPStatus.OK
    forwarded_keys = {k for k, _ in received_params}
    assert forwarded_keys == {"balances-only"}


def test_accounts_does_not_relay_a_providers_403_as_its_own(tmp_path: Path) -> None:
    """Requirement: 403 here is about the client app's credentials, not a provider's."""

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(HTTPStatus.FORBIDDEN, content=b"forbidden by provider")

    auth = add_client_and_exchange(tmp_path)
    app = make_app(tmp_path)

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts", auth=auth)

    assert response.status_code == HTTPStatus.OK
    body = response.json()  # pyright: ignore[reportAny]
    assert body["accounts"] == []
    assert b"forbidden by provider" not in response.content


def test_accounts_provider_redirect_is_not_relayed_to_the_client_app(tmp_path: Path) -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            HTTPStatus.FOUND, headers={"location": "https://attacker.example.net/accounts"}
        )

    auth = add_client_and_exchange(tmp_path)
    app = make_app(tmp_path)

    with TestClient(app) as client:
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts", auth=auth, follow_redirects=False)

    assert response.status_code == HTTPStatus.OK
    assert "location" not in response.headers
    assert b"attacker.example.net" not in response.content


def test_accounts_answers_a_v1_body_when_a_provider_is_unreachable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Requirement: a dead provider still gets the client app a well-formed v1 response."""

    async def handler(request: httpx2.Request) -> httpx2.Response:
        msg = "connection refused"
        raise httpx2.ConnectError(msg, request=request)

    auth = add_client_and_exchange(tmp_path)
    app = make_app(tmp_path)

    with TestClient(app) as client, caplog.at_level(logging.WARNING):
        install_provider_transport(app, "my-bank", handler)
        response = client.get("/simplefin/accounts", auth=auth)

    assert response.status_code == HTTPStatus.OK
    body = cast("dict[str, object]", response.json())
    assert set(body) == {"accounts", "errors"}, "errlist is a v2 field this application never emits"
    assert body["accounts"] == []
    assert "my-bank" in caplog.text
    assert "ConnectError" in caplog.text, "the operator is told what kind of failure it was"
    assert "connection refused" not in response.text, "not the client app's business"


def test_two_providers_accounts_arrive_in_configured_order_behind_their_prefixes(
    tmp_path: Path,
) -> None:
    """Requirement: the union of both providers' accounts is what the client app sees."""
    with _aggregating(
        tmp_path,
        accounts={
            "bank-a": [{"id": "acc-1", "name": "Checking"}, {"id": "acc-2"}],
            "bank-b": [{"id": "acc-1", "name": "Savings"}],
        },
    ) as (client, auth, _calls):
        response = client.get("/simplefin/accounts", auth=auth)

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {
        "accounts": [
            {"id": "bank-a:acc-1", "name": "Checking"},
            {"id": "bank-a:acc-2"},
            {"id": "bank-b:acc-1", "name": "Savings"},
        ],
        "errors": [],
    }


def test_without_an_account_filter_every_provider_is_asked_for_everything(tmp_path: Path) -> None:
    """Requirement: the endpoint with no filter means every account this server knows about."""
    with _aggregating(tmp_path) as (client, auth, calls):
        response = client.get("/simplefin/accounts?start-date=1700000000", auth=auth)

    assert response.status_code == HTTPStatus.OK
    assert calls == {
        "bank-a": [[("start-date", "1700000000")]],
        "bank-b": [[("start-date", "1700000000")]],
    }, "each provider asked once, with the shared parameters and no account filter"


def test_account_ids_spanning_two_providers_become_one_request_each(tmp_path: Path) -> None:
    """Requirement: an account id never reaches a provider that did not issue it."""
    with _aggregating(tmp_path) as (client, auth, calls):
        asked = "account=bank-b:b-1&account=bank-a:a-1&account=bank-b:b-2&pending=1"
        response = client.get(f"/simplefin/accounts?{asked}", auth=auth)

    assert response.status_code == HTTPStatus.OK
    assert calls == {
        "bank-a": [[("pending", "1"), ("account", "a-1")]],
        "bank-b": [[("pending", "1"), ("account", "b-1"), ("account", "b-2")]],
    }


def test_an_id_asked_for_twice_is_asked_of_its_provider_once(tmp_path: Path) -> None:
    """Requirement: a provider hears each of its ids once, however often the client named it."""
    named = ("bank-a:a-1", "bank-a:a-1")
    query = "&".join(f"account={account_id}" for account_id in named)
    assert len(named) > len(set(named)), "the premise: the client app names one id more than once"

    with _aggregating(tmp_path, accounts={"bank-a": [{"id": "a-1"}]}) as (client, auth, calls):
        response = client.get(f"/simplefin/accounts?{query}", auth=auth)

    assert response.status_code == HTTPStatus.OK
    assert calls == {"bank-a": [[("account", "a-1")]], "bank-b": []}
    assert response.json() == {"accounts": [{"id": "bank-a:a-1"}], "errors": []}


def test_a_filtered_request_merges_in_configured_order_not_the_order_of_the_ids(
    tmp_path: Path,
) -> None:
    """Requirement: the response's order is the operator's configuration, not the client's ask.

    The ids arrive naming the second provider first, so an implementation that
    built its requests in the order the ids came in would answer in that order.
    """
    with _aggregating(
        tmp_path, accounts={"bank-a": [{"id": "a-1"}], "bank-b": [{"id": "b-1"}]}
    ) as (client, auth, _calls):
        response = client.get(
            "/simplefin/accounts?account=bank-b:b-1&account=bank-a:a-1", auth=auth
        )

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {
        "accounts": [{"id": "bank-a:a-1"}, {"id": "bank-b:b-1"}],
        "errors": [],
    }


def test_an_id_no_prefix_claims_reaches_no_provider_and_is_logged_not_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Requirement: an unroutable id costs the rest of the request nothing."""
    with (
        _aggregating(tmp_path, accounts={"bank-a": [{"id": "a-1"}]}) as (client, auth, calls),
        caplog.at_level(logging.WARNING),
    ):
        response = client.get(
            "/simplefin/accounts?account=bank-a:a-1&account=bank-c:c-1", auth=auth
        )

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"accounts": [{"id": "bank-a:a-1"}], "errors": []}
    assert "bank-c:c-1" not in response.text
    assert "bank-c:c-1" in caplog.text, "the operator is told which id it was"
    assert calls["bank-b"] == [], "an id belonging to nobody is not fanned out"


def test_a_request_naming_only_unknown_ids_asks_no_provider_at_all(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Requirement: no provider traffic beyond what the client app actually asked about."""
    with _aggregating(tmp_path) as (client, auth, calls), caplog.at_level(logging.WARNING):
        response = client.get("/simplefin/accounts?account=nobody:1&account=nobody:2", auth=auth)

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"accounts": [], "errors": []}
    assert calls == {"bank-a": [], "bank-b": []}
    assert "nobody:1" in caplog.text
    assert "nobody:2" in caplog.text


def test_a_blank_prefix_provider_may_collide_with_another_without_losing_an_account(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Requirement: a collision the operator configured costs a log line, not the user's data."""
    blank = ProviderSpec(
        key=BANK_A.key, origin=BANK_A.origin, prefix="", access_url=BANK_A.access_url
    )
    with (
        _aggregating(
            tmp_path,
            specs=(blank, BANK_B),
            accounts={
                "bank-a": [{"id": "bank-b:shared", "name": "from a"}],
                "bank-b": [{"id": "shared", "name": "from b"}],
            },
        ) as (client, auth, _calls),
        caplog.at_level(logging.WARNING),
    ):
        response = client.get("/simplefin/accounts", auth=auth)

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {
        "accounts": [
            {"id": "bank-b:shared", "name": "from a"},
            {"id": "bank-b:shared", "name": "from b"},
        ],
        "errors": [],
    }
    # Not `"bank-b" in caplog.text`: the colliding id contains it, so that would pass
    # even if the warning named one owner.
    assert "from bank-a, bank-b" in caplog.text


def test_with_several_blank_prefixes_each_id_reaches_every_provider_it_may_belong_to(
    tmp_path: Path,
) -> None:
    """Requirement: an id no named prefix claims is asked of every blank-prefix provider.

    A named id, alongside it, is asked only of the provider it names.
    """
    bank_c = ProviderSpec(
        key="bank-c",
        origin="https://bank-c.example.com",
        prefix="",
        access_url="https://user:pass@bank-c.example.com/simplefin",
    )
    with _aggregating(
        tmp_path,
        specs=(BANK_A._replace(prefix=""), BANK_B, bank_c),
        accounts={"bank-b": [{"id": "b-1"}], "bank-c": [{"id": "c-1"}]},
        allow_multiple_blank_prefixes=True,
    ) as (client, auth, calls):
        response = client.get("/simplefin/accounts?account=c-1&account=bank-b:b-1", auth=auth)

    assert response.status_code == HTTPStatus.OK
    assert calls == {
        "bank-a": [[("account", "c-1")]],
        "bank-b": [[("account", "b-1")]],
        "bank-c": [[("account", "c-1")]],
    }
    assert response.json() == {"accounts": [{"id": "bank-b:b-1"}, {"id": "c-1"}], "errors": []}


VERSIONS = ["1", "1.0", "2", "1.0.7", "", "one"]


def test_a_version_a_client_app_asks_for_changes_nothing(tmp_path: Path) -> None:
    """Requirement: v1 is the only protocol spoken in either direction, so nothing selects one."""
    with _aggregating(tmp_path) as (client, auth, calls):
        answered = [
            client.get(f"/simplefin/accounts?version={version}&version=9", auth=auth)
            for version in VERSIONS
        ]

    assert [response.status_code for response in answered] == [HTTPStatus.OK] * len(VERSIONS)
    assert [response.json() for response in answered] == [{"accounts": [], "errors": []}] * len(
        VERSIONS
    )
    assert calls == {"bank-a": [[]] * len(VERSIONS), "bank-b": [[]] * len(VERSIONS)}, (
        "every request reached both providers, none of them carrying a version"
    )


def test_a_provider_that_fails_is_asked_once_and_costs_only_its_own_accounts(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Requirement: nothing on this path retries, and one provider's failure is not the others'."""
    attempts: list[str] = []

    async def failing(request: httpx2.Request) -> httpx2.Response:
        attempts.append("bank-b")
        msg = "connection refused"
        raise httpx2.ConnectError(msg, request=request)

    async def answering(_request: httpx2.Request) -> httpx2.Response:
        attempts.append("bank-a")
        return httpx2.Response(HTTPStatus.OK, json={"accounts": [{"id": "a-1"}]})

    auth = add_client_and_exchange(tmp_path)
    app = make_app(tmp_path, make_config(BANK_A, BANK_B), make_access_urls(BANK_A, BANK_B))

    with TestClient(app) as client, caplog.at_level(logging.WARNING):
        install_provider_transport(app, "bank-a", answering)
        install_provider_transport(app, "bank-b", failing)
        response = client.get("/simplefin/accounts", auth=auth)

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"accounts": [{"id": "bank-a:a-1"}], "errors": []}
    assert sorted(attempts) == ["bank-a", "bank-b"], "one request each, and no retry"
    assert "bank-b" in caplog.text


@contextmanager
def _loopback_provider() -> Generator[int]:
    """A provider the app can really reach, answering every GET with a redirect.

    A live server rather than a mock transport, so the client exercised is the
    whole of what `build_provider_client` returns. Substituting a transport
    would replace the part that renders the outbound URL and sends it, which is
    the part this test is about.
    """

    class _Redirecting(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("location", "https://attacker.example.net/accounts")
            self.end_headers()

        @override
        def log_message(self, format: str, *args: object) -> None:
            """Silence the server's own request logging, which is not what is under test."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Redirecting)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield cast(tuple[str, int], server.server_address)[1]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_a_provider_cannot_get_its_credential_into_a_log_by_echoing_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Requirement: nothing a provider puts on the wire is rendered by this application."""
    password = "s3cret-provider-password"  # noqa: S105
    auth = add_client_and_exchange(tmp_path)

    with echoing_provider(echo_the_basic_auth_password) as (port, echoed):
        provider = ProviderSpec(
            origin=f"http://127.0.0.1:{port}",
            access_url=f"http://user:{password}@127.0.0.1:{port}/simplefin",
        )
        app = make_app(tmp_path, make_config(provider), make_access_urls(provider))
        with TestClient(app) as client:
            response = client.get("/simplefin/accounts", auth=auth)

    assert echoed == [f"NOT-HTTP user:{password}"], (
        "the provider did hand the credential back, which is what the rest of this tests"
    )
    assert response.status_code == HTTPStatus.OK
    assert "RemoteProtocolError" in caplog.text, "and this application saw the malformed reply"
    assert password not in response.text
    assert password not in caplog.text


def test_a_provider_request_names_no_credentials_in_the_logs_or_the_body(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Everything the app does with a stored access URL, with nothing stubbed out.

    The provider answers rather than merely being attempted: the one line that
    renders the outbound URL in full is written only when a response comes
    back, and that is the line credentials would reach.
    """
    password = "s3cret-provider-password"  # noqa: S105
    auth = add_client_and_exchange(tmp_path)

    with _loopback_provider() as port, caplog.at_level(logging.INFO):
        provider = ProviderSpec(
            origin=f"http://127.0.0.1:{port}",
            access_url=f"http://user:{password}@127.0.0.1:{port}/simplefin",
        )
        app = make_app(tmp_path, make_config(provider), make_access_urls(provider))
        with TestClient(app) as client:
            response = client.get("/simplefin/accounts", auth=auth)

    assert response.status_code == HTTPStatus.OK
    # Named exactly, since the test client logs a request line of its own: this
    # is the provider request, rendered as httpx2 rendered it.
    assert f"HTTP Request: GET http://127.0.0.1:{port}/simplefin/accounts" in caplog.text
    assert "provider request: my-bank" in caplog.text
    assert password not in caplog.text
    assert password not in response.text
