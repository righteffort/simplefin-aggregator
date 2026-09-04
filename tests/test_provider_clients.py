from __future__ import annotations

from base64 import b64decode

from simplefin_aggregator.config import Provider
from simplefin_aggregator.provider_clients import build_provider_client


def _provider(access_url: str) -> Provider:
    return Provider.model_validate({"name": "my-bank", "access_url": access_url})


def test_build_provider_client_bare_hostname() -> None:
    provider = _provider("https://user:pass@provider.example.com/simplefin")

    client = build_provider_client(provider)

    assert str(client.base_url) == "https://provider.example.com/simplefin/"


def test_build_provider_client_brackets_ipv6_host() -> None:
    provider = _provider("https://user:pass@[::1]:8443/simplefin")

    client = build_provider_client(provider)

    assert str(client.base_url) == "https://[::1]:8443/simplefin/"


def test_build_provider_client_decodes_percent_encoded_credentials() -> None:
    # "user@name" / "p@ss" as they'd have to appear percent-encoded in a URL's userinfo.
    provider = _provider("https://user%40name:p%40ss@provider.example.com/simplefin")

    client = build_provider_client(provider)

    auth = client.auth
    assert auth is not None
    request = client.build_request("GET", "/accounts")
    authed_request = next(auth.auth_flow(request))
    scheme, _, token = authed_request.headers["authorization"].partition(" ")
    assert scheme == "Basic"
    assert b64decode(token).decode() == "user@name:p@ss"
