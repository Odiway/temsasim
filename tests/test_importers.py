"""Importers must accept the files engineers actually have.

Each fixture here imitates a real way a supplier or logger hands over data. The
ground truth is always a known synthetic map, so every test can check that the
*numbers* came through, not just that nothing crashed.
"""

from __future__ import annotations

import io

import numpy as np
import pytest

from temsasim.importers import (build_log, detect_log_columns, import_curve,
                                import_map, import_route, parse_number, read_table)
from temsasim.maps import synth_motor_efficiency
from temsasim.units import RPM

RPM_AX = np.arange(250, 3001, 250)          # 12 speeds
NM_AX = np.arange(200, 2801, 200)           # 14 torques
TRUTH = synth_motor_efficiency(RPM_AX * RPM, NM_AX, peak_efficiency=0.955,
                               peak_at_speed_rads=1100 * RPM, peak_at_torque_nm=1100)
PROBE_RPM, PROBE_NM = 1500, 1000
EXPECT = TRUTH(PROBE_RPM * RPM, PROBE_NM)


def _probe(imp):
    return imp.map(PROBE_RPM * RPM, PROBE_NM)


def _grid_text(sep, dec, corner, *, percent=False, transpose=False):
    z = TRUTH.z * (100 if percent else 1)
    top, side = (NM_AX, RPM_AX) if transpose else (RPM_AX, NM_AX)
    zz = z.T if transpose else z
    fmt = lambda v: (f"{v:.4f}".replace(".", dec))
    lines = [sep.join([corner] + [f"{v:g}" for v in top])]
    for k, s in enumerate(side):
        lines.append(sep.join([f"{s:g}"] + [fmt(v) for v in zz[k]]))
    return "\n".join(lines).encode("utf-8")


# ------------------------------------------------------------ numbers & text
@pytest.mark.parametrize("raw,dec,want", [
    ("95,5", ",", 95.5), ("1.234,5", ",", 1234.5), ("%95", ".", 95.0),
    ("95 %", ".", 95.0), (" 12 ", ".", 12.0), ("1,234.5", ".", 1234.5),
    ("", ".", None), ("n/a", ".", None), (3, ".", 3.0), (None, ".", None),
])
def test_parse_number_handles_locales(raw, dec, want):
    assert parse_number(raw, dec) == want


def test_turkish_csv_semicolon_decimal_comma_percent():
    """Excel in Turkish locale: ';' separators, ',' decimals, efficiency in %."""
    data = _grid_text(";", ",", "Tork [Nm] \\ Devir [rpm]", percent=True)
    t = read_table(data, "tedarikci_verim.csv")
    assert t.delimiter == ";" and t.decimal == ","
    imp = import_map(t)
    assert imp.settings == {"orientation": "speed_in_columns", "speed_unit": "rpm",
                            "value_type": "efficiency_percent"}
    assert _probe(imp) == pytest.approx(EXPECT, abs=2e-3)


def test_transposed_grid_is_detected_from_the_corner_label():
    data = _grid_text(",", ".", "Speed [rpm] / Torque [Nm]", transpose=True)
    imp = import_map(read_table(data, "map.csv"))
    assert imp.settings["orientation"] == "speed_in_rows"
    assert imp.confidence["orientation"] == "high"
    assert _probe(imp) == pytest.approx(EXPECT, abs=2e-3)


def test_transposed_grid_can_be_flipped_by_override():
    """With no label the guess may be wrong; the user's override must win."""
    data = _grid_text(",", ".", "", transpose=True)
    imp = import_map(read_table(data, "map.csv"), {"orientation": "speed_in_rows",
                                                   "speed_unit": "rpm"})
    assert _probe(imp) == pytest.approx(EXPECT, abs=2e-3)


def test_long_format_with_shuffled_named_columns():
    """Dyno logger export: one row per operating point, columns in any order."""
    lines = ["Torque (Nm)\tSpeed (rpm)\tEfficiency (%)"]
    for j, nm in enumerate(NM_AX):
        for i, r in enumerate(RPM_AX):
            lines.append(f"{nm}\t{r}\t{TRUTH.z[j, i] * 100:.3f}")
    imp = import_map(read_table("\n".join(lines).encode(), "dyno.txt"))
    assert imp.layout == "long"
    assert imp.settings["value_type"] == "efficiency_percent"
    assert _probe(imp) == pytest.approx(EXPECT, abs=2e-3)


def test_loss_map_in_kw_is_converted_to_efficiency():
    W, T = np.meshgrid(RPM_AX * RPM, NM_AX)
    loss_kw = W * T * (1 / TRUTH.z - 1) / 1e3
    lines = ["Kayıp güç [kW]; " + ";".join(str(r) for r in RPM_AX)]
    for j, nm in enumerate(NM_AX):
        lines.append(f"{nm};" + ";".join(f"{v:.4f}".replace(".", ",") for v in loss_kw[j]))
    imp = import_map(read_table("\n".join(lines).encode("cp1254"), "kayip.csv"))
    assert imp.settings["value_type"] == "loss_kw"
    assert _probe(imp) == pytest.approx(EXPECT, abs=2e-3)


def test_generating_quadrant_rows_are_dropped_with_a_warning():
    data = _grid_text(",", ".", "Nm \\ rpm").decode()
    first_row = data.splitlines()[1].split(",")
    neg = ",".join(["-" + first_row[0]] + first_row[1:])
    lines = data.splitlines()
    lines.insert(1, neg)
    imp = import_map(read_table("\n".join(lines).encode(), "m.csv"))
    assert any("negatif tork" in w for w in imp.warnings)
    assert imp.map.y.min() > 0


def test_excel_with_cover_sheet_title_row_and_blank_envelope_cells(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active.title = "Kapak"
    wb.active["A1"] = "Supplier X - traction motor - CONFIDENTIAL"
    ws = wb.create_sheet("Verim")
    ws["A1"] = "Efficiency map, motor + inverter, 650 VDC"
    ws.cell(row=3, column=1, value="Torque [Nm] \\ Speed [rpm]")
    for i, r in enumerate(RPM_AX):
        ws.cell(row=3, column=2 + i, value=int(r))
    for j, nm in enumerate(NM_AX):
        ws.cell(row=4 + j, column=1, value=int(nm))
        for i, r in enumerate(RPM_AX):
            # Leave the unreachable corner (high speed x high torque) blank,
            # exactly as supplier sheets do.
            if r * nm > 2800 * 1500:
                continue
            ws.cell(row=4 + j, column=2 + i, value=float(TRUTH.z[j, i]))
    buf = io.BytesIO()
    wb.save(buf)
    t = read_table(buf.getvalue(), "supplier_x.xlsx")
    assert t.sheet == "Verim"                  # picked the data, not the cover
    imp = import_map(t)
    assert imp.filled_mask.sum() > 0
    assert any("boş hücre" in w for w in imp.warnings)
    assert _probe(imp) == pytest.approx(EXPECT, abs=2e-3)


def test_rad_per_second_fraction_template_round_trips(tmp_path):
    p = tmp_path / "t.csv"
    TRUTH.to_csv(p)
    imp = import_map(read_table(p.read_bytes(), "t.csv"))
    assert imp.settings["speed_unit"] == "rad_s"
    assert imp.settings["value_type"] == "efficiency_fraction"
    assert _probe(imp) == pytest.approx(EXPECT, abs=2e-3)


def test_legacy_xls_gets_an_actionable_error():
    with pytest.raises(ValueError, match="xlsx"):
        read_table(b"\xd0\xcf\x11\xe0", "old.xls")


# ------------------------------------------------------------------ curves
def test_ocv_curve_percent_soc_and_pack_voltage():
    rows = ["SOC [%];Paket gerilimi [V]"] + [
        f"{s};{(3.0 + 1.2 * s / 100) * 200:.1f}".replace(".", ",") for s in range(0, 101, 10)]
    t = read_table("\n".join(rows).encode(), "ocv.csv")
    out = import_curve(t, "ocv_curve", context={"cells_series": 200})
    assert out["settings"] == {"x_scale": "percent", "y_scale": "pack"}
    x, y = np.array(out["points"]).T
    assert x.max() == pytest.approx(1.0) and y.max() == pytest.approx(4.2, abs=0.01)


def test_pack_voltage_without_cell_count_asks_for_it():
    t = read_table(b"soc,v\n0,600\n1,830\n", "ocv.csv")
    with pytest.raises(ValueError, match="seri hücre"):
        import_curve(t, "ocv_curve")


# ------------------------------------------------------------------ routes
GPX = b"""<?xml version="1.0"?>
<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
  <wpt lat="36.99000" lon="35.32100"><name>Durak 1</name></wpt>
  <wpt lat="37.00010" lon="35.33110"><name>Durak 2</name></wpt>
  <wpt lat="37.10000" lon="35.50000"><name>Garaj</name></wpt>
  <trk><trkseg>
""" + b"\n".join(
    f'<trkpt lat="{36.985 + k * 0.0005:.5f}" lon="{35.316 + k * 0.0005:.5f}">'
    f'<ele>{25 + 10 * np.sin(k / 8):.1f}</ele></trkpt>'.encode() for k in range(60)
) + b"""
  </trkseg></trk>
</gpx>"""


def test_gpx_track_with_waypoint_stops():
    r = import_route(GPX, "hat_413.gpx")
    assert r.source_format == "gpx"
    assert 3.0 < r.distance_m[-1] / 1000 < 5.0
    assert len(r.stops_m) == 2
    assert r.elevation_m.max() - r.elevation_m.min() > 10
    assert any("Garaj" in w for w in r.warnings)     # reported, not silently dropped


def test_route_table_in_km_with_turkish_headers():
    rows = ["Mesafe (km);Rakım (m);Hız limiti;Durak"] + [
        f"{k * 0.05:.2f};{40 + k * 0.5:.1f};50;{1 if k % 8 == 4 else 0}".replace(".", ",")
        for k in range(100)]
    r = import_route("\n".join(rows).encode("cp1254"), "hat.csv")
    assert r.distance_m[-1] == pytest.approx(4950, rel=0.01)
    assert len(r.stops_m) == 12
    assert any("km" in w for w in r.warnings)


def test_route_without_stops_can_assume_a_spacing():
    rows = ["distance_m,elevation_m"] + [f"{k * 20},{50}" for k in range(200)]
    r = import_route("\n".join(rows).encode(), "flat.csv", stop_spacing_m=400)
    assert len(r.stops_m) >= 8


# -------------------------------------------------------------------- logs
def test_can_log_with_datetimes_voltage_current_and_reversed_sign():
    """A typical telematics export: timestamp strings, no power column, current
    logged charge-positive, Turkish headers."""
    lines = ["Zaman;Araç hızı [km/h];Batarya gerilimi [V];Batarya akımı [A];SOC [%]"]
    for k in range(120):
        speed = 0.0 if k < 20 or k > 100 else 30.0
        current = -15.0 if speed == 0 else -250.0      # discharge logged negative
        lines.append(f"14.01.2026 07:{30 + k // 60:02d}:{k % 60:02d};{speed:.1f};"
                     f"700;{current:.1f};{90 - k * 0.01:.2f}".replace(".", ",", 0))
    t = read_table("\n".join(lines).encode("utf-8"), "bus_1042.csv")
    det = detect_log_columns(t)
    m = det["mapping"]
    assert {"time", "speed", "voltage", "current", "soc"} <= set(m)
    log, notes, settings = build_log(t, m, det["header_row"])
    assert settings["speed_unit"] == "kmh"
    assert settings["power_sign"] == "discharge_negative"
    assert log.time_s[-1] == pytest.approx(119.0)
    assert np.all(log.battery_power_w > 0)          # flipped to discharge-positive
    assert log.speed_mps.max() == pytest.approx(30 / 3.6)
    assert any("gerilim × akım" in n for n in notes)
