"""Scenarios: a named, saved combination of route, vehicle(s) and conditions.

A simulation run answers one question once. A scenario keeps the question so
it can be asked again -- of another vehicle, after a component changed, by a
colleague next month -- and so that two vehicles are compared under exactly
the same conditions. It is a small YAML file in ``library/scenario/``:

    name: Mersin–Adana gündüz, 18 t
    duty: {route: mersin_adana_otoyol_gidis, direction: gidis, laps: 1}
    vehicles: [maraton_13_dizel]
    conditions: {ambient_c: 33.5, solar: 700, night: false, hvac_off: true, headwind_mps: 1.1}
    load: {mode: mass, mass_kg: 18000}
    traffic: yok

Running it gives one row per vehicle with the figures the two solvers share
(energy or fuel per km, CO₂, time, mean speed) and the ones only one of them
has (range on the pack, litres), plus the full report of each run.
"""

from __future__ import annotations

import pathlib
import time
from dataclasses import replace
from dataclasses import dataclass, field

import yaml

from .cycle import Environment, Route
from .vecto_xml import safe_name

KINDS_TR = {"sehir_ici": "Şehir içi", "otoyol": "Otoyol / şehirlerarası", "serbest": "Serbest", "test": "Test tekrarı"}
LOAD_MODES = {"profile": "Duraklarda değişken (zirve)", "constant": "Sabit yolcu", "stops": "Duraklardan (biniş/iniş)",
              "mass": "Test kütlesi (kg)"}


@dataclass
class Scenario:
    name: str
    stem: str = ""
    kind: str = "serbest"
    description: str = ""
    duty: dict = field(default_factory=lambda: {"route": "", "direction": "gidis", "laps": 1})
    vehicles: list = field(default_factory=list)
    conditions: dict = field(default_factory=lambda: {"ambient_c": 20.0, "solar": 0.0, "night": False, "hvac_off": False,
                                                      "headwind_mps": 0.0, "pressure_hpa": 1013.25})
    load: dict = field(default_factory=lambda: {"mode": "profile", "passengers": 40, "mass_kg": None})
    traffic: str = "yok"
    overrides: dict = field(default_factory=dict)     # rolling_c0, ...
    daily_km: float = 200.0
    notes: str = ""

    def __post_init__(self) -> None:
        self.stem = safe_name(self.stem or self.name)
        self.kind = self.kind if self.kind in KINDS_TR else "serbest"
        self.vehicles = [str(v) for v in (self.vehicles or [])]
        self.duty = dict(self.duty or {})
        self.conditions = {**Scenario.__dataclass_fields__["conditions"].default_factory(), **(self.conditions or {})}
        self.load = {**Scenario.__dataclass_fields__["load"].default_factory(), **(self.load or {})}

    def to_dict(self) -> dict:
        return {"kind": "scenario", "name": self.name, "scenario_kind": self.kind, "description": self.description,
                "duty": self.duty, "vehicles": self.vehicles, "conditions": self.conditions, "load": self.load,
                "traffic": self.traffic, "overrides": self.overrides, "daily_km": self.daily_km, "notes": self.notes}

    @classmethod
    def from_dict(cls, d: dict, stem: str = "") -> "Scenario":
        return cls(name=d.get("name", stem or "senaryo"), stem=stem or d.get("stem", ""), kind=d.get("scenario_kind") or d.get("kind_", "serbest"),
                   description=d.get("description", ""), duty=d.get("duty") or {}, vehicles=d.get("vehicles") or [],
                   conditions=d.get("conditions") or {}, load=d.get("load") or {}, traffic=d.get("traffic", "yok"),
                   overrides=d.get("overrides") or {}, daily_km=float(d.get("daily_km") or 200), notes=d.get("notes", ""))

    def environment(self) -> Environment:
        c = self.conditions
        return Environment(ambient_c=float(c.get("ambient_c", 20)), solar_irradiance_w_per_m2=float(c.get("solar") or 0),
                           night=bool(c.get("night")), hvac_enabled=not bool(c.get("hvac_off")),
                           headwind_mps=float(c.get("headwind_mps") or 0),
                           air_pressure_pa=float(c.get("pressure_hpa") or 1013.25) * 100.0, label=self.name)

    def conditions_tr(self) -> str:
        c = self.conditions
        parts = [f"{c.get('ambient_c', 20):.0f} °C", f"güneş {c.get('solar') or 0:.0f} W/m²", "gece" if c.get("night") else "gündüz"]
        if c.get("hvac_off"):
            parts.append("klima kapalı")
        if c.get("headwind_mps"):
            parts.append(f"rüzgâr {c.get('headwind_mps'):+.1f} m/s")
        lo = self.load
        parts.append({"profile": f"{lo.get('passengers', 0)} yolcu (değişken)", "constant": f"{lo.get('passengers', 0)} yolcu (sabit)",
                      "stops": "yolcu duraklardan", "mass": f"{lo.get('mass_kg') or 0:.0f} kg test kütlesi"}.get(lo.get("mode"), ""))
        if self.traffic and self.traffic != "yok":
            parts.append(f"trafik {self.traffic}")
        return " · ".join(p for p in parts if p)

    def summary_tr(self) -> str:
        c, lo, d = self.conditions, self.load, self.duty
        duty = (f"{d.get('route')}{' (dönüş)' if d.get('direction') == 'donus' else ''}" if d.get("route") else f"çevrim {d.get('cycle', 'cbdbus')}")
        if int(d.get("laps") or d.get("repeat") or 1) > 1:
            duty += f" ×{int(d.get('laps') or d.get('repeat'))}"
        load = {"profile": f"{lo.get('passengers', 0)} yolcu (değişken)", "constant": f"{lo.get('passengers', 0)} yolcu (sabit)",
                "stops": "duraklardan", "mass": f"{lo.get('mass_kg') or 0:.0f} kg test kütlesi"}.get(lo.get("mode"), "")
        return (f"{duty} · {c.get('ambient_c', 20):.0f} °C · {'gece' if c.get('night') else 'gündüz'}"
                + (f" · güneş {c.get('solar'):.0f} W/m²" if c.get("solar") else "") + (" · klima kapalı" if c.get("hvac_off") else "")
                + (f" · rüzgâr {c.get('headwind_mps'):+.1f} m/s" if c.get("headwind_mps") else "")
                + f" · {load} · trafik {self.traffic}")


# ---------------------------------------------------------------- storage
def _dir(library_root) -> pathlib.Path:
    return pathlib.Path(library_root) / "scenario"


def list_scenarios(library_root) -> list:
    out = []
    d = _dir(library_root)
    if not d.exists():
        return out
    for p in sorted(d.glob("*.yaml")):
        try:
            sc = load_scenario(library_root, p.stem)
            out.append({"stem": p.stem, "name": sc.name, "kind": sc.kind, "kind_tr": KINDS_TR.get(sc.kind, ""),
                        "description": sc.description, "vehicles": sc.vehicles, "duty": sc.duty, "summary": sc.summary_tr(),
                        "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)),
                        "conditions": sc.conditions, "load": sc.load, "traffic": sc.traffic})
        except Exception as exc:
            out.append({"stem": p.stem, "name": p.stem, "error": str(exc)})
    return out


def load_scenario(library_root, stem: str) -> Scenario:
    p = _dir(library_root) / f"{safe_name(stem)}.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return Scenario.from_dict(data, stem=p.stem)


def save_scenario(library_root, sc: Scenario, *, overwrite: bool = False) -> pathlib.Path:
    d = _dir(library_root); d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sc.stem}.yaml"
    if p.exists() and not overwrite:
        raise FileExistsError(f"scenario/{p.name} zaten var. Üzerine yazmak için onay verin.")
    p.write_text("# Senaryo: güzergâh + araç(lar) + koşullar. Arayüzde Senaryo sayfasından düzenlenir.\n"
                 + yaml.safe_dump(sc.to_dict(), sort_keys=False, allow_unicode=True), encoding="utf-8")
    return p


# ---------------------------------------------------------------- running
def build_cycle(lib, sc: Scenario, vehicle):
    """The drive cycle a scenario puts in front of a vehicle."""
    d, lo = sc.duty, sc.load
    laps = max(1, int(d.get("laps") or d.get("repeat") or 1))
    mode = lo.get("mode", "profile")
    n = float(lo.get("passengers") or 0)
    occ = None
    if mode == "constant":
        occ = n
    elif mode == "profile" and n > 0:
        occ = (lambda f, peak=n: peak * 4.0 * f * (1.0 - f))
    elif mode == "stops":
        occ = "stops"
    if d.get("route"):
        route = Route.from_csv(lib.root / "route" / f"{safe_name(d['route'])}.csv")
        if d.get("direction") == "donus":
            route = route.reversed()
        if occ == "stops" and route.occupancy_from_stops() is None:
            occ = (lambda f, peak=n: peak * 4.0 * f * (1.0 - f)) if n > 0 else None
        return route.to_cycle(vehicle=vehicle, occupancy=occ, traffic=sc.traffic or "yok", laps=laps)
    cyc = lib.load_cycle(d.get("cycle") or "cbdbus")
    if occ is not None and occ != "stops":
        cyc = cyc.with_occupancy(occ)
    return cyc.repeat(laps) if laps > 1 else cyc


def prepare_vehicle(lib, sc: Scenario, stem: str):
    """The vehicle with the scenario's overrides (test mass, rolling coefficient)."""
    from .app.server import _tuned
    veh = lib.load_vehicle(stem)
    tune = dict(sc.overrides or {})
    if sc.load.get("mode") == "mass" and sc.load.get("mass_kg"):
        tune["kerb_mass_kg"] = float(sc.load["mass_kg"])
    return _tuned(veh, tune) if tune else veh


def run_scenario(lib, sc: Scenario, *, vehicles: list | None = None, render=None) -> dict:
    """Run every vehicle of the scenario on the same duty; return rows and results.

    ``render`` is an optional callable ``(result) -> report_id`` the server
    passes to keep full reports; the CLI passes none."""
    from .simulator import run
    env = sc.environment()
    rows, results = [], {}
    for stem in (vehicles or sc.vehicles):
        veh = prepare_vehicle(lib, sc, stem)
        cyc = build_cycle(lib, sc, veh)
        res = run(veh, cyc, env)
        t = res.totals
        row = {"vehicle": stem, "name": veh.name, "variant": veh.variant, "powertrain": veh.powertrain,
               "distance_km": round(t["distance_km"], 2), "duration_min": round(t["duration_s"] / 60, 1),
               "mean_speed_kmh": round(t["distance_km"] / max(t["duration_s"] / 3600, 1e-9), 1),
               "energy_kwh_per_km": round(res.energy_per_km_kwh, 3), "achievable": res.achievable, "shortfall": res.shortfall,
               "hvac_share": round(t.get("hvac_share", 0.0), 3), "stops": t.get("stops"),
               "warnings": [w.tr if hasattr(w, "tr") else str(w) for w in res.warnings]}
        if veh.is_diesel:
            row.update({"fuel_l": round(t["fuel_total_l"], 3), "l_per_100km": round(res.fuel_l_per_100km, 2),
                        "co2_g_per_km": round(res.co2_g_per_km, 0), "idle_fuel_l": round(t["idle_fuel_l"], 3),
                        "range_km": round(veh.fuel_tank_l / max(res.fuel_l_per_100km, 1e-6) * 100, 0)})
        else:
            row.update({"battery_kwh": round(t["battery_energy_kwh"], 2), "range_km": round(res.range_km, 0),
                        "regen_kwh": round(t["regen_recovered_kwh"], 2), "soc_end": round(float(res.series["soc"][-1]), 3),
                        "co2_g_per_km": None})
        if render is not None:
            row["report_id"] = render(res)
        rows.append(row); results[stem] = res
    cyc_text = ""
    if rows:
        cyc_text = f"{cyc.name}: {cyc.distance_km:.2f} km, {cyc.duration_s / 60:.1f} dk, ort. {cyc.mean_speed_kmh:.1f} km/h, {cyc.stop_count} durak"
    return {"scenario": sc.to_dict(), "stem": sc.stem, "environment": sc.conditions_tr(), "cycle": cyc_text, "rows": rows, "results": results}
