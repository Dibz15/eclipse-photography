"""
Standalone camera connectivity check and recovery.

Exists because the failure that actually matters mid-eclipse isn't a
crash — it's not being able to get the camera back after unplugging it to
refocus or re-frame. When that happens you want one command that tells
you whether the camera is reachable and, if not, what is holding it,
without starting a whole schedule.

Usage:
    uv run eclipse-camera-check              # is the camera reachable?
    uv run eclipse-camera-check --fix        # also stop the macOS PTP
                                             # daemon and retry
    uv run eclipse-camera-check --watch      # keep checking until it
                                             # connects (replug while
                                             # this runs)

On macOS a camera is claimed by the system PTP daemon the moment it
enumerates, which shows up as [-53] Could not claim the USB device.
[-105] Unknown model instead means the device is present but not yet
identified — that one resolves by waiting, so --watch is the right
answer for it.
"""

from __future__ import annotations

import argparse
import logging
import platform
import subprocess
import time

from .camera import (
    PTP_DAEMON_NAMES,
    connect,
    free_macos_usb_claim,
    is_usb_claim_error,
    list_config_names,
    release,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("eclipse.camera_check")


def other_holders() -> list[str]:
    """Photo apps that would hold the camera and that killing the PTP
    daemon cannot free.

    Matches only real application bundles. An earlier version grepped raw
    `ps` output for names like "Image Capture", which matches macOS's own
    always-present support components (Image Capture Extension) and
    produced confident false positives."""
    if platform.system() != "Darwin":
        return []
    suspects = {
        "Image Capture.app": "Image Capture",
        "Photos.app": "Photos",
        "Adobe Lightroom": "Lightroom",
        "Capture One": "Capture One",
    }
    try:
        out = subprocess.run(
            ["ps", "-Ao", "args"], capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except Exception:  # noqa: BLE001 - diagnostic only
        return []
    found = []
    for marker, label in suspects.items():
        # Require the app bundle's own executable path, not a support
        # process living under /System/Library/Image Capture/.
        if f"{marker}/Contents/MacOS/" in out:
            found.append(label)
    return found


def try_connect() -> tuple[bool, str]:
    try:
        camera = connect(attempts=1)
        names = list_config_names(camera)
        release(camera)
        return True, f"connected; {len(names)} config nodes visible"
    except Exception as e:  # noqa: BLE001 - reporting, not handling
        return False, str(e)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="stop the macOS PTP daemon before retrying (harmless elsewhere)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="keep retrying until the camera connects — replug while this runs",
    )
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    if args.fix:
        if free_macos_usb_claim(force=True):
            print("Stopped the macOS PTP daemon; giving the device a moment...")
            time.sleep(3.0)
        elif platform.system() == "Darwin":
            print(f"No {'/'.join(PTP_DAEMON_NAMES)} process was running.")

    deadline = time.monotonic() + (args.timeout if args.watch else 0)
    while True:
        ok, detail = try_connect()
        if ok:
            print(f"OK — {detail}")
            return
        print(f"FAILED — {detail}")

        if is_usb_claim_error(detail if isinstance(detail, BaseException) else Exception(detail)):
            holders = other_holders()
            if holders:
                print(f"  These photo apps are running and may hold it: {', '.join(holders)}")
            print(
                "  Try --fix to stop the macOS PTP daemon. If that doesn't help, "
                "power-cycle the CAMERA (off, unplug, wait, on, replug) — "
                "alternating -53/-105 usually means its USB stack is wedged, "
                "which no amount of host-side retrying will clear."
            )
        elif "-105" in detail:
            print("  Device present but not identified yet — this usually clears on its own.")
        else:
            print("  Check the cable and that the camera is powered on.")

        if not args.watch or time.monotonic() >= deadline:
            raise SystemExit(1)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()