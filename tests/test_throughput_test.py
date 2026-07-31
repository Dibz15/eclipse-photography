from eclipse.throughput_test import is_real_choice


def test_real_choices_pass():
    assert is_real_choice("JPEG Fine")
    assert is_real_choice("NEF (Raw)")
    assert is_real_choice("NEF+Fine")


def test_placeholder_choices_are_filtered():
    assert not is_real_choice("Unknown value 0003")
    assert not is_real_choice("Unknown value 000d")