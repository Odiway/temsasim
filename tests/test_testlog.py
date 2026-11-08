"""Test-team CAN logs: reading, replaying through the diesel model, campaign table."""

from __future__ import annotations

import io
import pathlib

import numpy as np
import pytest

from temsasim.cycle import Environment, Route
from temsasim.library import Library
from temsasim.testlog import (correlate_diesel, grade_along, list_campaigns, load_campaign,
                              read_can_log, run_campaign)

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "library" / "testlog" / "mersin_adana_2026_09"


def _dewesoft_xlsx() -> bytes:
    """A small file in the Dewesoft export layout: header row, unit row, data."""
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Data1"
    ws.append(["Time", "BrakePedalPos", "AccelPedalPos1", "EngSpeed", "TransCurrentGear",
               "HighResolutionEngFuelRate", "HighResolutionEngTripFuel", "WheelBasedVehicleSpeed", "HighResolutionTripDistance"])
    ws.append(["s", "%", "%", "rpm", "", "l/h", "l", "km/h", "m"])
    fuel = 100.0
    for t in range(240):
        v = min(100.0, 1.2 * t) if t < 120 else max(0.0, 100.0 - 1.5 * (t - 120))
        rate = 1.1 if v < 1 else 14 + 0.06 * v
        fuel += rate / 3600
        ws.append([t, 0, 60 if t < 120 else 0, 600 if v < 1 else 1000 + 2 * v, 0 if v < 1 else 12, rate, fuel, v, t * 20])
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def test_dewesoft_export_is_read_with_units_and_signals():
    log = read_can_log(_dewesoft_xlsx(), "Gündüz - Gidiş 9.xlsx")
    assert log.time_s.size == 240 and log.speed_mps.max() == pytest.approx(100 / 3.6, rel=1e-3)
    assert log.fuel_rate_lps is not None and log.engine_rpm is not None and log.gear is not None
    assert log.fuel_l == pytest.approx(sum((1.1 if v < 1 else 14 + 0.06 * v) / 3600
                                           for v in [min(100.0, 1.2 * t) if t < 120 else max(0.0, 100.0 - 1.5 * (t - 120)) for t in range(240)]), rel=0.02)
    assert "WheelBasedVehicleSpeed" in log.columns["speed_kmh"]
    assert any("eğim" in n.lower() for n in log.notes)


def test_library_csv_logs_are_read_too():
    p = next(CAMPAIGN.glob("*.csv"))
    log = read_can_log(p.read_bytes(), p.name)
    assert log.fuel_rate_lps is not None and log.engine_rpm is not None and log.gear is not None
    assert 40 < log.distance_km < 48 and 8 < log.fuel_l < 11


def test_grade_along_reverses_for_the_return_direction():
    # a ramp then a plateau: driven forward it climbs first; driven back it descends last
    r = Route(np.array([0.0, 1000.0, 2000.0]), np.array([0.0, 50.0, 50.0]), np.full(3, 27.0), name="rampa")
    travelled = np.linspace(0, 2000, 201)
    g = grade_along(r, travelled)
    gr = grade_along(r, travelled, reverse=True)
    assert np.tan(g[50]) == pytest.approx(0.05, rel=0.05) and abs(np.tan(g[150])) < 0.002
    assert abs(np.tan(gr[50])) < 0.002 and np.tan(gr[150]) == pytest.approx(-0.05, rel=0.05)


def test_correlation_scores_fuel_and_names_the_gap(demo_library):
    lib = Library(demo_library)
    v = lib.load_vehicle("maraton_13_dizel")
    log = read_can_log(_dewesoft_xlsx(), "deneme.xlsx")
    c = correlate_diesel(v, log, environment=Environment(ambient_c=25, hvac_enabled=False), mass_kg=18000)
    m = c.metrics
    assert m["fuel_sim_l"] > 0 and m["fuel_meas_l"] > 0 and "by_phase" in m
    assert set(m["by_phase"]) == {"idle", "cruise", "accel", "decel", "other"}
    assert "rpm_cruise_sim" in m and "gear_agreement" in m
    assert isinstance(c.summary(), str) and "l/100 km" in c.summary()


def test_campaign_runs_and_matches_the_fuel_device_within_tolerance(demo_library):
    lib = Library(demo_library)
    assert any(c["name"] == "mersin_adana_2026_09" for c in list_campaigns(lib.root))
    meta, runs = load_campaign(lib.root, "mersin_adana_2026_09")
    assert len(runs) == 9 and meta["test_mass_kg"] == 18000
    out = run_campaign(lib, "mersin_adana_2026_09", runs=["gunduz_gidis_1", "gunduz_donus_1"])
    assert out["route_found"]
    rows = {r["key"]: r for r in out["rows"]}
    # the physical flow meter: both directions within 10 %, and the direction
    # asymmetry (gidiş burns more) reproduced
    assert abs(rows["gunduz_gidis_1"]["err_device"]) < 0.10
    assert abs(rows["gunduz_donus_1"]["err_device"]) < 0.10
    assert rows["gunduz_gidis_1"]["fuel_sim_l"] > rows["gunduz_donus_1"]["fuel_sim_l"]
    assert rows["gunduz_gidis_1"]["gear_agreement"] > 0.7
    assert abs(rows["gunduz_gidis_1"]["rpm_cruise_sim"] - rows["gunduz_gidis_1"]["rpm_cruise_meas"]) < 60


def test_campaign_endpoints_and_single_log_upload(demo_library, tmp_path):
    import shutil
    from temsasim.app.server import create_app
    root = tmp_path / "library"; shutil.copytree(demo_library, root)
    c = create_app(root).test_client()
    lst = c.get("/api/campaigns").get_json()["campaigns"]
    assert lst and lst[0]["name"] == "mersin_adana_2026_09"
    r = c.post("/api/campaign/mersin_adana_2026_09/run", json={"runs": ["gece_gidis_1"]})
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    assert j["rows"][0]["key"] == "gece_gidis_1" and "meas_fuel_lph" in j["overlays"]["gece_gidis_1"]
    assert abs(j["rows"][0]["err_device"]) < 0.10
    p = root / "testlog" / "mersin_adana_2026_09" / "gunduz_donus_1.csv"
    r = c.post("/api/correlate", data={"file": (io.BytesIO(p.read_bytes()), p.name), "vehicle": "maraton_13_dizel", "mass_kg": "18000",
                                       "ambient": "34.4", "headwind": "-1.9", "hvac_off": "1", "route": "mersin_adana_otoyol_gidis",
                                       "direction": "donus", "distance_km": "44.3", "fuel_device_l": "9.773"},
               content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    assert abs(j["metrics"]["fuel_error_device"]) < 0.10 and "sim_fuel_lph" in j["overlay"] and j["by_phase"]["cruise"]["share_time"] > 0.3
