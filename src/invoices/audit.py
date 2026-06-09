"""Append-only audit log for retrieval and delivery actions.

Each entry is one JSON line in ``<output dir>/audit.log``, so there is a local
record of what was fetched and what was drafted, for the company's own trust and
review. Nothing is sent anywhere.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from .config import BASE_DIR

_LOG_PATH = BASE_DIR / 'audit.log'


def log_event(action: str, details: dict | None = None) -> None:
    """Append one audit entry (best-effort; never raises)."""
    try:
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            'time': datetime.now(UTC).isoformat(),
            'action': action,
            **(details or {}),
        }
        with _LOG_PATH.open('a') as f:
            # default=str so a stray non-serializable value degrades instead of
            # raising; the broad-but-specific except keeps auditing best-effort.
            f.write(json.dumps(entry, default=str) + '\n')
    except (OSError, TypeError, ValueError):
        # Auditing must never break the actual operation.
        pass


def read_audit_log(limit: int | None = None) -> list[dict]:
    """Return audit entries (most recent last); ``limit`` keeps the last N."""
    if not _LOG_PATH.exists():
        return []
    lines = _LOG_PATH.read_text().splitlines()
    if limit is not None:
        lines = lines[-limit:] if limit > 0 else []
    entries: list[dict] = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries
