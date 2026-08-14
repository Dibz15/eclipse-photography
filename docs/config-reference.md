# config.yaml reference

`config.yaml` is the only file meant to be hand-edited. Everything else
(calibrated timing constants, discovered camera settings, confirmed
contact times) is designed to be *written by a tool*, not typed in,
because those values are either measured against your specific gear or
verified against an external source. Fields below note which command
fills them in, where relevant.

## `location`

```yaml
location:
  latitude: 0.0     # decimal degrees, + N
  longitude: 0.0     # decimal degrees, + E
  elevation_m: 0      # helps refraction/altitude accuracy slightly
```

Fed into Skyfield by `eclipse-timings` to root-find your local contact
times. Not used anywhere else. 

For finding the right coordinates and double-checking timings, this is a brilliant resource: [http://xjubier.free.fr/en/site_pages/Solar_Eclipses.html](http://xjubier.free.fr/en/site_pages/Solar_Eclipses.html).

## `date`, `timezone`

```yaml
date: "2026-08-12"
timezone: "America/Denver"
```

`date` anchors the whole schedule. `timezone` is **display-only**. It
never affects when anything actually fires, only what local time gets
echoed alongside UTC in logs. See
[`setup.md`](setup.md#a-note-on-the-timezone-field). An invalid or
missing timezone name logs a warning and falls back to UTC-only display;
it does not error and does not shift the schedule.

## `camera`

```yaml
camera:
  port: null
```
Optional, e.g. `"usb:001,005"`. Leave `null` to auto-detect.

```yaml
  image_quality: null
```
Your camera's exact `imagequality` config choice string. You can discover the
real options with `eclipse-throughput --list-image-quality` rather than
guessing; these vary by camera model and driver version. **Must not be a
RAW+JPEG combo choice** (anything with a `+` in the name, e.g.
`"NEF+Fine"`). `eclipse-run` refuses to start if it is. See
[`camera-notes.md`](camera-notes.md#combo-raw-and-jpeg-image-quality-is-unsafe) for
why. Leave `null` to inherit whatever the camera is already set to. Note that sometimes this didn't always set the setting correctly for me on my Nikon D5500, so try to remember to set the setting manually on your body before the eclipse and treat this as a backup.

```yaml
  capture_target: null
```
Normally auto-detected (whichever `capturetarget` choice contains
"card") and enforced automatically at connect time. See
[`camera-notes.md`](camera-notes.md#capturetarget-defaults-to-ram-on-many-cameras).
Only set this explicitly if auto-detection ever picks the wrong choice on
your camera; check with `eclipse-throughput --list-capture-target`.

```yaml
  bracket_overhead: null
  bracket_timeout_margin: null
```
Two related but deliberately different calibrated values, both filled in
by `eclipse-throughput --bracket-test --write`:

- `bracket_overhead` is the **average** measured per-shot processing time
  beyond the exposure itself. Used for *estimating* whether a shot or a
  full pass will fit in the time remaining
  (`bracket_plans.trim_to_fit`, the per-shot predictive cut in
  `camera.run_bracket_once`).
- `bracket_timeout_margin` is the **max** observed overhead plus a safety
  margin. Used for deciding when to *give up* waiting on a shot's
  confirmation (`camera.trigger_capture_one`'s `event_timeout`).

Using the max for estimation over-trims the bracket; using the average
for the timeout risks recording slow-but-working exposures as failures.
Both default to conservative built-in constants
(`camera.DEFAULT_BRACKET_OVERHEAD`, `camera.DEFAULT_BRACKET_TIMEOUT_MARGIN`)
measured against this project's original camera. Recalibrate against
yours.

```yaml
  auto_free_usb_claim: false
```
macOS only. If a connect attempt fails with `[-53] Could not claim the
USB device`, the system PTP daemon almost certainly owns the camera.
This flag controls whether `connect()` kills that daemon automatically
and retries. **Off by default, deliberately**. See
[`camera-notes.md`](camera-notes.md#the-macos-ptp-daemon) for why
auto-killing a system process turned out to be worse than leaving it
alone. Use `eclipse-camera-check --fix` instead, which does the same
thing but as an explicit decision you make.

```yaml
  download_phases: []
```
Phase labels whose captured frames should also be downloaded in full to
`<output_dir>/monitor/`, for checking focus drift or lens fogging
mid-event without the LCD (dark while tethered — see
[`camera-notes.md`](camera-notes.md#the-lcd-goes-dark-while-tethered)). Valid labels are the phase
names in the schedule: `partial_pre_totality`,
`deep_crescent_pre_totality`, `diamond_ring_in`, `totality`,
`diamond_ring_out`, `deep_crescent_post_totality`,
`partial_post_totality`. An unknown label refuses to start; listing a
time-critical phase (`totality` or either diamond ring) is allowed but
logs a warning, since a download costs window time that can't be
recovered. The long partial phases are the intended place for this.

## `output_dir`

```yaml
output_dir: "./eclipse_frames"
```
Where `download_phases` monitoring copies and `eclipse-focus-check`
captures land. The real deliverables stay on the camera's card
throughout the event and come off afterward via
`scripts/pull_from_card.py`, independent of this setting.

## `focus_check`

```yaml
# focus_check:
#   low_threshold: 45.2
#   high_threshold: 210.8
#   roi: null   # [x, y, width, height], or null for whole-frame
```
Filled in by `eclipse-focus-check --calibrate`. Thresholds are a relative
sharpness metric specific to your lens, aperture, and ROI. Recalibrate
if you change any of those. Used both by standalone focus checks and by
`eclipse-run --focus-check`'s startup check.

## `timings_utc`

```yaml
# timings_utc:
#   C1: "17:01:23"
#   C2: "18:23:47"
#   max: "18:25:36"
#   C3: "18:27:25"
#   C4: "19:48:02"
```
Filled in by `eclipse-timings --write`, after you've cross-checked
against an external source. This is what `eclipse-run` actually reads. 
No live astronomical computation happens on eclipse morning, and nothing
depends on internet access at that point.

## `audio_cues`

```yaml
audio_cues:
  enabled: true
  voice: null
  cues:
    - at: "C2-600"
      say: "Ten minutes to totality."
    - at: "C2-20"
      say: "Filter off now."
      countdown_from: 5
```
Spoken reminders for the things a script can't do for you (pulling the
filter, checking framing, etc). `at` is a contact (`C1`/`C2`/`max`/`C3`/`C4`)
optionally followed by an offset in seconds: `"C2-300"` is five minutes
before C2, `"C3+8"` is eight seconds after C3. `countdown_from` (1–10)
speaks a countdown timed so the final word lands exactly on the cue. `voice` is a macOS `say` voice name (`null` for the system
default). Malformed offsets fail at startup, before anything is
scheduled. Runs on an isolated thread that cannot affect capture even if
it fails. See [`architecture.md`](architecture.md#audio-cue-isolation).

Cues more than a few seconds late (restart mid-event, a stall) are
skipped rather than replayed. See
[`camera-notes.md`](camera-notes.md#stale-cues-and-a-frozen-laptop).

## `prevent_sleep`

```yaml
prevent_sleep: true
```
Holds the system awake for the duration of a real run (macOS:
`caffeinate`). On by default and worth leaving on. See
[`camera-notes.md`](camera-notes.md#a-sleeping-laptop-is-worse-than-a-crashed-one).
Ignored for `--dry-run`.