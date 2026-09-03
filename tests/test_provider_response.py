from simplefin_aggregator.provider_response import ProviderFailure, ProviderSuccess


def test_provider_success_parses_json_body_on_access() -> None:
    response = ProviderSuccess(
        provider_name="my-bank",
        status=200,
        headers={"content-type": "application/json"},
        body=b'{"accounts": []}',
    )

    assert response.ok is True
    assert response.json == {"accounts": []}  # pyright: ignore[reportAny]


def test_provider_failure_has_no_status_or_body() -> None:
    response = ProviderFailure(provider_name="my-bank", error="connection refused")

    assert response.ok is False
    assert response.error == "connection refused"
