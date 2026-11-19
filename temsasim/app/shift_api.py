"""Gearshift logic and gearbox calibration: the endpoints behind the
workbench's "Vites" tab.

Everything works on a vehicle of the library and on set points sent with the
request, so the engineer can move a set point and see the gear trace answer
before anything is written to disk. Only ``save`` touches the library.
"""

from __future__ import annotations

import datetime
import pathlib
import re
import threading
from dataclasses import replace

import numpy as np
import yaml
from flask import jsonify, request

from .. import shift, shiftcal
from ..cycle import DriveCycle, Environment
from ..shift import SET_POINT_INFO, STRATEGIES, STRATEGY_EFFSHIFT, ShiftSetPoints
from ..units import KMH, RPM

GROUP_ORDER = ["Kalkış", "Vites hatları", "Verim vitesi", "Zamanlayıcılar", "Sürüş durumu", "Uyarlama ve eklentiler"]
STRATEGY_TR = {"vecto_effshift": "VECTO EffShift", "window": "Devir penceresi (eski)"}


def _transmission_path(L, vehicle_stem: str) -> pathlib.Path:
    raw = yaml.safe_load((L.root / "vehicle" / f"{vehicle_stem}.yaml").read_text(encoding="utf-8")) or {}
    ref = raw.get("transmission")
    ref = ref.get("ref") if isinstance(ref, dict) else ref
    if not ref:
        raise ValueError("Bu araçta çok vitesli şanzıman yok; vites mantığı dizel (şanzımanlı) araçlar içindir.")
    return L._resolve(str(ref), "transmission")


def write_shift_block(path: pathlib.Path, strategy: str, setpoints: dict, note: str = "") -> None:
    """Replace the shift strategy and its set points in a transmission file,
    leaving every other line (and its comments) as it was."""
    out, skip = [], False
    for ln in path.read_text(encoding="utf-8").splitlines():
        if skip:
            if ln[:1] in (" ", "\t") or not ln.strip():
                continue
            skip = False
        if re.match(r"^shift_strategy\s*:", ln) or ln.startswith("# vites mantığı:"):
            continue
        if re.match(r"^shift_setpoints\s*:", ln):
            skip = True
            continue
        out.append(ln)
    while out and not out[-1].strip():
        out.pop()
    if note:
        out.append(f"# vites mantığı: {note}")
    out.append(f"shift_strategy: {strategy}")
    if setpoints:
        out.append("shift_setpoints:")
        for k, v in setpoints.items():
            if isinstance(v, bool):
                txt = "true" if v else "false"
            elif isinstance(v, int):
                txt = str(v)
            else:
                txt = f"{float(v):.6g}"
            out.append(f"  {k}: {txt}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def register(app, lib, *, cycle_from, env_from) -> None:
    cache: dict = {}
    lock = threading.Lock()

    def load(stem: str, body: dict | None = None):
        """The vehicle with the strategy and set points of the request (or the
        saved ones), on its own copy of the gearbox."""
        L = lib()
        veh = L.load_vehicle(stem)
        if not veh.is_diesel or veh.transmission is None or veh.transmission.n_gears < 2:
            raise ValueError("Bu araçta çok vitesli şanzıman yok; vites mantığı dizel (şanzımanlı) araçlar içindir.")
        tr = veh.transmission
        body = body or {}
        strategy = body.get("strategy") or tr.shift_strategy
        if strategy not in STRATEGIES:
            raise ValueError(f"Bilinmeyen vites stratejisi: {strategy}")
        sp = ShiftSetPoints.from_dict(body["setpoints"]) if body.get("setpoints") is not None else shift.setpoints_of(tr)
        veh = replace(veh, transmission=replace(tr, shift_strategy=strategy, shift_setpoints=sp.changed()))
        return L, veh, sp

    def campaigns_of(L, stem: str) -> list:
        from ..testlog import list_campaigns, load_campaign
        out = []
        for c in list_campaigns(L.root):
            if c.get("vehicle") != stem:
                continue
            meta, runs = load_campaign(L.root, c["name"])
            keys = [r.key for r in runs if r.log.gear is not None]
            if keys:
                out.append({"name": c["name"], "campaign": c["campaign"], "report": c.get("report"), "runs": keys,
                            "test_mass_kg": c.get("test_mass_kg")})
        return out

    def prepared(L, stem: str, campaign: str):
        """Driveline tables of a campaign's runs; they do not depend on the
        set points, so they are built once and kept until the library changes."""
        sig = max((p.stat().st_mtime for p in L.root.rglob("*.yaml")), default=0.0)
        key = (str(L.root), stem, campaign)
        with lock:
            hit = cache.get(key)
            if hit and hit[0] == sig:
                return hit[1]
        _, runs = shiftcal.prepare_campaign(L, campaign, vehicle=L.load_vehicle(stem))
        if not runs:
            raise ValueError("Bu kampanyanın loglarında vites sinyali yok.")
        with lock:
            cache[key] = (sig, runs)
            while len(cache) > 4:
                cache.pop(next(iter(cache)))
        return runs

    def polygons_payload(veh, sp):
        v = np.array([0.0, 20.0, 40.0]) * KMH
        tb = shift.build_table(veh, DriveCycle(np.arange(3.0), v, name="-"), Environment())
        polys = tb.polygons(sp)
        return tb, {"polygons": [{"gear": g + 1, **p.as_rpm()} for g, p in enumerate(polys)],
                    "max_start_gear": shift.max_start_gear(tb, sp) + 1,
                    "auto": {k: round(float(x), 1) for k, x in shiftcal.auto_values(tb, sp).items()}}

    def metrics(ev: dict) -> dict:
        return {k: ev[k] for k in ("objective", "agreement", "transient_agreement", "within_one", "rpm_rmse",
                                   "launch_agreement", "launches", "shifts_sim", "shifts_meas")} | {
            "runs": [{k: r[k] for k in ("key", "samples", "agreement", "transient_agreement", "within_one", "rpm_rmse",
                                         "shifts_sim", "shifts_meas", "launches", "launch_agreement")} for r in ev["runs"]]}

    @app.get("/api/shift/<stem>")
    def shift_info(stem):
        L, veh, sp = load(stem)
        tr, eng = veh.transmission, veh.engine
        tb, pol = polygons_payload(veh, sp)
        n = eng.full_load_curve.x
        # brake specific fuel consumption on a coarse grid, for the shading of the engine map
        gn = np.linspace(max(eng.idle_speed_rads, n[0]), n[-1], 26)
        gt = np.linspace(100.0, tb.char0.max_torque_nm, 16)
        W, T = np.meshgrid(gn, gt)
        with np.errstate(divide="ignore", invalid="ignore"):
            bsfc = np.where(T <= eng.full_load_curve(W), eng.fuel_map(W, T) / (W * T / 1000.0), np.nan)
        return jsonify({
            "vehicle": {"stem": stem, "name": veh.name, "variant": veh.variant},
            "transmission": {"stem": _transmission_path(L, stem).stem, "name": tr.name, "type": tr.transmission_type,
                             "n_gears": tr.n_gears, "ratios": [float(x) for x in tr.gear_ratios],
                             "axle_ratio": float(tr.axle_ratio), "tyre_radius_m": float(veh.chassis.tyre_dynamic_radius_m),
                             "strategy": tr.shift_strategy, "validated": bool(tr.validated)},
            "strategies": [[k, STRATEGY_TR[k]] for k in (STRATEGY_EFFSHIFT, "window")],
            "setpoints": sp.to_dict(), "defaults": ShiftSetPoints().to_dict(),
            "info": SET_POINT_INFO, "groups": GROUP_ORDER,
            "characteristics": {k: round(float(x), 1) for k, x in tb.char0.as_rpm().items()},
            "idle_rpm": float(eng.idle_speed_rads / RPM),
            "engine": {"full_load": [[float(a / RPM), float(b)] for a, b in zip(n, eng.full_load_curve.y)],
                       "drag": [[float(a / RPM), float(-abs(b))] for a, b in zip(eng.drag_curve.x, eng.drag_curve.y)],
                       "bsfc": {"rpm": (gn / RPM).round(0).tolist(), "torque": gt.round(0).tolist(),
                                "g_per_kwh": [[None if not np.isfinite(x) else round(float(x), 1) for x in row] for row in bsfc]}},
            "window": {"downshift_rpm": tr.downshift_speed_rads / RPM, "upshift_rpm": tr.upshift_speed_rads / RPM,
                       "eco_upshift_rpm": tr.eco_upshift_speed_rads / RPM, "lug_rpm": tr.lug_speed_rads / RPM,
                       "launch_gear": tr.launch_gear, "launch_gear_max": tr.launch_gear_max},
            "campaigns": campaigns_of(L, stem), "calibration_params": shiftcal.DEFAULT_PARAMS, **pol})

    @app.post("/api/shift/<stem>/polygons")
    def shift_polygons(stem):
        _, veh, sp = load(stem, request.get_json(force=True))
        return jsonify(polygons_payload(veh, sp)[1])

    @app.post("/api/shift/<stem>/evaluate")
    def shift_evaluate(stem):
        b = request.get_json(force=True)
        L, veh, sp = load(stem, b)
        runs = prepared(L, stem, b["campaign"])
        ev = shiftcal.evaluate(runs, sp, detail=True)
        key = b.get("run") or runs[0].key
        i = next((k for k, r in enumerate(runs) if r.key == key), 0)
        out = metrics(ev) | {"run": runs[i].key, "trace": shiftcal.run_traces(runs[i], ev["plans"][i]),
                             "reasons": ev["plans"][i].reason_counts(), **polygons_payload(veh, sp)[1]}
        if b.get("fuel"):
            from ..testlog import run_campaign
            rep = run_campaign(L, b["campaign"], vehicle=veh)
            s = rep["summary"]
            out["fuel"] = {"err_can": s["err_can"], "err_device": s["err_device"], "gear_agreement": s["mean_gear_agreement"],
                           "rows": [{k: r[k] for k in ("key", "err_can", "err_device", "l100_sim", "l100_can", "gear_agreement",
                                                         "rpm_cruise_meas", "rpm_cruise_sim")} for r in rep["rows"]]}
        return jsonify(out)

    @app.post("/api/shift/<stem>/calibrate")
    def shift_calibrate(stem):
        b = request.get_json(force=True)
        L, veh, sp = load(stem, b)
        runs = prepared(L, stem, b["campaign"])
        if b.get("runs"):
            runs = [r for r in runs if r.key in b["runs"]] or runs
        budget = min(max(float(b.get("budget_s") or 40.0), 5.0), 180.0)
        cal = shiftcal.calibrate(runs, sp, params=b.get("params") or None, budget_s=budget)
        return jsonify({"setpoints": cal.setpoints.to_dict(), "changed": cal.changed(),
                        "before": metrics(cal.before), "after": metrics(cal.after), "history": cal.history,
                        "sensitivity": cal.sensitivity, "evaluations": cal.evaluations,
                        "seconds": round(cal.seconds, 1), "stopped": cal.stopped})

    @app.post("/api/shift/<stem>/preview")
    def shift_preview(stem):
        """The strategy on a route, cycle or steady-speed test of the library:
        what the calibrated box does where there is no log."""
        from ..diesel import simulate_diesel
        b = request.get_json(force=True)
        L, veh, sp = load(stem, b)
        cyc = cycle_from(L, b, veh)
        res = simulate_diesel(veh, cyc, env_from(b))
        s = res.series
        ev = res.shift_plan.events if res.shift_plan is not None else []
        return jsonify({
            "trace": {"time_s": s["time_s"].tolist(), "speed_kmh": (s["speed_mps"] * 3.6).round(2).tolist(),
                      "gear_sim": s["gear"].astype(int).tolist(), "gear_meas": None,
                      "rpm_sim": (s["engine_speed_rads"] / RPM).round(1).tolist(), "rpm_meas": None,
                      "load": np.round(s["engine_torque_nm"] / np.maximum(s["engine_torque_max_nm"], 1.0), 3).tolist(),
                      "torque_nm": s["engine_torque_nm"].round(1).tolist(),
                      "grade_pct": (np.tan(s["grade_rad"]) * 100.0).round(2).tolist(),
                      "clutch_open": s["clutch_open"].astype(int).tolist(), "events": ev},
            "kpi": {"l_per_100km": res.fuel_l_per_100km, "shifts": res.totals["shifts"], "distance_km": res.totals["distance_km"],
                    "mean_rpm": res.totals["mean_engine_rpm"], "unmet_s": res.unmet_samples * res.dt_s,
                    "gear_share": res.totals["gear_share"]},
            "reasons": res.totals["shift_reasons"], "cycle": cyc.name, **polygons_payload(veh, sp)[1]})

    @app.post("/api/shift/<stem>/save")
    def shift_save(stem):
        b = request.get_json(force=True)
        L, veh, sp = load(stem, b)
        path = _transmission_path(L, stem)
        before = path.read_text(encoding="utf-8")
        note = (b.get("note") or "").replace("\n", " ").strip()
        stamp = datetime.date.today().isoformat()
        try:
            write_shift_block(path, veh.transmission.shift_strategy, sp.changed(),
                              f"{stamp}" + (f", {note}" if note else ""))
            L.load_component(f"transmission/{path.stem}.yaml", "transmission")
        except Exception:
            path.write_text(before, encoding="utf-8")
            raise
        from ..library import Library
        users = [v for v in L.list_kind("vehicle") if _uses(L, v, path.stem)]
        return jsonify({"saved": f"transmission/{path.name}", "strategy": veh.transmission.shift_strategy,
                        "setpoints": sp.changed(), "vehicles": users})

    def _uses(L, vehicle_stem: str, tr_stem: str) -> bool:
        try:
            return _transmission_path(L, vehicle_stem).stem == tr_stem
        except Exception:
            return False
