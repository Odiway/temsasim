"""Self-contained HTML report: a 2D bus diagram plus the charts behind it.

Two audiences read a simulation result and they need different things.

An engineer needs the operating points: where the motor actually sat on its
efficiency map, when the pack hit a limit, what the cabin load was made of.
Those are charts.

Everyone else -- a reviewer, a project manager, a customer's technical buyer --
needs to *see* the bus. A side view with energy visibly flowing from the roof
pack to the wheels, reversing when it brakes, with the doors opening at stops
and the cabin temperature dropping when they do, explains in ten seconds what a
table of kilowatt-hours does not explain at all.

The output is one HTML file with no external dependencies, so it can be emailed,
opened on a plant PC with no internet, and attached to a design review.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

import numpy as np

from .components.aging import project_life
from .messages import localise, tr_num
from .simulator import SimulationResult


def _downsample(arr: np.ndarray, n: int) -> list:
    """Reduce a series to at most n points by shared-index striding.

    Every series in the payload is strided with the same index array, so the
    speed, power and temperature plotted at a given distance really are the
    same instant. Picking each series' own extremes per bucket looks better on
    a spiky trace but silently misaligns the series against each other, which
    is worse than missing a one-second peak. Default ``points`` is high enough
    that a run of up to an hour is drawn at full 1 s resolution anyway.
    """
    a = np.asarray(arr, dtype=float)
    if a.size <= n:
        return [round(float(v), 4) for v in a]
    idx = np.linspace(0, a.size - 1, n).round().astype(int)
    return [round(float(v), 4) for v in a[idx]]


def build_payload(result: SimulationResult, *, points: int = 3600,
                  frames: int = 500, daily_km: float = 200.0,
                  annual_mean_temp_c: float = 15.0,
                  depot_rest_soc: float = 0.70,
                  operating_days_per_year: float = 340.0,
                  lang: str = "en") -> dict:
    """Turn a SimulationResult into the JSON the page renders.

    The ageing projection needs three things the drive cycle cannot supply, so
    they are explicit arguments rather than quiet guesses:

    ``annual_mean_temp_c``
        The pack spends most of its life parked, not driving. Calendar fade is
        driven by the annual mean temperature where the depot is, not by the
        ambient of whichever run happened to be simulated. Simulating a -10 degC
        winter route and then applying -10 degC calendar ageing for eight years
        would flatter the pack enormously.

    ``depot_rest_soc``
        Likewise the SOC the pack rests at overnight, which is a depot policy
        decision, not a consequence of the route. Finishing the charge shortly
        before departure instead of on plug-in is worth real years of life, and
        this argument is where that shows up.

    ``daily_km``
        Distance per vehicle-day, used to scale one simulated cycle into a
        representative day of duty.
    """
    s = result.series
    t = result.totals
    v = result.vehicle

    dist_km = s["distance_m"] / 1000.0

    def ds(key, scale=1.0):
        return _downsample(s[key] * scale, points)

    # --- motor operating points, binned onto the efficiency map --------------
    emap = v.motor.efficiency_map
    map_payload = {
        "speed_rpm": [round(float(x) * 60 / (2 * np.pi), 1) for x in emap.x],
        "torque_nm": [round(float(y), 1) for y in emap.y],
        "efficiency": [[round(float(z), 4) for z in row] for row in emap.z],
        "source": emap.source,
    }
    # Sample operating points sparsely; a scatter of 3000 dots is unreadable.
    step = max(len(s["motor_speed_rads"]) // 1500, 1)
    op = {
        "rpm": [round(float(x) * 60 / (2 * np.pi), 1)
                for x in s["motor_speed_rads"][::step]],
        "torque": [round(float(x), 1) for x in s["motor_torque_nm"][::step]],
    }

    envelope_rpm = np.linspace(1.0, v.motor.max_speed_rads, 120)
    envelope = {
        "rpm": [round(float(w) * 60 / (2 * np.pi), 1) for w in envelope_rpm],
        "peak": [round(v.motor.max_torque(float(w)), 1) for w in envelope_rpm],
        "continuous": [round(v.motor.max_torque(float(w), continuous=True), 1)
                       for w in envelope_rpm],
    }

    # --- battery ageing projection ------------------------------------------
    scale = daily_km / max(t["distance_km"], 1e-6)
    years, soh, eol = project_life(
        v.aging,
        daily_throughput_ah_per_cell=t["throughput_ah_per_cell"] * scale,
        mean_c_rate=t["mean_c_rate"],
        mean_soc=depot_rest_soc,
        operating_temp_c=t["mean_pack_c"],
        storage_temp_c=annual_mean_temp_c,
        operating_days_per_year=operating_days_per_year,
    )

    balance = result.energy_balance()
    env = result.environment
    env_text = env.describe()
    hvac_type = v.hvac.system_type
    if lang == "tr":
        env_text = (f"{tr_num(env.ambient_c, 0)} °C dış sıcaklık · "
                    f"{tr_num(env.solar_irradiance_w_per_m2, 0)} W/m² güneş · "
                    f"{'gece' if env.night else 'gündüz'}")
        hvac_type = HVAC_TR.get(hvac_type, hvac_type)

    return {
        "meta": {
            "vehicle": v.name,
            "variant": v.variant,
            "cycle": result.cycle.name,
            "cycle_source": result.cycle.source,
            "environment": env_text,
            "ambient_c": result.environment.ambient_c,
            "validated": v.validated,
            "unvalidated": v.unvalidated_components(),
            "warnings": localise(result.warnings, lang),
            "achievable": result.achievable,
            "unmet_seconds": result.unmet_samples * result.dt_s,
            "seats": v.seats,
            "standees": v.standees,
            "pack_kwh": round(v.battery.nominal_energy_kwh, 1),
            "usable_kwh": round(v.battery.usable_energy_kwh, 1),
            "motor_kw": round(v.motor.peak_power_w / 1e3),
            "hvac_type": hvac_type,
            "hvac_ua": round(v.hvac.ua_w_per_k),
            "door_count": v.hvac.door_count,
        },
        "kpi": {
            "energy_per_km": round(result.energy_per_km_kwh, 3),
            "range_km": round(result.range_km, 1),
            "distance_km": round(t["distance_km"], 2),
            "duration_min": round(t["duration_s"] / 60, 1),
            "mean_speed_kmh": round(result.cycle.mean_speed_kmh, 1),
            "hvac_share": round(t["hvac_share"], 3),
            "hvac_kwh": round(t["hvac_kwh"], 2),
            "aux_share": round(t["auxiliary_share"], 3),
            "regen_recovered_kwh": round(t["regen_recovered_kwh"], 2),
            "regen_fraction": round(t["regen_fraction_of_braking"], 3),
            "regen_lost_kwh": round(t["regen_lost_kwh"], 2),
            "battery_kwh": round(t["battery_energy_kwh"], 2),
            "soc_start": round(result.initial_soc, 3),
            "soc_end": round(float(s["soc"][-1]), 3),
            "stops_per_km": round(t["stops_per_km"], 2),
            "peak_motor_kw": round(t["peak_motor_power_kw"], 1),
            "mean_cabin_c": round(t["mean_cabin_c"], 1),
            "eol_year": round(eol, 1) if eol else None,
        },
        "series": {
            "distance_km": _downsample(dist_km, points),
            "time_s": ds("time_s"),
            "speed_kmh": ds("speed_mps", 3.6),
            "grade_pct": [round(float(np.tan(x)) * 100, 2)
                          for x in _downsample(s["grade_rad"], points)],
            "battery_kw": ds("p_battery_w", 1e-3),
            "motor_kw": ds("p_motor_elec_w", 1e-3),
            "hvac_kw": ds("p_hvac_w", 1e-3),
            "aux_kw": ds("p_aux_w", 1e-3),
            "soc": ds("soc"),
            "cabin_c": ds("cabin_c"),
            "pack_c": ds("pack_c"),
            "passengers": ds("passengers"),
            "doors_open": ds("doors_open"),
            "q_envelope_kw": ds("q_envelope_w", 1e-3),
            "q_ventilation_kw": ds("q_ventilation_w", 1e-3),
            "q_doors_kw": ds("q_doors_w", 1e-3),
            "q_passengers_kw": ds("q_passengers_w", 1e-3),
            "q_solar_kw": ds("q_solar_w", 1e-3),
            "q_hvac_kw": ds("hvac_q_w", 1e-3),
        },
        "animation": {
            "distance_km": _downsample(dist_km, frames),
            "speed_kmh": _downsample(s["speed_mps"] * 3.6, frames),
            "battery_kw": _downsample(s["p_battery_w"] * 1e-3, frames),
            "motor_kw": _downsample(s["p_motor_elec_w"] * 1e-3, frames),
            "hvac_kw": _downsample(s["p_hvac_w"] * 1e-3, frames),
            "aux_kw": _downsample(s["p_aux_w"] * 1e-3, frames),
            "soc": _downsample(s["soc"], frames),
            "cabin_c": _downsample(s["cabin_c"], frames),
            "pack_c": _downsample(s["pack_c"], frames),
            "doors": _downsample(s["doors_open"], frames),
            "passengers": _downsample(s["passengers"], frames),
            "time_s": _downsample(s["time_s"], frames),
        },
        "balance": [{"label": BALANCE_TR.get(k, k) if lang == "tr" else k,
                     "kwh": round(val, 3), "resid": k.startswith("residual")}
                    for k, val in balance.items()],
        "cell_energy_kwh": round(t["cell_energy_kwh"], 3),
        "motor_map": map_payload,
        "operating_points": op,
        "envelope": envelope,
        "aging": {
            "years": [round(float(y), 2) for y in years],
            "soh": [round(float(x), 4) for x in soh],
            "eol_year": round(eol, 1) if eol else None,
            "eol_threshold": v.aging.end_of_life_capacity,
            "calibrated": v.aging.validated,
            "assumed_daily_km": daily_km,
            "assumed_storage_c": annual_mean_temp_c,
            "assumed_rest_soc": depot_rest_soc,
            "operating_days_per_year": operating_days_per_year,
        },
    }


def render_report(result: SimulationResult, *, lang: str = "en", **kw) -> str:
    """The report as an HTML string, for serving without touching disk.

    ``lang="tr"`` produces the Turkish report used by the web interface: the
    English template is translated by an ordered substitution table (longest
    phrases first, so no fragment is translated twice), numbers switch to the
    Turkish convention (1.234,5), and solver warnings use their Turkish text.
    """
    html = _TEMPLATE
    if lang == "tr":
        for en, trs in sorted(REPORT_TR, key=lambda kv: -len(kv[0])):
            if en not in html:
                raise KeyError(f"report translation key not found in template: {en[:60]!r}")
            html = html.replace(en, trs)
        html = html.replace(_FMT_EN, _FMT_TR)
    html = html.replace("__LANG__", lang)
    return html.replace("/*__PAYLOAD__*/", json.dumps(build_payload(result, lang=lang, **kw)))


def write_report(result: SimulationResult, path: str | pathlib.Path,
                 **kw) -> pathlib.Path:
    """Render the HTML report to disk and return the path."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(result, **kw), encoding="utf-8")
    return path


HVAC_TR = {"heat_pump": "ısı pompalı", "hybrid": "ısı pompası + yedek ısıtıcılı",
           "resistive": "rezistif", "fuel_fired": "yakıtlı ısıtıcılı"}

BALANCE_TR = {
    "rolling resistance": "yuvarlanma direnci", "aerodynamic drag": "aerodinamik direnç",
    "gradient (net)": "eğim (net)", "inertia (net)": "atalet (net)",
    "friction brakes": "sürtünmeli frenler", "driveline losses": "aktarma kayıpları",
    "motor + inverter losses": "motor + invertör kayıpları",
    "battery internal losses": "batarya iç kayıpları", "HVAC": "klima",
    "auxiliaries": "yardımcı tüketiciler", "pack thermal management": "paket ısıl yönetimi",
    "demand not delivered (unmet)": "karşılanamayan talep",
    "residual (should be ~0)": "artık (≈0 olmalı)",
}

_FMT_EN = "function fmt(v, d){ return (Math.round(v*Math.pow(10,d))/Math.pow(10,d)).toFixed(d); }"
_FMT_TR = ("function fmt(v, d){ return Number(v).toLocaleString('tr-TR', "
           "{minimumFractionDigits: d, maximumFractionDigits: d}); }")

# (English as it appears in the template, Turkish). Keys are exact substrings.
REPORT_TR = [
    ("<title>Bus Duty Cycle Report</title>", "<title>Görev Çevrimi Raporu</title>"),
    ('<h1 id="title">Bus Duty Cycle Report</h1>', '<h1 id="title">Görev Çevrimi Raporu</h1>'),
    ('onclick="toggleTheme()">Toggle theme</button>', 'onclick="toggleTheme()">Tema</button>'),
    ("<h2>Energy flow through the vehicle</h2>", "<h2>Araçta enerji akışı</h2>"),
    ("""Side view with live power flow. Line thickness is power; blue flows
  out of the pack, green flows back into it under regenerative braking. Scrub or play
  to walk through the duty cycle and watch the cabin temperature move when the doors open.""",
     """Canlı güç akışıyla yandan görünüş. Çizgi kalınlığı gücü gösterir: mavi paketten çıkan,
  yeşil rejeneratif frenlemede pakete geri dönen enerjidir. Görev boyunca ilerlemek için
  kaydırın ya da oynatın; kapılar açıldığında kabin sıcaklığının nasıl düştüğünü izleyin."""),
    ('id="playbtn" onclick="togglePlay()">Play</button>', 'id="playbtn" onclick="togglePlay()">Oynat</button>'),
    ("<h2>Along the route</h2>", "<h2>Güzergâh boyunca</h2>"),
    ("""Four panels on a shared distance axis rather than one chart with
  stacked scales: speed, power at the battery terminals, state of charge, and the two
  temperatures that decide whether the numbers above hold up.""",
     """Tek grafikte üst üste eksenler yerine ortak mesafe ekseninde dört panel: hız, batarya
  uçlarındaki güç, şarj durumu ve yukarıdaki sayıların geçerli olup olmadığını belirleyen
  iki sıcaklık."""),
    ("<h2>Where the energy went</h2>", "<h2>Enerji nereye gitti</h2>"),
    ("""A closed balance from the cells outward. The residual is shown
    rather than hidden: if it is not near zero, the model has a bookkeeping error
    and nothing else on this page should be trusted.""",
     """Hücrelerden dışarıya kapalı bir denge. Artık gizlenmez, gösterilir: sıfıra yakın
    değilse modelde bir hesap hatası vardır ve bu sayfadaki başka hiçbir sayıya
    güvenilmemelidir."""),
    ("<summary>Table view</summary>", "<summary>Tablo görünümü</summary>"),
    ("<h2>What the cabin load is made of</h2>", "<h2>Kabin yükü neden oluşuyor</h2>"),
    ("""The heat balance on the cabin, positive into the cabin. Door
    openings appear as spikes because gravity drives a real air exchange every time
    a bus stops -- the load a drive-cycle-only model cannot see.""",
     """Kabinin ısı dengesi; kabine giren ısı pozitiftir. Kapı açılışları sivri tepeler olarak
    görünür, çünkü otobüs her durduğunda yerçekimi gerçek bir hava değişimi yaratır:
    yalnızca hız profiline bakan bir modelin göremediği yük."""),
    ("<h2>Motor operating points</h2>", "<h2>Motor çalışma noktaları</h2>"),
    ("""Where this duty cycle actually put the machine, on its own
    efficiency map. Clusters sitting in low-efficiency territory are the argument
    for changing the final drive ratio.""",
     """Bu görevin motoru kendi verim haritası üzerinde gerçekte nerede çalıştırdığı.
    Düşük verim bölgesinde toplanan noktalar, diferansiyel oranını değiştirmenin
    gerekçesidir."""),
    ("<h2>Projected pack life</h2>", "<h2>Öngörülen paket ömrü</h2>"),
    ("""Capacity fade from this duty repeated daily. Read it as a
    sensitivity study, not a prediction, until the ageing coefficients are fitted
    to your own cell.""",
     """Bu görev her gün tekrarlanırsa kapasite kaybı. Yaşlanma katsayıları kendi
    hücrenize uydurulana kadar bunu bir tahmin değil, duyarlılık çalışması olarak okuyun."""),
    # banners
    ("""<b>Cycle not achievable.</b> The vehicle fell short of
       the demanded speed for ${m.unmet_seconds.toFixed(0)} s. Consumption figures below
       describe a trace the bus did not actually follow.""",
     """<b>Görev takip edilemedi.</b> Araç istenen hıza ${m.unmet_seconds.toFixed(0)} s
       boyunca ulaşamadı. Aşağıdaki tüketim değerleri, otobüsün gerçekte izleyemediği bir
       hız profilini anlatıyor."""),
    ("""<b>${m.warnings.length} finding${m.warnings.length>1?"s":""}
       from this run.</b>""", """<b>Bu koşudan ${m.warnings.length} bulgu.</b>"""),
    # tiles
    ('["Consumption", ', '["Tüketim", '),
    ('`${fmt(k.mean_speed_kmh, 1)} km/h mean, ${fmt(k.stops_per_km, 2)} stops/km`',
     '`ortalama ${fmt(k.mean_speed_kmh, 1)} km/h, km başına ${fmt(k.stops_per_km, 2)} durak`'),
    ('["Range", ', '["Menzil", '),
    ('"on the usable SOC window"', '"kullanılabilir SOC aralığında"'),
    ('["Climate", ', '["Klima", '),
    ('`${fmt(k.hvac_kwh, 2)} kWh, cabin held ${fmt(k.mean_cabin_c, 1)} °C`',
     '`${fmt(k.hvac_kwh, 2)} kWh, kabin ortalaması ${fmt(k.mean_cabin_c, 1)} °C`'),
    ('["Regen recovered", ', '["Geri kazanım", '),
    ('`${Math.round(k.regen_fraction*100)}% of braking energy`', '`fren enerjisinin %${Math.round(k.regen_fraction*100)}`'),
    ('["Regen refused", ', '["Reddedilen rejenerasyon", '),
    ('"burned in the friction brakes"', '"sürtünmeli frenlerde ısıya döndü"'),
    ('["Pack reaches 80%", ', '["Paket %80 seviyesine", '),
    ('`${D.aging.assumed_daily_km} km/day` + (D.aging.calibrated ? "" : ", uncalibrated")',
     '`günde ${D.aging.assumed_daily_km} km` + (D.aging.calibrated ? "" : ", kalibre edilmemiş")'),
    ('"yr", `', '"yıl", `'),
    # subtitle
    ('km in ${fmt(k.duration_min, 1)} min ` +', 'km, ${fmt(k.duration_min, 1)} dk ` +'),
    ('kWh pack  ·  ${fmt(m.motor_kw, 0)} kW motor  ·  ${m.hvac_type} HVAC`',
     'kWh paket  ·  ${fmt(m.motor_kw, 0)} kW motor  ·  ${m.hvac_type} klima`'),
    # bus diagram
    ('.textContent = "TRACTION PACK";', '.textContent = "ÇEKİŞ BATARYASI";'),
    ('`TRACTION PACK  ·  ${fmt(soc*100,0)}%`', '`ÇEKİŞ BATARYASI  ·  %${fmt(soc*100,0)}`'),
    ('.textContent = "HVAC";', '.textContent = "KLİMA";'),
    ('.textContent = "AUX";', '.textContent = "YRD";'),
    ('.textContent = "drive axle";', '.textContent = "tahrik aksı";'),
    ('(regen ? "regen " : "traction ")', '(regen ? "rejenerasyon " : "çekiş ")'),
    ('"HVAC " + fmt(hv,1) + " kW"', '"klima " + fmt(hv,1) + " kW"'),
    ('"aux " + fmt(aux,1) + " kW"', '"yardımcı " + fmt(aux,1) + " kW"'),
    ('`cabin ${fmt(a.cabin_c[i],1)} °C  ·  `', '`kabin ${fmt(a.cabin_c[i],1)} °C  ·  `'),
    ('`${Math.round(a.passengers[i])} on board`', '`${Math.round(a.passengers[i])} yolcu`'),
    ('"  ·  doors open"', '"  ·  kapılar açık"'),
    ('["Distance", fmt(', '["Mesafe", fmt('),
    ('["Speed", fmt(', '["Hız", fmt('),
    ('["Battery", fmt(', '["Batarya", fmt('),
    ('["Cabin", fmt(', '["Kabin", fmt('),
    ('["Pack", fmt(', '["Paket", fmt('),
    ('`t = ${fmt(a.time_s[i],0)} s of ${fmt(a.time_s[a.time_s.length-1],0)} s`',
     '`t = ${fmt(a.time_s[i],0)} / ${fmt(a.time_s[a.time_s.length-1],0)} s`'),
    ('playing ? "Pause" : "Play"', 'playing ? "Duraklat" : "Oynat"'),
    # panels
    ('title:"Speed", unit:"km/h", series:[{k:"speed_kmh", c:"--s1", n:"Speed"}]',
     'title:"Hız", unit:"km/h", series:[{k:"speed_kmh", c:"--s1", n:"Hız"}]'),
    ('title:"Battery power", unit:"kW",', 'title:"Batarya gücü", unit:"kW",'),
    ('n:"Battery terminals"', 'n:"Batarya uçları"'),
    ('title:"State of charge", unit:"%",', 'title:"Şarj durumu", unit:"%",'),
    ('title:"Temperatures", unit:"°C",', 'title:"Sıcaklıklar", unit:"°C",'),
    ('{k:"cabin_c", c:"--s2", n:"Cabin"}, {k:"pack_c", c:"--s5", n:"Pack"}',
     '{k:"cabin_c", c:"--s2", n:"Kabin"}, {k:"pack_c", c:"--s5", n:"Paket"}'),
    ('"distance along the route (km)"', '"güzergâh boyunca mesafe (km)"'),
    ('["--s1","Speed", ', '["--s1","Hız", '),
    ('["--s1","Battery", ', '["--s1","Batarya", '),
    ('["--s2","Cabin", ', '["--s2","Kabin", '),
    ('["--s5","Pack", ', '["--s5","Paket", '),
    ('["--s4","Grade", ', '["--s4","Eğim", '),
    # balance
    ('></i>Energy spent</span>', '></i>Harcanan enerji</span>'),
    ('></i>Energy returned (net descent or deceleration)</span>', '></i>Geri dönen enerji (net iniş ya da yavaşlama)</span>'),
    ('<th>Sink</th><th>kWh</th><th>Share</th>', '<th>Kalem</th><th>kWh</th><th>Pay</th>'),
    ('<td>Energy drawn from cells</td>', '<td>Hücrelerden çekilen enerji</td>'),
    # cabin
    ('n:"Doors"}', 'n:"Kapılar"}'), ('n:"Envelope"}', 'n:"Gövde"}'),
    ('n:"Ventilation"}', 'n:"Havalandırma"}'), ('n:"Passengers"}', 'n:"Yolcular"}'),
    ('n:"Solar"}', 'n:"Güneş"}'), ('n:"HVAC delivered"}', 'n:"Klimanın verdiği"}'),
    ('"distance (km)  ·  heat flow into cabin (kW)"', '"mesafe (km)  ·  kabine giren ısı (kW)"'),
    # motor map
    ('></i>Motoring</span>', '></i>Motor</span>'),
    ('></i>Regenerating</span>', '></i>Rejenerasyon</span>'),
    ('></i>&le;80% efficient</span>', '></i>&le;%80 verim</span>'),
    ('></i>&ge;96% efficient</span>', '></i>&ge;%96 verim</span>'),
    ('></i>Peak / continuous envelope</span>', '></i>Tepe / sürekli tork eğrisi</span>'),
    ('"motor speed (rpm)  ·  torque (Nm)"', '"motor devri (rpm)  ·  tork (Nm)"'),
    ('"Map source: "', '"Harita kaynağı: "'),
    # ageing
    ('`end of life ${Math.round(A.eol_threshold*100)}%`', '`ömür sonu %${Math.round(A.eol_threshold*100)}`'),
    ('`year ${A.eol_year}`', '`${fmt(A.eol_year,1)}. yıl`'),
    ('`years in service · ${A.assumed_daily_km} km/day, ${A.operating_days_per_year} days/yr, `',
     '`hizmet yılı · günde ${A.assumed_daily_km} km, yılda ${A.operating_days_per_year} gün, `'),
    ('`depot ${A.assumed_storage_c}°C at ${Math.round(A.assumed_rest_soc*100)}% SOC`',
     '`depo ${A.assumed_storage_c}°C, %${Math.round(A.assumed_rest_soc*100)} SOC`'),
    ('"  —  UNCALIBRATED"', '"  —  KALİBRE EDİLMEMİŞ"'),
]

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="__LANG__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bus Duty Cycle Report</title>
<style>
:root {
  color-scheme: light;
  --surface-1:#fcfcfb; --plane:#f2f2ef; --ink:#0b0b0b; --ink-2:#52514e;
  --muted:#898781; --grid:#e1e0d9; --axis:#c3c2b7; --ring:rgba(11,11,11,0.10);
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100; --s5:#e87ba4; --s6:#008300;
  --good:#0ca30c; --warn:#fab219; --serious:#ec835a; --critical:#d03b3b;
  --body-fill:#eceae4; --body-stroke:#c3c2b7; --glass:#d7e3ef;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface-1:#1a1a19; --plane:#0d0d0d; --ink:#ffffff; --ink-2:#c3c2b7;
    --muted:#898781; --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,0.10);
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --s5:#d55181; --s6:#008300;
    --body-fill:#26262a; --body-stroke:#4a4a46; --glass:#2d3b48;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-1:#1a1a19; --plane:#0d0d0d; --ink:#ffffff; --ink-2:#c3c2b7;
  --muted:#898781; --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,0.10);
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --s5:#d55181; --s6:#008300;
  --body-fill:#26262a; --body-stroke:#4a4a46; --glass:#2d3b48;
}
* { box-sizing: border-box; }
body {
  margin:0; background:var(--plane); color:var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  font-size:14px; line-height:1.5;
}
.wrap { max-width:1280px; margin:0 auto; padding:24px 16px 64px; }
header { margin-bottom:20px; }
h1 { font-size:22px; margin:0 0 4px; letter-spacing:-0.01em; }
.sub { color:var(--ink-2); font-size:13px; }
.card {
  background:var(--surface-1); border:1px solid var(--ring); border-radius:10px;
  padding:18px 20px; margin-bottom:16px;
}
h2 { font-size:15px; margin:0 0 2px; letter-spacing:-0.005em; }
.note { color:var(--muted); font-size:12.5px; margin:0 0 14px; max-width:78ch; }
.banner {
  display:flex; gap:10px; align-items:flex-start; padding:11px 14px;
  border-radius:8px; margin-bottom:16px; font-size:13px;
  border:1px solid var(--ring);
}
.banner.warn { background:color-mix(in srgb, var(--warn) 12%, var(--surface-1)); }
.banner.bad  { background:color-mix(in srgb, var(--critical) 12%, var(--surface-1)); }
.banner b { font-weight:600; }
.banner ul { margin:6px 0 0; padding-left:18px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:1px;
  background:var(--ring); border:1px solid var(--ring); border-radius:10px; overflow:hidden; }
.tile { background:var(--surface-1); padding:14px 16px; }
.tile .k { font-size:11.5px; text-transform:uppercase; letter-spacing:0.04em; color:var(--muted); }
.tile .v { font-size:26px; font-weight:600; letter-spacing:-0.02em; margin-top:2px; }
.tile .u { font-size:13px; color:var(--ink-2); font-weight:400; margin-left:2px; }
.tile .d { font-size:12px; color:var(--muted); margin-top:1px; }
.legend { display:flex; flex-wrap:wrap; gap:6px 16px; margin:10px 0 4px; font-size:12.5px; color:var(--ink-2); }
.legend span { display:inline-flex; align-items:center; gap:6px; }
.swatch { width:10px; height:10px; border-radius:3px; flex:none; }
.controls { display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-top:12px; }
button {
  font:inherit; font-size:13px; padding:6px 14px; border-radius:7px;
  border:1px solid var(--ring); background:var(--surface-1); color:var(--ink); cursor:pointer;
}
button:hover { background:var(--plane); }
input[type=range] { flex:1; min-width:220px; accent-color:var(--s1); }
.readouts { display:grid; grid-template-columns:repeat(auto-fit,minmax(112px,1fr)); gap:1px;
  background:var(--ring); border:1px solid var(--ring); border-radius:8px; overflow:hidden; margin-top:14px; }
.readouts div { background:var(--surface-1); padding:9px 12px; }
.readouts .k { font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:0.04em; }
.readouts .v { font-size:17px; font-weight:600; font-variant-numeric:tabular-nums; }
svg { display:block; width:100%; height:auto; overflow:visible; }
table { border-collapse:collapse; width:100%; font-size:13px; font-variant-numeric:tabular-nums; }
th, td { text-align:right; padding:6px 10px; border-bottom:1px solid var(--grid); }
th:first-child, td:first-child { text-align:left; font-variant-numeric:normal; }
th { color:var(--muted); font-weight:500; font-size:12px; text-transform:uppercase; letter-spacing:0.04em; }
tfoot td { font-weight:600; border-bottom:none; border-top:1px solid var(--axis); }
details { margin-top:12px; }
summary { cursor:pointer; color:var(--ink-2); font-size:13px; }
.tip {
  position:fixed; pointer-events:none; opacity:0; transition:opacity .08s;
  background:var(--surface-1); border:1px solid var(--ring); border-radius:7px;
  padding:8px 11px; font-size:12.5px; box-shadow:0 4px 16px rgba(0,0,0,.14);
  z-index:50; font-variant-numeric:tabular-nums; max-width:260px;
}
.tip b { display:block; font-size:12px; color:var(--muted); font-weight:500; margin-bottom:3px; }
.tip i { display:inline-block; width:8px; height:8px; border-radius:2px; margin-right:6px; font-style:normal; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
@media (max-width: 900px) { .grid2 { grid-template-columns:1fr; } }
.flowline { stroke-linecap:round; fill:none; }
.flowline.anim { stroke-dasharray:9 11; animation:flow 1s linear infinite; }
@keyframes flow { to { stroke-dashoffset:-20; } }
.theme-toggle { float:right; }
</style>
</head>
<body>
<div class="wrap">
<header>
  <button class="theme-toggle" onclick="toggleTheme()">Toggle theme</button>
  <h1 id="title">Bus Duty Cycle Report</h1>
  <div class="sub" id="subtitle"></div>
</header>

<div id="banners"></div>

<div class="tiles" id="tiles"></div>

<div class="card" style="margin-top:16px">
  <h2>Energy flow through the vehicle</h2>
  <p class="note">Side view with live power flow. Line thickness is power; blue flows
  out of the pack, green flows back into it under regenerative braking. Scrub or play
  to walk through the duty cycle and watch the cabin temperature move when the doors open.</p>
  <div id="busdiagram"></div>
  <div class="readouts" id="readouts"></div>
  <div class="controls">
    <button id="playbtn" onclick="togglePlay()">Play</button>
    <input type="range" id="scrub" min="0" value="0" step="1">
    <span class="sub" id="scrubpos"></span>
  </div>
</div>

<div class="card">
  <h2>Along the route</h2>
  <p class="note">Four panels on a shared distance axis rather than one chart with
  stacked scales: speed, power at the battery terminals, state of charge, and the two
  temperatures that decide whether the numbers above hold up.</p>
  <div id="panels"></div>
</div>

<div class="grid2">
  <div class="card">
    <h2>Where the energy went</h2>
    <p class="note">A closed balance from the cells outward. The residual is shown
    rather than hidden: if it is not near zero, the model has a bookkeeping error
    and nothing else on this page should be trusted.</p>
    <div id="balance"></div>
    <details>
      <summary>Table view</summary>
      <div id="balancetable"></div>
    </details>
  </div>
  <div class="card">
    <h2>What the cabin load is made of</h2>
    <p class="note">The heat balance on the cabin, positive into the cabin. Door
    openings appear as spikes because gravity drives a real air exchange every time
    a bus stops -- the load a drive-cycle-only model cannot see.</p>
    <div id="cabin"></div>
  </div>
</div>

<div class="grid2">
  <div class="card">
    <h2>Motor operating points</h2>
    <p class="note">Where this duty cycle actually put the machine, on its own
    efficiency map. Clusters sitting in low-efficiency territory are the argument
    for changing the final drive ratio.</p>
    <div id="motormap"></div>
    <p class="note" id="mapsource" style="margin-top:8px"></p>
  </div>
  <div class="card">
    <h2>Projected pack life</h2>
    <p class="note">Capacity fade from this duty repeated daily. Read it as a
    sensitivity study, not a prediction, until the ageing coefficients are fitted
    to your own cell.</p>
    <div id="aging"></div>
  </div>
</div>

</div>
<div class="tip" id="tip"></div>

<script>
const D = /*__PAYLOAD__*/;
const NS = "http://www.w3.org/2000/svg";
const tip = document.getElementById("tip");

function css(n){ return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
function el(tag, attrs, parent){
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}
function svg(host, w, h){
  host.innerHTML = "";
  const s = el("svg", {viewBox:`0 0 ${w} ${h}`, preserveAspectRatio:"xMidYMid meet"}, host);
  return s;
}
function fmt(v, d){ return (Math.round(v*Math.pow(10,d))/Math.pow(10,d)).toFixed(d); }
function niceTicks(lo, hi, n){
  const span = hi - lo || 1;
  const raw = span / n;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
  const out = [];
  for (let v = Math.ceil(lo/step)*step; v <= hi + 1e-9; v += step) out.push(v);
  return out;
}
function tickDecimals(ticks){
  if (ticks.length < 2) return 0;
  const step = Math.abs(ticks[1] - ticks[0]);
  return step >= 1 ? 0 : step >= 0.1 ? 1 : 2;
}
/* Push right-edge direct labels apart so they never overprint. Keeps the
   original order, moves the minimum amount, and stays inside [top, bottom]. */
function spreadLabels(items, gap, top, bottom){
  items.sort((a,b) => a.y - b.y);
  for (let i = 1; i < items.length; i++)
    if (items[i].y - items[i-1].y < gap) items[i].y = items[i-1].y + gap;
  const over = items.length ? items[items.length-1].y - bottom : 0;
  if (over > 0) items.forEach(it => it.y -= over);
  for (let i = items.length - 2; i >= 0; i--)
    if (items[i+1].y - items[i].y < gap) items[i].y = items[i+1].y - gap;
  items.forEach(it => it.y = Math.max(it.y, top));
  return items;
}
function showTip(evt, html){
  tip.innerHTML = html;
  tip.style.opacity = 1;
  const r = tip.getBoundingClientRect();
  let x = evt.clientX + 14, y = evt.clientY + 14;
  if (x + r.width > innerWidth - 8) x = evt.clientX - r.width - 14;
  if (y + r.height > innerHeight - 8) y = evt.clientY - r.height - 14;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
function hideTip(){ tip.style.opacity = 0; }
function toggleTheme(){
  const cur = document.documentElement.getAttribute("data-theme");
  const dark = cur ? cur === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.setAttribute("data-theme", dark ? "light" : "dark");
  renderAll();
}

/* ---------------------------------------------------------------- header */
function renderHeader(){
  const m = D.meta, k = D.kpi;
  document.getElementById("title").textContent =
    m.vehicle + (m.variant ? "  —  " + m.variant : "");
  document.getElementById("subtitle").textContent =
    `${m.cycle}  ·  ${m.environment.replace(/ degC/g, "°C")}  ·  ${fmt(k.distance_km, 2)} km in ${fmt(k.duration_min, 1)} min ` +
    `·  ${fmt(m.pack_kwh, 0)} kWh pack  ·  ${fmt(m.motor_kw, 0)} kW motor  ·  ${m.hvac_type} HVAC`;

  const b = document.getElementById("banners");
  b.innerHTML = "";
  if (!m.achievable){
    b.insertAdjacentHTML("beforeend",
      `<div class="banner bad"><b>Cycle not achievable.</b> The vehicle fell short of
       the demanded speed for ${m.unmet_seconds.toFixed(0)} s. Consumption figures below
       describe a trace the bus did not actually follow.</div>`);
  }
  if (m.warnings.length){
    b.insertAdjacentHTML("beforeend",
      `<div class="banner warn"><div><b>${m.warnings.length} finding${m.warnings.length>1?"s":""}
       from this run.</b><ul>` + m.warnings.map(w=>`<li>${w}</li>`).join("") + `</ul></div></div>`);
  }
}

function renderTiles(){
  const k = D.kpi;
  const tiles = [
    ["Consumption", fmt(k.energy_per_km, 3), "kWh/km", `${fmt(k.mean_speed_kmh, 1)} km/h mean, ${fmt(k.stops_per_km, 2)} stops/km`],
    ["Range", fmt(k.range_km, 1), "km", "on the usable SOC window"],
    ["Climate", Math.round(k.hvac_share*100), "%", `${fmt(k.hvac_kwh, 2)} kWh, cabin held ${fmt(k.mean_cabin_c, 1)} °C`],
    ["Regen recovered", fmt(k.regen_recovered_kwh, 2), "kWh", `${Math.round(k.regen_fraction*100)}% of braking energy`],
    ["Regen refused", fmt(k.regen_lost_kwh, 2), "kWh", "burned in the friction brakes"],
    ["Pack reaches 80%", k.eol_year ? fmt(k.eol_year, 1) : "> " + Math.round(Math.max(...D.aging.years)),
     "yr", `${D.aging.assumed_daily_km} km/day` + (D.aging.calibrated ? "" : ", uncalibrated")],
  ];
  document.getElementById("tiles").innerHTML = tiles.map(([kk,v,u,d]) =>
    `<div class="tile"><div class="k">${kk}</div><div class="v">${v}<span class="u">${u}</span></div>
     <div class="d">${d}</div></div>`).join("");
}

/* ------------------------------------------------------------ bus diagram */
let frame = 0, playing = false, timer = null;

function renderBus(){
  const host = document.getElementById("busdiagram");
  const W = 1200, H = 330;
  const s = svg(host, W, H);
  const ink = css("--ink"), ink2 = css("--ink-2"), muted = css("--muted");
  const bodyFill = css("--body-fill"), bodyStroke = css("--body-stroke"), glass = css("--glass");

  // Geometry of a 12 m low-floor city bus in side view, front to the left.
  // Front axle at x=218, rear axle at x=992, floor at y=262, roof at y=92.
  el("line", {x1:20, y1:306, x2:W-20, y2:306, stroke:css("--axis"), "stroke-width":2}, s);

  // body shell
  el("path", {d:"M 58 262 L 58 108 Q 58 92 74 92 L 1128 92 Q 1144 92 1144 108 L 1144 262 Z",
    fill:bodyFill, stroke:bodyStroke, "stroke-width":2, "stroke-linejoin":"round"}, s);

  // Window band stops short of the rear tower, which houses the HV junction
  // box and carries the cable run down to the drive axle.
  el("rect", {x:70, y:114, width:1000, height:64, rx:6, fill:glass,
    stroke:bodyStroke, "stroke-width":1.5}, s);
  for (const x of [252, 434, 616, 798, 950]) {
    el("line", {x1:x, y1:114, x2:x, y2:178, stroke:bodyStroke, "stroke-width":1.5}, s);
  }
  el("rect", {x:1076, y:114, width:58, height:140, rx:4, fill:"none",
    stroke:bodyStroke, "stroke-width":1.2, "stroke-dasharray":"3 3"}, s);

  // Doors: front ahead of the front axle, centre mid-wheelbase, rear ahead
  // of the drive axle -- the standard three-door city layout.
  const doorsX = [76, 520, 812];
  doorsX.forEach((x, i) => {
    el("rect", {x:x, y:180, width:84, height:82, rx:3, fill:"none",
      stroke:bodyStroke, "stroke-width":2, id:"door"+i}, s);
    el("line", {x1:x+42, y1:182, x2:x+42, y2:260, stroke:bodyStroke,
      "stroke-width":1.5, id:"doorsplit"+i}, s);
  });

  // wheels
  for (const cx of [218, 992]) {
    el("circle", {cx:cx, cy:262, r:44, fill:bodyFill, stroke:bodyStroke, "stroke-width":7}, s);
    el("circle", {cx:cx, cy:262, r:18, fill:"none", stroke:bodyStroke, "stroke-width":3}, s);
  }

  // roof equipment
  el("rect", {x:230, y:60, width:420, height:32, rx:5, fill:css("--s1"),
    "fill-opacity":0.14, stroke:css("--s1"), "stroke-width":2}, s);
  el("rect", {x:234, y:64, width:0, height:24, rx:3, fill:css("--s1"),
    "fill-opacity":0.45, id:"socfill"}, s);
  el("text", {x:440, y:81, "text-anchor":"middle", "font-size":13.5, fill:ink,
    "font-weight":600, id:"packlabel"}, s).textContent = "TRACTION PACK";

  el("rect", {x:720, y:58, width:260, height:34, rx:5, fill:css("--s2"),
    "fill-opacity":0.16, stroke:css("--s2"), "stroke-width":2}, s);
  el("text", {x:850, y:80, "text-anchor":"middle", "font-size":13.5, fill:ink,
    "font-weight":600}, s).textContent = "HVAC";

  // auxiliaries: under-floor between the front axle and the centre door
  el("rect", {x:300, y:206, width:84, height:40, rx:5, fill:css("--s3"),
    "fill-opacity":0.16, stroke:css("--s3"), "stroke-width":2}, s);
  el("text", {x:342, y:231, "text-anchor":"middle", "font-size":12, fill:ink,
    "font-weight":600}, s).textContent = "AUX";

  // ---- power flows ------------------------------------------------------
  // pack -> HVAC along the roof
  el("path", {d:"M 650 76 L 720 76", class:"flowline", id:"flow_hvac",
    stroke:css("--s2"), "stroke-width":3}, s);
  // pack -> motor: into the ceiling void, aft to the rear tower, down to the axle
  el("path", {d:"M 600 92 L 600 103 L 1092 103 Q 1104 103 1104 115 L 1104 250 "
             + "Q 1104 262 1092 262 L 1022 262",
    class:"flowline", id:"flow_motor", stroke:css("--s1"), "stroke-width":3}, s);
  // pack -> aux: down the first window pillar
  el("path", {d:"M 252 92 L 252 196 Q 252 206 262 212 L 300 222",
    class:"flowline", id:"flow_aux", stroke:css("--s3"), "stroke-width":3}, s);

  // motor on the drive axle
  el("circle", {cx:992, cy:262, r:28, fill:css("--surface-1"), stroke:css("--s1"),
    "stroke-width":3, id:"motorbox"}, s);
  el("text", {x:992, y:267, "text-anchor":"middle", "font-size":13, fill:ink,
    "font-weight":700}, s).textContent = "M";

  // labels, each in clear space next to what it describes
  el("text", {x:685, y:50, "text-anchor":"middle", "font-size":12, fill:ink2,
    id:"lbl_hvac"}, s).textContent = "";
  el("text", {x:1140, y:50, "text-anchor":"end", "font-size":12, fill:ink2,
    "font-weight":600, id:"lbl_motor"}, s).textContent = "";
  el("text", {x:392, y:200, "font-size":12, fill:ink2, id:"lbl_aux"}, s).textContent = "";
  el("text", {x:992, y:324, "text-anchor":"middle", "font-size":11.5, fill:muted}, s)
    .textContent = "drive axle";
  el("text", {x:570, y:152, "text-anchor":"middle", "font-size":13.5, fill:ink,
    id:"cabinlabel", "font-weight":600}, s).textContent = "";

  document.getElementById("scrub").max = D.animation.distance_km.length - 1;
  setFrame(frame);
}

function setFrame(i){
  const a = D.animation;
  i = Math.max(0, Math.min(i, a.distance_km.length - 1));
  frame = i;
  document.getElementById("scrub").value = i;

  const soc = a.soc[i], batt = a.battery_kw[i], mot = a.motor_kw[i];
  const hv = a.hvac_kw[i], aux = a.aux_kw[i], doors = a.doors[i] > 0.5;

  const socfill = document.getElementById("socfill");
  if (socfill) socfill.setAttribute("width", (412 * Math.max(soc,0)).toFixed(1));
  const pl = document.getElementById("packlabel");
  if (pl) pl.textContent = `TRACTION PACK  ·  ${fmt(soc*100,0)}%`;

  const wid = p => Math.max(1.6, Math.min(13, Math.sqrt(Math.abs(p)) * 1.05));
  const regen = mot < -0.5;
  const setFlow = (id, p, color, label, labelId) => {
    const e = document.getElementById(id);
    if (!e) return;
    e.setAttribute("stroke-width", wid(p));
    e.setAttribute("stroke", color);
    e.setAttribute("opacity", Math.abs(p) > 0.4 ? 0.95 : 0.16);
    e.classList.toggle("anim", Math.abs(p) > 0.4);
    e.style.animationDirection = p < 0 ? "reverse" : "normal";
    const l = document.getElementById(labelId);
    if (l) l.textContent = label;
  };
  setFlow("flow_motor", mot, regen ? css("--s6") : css("--s1"),
          (regen ? "regen " : "traction ") + fmt(Math.abs(mot),0) + " kW", "lbl_motor");
  setFlow("flow_hvac", hv, css("--s2"), "HVAC " + fmt(hv,1) + " kW", "lbl_hvac");
  setFlow("flow_aux", aux, css("--s3"), "aux " + fmt(aux,1) + " kW", "lbl_aux");

  for (let d = 0; d < 3; d++){
    const r = document.getElementById("door"+d), sp = document.getElementById("doorsplit"+d);
    if (!r) continue;
    r.setAttribute("stroke", doors ? css("--s4") : css("--body-stroke"));
    r.setAttribute("stroke-width", doors ? 3 : 2);
    r.setAttribute("fill", doors ? css("--s4") : "none");
    r.setAttribute("fill-opacity", doors ? 0.22 : 0);
    if (sp) sp.setAttribute("opacity", doors ? 0.25 : 1);
  }
  const cl = document.getElementById("cabinlabel");
  if (cl) cl.textContent = `cabin ${fmt(a.cabin_c[i],1)} °C  ·  `
    + `${Math.round(a.passengers[i])} on board` + (doors ? "  ·  doors open" : "");

  document.getElementById("readouts").innerHTML = [
    ["Distance", fmt(a.distance_km[i],2) + " km"],
    ["Speed", fmt(a.speed_kmh[i],0) + " km/h"],
    ["Battery", fmt(batt,1) + " kW"],
    ["SOC", fmt(soc*100,1) + " %"],
    ["Cabin", fmt(a.cabin_c[i],1) + " °C"],
    ["Pack", fmt(a.pack_c[i],1) + " °C"],
  ].map(([k,v]) => `<div><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");

  document.getElementById("scrubpos").textContent =
    `t = ${fmt(a.time_s[i],0)} s of ${fmt(a.time_s[a.time_s.length-1],0)} s`;
}

function togglePlay(){
  playing = !playing;
  document.getElementById("playbtn").textContent = playing ? "Pause" : "Play";
  if (playing){
    timer = setInterval(() => {
      let n = frame + 1;
      if (n >= D.animation.distance_km.length) n = 0;
      setFrame(n);
    }, 45);
  } else clearInterval(timer);
}
document.getElementById("scrub").addEventListener("input", e => {
  if (playing) togglePlay();
  setFrame(+e.target.value);
});

/* ------------------------------------------------------- stacked panels */
function renderPanels(){
  const host = document.getElementById("panels");
  const S = D.series, x = S.distance_km;
  const panels = [
    {h:104, title:"Speed", unit:"km/h", series:[{k:"speed_kmh", c:"--s1", n:"Speed"}], zero:true},
    {h:118, title:"Battery power", unit:"kW",
     series:[{k:"battery_kw", c:"--s1", n:"Battery terminals"}], zero:true, signed:true},
    {h:104, title:"State of charge", unit:"%",
     series:[{k:"soc", c:"--s6", n:"SOC", scale:100}]},
    {h:118, title:"Temperatures", unit:"°C",
     series:[{k:"cabin_c", c:"--s2", n:"Cabin"}, {k:"pack_c", c:"--s5", n:"Pack"}]},
  ];
  const W = 1180, L = 58, R = 16, GAP = 26;
  const totalH = panels.reduce((a,p)=>a+p.h+GAP, 0) + 26;
  const s = svg(host, W, totalH);
  const xmin = 0, xmax = Math.max(...x);
  const sx = v => L + (v - xmin) / (xmax - xmin || 1) * (W - L - R);

  let y0 = 8;
  const hitzones = [];
  panels.forEach(p => {
    const vals = p.series.flatMap(ss => S[ss.k].map(v => v * (ss.scale || 1)));
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (p.zero) lo = Math.min(lo, 0);
    const pad = (hi - lo) * 0.10 || 1;
    lo -= pad; hi += pad;
    const sy = v => y0 + p.h - (v - lo) / (hi - lo) * p.h;

    el("text", {x:L, y:y0 - 4, "font-size":12, fill:css("--ink-2"),
      "font-weight":600}, s).textContent = p.title;
    el("text", {x:L + 6 + p.title.length * 6.6, y:y0 - 4, "font-size":11.5,
      fill:css("--muted")}, s).textContent = p.unit;

    const pticks = niceTicks(lo, hi, 3), pdec = tickDecimals(pticks);
    pticks.forEach(t => {
      const yy = sy(t);
      if (yy < y0 - 1 || yy > y0 + p.h + 1) return;
      el("line", {x1:L, y1:yy, x2:W-R, y2:yy, stroke:css("--grid"),
        "stroke-width":1, "shape-rendering":"crispEdges"}, s);
      el("text", {x:L-8, y:yy+4, "text-anchor":"end", "font-size":11,
        fill:css("--muted")}, s).textContent = fmt(t, pdec);
    });
    if (p.signed && lo < 0 && hi > 0){
      el("line", {x1:L, y1:sy(0), x2:W-R, y2:sy(0), stroke:css("--axis"),
        "stroke-width":1.5}, s);
    }

    p.series.forEach(ss => {
      const d = S[ss.k].map((v,i) => `${i?"L":"M"}${sx(x[i]).toFixed(1)} ${sy(v*(ss.scale||1)).toFixed(1)}`).join(" ");
      el("path", {d:d, fill:"none", stroke:css(ss.c), "stroke-width":2,
        "stroke-linejoin":"round", "stroke-linecap":"round"}, s);
      // direct label at the right edge
      const last = S[ss.k][S[ss.k].length-1] * (ss.scale || 1);
      if (p.series.length > 1){
        el("text", {x:W-R+4, y:sy(last)+4, "font-size":11, fill:css("--ink-2")},
          s).textContent = ss.n;
      }
    });
    hitzones.push({y0:y0, h:p.h, p:p, sy:sy});
    y0 += p.h + GAP;
  });

  // shared x axis
  const ay = y0 - GAP + 14;
  el("line", {x1:L, y1:ay, x2:W-R, y2:ay, stroke:css("--axis"), "stroke-width":1.5}, s);
  const xticks = niceTicks(xmin, xmax, 8), xdec = tickDecimals(xticks);
  xticks.forEach(t => {
    el("text", {x:sx(t), y:ay+16, "text-anchor":"middle", "font-size":11,
      fill:css("--muted")}, s).textContent = fmt(t, xdec);
  });
  el("text", {x:(L+W-R)/2, y:ay+32, "text-anchor":"middle", "font-size":11.5,
    fill:css("--ink-2")}, s).textContent = "distance along the route (km)";

  // crosshair + tooltip across all panels
  const cross = el("line", {x1:0,y1:8,x2:0,y2:ay, stroke:css("--axis"),
    "stroke-width":1, opacity:0}, s);
  const hit = el("rect", {x:L, y:0, width:W-L-R, height:ay, fill:"transparent"}, s);
  hit.addEventListener("mousemove", ev => {
    const box = s.getBoundingClientRect();
    const px = (ev.clientX - box.left) / box.width * W;
    const frac = (px - L) / (W - L - R);
    const i = Math.max(0, Math.min(x.length-1, Math.round(frac * (x.length-1))));
    cross.setAttribute("x1", sx(x[i])); cross.setAttribute("x2", sx(x[i]));
    cross.setAttribute("opacity", 1);
    const rows = [
      ["--s1","Speed", fmt(S.speed_kmh[i],1)+" km/h"],
      ["--s1","Battery", fmt(S.battery_kw[i],1)+" kW"],
      ["--s6","SOC", fmt(S.soc[i]*100,1)+" %"],
      ["--s2","Cabin", fmt(S.cabin_c[i],1)+" °C"],
      ["--s5","Pack", fmt(S.pack_c[i],1)+" °C"],
      ["--s4","Grade", fmt(S.grade_pct[i],1)+" %"],
    ].map(([c,k,v]) => `<div><i style="background:${css(c)}"></i>${k} <b style="display:inline;float:right;margin-left:14px;color:var(--ink)">${v}</b></div>`).join("");
    showTip(ev, `<b>${fmt(x[i],2)} km · ${fmt(S.time_s[i],0)} s</b>${rows}`);
    setFrame(Math.round(i / (x.length-1) * (D.animation.distance_km.length-1)));
  });
  hit.addEventListener("mouseleave", () => { cross.setAttribute("opacity",0); hideTip(); });
}

/* ------------------------------------------------------- energy balance */
function renderBalance(){
  const host = document.getElementById("balance");
  const rows = D.balance.filter(r => Math.abs(r.kwh) > 0.0005);
  const W = 560, rowH = 26, L = 186, R = 56;
  const H = rows.length * rowH + 16;
  const s = svg(host, W, H);
  // True zero baseline: negative entries (net descent, net deceleration,
  // undelivered demand) extend left, so nothing returned reads as a loss.
  const lo = Math.min(0, ...rows.map(r => r.kwh));
  const hi = Math.max(0, ...rows.map(r => r.kwh));
  const span = (hi - lo) || 1;
  const sx = v => (L + 40 * (lo < 0)) + (v - lo) / span * (W - L - R - 40 * (lo < 0));
  const x0 = sx(0);
  rows.forEach((r, i) => {
    const y = 8 + i * rowH;
    const isResid = r.resid || r.label.startsWith("residual");
    const neg = r.kwh < 0;
    const x1 = sx(r.kwh);
    el("text", {x:L-10, y:y+15, "text-anchor":"end", "font-size":12,
      fill:isResid ? css("--muted") : css("--ink-2")}, s).textContent = r.label;
    el("rect", {x:Math.min(x0, x1), y:y+4, width:Math.max(Math.abs(x1 - x0), 1.5),
      height:15, rx:4,
      fill: isResid ? css("--muted") : neg ? css("--s6") : css("--s1"),
      opacity: isResid ? 0.45 : 0.92}, s);
    el("text", {x: neg ? Math.max(x1 - 6, 4) : x1 + 8, y:y+16, "font-size":12,
      "text-anchor": neg ? "end" : "start",
      fill:css("--ink"), "font-variant-numeric":"tabular-nums"},
      s).textContent = fmt(r.kwh, 2);
  });
  el("line", {x1:x0, y1:4, x2:x0, y2:H-4, stroke:css("--axis"), "stroke-width":1.5}, s);
  host.insertAdjacentHTML("beforeend", `<div class="legend">
    <span><i class="swatch" style="background:${css('--s1')}"></i>Energy spent</span>
    <span><i class="swatch" style="background:${css('--s6')}"></i>Energy returned (net descent or deceleration)</span>
  </div>`);
  document.getElementById("balancetable").innerHTML =
    `<table><thead><tr><th>Sink</th><th>kWh</th><th>Share</th></tr></thead><tbody>` +
    rows.map(r => `<tr><td>${r.label}</td><td>${fmt(r.kwh,3)}</td>
      <td>${fmt(100*r.kwh/D.cell_energy_kwh,1)}%</td></tr>`).join("") +
    `</tbody><tfoot><tr><td>Energy drawn from cells</td>
      <td>${fmt(D.cell_energy_kwh,3)}</td><td>100.0%</td></tr></tfoot></table>`;
}

/* ---------------------------------------------------------- cabin loads */
function renderCabin(){
  const host = document.getElementById("cabin");
  const S = D.series, x = S.distance_km;
  const keys = [
    {k:"q_doors_kw",      c:"--s2", n:"Doors"},
    {k:"q_envelope_kw",   c:"--s1", n:"Envelope"},
    {k:"q_ventilation_kw",c:"--s3", n:"Ventilation"},
    {k:"q_passengers_kw", c:"--s4", n:"Passengers"},
    {k:"q_solar_kw",      c:"--s5", n:"Solar"},
    {k:"q_hvac_kw",       c:"--s6", n:"HVAC delivered"},
  ];
  const W = 560, H = 240, L = 46, R = 78, T = 10, B = 40;
  const s = svg(host, W, H);
  const all = keys.flatMap(kk => S[kk.k]);
  let lo = Math.min(...all, 0), hi = Math.max(...all, 0);
  const pad = (hi-lo)*0.08 || 1; lo -= pad; hi += pad;
  const sx = v => L + v / (Math.max(...x)||1) * (W-L-R);
  const sy = v => T + (hi - v) / (hi - lo) * (H-T-B);

  const cticks = niceTicks(lo, hi, 4), cdec = tickDecimals(cticks);
  cticks.forEach(t => {
    el("line", {x1:L, y1:sy(t), x2:W-R, y2:sy(t), stroke:css("--grid"),
      "stroke-width":1, "shape-rendering":"crispEdges"}, s);
    el("text", {x:L-7, y:sy(t)+4, "text-anchor":"end", "font-size":11,
      fill:css("--muted")}, s).textContent = fmt(t, cdec);
  });
  el("line", {x1:L, y1:sy(0), x2:W-R, y2:sy(0), stroke:css("--axis"), "stroke-width":1.5}, s);

  const cabinLabels = [];
  keys.forEach(kk => {
    const d = S[kk.k].map((v,i)=>`${i?"L":"M"}${sx(x[i]).toFixed(1)} ${sy(v).toFixed(1)}`).join(" ");
    el("path", {d:d, fill:"none", stroke:css(kk.c), "stroke-width":1.8,
      "stroke-linejoin":"round", opacity:0.95}, s);
    // Label at the trailing mean rather than the last sample, so a label is
    // not parked on a door spike that happened to fall at the final instant.
    const tail = S[kk.k].slice(-Math.max(10, Math.floor(S[kk.k].length/20)));
    const mean = tail.reduce((a,b)=>a+b,0) / tail.length;
    cabinLabels.push({y: sy(mean) + 4, n: kk.n, c: kk.c});
  });
  spreadLabels(cabinLabels, 13, T + 8, H - B).forEach(l => {
    el("text", {x:W-R+5, y:l.y, "font-size":11, fill:css("--ink-2")}, s).textContent = l.n;
  });
  el("text", {x:(L+W-R)/2, y:H-8, "text-anchor":"middle", "font-size":11.5,
    fill:css("--ink-2")}, s).textContent = "distance (km)  ·  heat flow into cabin (kW)";

  host.insertAdjacentHTML("beforeend", `<div class="legend">` +
    keys.map(kk=>`<span><i class="swatch" style="background:${css(kk.c)}"></i>${kk.n}</span>`).join("") +
    `</div>`);
}

/* ------------------------------------------------------------ motor map */
function renderMotorMap(){
  const host = document.getElementById("motormap");
  const M = D.motor_map, O = D.operating_points, E = D.envelope;
  const W = 560, H = 300, L = 54, R = 66, T = 10, B = 42;
  const s = svg(host, W, H);
  const xmax = Math.max(...M.speed_rpm), ymax = Math.max(...M.torque_nm, ...E.peak);
  const sx = v => L + v / xmax * (W-L-R);
  const sy = v => T + (1 - v / ymax) * (H-T-B);

  // efficiency field as a sequential blue ramp
  // Lighter half of the sequential ramp so the operating-point dots on top
  // stay legible; the scale spans 80-96 %, where the useful contrast is.
  const ramp = ["#e6f0fc","#cde2fb","#b7d3f6","#9ec5f4","#86b6ef","#6da7ec","#5598e7"];
  const EFF_LO = 0.80, EFF_HI = 0.96;
  const nx = M.speed_rpm.length, ny = M.torque_nm.length;
  const peakAt = rpm => {
    for (let k = 1; k < E.rpm.length; k++) if (E.rpm[k] >= rpm) {
      const f = (rpm - E.rpm[k-1]) / (E.rpm[k] - E.rpm[k-1] || 1);
      return E.peak[k-1] + f * (E.peak[k] - E.peak[k-1]);
    }
    return E.peak[E.peak.length-1];
  };
  for (let j = 0; j < ny-1; j++){
    for (let i = 0; i < nx-1; i++){
      const e = M.efficiency[j][i];
      if (e <= 0.01) continue;
      // Do not shade torque the machine cannot produce at this speed.
      if (M.torque_nm[j] > peakAt(0.5*(M.speed_rpm[i] + M.speed_rpm[i+1]))) continue;
      const f = Math.max(0, Math.min(1, (e - EFF_LO) / (EFF_HI - EFF_LO)));
      el("rect", {x:sx(M.speed_rpm[i]), y:sy(M.torque_nm[j+1]),
        width:Math.abs(sx(M.speed_rpm[i+1])-sx(M.speed_rpm[i]))+0.6,
        height:Math.abs(sy(M.torque_nm[j])-sy(M.torque_nm[j+1]))+0.6,
        fill:ramp[Math.round(f*(ramp.length-1))], opacity:0.85}, s);
    }
  }
  // envelopes
  const path = (arr) => arr.map((t,i)=>`${i?"L":"M"}${sx(E.rpm[i]).toFixed(1)} ${sy(t).toFixed(1)}`).join(" ");
  el("path", {d:path(E.peak), fill:"none", stroke:css("--ink"), "stroke-width":2}, s);
  el("path", {d:path(E.continuous), fill:"none", stroke:css("--ink"),
    "stroke-width":2, "stroke-dasharray":"6 5", opacity:0.7}, s);

  // operating points
  O.rpm.forEach((r,i) => {
    const tq = O.torque[i];
    if (Math.abs(tq) < 5 && r < 5) return;
    el("circle", {cx:sx(r), cy:sy(Math.abs(tq)), r:2.4,
      fill: tq < 0 ? css("--s6") : css("--s2"), opacity:0.75,
      stroke:css("--surface-1"), "stroke-width":0.6}, s);
  });

  niceTicks(0, xmax, 5).forEach(t => {
    el("text", {x:sx(t), y:H-B+16, "text-anchor":"middle", "font-size":11,
      fill:css("--muted")}, s).textContent = fmt(t,0);
  });
  niceTicks(0, ymax, 4).forEach(t => {
    el("text", {x:L-7, y:sy(t)+4, "text-anchor":"end", "font-size":11,
      fill:css("--muted")}, s).textContent = fmt(t,0);
  });
  el("text", {x:(L+W-R)/2, y:H-6, "text-anchor":"middle", "font-size":11.5,
    fill:css("--ink-2")}, s).textContent = "motor speed (rpm)  ·  torque (Nm)";

  host.insertAdjacentHTML("beforeend", `<div class="legend">
    <span><i class="swatch" style="background:${css('--s2')}"></i>Motoring</span>
    <span><i class="swatch" style="background:${css('--s6')}"></i>Regenerating</span>
    <span><i class="swatch" style="background:${ramp[1]}"></i>&le;80% efficient</span>
    <span><i class="swatch" style="background:${ramp[6]}"></i>&ge;96% efficient</span>
    <span><i class="swatch" style="background:${css('--ink')}"></i>Peak / continuous envelope</span>
  </div>`);
  document.getElementById("mapsource").textContent = "Map source: " + M.source;
}

/* ---------------------------------------------------------------- ageing */
function renderAging(){
  const host = document.getElementById("aging");
  const A = D.aging;
  const W = 560, H = 300, L = 50, R = 24, T = 12, B = 44;
  const s = svg(host, W, H);
  const xmax = Math.max(...A.years);
  const lo = Math.min(0.70, Math.min(...A.soh) - 0.02), hi = 1.005;
  const sx = v => L + v / xmax * (W-L-R);
  const sy = v => T + (hi - v)/(hi - lo) * (H-T-B);

  niceTicks(lo, hi, 4).forEach(t => {
    el("line", {x1:L, y1:sy(t), x2:W-R, y2:sy(t), stroke:css("--grid"),
      "stroke-width":1, "shape-rendering":"crispEdges"}, s);
    el("text", {x:L-7, y:sy(t)+4, "text-anchor":"end", "font-size":11,
      fill:css("--muted")}, s).textContent = fmt(t*100,0)+"%";
  });
  // end-of-life threshold
  el("line", {x1:L, y1:sy(A.eol_threshold), x2:W-R, y2:sy(A.eol_threshold),
    stroke:css("--critical"), "stroke-width":1.6, "stroke-dasharray":"5 4"}, s);
  el("text", {x:W-R-4, y:sy(A.eol_threshold)-6, "text-anchor":"end", "font-size":11,
    fill:css("--critical")}, s).textContent = `end of life ${Math.round(A.eol_threshold*100)}%`;

  const d = A.soh.map((v,i)=>`${i?"L":"M"}${sx(A.years[i]).toFixed(1)} ${sy(v).toFixed(1)}`).join(" ");
  el("path", {d:d, fill:"none", stroke:css("--s1"), "stroke-width":2.4,
    "stroke-linejoin":"round"}, s);

  if (A.eol_year){
    el("line", {x1:sx(A.eol_year), y1:sy(A.eol_threshold), x2:sx(A.eol_year), y2:H-B,
      stroke:css("--critical"), "stroke-width":1.2, "stroke-dasharray":"3 3"}, s);
    el("circle", {cx:sx(A.eol_year), cy:sy(A.eol_threshold), r:5,
      fill:css("--critical"), stroke:css("--surface-1"), "stroke-width":2}, s);
    el("text", {x:sx(A.eol_year), y:H-B+30, "text-anchor":"middle", "font-size":12,
      fill:css("--ink"), "font-weight":600}, s).textContent = `year ${A.eol_year}`;
  }
  niceTicks(0, xmax, 6).forEach(t => {
    el("text", {x:sx(t), y:H-B+16, "text-anchor":"middle", "font-size":11,
      fill:css("--muted")}, s).textContent = fmt(t,0);
  });
  el("text", {x:(L+W-R)/2, y:H-4, "text-anchor":"middle", "font-size":11.5,
    fill:css("--ink-2")}, s).textContent =
    `years in service · ${A.assumed_daily_km} km/day, ${A.operating_days_per_year} days/yr, ` +
    `depot ${A.assumed_storage_c}°C at ${Math.round(A.assumed_rest_soc*100)}% SOC` +
    (A.calibrated ? "" : "  —  UNCALIBRATED");
}

function renderAll(){
  renderHeader(); renderTiles(); renderBus(); renderPanels();
  renderBalance(); renderCabin(); renderMotorMap(); renderAging();
}
renderAll();
{
  const m = /frame=(\d+)/.exec(location.hash);
  if (m) setFrame(+m[1]);
}
addEventListener("resize", () => { renderBus(); });
</script>
</body>
</html>
"""
