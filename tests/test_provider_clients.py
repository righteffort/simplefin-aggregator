from __future__ import annotations

from base64 import b64decode

from simplefin_aggregator.provider_clients import build_provider_client
from simplefin_aggregator.url_validation import parse_root, validate_access_url


def test_build_provider_client_bare_hostname() -> None:
    access_url = validate_access_url(
        parse_root("https://provider.example.com/simplefin"),
        "https://user:pass@provider.example.com/simplefin",
        provider="my-bank",
    )

    client = build_provider_client(access_url)

    assert str(client.base_url) == "https://provider.example.com/simplefin/"


def test_build_provider_client_brackets_ipv6_host() -> None:
    access_url = validate_access_url(
        parse_root("https://[::1]:8443/simplefin"),
        "https://user:pass@[::1]:8443/simplefin",
        provider="my-bank",
    )

    client = build_provider_client(access_url)

    assert str(client.base_url) == "https://[::1]:8443/simplefin/"


def test_build_provider_client_decodes_percent_encoded_credentials() -> None:
    # "user@name" / "p@ss" as they'd have to appear percent-encoded in a URL's
    # userinfo. urlsplit does not decode them, so build_provider_client must.
    access_url = validate_access_url(
        parse_root("https://provider.example.com/simplefin"),
        "https://user%40name:p%40ss@provider.example.com/simplefin",
        provider="my-bank",
    )

    client = build_provider_client(access_url)

    auth = client.auth
    assert auth is not None
    request = client.build_request("GET", "/accounts")
    authed_request = next(auth.auth_flow(request))
    scheme, _, token = authed_request.headers["authorization"].partition(" ")
    assert scheme == "Basic"
    assert b64decode(token).decode() == "user@name:p@ss"


def test_build_provider_client_base_url_carries_no_credentials() -> None:
    access_url = validate_access_url(
        parse_root("https://provider.example.com/simplefin"),
        "https://user:s3cret-provider-password@provider.example.com/simplefin",
        provider="my-bank",
    )

    client = build_provider_client(access_url)

    assert "s3cret-provider-password" not in str(client.base_url)
