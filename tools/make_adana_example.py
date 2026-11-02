"""Generate the Adana EXAMPLE line: a plausible 15 km urban corridor with 24
stops, written as library/route/adana_ornek_hat.csv and
library/line/adana_ornek.yaml.

This is illustrative data for developing and demonstrating the line
simulator. It is NOT measured Adana data: the alignment, elevation, stop
spacing and boardings are synthetic. Replace with GTFS / AVL / ticketing
exports when they are available (see temsasim/network.py).

Run from the repository root:  python tools/make_adana_example.py
"""

from __future__ import annotations

import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIB = ROOT / "library"

STOPS = [  # name, km from start, peak boardings/h outbound, peak boardings/h inbound
    ("Otogar", 0.00, 110, 15),
    ("Sanayi Sitesi", 0.65, 25, 20),
    ("Hastane", 1.30, 45, 60),
    ("Yeni Cami", 1.95, 30, 35),
    ("Çarşı", 2.55, 80, 120),
    ("Büyüksaat", 3.10, 40, 60),
    ("5 Ocak Meydanı", 3.75, 90, 130),
    ("Vali Konağı", 4.35, 25, 30),
    ("Atatürk Parkı", 4.95, 35, 45),
    ("Adliye", 5.60, 20, 40),
    ("Stadyum", 6.25, 30, 25),
    ("Gazipaşa", 6.90, 35, 40),
    ("Belediye Evleri", 7.55, 40, 35),
    ("Toros Mahallesi", 8.20, 45, 30),
    ("Turgut Özal Bulvarı", 8.90, 30, 25),
    ("Mahfesığmaz", 9.55, 40, 30),
    ("Huzurevleri", 10.20, 35, 25),
    ("Güzelyalı", 10.90, 45, 30),
    ("Beyazevler", 11.55, 25, 20),
    ("Yurtlar", 12.25, 20, 60),
    ("Kampüs Kapısı", 12.95, 15, 70),
    ("Balcalı Hastanesi", 13.70, 10, 50),
    ("Fakülteler", 14.45, 5, 45),
    ("Çukurova Üniversitesi", 15.20, 0, 120),
]

TIMETABLE = [  # from, to, headway min, traffic level, demand multiplier
    (6, 7, 20, "akici", 0.35),
    (7, 9, 8, "yogun", 1.00),
    (9, 12, 12, "orta", 0.60),
    (12, 15, 12, "orta", 0.65),
    (15, 17, 10, "orta", 0.75),
    (17, 19, 8, "yogun", 0.95),
    (19, 21, 15, "akici", 0.45),
    (21, 23, 30, "akici", 0.20),
]


def main() -> None:
    L = STOPS[-1][1] * 1000.0
    d = np.arange(0.0, L + 1e-6, 10.0)
    rng = np.random.default_rng(42)
    # the plain by the river (about 20 m) rising toward the campus plateau (about 110 m),
    # with a gentle swell through the old town and the university hill at the end
    base = 20 + 55 * (d / L) ** 1.6 + 8 * np.sin(d / 900.0) + 6 * np.sin(d / 2300.0 + 1.0)
    base[d > 12000] += 25 * ((d[d > 12000] - 12000) / (L - 12000)) ** 1.3
    noise = np.cumsum(rng.normal(0, 0.05, d.size))
    elev = base + noise - noise[0]
    # speed limits: 50 km/h in the centre, 70 on the boulevard, 50 on campus
    v = np.full(d.size, 50.0)
    v[(d > 7000) & (d < 12000)] = 70.0
    stop_km = {round(s[1] * 1000.0 / 10.0) * 10.0 for s in STOPS}
    (LIB / "route").mkdir(parents=True, exist_ok=True)
    with open(LIB / "route" / "adana_ornek_hat.csv", "w", encoding="utf-8", newline="") as f:
        f.write("distance_m,elevation_m,speed_limit_kmh,stop\n")
        for k in range(d.size):
            f.write(f"{d[k]:.0f},{elev[k]:.2f},{v[k]:.0f},{1 if d[k] in stop_km else 0}\n")
    (LIB / "line").mkdir(parents=True, exist_ok=True)
    lines = ["kind: line", "code: ADN-Ö1", "name: Otogar – Çukurova Üniversitesi (ÖRNEK)",
             "example: true", "route: route/adana_ornek_hat.csv", "layover_min: 8",
             "notes: >", "  ÖRNEK VERİ. Güzergâh, rakım, duraklar ve yolcu sayıları temsilidir; Adana ölçümü",
             "  değildir. Gerçek GTFS / AVL / bilet verisi geldiğinde bu dosya onunla değiştirilir.",
             "", "# stops in outbound order; board = peak-hour boardings per hour outbound, board_in = inbound",
             "stops:"]
    for name, km, bo, bi in STOPS:
        lines.append(f"  - {{name: \"{name}\", board: {bo}, board_in: {bi}}}")
    lines += ["", "# hour bands: headway in minutes, traffic level, demand relative to the peak", "timetable:"]
    for a, b, h, tr, dem in TIMETABLE:
        lines.append(f"  - {{from: {a}, to: {b}, headway_min: {h}, traffic: {tr}, demand: {dem}}}")
    (LIB / "line" / "adana_ornek.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", LIB / "route" / "adana_ornek_hat.csv", "and", LIB / "line" / "adana_ornek.yaml")


if __name__ == "__main__":
    main()
