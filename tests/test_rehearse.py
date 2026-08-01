import datetime as dt

import pytest

from eclipse.rehearse import build_synthetic_timings


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