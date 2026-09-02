from __future__ import annotations

from typing import TYPE_CHECKING

from simplefin_aggregator.access_url import build_access_url

from .support import make_config


if TYPE_CHECKING:
    from simplefin_aggregator.config import Config


def _config(base_url: str, username: str = "app-username", password: str = "s3cret") -> Config:
    return make_config(base_url=base_url, username=username, password=password)


def test_build_access_url_embeds_consumer_credentials_and_simplefin_path() -> None:
    config = _config("http://127.0.0.1:8080")

    assert build_access_url(config) == "http://app-username:s3cret@127.0.0.1:8080/simplefin"


def test_build_access_url_percent_encodes_special_characters_in_credentials() -> None:
    config = _config("http://127.0.0.1:8080", username="user@name", password="p@ss:word")

    assert build_access_url(config) == "http://user%40name:p%40ss%3Aword@127.0.0.1:8080/simplefin"


def test_build_access_url_strips_trailing_slash_from_base_url_path() -> None:
    config = _config("http://127.0.0.1:8080/")

    assert build_access_url(config) == "http://app-username:s3cret@127.0.0.1:8080/simplefin"


def test_build_access_url_uses_base_url_scheme() -> None:
    config = _config("https://aggregator.example.com")

    assert (
        build_access_url(config) == "https://app-username:s3cret@aggregator.example.com/simplefin"
    )
