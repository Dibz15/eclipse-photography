"""
Skyfield-based local circumstances calculator for a solar eclipse.

Skyfield ships clean almanac helpers for lunar eclipses, solstices, and
equinoxes, but *not* for local solar eclipse contact times — those depend
on the observer's topocentric position, which needs a root-find on the
angular separation between Sun and Moon vs. the sum/difference of their
apparent angular radii. That's what this module does.

Treat the output as a first-pass estimate, accurate to within a couple of
seconds once you're using the DE440s ephemeris. Cross-check against
EclipseWise or the USNO calculator for your exact site once you've scouted
it, then hard-code the confirmed times into config.yaml's `timings_utc`
block with `--write`. That gives you belt-and-suspenders: a script that
*can* compute it, plus verified numbers that don't depend on your internet
connection working on eclipse morning.

Usage:
    uv run eclipse-timings                # print only
    uv run eclipse-timings --write         # also write timings_utc into config.yaml
"""

from __future__ import annotations

import argparse
from math import asin, degrees
from pathlib import Path

import yaml
from skyfield.api import load, wgs84

from .tzutil import resolve_tz

SUN_RADIUS_KM = 696_000.0
MOON_RADIUS_KM = 1_737.4

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
EPHEMERIS_PATH = PROJECT_ROOT / "de440s.bsp"


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def ensure_ephemeris(path: Path = EPHEMERIS_PATH) -> Path:
    if not path.exists():
        print(f"Downloading DE440s ephemeris to {path} (~30MB, one-time)...")
        load.download(url="de440s.bsp", filename=str(path))
    return path


class LocalCircumstances:
    """Sun/Moon angular geometry as seen from a fixed observer, used to
    root-find the four solar eclipse contact times (and time of max
    eclipse) at that location."""

    def __init__(self, cfg: dict):
        loc = cfg["location"]
        self.eph = load(str(ensure_ephemeris()))
        self.ts = load.timescale()
        self.topo = wgs84.latlon(
            loc["latitude"], loc["longitude"], elevation_m=loc.get("elevation_m", 0)
        )
        self.sun = self.eph["sun"]
        self.moon = self.eph["moon"]
        self.observer = self.eph["earth"] + self.topo

    def _angular_radius_deg(self, body, t, physical_radius_km: float) -> float:
        astrometric = self.observer.at(t).observe(body).apparent()
        _, _, distance = astrometric.radec()
        return degrees(asin(physical_radius_km / distance.km))

    def separation_deg(self, t) -> float:
        s = self.observer.at(t).observe(self.sun).apparent()
        m = self.observer.at(t).observe(self.moon).apparent()
        return s.separation_from(m).degrees

    def sun_radius_deg(self, t) -> float:
        return self._angular_radius_deg(self.sun, t, SUN_RADIUS_KM)

    def moon_radius_deg(self, t) -> float:
        return self._angular_radius_deg(self.moon, t, MOON_RADIUS_KM)

    def f_outer(self, t) -> float:
        """Zero at C1/C4: limbs just touching (separation == sum of radii)."""
        return self.separation_deg(t) - (self.sun_radius_deg(t) + self.moon_radius_deg(t))

    def f_inner(self, t) -> float:
        """Zero at C2/C3: Moon's disk just fully covers Sun's (total eclipse only)."""
        return self.separation_deg(t) - (self.moon_radius_deg(t) - self.sun_radius_deg(t))

    def _scan_sign_changes(self, f, t0, t1, step_minutes: float = 2.0):
        n = max(int((t1 - t0) * 24 * 60 / step_minutes), 2)
        times = self.ts.linspace(t0, t1, n + 1)
        values = [f(t) for t in times]
        crossings = []
        for i in range(len(values) - 1):
            if (values[i] < 0) != (values[i + 1] < 0):
                crossings.append((times[i], times[i + 1]))
        return crossings

    def _refine(self, f, t_lo, t_hi, tol_seconds: float = 0.5):
        from scipy.optimize import brentq

        lo_jd, hi_jd = t_lo.tt, t_hi.tt
        root_jd = brentq(lambda jd: f(self.ts.tt_jd(jd)), lo_jd, hi_jd, xtol=tol_seconds / 86400)
        return self.ts.tt_jd(root_jd)

    def find_contacts(self, date_str: str) -> dict:
        y, m, d = map(int, date_str.split("-"))
        t0 = self.ts.utc(y, m, d, 0)
        t1 = self.ts.utc(y, m, d, 23, 59, 59)

        outer = self._scan_sign_changes(self.f_outer, t0, t1)
        inner = self._scan_sign_changes(self.f_inner, t0, t1)

        if len(outer) < 2:
            raise RuntimeError(
                "No two outer-contact crossings found on this date/location — "
                "check lat/lon/date in config.yaml, or there's no eclipse "
                "visible from here at all."
            )

        c1 = self._refine(self.f_outer, *outer[0])
        c4 = self._refine(self.f_outer, *outer[-1])

        c2 = c3 = None
        if len(inner) >= 2:
            c2 = self._refine(self.f_inner, *inner[0])
            c3 = self._refine(self.f_inner, *inner[-1])

        # Max eclipse = minimum separation between C1 and C4.
        fine = self.ts.linspace(c1, c4, 500)
        seps = [self.separation_deg(t) for t in fine]
        max_ecl = fine[min(range(len(seps)), key=lambda i: seps[i])]

        return {"C1": c1, "C2": c2, "max": max_ecl, "C3": c3, "C4": c4}


def format_utc_hms(t) -> str:
    return t.utc_datetime().strftime("%H:%M:%S")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument(
        "--write",
        action="store_true",
        help="write the computed times into config.yaml's timings_utc block "
        "(cross-check against EclipseWise/USNO first!)",
    )
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    lc = LocalCircumstances(cfg)
    contacts = lc.find_contacts(cfg["date"])

    tz = resolve_tz(cfg.get("timezone"))

    print(
        f"Local circumstances for {cfg['location']['latitude']}, "
        f"{cfg['location']['longitude']} on {cfg['date']}:\n"
    )
    for label in ("C1", "C2", "max", "C3", "C4"):
        t = contacts[label]
        if t is None:
            print(f"  {label:>4}: -- (no totality at this location)")
            continue
        line = f"  {label:>4}: {format_utc_hms(t)} UTC"
        if tz is not None:
            local = t.utc_datetime().astimezone(tz)
            line += f"   ({local.strftime('%Y-%m-%d %H:%M:%S %Z')})"
        print(line)

    print(
        "\nThis is a Skyfield first-pass estimate. Cross-check against "
        "EclipseWise or the USNO calculator for your exact coordinates "
        "once you've scouted your spot, then re-run with --write once "
        "you're confident — so eclipse morning doesn't depend on this "
        "script or your internet connection."
    )

    if args.write:
        if not all(contacts[k] is not None for k in ("C1", "C2", "C3", "C4")):
            raise SystemExit(
                "Refusing to write: not all four contacts were found "
                "(no totality at this location?)."
            )
        cfg.setdefault("timings_utc", {})
        for label in ("C1", "C2", "max", "C3", "C4"):
            cfg["timings_utc"][label] = format_utc_hms(contacts[label])
        with open(args.config, "w") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)
        print(f"\nWrote timings_utc into {args.config}")


if __name__ == "__main__":
    main()