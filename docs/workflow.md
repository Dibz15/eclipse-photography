# Workflow

Six commands, run roughly in this order over the weeks leading up to the
event, then once on the day itself. Each one writes calibrated values
into `config.yaml`.

## 1. Weeks before: compute contact times

```bash
uv run eclipse-timings --write
```

Uses Skyfield to root-find your local C1 (partial begins), C2 (totality
begins), max eclipse, C3 (totality ends), and C4 (partial ends) from the
coordinates in `config.yaml`. Accurate to within a couple of seconds, but
**cross-check the output against
[EclipseWise](https://www.eclipsewise.com/) or the USNO calculator**
before trusting it. `--write` locks the confirmed times into
`config.yaml`'s `timings_utc` block, which is what the real run reads.
Locking in verified times, rather than computing them live, means eclipse
morning doesn't depend on this script or your internet connection
working.

## 2. Weeks before, camera attached: measure real throughput

Point the camera at a lamp indoors to measure camera USB throughput. This is important, because I found that there's a significant difference between the speed I can capture using the manual trigger and the USB trigger.

```bash
uv run eclipse-throughput --list-image-quality
uv run eclipse-throughput --list-capture-target
```

Prints your camera's *actual* config choice strings. These vary by
camera model and even driver version — don't guess or copy one from a
forum post. Pick a plain (not combo) format and put it in
`config.yaml`'s `camera.image_quality`. I recommend choosing whatever your RAW format is for your camera body.

```bash
uv run eclipse-throughput --bracket-test --write
```

The one that matters most: fires the real bracket shutter-speed sequence
(including your slowest exposures) and calibrates
`camera.bracket_overhead` and `camera.bracket_timeout_margin` against
your actual gear. Re-run this if you change lens, card, or camera body.
See [`config-reference.md`](config-reference.md) for what these values
actually drive.

## 3. Anytime: check and lock in focus

```bash
uv run eclipse-focus-check --calibrate
```

Captures a deliberately-defocused and a carefully-focused reference shot
and derives sharpness thresholds from the difference. See
[`exposure-design.md`](exposure-design.md#focusing) for why this exists
and what to point it at. Day-to-day:

```bash
uv run eclipse-focus-check --watch --interval 5
```

## 4. Rehearse. Do this at least a few times before the day.

```bash
uv run eclipse-rehearse --dry-run                 # no camera, quick
uv run eclipse-rehearse                            # real camera, ~110s
uv run eclipse-rehearse --from-config --dry-run     # your REAL timings,
                                                     # re-dated to today
```

`--from-config` is the most valuable: it takes your locked-in
`timings_utc` verbatim and re-dates it to today, preserving both the real durations *and* the real times
of day. Run it the evening before, at the same clock time, and the sun
will sit within about a degree of where it'll be on the day.

Kill it mid-run and restart to rehearse resuming mid-event, same as a
real disconnect would require.

## 5. Before you rely on it: check camera connectivity

```bash
uv run eclipse-camera-check
uv run eclipse-camera-check --watch   # keep checking while you replug
```


## 6. On eclipse day

```bash
uv run eclipse-run --focus-check
```

Sync your system clock first (NTP or GPS). The scheduler is pure
sleep-until-timestamp, so clock accuracy matters more than anything else.
`--focus-check` runs one sharpness check right after connecting and
blocks for confirmation only if it looks confidently out of focus;
otherwise it logs the score and starts the schedule without waiting on
you.

From here it runs unattended: filtered partial-phase brackets, a
dedicated thin-crescent phase in the last few minutes before/after
totality, diamond-ring bursts straddling C2 and C3, and the totality
bracket in between. If you enabled audio cues, it'll tell you when to
pull the filter and when to put it back. See
[`config-reference.md`](config-reference.md#audio_cues).

## After the eclipse

```bash
uv run python scripts/pull_from_card.py
```

Downloads everything off the card. Not run during the event on purpose. See [`camera-notes.md`](camera-notes.md#why-nothing-downloads-during-capture).