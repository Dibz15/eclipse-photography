"""
Exposure sequences for each eclipse phase. Plain dicts, not classes/
dataclasses, on purpose — day-before, you might want to hand-trim
totality_bracket to fit your site-confirmed totality duration. Do that with
trim_to_fit() below rather than editing this file under time pressure.

Filter safety note: partial_phase_bracket, deep_crescent_bracket, and
diamond_ring_burst assume a solar filter is ON the lens; totality_bracket
assumes it is OFF. Filter comes off right at C2 (or just before, per your
own safety procedure) and back on right after C3 — see run_eclipse.py.

Aperture is locked to f/11 in every phase below (tested sharper than f/8
for this camera+lens on the moon) so exposure is fully reproducible
regardless of the lens's last manual setting. All shutter speeds below are
computed from Fred Espenak's Solar Eclipse Exposure Guide
(t = f^2 / (ISO * 2^Q)) for f/11 at each phase's ISO, then rounded to the
nearest standard shutter speed. Assumes a ~ND 5.0 (100,000x) solar filter —
confirm against your actual filter's rated density.
"""

from __future__ import annotations

from .camera import shutter_speed_seconds

# Partial phases (C1->C2 and C3->C4): SOLAR FILTER ON.
# Slow cadence is fine — the sun's disk brightness barely changes minute to
# minute, EXCEPT in the final few minutes before/after totality — see
# deep_crescent_bracket below.
partial_phase_bracket = {
    "interval_seconds": 300,  # one bracket every 5 min
    "shutter_speeds": ["1/500", "1/250", "1/125"],  # centered ~1/250s, f/11 ISO100
    "iso": 100,
    "aperture": "f/11",
}

# Final ~3 minutes before C2 and after C3: the crescent is thin enough that
# solar limb darkening requires roughly 2 stops MORE exposure than the rest
# of the partial phase (per Espenak: "no exposure compensation is needed
# except for the narrow crescent phases, which require two more stops").
# Denser cadence than partial_phase_bracket since the crescent is changing
# faster here. Still filtered — this is not the diamond ring/beads moment,
# which diamond_ring_burst covers separately once the filter comes off.
deep_crescent_bracket = {
    "interval_seconds": 30,
    "shutter_speeds": ["1/125", "1/60", "1/30"],  # ~2 stops brighter than partial_phase_bracket
    "iso": 100,
    "aperture": "f/11",
}

# Diamond ring / Baily's beads: happens in the ~5-15 seconds immediately
# before C2 and immediately after C3. FILTER COMES OFF right at C2 (or just
# before, per your own safety procedure) and back ON right after C3.
# Bright, fast-changing — burst as fast as possible, fixed exposure (no
# bracket) so you don't waste frames on wrong settings. Fires via
# trigger_capture_one() (see camera.py), not plain capture_one() —
# confirmed on this project's camera at ~0.51fps sustained (single-format
# NEF) once warmed up, vs. ~0.4-0.5fps for plain capture() at the same
# quality — so this 15-second window gets roughly 7-8 frames.
#
# Shutter speed is the D5200's ceiling and matches Espenak's Baily's Beads
# (Q=12) value at f/11/ISO100 almost exactly — deliberately NOT biased
# brighter the way a single exposure would be. Because this is a burst,
# highlight protection on the bead matters more than any one frame's
# corona visibility: the surrounding corona/chromosphere (only 1-3 stops
# fainter) can be recovered by stacking multiple burst frames in post,
# without any frame clipping the bead itself. RAW is worth it here for
# that stacking headroom — set camera.image_quality in config.yaml to your
# camera's PLAIN NEF/RAW choice (see `eclipse-throughput
# --list-image-quality`) -- NOT a "+JPEG"/"+Fine"/"+Normal" combo choice:
# run_eclipse.py refuses those outright, since each capture in combo mode
# fires two FILE_ADDED events and trigger_capture_one() can silently
# confirm the wrong shutter speed as a result (confirmed directly on this
# project's camera — see camera.is_raw_jpeg_combo_quality).
diamond_ring_burst = {
    "trigger_offset_seconds": -8,  # start burst 8s before predicted C2
    "duration_seconds": 15,  # run through the actual contact moment
    "shutter_speed": "1/4000",  # D5200 ceiling; matches Espenak's Q12 value at f/11 ISO100
    "iso": 100,
    "aperture": "f/11",
    "mode": "burst_single_exposure",  # no bracket, just max fps at fixed settings
}

# Totality (C2->C3): FILTER OFF. This is the main bracket sequence —
# corona dynamic range is huge, so step across the full range. A clean
# 1-stop ladder from 1/2000 (chromosphere/prominence territory, though the
# true flash is already covered by diamond_ring_burst) down to 4s (matches
# Espenak's Q=-3 outer-corona/streamer value at f/11 ISO200). ISO 200
# matches Espenak's own stated personal practice (ISO 200, f/9, 1/1000s to
# 1+s) closely.
totality_bracket = {
    "shutter_speeds": [
        "1/2000", "1/1000", "1/500", "1/250", "1/125",
        "1/60", "1/30", "1/15", "1/8", "1/4", "1/2", "1", "2", "4",
    ],
    "iso": 200,
    "aperture": "f/11",
    "repeat_until": "C3",  # loop the sequence back-to-back until totality ends
}


def _select_indices(n_total: int, k: int) -> list[int]:
    """Evenly-spaced indices spanning a sequence of length n_total,
    always including both endpoints once k >= 2 — the fastest/slowest
    stops are the ones worth keeping most. May return fewer than k
    indices if rounding collisions occur for a given n_total/k
    combination; that only means slightly less total time than budgeted
    for, never more."""
    if k >= n_total:
        return list(range(n_total))
    if k < 2:
        return [0]
    return sorted({round(i * (n_total - 1) / (k - 1)) for i in range(k)})


def trim_to_fit(bracket_plan: dict, totality_seconds: float, overhead: float | None = None) -> dict:
    """If the full bracket won't complete once within totality_seconds,
    keep the outer (fastest/slowest) stops plus as many evenly-spaced
    middle stops as will fit, rather than run out of time mid-loop.
    Returns a new dict; never mutates the input.

        totality_seconds = C3 - C2   (known once you're at your site)

    Each kept step's estimated real time is shutter_speed_seconds(speed)
    + overhead — the shutter has to stay open for the full exposure time
    (from a fraction of a second up to totality_bracket's own 4-second
    stop), plus per-shot processing/confirmation overhead via
    trigger_capture_one(). overhead=None uses the built-in default
    (1.3s) — set from `eclipse-throughput --bracket-test` results against
    this project's original camera: ~1.1-1.35s across all 14 real
    totality_bracket speeds, consistent regardless of exposure length.
    That's specific to one camera/card/cable combination, though —
    config.yaml's camera.bracket_overhead (filled in by
    `--bracket-test --write`) overrides this with a value measured
    against your actual gear; run_eclipse.py passes that through when
    set.

    This replaced an earlier flat-fps estimate (frames_possible =
    totality_seconds * measured_max_fps), which assumed every step took
    the same time — not true once the bracket spans 1/2000 to 4 real
    seconds; the slow end costs far more per shot than the fast end.
    """
    if overhead is None:
        overhead = 1.3

    speeds = bracket_plan["shutter_speeds"]
    n = len(speeds)

    def total_seconds_for(k: int) -> float:
        return sum(shutter_speed_seconds(speeds[i]) + overhead for i in _select_indices(n, k))

    if total_seconds_for(n) <= totality_seconds:
        return bracket_plan

    for k in range(n - 1, 0, -1):
        if total_seconds_for(k) <= totality_seconds:
            trimmed = [speeds[i] for i in _select_indices(n, k)]
            return {**bracket_plan, "shutter_speeds": trimmed}

    # Even the single fastest shot doesn't fit — take it anyway rather
    # than return an empty bracket.
    return {**bracket_plan, "shutter_speeds": [speeds[0]]}