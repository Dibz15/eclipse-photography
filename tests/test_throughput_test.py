import pytest

from eclipse.throughput_test import is_real_choice, suggest_overhead


def test_real_choices_pass():
    assert is_real_choice("JPEG Fine")
    assert is_real_choice("NEF (Raw)")
    assert is_real_choice("NEF+Fine")


def test_placeholder_choices_are_filtered():
    assert not is_real_choice("Unknown value 0003")
    assert not is_real_choice("Unknown value 000d")


def test_suggest_overhead_uses_worst_confirmed_case_plus_margin():
    # 1/2000 (~0s exposure) took 1.07s -> overhead ~1.07; "4" (4s exposure)
    # took 5.25s -> overhead ~1.25, the worst of the two.
    results = [("1/2000", 1.07, True), ("4", 5.25, True)]
    assert suggest_overhead(results, safety_margin=0.2) == 1.45


def test_suggest_overhead_ignores_unconfirmed_shots():
    # The unconfirmed shot's elapsed time reflects giving up at whatever
    # timeout was set, not a real completion -- including it would wildly
    # inflate the suggestion.
    results = [("1/2000", 1.07, True), ("4", 30.0, False)]
    assert suggest_overhead(results, safety_margin=0.2) == pytest.approx(1.27)


def test_suggest_overhead_none_when_nothing_confirmed():
    results = [("1/2000", 30.0, False)]
    assert suggest_overhead(results) is None


def test_suggest_overhead_none_for_empty_results():
    assert suggest_overhead([]) is None