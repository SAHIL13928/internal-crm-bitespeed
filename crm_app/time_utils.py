"""Time helpers — kept in one place to avoid `datetime.utcnow()`
deprecation warnings spread across the codebase, and to make the
"naive UTC" convention explicit.

Convention: every DB column storing a datetime is naive UTC. The rest
of the system assumes that. These helpers preserve it.
"""
from datetime import datetime, timezone


def utcnow_naive() -> datetime:
    """`datetime.now(timezone.utc).replace(tzinfo=None)` — the only
    correct replacement for the deprecated `datetime.utcnow()` that
    keeps our naive-UTC storage convention intact."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
