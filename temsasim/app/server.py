"""Flask server behind the local web interface.

Deliberately thin: every endpoint is a few lines that call into the same
library, importer, simulator and validator modules the CLI uses, so the GUI
cannot drift into computing something different from the command line.

Binds to 127.0.0.1 only. The library is plain files on this PC; nothing is
exposed to the network.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import threading
import time
import traceback
import uuid
import webbrowser
from collections import OrderedDict
from dataclasses import replace

import numpy as np
import yaml
from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from ..cycle import DriveCycle, Environment, Route
from ..importers import (LOG_ROLES_TR, build_log, detect_log_columns, import_curve,
                         import_map, import_route, read_table)
from ..library import Library, LibraryError
from ..messages import localise
from .playback import events as play_events, profile_from_cycle, profile_from_route, timeline
from ..report import render_report
from ..schema import (COMPONENT_KINDS, KIND_LABELS_TR, VEHICLE_SLOTS, component_values,
                      raw_description, safe_stem, save_component, save_vehicle, schema)
from ..simulator import simulate
from ..traffic import LEVELS as TRAFFIC_LEVELS
from ..units import air_density
from ..validate import validate

STATIC = pathlib.Path(__file__).parent / "static"
ROLE_ORDER = ["time", "speed", "power", "voltage", "current", "soc",
              "grade", "passengers", "doors", "ambient"]


class _Cache:
    """Small LRU for uploads and reports, so re-detecting with different
    settings does not require uploading the file again."""

    def __init__(self, size: int = 24):
        self.size = size
        self.data: OrderedDict = OrderedDict()
        self.lock = threading.Lock()

    def put(self, value) -> str:
        key = uuid.uuid4().hex[:12]
        with self.lock:
            self.data[key] = value
            while len(self.data) > self.size:
                self.data.popitem(last=False)
        return key

    def get(self, key):
        with self.lock:
            if key not in self.data:
                raise KeyError("Yükleme süresi doldu; dosyayı tekrar yükleyin.")
            self.data.move_to_end(key)
            return self.data[key]


def create_app(library_root: str | pathlib.Path = "library") -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024
    lib_root = pathlib.Path(library_root).resolve()
    uploads = _Cache()
    reports = _Cache(size=12)

    def lib() -> Library:
        return Library(lib_root)          # fresh each call: files may change on disk

    # ------------------------------------------------------------ errors
    @app.errorhandler(Exception)
    def _err(exc):
        if isinstance(exc, HTTPException):      # 404, 405...: keep their real status
            return jsonify({"error": f"{exc.code}: {exc.name}"}), exc.code
        known = (LibraryError, ValueError, KeyError, FileExistsError, FileNotFoundError)
        if isinstance(exc, known):
            msg = exc.args[0] if exc.args else str(exc)
            return jsonify({"error": str(msg)}), 400
        traceback.print_exc()
        return jsonify({"error": f"Beklenmeyen hata: {exc}"}), 500

    # ------------------------------------------------------------ static
    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/static/<path:name>")
    def static_file(name):
        return send_from_directory(STATIC, name)

    # ------------------------------------------------------------ library
    @app.get("/api/overview")
    def overview():
        L = lib()
        groups = []
        for kind in COMPONENT_KINDS:
            items = []
            for stem in L.list_kind(kind):
                try:
                    obj = L.load_component(f"{kind}/{stem}.yaml", kind)
                    items.append({"stem": stem, "name": getattr(obj, "name", stem),
                                  "summary": _summary(kind, obj),
                                  "validated": bool(getattr(obj, "validated", False)),
                                  "map": _map_kind(obj)})
                except Exception as exc:          # show broken files, don't hide them
                    items.append({"stem": stem, "name": stem, "error": str(exc)})
            groups.append({"kind": kind, "label": KIND_LABELS_TR[kind], "items": items})
        vehicles = []
        for stem in L.list_kind("vehicle"):
            try:
                v = L.load_vehicle(stem)
                vehicles.append({
                    "stem": stem, "name": v.name, "variant": v.variant,
                    "validated": v.validated, "unvalidated": v.unvalidated_components(),
                    "summary": (f"{_tr(v.battery.nominal_energy_kwh)} kWh · "
                                f"{_tr(v.motor.peak_power_w / 1e3)} kW · "
                                f"{_SYSTEM_TR.get(v.hvac.system_type, v.hvac.system_type)} · "
                                f"{v.passenger_capacity} yolcu")})
            except Exception as exc:
                vehicles.append({"stem": stem, "name": stem, "error": str(exc)})
        return jsonify({"components": groups, "vehicles": vehicles,
                        "cycles": L.list_cycles(), "routes": _routes(L),
                        "traffic_levels": [[k, v.label] for k, v in TRAFFIC_LEVELS.items()],
                        "library_root": str(lib_root)})

    @app.get("/api/schema/<kind>")
    def get_schema(kind):
        return jsonify(schema(kind))

    @app.get("/api/component/<kind>/<stem>")
    def get_component(kind, stem):
        L = lib()
        obj = L.load_component(f"{kind}/{stem}.yaml", kind)
        raw = yaml.safe_load((L.root / kind / f"{stem}.yaml").read_text(encoding="utf-8")) or {}
        # Map fields are file references; hand the raw reference back so the
        # form can keep it unchanged without reverse-engineering a path.
        refs = {f["name"]: raw[f["name"]] for f in schema(kind)["fields"]
                if f["type"] == "map" and isinstance(raw.get(f["name"]), str)}
        out = {"stem": stem, "values": component_values(obj, kind),
               "description": raw_description(L, kind, stem),
               "summary": _summary(kind, obj), "raw_refs": refs}
        if kind == "motor" and obj.efficiency_map is not None:
            out["map_preview"] = _map_preview(obj.efficiency_map)
        return jsonify(out)

    @app.post("/api/component/<kind>")
    def post_component(kind):
        if kind not in COMPONENT_KINDS:
            raise ValueError(f"Bilinmeyen bileşen tipi: {kind}")
        body = request.get_json(force=True)
        L = lib()
        stem = safe_stem(body.get("stem") or body["values"].get("name") or kind)
        values = dict(body["values"])
        # Refuse before writing anything: a failed save must not leave a map
        # CSV behind, or overwrite another motor's map with the same name.
        if (L.root / kind / f"{stem}.yaml").exists() and not body.get("overwrite"):
            raise FileExistsError(f"{kind}/{stem}.yaml zaten var. Başka bir ad seçin.")
        map_refs = {}
        for field, up_id in (body.get("map_uploads") or {}).items():
            imp = uploads.get(up_id)["import"]
            rel = _write_map(L, kind, stem, imp)
            map_refs[field] = rel
        path = save_component(L, kind, stem, values, description=body.get("description", ""),
                              map_refs=map_refs, overwrite=bool(body.get("overwrite")))
        return jsonify({"saved": f"{kind}/{path.name}", "stem": path.stem})

    @app.post("/api/component/<kind>/<stem>/delete")
    def delete_component(kind, stem):
        L = lib()
        src = L.root / kind / f"{safe_stem(stem)}.yaml"
        users = _vehicles_using(L, kind, src.stem)
        if users:
            raise ValueError(f"Bu bileşeni kullanan araçlar var: {', '.join(users)}. "
                             f"Önce onları başka bir bileşene bağlayın.")
        trash = L.root / ".cop_kutusu" / kind
        trash.mkdir(parents=True, exist_ok=True)
        dst = trash / f"{src.stem}_{time.strftime('%Y%m%d_%H%M%S')}.yaml"
        shutil.move(str(src), dst)
        return jsonify({"moved_to": str(dst.relative_to(L.root))})

    # ------------------------------------------------------------ map import
    @app.post("/api/import/map")
    def import_map_upload():
        f = request.files["file"]
        data = f.read()
        table = read_table(data, f.filename or "yuklenen")
        imp = import_map(table)
        up_id = uploads.put({"table": table, "import": imp, "data": data,
                             "filename": f.filename})
        return jsonify({"upload_id": up_id, **imp.to_dict()})

    @app.post("/api/import/map/<up_id>")
    def import_map_redetect(up_id):
        entry = uploads.get(up_id)
        overrides = request.get_json(force=True) or {}
        table = entry["table"]
        if overrides.get("sheet") and overrides["sheet"] != table.sheet:
            table = read_table(entry["data"], entry["filename"], sheet=overrides["sheet"])
            entry["table"] = table
        imp = import_map(table, {k: overrides.get(k) for k in
                                 ("orientation", "speed_unit", "value_type")})
        entry["import"] = imp
        return jsonify({"upload_id": up_id, **imp.to_dict()})

    @app.post("/api/import/map/<up_id>/attach")
    def attach_map(up_id):
        """Save the imported map and point an existing motor at it."""
        body = request.get_json(force=True)
        L = lib()
        stem = safe_stem(body["motor"])
        motor_path = L.root / "motor" / f"{stem}.yaml"
        if not motor_path.exists():
            raise FileNotFoundError(f"motor/{stem}.yaml bulunamadı")
        rel = _write_map(L, "motor", stem, uploads.get(up_id)["import"])
        _set_yaml_key(motor_path, "efficiency_map", rel)
        L.load_component(f"motor/{stem}.yaml", "motor")      # must still load
        return jsonify({"saved": f"motor/{rel}", "motor": stem})

    # ------------------------------------------------------------ curves
    @app.post("/api/import/curve")
    def import_curve_upload():
        f = request.files["file"]
        field = request.form.get("field", "")
        ctx = {}
        if request.form.get("cells_series"):
            ctx["cells_series"] = float(request.form["cells_series"])
        overrides = {k: request.form.get(k) for k in ("x_scale", "y_scale")
                     if request.form.get(k)}
        out = import_curve(read_table(f.read(), f.filename or "egri"), field,
                           overrides, ctx)
        return jsonify(out)

    # ------------------------------------------------------------ routes
    @app.post("/api/import/route")
    def import_route_upload():
        f = request.files["file"]
        spacing = request.form.get("stop_spacing_m")
        speed = request.form.get("default_speed_kmh")
        r = import_route(f.read(), f.filename or "guzergah",
                         default_speed_kmh=float(speed) if speed else 50.0,
                         stop_spacing_m=float(spacing) if spacing else None)
        up_id = uploads.put({"route": r, "filename": f.filename})
        return jsonify({"upload_id": up_id, **r.to_dict()})

    @app.post("/api/import/route/<up_id>/save")
    def save_route(up_id):
        body = request.get_json(force=True)
        L = lib()
        stem = safe_stem(body.get("name") or pathlib.Path(uploads.get(up_id)["filename"]).stem)
        path = L.root / "route" / f"{stem}.csv"
        if path.exists() and not body.get("overwrite"):
            raise FileExistsError(f"route/{stem}.csv zaten var")
        uploads.get(up_id)["route"].write_csv(path)
        return jsonify({"saved": f"route/{path.name}", "stem": stem})

    # ------------------------------------------------------------ vehicles
    @app.get("/api/vehicle/<stem>")
    def get_vehicle(stem):
        L = lib()
        p = L.root / "vehicle" / f"{stem}.yaml"
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        v = L.load_vehicle(stem)
        slots = {}
        for slot, kind in VEHICLE_SLOTS.items():
            ref = raw.get(slot)
            ref = ref.get("ref") if isinstance(ref, dict) else ref
            slots[slot] = pathlib.Path(str(ref)).stem if ref else None
        return jsonify({"stem": stem, "name": v.name, "variant": v.variant,
                        "seats": v.seats, "standees": v.standees, "slots": slots,
                        "control": component_values(v.control, "control"),
                        "description": " ".join(str(raw.get("description", "")).split()),
                        "describe": v.describe()})

    @app.post("/api/vehicle")
    def post_vehicle():
        b = request.get_json(force=True)
        path = save_vehicle(lib(), b.get("stem") or b["name"] + " " + b.get("variant", ""),
                            name=b["name"], variant=b.get("variant", ""),
                            seats=b.get("seats", 28), standees=b.get("standees", 60),
                            slots=b["slots"], control=b.get("control", {}),
                            description=b.get("description", ""),
                            overwrite=bool(b.get("overwrite")))
        return jsonify({"saved": f"vehicle/{path.name}", "stem": path.stem})

    # ------------------------------------------------------------ simulate
    @app.post("/api/simulate")
    def run_simulation():
        b = request.get_json(force=True)
        L = lib()
        veh = L.load_vehicle(b["vehicle"])
        cyc = _cycle_from(L, b, veh)
        env = _env_from(b)
        result = simulate(veh, cyc, env)
        rid = reports.put(render_report(result, lang="tr",
                                        daily_km=float(b.get("daily_km") or 200)))
        base = 0.0
        if b.get("route"):
            base = float(Route.from_csv(L.root / "route" / f"{b['route']}.csv").elevation_m[0])
        tl = timeline(result, base_elevation_m=base)
        doors = np.asarray(tl["doors"])
        starts = np.nonzero(np.diff(np.r_[0, doors]) == 1)[0]
        play = {
            "timeline": tl,
            "events": play_events(result),
            "profile": {"d": tl["d"], "elev": tl["elev"],
                        "stops": [tl["d"][i] for i in starts],
                        "signals": (cyc.meta or {}).get("signals", []),
                        "length_m": tl["d"][-1], "name": cyc.name},
            "traffic": _traffic_payload(cyc),
            "env": {"ambient": env.ambient_c, "solar": env.solar_irradiance_w_per_m2,
                    "night": env.night},
            "vehicle": {"name": veh.name, "variant": veh.variant,
                        "doors": veh.hvac.door_count, "pack_kwh": veh.battery.nominal_energy_kwh,
                        "usable_kwh": veh.battery.usable_energy_kwh,
                        "motor_kw": veh.motor.peak_power_w / 1e3,
                        "hvac_kw": veh.hvac.heating_capacity_w / 1e3},
        }
        return jsonify({
            "report_id": rid, "kpi": _kpi(result),
            "achievable": result.achievable, "warnings": localise(result.warnings, "tr"),
            "cycle": _cycle_tr(cyc), "play": play})

    # ------------------------------------------------------------ free drive
    @app.post("/api/drive/setup")
    def drive_setup():
        """What the browser needs to drive the bus live: the vehicle's own
        parameters, plus the cabin and pack thermal draw the real model settles
        to in these conditions (the live view cannot run the thermal model)."""
        b = request.get_json(force=True)
        L = lib()
        base = L.load_vehicle(b["vehicle"])
        veh = _tuned(base, b.get("tune"))
        env = _env_from(b)
        pax = float(b.get("passengers") or 0)
        ch, dl, mot, batt, aux = veh.chassis, veh.driveline, veh.motor, veh.battery, veh.auxiliaries
        # steady thermal draw: 15 min at a steady 30 km/h, averaged over the last 5
        n = 900
        warm = simulate(veh, DriveCycle(np.arange(n, dtype=float), np.full(n, 8.33),
                                        passengers=np.full(n, pax), doors_open=np.zeros(n, bool),
                                        name="drive-setup"), env)
        s = warm.series
        v_tab = [0.0, 2.0, 5.0, 8.33, 12.0, 16.0, 20.0, 25.0]
        return jsonify({
            "vehicle": {"name": veh.name, "variant": veh.variant},
            "mass_kg": ch.kerb_mass_kg, "passenger_mass_kg": ch.passenger_mass_kg,
            "m_eq_kg": ch.equivalent_rotating_mass_kg(dl.referred_inertia_kgm2(0)),
            "coastdown": ([ch.coastdown_a_n, ch.coastdown_b_n_per_mps, ch.coastdown_c_n_per_mps2,
                           ch.coastdown_test_mass_kg, ch.coastdown_air_density]
                          if ch.uses_coastdown else None),
            "rolling_c0": ch.rolling_c0, "rolling_c1": ch.rolling_c1_s_per_m,
            "cda_m2": ch.drag_coefficient * ch.frontal_area_m2,
            "rho": air_density(env.ambient_c, env.air_pressure_pa),
            "wheel_r_m": ch.tyre_dynamic_radius_m,
            "ratios": [dl.total_ratio(g) for g in range(len(dl.gear_ratios))],
            "shift_up_mps": dl.shift_up_speed_mps, "shift_down_mps": dl.shift_down_speed_mps,
            "driveline_eff": dl.gearbox_efficiency * dl.final_drive_efficiency,
            "motor": {"peak_power_w": mot.peak_power_w, "peak_torque_nm": mot.peak_torque_nm,
                      "max_speed_rads": mot.max_speed_rads,
                      "max_regen_power_w": mot.max_regen_power_w or mot.peak_power_w,
                      "eff": mot.peak_efficiency},
            "battery": {"usable_kwh": batt.usable_energy_kwh, "nominal_kwh": batt.nominal_energy_kwh,
                        "max_charge_w": batt.max_charge_c_rate * batt.capacity_ah * batt.nominal_voltage_v,
                        "max_discharge_w": batt.max_discharge_c_rate * batt.capacity_ah * batt.nominal_voltage_v,
                        "soc_start": veh.control.soc_start, "soc_min": batt.soc_min},
            "aux_v_mps": v_tab,
            "aux_w": [aux.power(v, night=env.night)["total_w"] for v in v_tab],
            "hvac_w": float(np.mean(s["p_hvac_w"][-300:])),
            "pack_thermal_w": float(np.mean(s["p_pack_thermal_w"][-300:])),
            "cabin_c": float(s["cabin_c"][-1]),
            "pack_c": float(s["pack_c"][-1]),
            "surfaces": [{"key": k, "label": lab, "factor": f, "note": note}
                         for k, lab, f, note in SURFACES],
            "tune": {"base": _tune_values(base), "now": _tune_values(veh),
                     "limits": {k: [lo, hi] for k, (_, _, _, lo, hi) in TUNABLES.items()}},
        })

    @app.post("/api/drive/simulate")
    def drive_simulate():
        """Replay a drive recorded in the browser through the real model."""
        b = request.get_json(force=True)
        L = lib()
        veh = _tuned(L.load_vehicle(b["vehicle"]), b.get("tune"))
        t = np.asarray(b["t"], dtype=float)
        if t.size < 3 or np.any(np.diff(t) <= 0):
            raise ValueError("Sürüş kaydı çok kısa ya da zaman sırası bozuk.")
        n = t.size
        pax = float(b.get("passengers") or 0)
        cyc = DriveCycle(t - t[0], np.clip(np.asarray(b["v"], dtype=float), 0, None),
                         grade_rad=np.arctan(np.asarray(b["grade_pct"], dtype=float) / 100.0),
                         # a city-bus mission records the headcount as people get on and off
                         passengers=(np.asarray(b["pax"], dtype=float) if b.get("pax") and len(b["pax"]) == n
                                     else np.full(n, pax)),
                         doors_open=np.asarray(b.get("doors") or np.zeros(n), dtype=float) > 0.5,
                         name=b.get("name") or "Serbest sürüş", source="interactive drive",
                         surface_factor=np.asarray(b.get("surface") or np.ones(n), dtype=float))
        env = _env_from(b)
        result = simulate(veh, cyc, env)
        rid = reports.put(render_report(result, lang="tr", daily_km=float(b.get("daily_km") or 200)))
        tt = result.totals
        return jsonify({
            "report_id": rid, "kpi": _kpi(result),
            "kwh": {k: round(float(tt[k]), 3) for k in (
                "battery_energy_kwh", "traction_kwh", "regen_recovered_kwh", "regen_lost_kwh",
                "hvac_kwh", "auxiliary_kwh", "pack_thermal_kwh", "rolling_kwh", "aero_kwh",
                "grade_kwh", "friction_brake_kwh", "motor_loss_kwh", "driveline_loss_kwh",
                "battery_loss_kwh")},
            "achievable": result.achievable, "warnings": localise(result.warnings, "tr")})

    @app.get("/api/preview")
    def preview():
        """Road geometry for the idle scene, before anything has been run."""
        L = lib()
        if request.args.get("route"):
            return jsonify(profile_from_route(Route.from_csv(
                L.root / "route" / f"{safe_stem(request.args['route'])}.csv")))
        return jsonify(profile_from_cycle(L.load_cycle(request.args.get("cycle") or "cbdbus")))

    @app.post("/api/sweep")
    def run_sweep():
        b = request.get_json(force=True)
        L = lib()
        refs = b.get("vehicles") or [b["vehicle"]]
        temps = list(np.arange(float(b.get("t_min", -15)), float(b.get("t_max", 40)) + 0.01,
                               float(b.get("t_step", 5))))
        first = L.load_vehicle(refs[0])
        cyc = _cycle_from(L, b, first)
        series = []
        for ref in refs:
            veh = L.load_vehicle(ref)
            pts = []
            for tc in temps:
                env = Environment(ambient_c=float(tc),
                                  solar_irradiance_w_per_m2=float(b.get("solar") or 0)
                                  if tc >= 25 else 0.0)
                r = simulate(veh, cyc, env)
                pts.append({"t": float(tc), "kwh_per_km": round(r.energy_per_km_kwh, 3),
                            "range_km": round(r.range_km, 1),
                            "hvac_share": round(r.totals["hvac_share"], 3)})
            series.append({"vehicle": ref, "label": f"{veh.name} {veh.variant}".strip(),
                           "points": pts})
        return jsonify({"series": series, "cycle": _cycle_tr(cyc)})

    @app.post("/api/traffic_compare")
    def traffic_compare():
        """One vehicle, one route, one climate -- every traffic level."""
        b = request.get_json(force=True)
        if not b.get("route"):
            raise ValueError("Trafik karşılaştırması bir güzergâh gerektirir; standart "
                             "çevrimlerin hız profili trafiği zaten içerir.")
        L = lib()
        veh = L.load_vehicle(b["vehicle"])
        env = _env_from(b)
        rows = []
        for key, lv in TRAFFIC_LEVELS.items():
            cyc = _cycle_from(L, {**b, "traffic": key}, veh)
            r = simulate(veh, cyc, env)
            t = r.totals
            m = cyc.meta or {}
            hours = t["duration_s"] / 3600.0
            rows.append({
                "key": key, "label": lv.label,
                "mean_kmh": round(cyc.mean_speed_kmh, 1),
                "minutes": round(t["duration_s"] / 60, 1),
                "kwh_per_km": round(r.energy_per_km_kwh, 3),
                "kwh_per_h": round(t["battery_energy_kwh"] / hours, 1),
                "range_km": round(r.range_km, 1),
                "hvac_kwh": round(t["hvac_kwh"], 2), "aux_kwh": round(t["auxiliary_kwh"], 2),
                "brake_kwh": round(t["friction_brake_kwh"], 2),
                "red_lights": len(m.get("signal_stops", [])),
                "red_wait_s": round(sum(e["t_go"] - e["t0"] for e in m.get("signal_stops", []))),
                "jams": len(m.get("jams", [])),
                "jam_s": round(sum(j["t1"] - j["t0"] for j in m.get("jams", []))),
            })
        return jsonify({"rows": rows, "route": b["route"], "ambient": env.ambient_c,
                        "synthetic_signals": bool((cyc.meta or {}).get("signals_synthetic"))})

    @app.post("/api/maraton")
    def maraton():
        """Maraton GEN3 fuel and pad regressions; speed inputs from the chosen duty."""
        from ..maraton import FuelInputs, fuel_l100, pad_wear, speed_stats
        b = request.get_json(force=True)
        L = lib()
        if b.get("route"):
            cyc = Route.from_csv(L.root / "route" / f"{b['route']}.csv").to_cycle(
                traffic=b.get("traffic") or "yok")
        else:
            cyc = L.load_cycle(b.get("cycle") or "hhddt65")
        st = speed_stats(cyc.speed_mps, cyc.time_s)
        num = lambda k, d: float(b[k]) if b.get(k) not in (None, "") else d
        x = FuelInputs(idle_l100=num("idle", 1.0), ac_pct=num("ac", 0.0), v_mean_kmh=st["v_mean_kmh"],
                       rpm1200_pct=num("rpm1200", 17.0), pedal_pct=num("pedal", 76.0),
                       week=num("week", 30.0), gt102_pct=st["gt102_pct"], gt80_pct=st["gt80_pct"],
                       ambient_c=num("ambient", 20.0), kale_radiator=bool(b.get("kale")),
                       body=b.get("body") or "CD")
        return jsonify({"cycle": cyc.name, "stats": st, "fuel": fuel_l100(x), "pads": pad_wear(st["v030_pct"])})

    @app.get("/report/<rid>")
    def get_report(rid):
        return Response(reports.get(rid), mimetype="text/html")

    # ------------------------------------------------------------ validation
    @app.post("/api/import/log")
    def import_log_upload():
        f = request.files["file"]
        data = f.read()
        table = read_table(data, f.filename or "log")
        det = detect_log_columns(table)
        up_id = uploads.put({"table": table, "filename": f.filename})
        preview = [[("" if c is None else str(c)) for c in row[:30]]
                   for row in table.cells[det["header_row"]:det["header_row"] + 6]]
        return jsonify({"upload_id": up_id, "headers": det["headers"],
                        "mapping": det["mapping"], "header_row": det["header_row"],
                        "roles": [[k, LOG_ROLES_TR[k]] for k in ROLE_ORDER],
                        "preview": preview,
                        "table": table.describe()})

    @app.post("/api/validate")
    def run_validation():
        b = request.get_json(force=True)
        entry = uploads.get(b["upload_id"])
        mapping = {k: int(v) for k, v in (b.get("mapping") or {}).items()
                   if v not in (None, "", -1, "-1")}
        log, notes, settings = build_log(
            entry["table"], mapping, int(b.get("header_row", 0)),
            speed_unit=b.get("speed_unit") or None, power_sign=b.get("power_sign") or None)
        L = lib()
        veh = L.load_vehicle(b["vehicle"])
        env = None
        if b.get("ambient") not in (None, ""):
            env = Environment(ambient_c=float(b["ambient"]), label="girilen")
        rep = validate(veh, log, environment=env,
                       tolerance_energy=float(b.get("tolerance", 5)) / 100.0)
        s = rep.result.series
        t = s["time_s"]
        step = max(1, t.size // 1500)
        meas_p = (np.interp(t, log.time_s, log.battery_power_w) / 1e3).tolist() \
            if log.battery_power_w is not None else None
        meas_soc = (np.interp(t, log.time_s, log.soc) * 100).tolist() \
            if log.soc is not None else None
        m = rep.metrics
        return jsonify({
            "passed": rep.passed, "summary": rep.summary(),
            "notes": notes + localise(rep.notes, "tr"), "settings": settings,
            "metrics": {k: v for k, v in m.items() if k not in ("by_phase",)},
            "by_phase": {k: {"sim": a, "meas": c} for k, (a, c) in m.get("by_phase", {}).items()},
            "tolerance": rep.tolerance_energy,
            "overlay": {"time_s": t[::step].round(1).tolist(),
                        "sim_kw": (s["p_battery_w"][::step] / 1e3).round(2).tolist(),
                        "meas_kw": None if meas_p is None else np.round(meas_p[::step], 2).tolist(),
                        "sim_soc": (s["soc"][::step] * 100).round(2).tolist(),
                        "meas_soc": None if meas_soc is None else np.round(meas_soc[::step], 2).tolist()},
        })

    return app


# ---------------------------------------------------------------- helpers
_SYSTEM_TR = {"heat_pump": "ısı pompası", "hybrid": "ısı pompası + yedek",
              "resistive": "rezistif", "fuel_fired": "yakıtlı ısıtıcı"}
_LAYOUT_TR = {"direct_drive": "doğrudan aks", "central_motor": "merkezi motor + şanzıman",
              "portal_axle": "portal aks"}


def _tr(v, d=0) -> str:
    """Number in Turkish notation: 1.234,5"""
    s = f"{v:,.{d}f}"
    return s.replace(",", " ").replace(".", ",").replace(" ", ".")


def _summary(kind, o) -> str:
    """One Turkish line per component for list views."""
    try:
        if kind == "chassis":
            rl = "coast-down ölçümü" if o.uses_coastdown else f"Cd {_tr(o.drag_coefficient, 2)} · {_tr(o.frontal_area_m2, 1)} m²"
            return f"{_tr(o.kerb_mass_kg)} kg boş · {rl}"
        if kind == "driveline":
            return (f"{_LAYOUT_TR.get(o.layout, o.layout)} · toplam oran "
                    f"{_tr(o.total_ratio(0), 2)} · verim %{_tr(100 * o.gearbox_efficiency * o.final_drive_efficiency, 1)}")
        if kind == "motor":
            return (f"{o.technology} · {_tr(o.peak_power_w / 1e3)} kW tepe / "
                    f"{_tr(o.continuous_power_w / 1e3)} kW sürekli · {_tr(o.peak_torque_nm)} Nm")
        if kind == "battery":
            return (f"{o.chemistry} {o.cells_series}s{o.cells_parallel}p · "
                    f"{_tr(o.nominal_energy_kwh)} kWh ({_tr(o.usable_energy_kwh)} kullanılabilir) · "
                    f"{_tr(o.nominal_voltage_v)} V")
        if kind == "hvac":
            return (f"{_SYSTEM_TR.get(o.system_type, o.system_type)} · {_tr(o.heating_capacity_w / 1e3)} kW ısıtma / "
                    f"{_tr(o.cooling_capacity_w / 1e3)} kW soğutma · UA {_tr(o.ua_w_per_k)} W/K")
        if kind == "auxiliary":
            return (f"{_tr((o.constant_w + o.lighting_w) / 1e3, 1)} kW sabit · direksiyon "
                    f"{_tr(o.steering_pump_w / 1e3, 1)} kW · kompresör {_tr(o.air_compressor_w / 1e3, 1)} kW")
        if kind == "aging":
            return (f"{o.chemistry} · ömür sonu %{_tr(100 * o.end_of_life_capacity)}"
                    + ("" if o.validated else " · literatür katsayıları"))
    except Exception:
        pass
    return ""


def _map_kind(obj):
    m = getattr(obj, "efficiency_map", None)
    if m is None:
        return None
    return "sentetik" if str(m.source).upper().startswith("SYNTHETIC") else "ölçülmüş"


def _map_preview(m):
    from ..units import RPM
    return {"speed_rpm": [round(float(x) / RPM, 1) for x in m.x],
            "torque_nm": [round(float(y), 1) for y in m.y],
            "efficiency": [[round(float(v), 4) for v in row] for row in m.z],
            "source": m.source}


def _write_map(L: Library, kind: str, stem: str, imp) -> str:
    folder = L.root / kind / "maps"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stem}_verim.csv"
    imp.map.to_csv(path)
    note = (f"# Imported from {imp.table.get('source')} via the temsasim interface.\n"
            f"# Detected: {imp.settings}\n")
    path.write_text(note + path.read_text(), encoding="utf-8")
    return f"maps/{path.name}"


def _set_yaml_key(path: pathlib.Path, key: str, value: str) -> None:
    """Set one top-level key in place, keeping the file's comments."""
    text = path.read_text(encoding="utf-8")
    line = f"{key}: {value}"
    pat = re.compile(rf"^{re.escape(key)}\s*:.*$", re.M)
    text = pat.sub(line, text) if pat.search(text) else text.rstrip() + "\n" + line + "\n"
    path.write_text(text, encoding="utf-8")


def _vehicles_using(L: Library, kind: str, stem: str) -> list:
    """Vehicles whose component *references* point at this file. Parsed, not
    grepped: a vehicle's notes may mention a component without using it."""
    users = []
    for v in L.list_kind("vehicle"):
        raw = yaml.safe_load((L.root / "vehicle" / f"{v}.yaml").read_text(encoding="utf-8")) or {}
        for slot, slot_kind in VEHICLE_SLOTS.items():
            ref = raw.get(slot)
            ref = ref.get("ref") if isinstance(ref, dict) else ref
            if slot_kind == kind and ref and pathlib.Path(str(ref)).stem == stem:
                users.append(v)
                break
    return users


# Vehicle settings the free-drive page may change for one session. They are
# applied to a copy of the vehicle; the library file is never touched.
# key: (component, field, UI unit -> SI factor, low, high) in UI units.
TUNABLES = {
    "kerb_mass_kg": ("chassis", "kerb_mass_kg", 1.0, 3000.0, 40000.0),
    "motor_peak_kw": ("motor", "peak_power_w", 1000.0, 30.0, 800.0),
    "motor_peak_nm": ("motor", "peak_torque_nm", 1.0, 200.0, 8000.0),
    "regen_kw": ("motor", "max_regen_power_w", 1000.0, 0.0, 800.0),
    "rolling_c0": ("chassis", "rolling_c0", 1.0, 0.003, 0.03),
    "drag_cd": ("chassis", "drag_coefficient", 1.0, 0.3, 1.0),
    "battery_kwh": ("battery", None, 1.0, 50.0, 1000.0),
    "soc_start_pct": ("control", "soc_start", 0.01, 20.0, 100.0),
}


def _tune_values(veh) -> dict:
    """The tunable settings of a vehicle, in the units the form shows."""
    m = veh.motor
    return {"kerb_mass_kg": veh.chassis.kerb_mass_kg, "motor_peak_kw": m.peak_power_w / 1e3,
            "motor_peak_nm": m.peak_torque_nm,
            "regen_kw": (m.max_regen_power_w if m.max_regen_power_w is not None else m.peak_power_w) / 1e3,
            "rolling_c0": veh.chassis.rolling_c0, "drag_cd": veh.chassis.drag_coefficient,
            "battery_kwh": veh.battery.nominal_energy_kwh, "soc_start_pct": veh.control.soc_start * 100,
            "coastdown": veh.chassis.uses_coastdown}


def _tuned(veh, tune):
    """A copy of the vehicle with the session's settings applied."""
    if not tune:
        return veh
    parts = {"chassis": {}, "motor": {}, "battery": {}, "control": {}}
    for key, raw in tune.items():
        if key not in TUNABLES or raw is None or raw == "":
            continue
        comp, fld, k, lo, hi = TUNABLES[key]
        val = float(raw)
        if not (lo <= val <= hi):
            raise ValueError(f"Araç ayarı aralık dışında: {key} = {val:g} (izin verilen {lo:g}–{hi:g})")
        if key == "battery_kwh":
            # same cells in the same arrangement, each one bigger or smaller
            parts["battery"]["cell_capacity_ah"] = (veh.battery.cell_capacity_ah
                                                   * val / veh.battery.nominal_energy_kwh)
        else:
            parts[comp][fld] = val * k
    if not any(parts.values()):
        return veh
    return replace(veh, variant=f"{veh.variant} · sürüş ayarlı".strip(" ·"),
                   chassis=replace(veh.chassis, **parts["chassis"]),
                   motor=replace(veh.motor, **parts["motor"]),
                   battery=replace(veh.battery, **parts["battery"]),
                   control=replace(veh.control, **parts["control"]))


def _kpi(result) -> dict:
    t = result.totals
    return {"kwh_per_km": round(result.energy_per_km_kwh, 3),
            "range_km": round(result.range_km, 1),
            "distance_km": round(t["distance_km"], 2),
            "duration_min": round(t["duration_s"] / 60, 1),
            "hvac_share": round(t["hvac_share"], 3),
            "regen_kwh": round(t["regen_recovered_kwh"], 2),
            "regen_lost_kwh": round(t["regen_lost_kwh"], 2),
            "soc_end": round(float(result.series["soc"][-1]), 3)}


# Road surfaces for the free-drive road editor: rolling-resistance multiplier
# relative to the good dry asphalt the chassis coefficients describe. Typical
# literature values for heavy-vehicle tyres, not measurements -- the UI says so.
SURFACES = [
    ("yeni_asfalt", "Yeni asfalt", 0.95, "pürüzsüz, yeni serilmiş"),
    ("asfalt", "İyi asfalt", 1.00, "şasi katsayılarının tanımlandığı yüzey"),
    ("beton", "Beton", 0.97, "derzli beton kaplama"),
    ("eski_asfalt", "Eskimiş asfalt", 1.15, "yamalı, çatlaklı"),
    ("parke", "Parke taşı", 1.40, "kilitli beton parke"),
    ("arnavut", "Arnavut kaldırımı", 1.80, "doğal taş, çok pürüzlü"),
    ("stabilize", "Stabilize yol", 2.30, "sıkıştırılmış çakıl ve toprak"),
]


def _cycle_tr(c) -> str:
    text = (f"{c.name}: {_tr(c.distance_km, 2)} km, {_tr(c.duration_s / 60, 1)} dk, "
            f"ortalama {_tr(c.mean_speed_kmh, 1)} km/h, azami {_tr(c.max_speed_kmh, 0)} km/h, "
            f"{c.stop_count} durak (km başına {_tr(c.stops_per_km, 1)}), "
            f"sürenin %{_tr(100 * c.idle_fraction, 0)} kadarı duruşta")
    m = c.meta or {}
    if m.get("traffic") and m["traffic"] != "yok":
        text += (f"; trafik {m['traffic_label'].lower()}: {len(m.get('signal_stops', []))} kırmızı ışık, "
                 f"{len(m.get('jams', []))} sıkışıklık")
    return text


def _traffic_payload(c) -> dict:
    m = c.meta or {}
    keep = ("traffic", "traffic_label", "signals", "signals_active", "signals_synthetic",
            "signal_stops", "jams", "queue_spacing_m", "discharge_s_per_car", "reaction_s",
            "oncoming_per_km", "lap_length_m", "laps")
    return {k: m[k] for k in keep if k in m}


def _routes(L: Library) -> list:
    d = L.root / "route"
    return sorted(p.stem for p in d.glob("*.csv")) if d.exists() else []


def _cycle_from(L: Library, b: dict, veh):
    occ = None
    mode = b.get("occupancy_mode", "profile")
    n = float(b.get("passengers") or 0)
    if mode == "constant":
        occ = n
    elif n > 0:
        occ = (lambda f, peak=n: peak * 4.0 * f * (1.0 - f))
    rep = max(1, int(b.get("repeat") or 1))
    if b.get("route"):
        route = Route.from_csv(L.root / "route" / f"{b['route']}.csv")
        level = b.get("traffic") or "yok"
        if level not in TRAFFIC_LEVELS:
            raise ValueError(f"Bilinmeyen trafik seviyesi: {level}")
        # Laps are driven, not copied, so signal timings keep running.
        return route.to_cycle(vehicle=veh, occupancy=occ, traffic=level, laps=rep)
    cyc = L.load_cycle(b.get("cycle") or "cbdbus")
    if occ is not None:
        cyc = cyc.with_occupancy(occ)
    return cyc.repeat(rep) if rep > 1 else cyc


def _env_from(b: dict) -> Environment:
    return Environment(ambient_c=float(b.get("ambient", 20)),
                       solar_irradiance_w_per_m2=float(b.get("solar") or 0),
                       night=bool(b.get("night")),
                       label=b.get("label") or f"{float(b.get('ambient', 20)):+.0f} degC")


def _already_running(port: int) -> bool:
    """Windows lets a second server bind the same port, and requests then go to
    whichever answers -- often an old version. Check before starting one."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def serve(library_root="library", port: int = 8765, open_browser: bool = True) -> None:
    url = f"http://127.0.0.1:{port}/"
    if _already_running(port):
        print(f"\n  Arayüz zaten çalışıyor: {url}  (tarayıcıda açılıyor)\n"
              f"  Yeniden başlatmak için önce çalışan pencereyi kapatın.\n")
        if open_browser:
            webbrowser.open(url)
        return
    app = create_app(library_root)
    print(f"\n  temsasim arayüzü çalışıyor:  {url}")
    print("  Kapatmak için bu pencerede Ctrl+C.\n")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
