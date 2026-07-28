"""
Run this AFTER the eclipse to pull every image off the camera's card into
config.yaml's output_dir.

This is intentionally NOT part of run_eclipse.py's timing-critical path —
see throughput_test.py / README for why downloading during totality costs
too much fps. This shells out to the `gphoto2` CLI tool (not the python
bindings) because its recursive --get-all-files handles folder walking for
you reliably.

Prerequisite: brew install gphoto2   (the CLI, separate from libgphoto2)

Usage:
    uv run python scripts/pull_from_card.py
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    out_dir = Path(cfg.get("output_dir", "./eclipse_frames")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Pulling all files from the camera into {out_dir} ...")
    subprocess.run(
        ["gphoto2", "--get-all-files", "--skip-existing"],
        cwd=out_dir,
        check=True,
    )
    print("Done.")


if __name__ == "__main__":
    main()
