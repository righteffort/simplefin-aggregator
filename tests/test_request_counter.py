# ruff: noqa: PLR2004

from datetime import UTC, datetime
from typing import override

import pytest

from simplefin_aggregator import request_counter as request_counter_module
from simplefin_aggregator.request_counter import RequestCounter


def test_request_counter_increments_per_provider() -> None:
    counter = RequestCounter()

    assert counter.record("bank-a") == 1
    assert counter.record("bank-a") == 2
    assert counter.record("bank-b") == 1
    assert counter.record("bank-a") == 3


def test_request_counter_resets_at_midnight(monkeypatch: pytest.MonkeyPatch) -> None:
    today = datetime(2024, 1, 1, 23, 0, tzinfo=UTC)

    class _FixedDatetime(datetime):
        @classmethod
        @override
        def now(cls, tz: object = None) -> datetime:
            return today

    monkeypatch.setattr(request_counter_module, "datetime", _FixedDatetime)
    counter = RequestCounter()
    assert counter.record("bank-a") == 1
    assert counter.record("bank-a") == 2

    today = datetime(2024, 1, 2, 0, 30, tzinfo=UTC)

    assert counter.record("bank-a") == 1
    assert len(counter._counts) == 1  # pyright: ignore[reportPrivateUsage] -- testing the reset
