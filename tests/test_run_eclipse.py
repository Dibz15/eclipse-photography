import datetime as dt

from eclipse.run_eclipse import DEEP_CRESCENT_LEAD, build_schedule

CFG = {
    "date": "2026-08-12",
    "timings_utc": {
        "C1": "17:00:00",
        "C2": "18:00:00",
        "max": "18:01:00",
        "C3": "18:02:00",
        "C4": "19:00:00",
    },
    "camera": {"measured_max_fps": 1.5},
}


def test_schedule_is_chronological():
    schedule, fps = build_schedule(CFG)
    starts = [s for s, _, _, _ in schedule]
    assert starts == sorted(starts)
    assert fps == 1.5


def test_schedule_labels_in_expected_order():
    schedule, _ = build_schedule(CFG)
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
    schedule, _ = build_schedule(CFG)
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
    schedule, _ = build_schedule(cfg)
    windows = {label: (start, end) for start, end, label, _ in schedule}
    pre_start, _ = windows["deep_crescent_pre_totality"]
    c1 = dt.datetime.strptime(f"{cfg['date']} {cfg['timings_utc']['C1']}", "%Y-%m-%d %H:%M:%S")
    assert pre_start == c1


def test_totality_plan_gets_trimmed_for_short_totality():
    # C2->C3 is only 2 minutes here, at 1.5 fps -> well under the 14 stops
    # in the default totality_bracket, so it should come back trimmed.
    schedule, _ = build_schedule(CFG)
    totality_plan = next(plan for _, _, label, plan in schedule if label == "totality")
    assert len(totality_plan["shutter_speeds"]) <= 14


def test_missing_fps_leaves_totality_bracket_untrimmed():
    cfg = {**CFG, "camera": {"measured_max_fps": None}}
    schedule, fps = build_schedule(cfg)
    totality_plan = next(plan for _, _, label, plan in schedule if label == "totality")
    assert fps is None
    assert len(totality_plan["shutter_speeds"]) == 14