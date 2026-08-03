import datetime as dt

import pytest

from eclipse import bracket_plans as bp
from eclipse.run_eclipse import DEEP_CRESCENT_LEAD, build_schedule, run

CFG = {
    "date": "2026-08-12",
    "timings_utc": {
        "C1": "17:00:00",
        "C2": "18:00:00",
        "max": "18:01:00",
        "C3": "18:02:00",
        "C4": "19:00:00",
    },
    "camera": {},
}


def test_schedule_is_chronological():
    schedule = build_schedule(CFG)
    starts = [s for s, _, _, _ in schedule]
    assert starts == sorted(starts)


def test_schedule_labels_in_expected_order():
    schedule = build_schedule(CFG)
    labels = [label for _, _, label, _ in schedule]
    assert labels == [
        "partial_pre_totality",
        "deep_crescent_pre_totality",
        "diamond_ring_in",
        "totality",
        "diamond_ring_out",
        "deep_crescent_post_totality",
        "partial_post_totality",
    ]


def test_deep_crescent_windows_are_three_minutes_by_default():
    schedule = build_schedule(CFG)
    windows = {label: (start, end) for start, end, label, _ in schedule}

    pre_start, pre_end = windows["deep_crescent_pre_totality"]
    assert pre_end - pre_start == DEEP_CRESCENT_LEAD

    post_start, post_end = windows["deep_crescent_post_totality"]
    assert post_end - post_start == DEEP_CRESCENT_LEAD


def test_deep_crescent_pre_totality_clamped_to_c1_when_partial_phase_is_short():
    cfg = {
        **CFG,
        "timings_utc": {**CFG["timings_utc"], "C1": "17:59:00"},  # only 1 min before C2
    }
    schedule = build_schedule(cfg)
    windows = {label: (start, end) for start, end, label, _ in schedule}
    pre_start, _ = windows["deep_crescent_pre_totality"]
    c1 = dt.datetime.strptime(f"{cfg['date']} {cfg['timings_utc']['C1']}", "%Y-%m-%d %H:%M:%S")
    assert pre_start == c1


def test_totality_ends_when_diamond_ring_out_starts_not_at_c3():
    # The bracket must stop in time for the C3 diamond ring, not run to
    # C3 itself -- overrunning here delayed diamond_ring_out past the
    # event entirely.
    schedule = build_schedule(CFG)
    windows = {label: (start, end) for start, end, label, _ in schedule}
    _, totality_end = windows["totality"]
    diamond_out_start, _ = windows["diamond_ring_out"]
    assert totality_end == diamond_out_start


def test_totality_plan_untrimmed_when_totality_is_long():
    # C2->C3 is 2 minutes here -- comfortably more than the 8-step
    # ladder's ~24s/pass needs, so no trimming.
    schedule = build_schedule(CFG)
    totality_plan = next(plan for _, _, label, plan in schedule if label == "totality")
    assert totality_plan["shutter_speeds"] == bp.totality_bracket["shutter_speeds"]


def test_totality_plan_gets_trimmed_for_short_totality():
    cfg = {**CFG, "timings_utc": {**CFG["timings_utc"], "C3": "18:00:30"}}  # 30s totality
    schedule = build_schedule(cfg)
    totality_plan = next(plan for _, _, label, plan in schedule if label == "totality")
    speeds = totality_plan["shutter_speeds"]
    full = bp.totality_bracket["shutter_speeds"]
    assert len(speeds) < len(full)
    # Endpoints preserved -- the extremes are what the bracket exists for.
    assert speeds[0] == full[0]
    assert speeds[-1] == full[-1]


def test_trim_accounts_for_diamond_ring_burst_overrun():
    # diamond_ring_in straddles C2 and runs past it, so the bracket's real
    # window is smaller than C3-C2. A schedule whose nominal totality
    # looks sufficient but whose EFFECTIVE window isn't must still trim.
    burst = bp.diamond_ring_burst
    overrun = burst["duration_seconds"] + burst["trigger_offset_seconds"]
    assert overrun > 0, "test assumes the burst overruns C2"

    schedule = build_schedule(CFG)
    totality_start, totality_end = next(
        (start, end) for start, end, label, _ in schedule if label == "totality"
    )
    c2 = dt.datetime.strptime(f"{CFG['date']} {CFG['timings_utc']['C2']}", "%Y-%m-%d %H:%M:%S")
    # The scheduled start is still C2 (the scheduler just runs late), but
    # the window used for trimming accounts for the overrun.
    assert totality_start == c2
    effective = (totality_end - (c2 + dt.timedelta(seconds=overrun))).total_seconds()
    assert effective < (totality_end - c2).total_seconds()


def test_run_refuses_combo_image_quality_before_scheduling_starts():
    # Confirmed directly on this project's camera: combo RAW+JPEG modes
    # fire two FILE_ADDED events per capture, which trigger_capture_one()
    # can silently misattribute across shutter speeds -- this must raise
    # before the schedule (and any real waiting) begins, not partway
    # through the event. dry_run=True still hits this check since it's
    # unconditional, ahead of the try/except around the real set_config().
    cfg = {**CFG, "camera": {"image_quality": "NEF+Fine"}}
    with pytest.raises(SystemExit, match="RAW\\+JPEG combo"):
        run(cfg, dry_run=True)