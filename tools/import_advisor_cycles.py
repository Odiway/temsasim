"""Extract drive cycles from the NREL ADVISOR 2002 .mat library into plain CSV.

ADVISOR stores each schedule as `cyc_mph` = [time_s, speed_mph]. We convert to
SI (s, m/s) and keep the raw trace: no resampling, no smoothing, so the CSV is
traceable back to the source file.
"""
import sys, pathlib, csv
import numpy as np
from scipy.io import loadmat

SRC = pathlib.Path(r"c:/Users/User/Desktop/temsasim/advisor/data/drive_cycle")
DST = pathlib.Path(r"c:/Users/User/Desktop/temsasim/library/cycle")
MPH_TO_MS = 0.44704

# Cycles that matter for a bus OEM. Everything else stays in ADVISOR.
WANTED = [
    "CYC_CBDBUS", "CYC_CBD14", "CYC_MANHATTAN", "CYC_NewYorkBus",
    "CYC_UKBUS6", "CYC_WVUCITY", "CYC_WVUSUB", "CYC_WVUINTER",
    "CYC_UDDSHDV", "CYC_BUSRTE", "CYC_HHDDT65",
]

def main():
    DST.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in WANTED:
        f = SRC / f"{name}.mat"
        if not f.exists():
            print(f"  skip {name}: not found"); continue
        try:
            m = loadmat(f)
        except Exception as e:
            print(f"  skip {name}: {e}"); continue
        key = next((k for k in ("cyc_mph", "cyc_kph", "cyc_mps") if k in m), None)
        if key is None:
            print(f"  skip {name}: no cyc_* variable, keys={[k for k in m if not k.startswith('__')]}")
            continue
        arr = np.asarray(m[key], dtype=float)
        t = arr[:, 0]
        if key == "cyc_mph":
            v = arr[:, 1] * MPH_TO_MS
        elif key == "cyc_kph":
            v = arr[:, 1] / 3.6
        else:
            v = arr[:, 1]
        out = DST / f"{name.replace('CYC_','').lower()}.csv"
        with out.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["time_s", "speed_mps"])
            for ti, vi in zip(t, v):
                w.writerow([f"{ti:.3f}", f"{vi:.5f}"])
        dist_km = float(np.trapezoid(v, t)) / 1000.0
        dur = float(t[-1] - t[0])
        rows.append((out.name, len(t), dur, dist_km, float(v.max()*3.6), dist_km/(dur/3600)))
        print(f"  {out.name:20s} n={len(t):5d}  {dur:7.1f}s  {dist_km:6.3f}km  "
              f"vmax={v.max()*3.6:5.1f}km/h  vavg={dist_km/(dur/3600):5.1f}km/h")
    print(f"\n{len(rows)} cycles -> {DST}")

if __name__ == "__main__":
    main()
