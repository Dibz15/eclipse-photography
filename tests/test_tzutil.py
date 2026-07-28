import logging

from eclipse.tzutil import resolve_tz


def test_resolve_tz_returns_none_for_empty():
    assert resolve_tz(None) is None
    assert resolve_tz("") is None


def test_resolve_tz_returns_zoneinfo_for_valid_name():
    tz = resolve_tz("America/Denver")
    assert tz is not None
    assert str(tz) == "America/Denver"


def test_resolve_tz_falls_back_to_none_for_unknown_name(caplog):
    with caplog.at_level(logging.WARNING):
        tz = resolve_tz("Not/A_Real_Zone")
    assert tz is None
    assert "Unrecognized timezone" in caplog.text