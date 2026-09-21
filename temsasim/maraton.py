"""Maraton (GEN3) fuel and brake-pad regressions.

Source: "Maraton (GEN3) Fuel and Brake-Pad Regression Model", R&D Test Center,
Vehicle Integration, September 2026. Coefficients are copied from that report
and checked against its own tornado charts and life table (see tests).

Both models are empirical fits to a diesel intercity coach fleet. They are
evaluated here, not re-derived; the simulator supplies the speed-derived inputs
(mean speed, speed-band shares) from any drive cycle or logged trace. Inputs the
simulator cannot produce -- engine-speed share, accelerator pedal -- are taken
as inputs. Every input outside the range the models were trained on is flagged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# Training ranges quoted in the report (P10-P90 of the fleet data).
FUEL_RANGE = {
    "ac_pct": (0.0, 48.0), "v_mean_kmh": (74.0, 81.0), "rpm1200_pct": (11.0, 23.0),
    "pedal_pct": (72.0, 81.0), "ambient_c": (11.0, 27.0), "idle_l100": (0.5, 1.8),
}
PAD_RANGE = (8.0, 14.2)          # v0-30 share of the intercity training vehicles [%]
FUEL_LOOCV_RMSE = 0.39           # L/100 km, out of sample
ND_BODY_FACTOR = 1 - 0.065       # Mersin measured, ND vs CD


@dataclass
class FuelInputs:
    idle_l100: float = 1.0          # measured idle fuel, L/100 km (enters 1:1)
    ac_pct: float = 0.0             # A/C compressor usage, % of time
    v_mean_kmh: float = 78.0        # mean speed while driving
    rpm1200_pct: float = 17.0       # share of time at >= 1200 rpm
    pedal_pct: float = 76.0         # mean accelerator pedal
    week: float = 30.0              # week of the year, 1-52
    gt102_pct: float = 0.0          # share of time above 102 km/h
    gt80_pct: float = 55.0          # share of time above 80 km/h
    ambient_c: float = 20.0
    kale_radiator: bool = False
    body: str = "CD"                # "CD" current design, "ND" new design


def fuel_l100(x: FuelInputs) -> dict:
    """Fuel [L/100 km] with each term, and warnings for out-of-range inputs.

    Units follow the report and were checked against its tornado chart:
    the >102 km/h share enters in percentage points, the >80 km/h share in the
    temperature term as a fraction (in points it would give -61 L/100 km).
    """
    w = 2 * math.pi * x.week / 52.0
    terms = {
        "idle": x.idle_l100,
        "intercept": 13.26,
        "ac": 0.0519 * (x.ac_pct / x.v_mean_kmh * 100.0),
        "time_per_km": 3.29 * (100.0 / x.v_mean_kmh),
        "rpm1200": 0.1203 * x.rpm1200_pct,
        "pedal": 0.0576 * x.pedal_pct,
        "season": 0.641 * math.sin(w) + 0.730 * math.cos(w),
        "gt102": 0.035 * x.gt102_pct,
        "temperature": -0.074 * (x.ambient_c - 20.0) * (x.gt80_pct / 100.0),
        "kale": (0.0012 * 25.0 * max(x.ambient_c - 20.0, 0.0)) if x.kale_radiator else 0.0,
    }
    subtotal = sum(terms.values())
    if x.body.upper() == "ND":
        # Applied as a factor on the total: the report gives the body effect as
        # a measured percentage. Whether it applies to the idle part too is an
        # open question for the report's authors.
        terms["body_nd"] = subtotal * (ND_BODY_FACTOR - 1)
    total = sum(terms.values())

    warnings = []
    for key, (lo, hi) in FUEL_RANGE.items():
        val = getattr(x, key)
        if not lo <= val <= hi:
            warnings.append(f"{key} = {val:g} eğitim aralığının ({lo:g}–{hi:g}) dışında: ekstrapolasyon")
    if x.kale_radiator and x.ambient_c > 32:
        warnings.append("Kale cezası 32 °C üstünde ölçülmedi (ölçüm aralığı 26–32 °C)")
    return {"fuel_l100": total, "terms": terms, "rmse_l100": FUEL_LOOCV_RMSE, "warnings": warnings}


# ---------------------------------------------------------------- brake pads
WHEELS = ("front_right", "front_left", "rear")


def pad_wear(v030_pct: float) -> dict:
    """Wear rate [% per 10 000 km] per wheel and km from 100 % to 20 %.

    Wear = 0.092 v + 0.0072 v^2 + 0.045 (front axle) + 0.285 (front-right)
    with v = share of time in the 0-30 km/h band [%]. The front-right wheel
    sets the life. Trained on retarder-equipped diesel coaches: it describes
    friction braking where the retarder does little, i.e. at low speed.
    """
    v = float(v030_pct)
    base = 0.092 * v + 0.0072 * v * v
    rate = {"rear": base, "front_left": base + 0.045, "front_right": base + 0.045 + 0.285}
    life = {k: 80.0 / r * 10_000 if r > 0 else float("inf") for k, r in rate.items()}
    warnings = []
    lo, hi = PAD_RANGE
    if v > hi + 1:
        warnings.append(f"v₀₋₃₀ = %{v:.1f} eğitim aralığının (%{lo:g}–{hi:g}) üstünde: rapor şehir içi "
                        f"kullanımda aşınmayı %31–42 fazla tahmin ettiğini bildiriyor")
    elif v < lo - 1:
        warnings.append(f"v₀₋₃₀ = %{v:.1f} eğitim aralığının altında")
    return {"v030_pct": v, "rate_pct_per_10k": rate, "life_km_to_20pct": life,
            "limiting_wheel": "front_right", "warnings": warnings}


# ---------------------------------------------------------------- from a trace
def speed_stats(speed_mps, time_s=None, *, moving_kmh: float = 2.0,
                v030_includes_standstill: bool = True) -> dict:
    """Speed-derived model inputs from any trace (simulated or logged).

    Two definitions are assumptions until confirmed by the report's authors:
    v_mean is taken over driving time only (idle fuel is a separate input), and
    the 0-30 km/h band includes standstill.
    """
    v = np.asarray(speed_mps, dtype=float) * 3.6
    dt = np.gradient(np.asarray(time_s, dtype=float)) if time_s is not None else np.ones_like(v)
    T = dt.sum()
    moving = v > moving_kmh
    share = lambda m: float(dt[m].sum() / T * 100.0)
    band = (v <= 30.0) if v030_includes_standstill else (moving & (v <= 30.0))
    return {
        "v_mean_kmh": float((v[moving] * dt[moving]).sum() / max(dt[moving].sum(), 1e-9)),
        "gt80_pct": share(v > 80.0), "gt102_pct": share(v > 102.0), "v030_pct": share(band),
    }
