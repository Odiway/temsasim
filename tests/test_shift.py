"""VECTO EffShift port: full-load characteristics, shift polygons, the plan on
synthetic traces and on the Mersin-Adana logs, and the set-point calibration."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from temsasim import shift, shiftcal
from temsasim.cycle import DriveCycle, Environment
from temsasim.diesel import simulate_diesel
from temsasim.library import Library
from temsasim.shift import ShiftPolygon, ShiftSetPoints, engine_characteristics
from temsasim.units import KMH, RPM


@pytest.fixture(scope="module")
def lib(demo_library):
    return Library(demo_library)


@pytest.fixture()
def maraton(lib):
    v = lib.load_vehicle("maraton_13_dizel")
    # an own copy of the gearbox: these tests switch strategies and set points
    return replace(v, transmission=replace(v.transmission, shift_strategy="vecto_effshift", shift_setpoints=None))


def _trace(speeds_kmh, hold_s=1):
    v = np.repeat(np.asarray(speeds_kmh, dtype=float) * KMH, hold_s)
    return DriveCycle(np.arange(v.size, dtype=float), v, name="test")


def _launch_cruise_stop(v_kmh=90.0, accel=0.6, cruise_s=120, decel=0.8):
    up = np.arange(0.0, v_kmh * KMH, accel)
    down = np.arange(v_kmh * KMH, 0.0, -decel)
    v = np.concatenate([np.zeros(5), up, np.full(cruise_s, v_kmh * KMH), down, np.zeros(10)])
    return DriveCycle(np.arange(v.size, dtype=float), v, name="launch-cruise-stop")


# ---------------------------------------------------------------- characteristics
def test_characteristic_speeds_of_a_simple_curve():
    # torque 1000 Nm flat to 150 rad/s, then falling linearly to 500 Nm at 250 rad/s
    x = np.array([50.0, 100.0, 150.0, 250.0])
    y = np.array([600.0, 1000.0, 1000.0, 500.0])
    c = engine_characteristics(x, y, -np.array([50.0, 60.0, 70.0, 90.0]))
    assert c.max_torque_nm == 1000.0 and c.max_drag_torque_nm == -90.0
    # on the falling branch T = 1750 - 5 w, so P = 1750 w - 5 w^2 peaks at w = 175
    assert c.rated_speed == pytest.approx(175.0) and c.max_power_w == pytest.approx(153125.0)
    assert c.n_tq99l == pytest.approx(50.0 + 50.0 * (990.0 - 600.0) / 400.0)       # first crossing of 990 Nm
    assert c.n_tq98h == pytest.approx(150.0 + 100.0 * 20.0 / 500.0)                # last crossing of 980 Nm
    # 98 % of max power above rated speed: 1750 w - 5 w^2 = 0.98 * 153125
    assert c.n_p98h == pytest.approx((1750.0 + np.sqrt(1750.0 ** 2 - 20.0 * 0.98 * 153125.0)) / 10.0)
    assert c.rated_speed < c.n_p98h < c.n_95h


def test_maraton_engine_characteristics(maraton):
    e = maraton.engine
    c = engine_characteristics(e.full_load_curve.x, e.full_load_curve.y, e.drag_curve.y).as_rpm()
    assert c["max_torque_nm"] == pytest.approx(2353, abs=2) and c["max_power_kw"] == pytest.approx(328.5, abs=1)
    assert c["n_tq99l_rpm"] == pytest.approx(900, abs=5) and c["n_tq98h_rpm"] == pytest.approx(1284, abs=5)
    assert c["rated_speed_rpm"] == pytest.approx(1598, abs=10) and c["n_p98h_rpm"] == pytest.approx(1634, abs=5)
    assert c["n_95h_rpm"] == pytest.approx(1674, abs=5)


# ---------------------------------------------------------------- polygon
def test_shift_polygon_sides_follow_vecto():
    # downshift line: vertical at 70 rad/s up to 800 Nm, sloping to (100 rad/s, 1000 Nm), vertical above
    p = ShiftPolygon(down=[(-100.0, 70.0), (800.0, 70.0), (1000.0, 100.0), (1200.0, 100.0)],
                     up=[(-100.0, 170.0), (1200.0, 170.0)])
    assert p.is_below_downshift(300.0, 60.0) and p.is_below_downshift(-50.0, 60.0)      # slower than the line: any torque
    assert not p.is_below_downshift(300.0, 85.0)                                        # under the sloping part
    assert p.is_below_downshift(950.0, 85.0)                                            # above it (line is 900 Nm there)
    assert not p.is_below_downshift(1100.0, 120.0)                                      # faster than the line's end
    assert p.is_above_downshift(300.0, 85.0) and not p.is_above_downshift(950.0, 85.0)
    assert p.is_above_downshift(500.0, 120.0)
    assert p.is_above_upshift(200.0, 171.0) and not p.is_above_upshift(200.0, 169.0)
    assert not p.is_above_upshift(200.0, 170.0)                                         # on the line is not beyond it
    assert p.interpolate_downshift(85.0) == pytest.approx(900.0)
    assert np.isinf(p.interpolate_downshift(60.0)) and np.isinf(p.interpolate_downshift(130.0))
    # array form agrees with the scalar form
    t = np.array([300.0, 950.0, 1100.0]); n = np.array([60.0, 85.0, 120.0])
    assert p.is_below_downshift(t, n).tolist() == [True, True, False]


def test_efficiency_polygons_of_the_maraton(maraton):
    tb = shift.build_table(maraton, _trace([0, 20, 40]), Environment())
    polys = tb.polygons(ShiftSetPoints())
    assert len(polys) == 12
    assert polys[0].down.shape[0] == 0 and polys[11].up.shape[0] == 0          # no downshift from 1st, no upshift from 12th
    d = polys[5].as_rpm()
    # p2 = min(clutch closing speed - 10 rpm, 1.1 x idle) = min(600 + 0.065 x 998 - 10, 660)
    assert d["down"][0][1] == pytest.approx(654.9, abs=0.5) and d["down"][0][0] < 0
    assert d["down"][-1][1] == pytest.approx(900, abs=5) and d["down"][-1][0] == pytest.approx(1.1 * 2352.8, rel=1e-3)
    assert d["up"][0][1] == d["up"][1][1] == pytest.approx(1634, abs=5)
    # the sloping part hugs the full-load curve from below
    e = maraton.engine
    for torque, rpm in d["down"][2:-2]:
        assert 0.95 * e.max_torque(rpm * RPM) < torque < 0.985 * e.max_torque(rpm * RPM)
    # overrides move the lines
    moved = tb.polygons(ShiftSetPoints(downshift_low_speed_rpm=800, downshift_high_speed_rpm=1000, upshift_speed_rpm=1500))[5].as_rpm()
    assert moved["down"][0][1] == pytest.approx(800) and moved["down"][-1][1] == pytest.approx(1000)
    assert moved["up"][0][1] == pytest.approx(1500)
    # a hand-made polygon (VECTO .vgbs rows) replaces the computed one for that gear
    maraton.transmission.shift_polygons = {6: [[-200, 700, 1400], [2400, 1100, 1700]]}
    custom = tb.polygons(ShiftSetPoints())[5].as_rpm()
    assert custom["down"] == [[-200.0, pytest.approx(700.0)], [2400.0, pytest.approx(1100.0)]]


def test_max_start_gear_is_third_for_the_traxon(maraton):
    tb = shift.build_table(maraton, _trace([0, 5, 10]), Environment())
    assert shift.max_start_gear(tb, ShiftSetPoints()) + 1 == 3                  # 8 km/h: 831 rpm in 3rd, 644 in 4th
    assert shift.max_start_gear(tb, ShiftSetPoints(start_speed_kmh=13.5)) + 1 == 5
    assert shift.max_start_gear(tb, ShiftSetPoints(launch_gear_cap=2)) + 1 == 2


# ---------------------------------------------------------------- plan
def test_plan_pulls_away_in_third_shifts_up_and_opens_the_clutch_to_halt(maraton):
    cyc = _launch_cruise_stop()
    res = simulate_diesel(maraton, cyc, Environment(hvac_enabled=False))
    plan = res.shift_plan
    assert plan is not None and res.totals["shift_strategy"] == "vecto_effshift"
    g = res.series["gear"].astype(int)
    moving = res.series["speed_mps"] > 0.05
    first = int(np.argmax(moving))
    assert g[first] == 3 and plan.events[0]["reason"] == "launch"
    cruise = slice(first + 60, first + 150)
    assert set(g[cruise]) == {12}                                               # top gear on the motorway
    assert 915 < res.series["engine_speed_rads"][cruise].mean() / RPM < 940     # 90 km/h in 12th
    ups = [e for e in plan.events if e["to"] > e["from"] > 0]
    assert all(e["reason"] in ("eff_up", "polygon_up") for e in ups) and len(ups) >= 5
    assert max(e["to"] - e["from"] for e in ups) <= 2 or any(e["reason"] == "polygon_up" for e in ups)
    assert res.unmet_samples == 0 and res.achievable
    # rolling to the halt the clutch opens and the engine idles on fuel instead of the fuel cut
    opened = res.series["clutch_open"] > 0
    assert opened.any() and np.all(res.series["speed_mps"][opened] < 10 * KMH + 1e-9)
    assert np.all(res.series["fuel_g_per_s"][opened] > 0)
    assert np.allclose(res.series["engine_speed_rads"][opened], maraton.engine.idle_speed_rads)
    # the balance still closes, clutch-open samples included
    bal = res.energy_balance()
    assert abs(bal["residual (should be ~0)"]) < 0.01 * res.totals["fuel_energy_kwh"]


def test_window_strategy_is_untouched_and_differs(maraton):
    cyc = _launch_cruise_stop()
    old = simulate_diesel(replace(maraton, transmission=replace(maraton.transmission, shift_strategy="window")),
                          cyc, Environment(hvac_enabled=False))
    assert old.shift_plan is None and old.totals["shift_strategy"] == "window" and old.totals["shift_reasons"] == {}
    new = simulate_diesel(maraton, cyc, Environment(hvac_enabled=False))
    assert sum(new.totals["shift_reasons"].values()) > 0
    assert new.totals["fuel_total_l"] == pytest.approx(old.totals["fuel_total_l"], rel=0.08)


def test_set_points_change_the_behaviour_they_name(maraton):
    cyc = _launch_cruise_stop(v_kmh=80.0, accel=0.5)
    env = Environment(hvac_enabled=False)

    def run(**sp):
        veh = replace(maraton, transmission=replace(maraton.transmission, shift_setpoints=sp))
        return simulate_diesel(veh, cyc, env)

    base = run()
    # a floor on the engine speed after an upshift delays the upshifts
    late = run(min_engine_speed_post_upshift_rpm=1000)
    acc = slice(5, 60)
    assert late.series["engine_speed_rads"][acc].mean() > base.series["engine_speed_rads"][acc].mean() + 50 * RPM
    post = [e for e in late.shift_plan.events if e["reason"] == "eff_up"]
    assert post and all(e["rpm"] * maraton.transmission.gear_ratios[e["to"] - 1] / maraton.transmission.gear_ratios[e["from"] - 1] > 930 for e in post)
    # the clutch-protection cap pulls away in a lower gear
    capped = run(launch_gear_cap=1)
    assert capped.shift_plan.events[0]["to"] == 1 and capped.shift_plan.max_start_gear == 1
    # one gear at a time when the range is one
    single = run(allowed_gear_range_fc=1)
    assert max(e["to"] - e["from"] for e in single.shift_plan.events if e["reason"] == "eff_up") == 1
    # braking downshifts keep the engine turning for the engine brake
    brake = run(brake_downshift_speed_rpm=1100, brake_downshift_min_decel_mps2=0.3)
    assert any(e["reason"] == "brake_down" for e in brake.shift_plan.events)
    dec = slice(-36, -14)
    assert brake.series["engine_speed_rads"][dec].mean() > base.series["engine_speed_rads"][dec].mean() + 80 * RPM


def test_kickdown_meets_the_demand_on_a_generated_trace_only(maraton):
    # 85 km/h on a 6 % climb is more than 12th gear can pull at 900 rpm
    n = 160
    v = np.full(n, 85.0 * KMH)
    cyc = DriveCycle(np.arange(n, dtype=float), v, grade_rad=np.full(n, np.arctan(0.06)), name="climb")
    env = Environment(hvac_enabled=False)
    res = simulate_diesel(maraton, cyc, env)
    assert "kickdown" in res.totals["shift_reasons"] or res.series["gear"][-1] < 12
    assert res.series["gear"][-20:].max() < 12 and res.unmet_samples <= 3
    measured = DriveCycle(np.arange(n, dtype=float), v, grade_rad=np.full(n, np.arctan(0.06)), name="log", meta={"measured": True})
    assert shift.for_trace(ShiftSetPoints(), measured).kickdown is False
    assert shift.for_trace(ShiftSetPoints(), cyc).kickdown is True
    assert "kickdown" not in simulate_diesel(maraton, measured, env).totals["shift_reasons"]


def test_set_points_round_trip_through_the_library(lib, demo_library, tmp_path):
    import shutil
    import yaml
    root = tmp_path / "library"
    shutil.copytree(demo_library, root)
    p = root / "transmission" / "zf_12_tx_2210_td_a_133_203.yaml"
    d = yaml.safe_load(p.read_text(encoding="utf-8"))
    d["shift_strategy"] = "vecto_effshift"
    d["shift_setpoints"] = {"min_engine_speed_post_upshift_rpm": 870, "max_skip_down": 2, "kickdown": False}
    p.write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tr = Library(root).load_vehicle("maraton_13_dizel").transmission
    sp = shift.setpoints_of(tr)
    assert tr.shift_strategy == "vecto_effshift" and sp.min_engine_speed_post_upshift_rpm == 870.0
    assert sp.max_skip_down == 2 and sp.kickdown is False
    assert sp.changed() == {"min_engine_speed_post_upshift_rpm": 870.0, "max_skip_down": 2, "kickdown": False}
    assert set(shift.SET_POINT_INFO) == set(ShiftSetPoints().to_dict())           # every set point is documented


# ---------------------------------------------------------------- calibration
def test_calibration_improves_the_agreement_with_the_logged_gears(lib):
    veh, runs = shiftcal.prepare_campaign(lib, "mersin_adana_2026_09", runs=["gunduz_gidis_1", "gece_gidis_2"])
    assert len(runs) == 2 and runs[0].meas_gear.max() == 12
    before = shiftcal.evaluate(runs, ShiftSetPoints())
    assert 0.6 < before["agreement"] < 0.85 and before["launches"] >= 6
    cal = shiftcal.calibrate(runs, ShiftSetPoints(), budget_s=25,
                             params=["min_engine_speed_post_upshift_rpm", "brake_downshift_speed_rpm", "downshift_low_speed_rpm"])
    a = cal.after
    assert a["objective"] < before["objective"] - 0.05
    assert a["agreement"] > before["agreement"] + 0.05 and a["agreement"] > 0.85
    assert a["rpm_rmse"] < before["rpm_rmse"] and a["within_one"] > 0.95
    assert 750 <= cal.setpoints.min_engine_speed_post_upshift_rpm <= 1000        # the TraXon upshifts to about 900 rpm
    assert cal.history and set(cal.sensitivity) >= {"min_engine_speed_post_upshift_rpm"}
    assert cal.sensitivity["min_engine_speed_post_upshift_rpm"] > 0.02
    tr = shiftcal.run_traces(runs[0], shiftcal.evaluate(runs[:1], cal.setpoints, detail=True)["plans"][0])
    assert len(tr["gear_sim"]) == len(tr["gear_meas"]) == len(tr["time_s"]) and tr["events"]


def test_calibrated_strategy_keeps_the_fuel_correlation(lib):
    from temsasim.testlog import run_campaign
    veh = lib.load_vehicle("maraton_13_dizel")
    veh = replace(veh, transmission=replace(veh.transmission, shift_strategy="vecto_effshift", shift_setpoints={
        "min_engine_speed_post_upshift_rpm": 870, "brake_downshift_speed_rpm": 950, "brake_downshift_min_decel_mps2": 0.4,
        "time_between_gearshifts_s": 1.0, "downshift_after_upshift_delay_s": 1.0, "torque_reserve": 0.25}))
    out = run_campaign(lib, "mersin_adana_2026_09", vehicle=veh, runs=["gunduz_gidis_1", "gunduz_donus_1"])
    for r in out["rows"]:
        assert r["gear_agreement"] > 0.84                                       # was 0.74 / 0.83 with the window rules
        assert abs(r["rpm_cruise_sim"] - r["rpm_cruise_meas"]) < 15              # was 35 rpm high on the outbound run
        assert abs(r["err_device"]) < 0.12


# ---------------------------------------------------------------- interface
def test_shift_endpoints_evaluate_calibrate_preview_and_save(tmp_path):
    from conftest import merged_library
    from temsasim.app.server import create_app
    root = merged_library(tmp_path / "library")
    c = create_app(root).test_client()
    ov = c.get("/api/overview").get_json()
    mar = next(v for v in ov["vehicles"] if v["stem"] == "maraton_13_dizel")
    assert mar["gears"] == 12 and mar["campaigns"] == 1 and mar["shift_strategy"] == "vecto_effshift"
    info = c.get("/api/shift/maraton_13_dizel").get_json()
    assert info["max_start_gear"] == 3 and len(info["polygons"]) == 12 and info["transmission"]["strategy"] == "vecto_effshift"
    assert set(info["info"]) == set(info["defaults"]) and info["campaigns"][0]["name"] == "mersin_adana_2026_09"
    assert info["setpoints"]["min_engine_speed_post_upshift_rpm"] == 870 and info["defaults"]["min_engine_speed_post_upshift_rpm"] == 0
    assert info["auto"]["downshift_high_speed_rpm"] == pytest.approx(900, abs=5)
    body = {"campaign": "mersin_adana_2026_09", "strategy": "vecto_effshift"}
    saved = c.post("/api/shift/maraton_13_dizel/evaluate", json={**body, "setpoints": info["setpoints"], "run": "gece_gidis_1"}).get_json()
    vecto = c.post("/api/shift/maraton_13_dizel/evaluate", json={**body, "setpoints": {}}).get_json()
    assert saved["run"] == "gece_gidis_1" and len(saved["trace"]["gear_sim"]) == len(saved["trace"]["gear_meas"])
    assert saved["agreement"] > 0.87 > vecto["agreement"] > 0.7 and saved["launch_agreement"] == 1.0
    assert saved["rpm_rmse"] < vecto["rpm_rmse"] and saved["within_one"] > 0.97
    # moving a line moves the polygon the answer carries
    moved = c.post("/api/shift/maraton_13_dizel/polygons", json={"setpoints": {"upshift_speed_rpm": 1500, "launch_gear_cap": 2}}).get_json()
    assert moved["polygons"][5]["up"][0][1] == pytest.approx(1500) and moved["max_start_gear"] == 2
    cal = c.post("/api/shift/maraton_13_dizel/calibrate", json={**body, "setpoints": {}, "budget_s": 8, "runs": ["gunduz_gidis_1"],
                                                               "params": ["min_engine_speed_post_upshift_rpm"]}).get_json()
    assert cal["after"]["agreement"] > cal["before"]["agreement"] and 700 <= cal["changed"]["min_engine_speed_post_upshift_rpm"] <= 1000
    pre = c.post("/api/shift/maraton_13_dizel/preview", json={"strategy": "vecto_effshift", "setpoints": cal["setpoints"],
                                                             "constant": {"speed_kmh": 90, "grade_pct": 0, "distance_km": 3}}).get_json()
    assert pre["kpi"]["gear_share"]["12"] > 0.5 and pre["trace"]["gear_meas"] is None and pre["reasons"]["launch"] == 1
    # saving writes the transmission file and nothing else in it changes
    path = root / "transmission" / "zf_12_tx_2210_td_a_133_203.yaml"
    before = path.read_text(encoding="utf-8")
    r = c.post("/api/shift/maraton_13_dizel/save", json={"strategy": "vecto_effshift", "setpoints": {"max_skip_down": 2}, "note": "deneme"})
    assert r.status_code == 200 and r.get_json()["setpoints"] == {"max_skip_down": 2} and r.get_json()["vehicles"] == ["maraton_13_dizel"]
    after = path.read_text(encoding="utf-8")
    assert after.split("# vites mantığı:")[0] == before.split("# vites mantığı:")[0]
    assert after.rstrip().splitlines()[-2:] == ["shift_setpoints:", "  max_skip_down: 2"] and after.count("shift_strategy:") == 1
    assert c.get("/api/shift/maraton_13_dizel").get_json()["setpoints"]["max_skip_down"] == 2
    r = c.post("/api/shift/maraton_13_dizel/save", json={"strategy": "bilinmeyen", "setpoints": {}})
    assert r.status_code == 400 and path.read_text(encoding="utf-8") == after
    assert c.get("/api/shift/citybus_12m_be").status_code == 400                 # fixed-ratio electric driveline
    # the signals of a finished run for the explorer
    run = c.post("/api/simulate", json={"vehicle": "maraton_13_dizel", "constant": {"speed_kmh": 60, "grade_pct": 0, "distance_km": 1}}).get_json()
    sig = c.get(f"/report/{run['report_id']}/signals").get_json()
    assert {"gear", "engine_rpm", "l100_inst"} <= {x["key"] for x in sig["signals"]}
    html = c.get(f"/report/{run['report_id']}").data.decode("utf-8")
    assert "Sinyal inceleme" in html and "Vites değişimleri" in html and "TsimExplorer.mount" in html
