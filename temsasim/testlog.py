"""Test-team CAN logs and the diesel model side by side.

The R&D test centre records a fuel test as a 1 Hz CAN export (Dewesoft,
one sheet, a header row and a unit row): time, pedals, engine speed, gear,
fuel rate, cumulative fuel, trip distance and wheel speed. This module reads
those files, replays the measured speed trace through the diesel solver with
the test mass and weather, and scores the agreement the way the test team
reads it: total litres and l/100 km first, then where the difference sits
(idle, cruise, acceleration, deceleration), then engine speed and gear.

Nothing here tunes the model. It only measures the gap and names the part of
the model the gap points at, so the engineer decides what to change.
"""

from __future__ import annotations

import io
import pathlib
from dataclasses import dataclass, field

import numpy as np

from .cycle import DriveCycle, Environment
from .messages import Note
from .units import RPM

# CAN signal names (SAE J1939 SPNs as Dewesoft exports them) and the name we use.
# Matching is by prefix and case-insensitive, so "HighResolutionEngFuelRate" and
# "EngFuelRate" both land on fuel_rate_lph.
SIGNALS = {
    "time_s": ["time", "zaman"],
    "speed_kmh": ["wheelbasedvehiclespeed", "vehiclespeed", "speed", "hiz", "hız"],
    "engine_rpm": ["engspeed", "enginespeed", "enginerpm", "rpm", "devir"],
    "gear": ["transcurrentgear", "currentgear", "gear", "vites"],
    "fuel_rate_lph": ["highresolutionengfuelrate", "engfuelrate", "fuelrate", "yakitdebisi", "yakıt"],
    "fuel_cum_l": ["highresolutionengtripfuel", "engtripfuel", "tripfuel", "fuelcum", "toplamyakit"],
    "distance_m": ["highresolutiontripdistance", "tripdistance", "distance", "mesafe"],
    "accel_pedal_pct": ["accelpedalpos1", "accelpedalpos", "accelpedal", "gaz"],
    "brake_pedal_pct": ["brakepedalpos", "brakepedal", "fren"],
    "grade_pct": ["grade", "egim", "eğim", "slope"],
}
UNIT_HINTS = {"fuel_rate_lph": {"l/h": 1.0, "g/s": 3600.0 / 836.0, "kg/h": 1000.0 / 836.0},
              "speed_kmh": {"km/h": 1.0, "m/s": 3.6}, "distance_m": {"m": 1.0, "km": 1000.0}}


@dataclass
class DieselLog:
    """One recorded test run."""

    time_s: np.ndarray
    speed_mps: np.ndarray
    fuel_rate_lps: np.ndarray | None = None          # l/s
    fuel_cum_l: np.ndarray | None = None
    engine_rpm: np.ndarray | None = None
    gear: np.ndarray | None = None
    distance_m: np.ndarray | None = None
    accel_pedal_pct: np.ndarray | None = None
    brake_pedal_pct: np.ndarray | None = None
    grade_rad: np.ndarray | None = None
    name: str = "test"
    source: str = "unspecified"
    columns: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return float(self.time_s[-1] - self.time_s[0])

    @property
    def distance_km(self) -> float:
        """Distance from the integrated speed trace (the CAN odometer resets
        mid-run in some exports, so it is only used as a cross-check)."""
        return float(np.sum(self.speed_mps[:-1] * np.diff(self.time_s))) / 1000.0

    @property
    def fuel_l(self) -> float:
        """Litres burned: the trip counter if it is usable, else the integrated rate."""
        if self.fuel_cum_l is not None:
            ok = np.isfinite(self.fuel_cum_l) & (self.fuel_cum_l > 0)
            if ok.sum() > 10:
                return float(self.fuel_cum_l[ok][-1] - self.fuel_cum_l[ok][0])
        if self.fuel_rate_lps is not None:
            return float(np.sum(np.nan_to_num(self.fuel_rate_lps[:-1]) * np.diff(self.time_s)))
        return float("nan")

    def to_cycle(self, *, scale_speed: float = 1.0, passengers: float = 0.0,
                 grade_rad: np.ndarray | None = None) -> DriveCycle:
        v = np.clip(self.speed_mps * scale_speed, 0.0, None)
        n = v.size
        g = grade_rad if grade_rad is not None else self.grade_rad
        return DriveCycle(self.time_s - self.time_s[0], v, grade_rad=g,
                          passengers=np.full(n, float(passengers)),
                          doors_open=np.zeros(n, dtype=bool), name=self.name, source=self.source,
                          meta={"measured": True})

    def phases(self) -> dict:
        """Masks for the four phases the test team talks about."""
        v = self.speed_mps
        a = np.gradient(v, self.time_s)
        return {"idle": v < 0.5,
                "cruise": (v > 25.0) & (np.abs(a) < 0.15),
                "accel": (v >= 0.5) & (a >= 0.15),
                "decel": (v >= 0.5) & (a <= -0.15),
                "other": (v >= 0.5) & (v <= 25.0) & (np.abs(a) < 0.15)}


# ---------------------------------------------------------------- reading
def _norm(s) -> str:
    return "".join(str(s).lower().replace("ı", "i").replace("_", "").split())


def read_can_log(data: bytes, filename: str) -> DieselLog:
    """Read a Dewesoft-style CAN export (.xlsx, or CSV with the same layout)."""
    from .importers import read_table
    table = read_table(data, filename)
    rows = table.cells
    header_i = next((i for i, r in enumerate(rows[:10])
                     if sum(isinstance(c, str) and c.strip() != "" for c in r) >= 3), 0)
    header = [_norm(c) if c is not None else "" for c in rows[header_i]]
    unit_row = rows[header_i + 1] if header_i + 1 < len(rows) else []
    units = [str(c).strip().lower() if c is not None else "" for c in unit_row]
    numeric_unit_row = sum(1 for c in unit_row if isinstance(c, (int, float))) > len(unit_row) / 2
    data_start = header_i + (1 if numeric_unit_row else 2)

    cols: dict = {}
    for key, names in SIGNALS.items():
        for pref in names:
            hits = [i for i, h in enumerate(header) if h.startswith(_norm(pref))]
            if hits:
                # a duplicated signal: take the copy that actually has data (the last one)
                cols[key] = hits[-1]
                break

    def col(key):
        i = cols.get(key)
        if i is None:
            return None
        out = np.full(len(rows) - data_start, np.nan)
        for k, r in enumerate(rows[data_start:]):
            if i < len(r) and r[i] not in (None, ""):
                try:
                    out[k] = float(str(r[i]).replace(",", "."))
                except ValueError:
                    pass
        return out

    notes: list = []
    t = col("time_s")
    v = col("speed_kmh")
    if t is None or v is None:
        raise ValueError("Logda zaman ve hız sütunu bulunamadı (Time, WheelBasedVehicleSpeed).")
    ok = np.isfinite(t) & np.isfinite(v)
    order = np.argsort(t[ok])

    def pick(key, factor=1.0):
        a = col(key)
        if a is None:
            return None
        a = a[ok][order]
        unit = units[cols[key]] if cols[key] < len(units) else ""
        f = UNIT_HINTS.get(key, {}).get(unit, factor)
        return a * f

    t = t[ok][order]
    v_kmh = pick("speed_kmh")
    fr = pick("fuel_rate_lph")
    if fr is not None:
        fr = fr / 3600.0
    log = DieselLog(time_s=t, speed_mps=v_kmh / 3.6, fuel_rate_lps=fr, fuel_cum_l=pick("fuel_cum_l"),
                    engine_rpm=pick("engine_rpm"), gear=pick("gear"), distance_m=pick("distance_m"),
                    accel_pedal_pct=pick("accel_pedal_pct"), brake_pedal_pct=pick("brake_pedal_pct"),
                    name=pathlib.Path(filename).stem, source=f"CAN log: {pathlib.Path(filename).name}",
                    columns={k: str(rows[header_i][i]) for k, i in cols.items()})
    g = pick("grade_pct")
    if g is not None:
        log.grade_rad = np.arctan(np.nan_to_num(g) / 100.0)
    else:
        notes.append("Logda eğim yok; güzergâh rakım profili verilmezse yol düz kabul edilir.")
    if log.fuel_rate_lps is None and log.fuel_cum_l is None:
        notes.append("Logda yakıt sinyali yok; yalnızca hız, devir ve vites karşılaştırılır.")
    log.notes = notes
    return log


def read_can_log_path(path) -> DieselLog:
    p = pathlib.Path(path)
    return read_can_log(p.read_bytes(), p.name)


# ---------------------------------------------------------------- comparison
@dataclass
class DieselCorrelation:
    log: DieselLog
    result: object                      # DieselResult
    metrics: dict
    notes: list = field(default_factory=list)

    def summary(self) -> str:
        m = self.metrics
        lines = [f"{self.result.vehicle.name} vs {self.log.name}",
                 f"  distance          {m['distance_km']:7.2f} km   duration {m['duration_min']:.1f} min",
                 f"  fuel measured     {m['fuel_meas_l']:7.3f} l   ({m['l100_meas']:.2f} l/100 km, CAN)",
                 f"  fuel simulated    {m['fuel_sim_l']:7.3f} l   ({m['l100_sim']:.2f} l/100 km)",
                 f"  error             {100 * m['fuel_error']:+7.2f} %"]
        if m.get("fuel_meas_device_l"):
            lines.append(f"  fuel device       {m['fuel_meas_device_l']:7.3f} l   (error vs device {100 * m['fuel_error_device']:+.2f} %)")
        lines.append("  by phase (sim - meas, litres):")
        for k, d in m["by_phase"].items():
            lines.append(f"    {k:8s} {d['sim_l'] - d['meas_l']:+7.3f}  (sim {d['sim_l']:.3f} / meas {d['meas_l']:.3f}, {d['share_time']:.0%} of time)")
        if m.get("rpm_cruise_meas"):
            lines.append(f"  cruise rpm        meas {m['rpm_cruise_meas']:.0f} / sim {m['rpm_cruise_sim']:.0f}   gear agreement {m['gear_agreement']:.0%}")
        lines.append(f"  idle rate         meas {m['idle_lph_meas']:.2f} / sim {m['idle_lph_sim']:.2f} l/h")
        for n in self.notes:
            lines.append(f"  ! {n.tr if hasattr(n, 'tr') else n}")
        return "\n".join(lines)


def replay_cycle(vehicle, log: DieselLog, *, mass_kg: float | None = None, grade_rad: np.ndarray | None = None,
                 distance_km: float | None = None) -> tuple:
    """The vehicle at its test mass and the logged speed trace as a drive
    cycle: what both the correlation and the gearbox calibration replay.
    Returns ``(vehicle, cycle, speed_scale, notes)``."""
    from dataclasses import replace
    if not vehicle.is_diesel:
        raise ValueError("Bu karşılaştırma dizel araç içindir.")
    veh = vehicle
    if mass_kg:
        veh = replace(veh, chassis=replace(veh.chassis, kerb_mass_kg=float(mass_kg)),
                      variant=f"{veh.variant} · test kütlesi {mass_kg:.0f} kg".strip(" ·"))
    scale = 1.0
    notes: list = [n for n in log.notes if not (grade_rad is not None and "eğim yok" in str(n))]
    if distance_km and log.distance_km > 0:
        scale = distance_km / log.distance_km
        notes.append(f"CAN hızı, sabit parkur uzunluğu {distance_km:.1f} km ile ölçeklendi (çarpan {scale:.4f}).")
    return veh, log.to_cycle(scale_speed=scale, grade_rad=grade_rad), scale, notes


def correlate_diesel(vehicle, log: DieselLog, *, environment: Environment | None = None,
                     mass_kg: float | None = None, grade_rad: np.ndarray | None = None,
                     distance_km: float | None = None, fuel_device_l: float | None = None,
                     dt_s: float = 1.0) -> DieselCorrelation:
    """Replay the log through the diesel model and score it.

    ``mass_kg`` is the weighed test mass (replaces kerb mass, no passengers).
    ``distance_km`` is the test route's fixed length; when given, the CAN
    speed trace is scaled so the simulated distance matches it (CAN wheel
    speed over-reads by a fraction of a percent). ``fuel_device_l`` is the
    external flow-meter reading, when the test sheet has one.
    """
    from .simulator import run
    veh, cyc, scale, notes = replay_cycle(vehicle, log, mass_kg=mass_kg, grade_rad=grade_rad, distance_km=distance_km)
    env = environment or Environment()
    res = run(veh, cyc, env, dt_s=dt_s)
    s = res.series
    t = s["time_s"]
    eng = veh.engine

    sim_rate = (s["fuel_g_per_s"] + s["heater_fuel_g_per_s"]) / eng.fuel_density_kg_per_m3     # l/s
    meas_rate = (np.interp(t, log.time_s - log.time_s[0], np.nan_to_num(log.fuel_rate_lps))
                 if log.fuel_rate_lps is not None else None)
    fuel_meas = log.fuel_l
    fuel_sim = float(np.sum(sim_rate) * dt_s)
    dist_km = res.totals["distance_km"]
    m: dict = {
        "distance_km": dist_km, "duration_min": res.totals["duration_s"] / 60.0,
        "fuel_meas_l": fuel_meas, "fuel_sim_l": fuel_sim,
        "l100_meas": fuel_meas / max(dist_km, 1e-9) * 100.0, "l100_sim": fuel_sim / max(dist_km, 1e-9) * 100.0,
        "fuel_error": (fuel_sim - fuel_meas) / max(fuel_meas, 1e-9),
        "speed_scale": scale,
    }
    if fuel_device_l:
        m["fuel_meas_device_l"] = fuel_device_l
        m["fuel_error_device"] = (fuel_sim - fuel_device_l) / fuel_device_l

    # phases on the (scaled) simulated trace
    v = s["speed_mps"]
    a = s["accel_mps2"]
    phases = {"idle": v < 0.5, "cruise": (v > 25.0) & (np.abs(a) < 0.15),
              "accel": (v >= 0.5) & (a >= 0.15), "decel": (v >= 0.5) & (a <= -0.15),
              "other": (v >= 0.5) & (v <= 25.0) & (np.abs(a) < 0.15)}
    by = {}
    for k, mask in phases.items():
        by[k] = {"sim_l": float(np.sum(sim_rate[mask]) * dt_s),
                 "meas_l": float(np.sum(meas_rate[mask]) * dt_s) if meas_rate is not None else float("nan"),
                 "share_time": float(np.mean(mask)),
                 "mean_lph_sim": float(np.mean(sim_rate[mask]) * 3600) if mask.any() else 0.0,
                 "mean_lph_meas": float(np.mean(meas_rate[mask]) * 3600) if (meas_rate is not None and mask.any()) else float("nan")}
    m["by_phase"] = by
    if meas_rate is not None:
        m["rate_rmse_lph"] = float(np.sqrt(np.mean((sim_rate - meas_rate) ** 2)) * 3600)
    idle = phases["idle"]
    m["idle_lph_sim"] = float(np.mean(sim_rate[idle]) * 3600) if idle.any() else 0.0
    m["idle_lph_meas"] = float(np.mean(meas_rate[idle]) * 3600) if (meas_rate is not None and idle.any()) else float("nan")

    if log.engine_rpm is not None:
        meas_rpm = np.interp(t, log.time_s - log.time_s[0], np.nan_to_num(log.engine_rpm))
        sim_rpm = s["engine_speed_rads"] / RPM
        cr = phases["cruise"]
        m["rpm_cruise_meas"] = float(np.mean(meas_rpm[cr])) if cr.any() else 0.0
        m["rpm_cruise_sim"] = float(np.mean(sim_rpm[cr])) if cr.any() else 0.0
        m["rpm_rmse_moving"] = float(np.sqrt(np.mean((sim_rpm[v > 0.5] - meas_rpm[v > 0.5]) ** 2))) if (v > 0.5).any() else 0.0
    if log.gear is not None:
        meas_gear = np.interp(t, log.time_s - log.time_s[0], np.nan_to_num(log.gear))
        moving = v > 0.5
        m["gear_agreement"] = float(np.mean(np.round(meas_gear[moving]) == s["gear"][moving])) if moving.any() else 0.0
        m["gear_mean_diff"] = float(np.mean(s["gear"][moving] - meas_gear[moving])) if moving.any() else 0.0

    # --- what the gap points at ------------------------------------------------
    err = m["fuel_error"]
    if meas_rate is not None:
        cruise_d = by["cruise"]["mean_lph_sim"] - by["cruise"]["mean_lph_meas"]
        idle_d = m["idle_lph_sim"] - m["idle_lph_meas"]
        if abs(idle_d) > 0.15:
            notes.append(Note(f"idle rate off by {idle_d:+.2f} l/h: engine idle speed / auxiliary load at standstill",
                              f"Rölanti yakıtı {idle_d:+.2f} l/h sapıyor: motorun rölanti devri (logda {m['idle_lph_meas']:.2f} l/h) "
                              f"ve duruştaki yardımcı yük kontrol edilmeli."))
        if by["cruise"]["share_time"] > 0.1 and abs(cruise_d) > 0.8:
            notes.append(Note(f"cruise rate off by {cruise_d:+.1f} l/h: road load (grade, CdA, rolling), driveline losses or map",
                              f"Seyir yakıtı {cruise_d:+.1f} l/h sapıyor. Sabit hızda farkın kaynağı yol yükü (eğim, CdxA, "
                              f"yuvarlanma), aktarma kayıpları ya da yakıt haritasıdır; logda eğim yoksa önce eğim."))
        acc_d = by["accel"]["sim_l"] - by["accel"]["meas_l"]
        if by["accel"]["meas_l"] > 0 and abs(acc_d) / by["accel"]["meas_l"] > 0.15:
            notes.append(Note(f"acceleration fuel off by {acc_d:+.2f} l: shift strategy / full-load use",
                              f"Hızlanma yakıtı {acc_d:+.2f} l sapıyor: vites stratejisi ve tam yük kullanımı "
                              f"(ölçülen devir izine karşı vites seçimi) incelenmeli."))
    if log.engine_rpm is not None and m.get("rpm_cruise_meas", 0) > 0 and abs(m["rpm_cruise_sim"] - m["rpm_cruise_meas"]) > 25:
        d = m["rpm_cruise_sim"] - m["rpm_cruise_meas"]
        notes.append(Note(f"cruise engine speed off by {d:+.0f} rpm: tyre dynamic radius or axle ratio / top gear choice",
                          f"Seyir devri {d:+.0f} rpm sapıyor: dinamik lastik yarıçapı, aks oranı ya da seçilen vites."))
    if grade_rad is None and log.grade_rad is None:
        notes.append(Note("no gradient: direction-dependent fuel differences cannot be reproduced",
                          "Eğim bilgisi yok: gidiş/dönüş arasındaki yakıt farkı modelde görünmez."))
    return DieselCorrelation(log, res, m, notes)


# ---------------------------------------------------------------- campaigns
def grade_along(route, travelled_m: np.ndarray, *, reverse: bool = False) -> np.ndarray:
    """Road gradient under the vehicle at each sample, from a library route.

    The test runs a fixed stretch of road; the measured trace is laid onto the
    route by distance (reversed for the return direction), so the climb comes
    at the right moment even though the log itself carries no gradient."""
    L = float(route.distance_m[-1])
    x = np.clip(travelled_m, 0.0, L)
    if reverse:
        x = L - x
    elev = np.interp(x, route.distance_m, route.elevation_m)
    dx = np.gradient(np.maximum.accumulate(travelled_m + np.arange(travelled_m.size) * 1e-6))
    de = np.gradient(elev)
    with np.errstate(invalid="ignore", divide="ignore"):
        g = np.where(dx > 0.05, de / dx, 0.0)
    return np.arctan(np.nan_to_num(g))


@dataclass
class CampaignRun:
    key: str
    meta: dict
    log: DieselLog
    correlation: DieselCorrelation | None = None

    def row(self) -> dict:
        m = self.correlation.metrics if self.correlation else {}
        b = m.get("by_phase", {})
        return {"key": self.key, "direction": self.meta.get("direction"), "night": bool(self.meta.get("night")),
                "ambient_c": self.meta.get("ambient_c"), "wind_mps": self.meta.get("wind_mps"),
                "fuel_can_l": m.get("fuel_meas_l"), "fuel_device_l": self.meta.get("fuel_device_l"),
                "fuel_sim_l": m.get("fuel_sim_l"), "err_can": m.get("fuel_error"), "err_device": m.get("fuel_error_device"),
                "l100_can": m.get("l100_meas"), "l100_sim": m.get("l100_sim"),
                "l100_device": (self.meta["fuel_device_l"] / m["distance_km"] * 100 if self.meta.get("fuel_device_l") and m.get("distance_km") else None),
                "cruise_lph_meas": b.get("cruise", {}).get("mean_lph_meas"), "cruise_lph_sim": b.get("cruise", {}).get("mean_lph_sim"),
                "idle_lph_meas": m.get("idle_lph_meas"), "idle_lph_sim": m.get("idle_lph_sim"),
                "rpm_cruise_meas": m.get("rpm_cruise_meas"), "rpm_cruise_sim": m.get("rpm_cruise_sim"),
                "gear_agreement": m.get("gear_agreement"), "rate_rmse_lph": m.get("rate_rmse_lph"),
                "accel_l_meas": b.get("accel", {}).get("meas_l"), "accel_l_sim": b.get("accel", {}).get("sim_l"),
                "by_phase": b,
                "notes": [n.tr if hasattr(n, "tr") else str(n) for n in (self.correlation.notes if self.correlation else [])]}


def list_campaigns(library_root) -> list:
    d = pathlib.Path(library_root) / "testlog"
    out = []
    if not d.exists():
        return out
    import yaml
    for p in sorted(d.iterdir()):
        mp = p / "meta.yaml"
        if p.is_dir() and mp.exists():
            meta = yaml.safe_load(mp.read_text(encoding="utf-8")) or {}
            out.append({"name": p.name, "campaign": meta.get("campaign", p.name), "report": meta.get("report"),
                        "vehicle": meta.get("vehicle"), "route": meta.get("route"), "runs": len(meta.get("runs", {})),
                        "dates": str(meta.get("dates", "")), "test_mass_kg": meta.get("test_mass_kg"),
                        "summary_l100": meta.get("summary_l100")})
    return out


def load_campaign(library_root, name: str) -> tuple:
    """(meta, runs) for ``library/testlog/<name>``. Logs are read, not yet run."""
    import yaml
    d = pathlib.Path(library_root) / "testlog" / name
    meta = yaml.safe_load((d / "meta.yaml").read_text(encoding="utf-8")) or {}
    runs = []
    for key, r in (meta.get("runs") or {}).items():
        f = d / f"{key}.csv"
        if not f.exists():
            continue
        runs.append(CampaignRun(key, r, read_can_log_path(f)))
    return meta, runs


def campaign_cases(lib, name: str, *, vehicle=None, runs: list | None = None) -> tuple:
    """What each run of a campaign is replayed with: ``(meta, vehicle,
    route_found, cases)``, a case being ``{"run", "env", "grade", "mass_kg",
    "distance_km"}``.

    The vehicle (a name, or a loaded ``Vehicle`` to try unsaved changes), the
    route (for gradient), the test mass, the HVAC state and each run's weather
    come from ``meta.yaml``. Wind is applied with the campaign's stated
    convention (``wind_convention``: ``headwind_on_gidis`` or
    ``tailwind_on_gidis``)."""
    from .cycle import Route
    meta, all_runs = load_campaign(lib.root, name)
    veh = vehicle if hasattr(vehicle, "chassis") else lib.load_vehicle(vehicle or meta["vehicle"])
    route = None
    if meta.get("route"):
        rp = lib.root / "route" / f"{meta['route']}.csv"
        if rp.exists():
            route = Route.from_csv(rp)
    conv = meta.get("wind_convention", "headwind_on_gidis")
    hvac_on = str(meta.get("hvac", "on")).lower() not in ("off", "kapali", "kapalı", "false", "0")
    L_km = float(meta.get("route_length_km") or 0) or None
    mass = meta.get("test_mass_kg")
    selected = [r for r in all_runs if runs is None or r.key in runs]
    cases = []
    for cr in selected:
        r = cr.meta
        gidis = str(r.get("direction", "gidis")).lower().startswith("gid")
        wind = float(r.get("wind_mps") or 0.0)
        sign = (1 if conv == "headwind_on_gidis" else -1) * (1 if gidis else -1)
        env = Environment(ambient_c=float(r.get("ambient_c", 20.0)), night=bool(r.get("night")),
                          headwind_mps=sign * wind, hvac_enabled=hvac_on,
                          air_pressure_pa=float(r["pressure_hpa"]) * 100.0 if r.get("pressure_hpa") else 101325.0,
                          label=cr.key)
        grade = None
        if route is not None:
            scale = (L_km / cr.log.distance_km) if (L_km and cr.log.distance_km > 0) else 1.0
            travelled = np.cumsum(np.r_[0.0, cr.log.speed_mps[:-1] * scale * np.diff(cr.log.time_s)])
            grade = grade_along(route, travelled, reverse=not gidis)
        cases.append({"run": cr, "env": env, "grade": grade, "mass_kg": mass, "distance_km": L_km})
    return meta, veh, route is not None, cases


def run_campaign(lib, name: str, *, vehicle=None, runs: list | None = None) -> dict:
    """Replay every run of a test campaign and return the comparison table."""
    meta, veh, route_found, cases = campaign_cases(lib, name, vehicle=vehicle, runs=runs)
    selected = []
    for c in cases:
        cr = c["run"]
        cr.correlation = correlate_diesel(veh, cr.log, environment=c["env"], mass_kg=c["mass_kg"], grade_rad=c["grade"],
                                          distance_km=c["distance_km"], fuel_device_l=cr.meta.get("fuel_device_l"))
        selected.append(cr)
    rows = [cr.row() for cr in selected]
    can = sum(x["fuel_can_l"] for x in rows if x["fuel_can_l"]); sim = sum(x["fuel_sim_l"] for x in rows if x["fuel_sim_l"])
    dev = sum(x["fuel_device_l"] or 0 for x in rows)
    summary = {"runs": len(rows), "fuel_can_l": can, "fuel_sim_l": sim, "fuel_device_l": dev or None,
               "err_can": (sim / can - 1) if can else None, "err_device": (sim / dev - 1) if dev else None,
               "mean_abs_err_device": float(np.mean([abs(x["err_device"]) for x in rows if x.get("err_device") is not None])) if dev else None,
               "mean_gear_agreement": float(np.mean([x["gear_agreement"] for x in rows if x.get("gear_agreement") is not None])) if rows else None}
    return {"name": name, "meta": {k: v for k, v in meta.items() if k != "runs"}, "vehicle": veh.name,
            "route_found": route_found, "rows": rows, "summary": summary, "runs": selected}
