"""
Run this weeks before the eclipse, indoors, pointed at a lamp. You're
measuring camera + USB + card throughput, not exposure quality.

For every real image-quality choice your camera reports (JPEG variants,
NEF/RAW, RAW+JPEG combos), times N captures with and without downloading
each frame. These choice strings are discovered from your actual camera
rather than guessed — they vary by camera model and even libgphoto2
version (there are open libgphoto2 bug reports about exactly this for
several Nikon bodies), so don't hardcode one from a forum post.

This sweep uses plain capture_one() and is purely informational now —
none of run_eclipse.py's actual capture path (run_burst()/
run_bracket_once()) depends on measured fps anymore; both are self-pacing
via trigger_capture_one(). Use this to compare image qualities against
each other (JPEG vs. NEF, download vs. not) and pick camera.image_quality
in config.yaml accordingly — see --trigger-test and --bracket-test below
for what the real capture path actually measures.

Usage:
    uv run eclipse-throughput                       # test every quality choice
    uv run eclipse-throughput --list-image-quality   # just print your camera's
                                                      # imagequality choices and exit
    uv run eclipse-throughput --list-capture-target  # just print your camera's
                                                      # capturetarget choices and exit
    uv run eclipse-throughput --trigger-test                       # test
        trigger_capture()+wait_for_event() instead of the normal capture()-
        based sweep -- see below.
    uv run eclipse-throughput --trigger-test --trigger-pause 0.3   # with a
        pause between triggers.
    uv run eclipse-throughput --trigger-test --trigger-event-timeout 30  # if
        your camera pauses with sustained write activity for longer than the
        default 25s between groups of captures.
    uv run eclipse-throughput --trigger-test --warmup-captures 3  # test whether
        a few ordinary captures first eliminate trigger-test's slow first call.
    uv run eclipse-throughput --bracket-test                      # test
        trigger_capture_one() across totality_bracket's REAL varying-shutter-
        speed sequence (not diamond_ring_burst's single fixed exposure) --
        this is the verification that validated moving totality_bracket off
        plain capture_one() (see bracket_plans.trim_to_fit's overhead
        default, set directly from this test's results). Re-run this if you
        change lens, card, or camera. See time_trigger_bracket() below.
    uv run eclipse-throughput --bracket-test --bracket-margin 5    # more
        margin per shot if slower speeds don't confirm in time.
    uv run eclipse-throughput --bracket-test --write               # also save
        the suggested camera.bracket_overhead into config.yaml -- see
        suggest_overhead() and bracket_plans.trim_to_fit().

trigger_capture() is a different, lower-level operation than the plain
capture() the rest of this script uses: it just fires the shutter and
returns immediately, versus capture()'s full trigger-wait-resolve cycle
(gphoto2's own docs and multiple independent reports describe capture()
as internally retrieving the object during that resolve step regardless
of whether your own code calls file_get() afterward -- which would
explain why --download shows little difference from no-download in the
main sweep above). --trigger-test measures trigger_capture() paired with
wait_for_event() to confirm (not download) each resulting FILE_ADDED
event, within this one persistent session -- avoiding the reconnection
overhead a shell loop of separate `gphoto2` CLI calls would pay per
iteration. wait_for_event() returns on the FIRST event of ANY type, not
specifically FILE_ADDED (property-change notifications fire constantly
around every capture), so each trigger polls repeatedly until FILE_ADDED
shows up or --trigger-event-timeout elapses -- a single check isn't
enough and will undercount real captures your camera is actually
completing.

On first use, --trigger-test may show a long delay (tens of seconds)
before the FIRST confirmed capture, then fast confirmations after that.
Testing ruled out both the obvious explanations: rebooting the camera
didn't change it (not a camera-side write-buffer thing), and bare
`gphoto2 --trigger-capture` from the CLI is always instant, even cold
(not a capture-triggering cost at all). The remaining explanation:
opening a PTP session appears to emit a burst of initial "here's my
current state" property-change events (seen directly in manual CLI
testing -- expprogram, continousshootingcount, ExposureRemaining, etc.),
and only a caller that's actually polling wait_for_event() has to churn
through that backlog before reaching the real FILE_ADDED. That would
make it a one-time cost per SESSION, not per capture or per idle period
-- use --warmup-captures to test this: a few ordinary capture_one()
calls before the real measurement, mirroring what a real schedule
already does (ordinary captures throughout the partial phase) long
before diamond_ring_burst's own 15-second window ever starts.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import yaml

from . import bracket_plans
from .camera import (
    capture_one,
    connect,
    get_config_choices,
    set_config,
    shutter_speed_seconds,
    trigger_capture_one,
)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"

_PLACEHOLDER_PREFIX = "Unknown value"


def is_real_choice(choice: str) -> bool:
    """Filters out libgphoto2's opaque placeholder entries: some camera/
    driver combinations report choices like "Unknown value 0003" for PTP
    property values the driver hasn't mapped to a label for that specific
    body yet (a known, documented libgphoto2 limitation — see the module
    docstring). These aren't meaningfully settable, so they're excluded."""
    return not choice.startswith(_PLACEHOLDER_PREFIX)


def warm_up(camera, n: int) -> None:
    """Fires n plain capture_one() calls, discarding results, before the
    real measurement starts. Diagnostic for whether an initial event-sync
    delay -- seen on the first trigger_capture()+wait_for_event() call in
    a fresh session, but never with bare --trigger-capture alone, and not
    cleared by rebooting the camera -- is a one-time per-SESSION cost
    that ordinary captures elsewhere in a real schedule would already
    absorb before diamond_ring_burst ever runs. See the module
    docstring."""
    for _ in range(n):
        capture_one(camera)


def time_captures(camera, n: int, download: bool) -> float:
    import gphoto2 as gp

    start = time.time()
    for _ in range(n):
        path = capture_one(camera)
        if download and path is not None:
            cam_file = camera.file_get(path.folder, path.name, gp.GP_FILE_TYPE_NORMAL)
            cam_file.get_data_and_size()
    return time.time() - start


def time_trigger_captures(camera, n: int, pause: float, event_timeout: float = 25.0) -> tuple[float, int]:
    """Times n trigger_capture() + wait_for_event() cycles within this one
    persistent session, using the same trigger_capture_one() that
    run_burst() uses in production (see camera.py) — this diagnostic and
    the real capture path always exercise the identical mechanism.

    `event_timeout` here defaults much longer than trigger_capture_one()'s
    own production default (5s), specifically to accommodate the cold-
    start session-handshake delay this function exists to measure — see
    trigger_capture_one()'s docstring and --warmup-captures below.

    Caveat: if the camera has several captures queued up (e.g. right
    after a busy/writing period) and confirms them in a burst, a
    FILE_ADDED seen here isn't guaranteed to belong to the trigger that
    "caused" it in strict 1:1 order — good enough for a throughput read,
    not a rigorous per-frame audit.

    Returns (elapsed_seconds, frames_confirmed)."""
    start = time.time()
    confirmed = 0
    for _ in range(n):
        if trigger_capture_one(camera, event_timeout=event_timeout):
            confirmed += 1
        if pause:
            time.sleep(pause)
    elapsed = time.time() - start
    return elapsed, confirmed


def time_trigger_bracket(
    camera, shutter_speeds: list[str], margin: float
) -> list[tuple[str, float, bool]]:
    """Times a set_config(shutterspeed) + trigger_capture_one() cycle
    across a SEQUENCE of different shutter speeds — mirroring
    run_bracket_once()'s actual pattern (used by totality_bracket and the
    partial-phase brackets), unlike time_trigger_captures() above, which
    only exercises a single fixed exposure repeated (diamond_ring_burst's
    pattern). Neither the set_config()-between-shots interleaving nor
    slow (multi-second) exposures were covered by the testing that
    validated trigger_capture_one() for diamond_ring_burst — this is
    that missing verification.

    event_timeout for each shot is shutter_speed_seconds(speed) + margin
    — a fixed short timeout appropriate for diamond_ring_burst's
    near-instant 1/4000 exposure would incorrectly read a slow, working
    multi-second exposure as a failure, since the shutter has to stay
    open for the full exposure time before FILE_ADDED can possibly fire.
    `margin` is a starting guess (steady-state overhead measured at
    ~1.2s for FAST exposures in earlier testing; whether that same
    overhead holds for slow exposures is exactly what this is checking).

    Returns a list of (shutter_speed, elapsed_seconds, confirmed) per
    shot, in the order given, so you can see exactly which speeds (if
    any) need more margin."""
    results = []
    for speed in shutter_speeds:
        set_config(camera, "shutterspeed", speed)
        timeout = shutter_speed_seconds(speed) + margin
        start = time.time()
        confirmed = trigger_capture_one(camera, event_timeout=timeout)
        elapsed = time.time() - start
        results.append((speed, elapsed, confirmed))
    return results


def observed_overheads(results: list[tuple[str, float, bool]]) -> list[float]:
    """Per-shot overhead (elapsed - exposure time) for CONFIRMED shots
    only. Unconfirmed shots are excluded: their elapsed time reflects
    giving up at the timeout, not a real completion, so it isn't a
    meaningful overhead reading."""
    return [
        elapsed - shutter_speed_seconds(speed) for speed, elapsed, confirmed in results if confirmed
    ]


def suggest_overhead(results: list[tuple[str, float, bool]]) -> float | None:
    """AVERAGE per-shot overhead — for config.yaml's
    camera.bracket_overhead, which is used to ESTIMATE how long shots
    and passes will take (bracket_plans.trim_to_fit, and
    camera.run_bracket_once's per-shot predictive check).

    Average rather than max on purpose: the max is dominated by
    occasional catch-up shots absorbing a previous shot's write backlog,
    and using it for whole-pass estimation overestimated a real measured
    14-step pass by ~48% — which would trim the bracket harder than
    necessary and skip shots that would comfortably have fit. Returns
    None if nothing confirmed."""
    observed = observed_overheads(results)
    if not observed:
        return None
    return round(sum(observed) / len(observed), 2)


def suggest_timeout_margin(
    results: list[tuple[str, float, bool]], safety_margin: float = 0.5
) -> float | None:
    """MAX per-shot overhead plus a safety margin — for config.yaml's
    camera.bracket_timeout_margin, which sizes each shot's
    event_timeout in camera.trigger_capture_one().

    Max rather than average on purpose, the mirror image of
    suggest_overhead(): this value decides when to GIVE UP on a shot, so
    it has to comfortably exceed the worst observed case. Too low and
    slow-but-working shots get falsely recorded as failures. Returns
    None if nothing confirmed."""
    observed = observed_overheads(results)
    if not observed:
        return None
    return round(max(observed) + safety_margin, 2)


def run(camera, n: int = 20) -> dict[str, float]:
    choices = [c for c in get_config_choices(camera, "imagequality") if is_real_choice(c)]
    if not choices:
        raise SystemExit(
            "Camera reported no usable imagequality choices — try "
            "`uv run eclipse-throughput --list-image-quality` to see the raw list."
        )

    results: dict[str, float] = {}
    for quality in choices:
        set_config(camera, "imagequality", quality)
        set_config(camera, "shutterspeed", "1/500")
        for download in (False, True):
            elapsed = time_captures(camera, n, download)
            fps = n / elapsed
            key = f"{quality}_{'download' if download else 'nodownload'}"
            results[key] = fps
            print(
                f"{n} captures, {quality}, download={download}: "
                f"{elapsed:.2f}s -> {fps:.2f} fps"
            )
    return results


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--port", default=None)
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument(
        "--list-image-quality",
        action="store_true",
        help="print your camera's exact imagequality choice strings and exit — "
        "use one of these in config.yaml's camera.image_quality",
    )
    parser.add_argument(
        "--list-capture-target",
        action="store_true",
        help="print your camera's exact capturetarget choice strings and exit — "
        "use one of these in config.yaml's camera.capture_target if "
        "auto-detection ever picks the wrong one",
    )
    parser.add_argument(
        "--trigger-test",
        action="store_true",
        help="test trigger_capture()+wait_for_event() throughput instead of "
        "the normal capture()-based sweep — see the module docstring",
    )
    parser.add_argument(
        "--trigger-pause",
        type=float,
        default=0.0,
        help="seconds to sleep between trigger_capture() calls in --trigger-test (default: 0)",
    )
    parser.add_argument(
        "--trigger-event-timeout",
        type=float,
        default=25.0,
        help="max seconds to wait for each trigger's FILE_ADDED event before "
        "giving up on it, in --trigger-test (default: 25)",
    )
    parser.add_argument(
        "--warmup-captures",
        type=int,
        default=0,
        help="fire N plain capture_one() calls before the real test (any mode) "
        "-- checks whether that eliminates trigger_capture()'s first-call "
        "delay, see module docstring",
    )
    parser.add_argument(
        "--bracket-test",
        action="store_true",
        help="test trigger_capture_one() across the REAL totality_bracket "
        "shutter speed sequence (varying speeds via set_config between "
        "shots, including multi-second exposures) -- see module docstring "
        "and time_trigger_bracket()",
    )
    parser.add_argument(
        "--bracket-margin",
        type=float,
        default=3.0,
        help="seconds of margin added to each shot's own exposure time for "
        "--bracket-test's per-shot event_timeout (default: 3)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="with --bracket-test: write the suggested camera.bracket_overhead "
        "and camera.bracket_timeout_margin into config.yaml",
    )
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    # --list-* is diagnostic — it needs to work even when the automatic
    # capturetarget enforcement below would fail, since that's often
    # exactly what you're trying to debug.
    if args.list_image_quality or args.list_capture_target:
        camera = connect(args.port, enforce_capture_target=False)
        if args.list_image_quality:
            choices = get_config_choices(camera, "imagequality")
            print("Your camera's imagequality choices:")
            for c in choices:
                flag = "" if is_real_choice(c) else "  (placeholder — not usable, see docstring)"
                print(f"  {c!r}{flag}")
        if args.list_capture_target:
            choices = get_config_choices(camera, "capturetarget")
            print("Your camera's capturetarget choices:")
            for c in choices:
                print(f"  {c!r}")
        return

    camera = connect(args.port, capture_target=cfg.get("camera", {}).get("capture_target"))

    if args.warmup_captures:
        print(f"Warming up with {args.warmup_captures} plain capture(s)...")
        warm_up(camera, args.warmup_captures)

    if args.trigger_test:
        elapsed, confirmed = time_trigger_captures(
            camera, args.n, args.trigger_pause, args.trigger_event_timeout
        )
        fps = confirmed / elapsed if elapsed else 0.0
        print(
            f"{confirmed}/{args.n} confirmed in {elapsed:.2f}s "
            f"(pause={args.trigger_pause}s) -> {fps:.2f} fps (confirmed frames only)"
        )
        if confirmed < args.n:
            print(
                f"\n{args.n - confirmed} trigger(s) never saw a FILE_ADDED event "
                f"within {args.trigger_event_timeout:.0f}s — try a longer "
                "--trigger-event-timeout if your camera's write-buffer pause "
                "is longer than that."
            )
        return

    if args.bracket_test:
        speeds = bracket_plans.totality_bracket["shutter_speeds"]
        results = time_trigger_bracket(camera, speeds, args.bracket_margin)
        print(f"{'shutter speed':>15}  {'elapsed':>10}  confirmed")
        for speed, elapsed, confirmed in results:
            print(f"{speed:>15}  {elapsed:>9.2f}s  {'yes' if confirmed else 'NO'}")
        failures = [speed for speed, _, confirmed in results if not confirmed]
        if failures:
            print(
                f"\n{len(failures)} speed(s) didn't confirm within their allotted "
                f"time: {failures} — try a longer --bracket-margin for those."
            )
        else:
            print("\nAll shutter speeds confirmed.")

        overhead = suggest_overhead(results)
        timeout_margin = suggest_timeout_margin(results)
        if overhead is None:
            print("\nNo confirmed shots to base timing suggestions on.")
        else:
            print(f"\nSuggested camera.bracket_overhead:       {overhead}  (average, for estimation)")
            print(f"Suggested camera.bracket_timeout_margin: {timeout_margin}  (max+margin, for timeouts)")
            if args.write:
                cam = cfg.setdefault("camera", {})
                cam["bracket_overhead"] = overhead
                cam["bracket_timeout_margin"] = timeout_margin
                with open(args.config, "w") as f:
                    yaml.safe_dump(cfg, f, sort_keys=False)
                print(f"Wrote both into {args.config}")
        return

    results = run(camera, n=args.n)

    print("\nSummary (fps):")
    for k, v in results.items():
        print(f"  {k:>30}: {v:.2f}")


if __name__ == "__main__":
    main()