"""
python-gphoto2 wrapper for a Nikon D5200: connect, configure, and run the
bracket/burst sequences defined in bracket_plans.py.

`import gphoto2` is deliberately lazy (inside connect() / capture_one()),
so the rest of this project — timings, bracket trimming math, tests — stays
importable on a machine that doesn't have libgphoto2 installed, e.g. this
dev laptop before you've brewed it.
"""

from __future__ import annotations

import datetime as dt
import logging
import time

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
    hardware, and sleeps briefly per capture to simulate realistic PTP
    round-trip latency. Without this, a burst plan with no configured fps
    (e.g. measured_max_fps still null) would spin as fast as Python can
    loop for the full duration_seconds window — hundreds of thousands of
    "captures" in 15 seconds — since nothing here naturally throttles it
    the way a real camera's blocking capture() call would."""

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


# --------------------------------------------------------------------------
# Real camera
# --------------------------------------------------------------------------

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
        _force_capture_target_to_card(camera, override=capture_target)

    return camera


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


def trigger_capture_one(camera, event_timeout: float = 5.0) -> bool:
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
    in a burst shouldn't abort the rest of it."""
    if isinstance(camera, DryRunCamera):
        camera.trigger_capture()
        camera.wait_for_event(int(event_timeout * 1000))
        return True

    import gphoto2 as gp

    camera.trigger_capture()
    trigger_start = time.monotonic()
    while time.monotonic() - trigger_start < event_timeout:
        event_type, _event_data = camera.wait_for_event(3000)  # ms, per poll
        if event_type == gp.GP_EVENT_FILE_ADDED:
            return True
    return False


def _apply_static_settings(camera, plan: dict) -> None:
    if "iso" in plan:
        set_config(camera, "iso", str(plan["iso"]))
    if "aperture" in plan:
        set_config(camera, "aperture", plan["aperture"])


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


def run_bracket_once(camera, plan: dict) -> None:
    _apply_static_settings(camera, plan)
    for speed in plan["shutter_speeds"]:
        set_config(camera, "shutterspeed", speed)
        capture_one(camera)


def run_sequence(camera, plan: dict, end_time: dt.datetime | None = None):
    """Dispatches on the plan's shape (see bracket_plans.py):

    - mode == "burst_single_exposure": fixed-exposure burst for
      plan['duration_seconds'], regardless of end_time.
    - "interval_seconds" present: run one bracket, sleep the interval,
      repeat until end_time (or once, if end_time is None).
    - anything else (e.g. "repeat_until", like totality_bracket): loop the
      bracket back-to-back with no pause, until end_time (or once).
    """
    if plan.get("mode") == "burst_single_exposure":
        run_burst(camera, plan)
        return

    if "interval_seconds" in plan:
        while True:
            run_bracket_once(camera, plan)
            if end_time is None or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) >= end_time:
                break
            time.sleep(plan["interval_seconds"])
        return

    while True:
        run_bracket_once(camera, plan)
        if end_time is None or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) >= end_time:
            break