"""
Resolves config.yaml's `timezone` field for *display* purposes.

All scheduling in this project (timings_utc, run_eclipse.py's sleep-until
logic, the system clock you sync at your site) deliberately stays in naive
UTC throughout — that's robust and has no DST edge cases. `timezone` only
controls what local-time string gets echoed alongside UTC in timings.py's
output and run_eclipse.py's log lines, so you can sanity-check against
EclipseWise (often shown in local time) and follow along in your own head
without doing UTC arithmetic during totality.
"""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger("eclipse.tzutil")


def resolve_tz(tz_name: str | None) -> ZoneInfo | None:
    """Returns a ZoneInfo for tz_name, or None if tz_name is falsy or
    unrecognized (a warning is logged in the latter case rather than
    raising — a bad timezone string shouldn't block the eclipse)."""
    if not tz_name:
        return None
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        log.warning("Unrecognized timezone %r in config.yaml — showing UTC only", tz_name)
        return None