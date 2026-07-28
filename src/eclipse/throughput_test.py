"""
Run this weeks before the eclipse, indoors, pointed at a lamp. You're
measuring camera + USB + card throughput, not exposure quality.

Runs all four configurations from the workflow:
  1. JPEG, no download   (fastest — best-case ceiling)
  2. RAW,  no download
  3. JPEG, with download
  4. RAW,  with download

For a D5200 over USB 2.0, JPEG-no-download is typically your ceiling;
RAW-with-download is meaningfully slower and may cap you around 1-2 fps
rather than the ~5 fps the camera claims mechanically, since gphoto2's PTP
round-trip is the bottleneck, not the shutter. Decide now whether you're
leaving files on the card during totality (fast, retrieve later with
scripts/pull_from_card.py) or downloading live (safer against card
failure, slower).

Usage:
    uv run eclipse-throughput                 # print results
    uv run eclipse-throughput --write         # also save JPEG/no-download
                                               # fps into config.yaml
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import yaml

from .camera import capture_one, connect, set_config

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"


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
    results: dict[str, float] = {}
    for fmt in ("JPEG Fine", "RAW"):
        set_config(camera, "imageformat", fmt)
        set_config(camera, "shutterspeed", "1/500")
        for download in (False, True):
            elapsed = time_captures(camera, n, download)
            fps = n / elapsed
            key = f"{fmt}_{'download' if download else 'nodownload'}"
            results[key] = fps
            print(
                f"{n} captures, {fmt}, download={download}: "
                f"{elapsed:.2f}s -> {fps:.2f} fps"
            )
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=None)
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument(
        "--write",
        action="store_true",
        help="write the JPEG/no-download fps into config.yaml as camera.measured_max_fps",
    )
    args = parser.parse_args()

    camera = connect(args.port)
    results = run(camera, n=args.n)

    print("\nSummary (fps):")
    for k, v in results.items():
        print(f"  {k:>20}: {v:.2f}")

    if args.write:
        with open(args.config) as f:
            cfg = yaml.safe_load(f)
        cfg["camera"]["measured_max_fps"] = round(results["JPEG Fine_nodownload"], 2)
        with open(args.config, "w") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)
        print(f"\nWrote measured_max_fps into {args.config}")


if __name__ == "__main__":
    main()
