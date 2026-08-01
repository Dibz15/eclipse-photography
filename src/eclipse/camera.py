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
    hardware."""

    def get_config(self):
        return _DryRunConfig()

    def set_config(self, cfg):
        pass

    def capture(self, *_args, **_kwargs):
        log.info("[dry-run] capture()")


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


def _apply_static_settings(camera, plan: dict) -> None:
    if "iso" in plan:
        set_config(camera, "iso", str(plan["iso"]))
    if "aperture" in plan:
        set_config(camera, "aperture", plan["aperture"])


def run_burst(camera, plan: dict, fps: float | None = None) -> int:
    """diamond_ring_burst-style plan: fixed exposure, max fps, for
    plan['duration_seconds']. Returns the number of frames captured."""
    _apply_static_settings(camera, plan)
    set_config(camera, "shutterspeed", plan["shutter_speed"])

    delay = 1.0 / fps if fps else 0.0
    end = time.monotonic() + plan["duration_seconds"]
    n = 0
    while time.monotonic() < end:
        capture_one(camera)
        n += 1
        if delay:
            time.sleep(delay)
    log.info("Burst complete: %d frames", n)
    return n


def run_bracket_once(camera, plan: dict) -> None:
    _apply_static_settings(camera, plan)
    for speed in plan["shutter_speeds"]:
        set_config(camera, "shutterspeed", speed)
        capture_one(camera)


def run_sequence(camera, plan: dict, end_time: dt.datetime | None = None, fps: float | None = None):
    """Dispatches on the plan's shape (see bracket_plans.py):

    - mode == "burst_single_exposure": fixed-exposure burst for
      plan['duration_seconds'], regardless of end_time.
    - "interval_seconds" present: run one bracket, sleep the interval,
      repeat until end_time (or once, if end_time is None).
    - anything else (e.g. "repeat_until", like totality_bracket): loop the
      bracket back-to-back with no pause, until end_time (or once).
    """
    if plan.get("mode") == "burst_single_exposure":
        run_burst(camera, plan, fps=fps)
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