from __future__ import annotations

import stat
from typing import TYPE_CHECKING

import pytest

from simplefin_aggregator.config import Config, ConfigError, load_config


if TYPE_CHECKING:
    from pathlib import Path


VALID_TOML = """
bind_host = "127.0.0.1"
bind_port = 8080
base_url = "http://127.0.0.1:8080"
claim_token = "s3cret-claim-token"

[client]
username = "client-username"
password = "s3cret-password"

[[providers]]
name = "my-bank"
access_url = "https://user:pass@provider.example.com/simplefin"
"""


def _write(tmp_path: Path, contents: str) -> Path:
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(contents)
    config_path.chmod(0o600)
    return config_path


def test_load_config_parses_valid_file(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID_TOML)

    config = load_config(path)

    assert isinstance(config, Config)
    assert config.bind_host == "127.0.0.1"
    assert config.bind_port == 8080
    assert len(config.providers) == 1
    assert config.providers[0].name == "my-bank"
    assert config.client.username == "client-username"


def test_load_config_rejects_malformed_access_url_at_load_time(tmp_path: Path) -> None:
    bad_toml = VALID_TOML.replace(
        'access_url = "https://user:pass@provider.example.com/simplefin"',
        'access_url = "https://provider.example.com/simplefin"',  # missing basic auth creds
    )
    path = _write(tmp_path, bad_toml)

    with pytest.raises(ConfigError, match="basic auth"):
        _ = load_config(path)


def test_load_config_rejects_non_https_access_url(tmp_path: Path) -> None:
    bad_toml = VALID_TOML.replace(
        'access_url = "https://user:pass@provider.example.com/simplefin"',
        'access_url = "http://user:pass@provider.example.com/simplefin"',
    )
    path = _write(tmp_path, bad_toml)

    with pytest.raises(ConfigError, match="https") as exc_info:
        _ = load_config(path)

    assert "user:pass" not in str(exc_info.value)


def test_load_config_rejects_claim_token_with_slash(tmp_path: Path) -> None:
    bad_toml = VALID_TOML.replace(
        'claim_token = "s3cret-claim-token"', 'claim_token = "s3cret/claim/token"'
    )
    path = _write(tmp_path, bad_toml)

    with pytest.raises(ConfigError, match="claim_token"):
        _ = load_config(path)


def test_load_config_error_does_not_leak_rejected_claim_token_value(tmp_path: Path) -> None:
    bad_toml = VALID_TOML.replace(
        'claim_token = "s3cret-claim-token"', 'claim_token = "s3cret/claim/token"'
    )
    path = _write(tmp_path, bad_toml)

    with pytest.raises(ConfigError) as exc_info:
        _ = load_config(path)

    assert "s3cret/claim/token" not in str(exc_info.value)


def test_load_config_rejects_empty_claim_token(tmp_path: Path) -> None:
    bad_toml = VALID_TOML.replace('claim_token = "s3cret-claim-token"', 'claim_token = ""')
    path = _write(tmp_path, bad_toml)

    with pytest.raises(ConfigError, match="claim_token"):
        _ = load_config(path)


def test_load_config_accepts_url_safe_claim_token_characters(tmp_path: Path) -> None:
    ok_toml = VALID_TOML.replace(
        'claim_token = "s3cret-claim-token"', 'claim_token = "abc123._~-XYZ"'
    )
    path = _write(tmp_path, ok_toml)

    config = load_config(path)

    assert config.claim_token.get_secret_value() == "abc123._~-XYZ"


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


def test_load_config_rejects_more_than_one_provider(tmp_path: Path) -> None:
    two_providers = (
        VALID_TOML
        + """
[[providers]]
name = "second-bank"
access_url = "https://user:pass@second.example.com/simplefin"
"""
    )
    path = _write(tmp_path, two_providers)

    with pytest.raises(ConfigError):
        _ = load_config(path)


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


def test_access_url_and_claim_token_are_redacted_in_repr(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID_TOML)

    config = load_config(path)

    assert "s3cret-claim-token" not in repr(config)
    assert "user:pass" not in repr(config)
