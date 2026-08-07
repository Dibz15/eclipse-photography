# Eclipse frame planner

A single-page, static web tool for working out where the sun will sit in your
camera frame during a solar eclipse — from deep crescent, through totality,
to a couple of minutes after — given your location, date, and camera specs.

No build step, no server, no dependencies beyond the vendored library. Open
`index.html` in a browser, or serve the folder with anything static:

```
python3 -m http.server 8000    # from inside this directory
```

## How it relates to your Skyfield pipeline

The `eclipse-timings` script (DE440s via Skyfield) is the authoritative
source for contact times — that's what belongs in `config.yaml` and what you
should trust on eclipse morning. This tool computes its own local
circumstances independently, in the browser, using
[astronomy-engine](https://github.com/cosinekitty/astronomy) (VSOP87/ELP2000
based). In testing against your calculator's output, it agreed to within about 10 seconds and a few hundredths
of a degree — plenty accurate for framing, though not a replacement for the
verified numbers.

Two ways to reconcile the two:

- Use the "Override with my own C2 / max / C3 times" checkbox in the Contact
  Times card to paste in your script's numbers directly. The plot will use
  those instead.
- Or just trust the in-browser computation — for framing purposes the
  difference is well under one solar diameter of drift.

There's no file-system access from the page (browsers can't read the
`config.yaml` directly), so the override is manual entry. 

## What it does

1. **Location & date** — lat/lon/elevation/date, "Find eclipse circumstances"
   runs a local eclipse search and reports C1–C4 and max eclipse with
   altitude/azimuth for each.
2. **Camera** — native resolution (e.g. 6000×4000), orientation
   (landscape/portrait — this only changes which resolution number is
   horizontal vs. vertical; camera roll direction turns out not to matter,
   since a level shot always has real-world up = image up), and plate scale
   either computed from a 35mm-equivalent focal length or entered directly
   from a measurement you took through the lens (more accurate — use it if
   you have it).
3. **Framing** — choose what the frame is centered on (max eclipse, C2, or
   C3), a time window (minutes before C2 / after C3), an aim offset that
   trades corona-centering for visible foreground, corona clearance margin
   as a multiple of the solar radius, and a horizon obstruction height in
   degrees if hills or buildings block part of the sky.
4. **Live preview** — an SVG frame diagram with the sun's path, corona
   clearance circle, horizon line, and a scrub slider that moves a live
   marker with a time/alt/az readout. A readout table below shows the
   pixel margin to the frame edge for every key moment, and flags anything
   that clips. "Download SVG" saves the current diagram.

## Known simplifications

- The 35mm-equivalent focal length calculation assumes a 36×24mm reference
  sensor, which is the standard convention but won't be pixel-exact for
  every crop factor / aspect ratio combination — the "measured directly"
  mode sidesteps this if you want precision.
- Atmospheric refraction *is* corrected for — every altitude/azimuth in the
  tool comes from `astronomy-engine`'s `Horizon()` call in `'normal'` mode,
  which applies the standard Saemundsson formula. This matters more than it
  might sound: at a 2&ndash;3&deg; true altitude, refraction lifts the
  apparent position by roughly 15&ndash;20 arcminutes &mdash; around half a
  solar diameter. The one thing it doesn't account for is unusual local
  weather (the formula assumes a nominal atmosphere, ~10&deg;C and
  1010&nbsp;hPa); day-of conditions can shift the correction by a small
  fraction of an arcminute, which is negligible for framing.
- The hill silhouette is a single illustrative bump, not a real terrain
  profile — it's there as a sanity check on scale, not a substitute for
  scouting the actual skyline.

## Files

- `index.html` — structure
- `style.css` — dark, dusk-toned UI
- `app.js` — all the eclipse/camera/framing logic and SVG rendering
- `astronomy.js` — vendored `astronomy-engine` (MIT licensed), wrapped for
  direct `<script>` use in the browser