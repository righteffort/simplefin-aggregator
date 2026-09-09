"""Shared file handling for everything this application keeps on disk.

This module supports making guarantees common to all files, independent of their
schema and semantics: files are created read-only, updated atomically, and
parsed so that a failure is reported without quoting the rejected
value. `config.toml` is not a state file but uses the permission warning and the
error rendering from here.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import sys
import tempfile
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, ValidationError


if TYPE_CHECKING:
    from collections.abc import Generator

    from pydantic_core import ErrorDetails


def warn_if_permissive(path: Path) -> None:
    """Warn when a file this application owns is readable or writable by others.

    Modification is what matters for all three of them and disclosure only for
    some, so the warning does not say which risk applies to the file in hand;
    `docs/ARCHITECTURE.md` carries that asymmetry.

    Advisory, so a mode it cannot read is passed over rather than turned into
    a failure: the caller has already read the file, and refusing to hand it
    over because the permission check itself failed would deny access on the
    strength of a check that exists only to warn.
    """
    with suppress(OSError):
        mode = path.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            # A directory needs its owner's execute bit, so the two remedies
            # differ; the rule they enforce does not.
            remedy = "chmod 700" if stat.S_ISDIR(mode) else "chmod 600"
            print(
                f"warning: {path} is readable or writable by group/other, and should be {remedy}",
                file=sys.stderr,
            )


class StateFileError(Exception):
    """Raised when a state file cannot be read, written or understood.

    A store also raises it for a file that parsed but does not hold what was
    asked of it, which reaches the user by the same path and needs no second
    type.
    """


def _safe_location(model: type[BaseModel], location: tuple[int | str, ...]) -> str:
    """Name where validation failed, using only what the file did not supply.

    A location interleaves declared field names, list indices and mapping
    keys. A field name comes from the model and an index from the file's shape
    rather than its content, so both are safe to render; a key is a value the
    file chose, and a store naming a credential in key position would
    otherwise print it in an error the CLI shows.

    The limit is that only the top-level model's fields are recognized, so a
    location inside a nested model stops at the index that identifies it --
    `custom_providers.0` rather than `custom_providers.0.root`. Recovering the
    rest means walking the model graph through every typing construct the
    schema uses, which is a great deal of machinery to sit in the path that
    must not leak.
    """
    return ".".join(
        str(part) for part in location if isinstance(part, int) or part in model.model_fields
    )


_OUR_OWN_MESSAGE = frozenset({"value_error", "assertion_error"})
"""The failures whose message came from an exception this project raised.

Every other message is pydantic's own template, and some of those interpolate
the value they rejected, which for these files is a credential. Reading only
the failure's type instead of its message is what makes that impossible rather
than merely unobserved: a type is a fixed slug with nothing of the file in it.
The two below are the project's own messages, governed by the project's own
rules about what a message may name."""


def _describe(error: ErrorDetails) -> str:
    return error["msg"] if error["type"] in _OUR_OWN_MESSAGE else error["type"]


def describe_validation_failure(model: type[BaseModel], exc: ValidationError) -> str:
    """Say where a file failed to validate and how, using nothing the file supplied."""
    return "\n".join(
        f"{_safe_location(model, error['loc'])}: {_describe(error)}"
        for error in exc.errors(include_url=False, include_input=False)
    )


def load_state_file[ModelT: BaseModel](
    path: Path, model: type[ModelT], *, warn: bool = True
) -> ModelT:
    """Read and validate one state file.

    `warn=False` for a read that happens on every request rather than when a
    person runs a command: the permission warning is worth printing once and
    is noise printed per request, and `serve` makes it once at startup.

    A file that is not there yet is the empty store, validated by the same
    path as an empty file on disk rather than short-circuited past it, so that
    a model without defaults fails as a StateFileError like any other bad
    shape.

    The read follows a symlink at `path` where the write does not, so a store
    replaced by a link is read as though the link's target were the store.
    Deliberate: planting that link needs write access to the config directory,
    and that already allows writing the store outright, which is easier and
    worse.
    """
    try:
        # utf-8, not the locale's, on both the read and the write below: JSON
        # is UTF-8 by definition, and a locale change must not be able to make
        # a file this application wrote unreadable.
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raw = "{}"
    except OSError as exc:
        msg = f"cannot read {path}: {exc}"
        raise StateFileError(msg) from exc
    except UnicodeDecodeError:
        # Not the exception text: it quotes the byte it choked on, and these
        # files are credential-adjacent.
        msg = f"{path} is not UTF-8 text"
        raise StateFileError(msg) from None
    else:
        if warn:
            warn_if_permissive(path)

    try:
        data = cast(object, json.loads(raw))
    except json.JSONDecodeError as exc:
        msg = f"malformed JSON in {path}: {exc}"
        raise StateFileError(msg) from exc

    try:
        return model.model_validate(data)
    except ValidationError as exc:
        msg = f"invalid contents in {path}:\n{describe_validation_failure(model, exc)}"
        raise StateFileError(msg) from None


def check_can_save(path: Path) -> None:
    """Create the file's directory, fail if it cannot be written, warn if it is shared.

    Called before writing into the config directory, so that a directory that
    cannot be written is reported while there is still something left to retry
    -- which for `claim` means before a one-time setup token is spent.

    The directory's own mode is worth a warning of its own, and not only its
    files': one another local user can write is the premise of everything the
    write path defends against. It lets them replace a store outright, plant a
    symlink at a path this application will open, or swap the file a lock is
    held on. Those defenses narrow what that buys an attacker; none of them
    makes it safe.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        msg = f"cannot create {path.parent}: {exc}"
        raise StateFileError(msg) from exc

    if not os.access(path.parent, os.W_OK | os.X_OK):
        msg = f"cannot write {path}: {path.parent} is not writable"
        raise StateFileError(msg)

    warn_if_permissive(path.parent)


def _sync_directory(directory: Path) -> None:
    """Persist the rename itself, so that power loss cannot lose a store that was written.

    Best-effort, unlike the fsync before the rename: by this point the new
    file is in place and every reader already sees it, so a failure here is a
    weaker durability guarantee than was asked for -- not a failed save.
    Raising would tell `claim` it had lost a credential it did in fact store,
    which is the more expensive wrong answer. Failing here is ordinary rather
    than exotic: a directory that is writable but not readable takes every
    write the store makes and still refuses this open, and not every
    filesystem implements fsync on a directory at all.
    """
    with suppress(OSError):
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def save_state_file(path: Path, contents: BaseModel) -> None:
    """Replace the file at `path` with `contents`, atomically and owner-only.

    `contents` is written as JSON, so a model holding a `SecretStr` needs a
    `field_serializer` that reveals it: pydantic writes one as asterisks by
    default, and those asterisks read back as a valid store, overwriting a
    credential with nothing. Unchecked here -- a store whose values are
    secrets owns that serializer and the test that pins it.
    """
    serialized = contents.model_dump_json(indent=2)

    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Write-then-rename, so that a crash mid-write cannot leave a reader
        # looking at a truncated file. `mkstemp` creates with 0600, so the
        # persisted file has 0600 even if the prior version did not.
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=path.name, suffix=".tmp"
        )
    except OSError as exc:
        msg = f"cannot write {path}: {exc}"
        raise StateFileError(msg) from exc

    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            _ = handle.write(serialized + "\n")
            # Before the rename, so the rename cannot reach disk ahead of the
            # data blocks and leave an empty file where the store was.
            handle.flush()
            os.fsync(handle.fileno())
        _ = temporary.replace(path)
        _sync_directory(path.parent)
    except OSError as exc:
        # Every way this can fail reaches the caller as one type, as the read
        # side already does. A caller that had to catch OSError separately
        # would be one `except` away from a traceback where it wanted a
        # message.
        msg = f"cannot write {path}: {exc}"
        raise StateFileError(msg) from exc
    finally:
        # A successful replace leaves nothing at the temporary name, so this is
        # a no-op then. On any failure -- including KeyboardInterrupt, which an
        # `except OSError` would miss -- it keeps a partial file holding
        # credentials from lingering in the directory. Suppressed, because
        # whatever stopped the write usually stops the cleanup too, and the
        # caller needs to hear why the save failed rather than why the tidying
        # did.
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


@contextmanager
def _hold_lock(path: Path) -> Generator[None]:
    """Take an exclusive lock on a sidecar of `path`, for the caller's whole update.

    The lock is a sidecar and never the store itself: `save_state_file`
    replaces the store, so a lock held on its inode stops guarding the file
    that ends up at that path the moment the first writer finishes.

    The lock path is as guessable as the temporary one, and the config
    directory may be one another local user can write, so the open refuses to
    follow a link. Following one would let the planter have this create a file
    anywhere its owner can write, and -- by pointing it at a file they already
    hold a lock on -- wedge every writer with no timeout.

    `fcntl` is POSIX-only, as this application already is throughout its mode
    bits and its Dockerfile. A no-op fallback elsewhere would be worse than
    refusing to import, since it would silently drop the guarantee callers
    take from this.
    """
    lock_path = path.with_suffix(".lock")
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        msg = f"cannot open {lock_path}: {exc}"
        raise StateFileError(msg) from exc

    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as exc:
            msg = f"cannot lock {lock_path}: {exc}"
            raise StateFileError(msg) from exc
        yield
    finally:
        # Closing the descriptor releases the lock; the file stays, because
        # unlinking it would let the next writer create a second one and lock
        # that instead.
        os.close(descriptor)


@contextmanager
def update_state_file[ModelT: BaseModel](path: Path, model: type[ModelT]) -> Generator[ModelT]:
    """Read the store, hand it over to be changed, and write it back.

    A store is written whole, so changing one entry means reading the rest
    first, and two writers doing that at once each save a version missing what
    the other did. The lock is held across the read and the write to stop
    that. Readers take no lock and need none: the atomic replace means they
    see one whole version or another, never a torn one.

    The body raising leaves the file untouched, which is what an update that
    turns out to have nothing to do -- a duplicate key, a setup token matching
    no record -- wants.

    Not reentrant, and the lock has no timeout: a second update on the same
    path from inside the body blocks on the first forever, silently. Compose
    two changes by making them in one block, never by nesting.
    """
    with _hold_lock(path):
        stored = load_state_file(path, model)
        yield stored
        save_state_file(path, stored)
