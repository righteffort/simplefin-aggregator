import json
from http import HTTPStatus

from simplefin_aggregator.merge import merge
from simplefin_aggregator.provider_response import ProviderFailure, ProviderSuccess


def test_merge_passes_single_success_through_unchanged() -> None:
    body = b'{"accounts": [{"id": "acc-1"}]}'
    response = ProviderSuccess(
        provider_name="my-bank",
        status=HTTPStatus.OK,
        headers={"content-type": "application/json"},
        body=body,
    )

    merged = merge([response])

    assert merged.status == HTTPStatus.OK
    assert merged.content_type == "application/json"
    assert merged.body == body


def test_merge_preserves_non_2xx_status_from_provider() -> None:
    response = ProviderSuccess(
        provider_name="my-bank",
        status=HTTPStatus.FORBIDDEN,
        headers={"content-type": "text/plain"},
        body=b"forbidden",
    )

    merged = merge([response])

    assert merged.status == HTTPStatus.FORBIDDEN
    assert merged.body == b"forbidden"


def test_merge_turns_a_failure_into_a_502_with_simplefin_shaped_body() -> None:
    response = ProviderFailure(provider_name="my-bank", error="connection refused")

    merged = merge([response])

    assert merged.status == HTTPStatus.BAD_GATEWAY
    assert merged.content_type == "application/json"
    payload = json.loads(merged.body)  # pyright: ignore[reportAny]
    assert payload["errors"] == ["connection refused"]
    assert payload["errlist"][0]["msg"] == "connection refused"
