"""Per-provider daily request counts, for observability only — never used as a control."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime


class RequestCounter:
    """Counts requests issued to each provider, resetting implicitly at UTC midnight."""

    def __init__(self) -> None:
        self._counts: defaultdict[tuple[str, str], int] = defaultdict(int)

    def record(self, provider_name: str) -> int:
        """Record one request to a provider and return the running count for today."""
        today = datetime.now(UTC).date().isoformat()
        key = (provider_name, today)
        self._counts[key] += 1
        return self._counts[key]
