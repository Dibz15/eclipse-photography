"""
Quick focus-check tool: captures a full-resolution frame, downloads it, and
scores its sharpness so you can judge focus without relying on the D5200's
LCD (dark while tethered — see the tethering discussion), and without
trusting AF lock to survive repeated captures (it doesn't on this camera
tier — every gphoto2 capture re-triggers AF regardless of button
assignment). Uses a full capture rather than capture_preview(), since the
low-res preview frame is too coarse to judge critical focus reliably.

The sharpness metric is the variance of the image's Laplacian — a
standard, widely-used blur-detection proxy: a sharp image has strong
high-frequency edge content, which shows up as high variance after a
Laplacian (edge) filter; a blurry image's transitions are smoothed out,
giving low variance. This is a RELATIVE metric, not an absolute one — the
"right" score for your exact lens/aperture/ISO/subject combination can
only be established by calibrating against your own in-focus and
out-of-focus reference shots, which is what --calibrate does. Don't trust
a hardcoded universal threshold here; there isn't one that means anything
across different gear and subjects.

Usage:
    uv run eclipse-focus-check --calibrate
        Walks you through capturing a deliberately out-of-focus reference
        shot, then an in-focus one, and saves thresholds (and the --roi
        used, if any) into config.yaml's focus_check block.

    uv run eclipse-focus-check --calibrate --focused-img focused.jpg --unfocused-img unfocused.jpg
        Calibrate using existing images from disk instead of live captures.

    uv run eclipse-focus-check
        Single capture + score + verdict, using the calibrated thresholds
        from config.yaml (or explicit --low-threshold/--high-threshold).
        Always saves the captured image so you can look yourself — the
        verdict is a hint, not a replacement for your eyes.

    uv run eclipse-focus-check --watch --interval 5
        Repeats the capture+score loop every N seconds — handy for the
        "nudge focus, check, nudge, check" workflow.

    uv run eclipse-focus-check --roi 1800,900,400,400
        Crops the sharpness metric to a region (x,y,width,height) before
        scoring — e.g. the limb of the disk, rather than the whole frame,
        which is mostly flat/featureless interior or blank sky. Worth
        experimenting with; there's no auto-detection here.
"""

from __future__ import annotations

import argparse
import datetime as dt
import time
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

from .camera import capture_one, connect, get_config_choices, is_raw_jpeg_combo_quality, set_config

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"

_LAPLACIAN_KERNEL = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)


# --------------------------------------------------------------------------
# Pure functions -- no camera/hardware needed, fully unit-testable with
# synthetic arrays.
# --------------------------------------------------------------------------

def to_grayscale(image: np.ndarray) -> np.ndarray:
    """Rec. 709 luminance-weighted RGB -> grayscale. Accepts (H, W, 3) or
    already-grayscale (H, W) arrays."""
    if image.ndim == 2:
        return image.astype(np.float64)
    weights = np.array([0.2126, 0.7152, 0.0722])
    return image[..., :3].astype(np.float64) @ weights


def crop_roi(image: np.ndarray, roi: tuple[int, int, int, int] | None) -> np.ndarray:
    """Crops to (x, y, width, height) if given, else returns the image
    unchanged."""
    if roi is None:
        return image
    x, y, w, h = roi
    return image[y : y + h, x : x + w]


def laplacian_variance(gray: np.ndarray) -> float:
    """Variance of the image's Laplacian -- see module docstring. Higher
    means sharper; not calibrated to any absolute scale."""
    from scipy.ndimage import convolve

    lap = convolve(gray, _LAPLACIAN_KERNEL, mode="reflect")
    return float(lap.var())


def calculate_thresholds(out_score: float, in_score: float) -> tuple[float, float] | None:
    """Calculate calibration thresholds from out-of-focus and in-focus scores.
    Returns (low_threshold, high_threshold) or None if scores are inverted."""
    if in_score <= out_score:
        return None
    margin = (in_score - out_score) * 0.2
    return out_score + margin, in_score - margin


def classify_score(score: float, low_threshold: float, high_threshold: float) -> str:
    """Three-way verdict rather than a single cutoff, since a metric this
    simple shouldn't claim more confidence than it has:
      - score <= low_threshold: "out_of_focus"
      - score >= high_threshold: "in_focus"
      - otherwise: "uncertain" -- check the saved image yourself
    """
    if score <= low_threshold:
        return "out_of_focus"
    if score >= high_threshold:
        return "in_focus"
    return "uncertain"


def pick_jpeg_choice(choices: list[str]) -> str | None:
    """Case-insensitive match for a JPEG-only quality choice (excludes
    RAW+JPEG combo choices — see camera.is_raw_jpeg_combo_quality for why
    those are unsafe with this project's trigger-based capture mechanism,
    not just historically fragile). Focus-checking doesn't need RAW:
    JPEG downloads much faster and is plenty for a sharpness read.
    Doesn't touch config.yaml's intended eclipse-day image_quality —
    run_eclipse.py re-applies that from config at its own startup
    regardless of what this leaves the camera set to."""
    return next(
        (c for c in choices if "jpeg" in c.lower() and not is_raw_jpeg_combo_quality(c)), None
    )


VERDICT_LABELS = {
    "in_focus": "Looks IN FOCUS (but check the image to confirm)",
    "uncertain": "UNCERTAIN -- check the saved image",
    "out_of_focus": "Looks OUT OF FOCUS -- check the saved image",
}


# --------------------------------------------------------------------------
# Hardware-touching functions
# --------------------------------------------------------------------------

def score_image_from_path(path: Path, roi: tuple[int, int, int, int] | None) -> float:
    """Score sharpness from an existing image file on disk."""
    image = np.asarray(Image.open(path))
    gray = to_grayscale(image)
    gray = crop_roi(gray, roi)
    return laplacian_variance(gray)


def capture_and_download(camera, out_dir: Path) -> Path:
    """Captures a full-resolution frame and downloads it to out_dir,
    returning the local path."""
    import gphoto2 as gp

    choices = get_config_choices(camera, "imagequality")
    jpeg_choice = pick_jpeg_choice(choices)
    if jpeg_choice:
        set_config(camera, "imagequality", jpeg_choice)

    file_path = capture_one(camera)
    cam_file = camera.file_get(file_path.folder, file_path.name, gp.GP_FILE_TYPE_NORMAL)

    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    local_path = out_dir / f"focus_check_{timestamp}_{file_path.name}"
    cam_file.save(str(local_path))
    return local_path


def score_image(path: Path, roi: tuple[int, int, int, int] | None) -> float:
    image = np.asarray(Image.open(path))
    gray = to_grayscale(image)
    gray = crop_roi(gray, roi)
    return laplacian_variance(gray)


def run_single_check(camera, out_dir: Path, roi):
    path = capture_and_download(camera, out_dir)
    score = score_image(path, roi)
    return path, score


def run_calibration(camera, out_dir: Path, roi):
    input(
        "Rack focus badly on purpose (turn it well off), then press Enter "
        "to capture the OUT-OF-FOCUS reference shot..."
    )
    out_path, out_score = run_single_check(camera, out_dir, roi)
    print(f"Out-of-focus reference: {out_score:.1f}  ({out_path})")

    input(
        "Now focus carefully (capture_preview + limb check works well), "
        "then press Enter to capture the IN-FOCUS reference shot..."
    )
    in_path, in_score = run_single_check(camera, out_dir, roi)
    print(f"In-focus reference:     {in_score:.1f}  ({in_path})")

    if in_score <= out_score:
        print(
            "\nWarning: the 'in-focus' shot scored lower than the "
            "'out-of-focus' one -- something's off (maybe focus wasn't "
            "actually better, or the ROI doesn't contain enough edge "
            "content to distinguish them). Not saving thresholds; try again."
        )
        return None

    margin = (in_score - out_score) * 0.2
    return out_score + margin, in_score - margin


def _parse_roi(text: str) -> tuple[int, int, int, int]:
    parts = [int(v) for v in text.split(",")]
    if len(parts) != 4:
        raise ValueError("--roi must be 4 comma-separated integers: x,y,width,height")
    return tuple(parts)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--port", default=None)
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--output-dir", default=None, help="defaults to config.yaml's output_dir")
    parser.add_argument("--roi", default=None, help="x,y,width,height crop for the sharpness metric")
    parser.add_argument("--low-threshold", type=float, default=None)
    parser.add_argument("--high-threshold", type=float, default=None)
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--focused-img", default=None, help="Path to a focused reference image (for --calibrate)")
    parser.add_argument("--unfocused-img", default=None, help="Path to an out-of-focus reference image (for --calibrate)")
    parser.add_argument("--watch", action="store_true", help="repeat the check every --interval seconds")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--write", action="store_true", help="Write calculated calibration to config.")
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    out_dir = Path(args.output_dir or cfg.get("output_dir", "./eclipse_frames")) / "focus_checks"

    if args.roi:
        roi = _parse_roi(args.roi)
    elif cfg.get("focus_check", {}).get("roi"):
        roi = tuple(cfg["focus_check"]["roi"])
    else:
        roi = None

    # Determine if we need a camera or can work entirely from disk
    needs_camera = not (args.calibrate and args.focused_img and args.unfocused_img)

    if needs_camera:
        camera = connect(args.port, capture_target=cfg.get("camera", {}).get("capture_target"))
    else:
        camera = None  # type: ignore[assignment]

    if args.calibrate:
        # File-based calibration from disk images
        if args.focused_img and args.unfocused_img:
            focused_path = Path(args.focused_img)
            unfocused_path = Path(args.unfocused_img)

            if not focused_path.exists():
                print(f"Error: focused image not found: {focused_path}")
                return
            if not unfocused_path.exists():
                print(f"Error: unfocused image not found: {unfocused_path}")
                return

            focused_score = score_image_from_path(focused_path, roi)
            unfocused_score = score_image_from_path(unfocused_path, roi)

            print(f"Unfocused reference score: {unfocused_score:.1f}  ({unfocused_path})")
            print(f"In-focus reference score:  {focused_score:.1f}  ({focused_path})")

            result = calculate_thresholds(unfocused_score, focused_score)
            if result is None:
                print(
                    "\nWarning: the 'in-focus' shot scored lower than the "
                    "'out-of-focus' one -- something's off (maybe focus wasn't "
                    "actually better, or the ROI doesn't contain enough edge "
                    "content to distinguish them). Not saving thresholds; try again."
                )
                return

            low_threshold, high_threshold = result
        else:
            # Original live-capture calibration workflow
            result = run_calibration(camera, out_dir, roi)  # type: ignore[arg-type]
            if result is None:
                return
            low_threshold, high_threshold = result

        print(f"\nSuggested thresholds: low={low_threshold:.1f}  high={high_threshold:.1f}")
        cfg.setdefault("focus_check", {})
        cfg["focus_check"]["low_threshold"] = round(low_threshold, 1)
        cfg["focus_check"]["high_threshold"] = round(high_threshold, 1)
        cfg["focus_check"]["roi"] = list(roi) if roi else None
        if args.write:
            with open(args.config, "w") as f:
                yaml.safe_dump(cfg, f, sort_keys=False)
            print(f"Saved thresholds (and ROI) into {args.config}")
        else:
            print(f"Calibration calculated but not written. Re-run with --write to write to {args.config}")
        return

    low_threshold = args.low_threshold
    high_threshold = args.high_threshold
    if low_threshold is None:
        low_threshold = cfg.get("focus_check", {}).get("low_threshold")
    if high_threshold is None:
        high_threshold = cfg.get("focus_check", {}).get("high_threshold")

    if low_threshold is None or high_threshold is None:
        print(
            "No thresholds set -- run `uv run eclipse-focus-check --calibrate` "
            "first, or pass --low-threshold/--high-threshold. Showing raw "
            "score only.\n"
        )

    while True:
        if args.focused_img and args.unfocused_img:
            print("Error: use --calibrate with --focused-img/--unfocused-img, not live checks")
            return
        path, score = run_single_check(camera, out_dir, roi)  # type: ignore[arg-type]
        line = f"score={score:.1f}  {path}"
        if low_threshold is not None and high_threshold is not None:
            verdict = classify_score(score, low_threshold, high_threshold)
            line += f"  -- {VERDICT_LABELS[verdict]}"
        print(line)
        if not args.watch:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()