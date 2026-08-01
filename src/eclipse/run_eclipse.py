"""
The scheduler. Sleeps until each contact event (read from config.yaml's
timings_utc block — see timings.py) and fires the matching bracket
sequence. Deliberately simple: no dependency on anything but the system
clock, which you should sync (NTP or GPS) at your site on eclipse morning.

Usage:
    uv run eclipse-run                 # real camera
    uv run eclipse-run --dry-run       # rehearse timing/sequencing, no camera needed
    uv run eclipse-run --focus-check   # single focus check right after connecting;
                                        # blocks for confirmation if it looks out of
                                        # focus (per calibrated thresholds), otherwise
                                        # just logs the score and continues
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time
from pathlib import Path

import yaml

from . import bracket_plans as bp
from .camera import connect, run_sequence, set_config
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
    """Returns a list of (start_time, end_time, label, plan) tuples in
    chronological order."""
    t = cfg["timings_utc"]
    date_str = cfg["date"]
    c1, c2, c3, c4 = (to_dt(date_str, t[k]) for k in ("C1", "C2", "C3", "C4"))

    totality_seconds = (c3 - c2).total_seconds()
    overhead = cfg.get("camera", {}).get("bracket_overhead")
    totality_plan = bp.trim_to_fit(bp.totality_bracket, totality_seconds, overhead)

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
    return schedule


def _run_startup_focus_check(camera, cfg: dict, dry_run: bool) -> None:
    """Single-shot check right after connecting -- not --calibrate's
    interactive two-shot flow, which can't run inside anything meant to be
    unattended. Only blocks (requiring confirmation) for a confident
    "out_of_focus" verdict; "uncertain" or "in_focus" just get logged, and
    no calibrated thresholds means the score is informational only. You're
    necessarily at the terminal at startup, so blocking here doesn't
    compromise the schedule's own unattended design."""
    if dry_run:
        log.info("Dry run: skipping focus check (no real image to score).")
        return

    from .focus_check import VERDICT_LABELS, classify_score, run_single_check

    fc_cfg = cfg.get("focus_check", {}) or {}
    low = fc_cfg.get("low_threshold")
    high = fc_cfg.get("high_threshold")
    roi = tuple(fc_cfg["roi"]) if fc_cfg.get("roi") else None
    out_dir = Path(cfg.get("output_dir", "./eclipse_frames")) / "focus_checks"

    log.info("Running startup focus check...")
    path, score = run_single_check(camera, out_dir, roi)
    log.info("Focus check: score=%.1f  (%s)", score, path)

    if low is None or high is None:
        log.warning(
            "No focus_check thresholds calibrated (`eclipse-focus-check "
            "--calibrate`) -- score is informational only, continuing."
        )
        return

    verdict = classify_score(score, low, high)
    log.info("Verdict: %s", VERDICT_LABELS[verdict])

    if verdict == "out_of_focus":
        answer = input(
            f"\nFocus check suggests this may be OUT OF FOCUS. Check {path} "
            "yourself.\nContinue anyway? [y/N]: "
        )
        if answer.strip().lower() != "y":
            raise SystemExit("Stopped before the scheduled run -- fix focus and try again.")


def run(cfg: dict, dry_run: bool, focus_check: bool = False) -> None:
    schedule = build_schedule(cfg)
    tz = resolve_tz(cfg.get("timezone"))
    camera = connect(
        cfg.get("camera", {}).get("port"),
        dry_run=dry_run,
        capture_target=cfg.get("camera", {}).get("capture_target"),
    )

    if focus_check:
        _run_startup_focus_check(camera, cfg, dry_run)

    image_quality = cfg.get("camera", {}).get("image_quality")
    if image_quality:
        try:
            set_config(camera, "imagequality", image_quality)
            log.info("Set image quality to %r", image_quality)
        except Exception:
            log.exception(
                "Couldn't set image quality to %r — check it against "
                "`eclipse-throughput --list-image-quality`; continuing with "
                "whatever the camera is currently set to",
                image_quality,
            )

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
            run_sequence(camera, plan, end_time=end_time)
        except Exception:
            log.exception("Error during %s — continuing to next event", label)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="simulate the camera; use this to rehearse timing before the real day",
    )
    parser.add_argument(
        "--focus-check",
        action="store_true",
        help="run a single focus check right after connecting, before the "
        "schedule starts (see eclipse-focus-check --calibrate for the "
        "thresholds this uses)",
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

    run(cfg, dry_run=args.dry_run, focus_check=args.focus_check)


if __name__ == "__main__":
    main()