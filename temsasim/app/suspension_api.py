"""Suspension: the endpoints behind the workbench's "Süspansiyon" tab.

Everything is computed from the model sent with the request, so a centre of
gravity or a damper node can be moved and the answer seen before anything is
written. Only ``save`` touches the library.
"""

from __future__ import annotations

import math

import numpy as np
from flask import jsonify, request

from .. import suspension as S


def clean(obj):
    """numpy scalars and arrays to plain JSON; not-a-number to null."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return clean(obj.tolist())
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        v = float(obj)
        return v if math.isfinite(v) else None
    return obj


def vehicle_suspension_state(L, vehicle, passengers: float | None = None):
    """The suspension state for a library vehicle, or None when the library has no suspension file."""
    stem = (vehicle.source or "").rsplit("/", 1)[-1].removesuffix(".yaml")
    sus, _linked = S.suspension_for(L.root, stem)
    if not sus:
        return None
    try:
        return S.state_for_vehicle(S.load_suspension(L.root, sus), vehicle, passengers=passengers)
    except (ValueError, KeyError, FileNotFoundError):
        return None


def register(app, lib) -> None:
    def vehicle_info(L, stem):
        v = L.load_vehicle(stem)
        ch = v.chassis
        return v, {"stem": stem, "name": v.name, "kerb_mass_kg": ch.kerb_mass_kg, "gross_mass_kg": ch.gross_mass_kg,
                   "passenger_mass_kg": ch.passenger_mass_kg, "seats": int(v.seats), "capacity": int(v.passenger_capacity)}

    def model_from(L, vehicle_stem, b):
        if b.get("model"):
            return S.SuspensionModel.from_dict(dict(b["model"]), stem=str((b["model"] or {}).get("stem") or ""))
        stem = b.get("file") or S.suspension_for(L.root, vehicle_stem)[0]
        if not stem:
            raise ValueError("Kütüphanede süspansiyon dosyası yok (library/suspension).")
        return S.load_suspension(L.root, stem)

    @app.get("/api/suspension/<vehicle>")
    def suspension_get(vehicle):
        L = lib()
        _v, info = vehicle_info(L, vehicle)
        files = S.list_suspensions(L.root)
        linked_stem, linked = S.suspension_for(L.root, vehicle)
        stem = request.args.get("file") or linked_stem
        if not stem:
            raise ValueError("Kütüphanede süspansiyon dosyası yok (library/suspension).")
        m = S.load_suspension(L.root, stem)
        return jsonify(clean({
            "vehicle": info, "stem": m.stem, "linked": bool(linked and stem == linked_stem), "files": files,
            "model": m.to_dict(), "params": S.param_info(m), "groups": S.PARAM_GROUPS,
            "manoeuvres": {k: {"label": v["label"], "tip": v["tip"], "choices": v.get("choices", {}),
                               "params": [{"key": p[0], "label": p[1], "unit": p[2], "default": p[3], "min": p[4], "max": p[5], "step": p[6]} for p in v["params"]]}
                           for k, v in S.MANOEUVRES.items()},
            "manoeuvre_order": list(S.MANOEUVRES), "rate_bases": S.RATE_BASES, "road_classes": S.ISO8608_LABEL,
        }))

    @app.post("/api/suspension/<vehicle>/analyse")
    def suspension_analyse(vehicle):
        L = lib()
        b = request.get_json(force=True) or {}
        v, info = vehicle_info(L, vehicle)
        m = model_from(L, vehicle, b)
        pax = b.get("passengers")
        pax = float(info["seats"] if pax is None else pax)
        st = S.State(m, kerb_mass_kg=info["kerb_mass_kg"], passengers=pax, passenger_mass_kg=info["passenger_mass_kg"],
                     luggage_kg=float(b.get("luggage_kg") or 0.0))
        parts = set(b.get("parts") or ["base", "sim"])
        out = {}
        if "base" in parts:
            summ = st.summary()
            summ["over_gross"] = bool(st.mass > info["gross_mass_kg"] + 1e-6)
            summ["gross_mass_kg"] = info["gross_mass_kg"]
            out["summary"] = summ
            out["modes"] = st.modes()
            out["dampers"] = [d.to_dict() for d in st.damper_objs]
        man = b.get("manoeuvre") or {}
        kind = man.get("kind") or "fren"
        if "sim" in parts:
            res = S.simulate(st, S.manoeuvre_inputs(st, kind, man.get("params")))
            out["sim"] = {"kind": kind, "params": res["params"], "kpis": S.simulation_kpis(st, res), "explorer": S.explorer_payload(st, res)}
        if "roll" in parts:
            ro = S.rollover(st, rise_s=float(b.get("rise_s") or 0.5))
            ro["at_radius"] = S.speeds_on_radius(ro, float(b.get("radius_m") or 100.0))
            k = math.radians(m.understeer_deg_per_g)
            r_m = ro["at_radius"]["radius_m"]
            ro["steer"] = {"understeer_deg_per_g": m.understeer_deg_per_g, "ackermann_deg": math.degrees(m.wheelbase_m / r_m),
                           "at_static_deg": math.degrees(S.steer_angle(m.wheelbase_m, r_m, ro["at_radius"]["static_kmh"] / 3.6, k)),
                           "characteristic_kmh": 3.6 * S.characteristic_speed(m.wheelbase_m, k) if k > 0 else None}
            if kind in ("serit",):
                ro["manoeuvre_lift"] = dict(S.lift_speed(st, kind, man.get("params") or {}), kind=kind)
            out["rollover"] = ro
        if "comfort" in parts:
            out["comfort"] = S.comfort(st, float(b.get("comfort_speed_kmh") or 80.0))
        return jsonify(clean(out))

    @app.post("/api/suspension/<vehicle>/save")
    def suspension_save(vehicle):
        L = lib()
        b = request.get_json(force=True) or {}
        vehicle_info(L, vehicle)
        d = dict(b.get("model") or {})
        if not d:
            raise ValueError("Kaydedilecek model gönderilmedi.")
        if b.get("name"):
            d["name"] = str(b["name"])
        stem = S.safe_name(b.get("stem") or d.get("stem") or d.get("name") or "suspansiyon")
        d["vehicles"] = sorted(set(list(d.get("vehicles") or []) + [vehicle])) if b.get("link", True) else list(d.get("vehicles") or [])
        m = S.SuspensionModel.from_dict(d, stem=stem)
        if b.get("link", True):
            # one suspension per vehicle: take the vehicle off any other file
            for f in S.list_suspensions(L.root):
                if f["stem"] != stem and vehicle in f["vehicles"]:
                    other = S.load_suspension(L.root, f["stem"])
                    other.vehicles = [x for x in other.vehicles if x != vehicle]
                    S.save_suspension(L.root, other, overwrite=True)
        p = S.save_suspension(L.root, m, overwrite=bool(b.get("overwrite", True)))
        return jsonify({"saved": f"suspension/{p.name}", "stem": p.stem, "vehicles": m.vehicles})
