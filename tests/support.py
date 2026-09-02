"""Shared test helpers."""

from simplefin_aggregator.config import Config


def make_config(
    *,
    base_url: str = "http://127.0.0.1:8080",
    claim_token: str = "the-claim-token",
    username: str = "app-username",
    password: str = "s3cret-password",
    access_url: str = "https://user:pass@provider.example.com/simplefin",
) -> Config:
    """Build a Config the same way load_config does: from an untyped dict."""
    return Config.model_validate(
        {
            "base_url": base_url,
            "claim_token": claim_token,
            "consumer": {"username": username, "password": password},
            "providers": [{"name": "my-bank", "access_url": access_url}],
        }
    )
