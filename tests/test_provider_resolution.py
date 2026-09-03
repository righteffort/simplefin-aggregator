from simplefin_aggregator.provider_resolution import resolve_provider_for_account

from .support import make_config


def test_resolve_provider_for_account_returns_the_sole_configured_provider() -> None:
    config = make_config()

    resolved = resolve_provider_for_account("any-account-id", config.providers)

    assert resolved is config.providers[0]
