"""Configuration model and loading for simplefin-aggregator."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from platformdirs import user_config_dir
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .provider_registry import ProviderEntry, find_provider, merged_providers
from .state_file import describe_validation_failure, warn_if_permissive
from .url_validation import UrlValidationError, is_loopback_host, parse_root


if TYPE_CHECKING:
    from .url_validation import NormalizedUrl


APP_NAME = "simplefin-aggregator"


class ConfigError(Exception):
    """Raised when the config file is missing, malformed, or fails validation."""


class Provider(BaseModel):
    """A single SimpleFIN provider this aggregator proxies."""

    # A provider key, built-in or from `custom_providers` below. It identifies the
    # provider everywhere: as the store's key, as the provider client dict's
    # key, and in log lines.
    key: str


def _parse_root_or_value_error(raw: str) -> NormalizedUrl:
    """`parse_root`, reporting failure the way pydantic can render it.

    UrlValidationError is not a ValueError, so pydantic would let it escape as
    a traceback rather than reporting it as a config error. Its message is
    built for display and carries no credentials.
    """
    try:
        return parse_root(raw)
    except UrlValidationError as exc:
        raise ValueError(str(exc)) from None


class CustomProvider(BaseModel):
    """A provider this config adds to the built-in list.

    Editing this entry in the config file is deliberately the only way
    to add one -- see `provider_registry.py` for why there is no flag
    and no prompt.
    """

    key: str
    label: str
    root: str

    @field_validator("root")
    @classmethod
    def _validate_root(cls, value: str) -> str:
        """Reject a bad root as a fault of this field, not of the config as a whole.

        Left to the model-level check below, a bad root is reported against the
        whole `Config`, so the message names no entry and pydantic's own
        rendering of the rejected input is the entire config file.
        """
        _ = _parse_root_or_value_error(value)
        return value

    def as_provider_entry(self) -> ProviderEntry:
        """Convert to a registry entry, validating the key and the root."""
        return ProviderEntry(
            key=self.key, label=self.label, root=_parse_root_or_value_error(self.root)
        )


class Config(BaseModel):
    """The parsed config file.

    Treated as read-only once `load_config` returns: nothing mutates a Config
    and nothing reloads one, so validators here may establish invariants --
    see _check_provider_keys -- that hold for the object's whole lifetime.
    """

    bind_host: str = "127.0.0.1"
    bind_port: int = 8080
    # Schema and internal types are already a list for the multi-provider version to come;
    # this version only supports exactly one.
    providers: list[Provider] = Field(min_length=1, max_length=1)
    custom_providers: list[CustomProvider] = []
    base_url: str

    def provider_entries(self) -> tuple[ProviderEntry, ...]:
        """Every provider a token may be claimed from: the built-in ones plus this config's."""
        return merged_providers(entry.as_provider_entry() for entry in self.custom_providers)

    @model_validator(mode="after")
    def _check_provider_keys(self) -> Config:
        """Fail at load time on a key no provider defines.

        Checked here rather than at first use so that a dangling reference is
        reported by every command, not just the one that would dereference it.
        """
        entries = self.provider_entries()
        for provider in self.providers:
            _ = find_provider(entries, provider.key)
        return self

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        if not value.isascii():
            # A setup token carries this URL as ASCII, so an internationalized
            # host has to arrive already encoded or the token cannot be built
            # at all -- which would otherwise be discovered after `app new` had
            # written a record for an app it could not hand a token to.
            msg = "base_url must be ASCII; give an internationalized host in its encoded form"
            raise ValueError(msg)
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https"):
            msg = "base_url must be http or https"
            raise ValueError(msg)
        if parsed.hostname is None:
            msg = "base_url must include a host"
            raise ValueError(msg)
        if parsed.username is not None or parsed.password is not None:
            msg = "base_url must not include user-info"
            raise ValueError(msg)
        # Plaintext only where the traffic cannot leave the machine. See
        # is_loopback_host for why `localhost` does not qualify.
        if parsed.scheme == "http" and not is_loopback_host(parsed.hostname):
            msg = "base_url must use https unless the host is a literal loopback IP address"
            raise ValueError(msg)
        return value


CONFIG_FILENAME = "config.toml"


def default_config_dir() -> Path:
    return Path(user_config_dir(APP_NAME))


def config_path(config_dir: Path | None = None) -> Path:
    """Where config.toml lives: in `config_dir`, or the platform default."""
    directory = config_dir if config_dir is not None else default_config_dir()
    return directory / CONFIG_FILENAME


def load_config(path: Path) -> Config:
    try:
        # utf-8, not the locale's: TOML is UTF-8 by definition. The -sig
        # variant also drops a BOM, which an editor may have written.
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        msg = f"cannot read config file {path}: {exc}"
        raise ConfigError(msg) from exc
    except UnicodeDecodeError:
        # Not the exception text: it quotes the byte it choked on.
        msg = f"config file {path} is not UTF-8 text"
        raise ConfigError(msg) from None

    warn_if_permissive(path)

    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        msg = f"malformed TOML in {path}: {exc}"
        raise ConfigError(msg) from exc

    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        # The same rendering the state files use, for the same reason: a
        # provider root can carry userinfo, and pydantic's own rendering would
        # quote it back. Nothing here is exempt because this file no longer
        # holds credentials -- a root in it still does.
        msg = f"invalid config in {path}:\n{describe_validation_failure(Config, exc)}"
        raise ConfigError(msg) from None
