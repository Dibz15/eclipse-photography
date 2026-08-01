"""
Full-hardware rehearsal: runs the exact same scheduler (`run_eclipse.run`)
against synthetic, near-future, compressed timings instead of config.yaml's
`timings_utc` — so you can exercise the complete schedule at any time, not
just close to eclipse day, and with real camera behavior (unless
--dry-run), not just simulated timing.

This deliberately does NOT duplicate any scheduling logic. It only
synthesizes C1-C4 timestamps and feeds them into the unmodified
build_schedule()/run(), so a rehearsal exercises the exact same code path
as the real event — including deep-crescent window clamping, diamond-ring
offsets, and trim_to_fit() against your real measured_max_fps. Everything
else (camera.port, image_quality, capture_target, measured_max_fps,
focus_check thresholds, timezone) is read from your real config.yaml as
normal; config.yaml itself is never touched or written.

Note on --partial-seconds: DEEP_CRESCENT_LEAD (see run_eclipse.py) is a
fixed 3-minute constant, not something this tool overrides — testing the
real constant is the point. In practice this means deep_crescent_bracket
almost always claims your *entire* pre/post-totality window (any
--partial-seconds under ~3 minutes clamps that way), and since it's
interval-based (interval_seconds=30, also real and unscaled), it always
completes that full 30-second sleep once started rather than being cut
off mid-interval. On a short rehearsal this reliably eats past your
requested window and into the next phase's start time — you'll see
"already Xs in the past — running immediately" warnings cascade through
the rest of the run, and later phases (including totality itself) getting
less real execution time than requested. That's expected, not a bug: it's
exercising the schedule's real catch-up behavior under drift, which is
worth seeing work correctly at least once. If you want each phase to get
its full nominal duration instead, use a --partial-seconds comfortably
over 3 minutes.

Diamond ring bursts are similarly fixed at their own real 15-second
duration (bracket_plans.diamond_ring_burst), regardless of --partial-seconds
or --totality-seconds — that's not scaled either.

Usage:
    uv run eclipse-rehearse                          # real camera, ~110s total (default settings)
    uv run eclipse-rehearse --dry-run                 # simulated camera
    uv run eclipse-rehearse --totality-seconds 90      # see how your real
                                                        # measured_max_fps
                                                        # trims the bracket
                                                        # for a longer totality
    uv run eclipse-rehearse --partial-seconds 400 --totality-seconds 60
    uv run eclipse-rehearse --focus-check               # also exercise the
                                                          # startup focus check
"""

from __future__ import annotations

import argparse
import datetime as dt

import yaml

from .run_eclipse import CONFIG_PATH, run


def build_synthetic_timings(
    start_in: float,
    partial_seconds: float,
    totality_seconds: float,
    now: dt.datetime | None = None,
) -> tuple[str, dict[str, str]]:
    """Returns (date_str, timings_utc) in the same shape config.yaml's
    timings_utc block uses, built from a start offset and phase durations
    rather than real contact times. `now` is injectable for testing;
    real callers leave it as None to use the actual current time."""
    if start_in <= 0:
        raise ValueError("start_in must be positive")
    if partial_seconds <= 0 or totality_seconds <= 0:
        raise ValueError("partial_seconds and totality_seconds must be positive")

    if now is None:
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

    c1 = now + dt.timedelta(seconds=start_in)
    c2 = c1 + dt.timedelta(seconds=partial_seconds)
    c3 = c2 + dt.timedelta(seconds=totality_seconds)
    c4 = c3 + dt.timedelta(seconds=partial_seconds)

    if c4.date() != c1.date():
        raise ValueError(
            "This rehearsal schedule would cross a UTC midnight boundary "
            f"({c1.date()} -> {c4.date()}) — run_eclipse.py's timings_utc "
            "format assumes a single date. Use a smaller --start-in or "
            "shorter durations, or wait until after 00:00 UTC."
        )

    max_ecl = c2 + (c3 - c2) / 2
    fmt = "%H:%M:%S"
    return c1.strftime("%Y-%m-%d"), {
        "C1": c1.strftime(fmt),
        "C2": c2.strftime(fmt),
        "max": max_ecl.strftime(fmt),
        "C3": c3.strftime(fmt),
        "C4": c4.strftime(fmt),
    }


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument(
        "--dry-run", action="store_true", help="simulate the camera instead of using the real one"
    )
    parser.add_argument(
        "--focus-check", action="store_true", help="also exercise the startup focus check"
    )
    parser.add_argument(
        "--start-in", type=float, default=10.0, help="seconds from now until C1 (default: 10)"
    )
    parser.add_argument(
        "--partial-seconds",
        type=float,
        default=20.0,
        help="duration of each partial phase, before and after totality (default: 20)",
    )
    parser.add_argument(
        "--totality-seconds", type=float, default=30.0, help="duration of totality (default: 30)"
    )
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    date_str, timings = build_synthetic_timings(
        args.start_in, args.partial_seconds, args.totality_seconds
    )
    cfg["date"] = date_str
    cfg["timings_utc"] = timings

    print("Rehearsal schedule (synthetic — not written to config.yaml):")
    for label in ("C1", "C2", "max", "C3", "C4"):
        print(f"  {label:>4}: {timings[label]} UTC")

    if not args.dry_run:
        print(
            "\nThis fires REAL captures onto your camera's memory card. "
            "Clear the card before eclipse day so these don't take up "
            "space or get mixed up with the real captures.\n"
        )

    run(cfg, dry_run=args.dry_run, focus_check=args.focus_check)


if __name__ == "__main__":
    main()