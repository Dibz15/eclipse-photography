import datetime as dt

import pytest

from eclipse.cues import (
    build_cue_schedule,
    cue_datetime,
    parse_cue_offset,
    speak,
    start_cue_thread,
)

CONTACTS = {
    "C1": dt.datetime(2026, 8, 12, 17, 38, 16),
    "C2": dt.datetime(2026, 8, 12, 18, 31, 14),
    "max": dt.datetime(2026, 8, 12, 18, 32, 2),
    "C3": dt.datetime(2026, 8, 12, 18, 32, 51),
    "C4": dt.datetime(2026, 8, 12, 19, 22, 39),
}


def test_parse_offset_negative_and_positive():
    assert parse_cue_offset("C2-300") == ("C2", -300.0)
    assert parse_cue_offset("C3+2") == ("C3", 2.0)


def test_parse_offset_bare_contact_is_zero():
    assert parse_cue_offset("C2") == ("C2", 0.0)
    assert parse_cue_offset("max") == ("max", 0.0)


def test_parse_offset_is_case_and_space_tolerant():
    assert parse_cue_offset(" c2 - 30 ") == ("C2", -30.0)


@pytest.mark.parametrize("bad", ["C5-10", "totality-30", "C2*30", "", "-30"])
def test_parse_offset_rejects_nonsense(bad):
    # Fails loudly at startup rather than silently never firing.
    with pytest.raises(ValueError):
        parse_cue_offset(bad)


def test_cue_datetime_applies_the_offset():
    assert cue_datetime("C2-300", CONTACTS) == CONTACTS["C2"] - dt.timedelta(seconds=300)
    assert cue_datetime("C3+8", CONTACTS) == CONTACTS["C3"] + dt.timedelta(seconds=8)


def test_cue_datetime_rejects_unknown_contact():
    with pytest.raises(ValueError, match="unknown contact"):
        cue_datetime("C4-10", {"C2": CONTACTS["C2"]})


def test_build_schedule_sorts_by_time():
    specs = [
        {"at": "C3+8", "say": "filter on"},
        {"at": "C2-600", "say": "ten minutes"},
        {"at": "C2", "say": "totality"},
    ]
    times = [c[0] for c in build_cue_schedule(specs, CONTACTS)]
    assert times == sorted(times)


def test_build_schedule_requires_at_and_say():
    with pytest.raises(ValueError, match="both 'at' and 'say'"):
        build_cue_schedule([{"at": "C2"}], CONTACTS)
    with pytest.raises(ValueError, match="both 'at' and 'say'"):
        build_cue_schedule([{"say": "hi"}], CONTACTS)


def test_build_schedule_rejects_overlong_countdown():
    # A countdown longer than 10s would start before the previous cue and
    # overrun the moment it's counting to.
    with pytest.raises(ValueError, match="countdown_from must be"):
        build_cue_schedule([{"at": "C2", "say": "go", "countdown_from": 30}], CONTACTS)


def test_build_schedule_empty_is_fine():
    assert build_cue_schedule([], CONTACTS) == []
    assert build_cue_schedule(None, CONTACTS) == []


def test_the_shipped_config_cues_are_valid():
    """Guards the actual cues in config.example.yaml, not a synthetic set."""
    import pathlib

    import yaml

    root = pathlib.Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load((root / "config.example.yaml").read_text())
    build_cue_schedule(cfg["audio_cues"]["cues"], CONTACTS)


def test_speak_never_raises_when_synthesiser_missing(monkeypatch, capsys):
    from eclipse import cues

    monkeypatch.setattr(cues.shutil, "which", lambda name: None)
    speak("filter off now")  # must not raise
    assert "filter off now" in capsys.readouterr().out


def test_speak_never_raises_when_subprocess_fails(monkeypatch):
    from eclipse import cues

    monkeypatch.setattr(cues.shutil, "which", lambda name: "/usr/bin/say")
    monkeypatch.setattr(cues.platform, "system", lambda: "Darwin")

    def boom(*a, **k):
        raise OSError("no audio device")

    monkeypatch.setattr(cues.subprocess, "run", boom)
    speak("filter off now")  # must not raise


def test_cue_thread_failure_cannot_escape(monkeypatch):
    # The whole point of the daemon thread: a broken cue must not be able
    # to disturb the capture schedule.
    from eclipse import cues

    def boom(*a, **k):
        raise RuntimeError("cue exploded")

    monkeypatch.setattr(cues, "_run_cues", boom)
    thread = start_cue_thread([{"at": "C2", "say": "x"}], CONTACTS)
    assert thread is not None
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_cue_thread_is_a_daemon():
    # Must never hold the process open after the schedule finishes.
    thread = start_cue_thread([{"at": "C4+3600", "say": "late"}], CONTACTS)
    assert thread.daemon is True


def test_no_thread_when_no_cues():
    assert start_cue_thread([], CONTACTS) is None