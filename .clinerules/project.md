# Cline rules for this project

- Package manager: uv. Never call `pip install` directly — use `uv add <pkg>`
  (or `uv add --optional camera <pkg>` for the gphoto2 extra) and `uv sync`
  to install. Run scripts via `uv run <console-script>` (eclipse-timings,
  eclipse-throughput, eclipse-run) or `uv run python -m eclipse.<module>`.

- Source lives under src/eclipse/. Tests live under tests/, run with
  `uv run pytest`. Keep new logic testable without hardware — see the
  gphoto2 lazy-import rule below.

- config.yaml is the ONLY file meant to be hand-edited the day before the
  eclipse (lat/lon/elevation/date/timezone, then timings_utc once
  cross-checked against EclipseWise/USNO). Don't hardcode coordinates or
  contact times anywhere in the code itself.

- camera.py must keep `import gphoto2` lazy (inside connect() and
  capture_one(), not at module top), so the rest of the project stays
  importable and unit-testable on a machine without libgphoto2 installed —
  e.g. this dev laptop, before the camera is ever plugged in.

- Any change to run_eclipse.py's scheduling logic should be exercised with
  `--dry-run` (DryRunCamera in camera.py) before it's considered done.
  There's no second take on eclipse morning — favor readability and
  explicit control flow over cleverness in run_eclipse.py and camera.py.

- bracket_plans.py entries are plain dicts, not classes/dataclasses — keep
  it that way so they stay trivially hand-editable the night before, and so
  trim_to_fit() can return a shallow-modified copy without ceremony.

- timings.py's root-finding (angular separation vs. sun/moon angular
  radii) is intentional, not a workaround to remove — Skyfield's built-in
  almanac helpers don't cover local solar eclipse contact times the way
  they do lunar eclipses/solstices/equinoxes.

- Never change filter-on/filter-off timing logic (diamond_ring_burst /
  partial_phase_bracket vs. totality_bracket in bracket_plans.py) without
  flagging it explicitly — those boundaries exist to keep the sensor (and
  by implication, anyone looking through the viewfinder) safe outside of
  totality.
