from eclipse.bracket_plans import totality_bracket, trim_to_fit
from eclipse.camera import shutter_speed_seconds

OVERHEAD = 1.3  # matches trim_to_fit's default


def _estimated_seconds(speeds, overhead=OVERHEAD):
    return sum(shutter_speed_seconds(s) + overhead for s in speeds)


def test_trim_no_op_when_full_bracket_fits():
    trimmed = trim_to_fit(totality_bracket, totality_seconds=30)
    assert trimmed["shutter_speeds"] == totality_bracket["shutter_speeds"]


def test_trim_keeps_both_endpoints_when_they_fit():
    # 15s comfortably affords both endpoints (fastest ~1.3s, slowest
    # ~5.3s) plus several middle stops.
    trimmed = trim_to_fit(totality_bracket, totality_seconds=15)
    speeds = trimmed["shutter_speeds"]
    original = totality_bracket["shutter_speeds"]
    assert speeds[0] == original[0]
    assert speeds[-1] == original[-1]
    assert 2 < len(speeds) < len(original)


def test_trim_stays_within_the_time_budget():
    totality_seconds = 15
    trimmed = trim_to_fit(totality_bracket, totality_seconds=totality_seconds)
    assert _estimated_seconds(trimmed["shutter_speeds"]) <= totality_seconds


def test_trim_does_not_mutate_input():
    original_speeds = list(totality_bracket["shutter_speeds"])
    trim_to_fit(totality_bracket, totality_seconds=10)
    assert totality_bracket["shutter_speeds"] == original_speeds


def test_trim_returns_single_fastest_step_when_extremely_short():
    # Too short even for the single fastest shot's own overhead -- take
    # it anyway rather than return an empty bracket.
    trimmed = trim_to_fit(totality_bracket, totality_seconds=0.5)
    assert trimmed["shutter_speeds"] == [totality_bracket["shutter_speeds"][0]]


def test_trim_drops_the_slow_endpoint_when_totality_too_short_for_it():
    # The 4-second exposure alone costs ~5.3s (4s exposure + overhead) --
    # a 3s totality can't afford it even as the ONLY shot, so this must
    # not blindly keep both endpoints regardless of budget the way the
    # earlier flat-fps model implicitly assumed it always could.
    trimmed = trim_to_fit(totality_bracket, totality_seconds=3)
    assert "4" not in trimmed["shutter_speeds"]


def test_trim_picks_more_steps_for_longer_totality():
    short = trim_to_fit(totality_bracket, totality_seconds=8)
    longer = trim_to_fit(totality_bracket, totality_seconds=20)
    assert len(longer["shutter_speeds"]) >= len(short["shutter_speeds"])


def test_trim_respects_custom_overhead():
    # A much larger overhead should need more totality_seconds to fit the
    # same number of steps.
    default_overhead = trim_to_fit(totality_bracket, totality_seconds=15)
    generous_overhead = trim_to_fit(totality_bracket, totality_seconds=15, overhead=5.0)
    assert len(generous_overhead["shutter_speeds"]) <= len(default_overhead["shutter_speeds"])


def test_trim_overhead_none_matches_omitted_overhead():
    # None (e.g. from an unset config.yaml camera.bracket_overhead) must
    # behave identically to not passing overhead at all -- this is what
    # lets run_eclipse.py pass cfg.get(...) straight through without an
    # extra None-check at the call site.
    omitted = trim_to_fit(totality_bracket, totality_seconds=15)
    explicit_none = trim_to_fit(totality_bracket, totality_seconds=15, overhead=None)
    assert explicit_none["shutter_speeds"] == omitted["shutter_speeds"]


def test_trim_prunes_iso_overrides_for_removed_rungs():
    # Regression: trimming used to keep overrides naming rungs it had just
    # removed, which run_eclipse.py's startup validation then reported as
    # typos -- blocking a rehearsal with a short synthetic totality.
    plan = {
        "shutter_speeds": ["1/2000", "1/500", "1/8", "1", "2"],
        "iso": 200,
        "iso_overrides": {"1": 320, "2": 500},
    }
    trimmed = trim_to_fit(plan, totality_seconds=6)
    kept = set(trimmed["shutter_speeds"])
    assert set(trimmed["iso_overrides"]) <= kept
    assert plan["iso_overrides"] == {"1": 320, "2": 500}  # source untouched


def test_trim_keeps_overrides_for_surviving_rungs():
    plan = {
        "shutter_speeds": ["1/2000", "1/500", "1/8", "1", "2"],
        "iso": 200,
        "iso_overrides": {"2": 500},
    }
    trimmed = trim_to_fit(plan, totality_seconds=1000)  # no trimming needed
    assert trimmed["iso_overrides"] == {"2": 500}


def test_all_plans_have_valid_iso_overrides():
    from eclipse.bracket_plans import all_plans
    from eclipse.camera import unknown_iso_override_keys

    for name, plan in all_plans().items():
        assert unknown_iso_override_keys(plan) == [], name