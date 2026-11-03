"""Timeline and event feed for the 3D playback view.

The 3D scene replays a finished simulation: it does not compute anything, it
only interpolates what the solver already produced. Everything it shows --
where the bus is, how fast, what the pack is doing, why the cabin cooled --
comes from this module, so the picture can never disagree with the report.

Two outputs:

* ``timeline`` -- aligned arrays (time, distance, speed, elevation, powers,
  temperatures, cumulative energies) at up to ``max_points`` samples.
* ``events`` -- a short, readable list of the moments worth pointing at: stops
  with their cabin temperature drop, steep climbs, regen the pack refused,
  climate saturation, SOC milestones. Turkish text, because this feed is read
  by the people running the simulation, not by the solver.
"""

from __future__ import annotations

import numpy as np

from ..cycle import DriveCycle, Route
from ..messages import tr_num
from ..simulator import SimulationResult
from ..units import KWH, RPM


def _stride(n: int, max_points: int) -> np.ndarray:
    if n <= max_points:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, max_points).round().astype(int))


def _elevation(result: SimulationResult, base_m: float) -> np.ndarray:
    """Elevation the solver actually drove, integrated from its own gradient."""
    s = result.series
    ds = np.diff(s["distance_m"], prepend=0.0)
    return base_m + np.cumsum(np.sin(s["grade_rad"]) * ds)


def _runs(mask: np.ndarray) -> list:
    """(start, end) index pairs of consecutive True runs."""
    out, i, n = [], 0, mask.size
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            out.append((i, j - 1))
            i = j
        else:
            i += 1
    return out


def timeline(result: SimulationResult, *, base_elevation_m: float = 0.0,
             max_points: int = 2400) -> dict:
    s = result.series
    dt = result.dt_s
    elev = _elevation(result, base_elevation_m)

    def cum(arr_w, positive_only=False, negative_only=False):
        a = np.asarray(arr_w, dtype=float)
        if positive_only:
            a = np.clip(a, 0, None)
        if negative_only:
            a = -np.clip(a, None, 0)
        return np.cumsum(a) * dt / KWH

    cols = {
        "t": s["time_s"],
        "d": s["distance_m"],
        "v": s["speed_mps"] * 3.6,
        "grade": np.tan(s["grade_rad"]) * 100.0,
        "elev": elev,
        "soc": s["soc"] * 100.0,
        "p_batt": s["p_battery_w"] / 1e3,
        "p_motor": s["p_motor_elec_w"] / 1e3,
        "p_hvac": s["p_hvac_w"] / 1e3,
        "p_aux": s["p_aux_w"] / 1e3,
        "p_thermal": s["p_pack_thermal_w"] / 1e3,
        "regen_lost": s["regen_lost_w"] / 1e3,
        "cabin": s["cabin_c"],
        "pack": s["pack_c"],
        "pax": s["passengers"],
        "doors": s["doors_open"],
        "e_used": cum(s["p_battery_w"]),
        "e_trac": cum(s["p_motor_elec_w"], positive_only=True),
        "e_regen": cum(s["p_motor_elec_w"], negative_only=True),
        "e_hvac": cum(s["p_hvac_w"]),
        "e_aux": cum(s["p_aux_w"]),
        "e_thermal": cum(s["p_pack_thermal_w"]),
    }
    idx = _stride(s["time_s"].size, max_points)
    digits = {"t": 1, "d": 1, "doors": 0}
    return {k: np.round(v[idx], digits.get(k, 3)).tolist() for k, v in cols.items()}


def events(result: SimulationResult) -> list:
    """The moments worth pointing at, in time order."""
    s = result.series
    t, d = s["time_s"], s["distance_m"]
    ev: list = []

    # --- stops ------------------------------------------------------------
    for k, (a, b) in enumerate(_runs(s["doors_open"] > 0.5), start=1):
        drop = s["cabin_c"][a] - s["cabin_c"][b]
        dwell = t[b] - t[a] + result.dt_s
        pax = s["passengers"][a]
        detail = f"{tr_num(dwell, 0)} s bekleme, {tr_num(pax, 0)} yolcu"
        if abs(drop) >= 0.3:
            word = "düştü" if drop > 0 else "yükseldi"
            detail += (f"; kapılar açıkken kabin {tr_num(s['cabin_c'][a], 1)} → "
                       f"{tr_num(s['cabin_c'][b], 1)} °C'ye {word}")
        ev.append({"t": float(t[a]), "d": float(d[a]), "kind": "stop",
                   "title": f"Durak {k}", "text": detail})

    # --- steep climbs and descents ----------------------------------------
    g = np.tan(s["grade_rad"]) * 100.0
    for sign, kind, word in ((1, "climb", "Yokuş"), (-1, "descent", "İniş")):
        for a, b in _runs(sign * g > 5.0):
            if d[b] - d[a] < 60:
                continue
            peak = float(np.max(sign * g[a:b + 1]))
            e = float(np.sum(s["p_battery_w"][a:b + 1]) * result.dt_s / KWH)
            txt = (f"%{tr_num(peak, 1)} eğime kadar, {tr_num(d[b] - d[a], 0)} m; "
                   + (f"bu bölümde {tr_num(e, 2)} kWh harcandı" if e > 0
                      else f"bu bölümde {tr_num(-e, 2)} kWh geri kazanıldı"))
            ev.append({"t": float(t[a]), "d": float(d[a]), "kind": kind,
                       "title": f"{word} başladı", "text": txt})

    # --- refused regen ------------------------------------------------------
    for a, b in _runs(s["regen_lost_w"] > 5_000):
        lost = float(np.sum(s["regen_lost_w"][a:b + 1]) * result.dt_s / KWH)
        if lost < 0.01:
            continue
        ev.append({"t": float(t[a]), "d": float(d[a]), "kind": "regen",
                   "title": "Rejenerasyon reddedildi",
                   "text": (f"Paket {tr_num(s['pack_c'][a], 1)} °C, SOC %{tr_num(100 * s['soc'][a], 0)}: "
                            f"{tr_num(lost * 1000, 0)} Wh sürtünmeli frene gitti")})

    # --- climate saturation (first time only) --------------------------------
    hv = s["hvac_q_w"]
    cap = max(result.vehicle.hvac.heating_capacity_w, 1.0)
    sat = np.abs(hv) >= 0.995 * min(cap, result.vehicle.hvac.cooling_capacity_w)
    if np.any(sat):
        i = int(np.argmax(sat))
        ev.append({"t": float(t[i]), "d": float(d[i]), "kind": "hvac",
                   "title": "Klima tam kapasitede",
                   "text": f"Kabin {tr_num(s['cabin_c'][i], 1)} °C; sistem ayar sıcaklığını tutamıyor"})

    # --- traffic: red lights and stop-and-go ---------------------------------
    meta = result.cycle.meta or {}
    for e in meta.get("signal_stops", []):
        wait = e["t_go"] - e["t0"]
        q = e["queue"]
        ev.append({"t": float(e["t0"]), "d": float(e["d_line"]), "kind": "signal",
                   "title": "Kırmızı ışık",
                   "text": f"{tr_num(wait, 0)} s bekleme" + (f", önde {q} araç" if q else ", ilk sırada")})
    for j in meta.get("jams", []):
        ev.append({"t": float(j["t0"]), "d": float(j["d"]), "kind": "jam",
                   "title": "Trafik sıkışıklığı",
                   "text": (f"{tr_num(j['d_end'] - j['d'], 0)} m dur-kalk: {len(j['halts'])} kez durdu, "
                            f"{tr_num(j['t1'] - j['t0'], 0)} s sürdü")})

    # --- SOC milestones -------------------------------------------------------
    soc = s["soc"] * 100.0
    start = int(np.floor(soc[0] / 5.0) * 5)
    for level in range(start, 0, -5):
        hit = np.nonzero(soc <= level)[0]
        if hit.size and level < soc[0]:
            i = int(hit[0])
            ev.append({"t": float(t[i]), "d": float(d[i]), "kind": "soc",
                       "title": f"SOC %{level}", "text": f"{tr_num(d[i] / 1000, 2)} km'de"})

    ev.sort(key=lambda e: e["t"])
    return ev[:400]


def profile_from_route(route: Route) -> dict:
    from ..traffic import place_signals
    step = max(1, route.distance_m.size // 1500)
    sig, synthetic = place_signals(float(route.distance_m[-1]), route.stop_positions_m,
                                   given=route.signal_positions_m)
    return {"d": np.round(route.distance_m[::step], 1).tolist(),
            "elev": np.round(route.elevation_m[::step], 2).tolist(),
            "stops": [round(float(x), 1) for x in route.stop_positions_m],
            "signals": [g.as_dict() for g in sig], "signals_synthetic": synthetic,
            "length_m": float(route.distance_m[-1]), "name": route.name}


def profile_from_cycle(cycle: DriveCycle) -> dict:
    c = cycle.resample(1.0)
    d = np.concatenate([[0.0], np.cumsum(c.speed_mps[:-1])])
    stops = [float(d[a]) for a, _ in _runs(c.doors_open)]
    step = max(1, d.size // 1500)
    return {"d": np.round(d[::step], 1).tolist(), "elev": [0.0] * len(d[::step]),
            "stops": [round(x, 1) for x in stops], "length_m": float(d[-1]), "name": c.name}


# ---------------------------------------------------------------- diesel
def timeline_diesel(result, *, base_elevation_m: float = 0.0, max_points: int = 2400) -> dict:
    """Timeline of a diesel run, in the same shape the 3D view already reads.

    The battery keys are mapped to their diesel counterparts so the scene
    needs no second code path: ``p_batt`` and ``p_motor`` carry engine shaft
    power, ``soc`` the fuel left in the tank, ``regen_lost`` the friction
    braking power (red discs), ``e_used`` litres burned. Diesel-only keys
    (``rpm``, ``gear``, ``fuel_lph``, ``l100``) are added for the telemetry.
    """
    s = result.series
    dt = result.dt_s
    eng = result.vehicle.engine
    elev = _elevation(result, base_elevation_m)
    fuel_l = s["fuel_cum_g"] / eng.fuel_density_kg_per_m3
    km = np.maximum(s["distance_m"] / 1000.0, 1e-6)
    tank = max(float(result.vehicle.fuel_tank_l), 1.0)

    def cum(arr_w):
        return np.cumsum(np.asarray(arr_w, dtype=float)) * dt / KWH

    cols = {
        "t": s["time_s"], "d": s["distance_m"], "v": s["speed_mps"] * 3.6,
        "grade": np.tan(s["grade_rad"]) * 100.0, "elev": elev,
        "soc": np.clip((tank - fuel_l) / tank * 100.0, 0, 100),
        "p_batt": s["p_engine_w"] / 1e3, "p_motor": s["p_engine_w"] / 1e3,
        "p_hvac": s["p_hvac_w"] / 1e3, "p_aux": (s["p_aux_w"] + s["p_fan_w"]) / 1e3,
        "p_thermal": np.zeros_like(s["time_s"]), "regen_lost": s["p_friction_brake_w"] / 1e3,
        "cabin": s["cabin_c"], "pack": s["cabin_c"], "pax": s["passengers"], "doors": s["doors_open"],
        "e_used": fuel_l, "e_trac": cum(np.clip(s["p_engine_w"], 0, None)),
        "e_regen": np.zeros_like(s["time_s"]), "e_hvac": cum(s["p_hvac_w"]),
        "e_aux": cum(s["p_aux_w"] + s["p_fan_w"]), "e_thermal": np.zeros_like(s["time_s"]),
        "rpm": s["engine_speed_rads"] / RPM, "gear": s["gear"],
        "fuel_lph": (s["fuel_g_per_s"] + s["heater_fuel_g_per_s"]) * 3600.0 / eng.fuel_density_kg_per_m3,
        "l100": np.where(km > 0.2, fuel_l / km * 100.0, 0.0),
        "e_brake": cum(s["p_friction_brake_w"]), "e_engbrake": cum(s["p_engine_brake_w"]),
        "e_heater": np.cumsum(s["heater_fuel_g_per_s"]) * dt / eng.fuel_density_kg_per_m3,
    }
    idx = _stride(s["time_s"].size, max_points)
    digits = {"t": 1, "d": 1, "doors": 0, "gear": 0}
    return {k: np.round(v[idx], digits.get(k, 3)).tolist() for k, v in cols.items()}


def events_diesel(result) -> list:
    s = result.series
    eng = result.vehicle.engine
    t, d = s["time_s"], s["distance_m"]
    ev: list = []
    for k, (a, b) in enumerate(_runs(s["doors_open"] > 0.5), start=1):
        dwell = t[b] - t[a] + result.dt_s
        idle_l = float(np.sum(s["fuel_g_per_s"][a:b + 1]) * result.dt_s / eng.fuel_density_kg_per_m3)
        ev.append({"t": float(t[a]), "d": float(d[a]), "kind": "stop", "title": f"Durak {k}",
                   "text": f"{tr_num(dwell, 0)} s bekleme, {tr_num(s['passengers'][a], 0)} yolcu; "
                           f"rölantide {tr_num(idle_l * 1000, 0)} ml yakıt"})
    g = np.tan(s["grade_rad"]) * 100.0
    for sign, kind, word in ((1, "climb", "Yokuş"), (-1, "descent", "İniş")):
        for a, b in _runs(sign * g > 5.0):
            if d[b] - d[a] < 60:
                continue
            peak = float(np.max(sign * g[a:b + 1]))
            litres = float(np.sum(s["fuel_g_per_s"][a:b + 1]) * result.dt_s / eng.fuel_density_kg_per_m3)
            gears = s["gear"][a:b + 1]
            gear_min = int(np.min(gears[gears > 0])) if np.any(gears > 0) else 0
            txt = (f"%{tr_num(peak, 1)} eğime kadar, {tr_num(d[b] - d[a], 0)} m; "
                   + (f"{tr_num(litres, 2)} l yakıt, en düşük vites {gear_min}" if sign > 0
                      else f"motor freni ve retarder; {tr_num(litres * 1000, 0)} ml yakıt"))
            ev.append({"t": float(t[a]), "d": float(d[a]), "kind": kind, "title": f"{word} başladı", "text": txt})
    for a, b in _runs(s["unmet"] > 0):
        ev.append({"t": float(t[a]), "d": float(d[a]), "kind": "soc", "title": "Motor gücü yetmedi",
                   "text": f"{tr_num(t[b] - t[a] + result.dt_s, 0)} s boyunca tam yük; hız profili takip edilemedi"})
    meta = result.cycle.meta or {}
    for e in meta.get("signal_stops", []):
        wait = e["t_go"] - e["t0"]
        ev.append({"t": float(e["t0"]), "d": float(e["d_line"]), "kind": "signal", "title": "Kırmızı ışık",
                   "text": f"{tr_num(wait, 0)} s bekleme" + (f", önde {e['queue']} araç" if e.get("queue") else ", ilk sırada")})
    for j in meta.get("jams", []):
        ev.append({"t": float(j["t0"]), "d": float(j["d"]), "kind": "jam", "title": "Trafik sıkışıklığı",
                   "text": f"{tr_num(j['d_end'] - j['d'], 0)} m dur-kalk: {len(j['halts'])} kez durdu, "
                           f"{tr_num(j['t1'] - j['t0'], 0)} s sürdü"})
    ev.sort(key=lambda e: e["t"])
    return ev[:400]
