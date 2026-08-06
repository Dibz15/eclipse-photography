# Cline rules for this project

Automated eclipse photography for a **Nikon D5500** (some older comments still
say D5200 — that's stale wording, not a different body) shooting the
2026-08-12 total eclipse from Mallorca on a fixed tripod at 300mm.

## Tooling

- Package manager: uv. Never call `pip install` — use `uv add <pkg>` (or
  `uv add --optional camera <pkg>` for the gphoto2 extra) and `uv sync`.
- Console scripts: `eclipse-timings`, `eclipse-throughput`,
  `eclipse-focus-check`, `eclipse-rehearse`, `eclipse-run`. Run via
  `uv run <script>` or `uv run python -m eclipse.<module>`.
- Source in src/eclipse/, tests in tests/, `uv run pytest`, `uv run ruff check .`.

## Non-negotiables

- **config.yaml is the ONLY file meant to be hand-edited day-of.** Anything
  measured against real gear (bracket_overhead, bracket_timeout_margin,
  image_quality, capture_target, focus_check thresholds) belongs there, not
  hardcoded. THIS SHOULD ONLY BE EDITED BY THE USER.
- **Never change filter-on/filter-off boundaries** (diamond_ring_burst and the
  filtered partial/deep-crescent plans vs. totality_bracket) without flagging
  it explicitly. Those exist for sensor and eye safety outside totality.
- **`import gphoto2` stays lazy** — inside connect()/capture_one()/
  trigger_capture_one(), never at module top. The whole project must stay
  importable and testable on a machine with no libgphoto2.
- **Fail loud, never silently wrong.** This event is not repeatable. Existing
  guards raise `SystemExit` *before* the schedule starts for: RAW+JPEG combo
  image_quality, unknown iso_overrides keys, missing timings_utc. Follow that
  pattern for new failure modes rather than logging a warning and continuing.

## Capture mechanism (hard-won, don't regress)

- **`trigger_capture_one()` is the capture path, not `capture_one()`.** Plain
  `capture()` internally retrieves object data during its resolve step, making
  it ~2x slower. `capture_one()` survives only for warm-up, focus_check, and
  throughput_test's comparison sweep.
- **`wait_for_event()` returns the FIRST event of ANY type.** The camera emits
  constant property-change noise, so any new event-waiting code must loop until
  it sees `GP_EVENT_FILE_ADDED`, never check once. A single check silently
  undercounts real captures.
- **Never use RAW+JPEG combo image quality.** Each capture fires TWO
  FILE_ADDED events; trigger_capture_one() consumes one per call, so
  run_bracket_once() can confirm a shutter speed that was never actually shot.
  Guarded by `is_raw_jpeg_combo_quality()`. Shooting plain NEF.
- **`capturetarget` is forced to card and verified at connect().** Every
  bracket assumes frames survive without downloading. Cameras default to a
  small internal RAM buffer that silently drops frames and can hang the body.
- **Never trust a gphoto2 config node name or value from memory.** Choice
  strings vary by model and libgphoto2 version (`imagequality` was wrong for a
  while; `imageformat` doesn't exist). Discover them via `get_config_choices()`
  and the `--list-*` flags.

## Timing model

- `bracket_overhead` (AVERAGE per-shot overhead) is for *estimating* whether
  shots fit. `bracket_timeout_margin` (MAX + safety) is for *giving up* on a
  shot. They are deliberately different values — using max for estimation
  overestimated a real pass by ~48%; using average for timeouts would record
  slow-but-working exposures as failures. Don't collapse them.
- **Totality's end_time is `diamond_out_start` (C3-8s), not C3.** Overrunning
  delays the C3 diamond ring past the event itself.
- `run_bracket_once()` cuts **per exposure**, not per pass, and skips
  individual non-fitting rungs rather than abandoning the pass (reversed
  palindrome passes run slowest-first, so later rungs may still fit).
- `trim_to_fit()` picks the ladder's *composition*; the per-shot check handles
  the *final pass*. Complementary — don't merge them.

## bracket_plans.py

- Plain dicts, not classes — they must stay trivially hand-editable the night
  before, and trim_to_fit() returns shallow-modified copies.
- `iso_overrides` keys must match `shutter_speeds` exactly (guarded at
  startup). Per-step ISO exists so slow rungs trade gain for exposure time:
  at 300mm the sky drifts 5.4 px/sec, so 2s@ISO500 beats 4s@ISO200 — deeper
  *and* half the trailing.
- `palindrome: True` alternates pass direction, putting the two slowest
  exposures adjacent at the seam for stacking.
- **Keep the ladders WIDE.** Site-specific: the sun is ~2.4° above the horizon
  (air mass ~17), so extinction is several stops and uncertain by ±2. Breadth
  is the insurance. Red runs ~4.6 stops ahead of blue — expose for red.

## Testing

- Anything touching scheduling or capture: exercise with
  `eclipse-rehearse --dry-run` (real build_schedule()/run() against synthetic
  near-future timings) before considering it done. `eclipse-run --dry-run`
  works too but sleeps against real timings_utc.
- Hardware-touching logic gets a fake-camera test double (see
  `_RecordingCamera`, `FakeCamera`, `_FakeEventCamera` in tests/test_camera.py)
  rather than being left untested.
- Favor readability and explicit control flow over cleverness in run_eclipse.py
  and camera.py. There is no second take.