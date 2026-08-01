import numpy as np
import pytest

from eclipse.focus_check import (
    classify_score,
    crop_roi,
    laplacian_variance,
    pick_jpeg_choice,
    to_grayscale,
)


def test_to_grayscale_handles_already_gray():
    gray = np.array([[1.0, 2.0], [3.0, 4.0]])
    result = to_grayscale(gray)
    assert result.shape == (2, 2)
    assert np.array_equal(result, gray)


def test_to_grayscale_weights_rgb_correctly():
    # Pure green should weight higher than pure red/blue per Rec. 709
    red = np.zeros((1, 1, 3))
    red[0, 0, 0] = 255
    green = np.zeros((1, 1, 3))
    green[0, 0, 1] = 255
    assert to_grayscale(green)[0, 0] > to_grayscale(red)[0, 0]


def test_crop_roi_none_returns_unchanged():
    image = np.arange(100).reshape(10, 10)
    assert crop_roi(image, None) is image


def test_crop_roi_extracts_region():
    image = np.arange(100).reshape(10, 10)
    cropped = crop_roi(image, (2, 3, 4, 5))  # x, y, width, height
    assert cropped.shape == (5, 4)
    assert cropped[0, 0] == image[3, 2]


def test_classify_score_boundaries():
    assert classify_score(10, low_threshold=20, high_threshold=50) == "out_of_focus"
    assert classify_score(20, low_threshold=20, high_threshold=50) == "out_of_focus"
    assert classify_score(35, low_threshold=20, high_threshold=50) == "uncertain"
    assert classify_score(50, low_threshold=20, high_threshold=50) == "in_focus"
    assert classify_score(80, low_threshold=20, high_threshold=50) == "in_focus"


def test_pick_jpeg_choice_prefers_pure_jpeg_over_combos():
    choices = ["NEF (Raw)", "NEF+Fine", "JPEG Basic", "JPEG Fine"]
    assert pick_jpeg_choice(choices) in ("JPEG Basic", "JPEG Fine")


def test_pick_jpeg_choice_excludes_raw_jpeg_combos():
    choices = ["NEF+JPEG Fine", "NEF+JPEG Basic"]
    assert pick_jpeg_choice(choices) is None


def test_pick_jpeg_choice_returns_none_when_no_jpeg():
    assert pick_jpeg_choice(["NEF (Raw)"]) is None


# --------------------------------------------------------------------------
# The metric itself: does a sharp image actually score higher than a
# blurred version of the SAME image? This is the property the whole tool
# depends on, so it's worth testing directly rather than just the plumbing
# around it.
# --------------------------------------------------------------------------

def _synthetic_disk_with_limb(size: int = 200, radius: int = 70) -> np.ndarray:
    """A bright disk on a dark background, similar in shape to a filtered
    solar/lunar disk against the sky -- gives the Laplacian something with
    a real edge to respond to."""
    yy, xx = np.mgrid[0:size, 0:size]
    center = size // 2
    dist = np.sqrt((xx - center) ** 2 + (yy - center) ** 2)
    return np.where(dist <= radius, 200.0, 10.0)


def test_blurred_image_scores_lower_than_sharp_version():
    from scipy.ndimage import gaussian_filter

    sharp = _synthetic_disk_with_limb()
    blurred = gaussian_filter(sharp, sigma=4.0)

    sharp_score = laplacian_variance(sharp)
    blurred_score = laplacian_variance(blurred)

    assert sharp_score > blurred_score


def test_progressively_more_blur_progressively_lowers_score():
    from scipy.ndimage import gaussian_filter

    sharp = _synthetic_disk_with_limb()
    scores = [laplacian_variance(gaussian_filter(sharp, sigma=s)) for s in (0.0, 1.0, 2.0, 4.0, 8.0)]

    assert scores == sorted(scores, reverse=True)


def test_flat_image_scores_near_zero():
    flat = np.full((50, 50), 128.0)
    assert laplacian_variance(flat) == pytest.approx(0.0, abs=1e-6)