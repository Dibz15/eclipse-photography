"""
Spoken timing cues for the things a human still has to do by hand:
taking the solar filter off before the diamond ring, putting it back on
after, and framing the shot far enough ahead that the sun drifts through
the centre during totality.

Design constraint: these must NEVER be able to affect capture. Cues run
on a daemon thread that touches nothing but `subprocess`, every call is
wrapped so an exception can't escape, and the capture schedule neither
waits for nor checks on them. Losing your cues would be annoying; losing
totality because a cue failed would be unforgivable.

Speech, not tones, on purpose. A beep means whatever you remember it
meaning, which at C2 minus ten seconds with a filter in one hand is not a
reliable channel. macOS `say` is built in; espeak is used if present;
otherwise it falls back to the terminal bell so at least SOMETHING
happens.

Cue times are written relative to a contact, e.g. "C2-300" (five minutes
before C2) or "C3+2" (two seconds after C3). See config.example.yaml.
"""

from __future__ import annotations

import datetime as dt
import logging
import platform
import re
import shutil
import subprocess
import threading
import time

log = logging.getLogger("eclipse.cues")

_OFFSET_RE = re.compile(r"^\s*(C1|C2|C3|C4|max)\s*([+-]\s*\d+(?:\.\d+)?)?\s*$", re.IGNORECASE)

_CANONICAL = {"c1": "C1", "c2": "C2", "c3": "C3", "c4": "C4", "max": "max"}


def parse_cue_offset(spec: str) -> tuple[str, float]:
    """'C2-300' -> ('C2', -300.0). A bare contact means zero offset.

    Raises ValueError on anything unparseable, so a typo in config fails
    loudly at startup rather than silently never firing — the same rule
    the rest of this project follows for config that can't be verified
    later."""
    match = _OFFSET_RE.match(spec)
    if not match:
        raise ValueError(
            f"Unparseable cue time {spec!r}. Expected a contact optionally "
            "followed by an offset in seconds, e.g. 'C2-300', 'C3+2', 'max'."
        )
    contact = _CANONICAL[match.group(1).lower()]
    offset = float(match.group(2).replace(" ", "")) if match.group(2) else 0.0
    return contact, offset


def cue_datetime(spec: str, contacts: dict[str, dt.datetime]) -> dt.datetime:
    contact, offset = parse_cue_offset(spec)
    if contact not in contacts:
        raise ValueError(f"Cue time {spec!r} references unknown contact {contact!r}")
    return contacts[contact] + dt.timedelta(seconds=offset)


def _speech_command(text: str, voice: str | None) -> list[str] | None:
    if platform.system() == "Darwin" and shutil.which("say"):
        return ["say"] + (["-v", voice] if voice else []) + [text]
    if shutil.which("espeak"):
        return ["espeak", text]
    return None


def speak(text: str, voice: str | None = None, timeout: float = 20.0) -> None:
    """Say `text` aloud. Never raises — falls back to the terminal bell
    and a log line if no speech synthesiser is available."""
    cmd = _speech_command(text, voice)
    try:
        if cmd is None:
            print(f"\a>>> {text}", flush=True)
            return
        subprocess.run(cmd, timeout=timeout, check=False)
    except Exception:
        log.debug("Speech failed for %r", text, exc_info=True)
        try:
            print(f"\a>>> {text}", flush=True)
        except Exception:  # noqa: BLE001, S110
            pass


def build_cue_schedule(cue_specs: list[dict], contacts: dict[str, dt.datetime]):
    """Turns config cue entries into (when, text, countdown_from) tuples,
    sorted by time. Validates every entry up front."""
    schedule = []
    for entry in cue_specs or []:
        if "at" not in entry or "say" not in entry:
            raise ValueError(f"Cue entry needs both 'at' and 'say': {entry!r}")
        when = cue_datetime(str(entry["at"]), contacts)
        countdown = entry.get("countdown_from")
        if countdown is not None:
            countdown = int(countdown)
            if not 1 <= countdown <= 10:
                raise ValueError(
                    f"countdown_from must be 1-10 (got {countdown}) — longer "
                    "countdowns overrun the moment they're counting to."
                )
        schedule.append((when, str(entry["say"]), countdown))
    return sorted(schedule, key=lambda c: c[0])


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


_NUMBER_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
}


def _run_cues(schedule, voice: str | None) -> None:
    for when, text, countdown in schedule:
        if countdown:
            # Start early enough that the final word lands ON the cue time.
            start = when - dt.timedelta(seconds=countdown)
            _sleep_until(start)
            for n in range(countdown, 0, -1):
                if _utcnow() >= when:
                    break
                speak(_NUMBER_WORDS.get(n, str(n)), voice)
                _sleep_until(when - dt.timedelta(seconds=n - 1))
        else:
            _sleep_until(when)
        log.info("CUE: %s", text)
        speak(text, voice)


def _sleep_until(target: dt.datetime) -> None:
    remaining = (target - _utcnow()).total_seconds()
    if remaining > 0:
        time.sleep(remaining)


def start_cue_thread(cue_specs: list[dict], contacts: dict[str, dt.datetime], voice=None):
    """Starts cues on a daemon thread and returns it (or None if there's
    nothing to do). Daemon so it can never hold the process open, and
    every failure inside is swallowed — the capture schedule must not be
    able to notice this thread exists."""
    schedule = build_cue_schedule(cue_specs, contacts)
    if not schedule:
        return None

    def runner():
        try:
            _run_cues(schedule, voice)
        except Exception:
            log.exception("Cue thread failed — captures are unaffected")

    thread = threading.Thread(target=runner, name="eclipse-cues", daemon=True)
    thread.start()
    log.info("Audio cues armed: %d scheduled, first at %s UTC",
             len(schedule), schedule[0][0].strftime("%H:%M:%S"))
    return thread