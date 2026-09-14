"""Tests for the CLI's top-level help text."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from simplefin_aggregator import cli
from simplefin_aggregator.config import DIR_ENV_VAR, default_config_dir
from simplefin_aggregator.provider_registry import KEY_PATTERN


runner = CliRunner()


def test_help_mentions_the_dir_environment_variable_and_default() -> None:
    """Top-level `--help` names `SIMPLEFIN_AGGREGATOR_DIR` and the default directory."""
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert DIR_ENV_VAR in result.output
    assert str(default_config_dir()) in result.output


@pytest.mark.parametrize("args", [["app", "new", "--help"], ["claim", "--help"]])
def test_key_option_help_states_the_pattern_keys_must_match(args: list[str]) -> None:
    """A command that takes a key states the pattern it must match, in full."""
    result = runner.invoke(cli.app, args)

    assert result.exit_code == 0
    assert KEY_PATTERN.pattern in result.output
