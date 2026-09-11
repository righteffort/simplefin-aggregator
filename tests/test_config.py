from __future__ import annotations

import stat
from typing import TYPE_CHECKING

import pytest

from simplefin_aggregator.config import Config, ConfigError, load_config
from simplefin_aggregator.provider_registry import KNOWN_PROVIDERS


if TYPE_CHECKING:
    from pathlib import Path


VALID_TOML = """
bind_host = "127.0.0.2"
bind_port = 9999
base_url = "http://127.0.0.1:8080"

# Before [[providers]] so that a test can cut the providers off the end.
[[custom_providers]]
key = "my-bank"
label = "My Bank"
root = "https://provider.example.com/simplefin"

[[providers]]
key = "my-bank"
"""

ROOT_LINE = 'root = "https://provider.example.com/simplefin"'
KEY_LINE = 'key = "my-bank"'


def _with_provider_key(key: str) -> str:
    """VALID_TOML with only the [[providers]] key changed -- the last one in the file."""
    head, _, tail = VALID_TOML.rpartition(KEY_LINE)
    return f'{head}key = "{key}"{tail}'


def _write(tmp_path: Path, contents: str) -> Path:
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(contents)
    config_path.chmod(0o600)
    return config_path


def test_load_config_tolerates_a_byte_order_mark(tmp_path: Path) -> None:
    """Some editors write one; TOML would otherwise fail on line 1, column 1."""
    path = tmp_path / "config.toml"
    _ = path.write_text(VALID_TOML, encoding="utf-8-sig")
    path.chmod(0o600)

    config = load_config(path)

    assert config.providers[0].key == "my-bank"


def test_load_config_parses_valid_file(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID_TOML)

    config = load_config(path)

    assert isinstance(config, Config)
    assert config.bind_host == "127.0.0.2"
    assert config.bind_port == 9999  # noqa: PLR2004
    assert len(config.providers) == 1
    assert config.providers[0].key == "my-bank"


def test_load_config_accepts_http_base_url_on_loopback_ipv6(tmp_path: Path) -> None:
    ok_toml = VALID_TOML.replace(
        'base_url = "http://127.0.0.1:8080"', 'base_url = "http://[::1]:8080"'
    )
    path = _write(tmp_path, ok_toml)

    config = load_config(path)

    assert config.base_url == "http://[::1]:8080"


def test_load_config_rejects_http_base_url_on_non_loopback_ip(tmp_path: Path) -> None:
    bad_toml = VALID_TOML.replace(
        'base_url = "http://127.0.0.1:8080"', 'base_url = "http://192.168.1.10:8080"'
    )
    path = _write(tmp_path, bad_toml)

    with pytest.raises(ConfigError, match="https"):
        _ = load_config(path)


def test_load_config_rejects_http_base_url_on_hostname(tmp_path: Path) -> None:
    bad_toml = VALID_TOML.replace(
        'base_url = "http://127.0.0.1:8080"', 'base_url = "http://localhost:8080"'
    )
    path = _write(tmp_path, bad_toml)

    with pytest.raises(ConfigError, match="https"):
        _ = load_config(path)


def test_load_config_accepts_https_base_url_on_non_loopback_host(tmp_path: Path) -> None:
    ok_toml = VALID_TOML.replace(
        'base_url = "http://127.0.0.1:8080"', 'base_url = "https://aggregator.example.com"'
    )
    path = _write(tmp_path, ok_toml)

    config = load_config(path)

    assert config.base_url == "https://aggregator.example.com"


def test_load_config_rejects_base_url_with_userinfo(tmp_path: Path) -> None:
    bad_toml = VALID_TOML.replace(
        'base_url = "http://127.0.0.1:8080"', 'base_url = "http://olduser:oldpass@127.0.0.1:8080"'
    )
    path = _write(tmp_path, bad_toml)

    with pytest.raises(ConfigError, match="base_url"):
        _ = load_config(path)


def test_load_config_rejects_a_base_url_no_setup_token_could_carry(tmp_path: Path) -> None:
    """A setup token embeds this URL as ASCII, so a host outside it never works."""
    bad_toml = VALID_TOML.replace(
        'base_url = "http://127.0.0.1:8080"', 'base_url = "https://ex\u00e4mple.test"'
    )
    path = _write(tmp_path, bad_toml)

    with pytest.raises(ConfigError, match="ASCII"):
        _ = load_config(path)


def test_load_config_rejects_base_url_with_username_only(tmp_path: Path) -> None:
    bad_toml = VALID_TOML.replace(
        'base_url = "http://127.0.0.1:8080"', 'base_url = "http://olduser@127.0.0.1:8080"'
    )
    path = _write(tmp_path, bad_toml)

    with pytest.raises(ConfigError, match="base_url"):
        _ = load_config(path)


def test_load_config_rejects_malformed_toml(tmp_path: Path) -> None:
    path = _write(tmp_path, "this is not [valid toml")

    with pytest.raises(ConfigError, match="malformed TOML"):
        _ = load_config(path)


def test_load_config_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        _ = load_config(tmp_path / "does-not-exist.toml")


def test_load_config_rejects_zero_providers(tmp_path: Path) -> None:
    no_providers = VALID_TOML.split("[[providers]]", maxsplit=1)[0]
    path = _write(tmp_path, no_providers)

    with pytest.raises(ConfigError):
        _ = load_config(path)


def _providers_toml(*entries: tuple[str, str | None]) -> str:
    """A config naming several providers by key, each with an explicit prefix or none.

    Every key named gets a `custom_providers` entry, deduplicated, so that a
    repeated key is rejected for being repeated in `providers` rather than for
    defining the same custom provider twice.
    """
    customs = "".join(
        f"""
[[custom_providers]]
key = "{key}"
label = "Test Provider"
root = "https://{key}.example.com/simplefin"
"""
        for key in dict.fromkeys(key for key, _ in entries)
    )
    providers = "".join(
        f"""
[[providers]]
key = "{key}"
"""
        + ("" if prefix is None else f'prefix = "{prefix}"\n')
        for key, prefix in entries
    )
    return f'base_url = "http://127.0.0.1:8080"\n{customs}{providers}'


def test_a_providers_prefix_defaults_to_its_key_and_a_colon(tmp_path: Path) -> None:
    """Requirement: an unconfigured prefix namespaces that provider's ids anyway.

    The colon is what keeps one default from being a prefix of another, since
    a key cannot contain one.
    """
    path = _write(tmp_path, _providers_toml(("bank-a", None), ("bank-b", None)))

    config = load_config(path)

    assert [provider.prefix for provider in config.providers] == ["bank-a:", "bank-b:"]


def test_an_explicit_prefix_replaces_the_default(tmp_path: Path) -> None:
    """Requirement: the operator owns the prefix, including the blank one.

    A blank prefix is how someone already syncing straight from a provider
    keeps the account ids their client app holds, so it has to be
    distinguishable from having said nothing.
    """
    path = _write(tmp_path, _providers_toml(("bank-a", ""), ("bank-b", "b.")))

    config = load_config(path)

    assert [provider.prefix for provider in config.providers] == ["", "b."]


def test_load_config_accepts_several_providers(tmp_path: Path) -> None:
    """Requirement: aggregating more than one provider is the point of the application."""
    path = _write(tmp_path, _providers_toml(("bank-a", None), ("bank-b", None), ("bank-c", None)))

    config = load_config(path)

    assert [provider.key for provider in config.providers] == ["bank-a", "bank-b", "bank-c"]


def test_load_config_rejects_a_provider_key_named_twice(tmp_path: Path) -> None:
    """Requirement: everything per-provider is keyed by the key, so two entries would collapse."""
    path = _write(tmp_path, _providers_toml(("bank-a", "one."), ("bank-a", "two.")))

    with pytest.raises(ConfigError, match="more than once"):
        _ = load_config(path)


def test_load_config_rejects_two_blank_prefixes(tmp_path: Path) -> None:
    """Requirement: the blank prefix is the catch-all, and two catch-alls name no owner."""
    path = _write(tmp_path, _providers_toml(("bank-a", ""), ("bank-b", "")))

    with pytest.raises(ConfigError, match="blank prefix"):
        _ = load_config(path)


def test_load_config_rejects_a_prefix_that_is_a_prefix_of_another(tmp_path: Path) -> None:
    """Requirement: distinctness is not enough -- `bank` and `bank2` are distinct and ambiguous."""
    path = _write(tmp_path, _providers_toml(("bank-a", "bank"), ("bank-b", "bank2")))

    with pytest.raises(ConfigError, match="is a prefix of"):
        _ = load_config(path)


def test_load_config_accepts_one_blank_prefix_beside_prefix_free_ones(tmp_path: Path) -> None:
    """Requirement: the catch-all is legal, which is what the rule above has to leave room for."""
    path = _write(tmp_path, _providers_toml(("bank-a", ""), ("bank-b", "b:"), ("bank-c", "bank2")))

    config = load_config(path)

    assert [provider.prefix for provider in config.providers] == ["", "b:", "bank2"]


def test_load_config_rejects_a_prefix_a_url_would_have_to_escape(tmp_path: Path) -> None:
    """Requirement: a prefix travels in a query parameter and lands in a client app's database."""
    path = _write(tmp_path, _providers_toml(("bank-a", "my bank:")))

    with pytest.raises(ConfigError, match="must match"):
        _ = load_config(path)


def test_a_rejected_prefix_is_not_quoted_back(tmp_path: Path) -> None:
    """Requirement: a value out of this file reaches no message, however unlikely a secret is here.

    The config holds no credentials, but a paste into the wrong field is how
    one would get here, and the entry's position says which prefix it was.
    """
    path = _write(tmp_path, _providers_toml(("bank-a", "https://user:leak-password@host/")))

    with pytest.raises(ConfigError) as exc_info:
        _ = load_config(path)

    message = str(exc_info.value)
    assert "leak-password" not in message
    assert "providers.0" in message, "the entry is named even though its value is not"


def test_load_config_warns_on_permissive_file_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, VALID_TOML)
    path.chmod(0o644)

    _ = load_config(path)

    assert "chmod 600" in capsys.readouterr().err


def test_load_config_does_not_warn_on_owner_only_file_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, VALID_TOML)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    _ = load_config(path)

    assert capsys.readouterr().err == ""


def test_the_config_holds_no_credential_to_redact(tmp_path: Path) -> None:
    """Pins a requirement: nothing in this file is a secret any more.

    The client app's credentials and the claim token used to live here. They
    are digests in the app token store now, so the whole config is safe to
    render.
    """
    config = load_config(_write(tmp_path, VALID_TOML))

    assert set(type(config).model_fields) == {
        "bind_host",
        "bind_port",
        "providers",
        "custom_providers",
        "base_url",
    }


def test_provider_entries_are_the_built_in_ones_plus_the_config_s(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, VALID_TOML))

    entries = config.provider_entries()

    assert [entry.key for entry in entries] == [
        *(entry.key for entry in KNOWN_PROVIDERS),
        "my-bank",
    ]
    assert entries[-1].root.origin_and_path == "https://provider.example.com/simplefin/"


def test_load_config_accepts_a_provider_key_naming_a_built_in_provider(tmp_path: Path) -> None:
    """A built-in provider needs no custom provider entry of its own."""
    built_in_only = _with_provider_key("redbark")
    config = load_config(_write(tmp_path, built_in_only))

    assert config.providers[0].key == "redbark"


def test_load_config_rejects_a_provider_key_no_entry_defines(tmp_path: Path) -> None:
    dangling = _with_provider_key("no-such-bank")
    path = _write(tmp_path, dangling)

    with pytest.raises(ConfigError, match="unknown provider 'no-such-bank'"):
        _ = load_config(path)


def test_load_config_rejects_a_custom_provider_key_that_shadows_a_built_in_provider(
    tmp_path: Path,
) -> None:
    shadowing = VALID_TOML.replace(KEY_LINE, 'key = "redbark"')
    path = _write(tmp_path, shadowing)

    with pytest.raises(ConfigError, match="duplicate provider key 'redbark'"):
        _ = load_config(path)


def test_load_config_rejects_a_malformed_custom_provider_key(tmp_path: Path) -> None:
    bad_key = VALID_TOML.replace(KEY_LINE, 'key = "My Bank"')
    path = _write(tmp_path, bad_key)

    with pytest.raises(ConfigError, match="must match"):
        _ = load_config(path)


CUSTOM_PROVIDER_ROOT_CASES = [
    ("http://provider.example.com/simplefin", "must use https"),
    ("https://user:pass@provider.example.com/simplefin", "must not contain credentials"),
    ("https://provider.example.com:99999/simplefin", "not a valid URL"),
    ("https://provider.example.com/simplefin?x=1", "query string or fragment"),
    ("https://provider.example.com/simplefin#f", "query string or fragment"),
    ("/simplefin", "has no host"),
]


@pytest.mark.parametrize(("root", "expected"), CUSTOM_PROVIDER_ROOT_CASES)
def test_load_config_rejects_a_bad_custom_provider_root(
    tmp_path: Path, root: str, expected: str
) -> None:
    bad_root = VALID_TOML.replace(ROOT_LINE, f'root = "{root}"')
    path = _write(tmp_path, bad_root)

    with pytest.raises(ConfigError, match=expected):
        _ = load_config(path)


def test_rejected_custom_provider_root_error_does_not_quote_the_credentials(tmp_path: Path) -> None:
    """A rejected root is named in the error, but never by quoting the raw input back."""
    root = "https://leak-username:leak-password@provider.example.com/simplefin"
    path = _write(tmp_path, VALID_TOML.replace(ROOT_LINE, f'root = "{root}"'))

    with pytest.raises(ConfigError) as exc_info:
        _ = load_config(path)

    message = str(exc_info.value)
    assert "leak-username" not in message
    assert "leak-password" not in message
    # Named as a fault of the entry, so a config with several says which one.
    # The field within it is not named: a location is rendered from the
    # top-level model's fields alone, and `root` belongs to the nested one.
    assert "custom_providers.0" in message


def test_load_config_accepts_a_loopback_http_custom_provider_root(tmp_path: Path) -> None:
    """The one non-https root allowed: a provider on the loopback interface."""
    loopback = VALID_TOML.replace(ROOT_LINE, 'root = "http://127.0.0.1:8081/simplefin"')

    config = load_config(_write(tmp_path, loopback))

    assert config.provider_entries()[-1].root.origin_and_path == "http://127.0.0.1:8081/simplefin/"
