"""Upload templates: one sample file per data type the interface accepts.

A supplier, a test engineer or a colleague who wants to hand data to the
simulator should not have to reverse-engineer a format from the library. Every
template here is a small, valid file in exactly the layout the matching
importer (or the library loader) reads, with synthetic numbers that are
obviously not measurements. The web page "Veri biçimleri" lists them with the
column meanings and the page each one is uploaded on.

Two families:

* ``importer`` templates are what *Veri yükle* reads: the importer detects
  separators, decimal commas and units itself, so these are offered as .xlsx
  (opens cleanly in a Turkish-locale Excel) or as GPX/YAML.
* ``library`` templates are the solver's own file formats (cycle CSV, VECTO
  style engine maps, test logs): comma separated, decimal point, copied into
  the library folder by hand or through a package.
"""

from __future__ import annotations

import io
import pathlib
import zipfile

import numpy as np
import yaml

from .routes import template as route_template
from .scenarios import Scenario
from .schema import COMPONENT_KINDS, KIND_LABELS_TR, schema, to_yaml_dict
from .vehicle import SLOTS_BY_POWERTRAIN

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
GROUPS = [("guzergah", "Güzergâh ve çevrim"), ("harita", "Harita ve eğri"), ("arac", "Araç ve bileşen"),
          ("senaryo", "Senaryo ve hat"), ("test", "Test kaydı")]

# key, group, label, filename, kind (importer|library), upload page, description, columns [(name, meaning)], notes
_CATALOGUE = [
    dict(key="route_table", group="guzergah", label="Güzergâh tablosu (mesafe–rakım)", filename="guzergah_tablo_sablonu.xlsx",
         family="importer", upload="#/yukle/guzergah", upload_label="Veri yükle → Güzergâh",
         description="Yol boyunca mesafe ve rakım; isteğe bağlı hız sınırı ve durak işareti. Satır aralığı serbesttir, 10 m ızgaraya oturtulur.",
         columns=[("distance_m", "yol boyunca mesafe, m (km yazılırsa algılanır)"), ("elevation_m", "rakım, m"),
                  ("speed_limit_kmh", "hız sınırı, km/h (yoksa form değeri)"), ("stop", "1 = bu noktada durak")],
         notes=["Başlıklar Türkçe de olabilir: mesafe, rakım, hız limiti, durak.", "Ondalık virgül ve noktalı virgül kabul edilir."]),
    dict(key="route_latlon", group="guzergah", label="Güzergâh tablosu (enlem–boylam)", filename="guzergah_enlem_boylam_sablonu.xlsx",
         family="importer", upload="#/yukle/guzergah", upload_label="Veri yükle → Güzergâh",
         description="GPS noktaları; mesafe büyük daire formülüyle hesaplanır.",
         columns=[("lat", "enlem, derece"), ("lon", "boylam, derece"), ("elevation_m", "rakım, m"), ("stop", "1 = durak")],
         notes=["Rakım yoksa güzergâh düz kabul edilir ve uyarı verilir."]),
    dict(key="route_gpx", group="guzergah", label="GPX izi", filename="guzergah_sablonu.gpx",
         family="importer", upload="#/yukle/guzergah", upload_label="Veri yükle → Güzergâh",
         description="GPS cihazı, Google Earth ya da My Maps çıktısı. Yol noktaları (wpt) durak sayılır. KML ve KMZ de aynı yere yüklenir.",
         columns=[("trkpt lat/lon/ele", "iz noktaları"), ("wpt", "durak (adıyla)")],
         notes=["Zaman damgası gerekmez; hız profili sürücü modelinden gelir."]),
    dict(key="route_spec", group="guzergah", label="Güzergâh tanımı (bölüm bölüm)", filename="guzergah_tanimi_sablonu.yaml",
         family="importer", upload="#/yukle/dosya", upload_label="Veri yükle → YAML dosyası",
         description="Güzergâh oluşturucunun dosyası: her bölüm uzunluk, eğim, hız sınırı, yüzey, rüzgâr; sonunda durak ya da ışık.",
         columns=[("segments[].length_m", "bölüm uzunluğu, m"), ("grade_pct", "eğim, %"), ("speed_limit_kmh", "hız sınırı"),
                  ("surface", "asfalt, eski_asfalt, beton, parke…"), ("stop", "name, dwell_s, board, alight"), ("signal", "true = bölüm sonunda ışık")],
         notes=["Kaydedince CSV'si de üretilir; ikisi birlikte tutulur."]),
    dict(key="cycle", group="guzergah", label="Standart hız çevrimi", filename="cevrim_sablonu.csv",
         family="library", upload="library/cycle/", upload_label="Kütüphane klasörü: cycle/",
         description="Zaman–hız çifti; çözücü doğrudan okur. Paket (zip) ile de alınır.",
         columns=[("time_s", "zaman, s (1 s adım önerilir)"), ("speed_mps", "hız, m/s")],
         notes=["Virgül ayraç, ondalık nokta. '#' ile başlayan satırlar yorumdur."]),

    dict(key="motor_map_grid", group="harita", label="Motor verim haritası (ızgara)", filename="motor_verim_izgara_sablonu.xlsx",
         family="importer", upload="#/yukle/harita", upload_label="Veri yükle → Motor verim haritası",
         description="Köşe hücresi eksenleri adlandırır; ilk satır devir, ilk sütun tork, içi verim. Yüzde ya da kesir olabilir; kayıp (kW) da kabul edilir.",
         columns=[("sol üst hücre", "torque_nm / speed_rpm (eksen adları)"), ("ilk satır", "devir, rpm ya da rad/s"),
                  ("ilk sütun", "tork, Nm"), ("iç hücreler", "verim % (ya da 0–1), motor + invertör")],
         notes=["Boş hücreler en yakın komşudan doldurulur ve uyarı verilir.", "Devir rad/s ise başlığa 'rad/s' yazın; rpm varsayılır."]),
    dict(key="motor_map_long", group="harita", label="Motor verim haritası (uzun liste)", filename="motor_verim_liste_sablonu.xlsx",
         family="importer", upload="#/yukle/harita", upload_label="Veri yükle → Motor verim haritası",
         description="Dinamometre çıktısı gibi satır satır: devir, tork, verim.",
         columns=[("speed_rpm", "devir"), ("torque_nm", "tork"), ("efficiency_pct", "verim")],
         notes=["Sıra önemsizdir; ızgaraya dönüştürülür."]),
    dict(key="curve_ocv", group="harita", label="Batarya OCV eğrisi", filename="batarya_ocv_sablonu.xlsx",
         family="importer", upload="#/yukle/egri", upload_label="Veri yükle → Batarya ya da klima eğrisi",
         description="Şarj durumuna göre açık devre gerilimi. Hücre ya da paket gerilimi olabilir; paket ise seri hücre sayısı sorulur.",
         columns=[("soc_pct", "şarj durumu, % (ya da 0–1)"), ("voltage_v", "hücre gerilimi, V")],
         notes=["İç direnç eğrisi aynı biçimde: soc_pct, resistance_mohm."]),
    dict(key="curve_cop", group="harita", label="Klima COP eğrisi", filename="klima_cop_sablonu.xlsx",
         family="importer", upload="#/yukle/egri", upload_label="Veri yükle → Batarya ya da klima eğrisi",
         description="Dış sıcaklığa göre ısı pompası ya da soğutma COP değeri.",
         columns=[("ambient_c", "dış sıcaklık, °C"), ("cop", "COP, birimsiz")], notes=[]),
    dict(key="engine_fuel_map", group="harita", label="Dizel yakıt haritası (VECTO biçimi)", filename="motor_yakit_haritasi_sablonu.csv",
         family="library", upload="library/engine/maps/", upload_label="Kütüphane klasörü: engine/maps/ (VECTO içe aktarma bunu kendisi yazar)",
         description="Izgara: ilk satır devir (rpm), ilk sütun tork (Nm), içi yakıt debisi g/h. VECTO motor sertifikasından otomatik üretilir; elle yalnızca dinamometre verisi için.",
         columns=[("torque_nm / speed_rpm", "eksen adları"), ("iç hücreler", "yakıt, g/h")], notes=["Virgül ayraç, ondalık nokta."]),
    dict(key="engine_full_load", group="harita", label="Dizel tam yük ve sürükleme eğrisi", filename="motor_tam_yuk_sablonu.csv",
         family="library", upload="library/engine/maps/", upload_label="Kütüphane klasörü: engine/maps/",
         description="Devire göre azami tork ve motor freni torku (negatif).",
         columns=[("speed_rpm", "devir"), ("max_torque_nm", "tam yük torku"), ("drag_torque_nm", "sürükleme torku, negatif")], notes=[]),
    dict(key="loss_map", group="harita", label="Şanzıman / aks kayıp haritası", filename="kayip_haritasi_sablonu.csv",
         family="library", upload="library/transmission/maps/", upload_label="Kütüphane klasörü: transmission/maps/",
         description="VECTO kayıp haritası biçimi, uzun liste: vites, giriş devri, giriş torku, kayıp torku.",
         columns=[("gear", "vites no (aks için 1)"), ("input_speed_rpm", "giriş devri"), ("input_torque_nm", "giriş torku"), ("loss_nm", "kayıp torku, Nm")], notes=[]),

    dict(key="vehicle", group="arac", label="Araç dosyası", filename="arac_sablonu.yaml",
         family="importer", upload="#/yukle/dosya", upload_label="Veri yükle → YAML dosyası (ya da Araç kur sayfası)",
         description="Bir araç, kütüphanedeki bileşenlere referanslardır. Şablon bu kütüphanede var olan bileşenleri gösterir.",
         columns=[("powertrain", "battery_electric ya da diesel"), ("chassis, motor, battery…", "kind/dosya.yaml referansları"),
                  ("seats, standees", "yolcu kapasitesi"), ("control", "elektrikli: rejenerasyon ve ön koşullandırma ayarları")],
         notes=["Referans verilen dosya yoksa yükleme reddedilir; önce bileşeni ekleyin."]),
    dict(key="scenario", group="senaryo", label="Senaryo dosyası", filename="senaryo_sablonu.yaml",
         family="importer", upload="#/yukle/dosya", upload_label="Veri yükle → YAML dosyası (ya da Başlangıç → Senaryo yükle)",
         description="Güzergâh ya da çevrim + araçlar + koşullar + yük + trafik. Kaydedilir, yeniden koşar, araçları yan yana karşılaştırır.",
         columns=[("duty", "route/direction/laps ya da cycle/repeat"), ("vehicles", "araç dosya adları"),
                  ("conditions", "ambient_c, solar, night, hvac_off, headwind_mps, pressure_hpa"),
                  ("load", "mode: profile | constant | stops | mass"), ("traffic", "yok, akici, orta, yogun, tikali")],
         notes=["Araç ve güzergâh adları kütüphanedekiyle aynı olmalı."]),
    dict(key="cabin", group="arac", label="Kabin katman modeli", filename="kabin_sablonu.yaml",
         family="importer", upload="#/yukle/dosya", upload_label="Veri yükle → YAML dosyası (ya da Kabin termal sayfası)",
         description="Kabin yüzeyleri (tavan, yan panel, cam, taban…) dıştan içe malzeme katmanlarıyla; üfleyici debisi ve üfleme sıcaklığı; CFD/test referansı. Klima bileşenine bağlanınca UA, cam alanı, iç kütle ve SHGC buradan türetilir.",
         columns=[("materials.<ad>", "density_kg_m3, cp_j_kgk, k_w_mk"), ("surfaces[].layers", "material, thickness_mm (dıştan içe)"),
                  ("surfaces[].orientation", "roof | side_left | side_right | front | rear | floor"), ("outer / outer_c", "ambient ya da fixed (motor bölmesi 80 °C)"),
                  ("absorptivity / transmissivity", "güneş için"), ("inlets[]", "count, mass_flow_kg_s, supply_c [[s, °C]…]"),
                  ("reference", "cfd / test eğrileri ve kabul kriterleri")],
         notes=["Alanlar CAD'den ölçülmeli; şablondaki alanlar örnektir.", "Doğrulama: Kabin termal sayfasında CFD ortalamasıyla karşılaştırılır."]),
    dict(key="line", group="senaryo", label="Hat ve tarife dosyası", filename="hat_sablonu.yaml",
         family="importer", upload="#/yukle/dosya", upload_label="Veri yükle → YAML dosyası",
         description="Durak listesi (biniş sayıları) ve saat bantları (sefer aralığı, trafik, talep). Güzergâh CSV'sindeki durak sayısıyla eşleşmeli.",
         columns=[("route", "route/dosya.csv"), ("stops[].board / board_in", "zirve saatte biniş, gidiş / dönüş"),
                  ("timetable[].headway_min", "sefer aralığı, dk"), ("traffic, demand", "bant trafiği ve talep oranı")], notes=[]),

    dict(key="log_electric", group="test", label="Elektrikli araç logu", filename="arac_logu_sablonu.xlsx",
         family="importer", upload="#/dogrulama", upload_label="Doğrulama → Araç logu",
         description="CAN / telematik kaydı: zaman, hız ve batarya gücü (ya da gerilim × akım). Model aynı izden geçer, fark bileşen bileşen raporlanır.",
         columns=[("time_s", "zaman, s"), ("speed_kmh", "hız"), ("battery_power_kw", "batarya gücü, + deşarj"),
                  ("soc_pct", "şarj durumu"), ("ambient_c", "dış sıcaklık"), ("doors", "1 = kapı açık"), ("passengers", "yolcu")],
         notes=["Sütunlar otomatik eşlenir; eşleme sayfada düzeltilebilir."]),
    dict(key="log_diesel", group="test", label="Dizel test kaydı (CAN)", filename="dizel_test_kaydi_sablonu.csv",
         family="library", upload="library/testlog/<kampanya>/", upload_label="Kütüphane klasörü: testlog/<kampanya>/ + meta.yaml",
         description="Dewesoft türü dışa aktarım, 1 Hz. Doğrulama sayfasına tek dosya olarak da yüklenir.",
         columns=[("time_s", "zaman"), ("speed_kmh", "hız"), ("engine_rpm", "devir"), ("gear", "vites"),
                  ("fuel_rate_lph", "anlık yakıt, l/h"), ("fuel_cum_l", "toplam yakıt, l"), ("accel_pedal_pct / brake_pedal_pct", "pedallar")],
         notes=["Sinyal adları Dewesoft/J1939 adlarıyla da tanınır (WheelBasedVehicleSpeed, EngSpeed, HighResolutionEngFuelRate…)."]),
    dict(key="testlog_meta", group="test", label="Test kampanyası meta.yaml", filename="meta_sablonu.yaml",
         family="library", upload="library/testlog/<kampanya>/", upload_label="Kütüphane klasörü: testlog/<kampanya>/",
         description="Kampanyanın koşulları ve her koşunun ölçülen yakıtı; Doğrulama sayfası bununla kampanyayı tek tuşla koşar.",
         columns=[("vehicle, route", "kütüphane adları"), ("test_mass_kg, hvac", "test koşulu"),
                  ("runs.<dosya>", "direction, ambient_c, wind_mps, night, fuel_device_l")], notes=[]),
]


def _doc(kind: str) -> str:
    """The component's docstring when it has one; the dataclass signature is not a description."""
    doc = " ".join(schema(kind)["doc"].split())
    if not doc or "(" in doc[:30]:
        return f"{KIND_LABELS_TR.get(kind, kind)} dosyası: alanlar, birimler ve ölçüm kaynağı."
    return doc[:220]


def list_templates(lib) -> dict:
    """The catalogue for the page, plus one entry per component kind."""
    items = []
    for t in _CATALOGUE:
        items.append({k: v for k, v in t.items()})
    for kind in COMPONENT_KINDS:
        items.append(dict(key=f"component_{kind}", group="arac", label=f"Bileşen: {KIND_LABELS_TR.get(kind, kind)}",
                          filename=f"bilesen_{kind}_sablonu.yaml", family="importer", upload="#/yukle/dosya",
                          upload_label="Veri yükle → YAML dosyası (ya da Kütüphane → Yeni)",
                          description=_doc(kind),
                          columns=[(f["name"], f"{f['label']}{' [' + f['unit'] + ']' if f.get('unit') else ''}") for f in schema(kind)["fields"]
                                   if f["type"] not in ("text",)][:12],
                          notes=["Her satırın sonunda alanın anlamı ve birimi yorum olarak yazar.", "validated: true yalnızca ölçülmüş veri için."]))
    return {"groups": GROUPS, "items": items}


# ---------------------------------------------------------------- builders
def _xlsx(headers: list, rows: list, *, note: str = "") -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook(); ws = wb.active; ws.title = "veri"
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append([float(v) if isinstance(v, (np.floating, np.integer)) else v for v in r])
    if note:
        ws2 = wb.create_sheet("NOT")
        for line in note.strip().splitlines():
            ws2.append([line])
    out = io.BytesIO(); wb.save(out)
    return out.getvalue()


_ASCII = str.maketrans("ıİşŞğĞüÜöÖçÇâÂ", "iISsGgUuOoCcaA")


def _csv(lines: list, comment: str = "") -> bytes:
    """Library-format CSV: the solver's readers open files in the platform
    encoding, so comments are folded to ASCII."""
    head = "".join(f"# {ln.translate(_ASCII)}\n" for ln in comment.strip().splitlines()) if comment else ""
    return (head + "\n".join(lines) + "\n").encode("ascii")


NOTE = "ŞABLON - SENTETİK DEĞERLER, ÖLÇÜM DEĞİL. Yalnızca dosya biçimini gösterir."


def _route_profile(n: int = 25, step_m: float = 200.0):
    d = np.arange(n) * step_m
    ele = 30 + 12 * np.sin(d / 1500.0) + d * 0.004
    lim = np.where(d < 1000, 50, np.where(d < 3600, 70, 50))
    stop = [1 if k % 5 == 2 else 0 for k in range(n)]
    return d, ele, lim, stop


def _b_route_table(lib):
    d, ele, lim, stop = _route_profile()
    return _xlsx(["distance_m", "elevation_m", "speed_limit_kmh", "stop"],
                 [[float(a), round(float(b), 1), int(c), s] for a, b, c, s in zip(d, ele, lim, stop)], note=NOTE)


def _b_route_latlon(lib):
    d, ele, lim, stop = _route_profile()
    lat = 37.0000 + d / 111_000.0
    lon = 35.3200 + d * 0.3 / 111_000.0
    return _xlsx(["lat", "lon", "elevation_m", "stop"],
                 [[round(float(a), 6), round(float(b), 6), round(float(c), 1), s] for a, b, c, s in zip(lat, lon, ele, stop)], note=NOTE)


def _b_route_gpx(lib):
    d, ele, lim, stop = _route_profile()
    lat = 37.0000 + d / 111_000.0
    lon = 35.3200 + d * 0.3 / 111_000.0
    pts = "".join(f'    <trkpt lat="{a:.6f}" lon="{b:.6f}"><ele>{c:.1f}</ele></trkpt>\n' for a, b, c in zip(lat, lon, ele))
    wpts = "".join(f'  <wpt lat="{lat[k]:.6f}" lon="{lon[k]:.6f}"><name>Durak {k // 5 + 1}</name></wpt>\n'
                   for k in range(len(d)) if stop[k])
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<!-- {NOTE} -->\n'
            f'<gpx version="1.1" creator="temsasim sablon" xmlns="http://www.topografix.com/GPX/1/1">\n'
            f'{wpts}  <trk><name>Örnek hat</name><trkseg>\n{pts}  </trkseg></trk>\n</gpx>\n').encode("utf-8")


def _b_route_spec(lib):
    spec = route_template("sehir_ici", stops=6, spacing_m=500)
    d = {"kind": "route_spec", "route_kind": spec.kind, **{k: v for k, v in spec.to_dict().items() if k != "kind"}}
    d["name"] = "Örnek hat (şablon)"
    d["stem"] = "ornek_hat_sablon"
    head = ("# Güzergâh tanımı şablonu. Yükleyince CSV'si üretilir ve düzenlenebilir kalır.\n"
            "# surface: asfalt | eski_asfalt | beton | parke | toprak   (yuvarlanma direnci çarpanı)\n"
            "# stop: {name, dwell_s (bekleme), board (biniş), alight (iniş)}   signal: true = bölüm sonunda ışık\n")
    return (head + yaml.safe_dump(d, sort_keys=False, allow_unicode=True)).encode("utf-8")


def _b_cycle(lib):
    t = np.arange(0, 181)
    v = np.clip(np.where(t < 30, t * 0.5, np.where(t < 120, 15.0, np.maximum(0.0, 15.0 - (t - 120) * 0.5))), 0, None)
    v[150:] = 0.0
    # the cycle reader takes no comment lines: header + data only
    return _csv(["time_s,speed_mps"] + [f"{int(a)},{b:.3f}" for a, b in zip(t, v)])


def _motor_grid():
    rpm = np.arange(500, 3501, 500)
    nm = np.arange(200, 2001, 300)
    R, T = np.meshgrid(rpm, nm)
    eff = 0.95 - 0.00003 * np.abs(R - 2000) - 0.00002 * np.abs(T - 900)
    return rpm, nm, np.clip(eff, 0.80, 0.95)


def _b_motor_map_grid(lib):
    rpm, nm, eff = _motor_grid()
    rows = [[float(nm[i])] + [round(float(eff[i, j]) * 100, 1) for j in range(rpm.size)] for i in range(nm.size)]
    return _xlsx(["torque_nm / speed_rpm"] + [float(r) for r in rpm], rows,
                 note=NOTE + "\nİlk satır devir (rpm), ilk sütun tork (Nm), iç hücreler verim %. Motor + invertör birlikte.")


def _b_motor_map_long(lib):
    rpm, nm, eff = _motor_grid()
    rows = [[float(rpm[j]), float(nm[i]), round(float(eff[i, j]) * 100, 1)] for i in range(nm.size) for j in range(rpm.size)]
    return _xlsx(["speed_rpm", "torque_nm", "efficiency_pct"], rows, note=NOTE)


def _b_curve_ocv(lib):
    soc = np.arange(0, 101, 5)
    v = 3.0 + 1.2 * (soc / 100) - 0.5 * (soc / 100) ** 2 + 0.05 * np.sin(soc / 8)
    return _xlsx(["soc_pct", "voltage_v"], [[int(a), round(float(b), 3)] for a, b in zip(soc, v)], note=NOTE + "\nHücre gerilimi. Paket gerilimi yazarsanız seri hücre sayısı sorulur.")


def _b_curve_cop(lib):
    t = np.arange(-20, 41, 5)
    cop = np.clip(1.6 + 0.045 * (t + 20), 1.5, 4.2)
    return _xlsx(["ambient_c", "cop"], [[int(a), round(float(b), 2)] for a, b in zip(t, cop)], note=NOTE)


def _b_engine_fuel_map(lib):
    rpm = [600, 800, 1000, 1200, 1400, 1600, 1800, 2000]
    nm = [-100, 0, 200, 400, 600, 800, 1000, 1200]
    lines = ["torque_nm / speed_rpm," + ",".join(str(r) for r in rpm)]
    for T in nm:
        vals = [max(0.0, 400 + 0.02 * r * max(T, 0) + 0.3 * r) if T >= 0 else 0.0 for r in rpm]
        lines.append(f"{T}," + ",".join(f"{v:.1f}" for v in vals))
    return _csv(lines, f"{NOTE}\nIzgara: ilk satır devir rpm, ilk sütun tork Nm, iç hücreler yakıt g/h.")


def _b_engine_full_load(lib):
    rpm = np.arange(600, 2201, 200)
    tmax = 1600 - 0.0004 * (rpm - 1300) ** 2
    drag = -(60 + 0.06 * rpm)
    return _csv(["speed_rpm,max_torque_nm,drag_torque_nm"] + [f"{int(r)},{a:.1f},{b:.1f}" for r, a, b in zip(rpm, tmax, drag)], NOTE)


def _b_loss_map(lib):
    lines = ["gear,input_speed_rpm,input_torque_nm,loss_nm"]
    for g in (1, 2, 3):
        for r in (600, 1200, 1800):
            for T in (0, 500, 1000, 1500):
                lines.append(f"{g},{r},{T},{(8 + 0.012 * T + 0.004 * r) * (1.3 if g == 1 else 1.0):.2f}")
    return _csv(lines, f"{NOTE}\nVECTO kayıp haritası biçimi. Aks için gear = 1 tek vites.")


def _b_vehicle(lib):
    from .library import Library
    L = lib if isinstance(lib, Library) else Library(lib)
    have = {k: L.list_kind(k) for k in COMPONENT_KINDS}
    pt = "battery_electric" if all(have.get(k) for k in SLOTS_BY_POWERTRAIN["battery_electric"].values()) else "diesel"
    d = {"kind": "vehicle", "name": "Örnek araç (şablon)", "variant": "12 m", "powertrain": pt,
         "description": "ŞABLON. Bileşen referanslarını kendi dosyalarınızla değiştirin."}
    for slot, kind in SLOTS_BY_POWERTRAIN[pt].items():
        items = have.get(kind) or []
        d[slot] = f"{kind}/{items[0] if items else '<dosya>'}.yaml"
    d["seats"] = 30; d["standees"] = 50
    if pt == "diesel":
        d["fuel_tank_l"] = 400.0
    else:
        d["control"] = to_yaml_dict("control", {})
    head = ("# Araç şablonu: bir araç, kütüphanedeki bileşenlere referanslardır (kind/dosya.yaml).\n"
            "# powertrain: battery_electric -> chassis, driveline, motor, battery, hvac, auxiliaries, aging\n"
            "#             diesel           -> chassis, transmission, engine, hvac, auxiliaries\n")
    return (head + yaml.safe_dump(d, sort_keys=False, allow_unicode=True)).encode("utf-8")


def _b_scenario(lib):
    from .library import Library
    from .routes import list_routes
    L = lib if isinstance(lib, Library) else Library(lib)
    routes = list_routes(L.root); vehicles = L.list_kind("vehicle")
    sc = Scenario(name="Örnek senaryo (şablon)", kind="sehir_ici",
                  description="ŞABLON. Güzergâh, araç ve koşulları kendi değerlerinizle değiştirin.",
                  duty={"route": routes[0]["stem"] if routes else "<guzergah>", "direction": "gidis", "laps": 1},
                  vehicles=[vehicles[0] if vehicles else "<arac>"],
                  conditions={"ambient_c": 30.0, "solar": 600.0, "night": False, "hvac_off": False, "headwind_mps": 0.0, "pressure_hpa": 1013.25},
                  load={"mode": "profile", "passengers": 40, "mass_kg": None}, traffic="orta")
    d = sc.to_dict()
    head = ("# Senaryo şablonu. duty: {route, direction: gidis|donus, laps} ya da {cycle, repeat}\n"
            "# load.mode: profile (yol boyunca değişken, zirve = passengers) | constant | stops (duraklardan) | mass (test kütlesi kg)\n"
            "# traffic: yok | akici | orta | yogun | tikali     conditions.hvac_off: true = bütün alıcılar kapalı (yakıt testi)\n")
    return (head + yaml.safe_dump(d, sort_keys=False, allow_unicode=True)).encode("utf-8")


def _b_cabin(lib):
    from .cabin import DEFAULT_MATERIALS
    d = {"kind": "cabin", "name": "Örnek kabin (şablon)", "description": "ŞABLON. Yüzey alanlarını CAD'den ölçüp girin.",
         "materials": {k: dict(v) for k, v in DEFAULT_MATERIALS.items()},
         "surfaces": [
             {"name": "Tavan", "orientation": "roof", "area_m2": 30.0, "layers": [{"material": "frp", "thickness_mm": 3.0}, {"material": "insulation", "thickness_mm": 40.0}, {"material": "abs", "thickness_mm": 2.5}], "absorptivity": 0.8, "transmissivity": 0.0, "h_out_w_m2k": 30.0, "h_in_w_m2k": 4.0},
             {"name": "Yan paneller", "orientation": "side_left", "area_m2": 26.0, "layers": [{"material": "steel", "thickness_mm": 1.0}, {"material": "insulation", "thickness_mm": 36.0}, {"material": "abs", "thickness_mm": 2.5}], "absorptivity": 0.4, "transmissivity": 0.0, "h_out_w_m2k": 30.0, "h_in_w_m2k": 4.0},
             {"name": "Yan camlar", "orientation": "side_right", "area_m2": 21.0, "layers": [{"material": "glass", "thickness_mm": 6.4}], "absorptivity": 0.1, "transmissivity": 0.8, "h_out_w_m2k": 30.0, "h_in_w_m2k": 4.0},
             {"name": "Taban (motor üstü)", "orientation": "floor", "area_m2": 7.0, "outer": "fixed", "outer_c": 80.0, "layers": [{"material": "insulation", "thickness_mm": 36.0}, {"material": "plywood", "thickness_mm": 12.0}], "absorptivity": 0.4, "transmissivity": 0.0, "h_out_w_m2k": 30.0, "h_in_w_m2k": 4.0}],
         "air_volume_m3": 56.0, "interior_mass_kg": 250.0, "interior_cp_j_kgk": 1075.0, "interior_contact_area_m2": 100.0, "interior_h_w_m2k": 4.0,
         "occupants": 50, "occupant_w": 100.0,
         "inlets": [{"name": "A/C üfleyicileri", "count": 12, "mass_flow_kg_s": 0.15, "supply_c": [[0, 41.0], [300, 42.0], [600, 27.5], [1000, 22.0], [1500, 18.5], [2400, 16.0], [3600, 13.8]]}],
         "infiltration_ach": 0.3, "location": {"lat": 37.0, "lon": 35.32, "day_of_year": 200, "hour_local": 15.0, "utc_offset_h": 3.0, "heading_deg": 90.0},
         "reference": {"cfd": {"label": "CFD (örnek)", "times_min": [0, 10, 20, 30, 40], "avg_c": [55.0, 35.85, 27.59, 23.98, 21.96]},
                       "criteria": [{"at_min": 30, "max_c": 31.0}, {"at_min": 45, "max_c": 29.0}]},
         "validated": False, "notes": "ŞABLON"}
    head = ("# Kabin katman modeli şablonu. surfaces[].layers dıştan içe; outer: ambient | fixed (outer_c ile).\n"
            "# orientation: roof | side_left | side_right | front | rear | floor. inlets[].supply_c: [[saniye, °C], ...]\n")
    return (head + yaml.safe_dump(d, sort_keys=False, allow_unicode=True)).encode("utf-8")


def _b_line(lib):
    d = {"kind": "line", "code": "ÖRN-1", "name": "Örnek hat (şablon)", "example": True,
         "route": "route/<guzergah>.csv", "layover_min": 8,
         "notes": "ŞABLON. Durak sayısı güzergâh CSV'sindeki durak işaretleriyle aynı olmalı.",
         "stops": [{"name": "Başlangıç", "board": 100, "board_in": 10}, {"name": "Çarşı", "board": 60, "board_in": 80},
                   {"name": "Hastane", "board": 30, "board_in": 50}, {"name": "Son durak", "board": 0, "board_in": 110}],
         "timetable": [{"from": 6, "to": 7, "headway_min": 20, "traffic": "akici", "demand": 0.35},
                       {"from": 7, "to": 9, "headway_min": 8, "traffic": "yogun", "demand": 1.0},
                       {"from": 9, "to": 16, "headway_min": 15, "traffic": "orta", "demand": 0.5},
                       {"from": 16, "to": 19, "headway_min": 8, "traffic": "yogun", "demand": 0.9},
                       {"from": 19, "to": 23, "headway_min": 20, "traffic": "akici", "demand": 0.3}]}
    return ("# Hat ve tarife şablonu.\n" + yaml.safe_dump(d, sort_keys=False, allow_unicode=True)).encode("utf-8")


def _b_log_electric(lib):
    t = np.arange(0, 241)
    v = np.clip(np.where(t < 40, t * 1.2, np.where(t < 160, 48.0, np.maximum(0.0, 48.0 - (t - 160) * 1.2))), 0, 50)
    p = 12 + 2.5 * v + np.where((t > 160) & (v > 0), -90.0, 0.0)
    soc = 80 - np.cumsum(np.clip(p, 0, None)) / 3600 / 3.5
    rows = [[int(a), round(float(b), 1), round(float(c), 1), round(float(d), 2), 28.0, 1 if (200 <= a <= 215) else 0, 35]
            for a, b, c, d in zip(t, v, p, soc)]
    return _xlsx(["time_s", "speed_kmh", "battery_power_kw", "soc_pct", "ambient_c", "doors", "passengers"], rows, note=NOTE)


def _b_log_diesel(lib):
    t = np.arange(0, 181)
    v = np.clip(np.where(t < 40, t * 2.0, 80.0), 0, 80)
    rpm = np.where(v < 1, 650, 900 + v * 4)
    gear = np.where(v < 1, 0, np.minimum(12, (v // 8) + 2)).astype(int)
    rate = np.where(v < 1, 1.2, 8 + 0.2 * v)
    cum = np.cumsum(rate) / 3600
    lines = ["time_s,speed_kmh,engine_rpm,gear,fuel_rate_lph,fuel_cum_l,accel_pedal_pct,brake_pedal_pct"]
    for a, b, c, d, e, f in zip(t, v, rpm, gear, rate, cum):
        lines.append(f"{int(a)},{b:.2f},{c:.0f},{d},{e:.3f},{f:.4f},{min(100, b * 1.1):.0f},0")
    return _csv(lines, f"{NOTE}\nDizel test kaydı, 1 Hz. Sinyal adları J1939/Dewesoft adlarıyla da tanınır.")


def _b_testlog_meta(lib):
    d = {"campaign": "Örnek kampanya (şablon)", "report": "TRM-000-000-00", "dates": "2026-01-01 .. 2026-01-02",
         "vehicle": "<arac>", "vehicle_id": "OA00000", "route": "<guzergah>", "route_length_km": 44.3,
         "test_mass_kg": 18000, "hvac": "off", "driving": "cruise control 100 km/h",
         "wind_convention": "headwind_on_gidis",
         "stops": [{"name": "Tesis", "idle_min": 5}],
         "measurement": {"can_fuel": "HighResolutionEngFuelRate / EngTripFuel", "fuel_device": "harici yakıt ölçer"},
         "runs": {"gunduz_gidis_1": {"direction": "gidis", "ambient_c": 33.5, "wind_mps": 1.1, "wind_dir": "D>B",
                                     "pressure_hpa": 1004.4, "fuel_device_l": 10.546, "display_l100": 21.6},
                  "gece_gidis_1": {"direction": "gidis", "night": True, "ambient_c": 23.3, "wind_mps": 0.7, "wind_dir": "D>B",
                                   "pressure_hpa": 1001.1, "fuel_device_l": 10.94, "display_l100": 22.2}}}
    head = ("# Test kampanyası meta dosyası şablonu. Her koşunun CSV'si bu klasörde aynı adla durur (gunduz_gidis_1.csv).\n"
            "# fuel_device_l: harici cihazla ölçülen toplam yakıt; display_l100: gösterge panelinin değeri.\n")
    return (head + yaml.safe_dump(d, sort_keys=False, allow_unicode=True)).encode("utf-8")


def _b_component(kind: str):
    def build(lib):
        sch = schema(kind)
        values = {f["name"]: f["default"] for f in sch["fields"] if f["type"] not in ("map",) and f["default"] not in (None, "")}
        values["name"] = f"Örnek {KIND_LABELS_TR.get(kind, kind).lower()} (şablon)"
        values["validated"] = False
        if "notes" in values or any(f["name"] == "notes" for f in sch["fields"]):
            values["notes"] = "ŞABLON: kabul değerleri. Ölçülmüş değerle değiştirip validated: true yapın."
        data = to_yaml_dict(kind, values)
        # the alias key each field is written under, so the comment lands on the right line
        alias = {}
        for f in sch["fields"]:
            if f["type"] == "map" or f["default"] in (None, ""):
                continue
            keys = [k for k in to_yaml_dict(kind, {f["name"]: f["default"]}) if k != "kind"]
            if keys:
                alias[keys[0]] = f
        lines = [f"# {KIND_LABELS_TR.get(kind, kind)} şablonu. Bilinmeyen alan adı hatadır; alan sonundaki ek birimi belirler (_kw, _rpm, _kmh, _mohm)."]
        doc = " ".join(sch["doc"].split())
        if doc and "(" not in doc[:30]:          # a real docstring, not the dataclass signature
            lines.append(f"# {doc[:300]}")
        for k, v in data.items():
            dumped = yaml.safe_dump({k: v}, sort_keys=False, allow_unicode=True, default_flow_style=False).rstrip("\n")
            f = alias.get(k)
            if f and "\n" not in dumped:
                unit = f" [{f['unit']}]" if f.get("unit") else ""
                doc = " ".join(str(f.get("doc", "")).split())[:110]
                dumped = f"{dumped:<42}# {f['label']}{unit}{' — ' + doc if doc else ''}"
            lines.append(dumped)
        maps = [f for f in sch["fields"] if f["type"] == "map"]
        for f in maps:
            lines.append(f"# {f['name']}: maps/<dosya>.csv   # {f['label']} (isteğe bağlı; Veri yükle → harita ile eklenir)")
        return ("\n".join(lines) + "\n").encode("utf-8")
    return build


_BUILDERS = {
    "route_table": (_b_route_table, XLSX), "route_latlon": (_b_route_latlon, XLSX), "route_gpx": (_b_route_gpx, "application/gpx+xml"),
    "route_spec": (_b_route_spec, "application/x-yaml"), "cycle": (_b_cycle, "text/csv"),
    "motor_map_grid": (_b_motor_map_grid, XLSX), "motor_map_long": (_b_motor_map_long, XLSX),
    "curve_ocv": (_b_curve_ocv, XLSX), "curve_cop": (_b_curve_cop, XLSX),
    "engine_fuel_map": (_b_engine_fuel_map, "text/csv"), "engine_full_load": (_b_engine_full_load, "text/csv"), "loss_map": (_b_loss_map, "text/csv"),
    "vehicle": (_b_vehicle, "application/x-yaml"), "scenario": (_b_scenario, "application/x-yaml"), "line": (_b_line, "application/x-yaml"),
    "cabin": (_b_cabin, "application/x-yaml"),
    "log_electric": (_b_log_electric, XLSX), "log_diesel": (_b_log_diesel, "text/csv"), "testlog_meta": (_b_testlog_meta, "application/x-yaml"),
}
for _k in COMPONENT_KINDS:
    _BUILDERS[f"component_{_k}"] = (_b_component(_k), "application/x-yaml")


def template_keys() -> list:
    return list(_BUILDERS)


def build_template(key: str, lib) -> tuple[bytes, str, str]:
    """(bytes, filename, mimetype) for one template."""
    if key not in _BUILDERS:
        raise KeyError(f"Bilinmeyen şablon: {key}")
    fn, mime = _BUILDERS[key]
    entry = next((t for t in list_templates(lib)["items"] if t["key"] == key), None)
    return fn(lib), (entry or {}).get("filename", f"{key}.txt"), mime


def all_templates_zip(lib) -> bytes:
    """Every template in one zip, in folders named after the groups."""
    labels = dict(GROUPS)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for t in list_templates(lib)["items"]:
            data, fname, _ = build_template(t["key"], lib)
            folder = labels.get(t["group"], t["group"]).replace(" ", "_")
            z.writestr(f"sablonlar/{folder}/{fname}", data)
        z.writestr("sablonlar/OKU.txt", "temsasim veri şablonları. Her dosya bir biçim örneğidir; sayılar sentetiktir.\n"
                   "Nereye yükleneceği arayüzde 'Veri biçimleri' sayfasında yazar.\n")
    return out.getvalue()
