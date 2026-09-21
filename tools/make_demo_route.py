"""Generate a synthetic hilly city route, so the route-based path is testable
before anyone has exported real GPS data.

Replace this entirely once you have real alignments. The export you want from a
telematics or GIS system is: distance along the route, elevation from a DEM
(SRTM at 30 m is adequate for a bus; anything from a consumer GPS altimeter is
not), the posted speed limit, and the bus-stop positions. Those four columns are
the whole interface.
"""

from __future__ import annotations

import csv
import pathlib

import numpy as np

OUT = pathlib.Path(r"c:/Users/User/Desktop/temsasim/library/route/demo_hilly_city.csv")


def main() -> None:
    rng = np.random.default_rng(7)
    length_m = 12_400
    step = 10.0
    d = np.arange(0.0, length_m + step, step)

    # Elevation: two long climbs plus gentle undulation. Peak gradient stays
    # under about 8 %, which is steep but realistic for a hilly city.
    elev = (
        62.0
        + 48.0 * np.sin(np.pi * d / 6200.0) ** 2
        + 26.0 * np.sin(np.pi * d / 2100.0 + 1.1)
        + 5.0 * np.sin(np.pi * d / 520.0)
    )
    elev = np.convolve(elev, np.ones(21) / 21, mode="same")
    elev[:21] = elev[21]
    elev[-21:] = elev[-22]

    # Speed limits: 50 km/h arterial, 30 km/h through two calmed sections.
    limit = np.full(d.size, 50.0)
    limit[(d > 3200) & (d < 4800)] = 30.0
    limit[(d > 8600) & (d < 9700)] = 30.0

    # Stops roughly every 380 m with jitter, which is dense urban spacing.
    stops = []
    s = 260.0
    while s < length_m - 200:
        stops.append(round(s / step) * step)
        s += 380.0 + rng.uniform(-90.0, 140.0)
    stopset = set(stops)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["distance_m", "elevation_m", "speed_limit_kmh", "stop"])
        for i, x in enumerate(d):
            w.writerow([f"{x:.1f}", f"{elev[i]:.2f}", f"{limit[i]:.0f}",
                        1 if x in stopset else 0])

    grade = np.gradient(elev, d) * 100.0
    print(f"wrote {OUT}")
    print(f"  {length_m/1000:.2f} km, {len(stops)} stops "
          f"({len(stops)/(length_m/1000):.1f}/km)")
    print(f"  elevation {elev.min():.0f}-{elev.max():.0f} m, "
          f"gradient {grade.min():+.1f}% to {grade.max():+.1f}%")
    print(f"  total climb {np.sum(np.clip(np.diff(elev), 0, None)):.0f} m")


if __name__ == "__main__":
    main()
