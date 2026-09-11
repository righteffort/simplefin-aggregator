from simplefin_aggregator.provider_response import ProviderFailure, ProviderSuccess


def test_provider_success_carries_the_body_as_the_provider_sent_it() -> None:
    response = ProviderSuccess(provider_name="my-bank", status=200, body=b'{"accounts": []}')

    assert response.ok is True
    assert response.body == b'{"accounts": []}'


def test_provider_failure_has_no_status_or_body() -> None:
    response = ProviderFailure(provider_name="my-bank", error="connection refused")

    assert response.ok is False
    assert response.error == "connection refused"
