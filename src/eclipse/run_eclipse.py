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
from .camera import (
    connect,
    free_macos_usb_claim,
    is_raw_jpeg_combo_quality,
    is_usb_claim_error,
    release,
    run_sequence,
    set_config,
    unknown_iso_override_keys,
)
from .cues import build_cue_schedule, start_cue_thread
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

    diamond_in_start = c2 - dt.timedelta(seconds=8)
    diamond_out_start = c3 - dt.timedelta(seconds=8)
    diamond_out_end = c3 + dt.timedelta(seconds=8)

    # diamond_ring_in deliberately straddles C2 (run_burst ignores
    # end_time — the burst's job is to bracket the contact moment), so it
    # runs past C2 by duration + offset. The totality bracket really
    # starts then, not at C2.
    burst_overrun = dt.timedelta(
        seconds=bp.diamond_ring_burst["duration_seconds"]
        + bp.diamond_ring_burst["trigger_offset_seconds"]
    )
    totality_start = c2 + burst_overrun

    # The bracket must END when diamond_ring_out needs to START, not at
    # C3: overrunning here delays the C3 diamond ring past the event
    # itself, which would miss it entirely.
    totality_seconds = (diamond_out_start - totality_start).total_seconds()
    overhead = cfg.get("camera", {}).get("bracket_overhead")
    totality_plan = bp.trim_to_fit(bp.totality_bracket, totality_seconds, overhead)

    # Clamp so short/compressed schedules (or a very short partial phase)
    # never push these before C1 or past C4.
    deep_crescent_pre_start = max(c1, diamond_in_start - DEEP_CRESCENT_LEAD)
    deep_crescent_post_end = min(c4, diamond_out_end + DEEP_CRESCENT_LEAD)

    schedule = [
        (c1, deep_crescent_pre_start, "partial_pre_totality", bp.partial_phase_bracket),
        (deep_crescent_pre_start, diamond_in_start, "deep_crescent_pre_totality", bp.deep_crescent_bracket),
        (diamond_in_start, c2, "diamond_ring_in", bp.diamond_ring_burst),
        (c2, diamond_out_start, "totality", totality_plan),
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


def unknown_download_phases(download_phases, known_labels) -> list[str]:
    """Phase labels in config.yaml's camera.download_phases that don't
    match any label in the built schedule — almost always a typo, and one
    that would otherwise fail silently (no downloads ever appear, and you
    only notice by their absence). Returns [] when everything matches."""
    return sorted(set(download_phases or []) - set(known_labels))


# Phases where a monitoring download costs window time that can't be
# recovered. The bursts and totality are obvious; the deep-crescent
# phases are included because their 30s interval is comparable to the
# time a full-file bracket download takes, so enabling downloads there
# can push a pass past its own window.
TIME_CRITICAL_PHASES = frozenset(
    {
        "totality",
        "diamond_ring_in",
        "diamond_ring_out",
        "deep_crescent_pre_totality",
        "deep_crescent_post_totality",
    }
)


def contact_times(cfg: dict) -> dict[str, dt.datetime]:
    """C1/C2/max/C3/C4 as naive-UTC datetimes — what audio cue offsets
    are measured from."""
    t = cfg["timings_utc"]
    return {k: to_dt(cfg["date"], t[k]) for k in ("C1", "C2", "max", "C3", "C4") if k in t}


def phase_window_end(start_time: dt.datetime, end_time: dt.datetime, plan: dict) -> dt.datetime:
    """When a phase's useful window actually closes.

    For brackets that's the scheduled end_time. For bursts it is NOT:
    run_burst deliberately ignores end_time and runs for its full
    duration_seconds so it straddles the contact moment, so its real
    close is start + duration.

    Used to skip phases that are already over — which matters if the
    script is restarted mid-event (camera unplugged, focus tweak, USB
    glitch). Brackets already self-skip via run_bracket_once's per-shot
    check, but a burst would otherwise fire its full 15 seconds at the
    wrong moment: restarting during totality would spend 15s of it
    re-shooting the C2 diamond ring at 1/4000, long after the diamond
    ring is over."""
    if plan.get("mode") == "burst_single_exposure":
        return start_time + dt.timedelta(seconds=plan.get("duration_seconds", 0))
    return end_time


# How long to keep retrying a reconnect, when the current phase's own
# window allows it. Sized for a human: unplugging and replugging a cable,
# plus USB re-enumeration, takes several seconds — a single immediate
# attempt will essentially always fail because the device isn't back yet.
# How many times to retry a single phase (each retry preceded by a
# reconnect) before giving up and moving on. Bounded so an instantly
# failing camera can't spin, but high enough to survive several
# unplug/replug cycles inside one long partial phase.
MAX_PHASE_ATTEMPTS = 5

RECONNECT_MAX_SECONDS = 45.0
RECONNECT_INITIAL_DELAY = 1.0
RECONNECT_MAX_DELAY = 5.0


def _reconnect_with_retries(cfg: dict, dry_run: bool, deadline: dt.datetime):
    """Retry _connect_and_prepare with backoff until `deadline`.

    A single immediate attempt is nearly useless: the original failure and
    the retry land milliseconds apart, long before a replugged camera has
    re-enumerated. Backing off gives the device (and the person holding
    the cable) time to come back.

    `deadline` is normally bounded by the current phase's own window, so
    a long partial phase can afford to keep trying while totality gives up
    quickly and moves on rather than burning irreplaceable seconds."""
    delay = RECONNECT_INITIAL_DELAY
    attempt = 0
    last_error: Exception | None = None
    while True:
        attempt += 1
        try:
            # attempts=1: this function owns the retry loop. Letting
            # connect() retry too multiplies the daemon kills and races
            # launchd instead of waiting it out.
            return _connect_and_prepare(cfg, dry_run, attempts=1)
        except Exception as e:  # noqa: BLE001 - retried below, re-raised on timeout
            last_error = e
            # [-53] means the device is present but owned by something
            # else — on macOS, the system PTP daemon, which grabs a camera
            # the moment it enumerates and never yields. Waiting longer
            # cannot help; freeing it can.
            if is_usb_claim_error(e) and cfg.get("camera", {}).get(
                "auto_free_usb_claim", False
            ):
                free_macos_usb_claim()
            remaining = (deadline - dt.datetime.now(dt.timezone.utc).replace(tzinfo=None))
            remaining_s = remaining.total_seconds()
            if remaining_s <= 0:
                break
            wait = min(delay, remaining_s)
            log.info(
                "Reconnect attempt %d failed (%s); retrying in %.1fs "
                "(%.0fs of retry budget left)",
                attempt,
                e,
                wait,
                remaining_s,
            )
            time.sleep(wait)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)
    raise RuntimeError(
        f"Reconnect failed after {attempt} attempts. Check the USB cable. "
        "If the errors were [-53] Could not claim the USB device, something "
        "else owns the camera: on macOS this project already tries to stop "
        "the system PTP daemon automatically, so check for an open Image "
        "Capture, Photos, Lightroom or a stray gphoto2 process."
    ) from last_error


def _connect_and_prepare(cfg: dict, dry_run: bool, attempts: int | None = None):
    """connect() plus the per-session settings that don't survive a
    reconnect. Factored out so a mid-event reconnect restores the camera
    to exactly the same state as the initial connection — connect()
    itself re-forces capturetarget, but image_quality is applied here and
    would otherwise be silently lost on reconnect."""
    kwargs = {} if attempts is None else {"attempts": attempts}
    camera = connect(
        cfg.get("camera", {}).get("port"),
        dry_run=dry_run,
        capture_target=cfg.get("camera", {}).get("capture_target"),
        free_claim=bool(cfg.get("camera", {}).get("auto_free_usb_claim", False)),
        **kwargs,
    )
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
    return camera


def run(cfg: dict, dry_run: bool, focus_check: bool = False) -> None:
    schedule = build_schedule(cfg)

    # Fail loud, before any waiting: a typo'd iso_overrides key never
    # applies, so that rung would silently shoot at base ISO — exactly
    # the kind of wrong-but-plausible result that's invisible in the logs
    # and unfixable after the fact.
    for name, plan in bp.all_plans().items():
        bad = unknown_iso_override_keys(plan)
        if bad:
            raise SystemExit(
                f"{name}: iso_overrides keys {bad} don't match any of its "
                f"shutter_speeds {plan.get('shutter_speeds')} — fix the typo in "
                "bracket_plans.py (see camera.iso_for_step)."
            )

    tz = resolve_tz(cfg.get("timezone"))

    # Optional per-phase preview downloads, for watching focus drift and
    # lens fogging during the long partial phases without needing the
    # camera's LCD (dark while tethered). Validated against the real
    # schedule labels: a typo would otherwise just silently never
    # download, and you'd discover that only by its absence.
    download_phases = set(cfg.get("camera", {}).get("download_phases") or [])
    known_labels = {label for _, _, label, _ in schedule}
    unknown = unknown_download_phases(download_phases, known_labels)
    if unknown:
        raise SystemExit(
            f"config.yaml's camera.download_phases contains unknown phase(s) "
            f"{unknown}. Valid labels: {sorted(known_labels)}"
        )
    if download_phases & TIME_CRITICAL_PHASES:
        log.warning(
            "download_phases includes a time-critical phase %s — full-file "
            "downloads take seconds each and cost window time that can't be "
            "recovered. This is allowed but rarely what you want; the partial "
            "phases have 300s intervals and are the intended place for it.",
            sorted(download_phases & TIME_CRITICAL_PHASES),
        )
    monitor_dir = Path(cfg.get("output_dir", "./eclipse_frames")) / "monitor"
    image_quality = cfg.get("camera", {}).get("image_quality")
    if image_quality and is_raw_jpeg_combo_quality(image_quality):
        raise SystemExit(
            f"config.yaml's camera.image_quality={image_quality!r} is a "
            "RAW+JPEG combo format. Confirmed directly on this project's "
            "camera: each capture in combo mode fires two FILE_ADDED "
            "events, but trigger_capture_one() consumes one per call — "
            "run_bracket_once() cycling shutter speeds can silently "
            "confirm a shot for the wrong speed (or none at all) with no "
            "error. Use a single-format choice instead (plain NEF/RAW or "
            "plain JPEG) — see `eclipse-throughput --list-image-quality`."
        )

    # Validate cues before anything else starts: a typo'd offset should
    # fail now, not silently never fire.
    audio = cfg.get("audio_cues") or {}
    contacts = contact_times(cfg)
    cue_specs = audio.get("cues") or []
    if cue_specs:
        try:
            build_cue_schedule(cue_specs, contacts)
        except ValueError as e:
            raise SystemExit(f"config.yaml audio_cues: {e}") from e

    log.info("Schedule (%d phases):", len(schedule))
    for start_time, end_time, label, plan in schedule:
        seconds = (phase_window_end(start_time, end_time, plan) - start_time).total_seconds()
        log.info(
            "  %-28s %s%s  %6.0fs%s",
            label,
            start_time.strftime("%H:%M:%S"),
            _local_suffix(start_time, tz),
            seconds,
            "   (ZERO-LENGTH — will be skipped)" if seconds <= 0 else "",
        )

    if cue_specs and audio.get("enabled", True):
        start_cue_thread(cue_specs, contacts, voice=audio.get("voice"))

    camera = _connect_and_prepare(cfg, dry_run)

    if focus_check:
        _run_startup_focus_check(camera, cfg, dry_run)

    for start_time, end_time, label, plan in schedule:
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

        window_end = phase_window_end(start_time, end_time, plan)
        if now >= window_end:
            if start_time >= window_end:
                # Structural, not a timing miss: the phase was allocated no
                # window at all. Happens when DEEP_CRESCENT_LEAD is longer
                # than the partial phase it sits inside, which is normal in
                # a compressed rehearsal and impossible on eclipse day.
                log.warning(
                    "Skipping %s — it has a zero-length window in this schedule "
                    "(nothing was lost at runtime; see the schedule summary above)",
                    label,
                )
            else:
                log.warning(
                    "Skipping %s — its window closed %.0fs ago (restarted mid-event?)",
                    label,
                    (now - window_end).total_seconds(),
                )
            continue

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
        kwargs = {
            "end_time": end_time,
            "overhead": cfg.get("camera", {}).get("bracket_overhead"),
            "timeout_margin": cfg.get("camera", {}).get("bracket_timeout_margin"),
            "download_dir": (monitor_dir if label in download_phases else None),
        }
        # Keep retrying this phase for as long as its window lasts. A
        # single reconnect isn't enough: an interval-based bracket can
        # succeed, sleep 30s, then hit a still-dead camera on its next
        # pass, and one-shot recovery would abandon the whole phase there.
        for attempt in range(1, MAX_PHASE_ATTEMPTS + 1):
            try:
                run_sequence(camera, plan, hard_stop=window_end, **kwargs)
                break
            except Exception:
                now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
                if now >= window_end:
                    log.exception(
                        "Error during %s and its window has closed — moving on", label
                    )
                    break
                if attempt == MAX_PHASE_ATTEMPTS:
                    log.exception(
                        "Error during %s — giving up after %d attempts, continuing "
                        "to next event (the schedule keeps running, and the next "
                        "phase will try to reconnect again)",
                        label,
                        attempt,
                    )
                    break
                log.exception(
                    "Error during %s (attempt %d/%d) — attempting to reconnect",
                    label,
                    attempt,
                    MAX_PHASE_ATTEMPTS,
                )
                try:
                    # Release first: the dead object still holds the USB
                    # claim, and connect() would otherwise fail with [-53]
                    # Could not claim the USB device.
                    release(camera)
                    # Never retry past this phase's own window — a long
                    # partial phase can wait, totality cannot.
                    deadline = min(
                        window_end,
                        now + dt.timedelta(seconds=RECONNECT_MAX_SECONDS),
                    )
                    camera = _reconnect_with_retries(cfg, dry_run, deadline)
                    log.info("Reconnected; retrying %s", label)
                except Exception:
                    log.exception(
                        "Reconnect failed for %s — continuing to next event "
                        "(the next phase will try again)",
                        label,
                    )
                    break


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