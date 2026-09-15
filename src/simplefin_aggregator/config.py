"""Configuration model and loading for simplefin-aggregator.

Exposes public types `Config` and `Provider`, in which every field is typed and
never None. These are built from `_ConfigModel` and `_ProviderFileEntry`, which
represent the file as written, where a field may be absent and mean something by
it.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from itertools import permutations
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlsplit

from platformdirs import user_config_dir
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .provider_registry import ProviderEntry, ProviderRegistryError, find_provider, merged_providers
from .state_file import describe_validation_failure, warn_if_permissive
from .url_validation import UrlValidationError, is_loopback_host, parse_root


if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from .url_validation import NormalizedUrl


APP_NAME = "simplefin-aggregator"

# Characters that need no escaping in query parameters.
PREFIX_PATTERN = re.compile(r"[A-Za-z0-9._:-]*")


class ConfigError(Exception):
    """Raised when the config file is missing, malformed, or fails validation."""


class ConfigCheckError(ValueError):
    """A config that parsed but describes something this application cannot run."""


class _ProviderFileEntry(BaseModel):
    """One `[[providers]]` entry exactly as the config file spells it."""

    # A provider key, built-in or from `custom_providers` below. It identifies the
    # provider everywhere: as the store's key, as the provider client dict's
    # key, and in log lines.
    key: str
    # None means the file said nothing, and `_resolve` supplies `f"{key}:"`.
    # "" means the operator asked for the blank catch-all, and is left alone.
    prefix: str | None = None

    @field_validator("prefix")
    @classmethod
    def _validate_prefix(cls, value: str | None) -> str | None:
        """Constrain a written prefix, without quoting the rejected value back."""
        if value is not None and not PREFIX_PATTERN.fullmatch(value):
            msg = f"a provider prefix must match {PREFIX_PATTERN.pattern}"
            raise ValueError(msg)
        return value


@dataclass(frozen=True)
class Provider:
    """A single SimpleFIN provider this aggregator proxies."""

    key: str
    # What this provider's account ids carry when the client app sees them, and
    # what routes an inbound `?account=` back to this provider.
    prefix: str

    def __post_init__(self) -> None:
        if not PREFIX_PATTERN.fullmatch(self.prefix):
            msg = f"a provider prefix must match {PREFIX_PATTERN.pattern}"
            raise ConfigCheckError(msg)


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
    """A provider this config adds to the built-in list. Immutable.

    Editing this entry in the config file is deliberately the only way
    to add one -- see `provider_registry.py` for why there is no flag
    and no prompt.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    key: str
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
        return ProviderEntry(key=self.key, root=_parse_root_or_value_error(self.root))


class _ConfigModel(BaseModel):
    """The config file as written, before any field is resolved."""

    bind_host: str = "127.0.0.1"
    bind_port: int = 8080
    providers: list[_ProviderFileEntry] = Field(min_length=1)
    custom_providers: list[CustomProvider] = []
    base_url: str
    allow_multiple_blank_prefixes: bool = False

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


@dataclass(frozen=True)
class Config:
    """The parsed config file, with every field resolved to what the code uses.

    Frozen, and nothing reloads one, so the checks `__post_init__` runs hold
    for the object's lifetime. No field here takes a default: defaults belong
    on `_ConfigModel`, so that a field `_resolve` forgets fails to compile.
    The reverse -- a field on `_ConfigModel` that `_resolve` drops -- would be
    accepted from the file and silently ignored, so the two field sets are
    pinned equal by a test.
    """

    bind_host: str
    bind_port: int
    providers: tuple[Provider, ...]
    custom_providers: tuple[CustomProvider, ...]
    base_url: str
    allow_multiple_blank_prefixes: bool

    def __post_init__(self) -> None:
        """Run the checks that span providers, so no Config exists that fails them.

        Here and not in `_resolve`, because `Config(...)` is public: a check on
        the parsing path alone would be a rule to remember, not a property.
        """
        _check_provider_keys(self.provider_entries(), (p.key for p in self.providers))
        _check_provider_prefixes(
            self.providers, allow_multiple_blank_prefixes=self.allow_multiple_blank_prefixes
        )

    def provider_entries(self) -> tuple[ProviderEntry, ...]:
        """Every provider a token may be claimed from: the built-in ones plus this config's."""
        return merged_providers(entry.as_provider_entry() for entry in self.custom_providers)


def _check_provider_keys(entries: tuple[ProviderEntry, ...], keys: Iterable[str]) -> None:
    """Fail on a key no provider defines, or on one named twice."""
    seen: set[str] = set()
    for key in keys:
        _ = find_provider(entries, key)  # raises on a key no entry defines
        if key in seen:
            msg = f"provider key {key!r} appears more than once in providers"
            raise ConfigCheckError(msg)
        seen.add(key)


def _check_provider_prefixes(
    providers: tuple[Provider, ...], *, allow_multiple_blank_prefixes: bool
) -> None:
    """Keep the prefix set unambiguous, so an inbound account id has one owner.

    Several blank prefixes, where allowed, are the one exception: an id no
    named prefix claims then belongs to all of them.
    """
    blank = [provider.key for provider in providers if not provider.prefix]
    if len(blank) > 1 and not allow_multiple_blank_prefixes:
        named = ", ".join(repr(key) for key in blank)
        msg = (
            f"at most one provider may have a blank prefix unless "
            f"allow_multiple_blank_prefixes = true; {named} all do"
        )
        raise ConfigCheckError(msg)
    named_prefixes = [provider for provider in providers if provider.prefix]
    for one, other in permutations(named_prefixes, 2):
        if other.prefix.startswith(one.prefix):
            msg = (
                f"provider {one.key!r}'s prefix is a prefix of provider {other.key!r}'s, "
                "so an account id would not say which of them it came from"
            )
            raise ConfigCheckError(msg)


def _resolve(model: _ConfigModel) -> Config:
    """Turn the file as written into the Config the rest of the code uses."""
    entries = merged_providers(entry.as_provider_entry() for entry in model.custom_providers)
    # Before any Provider exists: a bad key derives a bad prefix, and Provider
    # would then report the key's fault against the prefix.
    _check_provider_keys(entries, (entry.key for entry in model.providers))
    return Config(
        bind_host=model.bind_host,
        bind_port=model.bind_port,
        providers=tuple(
            Provider(
                key=entry.key, prefix=f"{entry.key}:" if entry.prefix is None else entry.prefix
            )
            for entry in model.providers
        ),
        custom_providers=tuple(model.custom_providers),
        base_url=model.base_url,
        allow_multiple_blank_prefixes=model.allow_multiple_blank_prefixes,
    )


def config_from_mapping(data: Mapping[str, object]) -> Config:
    """Validate and resolve an untyped mapping, as `load_config` does with a parsed file."""
    return _resolve(_ConfigModel.model_validate(data))


CONFIG_FILENAME = "config.toml"


# The one environment variable that selects the directory. Read in `config_dir`
# alone; every other place that needs the directory calls that, or one of the
# `*_path` functions built on it, rather than reading the variable itself.
DIR_ENV_VAR = "SIMPLEFIN_AGGREGATOR_DIR"


def default_config_dir() -> Path:
    return Path(user_config_dir(APP_NAME))


def config_dir() -> Path:
    """Where this application's on-disk state lives.

    `DIR_ENV_VAR` if set, else the platform default.
    """
    from_env = os.environ.get(DIR_ENV_VAR)
    return Path(from_env) if from_env else default_config_dir()


def config_path(directory: Path | None = None) -> Path:
    """Where config.toml lives: in `directory`, or where `config_dir()` resolves."""
    return (directory if directory is not None else config_dir()) / CONFIG_FILENAME


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
        return config_from_mapping(data)
    except ValidationError as exc:
        # The same rendering the state files use, for the same reason: a
        # provider root can carry userinfo, and pydantic's own rendering would
        # quote it back. That this file is not meant to hold credentials exempts
        # nothing -- a root in it still can.
        msg = f"invalid config in {path}:\n{describe_validation_failure(_ConfigModel, exc)}"
        raise ConfigError(msg) from None
    except (ConfigCheckError, ProviderRegistryError) as exc:
        # Named rather than ValueError, so an unexpected one stays a traceback
        # instead of being reported as the operator's config being wrong.
        msg = f"invalid config in {path}: {exc}"
        raise ConfigError(msg) from None
