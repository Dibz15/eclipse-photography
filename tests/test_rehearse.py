import datetime as dt
import os
import time

import pytest

from eclipse.rehearse import build_config_timings, build_synthetic_timings


def _to_dt(date_str: str, hms: str) -> dt.datetime:
    return dt.datetime.strptime(f"{date_str} {hms}", "%Y-%m-%d %H:%M:%S")


def test_produces_chronological_order():
    date_str, timings = build_synthetic_timings(start_in=10, partial_seconds=20, totality_seconds=30)
    c1, c2, c3, c4 = (_to_dt(date_str, timings[k]) for k in ("C1", "C2", "C3", "C4"))
    assert c1 < c2 < c3 < c4


def test_respects_requested_durations():
    date_str, timings = build_synthetic_timings(start_in=10, partial_seconds=20, totality_seconds=30)
    c1, c2, c3, c4 = (_to_dt(date_str, timings[k]) for k in ("C1", "C2", "C3", "C4"))
    # abs=1 to absorb HH:MM:SS string truncation of sub-second precision
    assert (c2 - c1).total_seconds() == pytest.approx(20, abs=1)
    assert (c3 - c2).total_seconds() == pytest.approx(30, abs=1)
    assert (c4 - c3).total_seconds() == pytest.approx(20, abs=1)


def test_max_falls_between_c2_and_c3():
    date_str, timings = build_synthetic_timings(start_in=10, partial_seconds=20, totality_seconds=30)
    c2, max_t, c3 = (_to_dt(date_str, timings[k]) for k in ("C2", "max", "C3"))
    assert c2 < max_t < c3


def test_uses_injected_now_deterministically():
    fixed_now = dt.datetime(2026, 8, 12, 10, 0, 0)
    date_str, timings = build_synthetic_timings(
        start_in=5, partial_seconds=10, totality_seconds=15, now=fixed_now
    )
    assert date_str == "2026-08-12"
    assert timings["C1"] == "10:00:05"
    assert timings["C2"] == "10:00:15"
    assert timings["C3"] == "10:00:30"
    assert timings["C4"] == "10:00:40"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start_in": 0, "partial_seconds": 20, "totality_seconds": 30},
        {"start_in": -5, "partial_seconds": 20, "totality_seconds": 30},
        {"start_in": 10, "partial_seconds": 0, "totality_seconds": 30},
        {"start_in": 10, "partial_seconds": 20, "totality_seconds": 0},
    ],
)
def test_rejects_non_positive_durations(kwargs):
    with pytest.raises(ValueError):
        build_synthetic_timings(**kwargs)


def test_raises_on_midnight_crossing():
    fixed_now = dt.datetime(2026, 8, 12, 23, 59, 0)  # 1 minute before UTC midnight
    with pytest.raises(ValueError, match="midnight"):
        build_synthetic_timings(start_in=10, partial_seconds=60, totality_seconds=60, now=fixed_now)


def test_does_not_raise_when_comfortably_within_the_day():
    fixed_now = dt.datetime(2026, 8, 12, 10, 0, 0)
    date_str, _timings = build_synthetic_timings(
        start_in=10, partial_seconds=60, totality_seconds=60, now=fixed_now
    )
    assert date_str == "2026-08-12"


# --------------------------------------------------------------------------
# --from-config: real timings_utc re-dated to today/tomorrow
# --------------------------------------------------------------------------

REAL_CFG = {
    "timings_utc": {
        "C1": "17:01:23",
        "C2": "18:23:47",
        "max": "18:24:34",
        "C3": "18:25:21",
        "C4": "19:48:02",
    }
}


def test_from_config_uses_today_when_c1_still_ahead():
    now = dt.datetime(2026, 8, 12, 9, 0, 0)  # well before C1
    date_str, timings, rolled = build_config_timings(REAL_CFG, now=now)
    assert date_str == "2026-08-12"
    assert rolled is False
    assert timings == REAL_CFG["timings_utc"]  # verbatim, times of day preserved


def test_from_config_uses_today_when_mid_event():
    # Starting partway through is useful, not an error: run_eclipse skips
    # phases whose windows have closed and stale cues are dropped, so
    # there is no reason to wait a whole day to rehearse the rest.
    now = dt.datetime(2026, 8, 12, 18, 0, 0)  # after C1, before C4
    date_str, _timings, rolled = build_config_timings(REAL_CFG, now=now)
    assert date_str == "2026-08-12"
    assert rolled is False


def test_from_config_uses_today_just_before_c4():
    now = dt.datetime(2026, 8, 12, 19, 48, 1)  # C4 is 19:48:02
    date_str, _t, rolled = build_config_timings(REAL_CFG, now=now)
    assert date_str == "2026-08-12"
    assert rolled is False


def test_from_config_rolls_only_once_c4_has_passed():
    now = dt.datetime(2026, 8, 12, 23, 0, 0)
    date_str, _t, rolled = build_config_timings(REAL_CFG, now=now)
    assert date_str == "2026-08-13"
    assert rolled is True


def test_from_config_preserves_real_durations():
    now = dt.datetime(2026, 8, 12, 9, 0, 0)
    date_str, timings, _ = build_config_timings(REAL_CFG, now=now)

    def at(k):
        return dt.datetime.strptime(f"{date_str} {timings[k]}", "%Y-%m-%d %H:%M:%S")

    assert (at("C3") - at("C2")).total_seconds() == 94  # the real totality
    assert (at("C4") - at("C1")).total_seconds() == 9_999


def test_from_config_requires_timings_block():
    with pytest.raises(ValueError, match="no timings_utc"):
        build_config_timings({}, now=dt.datetime(2026, 8, 12, 9, 0, 0))


def test_from_config_requires_all_contacts():
    cfg = {"timings_utc": {"C1": "17:00:00", "C2": "18:00:00"}}
    with pytest.raises(ValueError, match="missing"):
        build_config_timings(cfg, now=dt.datetime(2026, 8, 12, 9, 0, 0))


def test_from_config_rejects_midnight_crossing():
    cfg = {"timings_utc": {k: v for k, v in zip(
        ("C1", "C2", "max", "C3", "C4"),
        ("23:00:00", "23:40:00", "23:41:00", "23:42:00", "00:30:00"), strict=True)}}
    with pytest.raises(ValueError, match="midnight"):
        build_config_timings(cfg, now=dt.datetime(2026, 8, 12, 9, 0, 0))


def test_from_config_independent_of_os_timezone():
    # "today" means today in UTC, decided from the absolute clock -- never
    # from the machine's local timezone.
    if not hasattr(time, "tzset"):
        pytest.skip("tzset unavailable on this platform")
    now = dt.datetime(2026, 8, 12, 9, 0, 0)
    original = os.environ.get("TZ")
    try:
        results = []
        for tz in ("UTC", "Europe/London", "Pacific/Kiritimati", "America/Denver"):
            os.environ["TZ"] = tz
            time.tzset()
            results.append(build_config_timings(REAL_CFG, now=now))
        assert all(r == results[0] for r in results)
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()