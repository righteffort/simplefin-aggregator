"""Configuration model and loading for simplefin-aggregator."""

import stat
import sys
import tomllib
from pathlib import Path
from urllib.parse import SplitResult, urlsplit

from platformdirs import user_config_dir
from pydantic import BaseModel, Field, SecretStr, ValidationError, field_validator


APP_NAME = "simplefin-aggregator"


class ConfigError(Exception):
    """Raised when the config file is missing, malformed, or fails validation."""


class Provider(BaseModel):
    """A single SimpleFIN provider this aggregator proxies."""

    name: str
    access_url: SecretStr

    @field_validator("access_url")
    @classmethod
    def _validate_access_url(cls, value: SecretStr) -> SecretStr:
        parsed = urlsplit(value.get_secret_value())
        if parsed.scheme != "https":
            msg = "access_url must use https"
            raise ValueError(msg)
        if parsed.hostname is None:
            msg = "access_url must include a host"
            raise ValueError(msg)
        if parsed.username is None or parsed.password is None:
            msg = "access_url must embed basic auth credentials"
            raise ValueError(msg)
        return value

    def parsed_access_url(self) -> SplitResult:
        """Parse the access URL on demand; never cached in a logged field."""
        return urlsplit(self.access_url.get_secret_value())


class ConsumerAuth(BaseModel):
    """The basic-auth credentials this aggregator requires from its consumer."""

    username: str
    password: SecretStr


class Config(BaseModel):
    bind_host: str = "127.0.0.1"
    bind_port: int = 8080
    providers: list[Provider] = Field(min_length=1)
    consumer: ConsumerAuth
    claim_token: SecretStr
    base_url: str

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https"):
            msg = "base_url must be http or https"
            raise ValueError(msg)
        if parsed.hostname is None:
            msg = "base_url must include a host"
            raise ValueError(msg)
        return value


def default_config_path() -> Path:
    return Path(user_config_dir(APP_NAME)) / "config.toml"


def _warn_if_permissive(path: Path) -> None:
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        print(
            (
                f"warning: {path} is readable or writable by group/other; "
                "it contains credentials and should be chmod 600"
            ),
            file=sys.stderr,
        )


def load_config(path: Path) -> Config:
    try:
        raw = path.read_text()
    except OSError as exc:
        msg = f"cannot read config file {path}: {exc}"
        raise ConfigError(msg) from exc

    _warn_if_permissive(path)

    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        msg = f"malformed TOML in {path}: {exc}"
        raise ConfigError(msg) from exc

    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        msg = f"invalid config in {path}:\n{exc}"
        raise ConfigError(msg) from exc
