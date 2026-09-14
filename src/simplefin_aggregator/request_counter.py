"""Per-provider daily request counts, for observability only — never used as a control."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime


class RequestCounter:
    """Counts requests issued to each provider, resetting at UTC midnight."""

    def __init__(self) -> None:
        self._today: str = datetime.now(UTC).date().isoformat()
        self._counts: defaultdict[str, int] = defaultdict(int)

    def record(self, provider_name: str) -> int:
        """Record one request to a provider and return the running count for today."""
        today = datetime.now(UTC).date().isoformat()
        if today != self._today:
            self._today = today
            self._counts.clear()
        self._counts[provider_name] += 1
        return self._counts[provider_name]
