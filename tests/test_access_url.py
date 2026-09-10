from __future__ import annotations

from pydantic import SecretStr

from simplefin_aggregator.access_url import build_access_url
from simplefin_aggregator.app_tokens import ClientCredentials


def _credentials(username: str = "the-username", password: str = "s3cret") -> ClientCredentials:  # noqa: S107
    return ClientCredentials(SecretStr(username), SecretStr(password))


def test_the_access_url_carries_the_credentials_and_the_simplefin_path() -> None:
    url = build_access_url("http://127.0.0.1:8080", _credentials())

    assert url == "http://the-username:s3cret@127.0.0.1:8080/simplefin"


def test_a_character_that_would_change_the_url_is_encoded() -> None:
    """Pins a requirement, not the generator: whatever mints a credential, the URL parses.

    Nothing this application generates contains these characters, which is
    exactly why the encoding has to be checked rather than assumed away.
    """
    url = build_access_url("http://127.0.0.1:8080", _credentials("user@name", "p@ss:word"))

    assert url == "http://user%40name:p%40ss%3Aword@127.0.0.1:8080/simplefin"


def test_a_base_url_ending_in_a_slash_gives_the_same_access_url() -> None:
    url = build_access_url("http://127.0.0.1:8080/", _credentials())

    assert url == "http://the-username:s3cret@127.0.0.1:8080/simplefin"


def test_the_access_url_keeps_the_base_url_scheme() -> None:
    url = build_access_url("https://aggregator.example.com", _credentials())

    assert url == "https://the-username:s3cret@aggregator.example.com/simplefin"
