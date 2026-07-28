"""
The scheduler. Sleeps until each contact event (read from config.yaml's
timings_utc block — see timings.py) and fires the matching bracket
sequence. Deliberately simple: no dependency on anything but the system
clock, which you should sync (NTP or GPS) at your site on eclipse morning.

Usage:
    uv run eclipse-run                # real camera
    uv run eclipse-run --dry-run      # rehearse timing/sequencing, no camera needed
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time
from pathlib import Path

import yaml

from . import bracket_plans as bp
from .camera import connect, run_sequence
from .tzutil import resolve_tz

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"

# How long before C2 (and after C3) the deep_crescent_bracket takes over
# from the general partial_phase_bracket — see bracket_plans.py.
DEEP_CRESCENT_LEAD = dt.timedelta(minutes=3)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("eclipse_run.log")],
)
log = logging.getLogger("eclipse.run")


def to_dt(date_str: str, hms: str) -> dt.datetime:
    return dt.datetime.strptime(f"{date_str} {hms}", "%Y-%m-%d %H:%M:%S")


def _local_suffix(utc_naive: dt.datetime, tz) -> str:
    """" / 18:23:47 MDT local" if tz is resolved, else "" — for log lines."""
    if tz is None:
        return ""
    local = utc_naive.replace(tzinfo=dt.timezone.utc).astimezone(tz)
    return f" / {local.strftime('%H:%M:%S %Z')} local"


def build_schedule(cfg: dict):
    """Returns (schedule, fps) where schedule is a list of
    (start_time, end_time, label, plan) tuples in chronological order."""
    t = cfg["timings_utc"]
    date_str = cfg["date"]
    c1, c2, c3, c4 = (to_dt(date_str, t[k]) for k in ("C1", "C2", "C3", "C4"))
    fps = cfg.get("camera", {}).get("measured_max_fps")

    totality_seconds = (c3 - c2).total_seconds()
    totality_plan = (
        bp.trim_to_fit(bp.totality_bracket, totality_seconds, fps) if fps else bp.totality_bracket
    )

    diamond_in_start = c2 - dt.timedelta(seconds=8)
    diamond_out_start = c3 - dt.timedelta(seconds=8)
    diamond_out_end = c3 + dt.timedelta(seconds=8)

    # Clamp so short/compressed schedules (or a very short partial phase)
    # never push these before C1 or past C4.
    deep_crescent_pre_start = max(c1, diamond_in_start - DEEP_CRESCENT_LEAD)
    deep_crescent_post_end = min(c4, diamond_out_end + DEEP_CRESCENT_LEAD)

    schedule = [
        (c1, deep_crescent_pre_start, "partial_pre_totality", bp.partial_phase_bracket),
        (deep_crescent_pre_start, diamond_in_start, "deep_crescent_pre_totality", bp.deep_crescent_bracket),
        (diamond_in_start, c2, "diamond_ring_in", bp.diamond_ring_burst),
        (c2, c3, "totality", totality_plan),
        (diamond_out_start, diamond_out_end, "diamond_ring_out", bp.diamond_ring_burst),
        (diamond_out_end, deep_crescent_post_end, "deep_crescent_post_totality", bp.deep_crescent_bracket),
        (deep_crescent_post_end, c4, "partial_post_totality", bp.partial_phase_bracket),
    ]
    return schedule, fps


def run(cfg: dict, dry_run: bool) -> None:
    schedule, fps = build_schedule(cfg)
    tz = resolve_tz(cfg.get("timezone"))
    camera = connect(cfg.get("camera", {}).get("port"), dry_run=dry_run)

    for start_time, end_time, label, plan in schedule:
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        wait = (start_time - now).total_seconds()
        suffix = _local_suffix(start_time, tz)
        if wait > 0:
            log.info("Waiting %.0fs for %s (%s UTC%s)...", wait, label, start_time.strftime("%H:%M:%S"), suffix)
            time.sleep(wait)
        elif wait < -5:
            log.warning(
                "%s start time already %.0fs in the past — running immediately",
                label,
                -wait,
            )

        log.info("Firing %s%s", label, suffix)
        try:
            run_sequence(camera, plan, end_time=end_time, fps=fps)
        except Exception:
            log.exception("Error during %s — continuing to next event", label)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="simulate the camera; use this to rehearse timing before the real day",
    )
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    if "timings_utc" not in cfg:
        raise SystemExit(
            "config.yaml has no timings_utc block yet. Run `uv run eclipse-timings` "
            "(and cross-check EclipseWise/USNO) first, then hard-code the confirmed "
            "times with --write — see README."
        )

    run(cfg, dry_run=args.dry_run)


if __name__ == "__main__":
    main()