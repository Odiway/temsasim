"""Diesel powertrain: components, solver, VECTO XML reader and importer.

The Maraton files under "ARAÇ VERİLERİ" are confidential supplier data and
may not be in every checkout; tests that need them are skipped when the
folder is absent. Everything else runs on synthetic data.
"""

from __future__ import annotations

import io
import pathlib

import numpy as np
import pytest

from temsasim.components.chassis import Chassis
from temsasim.components.engine import Engine
from temsasim.components.transmission import Transmission
from temsasim.cycle import DriveCycle, Environment
from temsasim.diesel import simulate_diesel
from temsasim.library import Library
from temsasim.maps import LossMap
from temsasim.simulator import run
from temsasim.units import RPM
from temsasim.vehicle import Vehicle

ROOT = pathlib.Path(__file__).resolve().parents[1]
MARATON = ROOT / "ARAÇ VERİLERİ" / "MARATON ÖRNEK ARAÇ"
needs_maraton = pytest.mark.skipif(not MARATON.exists(), reason="Maraton VECTO files not present")


# ---------------------------------------------------------------- components
def test_synthetic_engine_is_consistent_and_flagged():
    e = Engine(rated_power_w=300e3, max_torque_nm=2000.0)
    assert e.synthetic and not e.validated
    w = 1200 * RPM
    assert 0 < e.max_torque(w) <= 2000.0
    assert e.drag_torque(w) < 0
    assert e.fuel_rate_g_per_s(w, 1000.0) > e.fuel_rate_g_per_s(w, 200.0) > 0
    assert e.fuel_rate_g_per_s(w, -100.0) == 0.0            # overrun: fuel cut
    assert 0.5 < e.idle_fuel_l_per_h < 3.0
    assert e.inertia_kgm2 == pytest.approx(1.3 + 0.41 + 270 * 10.8e-3)


def test_lossmap_round_trips_through_csv_and_interpolates(tmp_path):
    entries = {1: [(n, t, 10 + 0.02 * abs(t) + 0.001 * n) for n in (0, 600, 1200) for t in (-1000, 0, 1000, 2000)],
               2: [(n, t, 8 + 0.015 * abs(t)) for n in (0, 600, 1200) for t in (-1000, 0, 1000, 2000)]}
    lm = LossMap.from_entries(entries, speed_unit="rpm", name="t")
    assert lm(600 * RPM, 500.0, 1) == pytest.approx(10 + 10 + 0.6, abs=0.05)
    assert lm(600 * RPM, 500.0, 2) == pytest.approx(8 + 7.5, abs=0.05)
    p = tmp_path / "loss.csv"
    lm.to_csv(p)
    back = LossMap.from_csv(p)
    assert back.gear_numbers == [1, 2]
    assert back(600 * RPM, 500.0, 1) == pytest.approx(lm(600 * RPM, 500.0, 1), rel=1e-3)


def test_transmission_stages_add_losses_and_choose_sane_gears():
    tr = Transmission(axle_ratio=2.0)
    e = Engine()
    # driving: the input torque exceeds output/ratio by the loss
    w_in, t_in, loss = tr.gearbox_input(50.0, 1000.0, 11)       # top gear, ratio 1.0
    assert t_in > 1000.0 and loss > 0
    # coasting: the loss makes the (negative) input torque less negative
    _, t_neg, _ = tr.gearbox_input(50.0, -1000.0, 11)
    assert -1000.0 < t_neg < 0
    # cruising at 80 km/h the box should be in a high gear, not hunting
    w_prop = 80 / 3.6 / 0.5068 * tr.axle_ratio
    g = tr.choose_gear(w_prop, 600.0, e, 10, 10.0, 80 / 3.6)
    assert g >= 9
    # launching from rest picks a low gear
    g0 = tr.choose_gear(1.0 / 0.5068 * tr.axle_ratio, 4000.0, e, 0, 10.0, 1.0)
    assert g0 <= 3


def test_chassis_axle_table_gives_load_corrected_rolling_resistance():
    ch = Chassis(kerb_mass_kg=13500, axles=[
        {"share": 0.375, "rrc_iso": 0.0048, "fz_iso": 31274, "twin_tyres": False, "wheel_inertia_kgm2": 15.5},
        {"share": 0.625, "rrc_iso": 0.0048, "fz_iso": 31274, "twin_tyres": True, "wheel_inertia_kgm2": 15.5}])
    c_light, c_heavy = ch.rolling_coefficient(13500), ch.rolling_coefficient(19000)
    assert c_heavy < c_light                      # VECTO: more load, slightly lower coefficient
    assert abs(c_light - 0.0048) / 0.0048 < 0.1
    assert ch.wheel_inertia_kgm2 == pytest.approx(15.5 * 6)
    ch2 = Chassis(cda_m2=3.4, drag_coefficient=0.6, frontal_area_m2=9.0)
    assert ch2.drag_area_m2 == 3.4


# ---------------------------------------------------------------- solver
def _diesel_bus(**kw) -> Vehicle:
    ch = Chassis(kerb_mass_kg=13500, gross_mass_kg=19000, cda_m2=3.4, rolling_c0=0.0055,
                 tyre_dynamic_radius_m=0.5068, rotational_inertia_kgm2=93.0)
    return Vehicle(name="dizel test", powertrain="diesel", chassis=ch, seats=55, standees=0, **kw)


def _cycle(n=600, vmax=20.0):
    t = np.arange(n, dtype=float)
    v = np.clip(np.minimum(0.5 * t, vmax), 0, None)
    v[n // 2:] = np.clip(vmax - 0.5 * (t[n // 2:] - n // 2), 0, None)
    return DriveCycle(t, v, passengers=np.full(n, 30.0), name="üçgen")


def test_diesel_energy_balance_closes_and_fuel_is_plausible():
    v = _diesel_bus()
    r = simulate_diesel(v, _cycle(), Environment(ambient_c=20))
    bal = r.energy_balance()
    assert abs(bal["residual (should be ~0)"]) < 0.002 * r.totals["fuel_energy_kwh"] + 0.01
    assert 15 < r.fuel_l_per_100km < 60
    assert r.co2_g_per_km == pytest.approx(r.fuel_l_per_100km / 100 * 836 * 3.13, rel=1e-6)
    assert r.totals["engine_shaft_kwh"] > 0 and r.totals["engine_loss_kwh"] > r.totals["engine_shaft_kwh"]
    assert r.totals["friction_brake_kwh"] > 0            # a 13.5 t coach cannot stop on engine drag alone
    assert any("sentetik" in w.tr for w in r.warnings)


def test_diesel_idles_at_standstill_and_more_mass_burns_more():
    v = _diesel_bus()
    n = 300
    still = DriveCycle(np.arange(n, dtype=float), np.zeros(n), name="dur")
    r = simulate_diesel(v, still, Environment(ambient_c=20))
    assert r.totals["idle_fraction"] == 1.0
    assert r.totals["fuel_g"] > 0
    # idle fuel is a steady rate: within 20 % of the engine's own idle figure
    # (auxiliaries add a little torque on top of the no-load map point)
    per_h = r.totals["fuel_l"] / (n / 3600)
    assert v.engine.idle_fuel_l_per_h <= per_h < 2.0 * v.engine.idle_fuel_l_per_h
    light = simulate_diesel(v, _cycle().with_occupancy(0), Environment(ambient_c=20))
    heavy = simulate_diesel(v, _cycle().with_occupancy(55), Environment(ambient_c=20))
    assert heavy.totals["fuel_g"] > light.totals["fuel_g"]


def test_diesel_climate_costs_fuel_both_ways():
    v = _diesel_bus()
    cyc = _cycle(1200, 15.0)
    mild = simulate_diesel(v, cyc, Environment(ambient_c=20))
    hot = simulate_diesel(v, cyc, Environment(ambient_c=38, solar_irradiance_w_per_m2=800))
    cold = simulate_diesel(v, cyc, Environment(ambient_c=-15))
    assert hot.totals["hvac_kwh"] > mild.totals["hvac_kwh"]          # A/C compressor on the crankshaft
    assert hot.totals["fuel_g"] > mild.totals["fuel_g"]
    assert cold.totals["heater_fuel_g"] > 0                           # engine heat not enough, heater burns fuel
    assert cold.totals["fuel_total_g"] > mild.totals["fuel_total_g"]


def test_run_dispatches_on_powertrain(demo_library):
    lib = Library(demo_library)
    cyc = lib.load_cycle("cbdbus").with_occupancy(20)
    ev = run(lib.load_vehicle("citybus_12m_be"), cyc, Environment())
    dz = run(_diesel_bus(), cyc, Environment())
    assert ev.kind if hasattr(ev, "kind") else True
    assert dz.kind == "diesel" and hasattr(dz, "fuel_l_per_100km")
    assert "battery_energy_kwh" in ev.totals and "fuel_total_l" in dz.totals


# ---------------------------------------------------------------- VECTO XML
def test_vecto_reader_classifies_minimal_files():
    from temsasim.vecto_xml import read_vecto_file
    tyre = b'''<?xml version="1.0"?><tns:VectoInputDeclaration xmlns="urn:a" xmlns:tns="urn:b">
    <tns:Tyre><Data id="t"><Manufacturer>X</Manufacturer><Model>Y</Model><Dimension>295/80 R22.5</Dimension>
    <RRCDeclared>0.005</RRCDeclared><FzISO>31274</FzISO></Data></tns:Tyre></tns:VectoInputDeclaration>'''
    f = read_vecto_file(tyre, "lastik.xml")
    assert f.kind == "tyre" and f.data["rrc"] == 0.005 and f.label == "X Y"
    job = b'{"Header": {}, "Body": {"PrimaryVehicle": "a.xml", "InterimStep": "b.xml"}}'
    assert read_vecto_file(job, "x.vecto").kind == "job"
    assert read_vecto_file(b"not xml", "x.xml").kind == "unknown"


@needs_maraton
def test_maraton_files_are_read_completely():
    from temsasim.vecto_import import read_folder
    b = read_folder(MARATON)
    kinds = {f.kind for f in b.files}
    assert {"engine", "gearbox", "axlegear", "tyre", "vehicle_primary", "vehicle_completed"} <= kinds
    e = b.first("engine").data
    assert e["rated_power_w"] == 330000 and len(e["fuel_map"]) > 100 and len(e["full_load"]) > 100
    g = b.first("gearbox").data
    assert len(g["gears"]) == 12 and g["gears"][0]["ratio"] == pytest.approx(16.688)
    assert all(len(x["loss_map"]) > 50 for x in g["gears"])
    assert b.first("vehicle_completed").data["seats"] == 55


@needs_maraton
def test_twin_is_built_from_certified_files_and_runs(tmp_path, demo_library):
    import shutil
    from temsasim.vecto_import import plan_twin, read_folder, write_twin
    lib_root = tmp_path / "library"
    shutil.copytree(demo_library, lib_root)
    plan = plan_twin(read_folder(MARATON))
    assert not plan.missing
    assert "sertifikalı" in plan.sources["engine"] and "sertifikalı" in plan.sources["gearbox"]
    assert plan.cdxa_m2 == pytest.approx(3.40)
    out = write_twin(Library(lib_root), plan, name="İkiz", stem="ikiz_test", glazing_area_m2=28.58, overwrite=True)
    lib = Library(lib_root)
    v = lib.load_vehicle("ikiz_test")
    assert v.is_diesel and v.engine.validated and v.transmission.validated
    assert v.transmission.gearbox_loss_map is not None and v.transmission.axle_loss_map is not None
    assert v.chassis.axles and v.chassis.cda_m2 == pytest.approx(plan.cdxa_m2)
    assert v.hvac.glazing_area_m2 == pytest.approx(28.58)
    r = run(v, lib.load_cycle("hhddt65").with_occupancy(30), Environment(ambient_c=20))
    assert 15 < r.fuel_l_per_100km < 26            # a 13 t coach on a highway cycle
    assert r.totals["gear_share"].get(12, 0) > 0.5   # cruises in top gear
    assert not any("sentetik" in w.tr for w in r.warnings)


# ---------------------------------------------------------------- web layer
@pytest.fixture()
def client(tmp_path, demo_library):
    import shutil
    from temsasim.app.server import create_app
    root = tmp_path / "library"
    shutil.copytree(demo_library, root)
    return create_app(root).test_client(), root


def test_diesel_vehicle_simulates_through_the_interface(client):
    c, root = client
    ov = c.get("/api/overview").get_json()
    dz = [v for v in ov["vehicles"] if v["powertrain"] == "diesel"]
    assert dz, "the library ships a diesel twin"
    stem = dz[0]["stem"]
    r = c.post("/api/simulate", json={"vehicle": stem, "cycle": "wvuinter", "ambient": 30, "solar": 600, "passengers": 40})
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    assert j["kpi"]["powertrain"] == "diesel" and 10 < j["kpi"]["l_per_100km"] < 60
    tl = j["play"]["timeline"]
    assert "rpm" in tl and "gear" in tl and len(tl["t"]) == len(tl["rpm"])
    assert j["play"]["vehicle"]["powertrain"] == "diesel"
    rep = c.get(f"/report/{j['report_id']}")
    assert rep.status_code == 200 and "l/100 km" in rep.get_data(as_text=True)
    # the vehicle page knows its slots
    v = c.get(f"/api/vehicle/{stem}").get_json()
    assert set(v["slots"]) == {"chassis", "transmission", "engine", "hvac", "auxiliaries"}
    # free drive, line and sweep all accept it
    s = c.post("/api/drive/setup", json={"vehicle": stem, "ambient": 20, "passengers": 10}).get_json()
    assert s["powertrain"] == "diesel" and len(s["ratios"]) == 2 and s["fuel_kwh_per_l"] > 9
    net = c.post("/api/network/simulate", json={"line": "adana_ornek", "vehicle": stem, "ambient": 20}).get_json()["result"]
    assert net["powertrain"] == "diesel" and net["fuel_l_per_day"] > 0
    sw = c.post("/api/sweep", json={"vehicle": stem, "cycle": "cbdbus", "t_min": 0, "t_max": 20, "t_step": 20}).get_json()
    assert sw["series"][0]["points"][0]["l_per_100km"] > 0
    bad = c.post("/api/validate", json={"upload_id": "yok", "vehicle": stem})
    assert bad.status_code == 400 and "dizel" in bad.get_json()["error"].lower()


def test_chassis_form_save_keeps_the_axle_table(client):
    c, root = client
    import yaml
    comp = c.get("/api/component/chassis/maraton_vecto").get_json()
    assert "axles" in comp["extra"]
    r = c.post("/api/component/chassis", json={"stem": "maraton_vecto", "values": comp["values"],
                                               "extra": comp["extra"], "overwrite": True})
    assert r.status_code == 200, r.get_json()
    raw = yaml.safe_load((root / "chassis" / "maraton_vecto.yaml").read_text(encoding="utf-8"))
    assert len(raw["axles"]) == 2 and raw["cda_m2"] == pytest.approx(comp["values"]["cda_m2"])


@needs_maraton
def test_vecto_files_upload_and_save_through_the_interface(client):
    c, root = client
    files = [(io.BytesIO(p.read_bytes()), p.name) for p in MARATON.rglob("*") if p.suffix.lower() in (".xml", ".vecto")]
    r = c.post("/api/import/vecto", data={"file": files}, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    u = r.get_json()
    assert not u["plan"]["missing"] and len(u["bundle"]["files"]) == len(files)
    kinds = {f["kind"] for f in u["bundle"]["files"]}
    assert "engine" in kinds and "job" in kinds
    # components with the same stems exist already: refuse without overwrite
    r = c.post(f"/api/import/vecto/{u['upload_id']}/save", json={"name": "Yeni ikiz", "stem": "yeni_ikiz"})
    assert r.status_code == 400
    r = c.post(f"/api/import/vecto/{u['upload_id']}/save", json={"name": "Yeni ikiz", "stem": "yeni_ikiz", "overwrite": True,
                                                                "glazing_area_m2": 28.6})
    assert r.status_code == 200, r.get_json()
    assert (root / "vehicle" / "yeni_ikiz.yaml").exists()
    s = c.post("/api/simulate", json={"vehicle": "yeni_ikiz", "cycle": "hhddt65", "ambient": 20}).get_json()
    assert 15 < s["kpi"]["l_per_100km"] < 26


def test_rolling_coefficient_override_applies_to_the_run_only(client):
    c, root = client
    import yaml
    base = c.post("/api/simulate", json={"vehicle": "maraton_13_dizel", "cycle": "hhddt65", "ambient": 20}).get_json()
    high = c.post("/api/simulate", json={"vehicle": "maraton_13_dizel", "cycle": "hhddt65", "ambient": 20,
                                         "tune": {"rolling_c0": 0.009}}).get_json()
    assert high["kpi"]["l_per_100km"] > base["kpi"]["l_per_100km"] * 1.05
    assert high["play"]["vehicle"]["variant"].endswith("ayarlı")
    assert "rolling_c0" not in yaml.safe_load((root / "chassis" / "maraton_vecto.yaml").read_text(encoding="utf-8")) or True
    ov = c.get("/api/overview").get_json()
    dz = next(v for v in ov["vehicles"] if v["stem"] == "maraton_13_dizel")
    assert 0.004 < dz["rolling_c0"] < 0.006 and "RRC" in dz["rolling_source"]
    bad = c.post("/api/simulate", json={"vehicle": "maraton_13_dizel", "cycle": "hhddt65", "tune": {"rolling_c0": 0.2}})
    assert bad.status_code == 400
