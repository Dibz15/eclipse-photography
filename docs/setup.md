# Setup

## Prerequisites

- **macOS** with [uv](https://docs.astral.sh/uv/) installed:
  `brew install uv`
- **For controlling a real camera:** `brew install libgphoto2` - the
  Python bindings (`python-gphoto2`, imported as `gphoto2`) link against
  this. Without it, everything except actual capture still works: timing
  math, bracket-trimming logic, and the full test suite are all
  hardware-independent by design (see
  [`architecture.md`](architecture.md#lazy-hardware-imports)).
- **For pulling images off the card after the event:**
  `brew install gphoto2` — the separate CLI tool, not the Python bindings.
  `scripts/pull_from_card.py` shells out to it.

## Install

```bash
git clone https://github.com/dibz15/eclipse-photography
cd eclipse-photography

# Base install: timing math, bracket logic, the full test suite.
# No camera hardware needed for any of this.
uv sync

# Add camera control once libgphoto2 is installed.
uv sync --extra camera

# Dev tools (pytest, ruff) - you'll want these if you're adapting anything.
uv sync --extra dev
```

Run everything through `uv run <command>`, e.g. `uv run pytest`,
`uv run eclipse-timings`. This keeps you inside the project's managed
virtual environment without activating it manually.

## First-time configuration

```bash
cp config.example.yaml config.yaml
```

`config.yaml` is gitignored on purpose - it will end up holding your real
coordinates and site-specific calibration data, and shouldn't be
committed. `config.example.yaml` is the tracked template with every field
documented inline; see [`config-reference.md`](config-reference.md) for
the full field-by-field explanation.

At minimum, before anything else, fill in:

```yaml
location:
  latitude: 0.0     # your site, decimal degrees
  longitude: 0.0
  elevation_m: 0

date: "2026-08-12"   # your eclipse date
timezone: "America/Denver"   # IANA name, for DISPLAY only — see below
```

For finding the right coordinates and double-checking timings, this is a brilliant resource: [http://xjubier.free.fr/en/site_pages/Solar_Eclipses.html](http://xjubier.free.fr/en/site_pages/Solar_Eclipses.html).

Everything else (camera settings, calibrated timing constants, contact
times) gets filled in by the tools themselves as you work through
[`workflow.md`](workflow.md), not by hand.

### A note on the `timezone` field

All internal scheduling in this project runs on UTC, always. `timezone`
only controls what local time gets echoed alongside UTC in logs and
timing output. It cannot affect *when* anything actually fires. This is
deliberate: DST transitions, an incorrectly-set system timezone, or a
typo in this one field should never be able to shift a capture time. See
[`architecture.md`](architecture.md#why-everything-is-utc) if you want
the full reasoning.

## Verifying the install

```bash
uv run pytest        # should pass with no camera attached at all
uv run ruff check .  # lint
```

If you have the camera extra installed and a supported camera plugged
in:

```bash
uv run eclipse-throughput --list-image-quality
```

If this prints a list of choices (rather than an error), your camera,
cable, and libgphoto2 install are all working. If it errors, see
[`camera-notes.md`](camera-notes.md). Most first-connection problems
here are covered there.