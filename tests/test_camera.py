import sys
import types

import pytest

from eclipse.camera import (
    DryRunCamera,
    _force_capture_target_to_card,
    is_raw_jpeg_combo_quality,
    pick_card_choice,
    shutter_speed_seconds,
    trigger_capture_one,
)


def test_pick_card_choice_matches_case_insensitively():
    assert pick_card_choice(["Internal RAM", "Memory card"]) == "Memory card"
    assert pick_card_choice(["internal ram", "MEMORY CARD"]) == "MEMORY CARD"


def test_pick_card_choice_returns_none_when_no_card_option():
    assert pick_card_choice(["Internal RAM", "SDRAM"]) is None


def test_is_raw_jpeg_combo_quality_detects_combo_choices():
    assert is_raw_jpeg_combo_quality("NEF+Fine")
    assert is_raw_jpeg_combo_quality("NEF+Normal")
    assert is_raw_jpeg_combo_quality("NEF+Basic")


def test_is_raw_jpeg_combo_quality_false_for_single_format_choices():
    assert not is_raw_jpeg_combo_quality("NEF (Raw)")
    assert not is_raw_jpeg_combo_quality("JPEG Fine")
    assert not is_raw_jpeg_combo_quality("JPEG Basic")


def test_shutter_speed_seconds_fractional():
    assert shutter_speed_seconds("1/2000") == pytest.approx(0.0005)
    assert shutter_speed_seconds("1/500") == pytest.approx(0.002)
    assert shutter_speed_seconds("1/4") == pytest.approx(0.25)


def test_shutter_speed_seconds_whole_seconds():
    assert shutter_speed_seconds("1") == pytest.approx(1.0)
    assert shutter_speed_seconds("2") == pytest.approx(2.0)
    assert shutter_speed_seconds("4") == pytest.approx(4.0)


# --------------------------------------------------------------------------
# Lightweight stand-ins for gphoto2's CameraWidget tree, just enough to
# exercise _force_capture_target_to_card's get/set/verify round-trip
# without real hardware.
# --------------------------------------------------------------------------

class _FakeNode:
    def __init__(self, value, choices=None, sticky=False):
        self.value = value
        self._choices = choices or []
        self._sticky = sticky  # simulates a driver that silently ignores set_value

    def set_value(self, value):
        if not self._sticky:
            self.value = value

    def get_value(self):
        return self.value

    def get_choices(self):
        return self._choices


class _FakeConfig:
    def __init__(self, nodes):
        self._nodes = nodes

    def get_child_by_name(self, name):
        return self._nodes[name]


class FakeCamera:
    def __init__(self, node: _FakeNode):
        self._nodes = {"capturetarget": node}

    def get_config(self):
        return _FakeConfig(self._nodes)

    def set_config(self, cfg):
        pass  # the shared node object is already mutated via set_value()


def test_force_capture_target_auto_detects_and_applies():
    node = _FakeNode("Internal RAM", choices=["Internal RAM", "Memory card"])
    camera = FakeCamera(node)
    _force_capture_target_to_card(camera)
    assert node.get_value() == "Memory card"


def test_force_capture_target_respects_valid_override():
    node = _FakeNode("Internal RAM", choices=["Internal RAM", "SD Card Storage"])
    camera = FakeCamera(node)
    _force_capture_target_to_card(camera, override="SD Card Storage")
    assert node.get_value() == "SD Card Storage"


def test_force_capture_target_rejects_override_not_in_choices():
    node = _FakeNode("Internal RAM", choices=["Internal RAM", "Memory card"])
    camera = FakeCamera(node)
    with pytest.raises(RuntimeError, match="not one of"):
        _force_capture_target_to_card(camera, override="Nonexistent Choice")


def test_force_capture_target_raises_when_no_card_choice_and_no_override():
    node = _FakeNode("Internal RAM", choices=["Internal RAM", "SDRAM"])
    camera = FakeCamera(node)
    with pytest.raises(RuntimeError, match="No 'memory card' choice"):
        _force_capture_target_to_card(camera)


def test_force_capture_target_raises_when_camera_does_not_apply_the_change():
    # Simulates the real-world reports of some Nikon bodies/driver versions
    # silently not applying a capturetarget change.
    node = _FakeNode("Internal RAM", choices=["Internal RAM", "Memory card"], sticky=True)
    camera = FakeCamera(node)
    with pytest.raises(RuntimeError, match="still reports"):
        _force_capture_target_to_card(camera)


# --------------------------------------------------------------------------
# trigger_capture_one(): the fake camera's wait_for_event() returns from a
# pre-programmed sequence of (event_type, event_data) tuples, simulating
# the real camera's mix of irrelevant property-change events and an
# eventual FILE_ADDED (or none, to test the give-up path). A minimal fake
# `gphoto2` module is installed in sys.modules so the function's lazy
# `import gphoto2 as gp` resolves without the real package installed.
# --------------------------------------------------------------------------

class _FakeEventCamera:
    def __init__(self, events):
        self._events = list(events)
        self.trigger_count = 0

    def trigger_capture(self):
        self.trigger_count += 1

    def wait_for_event(self, timeout_ms):
        if self._events:
            return self._events.pop(0)
        return ("nothing_left", None)


@pytest.fixture
def fake_gphoto2_module(monkeypatch):
    fake_gp = types.ModuleType("gphoto2")
    fake_gp.GP_EVENT_FILE_ADDED = "file_added"
    monkeypatch.setitem(sys.modules, "gphoto2", fake_gp)
    return fake_gp


def test_trigger_capture_one_confirms_on_file_added(fake_gphoto2_module):
    camera = _FakeEventCamera([(fake_gphoto2_module.GP_EVENT_FILE_ADDED, "somefile")])
    assert trigger_capture_one(camera, event_timeout=1.0) is True
    assert camera.trigger_count == 1


def test_trigger_capture_one_skips_irrelevant_events_before_file_added(fake_gphoto2_module):
    # Several unrelated events before the real one -- exactly the pattern
    # that made the original (buggy) single-check version undercount real
    # captures the camera was actually completing.
    camera = _FakeEventCamera(
        [
            ("expprogram_changed", None),
            ("liveviewprohibit_changed", None),
            ("continousshootingcount_changed", None),
            (fake_gphoto2_module.GP_EVENT_FILE_ADDED, "somefile"),
        ]
    )
    assert trigger_capture_one(camera, event_timeout=1.0) is True


def test_trigger_capture_one_gives_up_after_event_timeout(fake_gphoto2_module):
    camera = _FakeEventCamera([("expprogram_changed", None)])  # never a real FILE_ADDED
    assert trigger_capture_one(camera, event_timeout=0.05) is False


def test_trigger_capture_one_dry_run_always_confirms():
    assert trigger_capture_one(DryRunCamera()) is True


# --------------------------------------------------------------------------
# Per-shot predictive cutting + palindrome. _RecordingCamera captures the
# shutter speeds actually set, so we can assert on the real firing order
# and on which shots got skipped for time.
# --------------------------------------------------------------------------

class _RecordingCamera:
    def __init__(self):
        self.speeds_set = []
        self._nodes = {}

    def get_config(self):
        return self

    def get_child_by_name(self, name):
        self._current = name
        return self

    def set_value(self, value):
        if self._current == "shutterspeed":
            self.speeds_set.append(value)

    def get_value(self):
        return None

    def set_config(self, cfg):
        pass


def test_run_bracket_once_fires_every_speed_when_no_end_time(monkeypatch):
    import eclipse.camera as cam

    monkeypatch.setattr(cam, "trigger_capture_one", lambda c, event_timeout=5.0: True)
    camera = _RecordingCamera()
    plan = {"shutter_speeds": ["1/2000", "1/500", "4"]}
    confirmed, attempted, skipped = cam.run_bracket_once(camera, plan)
    assert (confirmed, attempted, skipped) == (3, 3, 0)
    assert camera.speeds_set == ["1/2000", "1/500", "4"]


def test_run_bracket_once_reverse_fires_slowest_first(monkeypatch):
    import eclipse.camera as cam

    monkeypatch.setattr(cam, "trigger_capture_one", lambda c, event_timeout=5.0: True)
    camera = _RecordingCamera()
    plan = {"shutter_speeds": ["1/2000", "1/500", "4"]}
    cam.run_bracket_once(camera, plan, reverse=True)
    assert camera.speeds_set == ["4", "1/500", "1/2000"]


def test_run_bracket_once_skips_shots_that_will_not_fit(monkeypatch):
    import datetime as _dt

    import eclipse.camera as cam

    monkeypatch.setattr(cam, "trigger_capture_one", lambda c, event_timeout=5.0: True)
    camera = _RecordingCamera()
    plan = {"shutter_speeds": ["1/2000", "1/500", "4"]}
    # 5s left, overhead 1s: 1/2000 (~1s) and 1/500 (~1s) fit; "4" needs
    # 4+1=5s which is not < 5s remaining... it's exactly equal, so allow
    # a hair less to make the intent unambiguous.
    end_time = cam._utcnow() + _dt.timedelta(seconds=4.9)
    _confirmed, attempted, skipped = cam.run_bracket_once(
        camera, plan, end_time=end_time, overhead=1.0
    )
    assert attempted == 2
    assert skipped == 1
    assert camera.speeds_set == ["1/2000", "1/500"]


def test_run_bracket_once_skips_everything_when_no_time_left(monkeypatch):
    import datetime as _dt

    import eclipse.camera as cam

    monkeypatch.setattr(cam, "trigger_capture_one", lambda c, event_timeout=5.0: True)
    camera = _RecordingCamera()
    plan = {"shutter_speeds": ["1/2000", "4"]}
    end_time = cam._utcnow() - _dt.timedelta(seconds=1)  # already past
    confirmed, attempted, skipped = cam.run_bracket_once(
        camera, plan, end_time=end_time, overhead=1.0
    )
    assert (confirmed, attempted, skipped) == (0, 0, 2)
    assert camera.speeds_set == []


def test_run_sequence_palindrome_alternates_direction(monkeypatch):
    import datetime as _dt

    import eclipse.camera as cam

    monkeypatch.setattr(cam, "trigger_capture_one", lambda c, event_timeout=5.0: True)
    camera = _RecordingCamera()
    plan = {"shutter_speeds": ["1/2000", "1/500"], "palindrome": True}
    # Enough time for a few passes at ~0 real cost per shot.
    end_time = cam._utcnow() + _dt.timedelta(seconds=0.5)
    cam.run_sequence(camera, plan, end_time=end_time, overhead=0.0, timeout_margin=1.0)
    # First pass forward, second reversed -> the two slowest exposures
    # ("1/500") land back to back at the seam.
    assert camera.speeds_set[:4] == ["1/2000", "1/500", "1/500", "1/2000"]


def test_run_sequence_terminates_when_window_is_spent(monkeypatch):
    import datetime as _dt

    import eclipse.camera as cam

    monkeypatch.setattr(cam, "trigger_capture_one", lambda c, event_timeout=5.0: True)
    camera = _RecordingCamera()
    plan = {"shutter_speeds": ["1/2000", "4"]}
    # end_time already past: run_bracket_once fires nothing, and
    # run_sequence must not spin forever calling it.
    end_time = cam._utcnow() - _dt.timedelta(seconds=1)
    cam.run_sequence(camera, plan, end_time=end_time, overhead=1.0)
    assert camera.speeds_set == []


def test_run_bracket_once_reversed_still_fires_faster_shots_after_a_skip(monkeypatch):
    import datetime as _dt

    import eclipse.camera as cam

    monkeypatch.setattr(cam, "trigger_capture_one", lambda c, event_timeout=5.0: True)
    camera = _RecordingCamera()
    plan = {"shutter_speeds": ["1/2000", "1/500", "4"]}
    # Reversed order is 4, 1/500, 1/2000. With ~3s left and overhead 1s,
    # the 4s shot can't fit -- but the two fast ones that FOLLOW it in a
    # reversed pass still can, and skipping them would waste the window.
    end_time = cam._utcnow() + _dt.timedelta(seconds=3)
    _confirmed, attempted, skipped = cam.run_bracket_once(
        camera, plan, end_time=end_time, overhead=1.0, reverse=True
    )
    assert skipped == 1
    assert attempted == 2
    assert camera.speeds_set == ["1/500", "1/2000"]