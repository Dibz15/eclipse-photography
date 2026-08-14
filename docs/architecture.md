# Architecture

## Module map

```
src/eclipse/
  timings.py         Skyfield root-finding for C1-C4 and max eclipse.
  tzutil.py           Resolves config's `timezone` field for DISPLAY only
                       (see below) — never affects scheduling.
  camera.py            The gphoto2 wrapper: connect/reconnect, config-node
                        discovery, the trigger_capture_one() capture
                        mechanism, capturetarget enforcement, macOS PTP
                        daemon handling, sleep prevention. Everything
                        hardware-specific lives here; see camera-notes.md
                        for why each piece exists.
  bracket_plans.py       Plain-dict exposure sequences per eclipse phase,
                          and trim_to_fit() for shortening the totality
                          bracket to whatever window is actually available.
  throughput_test.py    Diagnostics: measures real camera throughput,
                          discovers config choices, calibrates the timing
                          constants bracket_plans.py and camera.py rely on.
  focus_check.py          Full-capture sharpness scoring and calibration.
  rehearse.py               Runs the real scheduler against synthetic OR
                             real (re-dated) timings, for testing without
                             waiting for an actual eclipse.
  camera_check.py            Standalone connectivity diagnostic, decoupled
                              from the schedule.
  cues.py                     Spoken timing cues, isolated on their own
                               thread (see below).
  run_eclipse.py                The scheduler: builds the phase-by-phase
                                 timeline from timings_utc, connects to the
                                 camera, and fires each phase's bracket
                                 plan at the right moment. This is what
                                 actually runs on eclipse morning.

scripts/
  pull_from_card.py    Post-event download, via the gphoto2 CLI tool
                         (not the Python bindings — see camera-notes.md).

tests/                  One test file per module above, plus fake-camera
                          test doubles for anything hardware-touching.
```

## Design principles

### Fail loud, never silently wrong

An eclipse cannot be reshot. Several checks exist specifically to turn a
subtle, silent wrong-result into a loud failure *before* the schedule
starts waiting:

- A RAW+JPEG combo `image_quality` is refused outright (see
  [`camera-notes.md`](camera-notes.md#combo-raw-and-jpeg-image-quality-is-unsafe)).
- An `iso_overrides` key that doesn't match a real shutter speed in its
  plan.
- An unknown phase label in `download_phases`.
- A malformed audio cue time.
- A missing `timings_utc` block.

If you add new configuration, follow this pattern: validate everything
you can at startup, before the schedule begins, rather than discovering
a problem mid-event.

### Lazy hardware imports

`import gphoto2` never happens at module load time anywhere in this
project; only inside the specific functions that need it
(`connect()`, `capture_one()`, `trigger_capture_one()`). This means
`bracket_plans.py`, the timing math, the trim logic, and the full test
suite all import and run correctly on a machine with no libgphoto2
installed at all. If you're adding camera-touching code, keep the import
lazy and inside the function, not at the top of the file.

### Why everything is UTC

All scheduling arithmetic (contact times, phase boundaries, reconnect
deadlines) runs on naive UTC datetimes throughout. `config.yaml`'s
`timezone` field only controls what gets echoed alongside UTC in display
strings; it's read by `tzutil.resolve_tz()` and used nowhere in the
actual scheduling logic. This was tested directly: the schedule is
provably identical regardless of the configured timezone, an invalid
timezone name, a missing timezone field, or the machine's own OS
timezone setting. The only thing that has to be correct is the system
clock's absolute time.

### Audio cue isolation

`cues.py` runs entirely on a daemon thread that touches only
`subprocess` (for `say`/`espeak`); never the camera, and never the
schedule. Every call inside it is wrapped so an exception cannot escape
the thread. The capture schedule
neither waits for cues nor checks on them in either direction.

## Testing

Everything hardware-touching is designed to be testable without
hardware:

- `camera.DryRunCamera` stands in for a real `gphoto2.Camera` and is
  exercised by the exact same code paths (`connect(dry_run=True)`,
  `run_eclipse.py --dry-run`) as the real thing run. It isn't a separate
  simulation, it's the same functions with a different object underneath.
- Tests that need to verify gphoto2-specific behavior (event polling,
  config-tree walking, retry/backoff logic) use small purpose-built fake
  objects. See `tests/test_camera.py` for `FakeCamera`,
  `_FakeEventCamera`, `_TreeCamera`, and `_FakeGPhoto2`, each modeling
  just enough of the real API surface to exercise one specific behavior.
- `eclipse-rehearse` is the integration-level test: it runs the *actual*
  `build_schedule()`/`run()` that fires for real, either against
  compressed synthetic timings or your real `timings_utc` re-dated to
  today. Several real bugs in this project were found this way rather
  than by unit test. Most notably a scheduling error where a diamond-ring
  burst was allowed to overrun into totality, only visible by watching a
  real (or realistically-timed) run happen.

If you're adapting this project, `uv run eclipse-rehearse --dry-run`
after any change to `run_eclipse.py` or `camera.py` is the fastest way to
catch a broken schedule before it matters.

## Adapting to a different camera, site, or date

- **Different camera:** start with
  [`camera-notes.md`](camera-notes.md). Several fixes here exist because
  of specific Nikon/PTP behavior (read-only `focusmode`, the `f-number`
  node name, the burst-mode buffer depth) that may not apply to your
  camera, and your camera may have its own undocumented quirks these
  don't cover. Run the `--list-*` diagnostics in `eclipse-throughput`
  before assuming any hardcoded config name or choice string carries
  over.
- **Different site or date:** `config.yaml`'s `location` and `date` are
  the only required changes; `eclipse-timings --write` recomputes contact
  times from there. The *exposure numbers* in `bracket_plans.py`,
  however, are tuned for this project's specific solar altitude
  (extinction) and focal length (trailing). See
  [`exposure-design.md`](exposure-design.md) for the reasoning behind
  each one, and recompute rather than copy if your altitude or focal
  length differ meaningfully.
- **Different totality duration:** nothing to change. `trim_to_fit()`
  and the per-shot predictive cut in `run_bracket_once()` both adapt the
  bracket to whatever window is actually available, calibrated against
  your measured `bracket_overhead`.