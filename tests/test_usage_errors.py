# SPDX-License-Identifier: GPL-3.0-only

"""Tests for how a command line the CLI cannot parse is reported."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import pytest
import typer
from typer.main import get_command
from typer.testing import CliRunner

from simplefin_aggregator import cli
from simplefin_aggregator.cli import _UsageErrorsWithoutInput  # pyright: ignore[reportPrivateUsage]


if TYPE_CHECKING:
    from typer._click.exceptions import UsageError
    from typer.testing import Result

MARKER = "s3cretmarker"
USAGE_ERROR = 2

runner = CliRunner()

dummy = typer.Typer(cls=_UsageErrorsWithoutInput, add_completion=False, no_args_is_help=True)
group = typer.Typer(no_args_is_help=True)
dummy.add_typer(group, name="group")


@dummy.command()
def bare() -> None:
    pass


@dummy.command()
def flagged(*, flag: Annotated[bool, typer.Option("--flag")] = False) -> None:
    _ = flag


@dummy.command()
def optional(name: Annotated[str | None, typer.Argument()] = None) -> None:
    _ = name


@group.command()
def required(key: Annotated[str, typer.Argument()]) -> None:
    _ = key


def _run(*arguments: str) -> Result:
    # Wide enough that no message wraps inside the error box.
    return runner.invoke(dummy, list(arguments), prog_name="dummy", env={"COLUMNS": "200"})


MISPARSES = {
    "extra argument, command takes none": (("bare", MARKER), "dummy bare takes no arguments."),
    "extra argument after an optional one": (
        ("optional", "name", MARKER),
        "dummy optional takes 1 argument: name.",
    ),
    "extra argument after a required one, nested": (
        ("group", "required", "key", MARKER),
        "dummy group required takes 1 argument: key.",
    ),
    "unknown command": (
        (MARKER,),
        "dummy expects one of these commands: bare, flagged, optional, group.",
    ),
    "unknown command, nested": (
        ("group", MARKER),
        "dummy group expects one of these commands: required.",
    ),
    "unknown option, root": ((f"--{MARKER}",), "dummy takes no options."),
    "unknown option, command takes none": (("bare", f"--{MARKER}"), "dummy bare takes no options."),
    "unknown option, command takes some": (
        ("flagged", f"--{MARKER}"),
        "dummy flagged has no such option; it takes --flag.",
    ),
}


@pytest.fixture
def rejected(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The text of each usage error as raised, before it is replaced."""
    messages: list[str] = []
    original = cli._without_input  # pyright: ignore[reportPrivateUsage]

    def recording(exc: UsageError) -> UsageError:
        messages.append(exc.format_message())
        return original(exc)

    monkeypatch.setattr(cli, "_without_input", recording)
    return messages


@pytest.mark.parametrize(("arguments", "message"), MISPARSES.values(), ids=list(MISPARSES))
def test_a_rejected_command_line_is_described_without_quoting_it(
    arguments: tuple[str, ...], message: str, rejected: list[str]
) -> None:
    """Requirement: a usage error names what the command accepts, never a token it was given."""
    result = _run(*arguments)

    assert any(MARKER in text for text in rejected)
    assert result.exit_code == USAGE_ERROR
    assert message in result.output
    assert MARKER not in result.output


def test_a_missing_argument_is_named() -> None:
    """Requirement: an error that quotes nothing typed keeps saying what is missing."""
    result = _run("group", "required")

    assert result.exit_code == USAGE_ERROR
    assert "'key'" in result.output
    assert "takes 1 argument" not in result.output


def test_a_group_given_nothing_shows_its_help() -> None:
    """Requirement: a group's help is not replaced by an error."""
    result = _run("group")

    assert "required" in result.output
    assert "expects one of these commands" not in result.output


def test_the_cli_reports_usage_errors_this_way() -> None:
    """Requirement: the real CLI is built on the class the tests above exercise."""
    assert isinstance(get_command(cli.app), _UsageErrorsWithoutInput)
