# Exposure design

The numbers in `bracket_plans.py` aren't arbitrary. Each one traces back
to either a published reference, a physical calculation specific to this
camera and site, or a real-world test. This page explains the reasoning; treat
the actual shutter speeds and ISOs as a worked example for one specific
setup (Nikon D5500, 300mm actual focal length, f/11, ~2.4° solar altitude
at totality) rather than values to copy for a different one.

## Starting point: Espenak's exposure formula

Fred Espenak's Solar Eclipse Exposure Guide gives
`t = f² / (ISO × 2^Q)`, where `Q` is a brightness tier (prominences,
inner corona, outer corona, etc.) and `t` is exposure time in seconds.
This seeded every bracket in this project: `diamond_ring_burst`'s
`1/4000` at f/11/ISO100 matched Espenak's Baily's Beads (Q=12) value
almost exactly (though adjusted later for sunset), and `totality_bracket`'s range was originally built by
walking the formula across corona brightness tiers at f/11/ISO200 (close
to Espenak's own stated personal practice of ISO 200, f/9).

## The filter density surprise

Real metering at f/11 through the actual solar filter, during a test
session, came out roughly 3.4 stops slower than Espenak's Q=8 (his
"~ND 5.0 filter" assumption) predicted. The filter itself checked out;
its rated transmission (`1/1000 of 1%`) works out to exactly ND 5.0, so
it wasn't unusually dense.

The real cause was atmospheric haze, which is why rather than trust one measured number, the
filtered brackets (`partial_phase_bracket`, `deep_crescent_bracket`) were
built *wide* to span both the theoretical and the measured value with
margin on both sides. So the actual correct exposure is captured
regardless of which estimate was closer. Check the RGB histogram (not
just the composite) when metering a filtered sun, and expose for red,
since it clips first.

## Low-altitude extinction

This project's eclipse site placed the sun only a few degrees above the
horizon at totality. That's an air mass around 17, versus ~1 for a sun
overhead. That has several concrete consequences, computed from standard
atmospheric extinction models:

- **Several stops of overall dimming**, uncertain by roughly ±2 stops
  depending on haze and aerosol on the day. This uncertainty, more than
  anything else, is why the totality bracket stays wide rather than being
  tuned tightly around one predicted value.
- **A real brightness gradient across the frame.** The bottom of the
  corona sits at meaningfully lower altitude than the top, even within one exposure. Worked out for this
  site: roughly 0.77 stops dimmer limb-to-limb, and about 1.5 stops
  across the visible corona out to 2 solar radii. 
- **Red and blue extinguish very differently.** Rayleigh scattering
  alone puts blue roughly 4.6 stops below red at this altitude (versus
  well under a stop overhead), meaning the corona reads distinctly warm,
  and white-balance correction that tries to fully neutralize that will
  mostly amplify noise in a blue channel that's genuinely started ~4.6
  stops down.

## Sensor trailing vs. long exposures

At 300mm actual focal length on the D5500 sensor (3.92 µm pixel pitch), the
sky drifts at roughly 5.4 px/second on a fixed tripod. That makes a
straightforward Espenak-formula 4-second exposure smear about 27 pixels —
enough to matter for the outer corona's finer structure.

**The fix used here was per-step ISO on just the slow rungs**, not a
blanket exposure change:

```python
totality_bracket = {
    "shutter_speeds": [..., "1", "2"],
    "iso": 200,
    "iso_overrides": {"1": 320, "2": 500},
    ...
}
```

A rung at `2s @ ISO 500` collects marginally *more* signal (in
ISO-seconds) than the `4s @ ISO 200` it replaced, while halving the
trailing. The tradeoff is real, since higher ISO costs shot-noise headroom in
that one frame. But it's made up for by having more *passes* through
the bracket to stack (see below), which recovers noise that a single
longer exposure would have avoided trading away in the first place.
`camera.iso_for_step()` looks up the override for a given shutter speed,
falling back to the plan's base ISO; `run_eclipse.py` refuses to start if
an override key doesn't match an actual shutter speed in the plan.

## Why 8 steps, not a denser ladder

An early version used a full 1-stop ladder (14 steps, `1/2000` through
`4s`). Once shooting RAW, that turned out to be the wrong trade: RAW's
recovery latitude (roughly 2-3 stops) means adjacent 1-stop rungs are
largely redundant, but a 14-step pass is very long. Measured at this site's
effective bracket window (totality minus the diamond-ring bursts'
overrun on either side, roughly 77–81 seconds), the full ladder fit
barely *one* complete pass.

The shipped bracket uses 2-stop spacing through the fast/middle range,
tightening to 1-stop at the slow end (where recovery latitude helps
least, since you're fighting read noise rather than clipping), for 8
total steps. That fits **2-3 complete passes** in the same window. 

## Palindrome looping

```python
totality_bracket = {..., "palindrome": True}
```

Alternates direction each pass (forward, reverse, forward) so at the
seam between two passes the two slowest exposures land back-to-back.
That's deliberately the best possible pairing for stacking the most
delicate frames: minimal time between them means minimal change in sky
conditions and corona rotation. It also roughly halves the number of
full-range shutter-speed jumps between consecutive shots, which may
reduce real `set_config()` settle time.

## Framing for drift, not just for C2

On a fixed tripod, the sun visibly moves during totality. At this site's ~94-second totality, the total drift worked out
to be roughly 500 pixels: about 400 vertically (from the changing solar
altitude) and 300 horizontally, against a frame where the solar disk
itself is only ~700 pixels across. Framing the sun dead-center at C2
means it's badly off-center by C3.

The practical fix is a framing offset applied *before* totality starts:
position the sun above and "upstream" of center by roughly half the
expected drift, so it crosses the center of the frame at mid-totality
rather than starting there. This is a manual step, not automated —
`config.yaml`'s `audio_cues` includes a reminder in the minutes before
C2 specifically for this. Note, for calculating this you can run and use the framing UI. See its [readme](../framing-ui/README.md).

## Focusing

Two things rule out the obvious approaches for this target, both covered
in more depth in [`camera-notes.md`](camera-notes.md):

- **Bahtinov masks don't work on the sun or moon**. They're a
  point-source technique, and an extended disk washes the pattern into
  noise.
- **Autofocus can't be trusted during the automated sequence**; it
  re-triggers on every capture with no way to prevent it, and totality
  against a dark background gives it almost nothing to hunt against.

The standard technique used here instead: magnified live view (or a
downloaded full-resolution frame, since the LCD is dark while tethered)
aimed at the disk's *limb*, adjusted until crisp, then
the lens locked to manual focus mechanically. `focus_check.py` automates
the sharpness judgment: it scores a captured frame via variance of the
Laplacian (a standard blur-detection metric; sharp edges produce high
variance after an edge filter, blur produces low), calibrated against
your own in-focus and out-of-focus reference shots rather than a
universal threshold, since the right number is specific to your lens,
aperture, and how tightly you crop the region of interest.

## Dew and fogging

This isn't a software
problem, but it's worth building the physical mitigation with the same
seriousness as everything else here: a USB dew heater strip around the
lens barrel, a deep lens hood to reduce radiative cooling, and getting
the gear outside to reach ambient temperature well before it's needed
rather than carrying it out warm. `eclipse-focus-check`'s sharpness score
doubles as an early-warning check for this.