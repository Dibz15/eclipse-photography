import pytest

from eclipse.camera import _force_capture_target_to_card, pick_card_choice


def test_pick_card_choice_matches_case_insensitively():
    assert pick_card_choice(["Internal RAM", "Memory card"]) == "Memory card"
    assert pick_card_choice(["internal ram", "MEMORY CARD"]) == "MEMORY CARD"


def test_pick_card_choice_returns_none_when_no_card_option():
    assert pick_card_choice(["Internal RAM", "SDRAM"]) is None


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