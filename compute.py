"""Find Mercury's double sunrises and double sunsets.

For each perihelion of Mercury in a date range, compute where on Mercury's
equator the Sun rises, sets and rises again (and, on the other side of the
planet, sets, rises and sets again). Print the observer longitude and the
UTC times of each horizon crossing.

Model:
- Positions: JPL DE440s through Skyfield (light-time corrected).
- Mercury's orientation: the IAU rotation elements that Stellarium uses
  (data/ssystem_major.ini): pole RA 281.0103 - 0.0328 T, pole Dec
  61.4155 - 0.0049 T, W = 329.5988 + 6.1385108 d (degrees; T in Julian
  centuries, d in days, from J2000 TDB).
- Longitudes are planetocentric, positive east, which is what Stellarium
  takes for a location on another planet.
- The horizon is a flat, smooth sphere with no atmosphere. Mercury's real
  hills and crater walls change the times at any given spot.

Usage: uv run compute.py [start-date] [days]
"""

import sys

import numpy as np
from skyfield.api import load
from skyfield.searchlib import find_minima

SUN_RADIUS_KM = 695_700.0


def body_matrix(t):
    """Rotation matrices (n, 3, 3) from ICRF to Mercury body-fixed axes."""
    d = t.tdb - 2451545.0
    T = d / 36525.0
    ra = np.radians(281.0103 - 0.0328 * T)
    dec = np.radians(61.4155 - 0.0049 * T)
    w = np.radians((329.5988 + 6.1385108 * d) % 360.0)

    def rz(a):
        c, s = np.cos(a), np.sin(a)
        z, o = np.zeros_like(a), np.ones_like(a)
        return np.stack([np.stack([c, s, z], -1),
                         np.stack([-s, c, z], -1),
                         np.stack([z, z, o], -1)], -2)

    def rx(a):
        c, s = np.cos(a), np.sin(a)
        z, o = np.zeros_like(a), np.ones_like(a)
        return np.stack([np.stack([o, z, z], -1),
                         np.stack([z, c, s], -1),
                         np.stack([z, -s, c], -1)], -2)

    return rz(w) @ rx(np.pi / 2 - dec) @ rz(np.pi / 2 + ra)


def sun_in_body_frame(eph, ts, jd):
    """Unit vector to the Sun in Mercury's body frame, and distance in km."""
    t = ts.tdb_jd(jd)
    v = eph["mercury"].at(t).observe(eph["sun"]).position.km  # (3, n)
    dist = np.linalg.norm(v, axis=0)
    u = (v / dist).T  # (n, 3)
    b = np.einsum("nij,nj->ni", body_matrix(t), u)
    return b, dist


def subsolar_lon(b):
    """Planetocentric east longitude of the subsolar point, unwrapped, degrees."""
    return np.degrees(np.unwrap(np.arctan2(b[:, 1], b[:, 0])))


def crossings(jd, alt, level):
    """Times where alt crosses level, with direction (+1 rising, -1 setting)."""
    s = alt - level
    idx = np.where(np.sign(s[:-1]) != np.sign(s[1:]))[0]
    out = []
    for i in idx:
        f = s[i] / (s[i] - s[i + 1])
        out.append((jd[i] + f * (jd[i + 1] - jd[i]), 1 if s[i + 1] > s[i] else -1))
    return out


def fmt(ts, jd):
    return ts.tdb_jd(jd).utc_strftime("%Y-%m-%d %H:%M UTC")


def analyse(eph, ts, t_peri):
    step = 2.0 / 1440  # 2 minutes
    jd = np.arange(t_peri.tdb - 12, t_peri.tdb + 12, step)
    b, dist = sun_in_body_frame(eph, ts, jd)
    lon = subsolar_lon(b)
    dlon = np.diff(lon)
    # The subsolar point normally moves west (longitude decreasing).
    # Near perihelion it turns back east for a few days.
    retro = np.where(dlon > 0)[0]
    if len(retro) == 0:
        print("  no retrograde motion found")
        return
    i0, i1 = retro[0], retro[-1] + 1
    lon_lo, lon_hi = lon[i0], lon[i1]  # turning points
    amp = lon_hi - lon_lo
    sun_radius = np.degrees(np.arcsin(SUN_RADIUS_KM / dist[(i0 + i1) // 2]))
    print(f"  Sun reverses: {fmt(ts, jd[i0])} to {fmt(ts, jd[i1])}"
          f" ({jd[i1] - jd[i0]:.1f} days), back by {amp:.2f} deg;"
          f" Sun's apparent radius {sun_radius:.2f} deg")

    # "centre" sites: the Sun's centre crosses the horizon three times, but
    # the disc (wider than the 1.1 deg reversal) never fully clears or
    # fully disappears. "full" sites, one solar radius further into the
    # night side: the top edge crosses three times, so the Sun peeks up,
    # disappears completely, then rises for good.
    mid = 0.5 * (lon_lo + lon_hi)
    for label, obs_lon in (("double sunrise (centre)", mid - 90.0),
                           ("double sunrise (full)", mid - 90.0 - sun_radius),
                           ("double sunset (centre)", mid + 90.0),
                           ("double sunset (full)", mid + 90.0 + sun_radius)):
        obs_lon = (obs_lon + 180.0) % 360.0 - 180.0
        o = np.radians(obs_lon)
        up = np.array([np.cos(o), np.sin(o), 0.0])  # equator
        alt = np.degrees(np.arcsin(b @ up))
        ew = "E" if obs_lon >= 0 else "W"
        print(f"  {label}: equator, longitude {obs_lon:+.2f} "
              f"({abs(obs_lon):.2f} {ew}, planetocentric)")
        for name, level in (("centre", 0.0), ("upper limb", -sun_radius),
                            ("lower limb", sun_radius)):
            ev = crossings(jd, alt, level)
            s = ", ".join(f"{'rise' if d > 0 else 'set'} {fmt(ts, j)}"
                          for j, d in ev)
            print(f"    {name:10s}: {s}")
        k = np.argmin(np.abs(jd - jd[(i0 + i1) // 2]))
        lo, hi = max(i0 - 1000, 0), min(i1 + 1000, len(jd) - 1)
        seg = alt[lo:hi]
        print(f"    centre altitude range in window: {seg.min():+.2f} to"
              f" {seg.max():+.2f} deg; at mid-reversal {alt[k]:+.2f} deg")


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2026-10-16"
    days = float(sys.argv[2]) if len(sys.argv) > 2 else 400
    ts = load.timescale()
    eph = load("de440s.bsp")
    y, m, d = map(int, start.split("-"))
    t0, t1 = ts.utc(y, m, d), ts.utc(y, m, d + days)

    def dist(t):
        return (eph["sun"].at(t) - eph["mercury"].at(t)).distance().km

    dist.step_days = 5
    times, _ = find_minima(t0, t1, dist)
    for t in times:
        print(f"Perihelion {t.utc_strftime('%Y-%m-%d %H:%M UTC')}")
        analyse(eph, ts, t)
        print()


if __name__ == "__main__":
    main()
