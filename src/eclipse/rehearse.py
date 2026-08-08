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
offsets, and trim_to_fit()'s per-shutter-speed time estimate for
totality_bracket. Everything else (camera.port, image_quality,
capture_target, focus_check thresholds, timezone) is read from your real
config.yaml as normal; config.yaml itself is never touched or written.

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
    uv run eclipse-rehearse --from-config             # use config.yaml's REAL
        timings_utc, re-dated to today (or tomorrow if today's C1 has passed).
        Preserves real durations AND real times of day, so running this the
        evening before puts the sun within about a degree of where it will be
        on the day -- the only way to rehearse framing, drift and the actual
        low-altitude light. Takes as long as the real eclipse (C1->C4).
    uv run eclipse-rehearse --totality-seconds 94      # realistic totality
                                                        # without waiting for
                                                        # the full C1->C4 run
    uv run eclipse-rehearse --partial-seconds 400 --totality-seconds 60
    uv run eclipse-rehearse --focus-check               # also exercise the
                                                          # startup focus check
"""

from __future__ import annotations

import argparse
import datetime as dt

import yaml

from .run_eclipse import CONFIG_PATH, run
from .tzutil import resolve_tz


def build_config_timings(
    cfg: dict, now: dt.datetime | None = None
) -> tuple[str, dict[str, str], bool]:
    """Returns (date_str, timings_utc, rolled_to_tomorrow) using
    config.yaml's REAL timings_utc verbatim, just re-dated to today (or
    tomorrow, if today's C1 has already passed).

    Preserves both the real phase durations AND the real times of day.
    The times of day matter for a dress rehearsal the evening before:
    run at the same clock time a day early and the sun sits within about
    a degree of where it will be during the eclipse, so framing, drift
    and the low-altitude light are all representative — which a
    compressed synthetic schedule can't test.

    `now` is injectable for testing; real callers leave it as None."""
    if "timings_utc" not in cfg:
        raise ValueError(
            "config.yaml has no timings_utc block — run `eclipse-timings --write` "
            "first, or rehearse with synthetic timings instead."
        )
    t = cfg["timings_utc"]
    missing = [k for k in ("C1", "C2", "max", "C3", "C4") if k not in t]
    if missing:
        raise ValueError(f"config.yaml's timings_utc is missing {missing}")

    if now is None:
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

    def on(date, hms):
        return dt.datetime.combine(date, dt.datetime.strptime(hms, "%H:%M:%S").time())

    date = now.date()
    rolled = False
    if on(date, t["C1"]) <= now:
        # Already past today's C1 — rolling forward keeps the whole
        # schedule in the future rather than firing the early phases in an
        # immediate catch-up burst, which wouldn't rehearse anything.
        date = date + dt.timedelta(days=1)
        rolled = True

    if on(date, t["C4"]) < on(date, t["C1"]):
        raise ValueError(
            f"timings_utc crosses UTC midnight (C1 {t['C1']} -> C4 {t['C4']}); "
            "run_eclipse.py's single-date format can't express that."
        )

    return date.strftime("%Y-%m-%d"), dict(t), rolled


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
    parser.add_argument(
        "--from-config",
        action="store_true",
        help="use config.yaml's REAL timings_utc verbatim, re-dated to today "
        "(or tomorrow if today's C1 has passed), instead of synthetic "
        "compressed timings. Preserves real durations AND real times of day "
        "-- ignores --start-in/--partial-seconds/--totality-seconds.",
    )
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    tz = resolve_tz(cfg.get("timezone"))

    if args.from_config:
        explicit = [
            name
            for name, val, default in (
                ("--start-in", args.start_in, 10.0),
                ("--partial-seconds", args.partial_seconds, 20.0),
                ("--totality-seconds", args.totality_seconds, 30.0),
            )
            if val != default
        ]
        if explicit:
            print(f"Note: {', '.join(explicit)} ignored with --from-config.\n")
        date_str, timings, rolled = build_config_timings(cfg)
        source = f"real config.yaml timings, re-dated to {date_str}"
        if rolled:
            source += " (today's C1 already passed, so rolled to tomorrow)"
    else:
        date_str, timings = build_synthetic_timings(
            args.start_in, args.partial_seconds, args.totality_seconds
        )
        source = "synthetic"

    cfg["date"] = date_str
    cfg["timings_utc"] = timings

    print(f"Rehearsal schedule ({source} — not written to config.yaml):")
    for label in ("C1", "C2", "max", "C3", "C4"):
        line = f"  {label:>4}: {timings[label]} UTC"
        if tz is not None:
            local = dt.datetime.combine(
                dt.date.fromisoformat(date_str),
                dt.datetime.strptime(timings[label], "%H:%M:%S").time(),
            ).replace(tzinfo=dt.timezone.utc).astimezone(tz)
            line += f"   ({local.strftime('%Y-%m-%d %H:%M:%S %Z')})"
        print(line)

    c1 = dt.datetime.strptime(f"{date_str} {timings['C1']}", "%Y-%m-%d %H:%M:%S")
    c4 = dt.datetime.strptime(f"{date_str} {timings['C4']}", "%Y-%m-%d %H:%M:%S")
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    print(
        f"\n  starts in {(c1 - now).total_seconds() / 60:.0f} min, "
        f"runs {(c4 - c1).total_seconds() / 60:.0f} min C1->C4"
    )

    if not args.dry_run:
        print(
            "\nThis fires REAL captures onto your camera's memory card. "
            "Clear the card before eclipse day so these don't take up "
            "space or get mixed up with the real captures.\n"
        )

    run(cfg, dry_run=args.dry_run, focus_check=args.focus_check)


if __name__ == "__main__":
    main()