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

from .camera import DEFAULT_BRACKET_OVERHEAD, shutter_speed_seconds

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
# Shutter speed is the D5500's ceiling and matches Espenak's Baily's Beads
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
    "trigger_offset_seconds": -7,  # start burst 8s before predicted C2
    "duration_seconds": 12,  # run through the actual contact moment
    "shutter_speed": "1/2000",  # D5500 ceiling; matches Espenak's Q12 value at f/11 ISO100
    "iso": 100,
    "aperture": "f/11",
    "mode": "burst_single_exposure",  # no bracket, just max fps at fixed settings
}

# Totality (C2->C3): FILTER OFF. This is the main bracket sequence —
# corona dynamic range is huge, so step across the full range.
#
# 8 steps, not a dense 1-stop ladder, because pass COUNT matters more than
# step density once you're shooting RAW. Measured on this project's camera
# (pure NEF, ~2.2s average overhead per shot) against a ~77-81s effective
# bracket window (a 92-96s totality, minus the ~7s diamond_ring_in
# overruns past C2 and the 8s diamond_ring_out needs before C3):
# ~21s/pass, so 3-4 complete passes. Three passes crosses a real
# threshold — median stacking needs >=3 samples per exposure level to
# reject outliers (satellites, aircraft, cosmic rays); two can only
# average. RAW's ~3 stops of recovery latitude makes 2-stop spacing safe.
#
# PER-STEP ISO (iso_overrides) on the slow end. This site's eclipse
# happens with the sun only a few degrees above the horizon — air mass
# ~17, meaning several stops of atmospheric extinction, so every tier of
# the corona needs far more exposure than Espenak's tables (which assume
# a high sun) suggest. Concretely, the ~1 solar-radius corona needs
# roughly 3-10s at ISO 200 f/11 here rather than a fraction of a second.
#
# But long exposures trail: on a fixed tripod at 300mm the sky drifts
# ~5.4 px/sec, so a 4s frame smears ~22 px. Raising ISO on just those
# rungs buys depth without the drift — 2s@ISO500 is 1000 ISO-seconds
# versus 4s@ISO200's 800, i.e. marginally DEEPER at half the trailing.
# It also tightens spacing where the corona gradient is steepest (the
# slow-end gaps become ~1.65 stops rather than 2.0). The fast rungs stay
# at ISO 200, where smear is already sub-pixel and higher gain would only
# cost headroom.
#
# Depth beyond the 2s rung comes from STACKING rather than a longer
# exposure: 3-4 frames at 2s@ISO500 stack to the noise of a single 6-8s
# exposure while each still carries only 2s of trailing.
#
# Extinction here is uncertain by +/-2 stops (it depends on haze and
# aerosol on the day), so the ladder is deliberately kept WIDE — ~13
# stops end to end — rather than optimised around any single predicted
# value. Breadth is the insurance against not knowing until you're there.
totality_bracket = {
    "shutter_speeds": ["1/2000", "1/500", "1/125", "1/30", "1/8", "1/2", "1", "2", "4"],
    "iso": 200,
    # Slow rungs only — see camera.iso_for_step. Keys must match
    # shutter_speeds exactly; run_eclipse.py refuses to start otherwise,
    # since a typo here would silently shoot that rung at base ISO.
    "iso_overrides": {"1": 320, "2": 500, "4": 500},
    "aperture": "f/11",
    # Alternate direction each pass (forward, reverse, forward...). At the
    # seam this puts the two slowest exposures back to back — ideal for
    # stacking the most delicate frames, since sky conditions and corona
    # rotation barely change between them — and halves the number of
    # full-range shutter-speed jumps. See camera.run_sequence.
    "palindrome": True,
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
    trigger_capture_one(). overhead=None uses
    camera.DEFAULT_BRACKET_OVERHEAD; config.yaml's
    camera.bracket_overhead (filled in by `eclipse-throughput
    --bracket-test --write`) overrides it with a value measured against
    your actual gear, and run_eclipse.py passes that through when set.

    Note this decides the bracket's COMPOSITION — which steps to keep, so
    they stay evenly spread across the full exposure range. It's
    complementary to camera.run_bracket_once()'s per-shot predictive
    check, which decides at runtime how much of the FINAL pass actually
    fits before the window closes. Without trim_to_fit the runtime check
    alone would just truncate the tail of each pass and you'd lose the
    slow end entirely; without the runtime check, trim_to_fit alone would
    either overrun the window or idle through the remainder of it.

    This replaced an earlier flat-fps estimate (frames_possible =
    totality_seconds * measured_max_fps), which assumed every step took
    the same time — not true once the bracket spans 1/2000 to 4 real
    seconds; the slow end costs far more per shot than the fast end.
    """
    if overhead is None:
        overhead = DEFAULT_BRACKET_OVERHEAD

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