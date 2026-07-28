# Eclipse Photography Automation

Skyfield for local contact timing (C1-C4, max eclipse) + python-gphoto2 for
firing the right exposure bracket at the right moment on a Nikon D5200 (though it should work for many Nikon cameras, 
as long as they're compatible with gphoto2).
One config file to edit the day before, everything else is pre-tested.

**⚠️ Safety first:** never look at the sun, or point an unfiltered lens/
viewfinder at it, outside of totality. `bracket_plans.py` assumes a solar
filter is ON during the partial phases and diamond-ring bursts, and OFF
only during totality — see `run_eclipse.py`'s schedule. Don't change that
timing without understanding the implication for your eyes and your
sensor.

## Prerequisites

- macOS with [uv](https://docs.astral.sh/uv/) installed
  (`brew install uv` or see the uv docs)
- Only if you're actually driving the camera:
  `brew install libgphoto2` (the python bindings link against this)

## Setup

```bash
cd eclipse-photography

# Base install (timings math, tests, bracket trimming — no camera needed)
uv sync

# Add camera control once libgphoto2 is installed
uv sync --extra camera

# Dev tools (pytest, ruff)
uv sync --extra dev
```

`config.yaml` is already created from `config.example.yaml` and gitignored
(so your real coordinates never get committed). It's the only file you
should need to hand-edit.

## Workflow

**Weeks before**, from wherever you're developing:

```bash
# Fill in location/date/timezone in config.yaml, then get a first-pass estimate
uv run eclipse-timings

# Cross-check the output against EclipseWise or the USNO calculator for
# your exact site, then lock in the confirmed numbers:
uv run eclipse-timings --write
```

**Weeks before, with the camera attached** (indoors, pointed at a lamp —
you're measuring throughput, not exposure quality):

```bash
uv run eclipse-throughput --write
```

This measures real JPEG/RAW × download/no-download fps and writes the
JPEG-no-download number into `config.yaml` as `camera.measured_max_fps`.
`run_eclipse.py` uses it to auto-trim `totality_bracket` if your confirmed
totality duration is too short to fit all 12 stops (see
`bracket_plans.trim_to_fit`).

**Anytime, without a camera** — rehearse the full schedule (waits, filter
timing, bracket dispatch) against your locked-in `timings_utc`:

```bash
uv run eclipse-run --dry-run
```

This logs every "capture" instead of touching hardware, so you can confirm
the sequencing and timing math well before eclipse morning.

**On eclipse morning:**

```bash
# Sync your system clock (NTP or GPS) first — the scheduler is pure
# sleep-until-timestamp, so clock accuracy matters more than anything else.
uv run eclipse-run
```

**After the eclipse**, if you ran with images left on the card rather than
downloaded live:

```bash
brew install gphoto2   # the CLI tool, separate from libgphoto2
uv run python scripts/pull_from_card.py
```

## Project layout

```
config.yaml            <- the only file you touch day-before (gitignored)
config.example.yaml    <- tracked template for the above
src/eclipse/
  timings.py            <- Skyfield: root-finds C1-C4, max eclipse
  camera.py              <- python-gphoto2 wrapper (lazy import, dry-run mode)
  bracket_plans.py        <- exposure sequences per phase + trim-to-fit math
  throughput_test.py     <- run weeks ahead to measure real camera fps
  run_eclipse.py           <- scheduler: sleeps to each event, fires sequences
scripts/
  pull_from_card.py       <- post-event download helper (not timing-critical)
tests/                     <- uv run pytest
de440s.bsp                 <- JPL ephemeris, auto-downloaded on first run (~30MB)
```

## Dev loop

```bash
uv run pytest          # tests run without any camera hardware
uv run ruff check .    # lint
```

`camera.py` keeps `import gphoto2` lazy specifically so the rest of the
project — timings math, bracket trimming, the scheduler's own logic — stays
importable and testable on a machine without libgphoto2, like this one.
See `.clinerules` for the conventions this repo expects contributions to
follow.

## Why Skyfield needs help here

Skyfield's `almanac` module has clean built-in helpers for lunar eclipses
and solstices/equinoxes, but not for *local* solar eclipse contact times —
those depend on the observer's topocentric position. `timings.py` does the
root-finding itself: angular separation between Sun and Moon vs. the
sum/difference of their apparent angular radii (computed from each body's
`.apparent().radec()` distance). This is the standard approach and is
accurate to within a couple of seconds using the DE440s ephemeris — plenty
tight for exposure sequencing, though still worth cross-checking against
EclipseWise/USNO as described above before you lock in `timings_utc`.
