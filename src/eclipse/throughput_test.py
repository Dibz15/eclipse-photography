"""
Run this weeks before the eclipse, indoors, pointed at a lamp. You're
measuring camera + USB + card throughput, not exposure quality.

For every real image-quality choice your camera reports (JPEG variants,
NEF/RAW, RAW+JPEG combos), times N captures with and without downloading
each frame. These choice strings are discovered from your actual camera
rather than guessed — they vary by camera model and even libgphoto2
version (there are open libgphoto2 bug reports about exactly this for
several Nikon bodies), so don't hardcode one from a forum post.

For a D5200 over USB 2.0, a JPEG-only quality with no download is
typically your ceiling; a RAW/NEF quality with download is meaningfully
slower and may cap you around 1-2 fps rather than the ~5 fps the camera
claims mechanically, since gphoto2's PTP round-trip is the bottleneck, not
the shutter. Decide now whether you're leaving files on the card during
totality (fast, retrieve later with scripts/pull_from_card.py) or
downloading live (safer against card failure, slower).

Usage:
    uv run eclipse-throughput                       # test every quality choice
    uv run eclipse-throughput --list-image-quality   # just print your camera's
                                                      # imagequality choices and exit
    uv run eclipse-throughput --list-capture-target  # just print your camera's
                                                      # capturetarget choices and exit
    uv run eclipse-throughput --write                # also save the fastest
                                                      # no-download result into
                                                      # config.yaml as measured_max_fps
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import yaml

from .camera import capture_one, connect, get_config_choices, set_config

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"

_PLACEHOLDER_PREFIX = "Unknown value"


def is_real_choice(choice: str) -> bool:
    """Filters out libgphoto2's opaque placeholder entries: some camera/
    driver combinations report choices like "Unknown value 0003" for PTP
    property values the driver hasn't mapped to a label for that specific
    body yet (a known, documented libgphoto2 limitation — see the module
    docstring). These aren't meaningfully settable, so they're excluded."""
    return not choice.startswith(_PLACEHOLDER_PREFIX)


def time_captures(camera, n: int, download: bool) -> float:
    import gphoto2 as gp

    start = time.time()
    for _ in range(n):
        path = capture_one(camera)
        if download and path is not None:
            cam_file = camera.file_get(path.folder, path.name, gp.GP_FILE_TYPE_NORMAL)
            cam_file.get_data_and_size()
    return time.time() - start


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
    parser = argparse.ArgumentParser(description=__doc__)
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
        "--write",
        action="store_true",
        help="write the fastest no-download fps into config.yaml as camera.measured_max_fps",
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
    results = run(camera, n=args.n)

    print("\nSummary (fps):")
    for k, v in results.items():
        print(f"  {k:>30}: {v:.2f}")

    if args.write:
        nodownload = {k: v for k, v in results.items() if k.endswith("_nodownload")}
        best_key = max(nodownload, key=nodownload.get)
        best_fps = nodownload[best_key]
        cfg["camera"]["measured_max_fps"] = round(best_fps, 2)
        with open(args.config, "w") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)
        print(f"\nWrote measured_max_fps = {best_fps:.2f} (from {best_key!r}) into {args.config}")


if __name__ == "__main__":
    main()