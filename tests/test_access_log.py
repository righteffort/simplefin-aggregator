from __future__ import annotations

import logging

from simplefin_aggregator.access_log import (
    _RedactPathFilter,  # pyright: ignore[reportPrivateUsage] -- testing the filter directly
    install_access_log_redaction,
)


ACCESS_LOG_MSG = '%s - "%s %s HTTP/%s" %d'
LOGGER_NAME = "test.access_log"


def _make_record(method: str, path: str, *, logger_name: str = LOGGER_NAME) -> logging.LogRecord:
    return logging.LogRecord(
        name=logger_name,
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg=ACCESS_LOG_MSG,
        args=("127.0.0.1:12345", method, path, "1.1", 200),
        exc_info=None,
    )


def test_redacts_a_matching_path() -> None:
    record = _make_record("POST", "/widgets/secret-widget-id")
    redact = _RedactPathFilter("POST", "/widgets/", "/widgets/[REDACTED]")

    kept = redact.filter(record)

    assert kept is True
    message = record.getMessage()
    assert "secret-widget-id" not in message
    assert message == '127.0.0.1:12345 - "POST /widgets/[REDACTED] HTTP/1.1" 200'


def test_does_not_redact_a_different_method() -> None:
    record = _make_record("GET", "/widgets/secret-widget-id")
    redact = _RedactPathFilter("POST", "/widgets/", "/widgets/[REDACTED]")

    _ = redact.filter(record)

    assert "secret-widget-id" in record.getMessage()


def test_does_not_redact_a_different_path_prefix() -> None:
    record = _make_record("POST", "/gadgets/secret-gadget-id")
    redact = _RedactPathFilter("POST", "/widgets/", "/widgets/[REDACTED]")

    _ = redact.filter(record)

    assert "secret-gadget-id" in record.getMessage()


def test_install_access_log_redaction_is_idempotent() -> None:
    logger = logging.getLogger(LOGGER_NAME)
    assert logger.filters == []

    try:
        install_access_log_redaction(
            logger_name=LOGGER_NAME, method="POST", path_prefix="/widgets/", replacement="[X]"
        )
        install_access_log_redaction(
            logger_name=LOGGER_NAME, method="POST", path_prefix="/widgets/", replacement="[X]"
        )

        installed = [f for f in logger.filters if isinstance(f, _RedactPathFilter)]
        assert len(installed) == 1
    finally:
        for f in list(logger.filters):
            logger.removeFilter(f)


def test_install_access_log_redaction_allows_distinct_redactions() -> None:
    logger = logging.getLogger(LOGGER_NAME)
    assert logger.filters == []

    try:
        install_access_log_redaction(
            logger_name=LOGGER_NAME, method="POST", path_prefix="/widgets/", replacement="[X]"
        )
        install_access_log_redaction(
            logger_name=LOGGER_NAME, method="POST", path_prefix="/gadgets/", replacement="[Y]"
        )

        installed = [f for f in logger.filters if isinstance(f, _RedactPathFilter)]
        assert len(installed) == 2
    finally:
        for f in list(logger.filters):
            logger.removeFilter(f)
