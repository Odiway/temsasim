"""Comparison report: two to four runs side by side, one self-contained HTML.

Each run already has its own full report. This one answers the question the
individual reports cannot: *which* vehicle, route or condition costs what,
on the same axes. Every series is resampled onto a common distance grid so a
44 km coach run and a 12 km city lap can share a chart; where a run ends its
line simply stops.

Electric and diesel runs can sit in the same table. The row that makes them
comparable is energy per km, where diesel is counted at the fuel's lower
heating value; the native figures (kWh/km, l/100 km) are kept beside it.
"""

from __future__ import annotations

import html

import numpy as np

from .diesel import DieselResult
from .messages import tr_num
from .report_diesel import _style, line_chart

COLOURS = ["#1f5fa8", "#e2601f", "#6f4fc0", "#1f8f52"]


def _is_diesel(r) -> bool:
    return isinstance(r, DieselResult)


def _f(v, d=1) -> str:
    return "—" if v is None else tr_num(v, d)


def run_facts(result, label: str = "") -> dict:
    """Everything the table needs, in one flat dict. Also the client's KPI source."""
    t = result.totals
    v = result.vehicle
    env = result.environment
    dz = _is_diesel(result)
    hours = max(t["duration_s"] / 3600.0, 1e-9)
    f = {
        "label": label or f"{v.name} {v.variant}".strip(),
        "vehicle": v.name, "variant": v.variant, "powertrain": v.powertrain,
        "powertrain_tr": "Dizel" if dz else "Bataryalı elektrikli",
        "duty": result.cycle.name, "ambient_c": float(env.ambient_c),
        "distance_km": float(t["distance_km"]), "duration_min": t["duration_s"] / 60.0,
        "mean_speed_kmh": t["distance_km"] / hours,
        "stops": int(t.get("stops") or 0) if t.get("stops") is not None else None,
        "energy_kwh_per_km": float(result.energy_per_km_kwh),
        "hvac_kwh": float(t.get("hvac_kwh", 0.0)), "aux_kwh": float(t.get("auxiliary_kwh", 0.0)),
        "brake_kwh": float(t.get("friction_brake_kwh", 0.0)),
        "hvac_share": float(t.get("hvac_share", 0.0)),
        "achievable": bool(result.achievable),
        "warnings": [w.tr if hasattr(w, "tr") else str(w) for w in result.warnings],
    }
    if dz:
        f.update({"consumption": float(result.fuel_l_per_100km), "consumption_unit": "l/100 km",
                  "fuel_l": float(t["fuel_total_l"]), "co2_g_per_km": float(result.co2_g_per_km),
                  "range_km": float(v.fuel_tank_l) / max(result.fuel_l_per_100km, 1e-6) * 100.0,
                  "idle_fuel_l": float(t.get("idle_fuel_l", 0.0)), "regen_kwh": 0.0,
                  "traction_kwh": float(t.get("fuel_energy_kwh", 0.0)) - f["hvac_kwh"] - f["aux_kwh"] - f["brake_kwh"],
                  "total_kwh": float(t.get("fuel_energy_kwh", 0.0))})
    else:
        f.update({"consumption": float(result.energy_per_km_kwh), "consumption_unit": "kWh/km",
                  "battery_kwh": float(t["battery_energy_kwh"]), "co2_g_per_km": None,
                  "range_km": float(result.range_km), "regen_kwh": float(t.get("regen_recovered_kwh", 0.0)),
                  "soc_end": float(result.series["soc"][-1]) * 100.0,
                  "traction_kwh": float(t.get("traction_kwh", 0.0)) - float(t.get("regen_recovered_kwh", 0.0)),
                  "total_kwh": float(t["battery_energy_kwh"])})
    return f


def _resample(result, grid_km: np.ndarray) -> dict:
    s = result.series
    km = np.asarray(s["distance_m"], dtype=float) / 1000.0
    dt = result.dt_s
    dz = _is_diesel(result)
    if dz:
        eng = result.vehicle.engine
        fuel_g = np.asarray(s["fuel_cum_g"], dtype=float)
        fuel_l = fuel_g / eng.fuel_density_kg_per_m3
        power = np.asarray(s["p_engine_w"], dtype=float) / 1e3
        energy = fuel_g / 1000.0 * eng.fuel_lhv_j_per_kg / 3.6e6
        state = (1 - fuel_l / max(float(result.vehicle.fuel_tank_l), 1.0)) * 100.0
    else:
        power = np.asarray(s["p_battery_w"], dtype=float) / 1e3
        energy = np.cumsum(power) * dt / 3600.0
        state = np.asarray(s["soc"], dtype=float) * 100.0
    speed = np.asarray(s["speed_mps"], dtype=float) * 3.6
    out = {}
    for key, arr in (("speed", speed), ("power", power), ("energy", energy), ("state", state)):
        y = np.interp(grid_km, km, arr)
        y[grid_km > km[-1] + 1e-9] = np.nan
        out[key] = y
    return out


def render_compare(runs: list, *, title: str = "Karşılaştırma") -> str:
    """``runs``: list of (label, result). Returns the HTML page."""
    facts = [run_facts(r, lab) for lab, r in runs]
    n_pts = 700
    dmax = max(f["distance_km"] for f in facts) or 1.0
    grid = np.linspace(0.0, dmax, n_pts)
    series = [_resample(r, grid) for _, r in runs]
    cols = [COLOURS[i % len(COLOURS)] for i in range(len(runs))]
    any_d = any(f["powertrain"] == "diesel" for f in facts)
    any_e = any(f["powertrain"] != "diesel" for f in facts)

    def best_idx(key, lower=True):
        vals = [(f.get(key), i) for i, f in enumerate(facts) if f.get(key) is not None]
        if len(vals) < 2:
            return None
        return (min if lower else max)(vals)[1]

    ROWS = [
        ("Araç", "vehicle", lambda f: html.escape(f["vehicle"] + (" · " + f["variant"] if f["variant"] else "")), None),
        ("Güç aktarma", "powertrain_tr", lambda f: f["powertrain_tr"], None),
        ("Görev", "duty", lambda f: html.escape(f["duty"]), None),
        ("Dış sıcaklık", "ambient_c", lambda f: f"{_f(f['ambient_c'], 0)} °C", None),
        ("Mesafe", "distance_km", lambda f: f"{_f(f['distance_km'], 2)} km", None),
        ("Süre", "duration_min", lambda f: f"{_f(f['duration_min'], 1)} dk", None),
        ("Ortalama hız", "mean_speed_kmh", lambda f: f"{_f(f['mean_speed_kmh'], 1)} km/h", None),
        ("Tüketim", "consumption", lambda f: f"{_f(f['consumption'], 3 if f['powertrain'] != 'diesel' else 2)} {f['consumption_unit']}",
         "consumption" if not (any_d and any_e) else None),
        ("Enerji", "energy_kwh_per_km", lambda f: f"{_f(f['energy_kwh_per_km'], 3)} kWh/km", "energy_kwh_per_km"),
        ("CO₂ (egzoz)", "co2_g_per_km", lambda f: "0 g/km" if f["co2_g_per_km"] is None else f"{_f(f['co2_g_per_km'], 0)} g/km", None),
        ("Menzil", "range_km", lambda f: f"{_f(f['range_km'], 0)} km", ("range_km", False)),
        ("Klima payı", "hvac_share", lambda f: f"%{_f(100 * f['hvac_share'], 0)}", "hvac_share"),
        ("Klima enerjisi", "hvac_kwh", lambda f: f"{_f(f['hvac_kwh'], 2)} kWh", "hvac_kwh"),
        ("Yardımcı tüketiciler", "aux_kwh", lambda f: f"{_f(f['aux_kwh'], 2)} kWh", "aux_kwh"),
        ("Fren kaybı (retarder + balata)", "brake_kwh", lambda f: f"{_f(f['brake_kwh'], 2)} kWh", "brake_kwh"),
        ("Geri kazanılan", "regen_kwh", lambda f: f"{_f(f['regen_kwh'], 2)} kWh" if f["powertrain"] != "diesel" else "—", ("regen_kwh", False)),
        ("Görev tamamlandı", "achievable", lambda f: "evet" if f["achievable"] else "hayır", None),
    ]
    thead = "".join(f'<th style="color:{cols[i]}">{html.escape(f["label"])}</th>' for i, f in enumerate(facts))
    body = []
    for label, key, fmt_fn, best in ROWS:
        if best is None:
            bi = None
        elif isinstance(best, tuple):
            bi = best_idx(best[0], lower=best[1])
        else:
            bi = best_idx(best, lower=True)
        cells = "".join(f'<td class="{"best" if bi == i else ""}">{fmt_fn(f)}</td>' for i, f in enumerate(facts))
        body.append(f"<tr><th>{label}</th>{cells}</tr>")
    base = facts[0]
    deltas = []
    for i, f in enumerate(facts[1:], start=1):
        d = (f["energy_kwh_per_km"] - base["energy_kwh_per_km"]) / max(base["energy_kwh_per_km"], 1e-9) * 100
        deltas.append(f'<li><b style="color:{cols[i]}">{html.escape(f["label"])}</b>: {html.escape(base["label"])} koşusuna göre enerji '
                      f'{"%+.1f" % d} %; süre {"%+.1f" % (f["duration_min"] - base["duration_min"])} dk; '
                      f'menzil {"%+.0f" % (f["range_km"] - base["range_km"])} km.</li>'.replace(".", ","))
    # energy split bars
    split_rows = []
    for i, f in enumerate(facts):
        dz = f["powertrain"] == "diesel"
        parts = [("Krank (net)" if dz else "Çekiş (net)", max(f["traction_kwh"], 0.0), cols[i]), ("Klima", f["hvac_kwh"], "#d99a00"),
                 ("PTO + fan" if dz else "Yardımcı", f["aux_kwh"], "#6f4fc0"), ("Fren" if dz else "Paket ısıl", f["brake_kwh"], "#b0261b")]
        tot = sum(p[1] for p in parts) or 1.0
        bar = "".join(f'<i title="{n}: {_f(v, 2)} kWh" style="flex:{max(v, 1e-6):.4f};background:{c}"></i>' for n, v, c in parts)
        legend = " · ".join(f'<span style="color:{c}">■</span> {n} {_f(v, 1)} kWh (%{_f(v / tot * 100, 0)})' for n, v, c in parts)
        split_rows.append(f'<div class="split-row"><div class="split-lab" style="color:{cols[i]}">{html.escape(f["label"])}</div>'
                          f'<div class="split-bar">{bar}</div><div class="split-leg">{legend}</div></div>')
    charts = [
        ("Hız (km/h)", line_chart(grid, [(f["label"], series[i]["speed"], cols[i]) for i, f in enumerate(facts)], xlabel="km", ylabel="km/h", y0=0, height=230)),
        ("Güç: batarya ya da motor mili (kW)", line_chart(grid, [(f["label"], series[i]["power"], cols[i]) for i, f in enumerate(facts)], xlabel="km", ylabel="kW", y0=0, height=230)),
        ("Kümülatif enerji (kWh; dizelde yakıtın ısıl değeri)", line_chart(grid, [(f["label"], series[i]["energy"], cols[i]) for i, f in enumerate(facts)], xlabel="km", ylabel="kWh", y0=0, height=230)),
        ("Şarj durumu ya da depo doluluğu (%)", line_chart(grid, [(f["label"], series[i]["state"], cols[i]) for i, f in enumerate(facts)], xlabel="km", ylabel="%", height=230)),
    ]
    chart_html = "".join(f'<section class="card"><h2>{html.escape(t)}</h2>{svg}</section>' for t, svg in charts)
    warns = [f'<li><b style="color:{cols[i]}">{html.escape(f["label"])}</b>: {html.escape(w)}</li>' for i, f in enumerate(facts) for w in f["warnings"]]
    note_mixed = ('<p class="muted">Elektrikli ve dizel araç aynı tabloda: Enerji satırı dizelde yakıtın alt ısıl değeridir; ikisini kıyaslamak için odur.</p>'
                  if any_d and any_e else "")
    return f"""<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>{_style()}
table.cmp {{ border-collapse: collapse; width: 100%; font-size: 13.5px; }}
table.cmp th, table.cmp td {{ padding: 6px 10px; border-bottom: 1px solid var(--rule, #dfe3e7); text-align: right; white-space: nowrap; }}
table.cmp th:first-child {{ text-align: left; font-weight: 600; color: var(--ink-2, #434c55); }}
table.cmp thead th {{ text-align: right; border-bottom: 2px solid var(--rule, #b9c0c7); }}
table.cmp td.best {{ font-weight: 700; color: #15733f; background: #e2f2e8; }}
.split-row {{ display: grid; grid-template-columns: 180px 1fr; gap: 6px 14px; align-items: center; margin: 8px 0; }}
.split-lab {{ font-weight: 600; font-size: 13px; }}
.split-bar {{ display: flex; height: 14px; gap: 2px; }} .split-bar i {{ display: block; }}
.split-leg {{ grid-column: 2; font-size: 11.5px; color: #555; }}
ul.delta li {{ margin: 4px 0; }}
.muted {{ color: var(--muted); font-size: 12.5px; }}
</style></head>
<body><div class="wrap">
<header><h1>{html.escape(title)}</h1><p class="sub">{len(facts)} koşu yan yana · her koşunun tam raporu ayrıca mevcuttur · temsasim</p></header>
<section class="card"><h2>Sonuç tablosu</h2>{note_mixed}
<div style="overflow:auto"><table class="cmp"><thead><tr><th></th>{thead}</tr></thead><tbody>{"".join(body)}</tbody></table></div>
<p class="muted">Yeşil hücre: o satırda en iyi değer (tüketimde en düşük, menzilde en yüksek).</p>
{('<ul class="delta">' + ''.join(deltas) + '</ul>') if deltas else ''}
</section>
<section class="card"><h2>Enerji nereye gitti</h2>{"".join(split_rows)}</section>
{chart_html}
{('<section class="card"><h2>Uyarılar</h2><ul>' + ''.join(warns) + '</ul></section>') if warns else ''}
</div></body></html>"""
