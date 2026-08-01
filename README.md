# Eclipse Photography Automation

Skyfield for local contact timing (C1-C4, max eclipse) + gphoto2 for firing
the right exposure bracket at the right moment on a Nikon D5200, plus a
focus-check tool for working around this camera's tethering quirks. One
config file to edit the day before, everything else is pre-tested.

**⚠️ Safety first:** never look at the sun, or point an unfiltered lens/
viewfinder/eyepiece at it, outside of totality. `bracket_plans.py` assumes
a solar filter is ON during the partial phases and diamond-ring bursts,
and OFF only during totality — see `run_eclipse.py`'s schedule. Don't
change that timing without understanding the implication for your eyes
and your sensor.

## Prerequisites

- macOS with [uv](https://docs.astral.sh/uv/) installed
  (`brew install uv` or see the uv docs)
- Only if you're actually driving the camera:
  `brew install libgphoto2` (the python bindings link against this)
- Only for post-event downloads (`scripts/pull_from_card.py`):
  `brew install gphoto2` (the separate CLI tool)

## Setup

```bash
cd eclipse-photography

# Base install (timings math, tests, bracket trimming, focus-check scoring
# — no camera needed for any of this)
uv sync

# Add camera control once libgphoto2 is installed
uv sync --extra camera

# Dev tools (pytest, ruff)
uv sync --extra dev
```

`config.yaml` is already created from `config.example.yaml` and gitignored
(so your real coordinates never get committed). It's the only file you
should need to hand-edit — see `config.example.yaml` for every field with
inline comments; the sections below note which command fills in which
block.

## Workflow

**Weeks before**, from wherever you're developing:

```bash
# Fill in location/date/timezone in config.yaml, then get a first-pass estimate
uv run eclipse-timings

# Cross-check the output against EclipseWise or the USNO calculator for
# your exact site, then lock in the confirmed numbers:
uv run eclipse-timings --write
```

**Weeks before, with the D5200 attached** (indoors, pointed at a lamp —
you're measuring throughput, not exposure quality):

```bash
# Optional: see your camera's exact imagequality/capturetarget choice
# strings before picking one — these vary by camera model and even
# libgphoto2 version, so don't guess.
uv run eclipse-throughput --list-image-quality
uv run eclipse-throughput --list-capture-target

uv run eclipse-throughput --write
```

This tests every real image-quality choice your camera reports (JPEG
variants, NEF/RAW, RAW+JPEG combos) and writes the fastest no-download fps
into `config.yaml` as `camera.measured_max_fps`. `run_eclipse.py` uses it
to auto-trim `totality_bracket` if your confirmed totality duration is too
short to fit all 14 stops (see `bracket_plans.trim_to_fit`).

If you want a specific format locked in rather than whatever the camera
happens to be set to, put the exact string from `--list-image-quality`
into `config.yaml`'s `camera.image_quality`. `capture_target` is normally
auto-detected and enforced for you at connect time — every capture in this
project assumes it survives on the card without being downloaded, and many
cameras default to writing into limited internal RAM instead (see
*Camera quirks* below) — only set `camera.capture_target` explicitly if
that auto-detection ever picks the wrong choice on your specific camera.

**Focus, before eclipse day** — see *Focusing on the D5200* below for the
full story; the short version:

```bash
# Calibrate against a real target: the sun (filtered, any clear day) or
# the moon (same effective focus distance, no filter needed)
uv run eclipse-focus-check --calibrate

# Day-to-day / iterating on focus:
uv run eclipse-focus-check --watch --interval 5
```

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

# Lock focus (see Focusing on the D5200), then optionally sanity-check it
# right before the schedule starts:
uv run eclipse-run --focus-check

# or, if you're confident and don't want the startup check:
uv run eclipse-run
```

`--focus-check` runs one capture + score right after connecting, using
whatever thresholds `--calibrate` saved. A confident out-of-focus verdict
stops and asks for confirmation before the schedule starts; anything else
just logs the score and continues — it won't interrupt the unattended run
once it's underway.

**After the eclipse**, if you ran with images left on the card rather than
downloaded live:

```bash
uv run python scripts/pull_from_card.py
```

## Focusing on the D5200

Two things make this camera specifically annoying to focus once tethered,
both confirmed through direct testing on this project, not assumed:

**The rear LCD (live view and image review) goes dark while tethered.**
This is a documented, cross-model Nikon behavior, not a bug here — nothing
in gphoto2/PTP exposes a way to turn it back on. `capture_preview()` *does*
work on this D5200 (confirmed by testing — not guaranteed on every camera
in this tier) and gives a low-res live-view substitute, good for coarse
framing/focus checks, but too coarse to trust for critical focus.

**Autofocus re-triggers on every single capture, and can't be prevented.**
gphoto2 can't force the D5200 into manual focus mode remotely (`focusmode`
is read-only over PTP on this camera tier). Reassigning the AE-L/AF-L
button to AF-ON (Custom Setting f2) doesn't help either — it adds an
*extra* way to trigger AF, it doesn't remove the shutter's own
AF-triggering behavior, which is a higher-tier-body feature this camera
doesn't have. So every `camera.capture()` call during the automated
sequence will re-run AF regardless of any button/menu setting.

**The fix that actually works:** use AF (or AF-ON) to *acquire* focus
quickly, then flip the lens's own physical A/M switch to **M**. That's a
real mechanical disconnect inside the lens — no PTP command can override
it, unlike any electronic-only "lock." This is also just standard practice
for solar/lunar photography regardless of the tethering issue.

**`eclipse-focus-check`** exists because none of the above lets you
*verify* focus while tethered. It captures a full-resolution frame (not
the coarse preview), downloads it, and scores sharpness via variance of
the Laplacian — a standard blur-detection metric: sharp images have strong
high-frequency edge content, which shows up as high variance; blurry
images don't. This is a *relative* metric, so there's no universal
threshold baked in — `--calibrate` walks you through one deliberately
out-of-focus and one carefully-focused reference shot and derives
thresholds from your actual gear. Point it at the actual sun through the
actual filter on a clear day (any day, not just eclipse morning — solar
filters are fine for extended daytime use), or the moon at night, which
sits at the same effective focus distance. Crop to the limb (the disk's
edge) with `--roi x,y,width,height` rather than scoring the whole frame —
the interior is mostly flat/featureless and dilutes the signal. Always
re-verify on the actual sun through the actual filter on eclipse morning
itself, regardless of prior calibration — temperature and the exact
mounted setup on the day are what actually matter.

## Camera quirks (why the code does what it does)

A few things in `camera.py` exist because of real hardware/driver
behavior discovered while building this, not speculative caution:

- **`capturetarget` defaults to internal RAM on many cameras, not the
  card.** gphoto2's own docs: *"setting this to sdram will make the
  camera capture directly into the camera RAM and not on the memory card.
  You need to download the image in the same gphoto2 call, otherwise it
  will [be] gone when the connection is closed."* Every bracket plan in
  this project assumes captures survive on the card without downloading —
  `connect()` forces and verifies this at startup, raising loudly if it
  can't, rather than silently risking totality frames that vanish the
  moment the connection closes.
- **`imagequality` choice strings vary by camera model and libgphoto2
  version.** There are open bug reports of the same property reporting
  different, sometimes-wrong choices across Nikon bodies and driver
  versions. `eclipse-throughput --list-image-quality` discovers your
  camera's actual choices rather than hardcoding a guess from a forum
  post.
- **`focusmode` is read-only over PTP on this camera tier** (confirmed via
  a same-generation Nikon D3200 bug report showing the identical
  limitation) — see *Focusing on the D5200* above for what this means in
  practice.

## Project layout

```
config.yaml            <- the only file you touch day-before (gitignored)
config.example.yaml    <- tracked template for the above, with field comments
src/eclipse/
  timings.py          <- Skyfield: root-finds C1-C4, max eclipse
  tzutil.py           <- resolves config's timezone field for display only
  camera.py           <- gphoto2 wrapper (lazy import, dry-run mode,
                          capturetarget enforcement, config discovery)
  bracket_plans.py    <- exposure sequences per phase + trim-to-fit math
  throughput_test.py  <- run weeks ahead to measure real D5200 fps
  focus_check.py      <- full-capture sharpness scoring + calibration
  run_eclipse.py      <- scheduler: sleeps to each event, fires sequences,
                          optional startup focus check
scripts/
  pull_from_card.py   <- post-event download helper (not timing-critical)
tests/                 <- uv run pytest
de440s.bsp             <- JPL ephemeris, auto-downloaded on first run (~30MB)
```

## Dev loop

```bash
uv run pytest          # tests run without any camera hardware
uv run ruff check .    # lint
```

`camera.py` keeps `import gphoto2` lazy specifically so the rest of the
project — timings math, bracket trimming, focus-scoring math, the
scheduler's own logic — stays importable and testable on a machine
without libgphoto2, like this one. Where a test needs to exercise real
camera get/set/verify round-trips (e.g. the `capturetarget` enforcement),
it does so against a small fake `CameraWidget` stand-in rather than real
hardware — see `tests/test_camera.py`. See `.clinerules` for the
conventions this repo expects contributions to follow.

## Why there's a `timezone` field when everything runs on UTC

All scheduling — `timings_utc`, `run_eclipse.py`'s sleep-until logic, the
system clock you sync at your site — deliberately stays in UTC throughout.
That's the robust choice: no DST transitions, no ambiguity, no dependency
on the OS's local timezone setting matching your site.

`timezone` is display-only: `eclipse-timings` and `eclipse-run` use it to
echo the local wall-clock time next to each UTC timestamp (`18:23:47 UTC /
2026-08-12 12:23:47 MDT`), so you can sanity-check against EclipseWise
(often shown in local time) and follow the run log without doing UTC
arithmetic in your head during totality. Leave it blank and everything
still works — you just won't get the local-time echo. An unrecognized
timezone name logs a warning and falls back to UTC-only rather than
crashing.

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