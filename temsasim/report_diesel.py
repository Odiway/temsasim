"""HTML report for a diesel run.

Same look as the battery-bus report (its stylesheet is reused), but the
story is a different one: where each litre went. Charts are rendered as
inline SVG on the server, so the file is self-contained and opens anywhere.
"""

from __future__ import annotations

import html
import json
import pathlib

import numpy as np

from .diesel import DieselResult
from .messages import localise, tr_num
from .units import KWH, RPM

HVAC_TR = {"heat_pump": "ısı pompalı", "hybrid": "ısı pompası + yedek ısıtıcılı",
           "resistive": "rezistif", "fuel_fired": "yakıtlı ısıtıcılı"}


def _style() -> str:
    from .report import _TEMPLATE
    return _TEMPLATE.split("<style>", 1)[1].split("</style>", 1)[0]


# ---------------------------------------------------------------- svg helpers
def _ticks(lo: float, hi: float, n: int = 5) -> list:
    if hi <= lo:
        hi = lo + 1.0
    raw = (hi - lo) / n
    mag = 10 ** np.floor(np.log10(raw))
    step = min((s for s in (1, 2, 2.5, 5, 10) if s * mag >= raw), default=10) * mag
    start = np.ceil(lo / step) * step
    return [float(v) for v in np.arange(start, hi + 1e-9, step)]


def _fmt(v: float, d: int = 0) -> str:
    return tr_num(v, d)


def line_chart(x, series, *, width=1100, height=220, xlabel="", ylabel="", y0=None,
               step=False, ylim=None) -> str:
    """``series``: list of (label, y-array, colour). Shared x axis."""
    x = np.asarray(x, dtype=float)
    pad_l, pad_r, pad_t, pad_b = 54, 14, 10, 30
    W, H = width - pad_l - pad_r, height - pad_t - pad_b
    ys = [np.asarray(s[1], dtype=float) for s in series]
    lo = min(float(np.nanmin(y)) for y in ys) if ylim is None else ylim[0]
    hi = max(float(np.nanmax(y)) for y in ys) if ylim is None else ylim[1]
    if y0 is not None:
        lo = min(lo, y0)
    if hi - lo < 1e-9:
        hi = lo + 1.0
    lo -= 0.03 * (hi - lo)
    hi += 0.06 * (hi - lo)
    x0, x1 = float(x.min()), float(x.max()) if x.max() > x.min() else float(x.min()) + 1.0
    sx = lambda v: pad_l + (v - x0) / (x1 - x0) * W
    sy = lambda v: pad_t + (1 - (v - lo) / (hi - lo)) * H
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" preserveAspectRatio="none" '
           f'style="width:100%;height:{height}px">']
    for t in _ticks(lo, hi):
        out.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{sy(t):.1f}" y2="{sy(t):.1f}" stroke="var(--grid)"/>'
                   f'<text x="{pad_l - 6}" y="{sy(t) + 4:.1f}" text-anchor="end" font-size="11" fill="var(--muted)">{_fmt(t, 1 if abs(t) < 10 and t != int(t) else 0)}</text>')
    for t in _ticks(x0, x1, 8):
        out.append(f'<line x1="{sx(t):.1f}" x2="{sx(t):.1f}" y1="{pad_t}" y2="{pad_t + H}" stroke="var(--grid)"/>'
                   f'<text x="{sx(t):.1f}" y="{height - 10}" text-anchor="middle" font-size="11" fill="var(--muted)">{_fmt(t, 1 if x1 - x0 < 10 else 0)}</text>')
    n = x.size
    stride = max(1, n // 1500)
    for label, y, col in series:
        y = np.asarray(y, dtype=float)
        pts = []
        for i in range(0, n, stride):
            if not np.isfinite(y[i]):
                continue
            if step and pts:
                pts.append(f"L{sx(x[i]):.1f} {pts[-1].split(' ')[1]}")
            pts.append(f"{'M' if not pts else 'L'}{sx(x[i]):.1f} {sy(y[i]):.1f}")
        out.append(f'<path d="{" ".join(pts)}" fill="none" stroke="{col}" stroke-width="1.5" vector-effect="non-scaling-stroke"/>')
    legend = " · ".join(f'<tspan fill="{c}">■</tspan> {html.escape(l)}' for l, _, c in series)
    out.append(f'<text x="{pad_l + 4}" y="{pad_t + 12}" font-size="11" fill="var(--ink-2)">{legend}</text>')
    if ylabel:
        out.append(f'<text x="4" y="{pad_t + 10}" font-size="11" fill="var(--muted)">{html.escape(ylabel)}</text>')
    if xlabel:
        out.append(f'<text x="{width - pad_r}" y="{height - 10}" text-anchor="end" font-size="11" fill="var(--muted)">{html.escape(xlabel)}</text>')
    out.append("</svg>")
    return "".join(out)


def scatter_map(result: DieselResult, *, width=560, height=360) -> str:
    """Engine operating points over the full-load and drag curves, coloured
    by time share (darker = more time)."""
    eng = result.vehicle.engine
    s = result.series
    moving = s["speed_mps"] > 0.05
    rpm = s["engine_speed_rads"][moving] / RPM
    tq = s["engine_torque_nm"][moving]
    w = np.linspace(eng.idle_speed_rads, eng.rated_speed_rads * 1.25, 80)
    fl = np.array([eng.max_torque(v) for v in w])
    dr = np.array([eng.drag_torque(v) for v in w])
    pad_l, pad_r, pad_t, pad_b = 54, 14, 10, 30
    W, H = width - pad_l - pad_r, height - pad_t - pad_b
    x0, x1 = float(w[0] / RPM) - 50, float(w[-1] / RPM)
    lo, hi = float(dr.min()) - 100, float(fl.max()) + 100
    sx = lambda v: pad_l + (v - x0) / (x1 - x0) * W
    sy = lambda v: pad_t + (1 - (v - lo) / (hi - lo)) * H
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" style="width:100%;height:{height}px">']
    for t in _ticks(lo, hi, 6):
        out.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{sy(t):.1f}" y2="{sy(t):.1f}" stroke="var(--grid)"/>'
                   f'<text x="{pad_l - 6}" y="{sy(t) + 4:.1f}" text-anchor="end" font-size="11" fill="var(--muted)">{_fmt(t)}</text>')
    for t in _ticks(x0, x1, 6):
        out.append(f'<line x1="{sx(t):.1f}" x2="{sx(t):.1f}" y1="{pad_t}" y2="{pad_t + H}" stroke="var(--grid)"/>'
                   f'<text x="{sx(t):.1f}" y="{height - 10}" text-anchor="middle" font-size="11" fill="var(--muted)">{_fmt(t)}</text>')
    # 2-D histogram of operating points as shaded cells
    if rpm.size:
        hx = np.linspace(x0, x1, 41)
        hy = np.linspace(lo, hi, 31)
        Hc, _, _ = np.histogram2d(rpm, tq, bins=[hx, hy])
        m = Hc.max() or 1.0
        for i in range(hx.size - 1):
            for j in range(hy.size - 1):
                c = Hc[i, j]
                if c <= 0:
                    continue
                a = 0.15 + 0.85 * (c / m) ** 0.5
                out.append(f'<rect x="{sx(hx[i]):.1f}" y="{sy(hy[j + 1]):.1f}" width="{sx(hx[i + 1]) - sx(hx[i]):.1f}" '
                           f'height="{sy(hy[j]) - sy(hy[j + 1]):.1f}" fill="var(--s1)" fill-opacity="{a:.2f}"/>')
    pts = " ".join(f"{'M' if i == 0 else 'L'}{sx(w[i] / RPM):.1f} {sy(fl[i]):.1f}" for i in range(w.size))
    out.append(f'<path d="{pts}" fill="none" stroke="var(--ink)" stroke-width="1.8"/>')
    pts = " ".join(f"{'M' if i == 0 else 'L'}{sx(w[i] / RPM):.1f} {sy(dr[i]):.1f}" for i in range(w.size))
    out.append(f'<path d="{pts}" fill="none" stroke="var(--ink)" stroke-width="1.2" stroke-dasharray="4 3"/>')
    out.append(f'<text x="{pad_l + 4}" y="{pad_t + 12}" font-size="11" fill="var(--ink-2)">tam yük eğrisi (düz), sürükleme (kesikli); mavi: zaman payı</text>')
    out.append(f'<text x="{width - pad_r}" y="{height - 10}" text-anchor="end" font-size="11" fill="var(--muted)">devir (rpm)</text>')
    out.append(f'<text x="4" y="{pad_t + 10}" font-size="11" fill="var(--muted)">Nm</text>')
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------- report
def render_diesel_report(result: DieselResult, *, lang: str = "tr", daily_km: float = 200.0, **_) -> str:
    v, t, s, env = result.vehicle, result.totals, result.series, result.environment
    eng, tr = v.engine, v.transmission
    km = s["distance_m"] / 1000.0
    fuel_lph = (s["fuel_g_per_s"] + s["heater_fuel_g_per_s"]) * 3600.0 / eng.fuel_density_kg_per_m3
    l100 = result.fuel_l_per_100km
    esc = html.escape

    def tile(label, value, unit, note=""):
        return (f'<div class="tile"><div class="k">{esc(label)}</div><div class="v">{value}'
                f'<small> {esc(unit)}</small></div>{f"<div class=note2>{esc(note)}</div>" if note else ""}</div>')

    tiles = "".join([
        tile("Yakıt tüketimi", _fmt(l100, 2), "l/100 km", f"{_fmt(t['fuel_total_l'], 2)} l toplam"),
        tile("CO₂", _fmt(result.co2_g_per_km, 0), "g/km", "tank-to-wheel"),
        tile("Mesafe", _fmt(t["distance_km"], 2), "km", f"{_fmt(t['duration_s'] / 60, 1)} dk, ort. {_fmt(t['mean_speed_kmh'], 1)} km/h"),
        tile("Rölantide yakıt", _fmt(t["idle_fuel_l"], 2), "l", f"sürenin %{_fmt(100 * t['idle_fraction'], 0)} kadarı duruşta"),
        tile("Ortalama motor devri", _fmt(t["mean_engine_rpm"], 0), "rpm", f"{t['shifts']} vites değişimi"),
        tile("Isıtıcı yakıtı", _fmt(t["heater_fuel_l"], 2), "l", f"motor ısısıyla {_fmt(t['engine_heat_to_cabin_kwh'], 1)} kWh"),
        tile("Yıllık yakıt (tahmin)", _fmt(l100 / 100 * daily_km * 340 / 1000, 1), "bin l", f"{_fmt(daily_km, 0)} km/gün, 340 gün"),
    ])

    bal = result.energy_balance()
    e_total = max(t["fuel_energy_kwh"], 1e-9)
    rows = "".join(f"<tr><td>{esc(k)}</td><td class=num>{_fmt(val, 2)}</td><td class=num>{_fmt(100 * val / e_total, 1)} %</td></tr>"
                   for k, val in bal.items())
    bal_tr = {"engine losses (heat, exhaust)": "Motor kayıpları (ısı, egzoz)", "heater fuel": "Yakıtlı ısıtıcı",
              "rolling resistance": "Yuvarlanma direnci", "aerodynamic drag": "Hava direnci",
              "gradient (net)": "Eğim (net)", "inertia (net)": "Atalet (net)",
              "friction brakes (incl. retarder braking)": "Sürtünmeli frenler (retarder frenlemesi dahil)",
              "engine braking": "Motor freni", "axle losses": "Aks kayıpları", "gearbox losses": "Şanzıman kayıpları",
              "retarder idle drag": "Retarder boşta sürüklemesi", "clutch slip": "Debriyaj kayması",
              "auxiliaries (mechanical)": "Yardımcı tüketiciler (mekanik)", "cooling fan": "Soğutma fanı",
              "HVAC compressor and blowers": "Klima kompresörü ve fanlar",
              "demand not delivered (unmet)": "Karşılanamayan talep", "residual (should be ~0)": "Artık (≈0 olmalı)"}
    rows = "".join(f"<tr><td>{esc(bal_tr.get(k, k))}</td><td class=num>{_fmt(val, 2)}</td><td class=num>{_fmt(100 * val / e_total, 1)} %</td></tr>"
                   for k, val in bal.items())

    dt = result.dt_s
    cab = {"Dış kabuk (cam + gövde)": float(np.sum(s["q_envelope_w"]) * dt / KWH),
           "Havalandırma": float(np.sum(s["q_ventilation_w"]) * dt / KWH),
           "Kapılar": float(np.sum(s["q_doors_w"]) * dt / KWH),
           "Yolcular": float(np.sum(s["q_passengers_w"]) * dt / KWH),
           "Güneş": float(np.sum(s["q_solar_w"]) * dt / KWH),
           "Motor soğutma suyundan ısı": float(np.sum(s["q_engine_heat_w"]) * dt / KWH),
           "Yakıtlı ısıtıcıdan ısı": float(np.sum(s["q_heater_w"]) * dt / KWH),
           "Klima soğutması": float(-np.sum(np.clip(s["hvac_q_w"], None, 0)) * dt / KWH)}
    cab_rows = "".join(f"<tr><td>{esc(k)}</td><td class=num>{_fmt(val, 2)}</td></tr>" for k, val in cab.items())

    gear_rows = "".join(f"<tr><td>{g}</td><td class=num>{_fmt(100 * sh, 1)} %</td></tr>"
                        for g, sh in sorted(t["gear_share"].items()) if sh > 0.001)

    warns = localise(result.warnings, "tr")
    banner = ""
    if not result.achievable:
        banner += (f'<div class="banner warn">⚠ Araç bu görevi {result.unmet_samples} saniye boyunca takip edemedi '
                   f'(motor tam yükte yetersiz kaldı). Sonuçlar yalnızca gösterge niteliğindedir.</div>')
    if warns:
        banner += '<div class="banner warn"><ul style="margin:0;padding-left:18px">' + "".join(f"<li>{esc(w)}</li>" for w in warns) + "</ul></div>"

    env_text = (f"{_fmt(env.ambient_c, 0)} °C dış sıcaklık · {_fmt(env.solar_irradiance_w_per_m2, 0)} W/m² güneş · "
                f"{'gece' if env.night else 'gündüz'}")
    comp = [("Şasi", v.chassis.describe()), ("Motor", eng.describe()), ("Şanzıman ve aks", tr.describe()),
            ("Kabin ve klima", v.hvac.describe()), ("Yardımcılar", v.auxiliaries.describe())]
    comp_rows = "".join(f"<tr><td>{esc(a)}</td><td class=small>{esc(b)}</td></tr>" for a, b in comp)

    charts = [
        ("Hız ve anlık yakıt, güzergâh boyunca",
         line_chart(km, [("hız (km/h)", s["speed_mps"] * 3.6, "var(--s4)")], xlabel="km", ylabel="km/h", y0=0, height=170)
         + line_chart(km, [("yakıt (l/h)", fuel_lph, "var(--s2)")], xlabel="km", ylabel="l/h", y0=0, height=170)),
        ("Vites ve motor devri",
         line_chart(km, [("vites", s["gear"], "var(--s3)")], xlabel="km", ylabel="vites", y0=0, step=True, height=150,
                    ylim=(0, tr.n_gears + 0.5))
         + line_chart(km, [("motor devri (rpm)", s["engine_speed_rads"] / RPM, "var(--s1)")], xlabel="km", ylabel="rpm", y0=0, height=170)),
        ("Eğim ve motor gücü",
         line_chart(km, [("eğim (%)", np.tan(s["grade_rad"]) * 100, "var(--s5)")], xlabel="km", ylabel="%", y0=0, height=140)
         + line_chart(km, [("motor mili gücü (kW)", s["p_engine_w"] / 1e3, "var(--s1)"),
                           ("sürtünmeli fren (kW)", -s["p_friction_brake_w"] / 1e3, "var(--s2)")], xlabel="km", ylabel="kW", y0=0, height=170)),
    ]
    chart_html = "".join(f'<div class="card"><h2>{esc(title)}</h2>{svg}</div>' for title, svg in charts)

    assumptions = [
        f"24 V yükleri alternatör verimi 0,70 ile mekanik güce çevrildi; direksiyon pompası ve hava kompresörü doğrudan mekanik.",
        f"Kabin ısıtması motor soğutma suyundan (yakıt enerjisinin %28'i kabul); yetmeyen kısım yakıtlı ısıtıcıdan (%{_fmt(100 * v.hvac.fuel_fired_efficiency, 0)} verim).",
        f"Vites stratejisi: motor devri {_fmt(tr.downshift_speed_rads / RPM, 0)}–{_fmt(tr.upshift_speed_rads / RPM, 0)} rpm penceresi, %{_fmt(100 * tr.torque_reserve, 0)} tork rezervi, iki vites arası en az {_fmt(tr.min_shift_interval_s, 1)} s.",
        f"Yakıt özellikleri (VECTO): {_fmt(eng.fuel_density_kg_per_m3, 0)} kg/m³, {_fmt(eng.fuel_lhv_j_per_kg / 1e6, 1)} MJ/kg, {_fmt(eng.co2_kg_per_kg_fuel, 2)} kg CO₂ / kg yakıt. Yakıt haritası çarpanı {_fmt(eng.correction_factor, 4)}.",
        "Vites değiştirme sırasındaki çekiş kesintisi ve debriyaj kayıpları modellenmedi; çevrim birebir takip edildi.",
    ]
    assume_html = "".join(f"<li>{esc(a)}</li>" for a in assumptions)

    payload = {"l_per_100km": round(l100, 3), "co2_g_per_km": round(result.co2_g_per_km, 1),
               "fuel_l": round(t["fuel_total_l"], 3), "distance_km": round(t["distance_km"], 3)}
    return f"""<!DOCTYPE html>
<html lang="tr" data-theme="auto"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dizel görev raporu · {esc(v.name)}</title>
<style>{_style()}
.tile .note2 {{ color:var(--muted); font-size:11.5px; margin-top:2px; }}
table.bal {{ width:100%; border-collapse:collapse; font-size:13px; }}
table.bal td {{ padding:4px 6px; border-bottom:1px solid var(--grid); }}
table.bal td.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.chart {{ display:block; }}
.grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
@media (max-width: 900px) {{ .grid2 {{ grid-template-columns:1fr; }} }}
.small {{ font-size:12px; color:var(--ink-2); }}
</style></head>
<body><div class="wrap">
<header>
  <h1>Dizel Görev Çevrimi Raporu</h1>
  <div class="sub">{esc(v.name)}{(" · " + esc(v.variant)) if v.variant else ""} · {esc(result.cycle.name)} · {esc(env_text)}</div>
</header>
{banner}
<div class="tiles">{tiles}</div>
<div class="grid2">
  <div class="card"><h2>Yakıt enerjisi nereye gitti</h2>
    <p class="note">Yakıtın alt ısıl değeri {_fmt(e_total, 1)} kWh. Her kalem ayrı yazılır; "diğer" kutusu yoktur. Artık sıfıra yakın değilse modelde hata vardır.</p>
    <table class="bal"><tbody>{rows}</tbody></table></div>
  <div class="card"><h2>Motor çalışma noktaları</h2>
    <p class="note">Sürüş süresinin hangi devir–tork bölgesinde geçtiği. Tam yük eğrisine yaslanan koyu bölge, motorun gücünün yetmediği anları gösterir.</p>
    {scatter_map(result)}
    <table class="bal" style="margin-top:8px"><thead><tr><td>Vites</td><td class=num>Sürüş süresi payı</td></tr></thead><tbody>{gear_rows}</tbody></table></div>
</div>
{chart_html}
<div class="grid2">
  <div class="card"><h2>Kabin ısı yükleri</h2>
    <p class="note">Koşu boyunca toplam ısı; pozitif kabini ısıtır, negatif soğutur. Dizel araçta ısıtma önce motor soğutma suyundan karşılanır.</p>
    <table class="bal"><tbody>{cab_rows}</tbody></table></div>
  <div class="card"><h2>Kullanılan bileşenler</h2>
    <p class="note">Her satırda verinin ölçülmüş mü (VALIDATED) yoksa kabul mü (UNVALIDATED) olduğu görünür.</p>
    <table class="bal"><tbody>{comp_rows}</tbody></table></div>
</div>
<div class="card"><h2>Kabuller</h2><ul class="small">{assume_html}</ul></div>
<script>const D = {json.dumps(payload)};</script>
</div></body></html>"""


def write_diesel_report(result: DieselResult, path, **kw) -> pathlib.Path:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_diesel_report(result, **kw), encoding="utf-8")
    return path
