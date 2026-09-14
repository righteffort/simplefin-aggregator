"""Tests for `--config-dir` as a global option on the `simplefin-aggregator` command."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from simplefin_aggregator import app_tokens, cli
from simplefin_aggregator.provider_registry import KEY_PATTERN

from .support import make_unclaimed_app


if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_platform_default_config_dir(  # pyright: ignore[reportUnusedFunction]  # autouse: pytest calls it, no test names it
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Redirect the platform-default config directory into `tmp_path`.

    Every test here passes `--config-dir` or the environment variable
    deliberately; this only catches what happens when a test (or a mutation
    of the code under test) falls through to the default instead, so that
    fallback lands somewhere disposable rather than the developer's real
    `~/.config/simplefin-aggregator`.
    """
    monkeypatch.setattr(app_tokens, "default_config_dir", lambda: tmp_path / "unused-default")


def _env_without_config_dir() -> dict[str, str | None]:
    """The environment `CliRunner` should see when a test asserts no env var is in play.

    `CliRunner.invoke`'s `env` only overrides the keys it names: a `None`
    value deletes that key from the process environment for the duration of
    the call, but a key simply absent from the mapping leaves an ambient
    value in `os.environ` untouched. Filtering the variable out of a copy of
    `os.environ` does not delete it.
    """
    return {**os.environ, "SIMPLEFIN_AGGREGATOR_CONFIG_DIR": None}


def test_config_dir_flag_selects_the_directory(tmp_path: Path) -> None:
    """`--config-dir DIR` before the subcommand picks the store it reads."""
    _ = make_unclaimed_app(tmp_path, key="flag-app")

    # No SIMPLEFIN_AGGREGATOR_CONFIG_DIR in the environment: this is the flag
    # alone doing the work, not a variable the developer happens to have set.
    result = runner.invoke(
        cli.app, ["--config-dir", str(tmp_path), "app", "list"], env=_env_without_config_dir()
    )

    assert result.exit_code == 0
    assert "flag-app" in result.stdout


def test_config_dir_env_var_selects_the_directory(tmp_path: Path) -> None:
    """SIMPLEFIN_AGGREGATOR_CONFIG_DIR is used when `--config-dir` is not given."""
    _ = make_unclaimed_app(tmp_path, key="env-app")
    env = dict(os.environ, SIMPLEFIN_AGGREGATOR_CONFIG_DIR=str(tmp_path))

    result = runner.invoke(cli.app, ["app", "list"], env=env)

    assert result.exit_code == 0
    assert "env-app" in result.stdout


def test_config_dir_flag_wins_over_env_var(tmp_path: Path) -> None:
    """The `--config-dir` flag is used over the environment variable when both are given."""
    flag_dir = tmp_path / "flag"
    flag_dir.mkdir()
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    _ = make_unclaimed_app(flag_dir, key="flag-app")
    _ = make_unclaimed_app(env_dir, key="env-app")
    env = dict(os.environ, SIMPLEFIN_AGGREGATOR_CONFIG_DIR=str(env_dir))

    result = runner.invoke(cli.app, ["--config-dir", str(flag_dir), "app", "list"], env=env)

    assert result.exit_code == 0
    assert "flag-app" in result.stdout
    assert "env-app" not in result.stdout


def test_config_dir_after_the_subcommand_is_rejected(tmp_path: Path) -> None:
    """`--config-dir` is a global option, not accepted after a subcommand.

    Pins a deliberate decision, not a gap: the brief for this option rules out
    accepting it in the old per-subcommand position as well as the new one.
    """
    result = runner.invoke(cli.app, ["app", "list", "--config-dir", str(tmp_path)])

    assert result.exit_code != 0
    assert "No such option" in result.output


def test_help_mentions_the_config_dir_environment_variable() -> None:
    """Top-level `--help` documents SIMPLEFIN_AGGREGATOR_CONFIG_DIR."""
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "SIMPLEFIN_AGGREGATOR_CONFIG_DIR" in result.output


@pytest.mark.parametrize("args", [["app", "new", "--help"], ["claim", "--help"]])
def test_key_option_help_states_the_pattern_keys_must_match(args: list[str]) -> None:
    """A command that takes a key states the pattern it must match, in full."""
    result = runner.invoke(cli.app, args)

    assert result.exit_code == 0
    assert KEY_PATTERN.pattern in result.output
