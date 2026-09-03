"""Redact a path segment from uvicorn's own access log.

Uvicorn logs every request's raw path via its own access logger, independent
of anything an app logs itself. A route whose path embeds a credential (a
claim token in this project's case, but this module has no idea) would
otherwise have that credential printed straight to stdout on every request,
regardless of any logging discipline the app itself follows.

This module knows nothing about SimpleFIN, claim tokens, or routes -- it's a
generic "redact matching (method, path prefix) pairs in an access log"
utility. The caller supplies what to redact.
"""

from __future__ import annotations

import logging
from typing import override


# uvicorn's access log record args are (client_addr, method, path, http_version,
# status) -- need at least the first three to find and redact the path.
_MIN_ACCESS_LOG_ARGS = 3


class _RedactPathFilter(logging.Filter):
    def __init__(self, method: str, path_prefix: str, replacement: str) -> None:
        super().__init__()
        self.method: str = method
        self.path_prefix: str = path_prefix
        self.replacement: str = replacement

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= _MIN_ACCESS_LOG_ARGS:
            method, path = args[1], args[2]
            matches = (
                method == self.method
                and isinstance(path, str)
                and path.startswith(self.path_prefix)
            )
            if matches:
                record.args = (args[0], args[1], self.replacement, *args[3:])
        return True


def install_access_log_redaction(
    *, logger_name: str, method: str, path_prefix: str, replacement: str
) -> None:
    """Redact `replacement` in place of any `path_prefix`-starting path logged for `method`.

    Idempotent per (logger_name, method, path_prefix): calling this again with
    the same three doesn't add a second filter.
    """
    logger = logging.getLogger(logger_name)
    already_installed = any(
        isinstance(existing, _RedactPathFilter)
        and existing.method == method
        and existing.path_prefix == path_prefix
        for existing in logger.filters
    )
    if not already_installed:
        logger.addFilter(_RedactPathFilter(method, path_prefix, replacement))
