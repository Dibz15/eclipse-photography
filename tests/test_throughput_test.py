import pytest

from eclipse.throughput_test import (
    is_real_choice,
    observed_overheads,
    suggest_overhead,
    suggest_timeout_margin,
)


def test_real_choices_pass():
    assert is_real_choice("JPEG Fine")
    assert is_real_choice("NEF (Raw)")
    assert is_real_choice("NEF+Fine")


def test_placeholder_choices_are_filtered():
    assert not is_real_choice("Unknown value 0003")
    assert not is_real_choice("Unknown value 000d")


def test_observed_overheads_subtracts_exposure_time():
    # 1/2000 is ~0s exposure, so overhead ~= elapsed; "4" is 4s exposure.
    results = [("1/2000", 1.07, True), ("4", 5.25, True)]
    assert observed_overheads(results) == pytest.approx([1.0695, 1.25], abs=0.01)


def test_observed_overheads_excludes_unconfirmed():
    # An unconfirmed shot's elapsed time reflects giving up at the
    # timeout, not a real completion -- including it would wildly distort
    # both suggestions.
    results = [("1/2000", 1.07, True), ("4", 30.0, False)]
    assert len(observed_overheads(results)) == 1


def test_suggest_overhead_is_the_average():
    # Average, NOT max: this drives time ESTIMATION, and using the max
    # overestimated a real measured pass by ~48%.
    results = [("1/2000", 1.07, True), ("4", 5.25, True)]
    assert suggest_overhead(results) == pytest.approx(1.16, abs=0.01)


def test_suggest_timeout_margin_is_the_max_plus_safety():
    # Max, NOT average: this decides when to GIVE UP on a shot, so it
    # must exceed the worst observed case.
    results = [("1/2000", 1.07, True), ("4", 5.25, True)]
    assert suggest_timeout_margin(results, safety_margin=0.5) == pytest.approx(1.75, abs=0.01)


def test_timeout_margin_always_at_least_overhead():
    # The two are used for opposite purposes; the max-based one must
    # never come out below the average-based one, or shots would time out
    # before their own estimated duration elapsed.
    results = [("1/2000", 1.07, True), ("1/500", 2.4, True), ("4", 5.25, True)]
    assert suggest_timeout_margin(results) >= suggest_overhead(results)


def test_suggestions_none_when_nothing_confirmed():
    results = [("1/2000", 30.0, False)]
    assert suggest_overhead(results) is None
    assert suggest_timeout_margin(results) is None


def test_suggestions_none_for_empty_results():
    assert suggest_overhead([]) is None
    assert suggest_timeout_margin([]) is None