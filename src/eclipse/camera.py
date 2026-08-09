"""
python-gphoto2 wrapper for the Nikon D5500: connect, configure, and run the
bracket/burst sequences defined in bracket_plans.py.

`import gphoto2` is deliberately lazy (inside connect() / capture_one() /
trigger_capture_one()), so the rest of this project — timings, bracket
trimming math, tests — stays importable on a machine that doesn't have
libgphoto2 installed, e.g. this dev laptop before you've brewed it.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from pathlib import Path

log = logging.getLogger("eclipse.camera")


# --------------------------------------------------------------------------
# Dry-run camera: lets you rehearse run_eclipse.py's timing and dispatch
# logic with `--dry-run`, with no hardware attached.
# --------------------------------------------------------------------------

class _DryRunNode:
    def __init__(self, name: str):
        self.name = name

    def set_value(self, value):
        log.info("[dry-run] set %s = %s", self.name, value)


class _DryRunConfig:
    def get_child_by_name(self, name: str):
        return _DryRunNode(name)


class DryRunCamera:
    """Stand-in for a real gphoto2.Camera. Logs instead of touching
    hardware, and sleeps briefly per capture/trigger to simulate
    realistic PTP round-trip latency. Without this, run_burst()'s
    duration-bounded loop would spin as fast as Python can loop for the
    full duration_seconds window — hundreds of thousands of "captures" in
    15 seconds — since nothing here naturally throttles it the way a real
    camera's blocking capture()/trigger_capture() call would."""

    def get_config(self):
        return _DryRunConfig()

    def set_config(self, cfg):
        pass

    def capture(self, *_args, **_kwargs):
        log.info("[dry-run] capture()")
        time.sleep(0.2)  # ~5fps, roughly matching real D5200 throughput

    def trigger_capture(self):
        log.info("[dry-run] trigger_capture()")
        time.sleep(0.2)

    def wait_for_event(self, timeout_ms):
        log.info("[dry-run] wait_for_event(%d)", timeout_ms)

    def exit(self):
        log.info("[dry-run] exit()")


# --------------------------------------------------------------------------
# Real camera
# --------------------------------------------------------------------------

def verify_required_config_nodes(camera) -> None:
    """Confirm the config nodes every bracket depends on actually exist,
    at connect time rather than at the first capture.

    Learned the hard way: 'aperture' doesn't exist on this Nikon (it's
    'f-number'), and the failure surfaced as a bare [-2] Bad parameters
    midway through the first bracket of a rehearsal. Checking up front
    turns that into a clear message before anything is scheduled."""
    available = set(list_config_names(camera))
    missing = [n for n in ("iso", "shutterspeed") if n not in available]
    if resolve_aperture_name(camera) is None:
        missing.append(f"aperture (tried {list(APERTURE_NAMES)})")
    if missing:
        raise RuntimeError(
            f"Camera is missing required config node(s): {missing}. "
            f"Available nodes: {sorted(available)}"
        )


def release(camera) -> None:
    """Drop the camera's USB claim. Must be called before reconnecting to
    the same body: a dead-but-unreleased camera object keeps the claim,
    and the new connect() then fails with [-53] Could not claim the USB
    device. Never raises — the object may already be in a bad state, and
    the whole point of calling this is that we're recovering."""
    try:
        camera.exit()
    except Exception:
        log.debug("camera.exit() failed while releasing (already dead?)", exc_info=True)


def connect(
    port: str | None = None,
    dry_run: bool = False,
    capture_target: str | None = None,
    enforce_capture_target: bool = True,
):
    if dry_run:
        log.info("Dry run: using simulated camera, no hardware required.")
        return DryRunCamera()

    import gphoto2 as gp  # lazy import, see module docstring

    camera = gp.Camera()
    if port:
        try:
            port_info_list = gp.PortInfoList()
            port_info_list.load()
            idx = port_info_list.lookup_path(port)
            camera.set_port_info(port_info_list[idx])
        except gp.GPhoto2Error:
            log.warning(
                "Couldn't select port %s explicitly, falling back to auto-detect", port
            )
    camera.init()
    log.info("Connected to camera%s", f" on {port}" if port else " (auto-detected)")

    if enforce_capture_target:
        verify_required_config_nodes(camera)
        _force_capture_target_to_card(camera, override=capture_target)

    return camera


def list_config_names(camera) -> list[str]:
    """Every settable leaf node name the camera exposes. Used for
    discovery and for error messages — when a name we expect is missing,
    showing what IS available beats a bare 'Bad parameters'."""

    def walk(node):
        n = node.count_children()
        if n == 0:
            yield node.get_name()
            return
        for i in range(n):
            yield from walk(node.get_child(i))

    return sorted(walk(camera.get_config()))


def find_config_name(camera, candidates: tuple[str, ...]) -> str | None:
    """First candidate name that actually exists on this camera, or None.

    Config node names are NOT portable: aperture is 'f-number' on Nikon
    PTP but 'aperture' on Canon, and asking for a name the camera doesn't
    have raises a bare GPhoto2Error [-2] Bad parameters that says nothing
    about which name was wrong. Same lesson as imagequality vs the
    non-existent imageformat — discover, don't assume."""
    available = set(list_config_names(camera))
    return next((c for c in candidates if c in available), None)


# Aperture: Nikon PTP calls it "f-number"; other vendors differ. Ordered
# most-likely-first for this project's camera.
APERTURE_NAMES = ("f-number", "aperture", "fnumber", "aperturevalue")
_aperture_node: str | None = None


def resolve_aperture_name(camera) -> str | None:
    """Memoised — the name depends on the camera model, which doesn't
    change across a reconnect, and each lookup costs a PTP round trip."""
    global _aperture_node
    if _aperture_node is None:
        _aperture_node = find_config_name(camera, APERTURE_NAMES)
    return _aperture_node


def set_config(camera, name: str, value):
    cfg = camera.get_config()
    node = cfg.get_child_by_name(name)
    node.set_value(value)
    camera.set_config(cfg)


def get_config_choices(camera, name: str) -> list[str]:
    """Returns the valid choice strings for a RADIO/MENU config node (e.g.
    'imagequality'). These vary by camera model and even libgphoto2
    version, so discover them from the actual connected camera rather than
    hardcoding a guess — see throughput_test.py --list-image-quality."""
    cfg = camera.get_config()
    node = cfg.get_child_by_name(name)
    return list(node.get_choices())


def pick_card_choice(choices: list[str]) -> str | None:
    """Case-insensitive match for the 'write to memory card' choice among
    capturetarget's options (seen across cameras/drivers as e.g. 'Memory
    card') — returns None if nothing matches."""
    return next((c for c in choices if "card" in c.lower()), None)


def is_raw_jpeg_combo_quality(image_quality: str) -> bool:
    """True for RAW+JPEG combo imagequality choices (e.g. 'NEF+Fine',
    'NEF+Normal') — detected by the '+' that's consistently present in
    these choice strings across cameras/drivers.

    Confirmed directly on this project's camera: each physical capture in
    combo mode fires TWO FILE_ADDED events (one per file), but
    trigger_capture_one() consumes exactly one event per call and treats
    that as "this trigger is confirmed." For run_bracket_once() cycling
    through different shutter speeds, that mismatch means every other
    "confirmed" shot can actually be draining a STALE event left over
    from the previous trigger's second file — silently reporting a
    shutter speed as captured when no new photo was taken at that speed
    at all, with the real confirmation misattributed to a later speed
    instead (or lost). Single-format JPEG or single-format RAW produce
    exactly one event per capture and don't have this problem — see
    run_eclipse.py, which refuses to proceed with a combo
    camera.image_quality rather than risk this silently during the
    event."""
    return "+" in image_quality


def shutter_speed_seconds(shutter_speed: str) -> float:
    """Converts a shutter speed string in bracket_plans.py's format
    ('1/2000', '4', etc.) into seconds. Used to size
    trigger_capture_one()'s event_timeout per-shot: a 4-second exposure
    needs the shutter to stay physically open for the full 4 seconds
    before FILE_ADDED can possibly fire, so a fixed short timeout tuned
    for diamond_ring_burst's near-instant 1/4000 exposure would
    incorrectly read a slow, working shot as a failure."""
    if "/" in shutter_speed:
        numerator, denominator = shutter_speed.split("/")
        return float(numerator) / float(denominator)
    return float(shutter_speed)


def _force_capture_target_to_card(camera, override: str | None = None) -> None:
    """Every bracket plan in this project assumes captures survive on the
    card without being downloaded during the event. Many camera/driver
    combinations default `capturetarget` to the camera's small internal
    RAM buffer instead — gphoto2's own docs: "setting this to sdram will
    make the camera capture directly into the camera RAM and not on the
    memory card. You need to download the image in the same gphoto2 call,
    otherwise it will [be] gone when the connection is closed." Left
    unset, captures silently vanish, and a burst long enough can overflow
    that RAM buffer and hang the camera entirely.

    By default this auto-detects the right choice (whichever contains
    "card") and enforces it — there's no legitimate reason this project
    would ever want Internal RAM, unlike image_quality where JPEG vs RAW
    is a real preference. If `override` is given (from config.yaml's
    camera.capture_target — set this only if auto-detection picks the
    wrong choice on your camera), it's used instead, after validating it's
    actually one of the camera's real choices.

    Either way, this reads the value back to confirm the change actually
    took — some Nikon bodies/driver versions have been reported not to
    apply it — raising loudly rather than risking lost frames on eclipse
    morning.
    """
    choices = get_config_choices(camera, "capturetarget")

    if override is not None:
        if override not in choices:
            raise RuntimeError(
                f"config.yaml's camera.capture_target={override!r} is not one "
                f"of this camera's capturetarget choices: {choices!r}"
            )
        card_choice = override
    else:
        card_choice = pick_card_choice(choices)
        if card_choice is None:
            raise RuntimeError(
                f"No 'memory card' choice found in capturetarget options: {choices!r} "
                "— set camera.capture_target explicitly in config.yaml (see "
                "`eclipse-throughput --list-capture-target`), or check "
                "`gphoto2 --get-config capturetarget` directly."
            )

    set_config(camera, "capturetarget", card_choice)

    cfg = camera.get_config()
    node = cfg.get_child_by_name("capturetarget")
    actual = node.get_value()
    if actual != card_choice:
        raise RuntimeError(
            f"Tried to set capturetarget to {card_choice!r} but camera still "
            f"reports {actual!r} — captures may be going to internal RAM "
            "and will NOT survive without downloading."
        )
    log.info("capturetarget confirmed set to %r", card_choice)


def capture_one(camera):
    if isinstance(camera, DryRunCamera):
        return camera.capture()

    import gphoto2 as gp

    return camera.capture(gp.GP_CAPTURE_IMAGE)


def download_preview(camera, folder: str, name: str, out_dir: Path) -> Path | None:
    """Saves the EMBEDDED JPEG PREVIEW of a just-captured frame to out_dir,
    for monitoring focus drift and lens fogging during the long partial
    phases without touching the camera's LCD (which is dark while
    tethered anyway).

    Deliberately the preview, not the full file:
      - it's a small JPEG rather than a ~25MB NEF, so it costs a fraction
        of the download time — the partial-phase interval has room, but
        there's no reason to spend it;
      - PIL can read it, so it can be sharpness-scored automatically,
        which a raw NEF can't be without an extra RAW-decoding dependency;
      - the real full-resolution frame is untouched on the card either
        way. This is a monitoring copy, not the deliverable — those still
        come off the card afterwards via scripts/pull_from_card.py.

    Never raises: a failed monitoring download must not interrupt a
    bracket. Returns the local path, or None if anything went wrong."""
    import gphoto2 as gp

    try:
        cam_file = camera.file_get(folder, name, gp.GP_FILE_TYPE_PREVIEW)
        out_dir.mkdir(parents=True, exist_ok=True)
        local = out_dir / f"{Path(name).stem}_preview.jpg"
        cam_file.save(str(local))
    except Exception:
        log.exception("Preview download failed for %s (continuing)", name)
        return None

    # Sharpness score is a nice-to-have on top: a falling trend across the
    # partial phase means focus drift, and a sudden collapse means the
    # lens (or filter) has fogged. Imported lazily — focus_check imports
    # this module, so a top-level import would be circular.
    try:
        from .focus_check import score_image

        log.info("Preview %s  sharpness=%.1f", local.name, score_image(local, roi=None))
    except Exception:  # noqa: BLE001 - scoring is optional; never break a bracket for it
        log.info("Preview %s (not scored)", local.name)
    return local


def trigger_capture_one(
    camera, event_timeout: float = 5.0, download_dir: Path | None = None
) -> bool:
    """Fires trigger_capture() and confirms it via wait_for_event(),
    rather than the full trigger-wait-resolve cycle plain capture()
    does internally (gphoto2's own docs, and multiple independent
    reports, describe capture() as retrieving the object's data as a
    side effect of that resolve step regardless of whether the caller
    ever calls file_get() -- which is why it's meaningfully slower).
    Confirmed directly on this project's camera: ~0.82fps sustained via
    this mechanism vs. ~0.5fps for plain capture() at the same image
    quality, with zero dropped frames once warmed up (see below).

    wait_for_event() returns on the FIRST event of ANY type, not
    specifically FILE_ADDED — this camera fires plenty of unrelated
    property-change notifications around every capture — so a single
    call isn't enough; this polls until FILE_ADDED shows up or
    event_timeout seconds pass with nothing relevant arriving.

    The first trigger_capture()+wait_for_event() cycle in a freshly
    connected session can take tens of seconds (confirmed: not a
    camera-side write-buffer thing, since it wasn't cleared by
    rebooting the camera, and not a shutter-triggering cost, since bare
    trigger_capture() alone is instant even cold) — most likely a
    one-time PTP session handshake, where opening a session emits an
    initial burst of state-sync events. That cost is naturally absorbed
    by whichever capture happens first after connect() — every bracket
    plan in this project fires ordinary capture_one() calls well before
    diamond_ring_burst's own tight window, so event_timeout's default
    here reflects steady-state operation, not that cold-start case (see
    throughput_test.py's --trigger-test for testing the cold-start
    scenario directly, which uses a longer default for that reason).

    Returns True if a FILE_ADDED event was confirmed, False if the
    timeout elapsed without one — doesn't raise, since one missed frame
    in a burst shouldn't abort the rest of it.

    If download_dir is given, the frame's embedded JPEG preview is saved
    there for focus/fog monitoring — see download_preview(). Confirmation
    is unaffected by whether that download succeeds."""
    if isinstance(camera, DryRunCamera):
        camera.trigger_capture()
        camera.wait_for_event(int(event_timeout * 1000))
        if download_dir is not None:
            log.info("[dry-run] would download preview to %s", download_dir)
        return True

    import gphoto2 as gp

    camera.trigger_capture()
    trigger_start = time.monotonic()
    while time.monotonic() - trigger_start < event_timeout:
        event_type, event_data = camera.wait_for_event(3000)  # ms, per poll
        if event_type == gp.GP_EVENT_FILE_ADDED:
            if download_dir is not None:
                download_preview(camera, event_data.folder, event_data.name, download_dir)
            return True
    return False


def _apply_static_settings(camera, plan: dict, include_iso: bool = True) -> None:
    if include_iso and "iso" in plan:
        set_config(camera, "iso", str(plan["iso"]))
    if "aperture" in plan:
        node = resolve_aperture_name(camera)
        if node is None:
            raise RuntimeError(
                f"This camera exposes no aperture config node (tried "
                f"{list(APERTURE_NAMES)}). If the lens has no electronic "
                "aperture coupling, set it on the lens/body and remove "
                "'aperture' from the plans in bracket_plans.py. Available "
                f"nodes: {list_config_names(camera)}"
            )
        set_config(camera, node, plan["aperture"])


def iso_for_step(plan: dict, shutter_speed: str) -> int | None:
    """ISO for one rung of a bracket. Falls back to the plan's own `iso`
    unless plan['iso_overrides'] names this shutter speed specifically.

    Per-step ISO exists so the SLOW end of a bracket can trade sensor
    gain for exposure time — a rung at 2s/ISO500 reaches marginally
    deeper than 4s/ISO200 (1000 vs 800 ISO-seconds) while halving how far
    the sky drifts during the exposure. The fast rungs stay at the plan's
    base ISO, where smear is already sub-pixel and there's nothing to buy.
    See bracket_plans.totality_bracket."""
    overrides = plan.get("iso_overrides") or {}
    return overrides.get(shutter_speed, plan.get("iso"))


def unknown_iso_override_keys(plan: dict) -> list[str]:
    """iso_overrides keys that don't match any of the plan's own
    shutter_speeds — almost always a typo, and one that would otherwise
    fail silently (the override simply never applies, and the rung shoots
    at base ISO). Returns [] when everything lines up."""
    overrides = plan.get("iso_overrides") or {}
    speeds = set(plan.get("shutter_speeds", []))
    return sorted(k for k in overrides if k not in speeds)


def run_burst(camera, plan: dict) -> int:
    """diamond_ring_burst-style plan: fixed exposure, for
    plan['duration_seconds']. Uses trigger_capture_one() rather than
    capture_one() — confirmed meaningfully faster for this fixed-exposure
    pattern (see trigger_capture_one's docstring). No fps-based pacing
    here: trigger_capture_one() is already self-pacing via its own
    blocking wait_for_event() confirmation, so an additional sleep based
    on measured_max_fps (measured against the older, slower plain
    capture() mechanism) would just throttle this faster path back down
    toward the old rate.

    Returns the number of CONFIRMED frames captured — may be less than
    the number of trigger attempts if any went unconfirmed."""
    _apply_static_settings(camera, plan)
    set_config(camera, "shutterspeed", plan["shutter_speed"])

    end = time.monotonic() + plan["duration_seconds"]
    attempted = 0
    confirmed = 0
    while time.monotonic() < end:
        attempted += 1
        if trigger_capture_one(camera):
            confirmed += 1
    log.info("Burst complete: %d/%d confirmed", confirmed, attempted)
    return confirmed


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


# Fallback per-shot overhead (seconds) for TIME ESTIMATION when
# config.yaml's camera.bracket_overhead isn't set. This is the AVERAGE
# overhead measured across a real 14-step pure-NEF bracket on this
# project's camera ((total elapsed - total exposure time) / shots).
# Deliberately the average, not the max: the max is dominated by
# occasional catch-up shots absorbing a previous shot's backlog, and
# using it for whole-pass estimation overestimated a real pass by ~48%.
# Calibrate against your own gear with `eclipse-throughput --bracket-test
# --write`.
DEFAULT_BRACKET_OVERHEAD = 2.2

# Fallback margin (seconds) added to each shot's own exposure time when
# sizing trigger_capture_one()'s event_timeout. This one IS max-based on
# purpose: it decides when to give up on a shot, so it must comfortably
# exceed the worst observed case rather than the typical one, or slow
# working shots get falsely recorded as failures.
DEFAULT_BRACKET_TIMEOUT_MARGIN = 4.0


def run_bracket_once(
    camera,
    plan: dict,
    end_time: dt.datetime | None = None,
    overhead: float | None = None,
    timeout_margin: float | None = None,
    reverse: bool = False,
    download_dir: Path | None = None,
) -> tuple[int, int, int]:
    """Fires one pass through plan['shutter_speeds'], one shot per speed.
    Uses trigger_capture_one() rather than plain capture_one() — verified
    via `eclipse-throughput --bracket-test` across the real
    totality_bracket sequence (varying speeds via set_config between
    shots, up through a 4-second exposure), not just diamond_ring_burst's
    single fixed exposure.

    Two distinct timing values, deliberately separate because they answer
    different questions:

    - `timeout_margin` sizes each shot's event_timeout as
      shutter_speed_seconds(speed) + timeout_margin. The shutter must
      stay open for the full exposure before FILE_ADDED can fire, so a
      fixed short timeout tuned for a near-instant exposure would read a
      slow but working multi-second shot as a failure. Wants a
      worst-case (max-based) value.
    - `overhead` estimates how long a shot will actually TAKE, to decide
      whether it still fits before end_time. Wants a typical
      (average-based) value; using a worst-case number here would
      needlessly skip shots that would have fit.

    If `end_time` is given, each shot is checked predictively BEFORE
    firing: a shot is skipped (and the pass ends) if
    now + exposure + overhead would run past end_time. This is per-shot,
    not per-pass — a partial pass that fits several more exposures is
    much better than either overrunning the window or idling through it.
    For totality that end_time is the diamond_ring_out start, not C3:
    overrunning there means missing the C3 diamond ring entirely.

    `reverse` runs the ladder slowest-to-fastest, for palindrome looping
    (see run_sequence).

    Returns (confirmed, attempted, skipped)."""
    if overhead is None:
        overhead = DEFAULT_BRACKET_OVERHEAD
    if timeout_margin is None:
        timeout_margin = DEFAULT_BRACKET_TIMEOUT_MARGIN

    # ISO is handled per-shot below rather than once here: on a reversed
    # pass the first rung is the slowest (and most likely to carry an
    # override), so setting the base ISO up front would just be a wasted
    # PTP round-trip immediately overwritten.
    _apply_static_settings(camera, plan, include_iso=False)

    speeds = list(plan["shutter_speeds"])
    if reverse:
        speeds.reverse()

    # Only issue a set_config when a rung actually needs a different ISO —
    # every redundant PTP round-trip is time out of the window.
    current_iso = None

    attempted = 0
    confirmed = 0
    skipped = 0
    for speed in speeds:
        exposure = shutter_speed_seconds(speed)
        if end_time is not None:
            remaining = (end_time - _utcnow()).total_seconds()
            if remaining < exposure + overhead:
                # Skip just this shot, don't abandon the rest of the pass.
                # In a forward pass everything after is slower so they'll
                # skip too, but a REVERSED pass runs slowest-first — its
                # later shots are faster and may well still fit, and those
                # frames are worth having.
                skipped += 1
                continue
        iso = iso_for_step(plan, speed)
        if iso is not None and iso != current_iso:
            set_config(camera, "iso", str(iso))
            current_iso = iso
        set_config(camera, "shutterspeed", speed)
        attempted += 1
        timeout = exposure + timeout_margin
        if trigger_capture_one(camera, event_timeout=timeout, download_dir=download_dir):
            confirmed += 1
    return confirmed, attempted, skipped


def run_sequence(
    camera,
    plan: dict,
    end_time: dt.datetime | None = None,
    overhead: float | None = None,
    timeout_margin: float | None = None,
    download_dir: Path | None = None,
):
    """Dispatches on the plan's shape (see bracket_plans.py):

    - mode == "burst_single_exposure": fixed-exposure burst for
      plan['duration_seconds']. Deliberately ignores end_time — the
      burst's whole job is to straddle the contact moment, so its
      duration is fixed by the plan, and the schedule accounts for the
      overrun.
    - "interval_seconds" present: run one bracket, sleep the interval,
      repeat until end_time (or once, if end_time is None).
    - anything else (e.g. "repeat_until", like totality_bracket): loop the
      bracket back-to-back with no pause, until end_time (or once).

    Bracket plans with "palindrome": True alternate direction each pass
    (forward, reverse, forward, ...). At the seam this puts the two
    slowest exposures back to back — near-identical sky conditions and
    minimal corona rotation between them, which is exactly what you want
    for stacking the most delicate frames. It also halves the number of
    full-range shutter-speed jumps between consecutive shots.

    `overhead` and `timeout_margin` are passed through to
    run_bracket_once — see its docstring for why they're separate values.

    `download_dir`, if given, saves each frame's embedded JPEG preview
    there for focus-drift and fogging monitoring (see download_preview).
    Intended for the long filtered partial phases, where the interval has
    ample room; it is NOT applied to burst plans, whose windows are far
    too tight to spend on anything optional.
    """
    if plan.get("mode") == "burst_single_exposure":
        run_burst(camera, plan)
        return

    palindrome = plan.get("palindrome", False)
    interval = plan.get("interval_seconds")
    pass_index = 0

    while True:
        reverse = palindrome and (pass_index % 2 == 1)
        confirmed, attempted, skipped = run_bracket_once(
            camera,
            plan,
            end_time=end_time,
            overhead=overhead,
            timeout_margin=timeout_margin,
            reverse=reverse,
            download_dir=download_dir,
        )
        pass_index += 1
        log.info(
            "Bracket pass %d%s: %d/%d confirmed%s",
            pass_index,
            " (reversed)" if reverse else "",
            confirmed,
            attempted,
            f", {skipped} skipped for time" if skipped else "",
        )

        if end_time is None:
            break
        # Nothing fit at all — the window is spent. Without this the loop
        # would spin forever calling run_bracket_once for zero shots.
        if attempted == 0:
            break
        if _utcnow() >= end_time:
            break

        if interval:
            remaining = (end_time - _utcnow()).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(interval, remaining))