"""VECTO mission cycles as library routes, and the signal explorer payload."""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

from temsasim.cycle import Environment, Route
from temsasim.library import Library
from temsasim.simulator import run
from temsasim.vecto_cycles import MISSIONS, import_missions, mission_route, mission_stats, read_vdri

ROOT = pathlib.Path(__file__).resolve().parents[1]
VECTO = ROOT / "vecto-stable" / "vecto-stable"
needs_vecto = pytest.mark.skipif(not VECTO.exists(), reason="VECTO source tree not present")


def test_vdri_reader_and_route_on_a_small_cycle(tmp_path):
    p = tmp_path / "Mini.vdri"
    p.write_text("<s>,<v>,<grad>,<stop>,<HW>\n0,0,1.0,2,0\n1,50,1.0,0,0\n500,50,-2.0,0,0\n1000,0,-2.0,30,0\n1001,80,0.0,0,1\n3000,80,0.0,0,1\n", encoding="utf-8")
    c = read_vdri(p)
    assert c["s"].tolist() == [0, 1, 500, 1000, 1001, 3000] and c["stop"].tolist() == [2, 0, 0, 30, 0, 0]
    r = mission_route(p, name="Mini")
    assert r.kind == "vecto" and r.distance_m[-1] == 3000 and r.distance_m[1] == 10
    assert r.stop_positions_m == [1000.0] and r.stops[1000.0]["dwell_s"] == 30.0          # the halt at the start is not a stop on the way
    # +1 % over the first 500 m, then -2 % to the stop, then level
    e = r.elevation_m - r.elevation_m[0]
    assert np.interp(500, r.distance_m, e) == pytest.approx(5.0, abs=0.02)
    assert np.interp(1000, r.distance_m, e) == pytest.approx(-5.0, abs=0.05)
    assert np.interp(3000, r.distance_m, e) == pytest.approx(-5.02, abs=0.05)
    # the limit at the stop is the speed the vehicle leaves with, never zero
    assert np.interp(400, r.distance_m, r.speed_limit_mps) * 3.6 == pytest.approx(50)
    assert r.speed_limit_mps.min() * 3.6 >= 50 and r.speed_limit_mps[-1] * 3.6 == pytest.approx(80)
    bad = tmp_path / "bad.vdri"
    bad.write_text("<t>,<v>\n0,0\n1,5\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_vdri(bad)


def test_library_carries_the_five_bus_missions(demo_library):
    want = {"vecto_coach": (91.7, 4, 100), "vecto_interurban": (123.6, 87, 85), "vecto_suburban": (23.5, 42, 67),
            "vecto_urban": (39.55, 119, 68), "vecto_heavy_urban": (30.5, 153, 63)}
    assert {v[0] for v in MISSIONS.values()} == set(want)
    for stem, (km, stops, vmax) in want.items():
        p = demo_library / "route" / f"{stem}.csv"
        assert p.read_text(encoding="utf-8").startswith("# VECTO görev çevrimi")
        r = Route.from_csv(p)
        st = mission_stats(r)
        assert r.kind == "vecto"
        assert st["length_km"] == pytest.approx(km, abs=0.1) and st["stops"] == stops and st["v_max_kmh"] == pytest.approx(vmax, abs=0.6)
        assert all(d["dwell_s"] > 0 for d in r.stops.values())


def test_maraton_drives_the_coach_mission(demo_library):
    veh = Library(demo_library).load_vehicle("maraton_13_dizel")
    r = Route.from_csv(demo_library / "route" / "vecto_coach.csv")
    quiet = r.to_cycle(vehicle=veh, occupancy=30.0, traffic="yok")
    busy = r.to_cycle(vehicle=veh, occupancy=30.0, traffic="yogun")
    assert busy.duration_s == quiet.duration_s and busy.stop_count == quiet.stop_count      # a mission takes no traffic on top
    assert quiet.distance_km == pytest.approx(91.7, abs=0.3) and quiet.stop_count >= 4
    res = run(veh, quiet, Environment(hvac_enabled=False))
    assert res.achievable and 17.0 < res.fuel_l_per_100km < 27.0
    assert res.totals["shift_strategy"] == "vecto_effshift" and res.totals["gear_share"][12] > 0.35
    assert 60 < res.totals["mean_speed_kmh"] < 75


@needs_vecto
def test_import_from_the_vecto_tree_matches_the_library(tmp_path):
    rows = import_missions(VECTO, tmp_path, missions=["Coach", "Suburban"])
    assert [r["stem"] for r in rows] == ["vecto_coach", "vecto_suburban"]
    for r in rows:
        a = (tmp_path / "route" / r["file"]).read_text(encoding="utf-8")
        b = (ROOT / "library" / "route" / r["file"]).read_text(encoding="utf-8")
        assert a == b, f"{r['file']}: the library copy is not what the importer writes"
    with pytest.raises(FileNotFoundError):
        import_missions(tmp_path, tmp_path)


def test_explorer_payload_names_the_signals_of_both_powertrains(demo_library):
    from temsasim.signals import explorer_html, explorer_payload
    lib = Library(demo_library)
    cyc = lib.load_cycle("cbdbus")
    p = None
    for stem, must, preset in (("maraton_13_dizel", {"gear", "engine_rpm", "l100_inst", "engine_torque_max", "clutch_open"}, "Vites ve devir"),
                               ("citybus_12m_be", {"soc", "battery_current", "motor_eff", "kwh_km_inst", "regen_kw"}, "Hız ve SOC")):
        res = run(lib.load_vehicle(stem), cyc, Environment())
        p = explorer_payload(res, max_points=300)
        keys = {s["key"] for s in p["signals"]}
        assert must <= keys and {"speed_kmh", "grade_pct", "p_wheel_kw", "cabin_c"} <= keys
        assert p["presets"][0][0] == preset and set(p["selected"]) <= keys
        n = len(p["x"][0]["values"])
        assert n <= 300 and all(len(s["values"]) == n for s in p["signals"]) and len(p["x"][1]["values"]) == n
        assert all(s["label"] and s["group"] for s in p["signals"])
        html = explorer_html(res)
        assert "TsimExplorer.mount" in html and html.count("<script>") == 2
    # instantaneous consumption is blank at a standstill, not a division by zero
    inst = next(s for s in p["signals"] if s["key"] == "kwh_km_inst")
    assert any(v is None for v in inst["values"]) and all(v is None or abs(v) < 50 for v in inst["values"])
