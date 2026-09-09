"""Tests for the shared state-file helper's error reporting.

A store file holds this application's credentials, so a parse failure must
describe the shape that was wrong without quoting any of it back. The corpus
below is indexed by the way the parse can fail rather than by where in a file
the value sat, because it is the failure shape that decides which of pydantic's
messages gets built and therefore which ones can carry the value into it.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal

import pytest
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from simplefin_aggregator.state_file import StateFileError, load_state_file


if TYPE_CHECKING:
    from pathlib import Path


# Short, so that a message eliding a long value's middle cannot hide it and
# make one of these pass for the wrong reason.
MARKER = "s3cret-marker"


class _Tagged(BaseModel):
    kind: Literal["tagged"] = "tagged"
    n: int = 0


class _OtherTagged(BaseModel):
    kind: Literal["other"] = "other"


class _Nested(BaseModel):
    when: AwareDatetime = datetime.fromisoformat("2020-01-01T00:00:00Z")
    hexish: Annotated[str, Field(pattern=r"^[0-9a-f]{4}$")] = "abcd"


class _Corpus(BaseModel):
    """Every field shape this application's state files are built from."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    tagged: Annotated[_Tagged | _OtherTagged, Field(discriminator="kind")] = _Tagged()
    mapping: dict[str, int] = {}
    items: list[int] = []
    nested: _Nested = _Nested()
    literal: Literal["one", "two"] = "one"
    number: int = 0
    checked: str = "ok"

    @field_validator("checked")
    @classmethod
    def _reject_everything(cls, _value: str) -> str:
        msg = "a reason this project wrote, naming the rule and not the value"
        raise ValueError(msg)


MISPARSES = {
    "the tag of a discriminated union": {"tagged": {"kind": MARKER}},
    "a mapping key": {"mapping": {MARKER: ["wrong shape"]}},
    "a mapping value": {"mapping": {"key": MARKER}},
    "a list element": {"items": [MARKER]},
    "a field of a nested model": {"nested": {"hexish": MARKER}},
    "a nested timestamp": {"nested": {"when": MARKER}},
    "a value outside a literal's set": {"literal": MARKER},
    "a scalar of the wrong type": {"number": MARKER},
    "a whole field of the wrong type": {"mapping": MARKER},
    "the name of a field the schema does not declare": {MARKER: 1},
}


def test_a_message_this_project_wrote_is_rendered(tmp_path: Path) -> None:
    """Pins the one exemption, and states its limit.

    A failure raised by one of this project's own validators keeps its
    message, because that is where the reason a value is wrong actually lives.
    What such a message may name is then governed by the project's own rules
    about credentials rather than by anything here -- which is the limit: this
    corpus covers the messages the schema library writes, not the ones we do.
    """
    path = tmp_path / "corpus.json"
    _ = path.write_text(json.dumps({"checked": MARKER}))

    with pytest.raises(StateFileError) as excinfo:
        _ = load_state_file(path, _Corpus)

    message = str(excinfo.value)
    assert "naming the rule and not the value" in message
    assert MARKER not in message


@pytest.mark.parametrize("misparse", MISPARSES.values(), ids=list(MISPARSES))
def test_a_rejected_value_is_never_quoted_back(tmp_path: Path, misparse: dict[str, object]) -> None:
    """However the parse fails, the value that failed it stays out of the message.

    Each entry puts the marker in the position that a different one of
    pydantic's messages is built from. A message that names the constraint is
    safe; one that interpolates what it read is not, and which of the two you
    get depends only on the shape of the failure.
    """
    path = tmp_path / "corpus.json"
    _ = path.write_text(json.dumps(misparse))

    with pytest.raises(StateFileError) as excinfo:
        _ = load_state_file(path, _Corpus)

    assert MARKER not in str(excinfo.value)


def test_a_rejection_still_says_where_and_what(tmp_path: Path) -> None:
    """Redaction must not reduce every failure to the same unusable sentence.

    What survives is where the failure was and what kind it was, which is what
    sends a reader to the right line of the file.
    """
    path = tmp_path / "corpus.json"
    _ = path.write_text(json.dumps({"mapping": {"key": "not a number"}}))

    with pytest.raises(StateFileError) as excinfo:
        _ = load_state_file(path, _Corpus)

    message = str(excinfo.value)
    assert "mapping" in message
    # The failure's kind, not pydantic's sentence about it: the sentence is
    # where a rejected value can ride along, so only the kind is rendered.
    assert "int_parsing" in message
