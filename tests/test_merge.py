from __future__ import annotations

import json
import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, cast

import pytest

from simplefin_aggregator.merge import merge
from simplefin_aggregator.provider_response import ProviderFailure, ProviderSuccess


if TYPE_CHECKING:
    from collections.abc import Sequence

    from simplefin_aggregator.provider_response import ProviderResponse

    Account = dict[str, object]
    Result = tuple[str, ProviderResponse]

PROVIDER_A = "bank-a"
PROVIDER_B = "bank-b"


def _body(key: str, payload: object, status: int = HTTPStatus.OK) -> ProviderSuccess:
    return _raw(key, json.dumps(payload).encode(), status)


def _raw(key: str, body: bytes, status: int = HTTPStatus.OK) -> ProviderSuccess:
    return ProviderSuccess(provider_name=key, status=status, body=body)


def _accounts(key: str, *ids: str, status: int = HTTPStatus.OK) -> ProviderSuccess:
    return _body(key, {"accounts": [{"id": account_id} for account_id in ids]}, status)


def _payload(results: Sequence[Result]) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(merge(results).body))


def _merged(results: Sequence[Result]) -> tuple[list[Account], list[str]]:
    payload = _payload(results)
    return (cast("list[Account]", payload["accounts"]), cast("list[str]", payload["errors"]))


def _ids(accounts: Sequence[Account]) -> list[str]:
    return [cast("str", account["id"]) for account in accounts]


def test_accounts_follow_the_given_order_and_each_carries_its_own_prefix() -> None:
    """Requirement: the merged order is the configured one, not the providers' own or a sorted one.

    The first provider's prefix sorts after the second's, so an implementation
    that ordered by prefix or by id would fail this rather than pass by luck.
    """
    accounts, errors = _merged(
        [
            ("z-bank:", _accounts(PROVIDER_A, "acc-1", "acc-2")),
            ("a-bank:", _accounts(PROVIDER_B, "acc-9")),
        ]
    )

    assert _ids(accounts) == ["z-bank:acc-1", "z-bank:acc-2", "a-bank:acc-9"]
    assert errors == []


def test_a_provider_contributes_its_accounts_and_its_errors_together() -> None:
    """Requirement: a provider that reached some of its banks and not others reports both."""
    accounts, errors = _merged(
        [
            (
                "p:",
                _body(
                    PROVIDER_A,
                    {
                        "accounts": [{"id": "reached-1"}, {"id": "reached-2"}],
                        "errors": ["Connection to Third Bank may need attention."],
                    },
                ),
            )
        ]
    )

    assert _ids(accounts) == ["p:reached-1", "p:reached-2"]
    assert errors == ["Connection to Third Bank may need attention."]


def test_provider_errors_follow_the_same_order_as_their_accounts() -> None:
    """Requirement: a client app reading `errors` top to bottom gets the configured order."""
    accounts, errors = _merged(
        [
            ("a:", _body(PROVIDER_A, {"accounts": [{"id": "1"}], "errors": ["a is unhappy"]})),
            ("b:", _body(PROVIDER_B, {"accounts": [{"id": "2"}], "errors": ["b is unhappy"]})),
        ]
    )

    assert _ids(accounts) == ["a:1", "b:2"]
    assert errors == ["a is unhappy", "b is unhappy"]


def test_a_providers_own_errors_are_passed_through_verbatim() -> None:
    """Requirement: v1's `errors` is the provider's channel to the user; this is a relay."""
    said = "Connection to Example Bank may need attention. Reauthorize at example.com."
    _, errors = _merged([("p:", _body(PROVIDER_A, {"accounts": [], "errors": [said]}))])

    assert errors == [said]


def test_an_absent_errors_key_is_the_same_as_an_empty_one() -> None:
    """Requirement: v1 makes `errors` optional, so a provider omitting it has reported nothing."""
    _, errors = _merged([("p:", _body(PROVIDER_A, {"accounts": [{"id": "x"}]}))])

    assert errors == []


UNUSABLE = [
    pytest.param(
        ProviderFailure(provider_name=PROVIDER_B, error="connection refused"), id="no-response"
    ),
    pytest.param(_raw(PROVIDER_B, b"<html>not json</html>"), id="body-is-not-json"),
    pytest.param(
        _raw(PROVIDER_B, b'{"accounts": ' + b"[" * 10000 + b"]" * 10000 + b"}"),
        id="body-is-nested-past-the-decoder's-limit",
    ),
    pytest.param(_raw(PROVIDER_B, b"\xff\xfe not utf-8"), id="body-is-not-utf-8"),
    pytest.param(_body(PROVIDER_B, [{"id": "acc-9"}]), id="body-is-not-an-object"),
    pytest.param(_body(PROVIDER_B, {"errors": []}), id="no-accounts-key"),
    pytest.param(_body(PROVIDER_B, {"accounts": {"id": "acc-9"}}), id="accounts-is-not-a-list"),
    pytest.param(_body(PROVIDER_B, {"accounts": ["acc-9"]}), id="account-is-not-an-object"),
    pytest.param(_body(PROVIDER_B, {"accounts": [{"name": "no id"}]}), id="account-has-no-id"),
    pytest.param(_body(PROVIDER_B, {"accounts": [{"id": 9}]}), id="account-id-is-not-a-string"),
    pytest.param(_accounts(PROVIDER_B, "acc-9", status=HTTPStatus.PAYMENT_REQUIRED), id="402"),
    pytest.param(_accounts(PROVIDER_B, "acc-9", status=HTTPStatus.FORBIDDEN), id="403"),
    pytest.param(_accounts(PROVIDER_B, "acc-9", status=HTTPStatus.FOUND), id="3xx"),
    pytest.param(_accounts(PROVIDER_B, "acc-9", status=HTTPStatus.INTERNAL_SERVER_ERROR), id="500"),
]


@pytest.mark.parametrize("unusable", UNUSABLE)
def test_every_way_of_failing_is_the_same_failure(
    unusable: ProviderResponse, caplog: pytest.LogCaptureFixture
) -> None:
    """Requirement: however a provider fails, it costs only that provider's accounts.

    The 402, 403, 3xx and 500 cases carry a perfectly good account set, so what
    rejects them is the status and nothing else.
    """
    with caplog.at_level(logging.WARNING):
        accounts, errors = _merged([("a:", _accounts(PROVIDER_A, "acc-1")), ("b:", unusable)])

    assert _ids(accounts) == ["a:acc-1"]
    assert errors == []
    assert PROVIDER_B in caplog.text


def test_a_failed_provider_is_logged_and_not_reported(caplog: pytest.LogCaptureFixture) -> None:
    """Behavior, not a requirement: a failed provider puts nothing in the body.

    A bare string naming the provider tells a client app nothing it can act on,
    so nothing is put in the body. The diagnostic goes to the log, and the
    exception text stays there.
    """
    with caplog.at_level(logging.WARNING):
        accounts, errors = _merged(
            [
                (
                    "b:",
                    ProviderFailure(
                        provider_name=PROVIDER_B, error="[Errno 111] Connection refused"
                    ),
                )
            ]
        )

    assert accounts == []
    assert errors == []
    assert PROVIDER_B in caplog.text
    assert "[Errno 111] Connection refused" in caplog.text, "the reason is what makes it useful"


def test_every_provider_failing_still_answers_with_a_well_formed_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Requirement: total failure is still a v1 response, not a withheld one."""
    with caplog.at_level(logging.WARNING):
        payload = _payload(
            [
                ("a:", ProviderFailure(provider_name=PROVIDER_A, error="timed out")),
                ("b:", _raw(PROVIDER_B, b"nonsense")),
            ]
        )

    assert payload == {"accounts": [], "errors": []}
    assert PROVIDER_A in caplog.text
    assert PROVIDER_B in caplog.text


def test_no_providers_at_all_yields_a_well_formed_empty_response() -> None:
    """Requirement: a request naming only ids no provider owns still gets a v1 body."""
    assert _payload([]) == {"accounts": [], "errors": []}


def test_every_key_inside_an_account_survives_and_only_the_id_changes() -> None:
    """Requirement: this aggregator relays account contents it has no opinion about."""
    sent = {
        "id": "acc-1",
        "name": "Checking",
        "currency": "USD",
        "balance": "12.34",
        "org": {"domain": "example.com", "sfin-url": "https://example.com/simplefin"},
        "transactions": [{"id": "t-1", "amount": "-1.00"}],
        "extra": {"a-key-this-code-never-heard-of": True},
    }

    accounts, _ = _merged([("p:", _body(PROVIDER_A, {"accounts": [sent]}))])

    assert accounts == [{**sent, "id": "p:acc-1"}]


def test_top_level_keys_this_application_does_not_speak_are_dropped() -> None:
    """Requirement: the response is a v1 response built here, not a relayed v2 one.

    Held by construction -- the payload is a literal with two keys -- so this
    guards against a future rewrite into a passthrough rather than against a
    bug reachable today. The account is here so that such a rewrite fails on
    the prefixed id as well as on the key set.
    """
    payload = _payload(
        [
            (
                "p:",
                _body(
                    PROVIDER_A,
                    {
                        "accounts": [{"id": "acc-1"}],
                        "errors": [],
                        "errlist": [{"code": "gen.unreachable", "msg": "nope"}],
                        "connections": [{"id": "c-1"}],
                        "x-vendor-extension": True,
                    },
                ),
            )
        ]
    )

    assert set(payload) == {"accounts", "errors"}
    assert payload["accounts"] == [{"id": "p:acc-1"}]


def test_an_account_name_survives_as_text_rather_than_as_escapes() -> None:
    """Requirement: the body is UTF-8, so a non-ASCII name needs no escaping."""
    body = merge(
        [("p:", _body(PROVIDER_A, {"accounts": [{"id": "x", "name": "Sparkonto Ø"}]}))]
    ).body

    assert "Sparkonto Ø".encode() in body


def test_unreadable_provider_errors_cost_only_the_messages(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Requirement: accounts are real data, and an unreadable diagnostic beside them is not."""
    with caplog.at_level(logging.WARNING):
        accounts, errors = _merged(
            [("p:", _body(PROVIDER_A, {"accounts": [{"id": "x"}], "errors": "not a list"}))]
        )

    assert _ids(accounts) == ["p:x"]
    assert errors == []
    assert PROVIDER_A in caplog.text


def test_two_providers_sharing_a_merged_id_keep_both_accounts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Requirement: a blank prefix is allowed to collide, and data is not dropped to hide it."""
    with caplog.at_level(logging.WARNING):
        accounts, errors = _merged(
            [
                ("", _body(PROVIDER_A, {"accounts": [{"id": "shared", "name": "from a"}]})),
                ("s", _body(PROVIDER_B, {"accounts": [{"id": "hared", "name": "from b"}]})),
            ]
        )

    assert _ids(accounts) == ["shared", "shared"]
    assert [account["name"] for account in accounts] == ["from a", "from b"]
    assert errors == [], "a client app can do nothing with a collision; the operator can"
    assert PROVIDER_A in caplog.text
    assert PROVIDER_B in caplog.text


def test_one_provider_repeating_an_id_keeps_both_accounts_too(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Requirement: a provider's own duplicate is still the user's data.

    The log line counts the accounts and names the provider once, since
    "from bank-a, bank-a" would read as two providers to fix and there is one.
    """
    with caplog.at_level(logging.WARNING):
        accounts, errors = _merged([("p:", _accounts(PROVIDER_A, "twice", "twice"))])

    assert _ids(accounts) == ["p:twice", "p:twice"]
    assert errors == []
    assert "appears 2 times, from bank-a" in caplog.text
