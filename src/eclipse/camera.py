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

def connect(port: str | None = None, dry_run: bool = False):
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
    return camera


def set_config(camera, name: str, value):
    cfg = camera.get_config()
    node = cfg.get_child_by_name(name)
    node.set_value(value)
    camera.set_config(cfg)


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