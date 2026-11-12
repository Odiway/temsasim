"""Route builder specs and saved scenarios: build, save, reload, run, endpoints."""

from __future__ import annotations

import pathlib
import shutil

import numpy as np
import pytest

from temsasim.cycle import Route
from temsasim.library import Library
from temsasim.routes import RouteSpec, Segment, from_route_csv, list_routes, load_spec, save_spec, template
from temsasim.scenarios import Scenario, list_scenarios, load_scenario, run_scenario, save_scenario

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture()
def lib_root(tmp_path, demo_library):
    dst = tmp_path / "library"
    shutil.copytree(demo_library, dst)
    return dst


def test_spec_builds_grid_with_stops_signals_surface_and_wind():
    spec = RouteSpec(name="Tepe hattı", kind="sehir_ici", start_elevation_m=10.0, segments=[
        Segment(500, 4.0, 50, "asfalt", 1.0, {"name": "A", "dwell_s": 25, "board": 10, "alight": 0}),
        Segment(500, -4.0, 50, "parke", -1.0, None, signal=True),
        Segment(1000, 0.0, 30, "eski_asfalt", 0.0, {"name": "Son", "dwell_s": 10, "board": 0, "alight": 10})])
    b = spec.build()
    assert b["totals"]["length_km"] == pytest.approx(2.0) and b["totals"]["climb_m"] == pytest.approx(20.0)
    assert b["elevation_m"][50] == pytest.approx(30.0, abs=0.5)        # top of the climb at 500 m
    assert len(b["stops"]) == 2 and b["signals"] == [1000.0]
    assert b["surface_factor"][75] == pytest.approx(1.40) and b["headwind_mps"][25] == 1.0 and b["headwind_mps"][75] == -1.0


def test_spec_round_trips_through_the_library_and_the_solver_reads_everything(lib_root):
    spec = template("sehir_ici", stops=6)
    out = save_spec(lib_root, spec)
    assert (lib_root / "route" / out["csv"].split("/")[1]).exists() and (lib_root / "route" / out["yaml"].split("/")[1]).exists()
    back = load_spec(lib_root, out["stem"])
    assert back.kind == "sehir_ici" and len(back.segments) == 6 and back.segments[0].stop["name"] == "Durak 1"
    r = Route.from_csv(lib_root / "route" / f"{out['stem']}.csv")
    assert len(r.stop_positions_m) == 6 and len(r.signal_positions_m) == 3 and r.stops and r.dwell_at(r.stop_positions_m[0]) == 20.0
    occ = r.occupancy_from_stops()
    assert occ is not None and occ(0.5) > 0
    rr = r.reversed()
    assert rr.distance_m[-1] == pytest.approx(r.distance_m[-1]) and rr.elevation_m[0] == pytest.approx(r.elevation_m[-1])
    with pytest.raises(FileExistsError):
        save_spec(lib_root, spec)
    items = {x["stem"]: x for x in list_routes(lib_root)}
    assert items[out["stem"]]["editable"] and items[out["stem"]]["kind"] == "sehir_ici"
    assert not items["mersin_adana_otoyol_gidis"]["editable"]
    copy = from_route_csv(lib_root, "mersin_adana_otoyol_gidis")
    assert len(copy.segments) > 50 and abs(copy.build()["totals"]["length_km"] - 44.3) < 0.2


def test_route_wind_and_per_stop_dwell_reach_the_cycle(lib_root):
    spec = RouteSpec(name="rüzgâr", segments=[Segment(2000, 0.0, 60, "asfalt", 3.0, {"name": "x", "dwell_s": 45, "board": 5, "alight": 0}),
                                               Segment(2000, 0.0, 60, "asfalt", -3.0)])
    save_spec(lib_root, spec)
    r = Route.from_csv(lib_root / "route" / f"{spec.stem}.csv")
    cyc = r.to_cycle(occupancy="stops")
    assert cyc.headwind_mps.max() == pytest.approx(3.0) and cyc.headwind_mps.min() == pytest.approx(-3.0)
    dwell = int(np.sum(cyc.doors_open))
    assert 40 <= dwell <= 50
    assert cyc.passengers.max() == pytest.approx(5.0)


def test_scenario_runs_both_powertrains_on_the_same_trace(lib_root):
    lib = Library(lib_root)
    sc = Scenario(name="Kış zirve", kind="sehir_ici", duty={"route": "demo_hilly_city", "laps": 1},
                  vehicles=["citybus_12m_be", "maraton_13_dizel"],
                  conditions={"ambient_c": -5, "solar": 0, "night": True, "hvac_off": False, "headwind_mps": 2.0},
                  load={"mode": "profile", "passengers": 50}, traffic="orta")
    p = save_scenario(lib_root, sc)
    assert p.exists() and load_scenario(lib_root, sc.stem).vehicles == sc.vehicles
    assert list_scenarios(lib_root)[0]["summary"].startswith("demo_hilly_city")
    out = run_scenario(lib, sc)
    rows = {r["vehicle"]: r for r in out["rows"]}
    assert rows["citybus_12m_be"]["powertrain"] == "battery_electric" and "battery_kwh" in rows["citybus_12m_be"]
    assert rows["maraton_13_dizel"]["powertrain"] == "diesel" and rows["maraton_13_dizel"]["l_per_100km"] > 20
    assert rows["citybus_12m_be"]["distance_km"] == pytest.approx(rows["maraton_13_dizel"]["distance_km"], rel=0.02)
    # the mass override replaces kerb mass for a test replay
    sc2 = Scenario(name="test kütlesi", duty={"route": "mersin_adana_otoyol_gidis", "direction": "donus"}, vehicles=["maraton_13_dizel"],
                   conditions={"ambient_c": 30, "hvac_off": True}, load={"mode": "mass", "mass_kg": 18000})
    light = run_scenario(lib, Scenario(name="boş", duty=sc2.duty, vehicles=sc2.vehicles, conditions=sc2.conditions, load={"mode": "constant", "passengers": 0}))
    heavy = run_scenario(lib, sc2)
    assert heavy["rows"][0]["fuel_l"] > light["rows"][0]["fuel_l"]


def test_route_and_scenario_endpoints(lib_root):
    from temsasim.app.server import create_app
    c = create_app(lib_root).test_client()
    t = c.get("/api/route/template/otoyol").get_json()
    assert t["preview"]["totals"]["length_km"] == pytest.approx(45.0)
    spec = t["spec"]; spec["name"] = "Otoyol denemesi"; spec["stem"] = ""
    pv = c.post("/api/route/preview", json=spec).get_json()
    assert pv["preview"]["totals"]["stops"] == 1 and pv["stem"] == "otoyol_denemesi"
    r = c.post("/api/route", json=spec); assert r.status_code == 200, r.get_json()
    assert c.post("/api/route", json=spec).status_code == 400          # exists, no overwrite
    assert c.post("/api/route", json={**spec, "overwrite": True}).status_code == 200
    g = c.get("/api/route/otoyol_denemesi").get_json(); assert g["editable"] and len(g["spec"]["segments"]) == 6
    g2 = c.get("/api/route/demo_hilly_city").get_json(); assert not g2["editable"] and g2["spec"]["segments"]
    ov = c.get("/api/overview").get_json()
    assert any(x["stem"] == "otoyol_denemesi" for x in ov["route_items"]) and ov["surfaces"] and ov["load_modes"]
    sc = {"name": "Otoyol 18 t", "scenario_kind": "otoyol", "duty": {"route": "otoyol_denemesi", "direction": "gidis", "laps": 1},
          "vehicles": ["maraton_13_dizel"], "conditions": {"ambient_c": 30, "hvac_off": True, "headwind_mps": 0}, "load": {"mode": "mass", "mass_kg": 18000}, "traffic": "yok"}
    s = c.post("/api/scenario", json=sc); assert s.status_code == 200, s.get_json()
    run = c.post("/api/scenario/run", json={"stem": s.get_json()["stem"]}).get_json()
    assert run["rows"][0]["l_per_100km"] > 10 and c.get(f"/report/{run['rows'][0]['report_id']}").status_code == 200
    run2 = c.post("/api/scenario/run", json={"scenario": sc}).get_json(); assert run2["rows"][0]["vehicle"] == "maraton_13_dizel"
    assert c.get("/api/scenario/otoyol_18_t").get_json()["summary"]
    bad = c.post("/api/route/otoyol_denemesi/delete"); assert bad.status_code == 400     # used by a scenario
    assert c.post("/api/scenario/otoyol_18_t/delete").status_code == 200
    assert c.post("/api/route/otoyol_denemesi/delete").status_code == 200
    assert not (lib_root / "route" / "otoyol_denemesi.csv").exists()
