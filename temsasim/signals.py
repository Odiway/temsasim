"""The time series of a run as named, grouped signals for the signal explorer.

The report's fixed charts answer the questions everyone asks. The explorer is
for the one nobody planned: any signals on one plane (speed, gear and engine
speed; torque against full load; current against SOC), each on its own scale,
zoomable. This module only prepares the data: Turkish labels, units, groups
and a few ready-made selections; the drawing is ``app/static/explorer.js``,
which the application loads and the HTML report embeds.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np

from .units import KWH, RPM

EXPLORER_JS = pathlib.Path(__file__).parent / "app" / "static" / "explorer.js"


def _sig(key, label, unit, values, group, *, step=False, digits=1):
    return {"key": key, "label": label, "unit": unit, "group": group, "step": bool(step), "digits": digits,
            "values": values}


def _clean(a, digits):
    a = np.asarray(a, dtype=float)
    out = np.round(a, digits).tolist()
    return [None if (v != v or v in (float("inf"), float("-inf"))) else v for v in out]


def explorer_payload(result, *, max_points: int = 6000) -> dict:
    """Signals of a diesel or battery-electric run, thinned to ``max_points``."""
    s = result.series
    n = int(s["time_s"].size)
    stride = max(1, int(np.ceil(n / max_points)))
    sl = slice(None, None, stride)
    dt = result.dt_s
    v = s["speed_mps"]
    kmh = v * 3.6
    dist_km = s["distance_m"] / 1000.0 if "distance_m" in s else np.cumsum(v) * dt / 1000.0
    elev = np.cumsum(v * dt * np.sin(s["grade_rad"]))
    moving = v > 1.4                      # below 5 km/h a per-km figure is meaningless
    sig = []

    def add(key, label, unit, values, group, **kw):
        digits = kw.pop("digits", 1)
        sig.append(_sig(key, label, unit, _clean(np.asarray(values, dtype=float)[sl], digits), group, digits=digits, **kw))

    add("speed_kmh", "Hız", "km/h", kmh, "Sürüş")
    add("accel", "İvme", "m/s²", s["accel_mps2"], "Sürüş", digits=2)
    add("grade_pct", "Eğim", "%", np.tan(s["grade_rad"]) * 100.0, "Sürüş", digits=2)
    add("elevation_m", "Rakım farkı", "m", elev, "Sürüş")
    add("passengers", "Yolcu", "kişi", s["passengers"], "Sürüş", step=True, digits=0)
    add("mass_t", "Kütle", "t", s["mass_kg"] / 1000.0, "Sürüş", digits=2)

    if getattr(result, "kind", "") == "diesel":
        eng = result.vehicle.engine
        rho = eng.fuel_density_kg_per_m3
        lph = s["fuel_g_per_s"] * 3600.0 / rho
        fuel_l = s["fuel_cum_g"] / rho
        with np.errstate(divide="ignore", invalid="ignore"):
            l100 = np.where(moving, lph / np.maximum(kmh, 1e-6) * 100.0, np.nan)
            l100_avg = np.where(dist_km > 0.05, fuel_l / np.maximum(dist_km, 1e-9) * 100.0, np.nan)
            load = np.where(s["engine_torque_max_nm"] > 1, s["engine_torque_nm"] / s["engine_torque_max_nm"] * 100.0, 0.0)
        add("gear", "Vites", "", s["gear"], "Şanzıman", step=True, digits=0)
        if "clutch_open" in s:
            add("clutch_open", "Debriyaj açık", "", s["clutch_open"], "Şanzıman", step=True, digits=0)
        add("gearbox_loss_kw", "Şanzıman kaybı", "kW", s["gearbox_loss_w"] / 1e3, "Şanzıman", digits=2)
        add("axle_loss_kw", "Aks kaybı", "kW", s["axle_loss_w"] / 1e3, "Şanzıman", digits=2)
        add("retarder_loss_kw", "Retarder boşta kaybı", "kW", s["retarder_loss_w"] / 1e3, "Şanzıman", digits=2)
        add("engine_rpm", "Motor devri", "d/d", s["engine_speed_rads"] / RPM, "Motor", digits=0)
        add("engine_torque", "Motor torku", "Nm", s["engine_torque_nm"], "Motor", digits=0)
        add("engine_torque_max", "Tam yük torku", "Nm", s["engine_torque_max_nm"], "Motor", digits=0)
        add("engine_load", "Motor yükü", "%", load, "Motor", digits=0)
        add("p_engine_kw", "Krank gücü", "kW", s["p_engine_w"] / 1e3, "Motor")
        add("fuel_lph", "Yakıt debisi", "l/h", lph, "Yakıt", digits=2)
        add("l100_inst", "Anlık tüketim", "l/100 km", l100, "Yakıt")
        add("l100_avg", "Ortalama tüketim", "l/100 km", l100_avg, "Yakıt", digits=2)
        add("fuel_cum_l", "Toplam yakıt", "l", fuel_l, "Yakıt", digits=3)
        add("p_wheel_kw", "Tekerlek gücü", "kW", s["p_wheel_w"] / 1e3, "Güç")
        add("p_friction_brake_kw", "Fren", "kW", s["p_friction_brake_w"] / 1e3, "Güç")
        add("p_engine_brake_kw", "Motor freni", "kW", s["p_engine_brake_w"] / 1e3, "Güç")
        add("p_aux_kw", "PTO + yardımcılar", "kW", (s["p_aux_w"] + s["p_fan_w"]) / 1e3, "Güç", digits=2)
        add("p_hvac_kw", "Klima", "kW", s["p_hvac_w"] / 1e3, "Güç", digits=2)
        presets = [["Vites ve devir", ["speed_kmh", "gear", "engine_rpm"]],
                   ["Motor çalışma noktası", ["engine_rpm", "engine_torque", "engine_torque_max"]],
                   ["Yakıt", ["speed_kmh", "fuel_lph", "l100_inst"]],
                   ["Yol ve vites", ["speed_kmh", "grade_pct", "gear"]],
                   ["Güç dağılımı", ["p_engine_kw", "p_wheel_kw", "p_friction_brake_kw", "p_aux_kw"]]]
    else:
        pb = s["p_battery_w"]
        e_net = np.cumsum(pb) * dt / KWH
        with np.errstate(divide="ignore", invalid="ignore"):
            kwh_km = np.where(moving, pb / 1e3 / np.maximum(kmh, 1e-6), np.nan)
            kwh_km_avg = np.where(dist_km > 0.05, e_net / np.maximum(dist_km, 1e-9), np.nan)
        add("motor_rpm", "Motor devri", "d/d", s["motor_speed_rads"] / RPM, "Motor", digits=0)
        add("motor_torque", "Motor torku", "Nm", s["motor_torque_nm"], "Motor", digits=0)
        add("motor_eff", "Motor + inverter verimi", "%", s["motor_efficiency"] * 100.0, "Motor")
        add("p_motor_mech_kw", "Motor mekanik gücü", "kW", s["p_motor_mech_w"] / 1e3, "Motor")
        add("p_motor_elec_kw", "Motor elektrik gücü", "kW", s["p_motor_elec_w"] / 1e3, "Motor")
        add("motor_loss_kw", "Motor kaybı", "kW", s["motor_loss_w"] / 1e3, "Motor", digits=2)
        add("driveline_loss_kw", "Aktarma kaybı", "kW", s["driveline_loss_w"] / 1e3, "Motor", digits=2)
        add("soc", "Şarj durumu (SOC)", "%", s["soc"] * 100.0, "Batarya", digits=2)
        add("battery_voltage", "Batarya gerilimi", "V", s["battery_voltage_v"], "Batarya")
        add("battery_current", "Batarya akımı", "A", s["battery_current_a"], "Batarya")
        add("p_battery_kw", "Batarya gücü", "kW", pb / 1e3, "Batarya")
        add("regen_kw", "Geri kazanılan güç", "kW", np.clip(-pb, 0, None) / 1e3, "Batarya")
        add("regen_lost_kw", "Kullanılamayan frenleme gücü", "kW", s["regen_lost_w"] / 1e3, "Batarya")
        add("battery_loss_kw", "Batarya kaybı", "kW", s["battery_loss_w"] / 1e3, "Batarya", digits=2)
        add("pack_c", "Batarya sıcaklığı", "°C", s["pack_c"], "Batarya")
        add("kwh_km_inst", "Anlık tüketim", "kWh/km", kwh_km, "Enerji", digits=2)
        add("kwh_km_avg", "Ortalama tüketim", "kWh/km", kwh_km_avg, "Enerji", digits=3)
        add("e_net_kwh", "Bataryadan çekilen net enerji", "kWh", e_net, "Enerji", digits=2)
        add("p_wheel_kw", "Tekerlek gücü", "kW", s["p_wheel_w"] / 1e3, "Güç")
        add("p_friction_brake_kw", "Fren", "kW", s["p_friction_brake_w"] / 1e3, "Güç")
        add("p_aux_kw", "Yardımcı tüketiciler", "kW", s["p_aux_w"] / 1e3, "Güç", digits=2)
        add("p_hvac_kw", "Kabin içi termal yönetim", "kW", s["p_hvac_w"] / 1e3, "Güç", digits=2)
        add("p_pack_thermal_kw", "Batarya termal yönetimi", "kW", s["p_pack_thermal_w"] / 1e3, "Güç", digits=2)
        presets = [["Hız ve SOC", ["speed_kmh", "soc", "p_battery_kw"]],
                   ["Motor", ["motor_rpm", "motor_torque", "motor_eff"]],
                   ["Batarya", ["battery_voltage", "battery_current", "soc"]],
                   ["Tüketim", ["speed_kmh", "kwh_km_inst", "grade_pct"]],
                   ["Güç dağılımı", ["p_battery_kw", "p_motor_elec_kw", "p_hvac_kw", "p_aux_kw"]]]
    add("p_rolling_kw", "Yuvarlanma direnci", "kW", s["p_rolling_w"] / 1e3, "Yol yükü")
    add("p_aero_kw", "Hava direnci", "kW", s["p_aero_w"] / 1e3, "Yol yükü")
    add("p_grade_kw", "Eğim", "kW", s["p_grade_w"] / 1e3, "Yol yükü")
    add("p_inertia_kw", "Atalet", "kW", s["p_inertia_w"] / 1e3, "Yol yükü")
    add("cabin_c", "Kabin sıcaklığı", "°C", s["cabin_c"], "Kabin")
    add("hvac_q_kw", "Klima ısıl gücü", "kW", s["hvac_q_w"] / 1e3, "Kabin", digits=2)
    add("doors_open", "Kapılar açık", "", s["doors_open"], "Kabin", step=True, digits=0)
    return {
        "title": f"{result.vehicle.name} · {result.cycle.name}",
        "x": [{"key": "time_s", "label": "Zaman", "unit": "s", "values": _clean(s["time_s"][sl], 1)},
              {"key": "distance_km", "label": "Mesafe", "unit": "km", "values": _clean(dist_km[sl], 3)}],
        "signals": sig, "presets": presets, "selected": presets[0][1]}


def explorer_html(result, *, element_id: str = "sinyaller") -> str:
    """The explorer as a self-contained block for a report: the script and the
    data inline, so the saved HTML file works on its own."""
    js = EXPLORER_JS.read_text(encoding="utf-8")
    data = json.dumps(explorer_payload(result), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (f'<div id="{element_id}" class="tx-host"></div>\n<script>{js}</script>\n'
            f'<script>TsimExplorer.mount(document.getElementById("{element_id}"), {data});</script>')
