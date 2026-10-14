"""Tests that pin down the physics, not the implementation.

Most of these assert a *property* rather than a number: energy closes, a colder
pack accepts less regen, a heat pump beats a resistive heater. Properties
survive recalibration; golden numbers do not, and a test suite full of golden
numbers is one that gets deleted the first time someone updates a component map.

The two exceptions are marked as plausibility bands. They exist to catch the
class of bug where a unit slips by a factor of 3.6 or 1000 and every property
test still passes because everything scaled together.
"""

from __future__ import annotations

import math
import pathlib

import numpy as np
import pytest

from temsasim.components.battery import BatteryPack
from temsasim.components.chassis import Chassis
from temsasim.components.hvac import HVAC
from temsasim.cycle import DriveCycle, Environment, Route
from temsasim.library import Library, LibraryError
from temsasim.maps import Curve1D, Map2D, synth_motor_efficiency
from temsasim.simulator import simulate
from temsasim.units import G

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def lib():
    return Library(ROOT / "library")


@pytest.fixture(scope="module")
def bus(lib):
    return lib.load_vehicle("citybus_12m_be")


@pytest.fixture(scope="module")
def cycle(lib):
    return lib.load_cycle("cbdbus").repeat(3).with_occupancy(40)


# --------------------------------------------------------------------- maps
def test_map_calibrates_to_its_peak_exactly():
    # Place the calibration point on a grid node so this tests the loss-model
    # calibration itself, not bilinear interpolation error between nodes.
    speeds = np.union1d(np.linspace(1, 300, 60), [120.0])
    torques = np.union1d(np.linspace(1, 2500, 60), [900.0])
    m = synth_motor_efficiency(speeds, torques, peak_efficiency=0.95,
                               peak_at_speed_rads=120.0, peak_at_torque_nm=900.0)
    assert m(120.0, 900.0) == pytest.approx(0.95, abs=1e-9)


def test_map_interpolation_error_is_small_off_grid():
    m = synth_motor_efficiency(np.linspace(1, 300, 60), np.linspace(1, 2500, 60),
                               peak_efficiency=0.95, peak_at_speed_rads=120.0,
                               peak_at_torque_nm=900.0)
    assert m(120.0, 900.0) == pytest.approx(0.95, abs=5e-4)


def test_map_efficiency_falls_off_at_light_load_and_high_torque():
    m = synth_motor_efficiency(np.linspace(1, 300, 60), np.linspace(1, 2500, 60),
                               peak_efficiency=0.95, peak_at_speed_rads=120.0,
                               peak_at_torque_nm=900.0)
    assert m(250.0, 100.0) < m(120.0, 900.0)   # constant losses dominate
    assert m(40.0, 2400.0) < m(120.0, 900.0)   # copper losses dominate


def test_map_clamps_rather_than_extrapolating():
    m = Map2D([0.0, 1.0], [0.0, 1.0], [[0.1, 0.2], [0.3, 0.4]])
    assert m(-99.0, -99.0) == pytest.approx(0.1)
    assert m(99.0, 99.0) == pytest.approx(0.4)


def test_map_rejects_mismatched_grid():
    with pytest.raises(ValueError):
        Map2D([0.0, 1.0], [0.0, 1.0, 2.0], [[1.0, 2.0], [3.0, 4.0]])


# ------------------------------------------------------------------ library
def test_unit_aliases_convert(lib):
    motor = lib.load_component("motor/central_250kw_pmsm.yaml", "motor")
    assert motor.peak_power_w == pytest.approx(250_000.0)
    assert motor.max_speed_rads == pytest.approx(3000 * 2 * math.pi / 60)
    batt = lib.load_component("battery/nmc_350kwh.yaml", "battery")
    assert batt.cell_resistance_ohm == pytest.approx(0.0011)


def test_integer_fields_stay_integers(bus):
    assert isinstance(bus.seats, int)
    assert isinstance(bus.battery.cells_series, int)


def test_unknown_field_is_an_error_not_a_silent_default(tmp_path):
    """The failure mode this guards against: a typo leaves a default in place
    and the model returns a plausible, wrong answer."""
    root = tmp_path / "library"
    (root / "motor").mkdir(parents=True)
    (root / "motor" / "typo.yaml").write_text(
        "kind: motor\nname: typo\npeak_torque_Nm: 3000\n", encoding="utf-8")
    with pytest.raises(LibraryError, match="unknown field"):
        Library(root).load_component("motor/typo.yaml", "motor")


def test_giving_both_a_field_and_its_alias_is_an_error(tmp_path):
    root = tmp_path / "library"
    (root / "motor").mkdir(parents=True)
    (root / "motor" / "dup.yaml").write_text(
        "kind: motor\nname: dup\npeak_power_w: 1000\npeak_power_kw: 250\n",
        encoding="utf-8")
    with pytest.raises(LibraryError, match="pick one"):
        Library(root).load_component("motor/dup.yaml", "motor")


# ------------------------------------------------------------------ chassis
def test_grade_force_matches_hand_calculation():
    ch = Chassis(kerb_mass_kg=10_000.0)
    f = ch.resistive_force(10.0, mass_kg=10_000.0, grade_rad=math.atan(0.05))
    assert f["grade"] == pytest.approx(10_000.0 * G * math.sin(math.atan(0.05)), rel=1e-9)


def test_resistances_vanish_at_standstill_but_grade_does_not():
    ch = Chassis()
    f = ch.resistive_force(0.0, mass_kg=15_000.0, grade_rad=0.05)
    assert f["rolling"] == pytest.approx(0.0)
    assert f["aero"] == pytest.approx(0.0)
    assert f["grade"] > 0.0


def test_rotating_inertia_is_a_few_percent_of_mass():
    ch = Chassis(rotational_inertia_kgm2=90.0, tyre_dynamic_radius_m=0.478)
    m_eq = ch.equivalent_rotating_mass_kg(0.42 * 6.2 ** 2)
    assert 0.01 < m_eq / ch.kerb_mass_kg < 0.12


# ------------------------------------------------------------------ battery
def test_rint_solution_satisfies_the_circuit_equation():
    b = BatteryPack()
    r = b.solve(100_000.0, soc=0.6, temp_c=25.0, dt_s=1.0)
    assert r["voltage_v"] * r["current_a"] == pytest.approx(r["power_w"], rel=1e-6)
    assert r["voltage_v"] == pytest.approx(
        r["ocv_v"] - r["current_a"] * r["resistance_ohm"], rel=1e-9)


def test_discharge_beyond_the_maximum_power_point_is_clipped():
    b = BatteryPack()
    r = b.solve(50_000_000.0, soc=0.6, temp_c=25.0, dt_s=1.0)
    assert r["limited"]
    p_max_theoretical = b.ocv(0.6) ** 2 / (4.0 * b.resistance(0.6, 25.0))
    assert r["power_w"] <= p_max_theoretical * 1.001


def test_resistance_rises_when_cold():
    b = BatteryPack()
    assert b.resistance(0.5, -10.0) > 2.0 * b.resistance(0.5, 25.0)


def test_cold_pack_refuses_regen():
    """The winter range mechanism ADVISOR cannot represent."""
    b = BatteryPack()
    _, chg_warm = b.power_limits(0.5, 25.0)
    _, chg_cold = b.power_limits(0.5, -10.0)
    assert abs(chg_cold) < 0.2 * abs(chg_warm)


def test_soc_falls_on_discharge_and_rises_on_charge():
    b = BatteryPack()
    assert b.solve(80_000.0, 0.6, 25.0, 10.0)["soc"] < 0.6
    assert b.solve(-80_000.0, 0.6, 25.0, 10.0)["soc"] > 0.6


def test_full_pack_accepts_no_charge():
    b = BatteryPack()
    _, chg = b.power_limits(b.soc_max, 25.0)
    assert chg == pytest.approx(0.0)


# --------------------------------------------------------------------- HVAC
def test_door_exchange_scales_with_root_delta_t_and_reverses_sign():
    h = HVAC()
    cold = h.q_doors(cabin_c=20.0, ambient_c=-10.0, doors_open=True)
    mild = h.q_doors(cabin_c=20.0, ambient_c=5.0, doors_open=True)
    hot = h.q_doors(cabin_c=20.0, ambient_c=40.0, doors_open=True)
    assert cold < 0.0 and hot > 0.0          # heat leaves in winter, enters in summer
    assert abs(cold) > abs(mild)             # bigger dT, bigger exchange
    # Brown-Solvason goes as sqrt(dT), so doubling dT gives about 2.83x the power
    # (sqrt(2) on the flow, x2 on the enthalpy difference).
    ratio = abs(cold) / abs(h.q_doors(20.0, 5.0, True))
    assert 2.4 < ratio < 3.2


def test_closed_doors_exchange_nothing():
    assert HVAC().q_doors(20.0, -10.0, doors_open=False) == 0.0


def test_passengers_are_free_winter_heating():
    h = HVAC()
    assert h.q_passengers(60) == pytest.approx(60 * h.passenger_sensible_w)
    assert h.q_passengers(60) > 4000.0


def test_heat_pump_draws_less_than_resistive_for_the_same_duty():
    hp = HVAC(system_type="heat_pump")
    res = HVAC(system_type="resistive")
    p_hp, _, _ = hp._electrical_demand(20_000.0, ambient_c=0.0)
    p_res, _, _ = res._electrical_demand(20_000.0, ambient_c=0.0)
    assert p_hp < 0.6 * p_res


def test_heat_pump_falls_back_to_backup_below_cutoff():
    h = HVAC(system_type="hybrid", backup_heat_source="resistive",
             heat_pump_cutoff_c=-12.0)
    assert h._electrical_demand(20_000.0, -5.0)[2] == "heat_pump"
    assert h._electrical_demand(20_000.0, -20.0)[2] == "resistive"


def test_fuel_fired_heat_does_not_hide_in_the_electrical_total():
    h = HVAC(system_type="fuel_fired")
    p_elec, fuel, mode = h._electrical_demand(20_000.0, -10.0)
    assert mode == "fuel_fired"
    assert fuel > 20_000.0          # reported separately, and includes burner losses
    assert p_elec < 3_000.0         # only pumps and blowers come off the pack


def test_glazing_dominates_the_envelope_ua():
    h = HVAC()
    glazing = h.glazing_area_m2 * h.u_glazing_w_per_m2k
    opaque = h.opaque_area_m2 * h.u_opaque_w_per_m2k
    assert glazing > opaque


# -------------------------------------------------------------------- cycle
def test_resampling_preserves_distance(lib):
    c = lib.load_cycle("manhattan")
    assert c.resample(1.0).distance_km == pytest.approx(c.distance_km, rel=0.01)


def test_repeat_multiplies_distance(lib):
    c = lib.load_cycle("cbdbus")
    assert c.repeat(4).distance_km == pytest.approx(4 * c.distance_km, rel=0.01)


def test_door_inference_finds_the_bus_stops(lib):
    """CBD is 14 stop-and-go cycles over 2 miles, so door events should be in
    that neighbourhood rather than counting every brake application."""
    c = lib.load_cycle("cbdbus")
    assert 10 <= c.stop_count <= 20


def test_occupancy_profile_peaks_mid_route(lib):
    c = lib.load_cycle("cbdbus").with_occupancy(lambda f: 60 * 4 * f * (1 - f))
    mid = c.passengers[len(c.passengers) // 2]
    assert mid > c.passengers[0] and mid > c.passengers[-1]
    assert mid == pytest.approx(60.0, rel=0.1)


def test_route_driver_respects_speed_limits_and_stops():
    r = Route.from_csv(ROOT / "library" / "route" / "demo_hilly_city.csv")
    c = r.to_cycle(accel_mps2=1.0, decel_mps2=1.2)
    assert c.max_speed_kmh <= 50.5
    assert c.stop_count == len(r.stop_positions_m)
    assert c.distance_km == pytest.approx(r.length_km, rel=0.02)


def test_grade_smoothing_removes_dem_noise():
    d = np.arange(0.0, 2000.0, 10.0)
    clean = 0.02 * d
    noisy = clean + np.random.default_rng(1).normal(0, 0.4, d.size)
    r = Route(d, noisy, np.full(d.size, 13.9))
    g = np.tan(r.grade_at(d, smoothing_m=100.0))
    assert abs(np.median(g) - 0.02) < 0.004
    assert g.std() < 0.02


# ---------------------------------------------------------------- simulator
def test_energy_balance_closes(bus, cycle):
    """The single most important test here. If the balance does not close, no
    other number on the report means anything."""
    for t in (-10.0, 20.0, 35.0):
        r = simulate(bus, cycle, Environment(ambient_c=t))
        resid = r.energy_balance()["residual (should be ~0)"]
        assert abs(resid) < 0.001 * r.totals["cell_energy_kwh"]


def test_cold_costs_more_energy_than_mild(bus, cycle):
    cold = simulate(bus, cycle, Environment(ambient_c=-10.0))
    mild = simulate(bus, cycle, Environment(ambient_c=20.0))
    assert cold.energy_per_km_kwh > 1.3 * mild.energy_per_km_kwh
    assert cold.range_km < mild.range_km


def test_hot_and_sunny_costs_more_than_mild(bus, cycle):
    hot = simulate(bus, cycle, Environment(ambient_c=38.0,
                                           solar_irradiance_w_per_m2=900.0))
    mild = simulate(bus, cycle, Environment(ambient_c=20.0))
    assert hot.energy_per_km_kwh > mild.energy_per_km_kwh


def test_heat_pump_bus_beats_resistive_bus_on_the_same_winter_duty(lib, cycle):
    hp = simulate(lib.load_vehicle("citybus_12m_be"), cycle,
                  Environment(ambient_c=-7.0))
    res = simulate(lib.load_vehicle("citybus_12m_be_resistive"), cycle,
                   Environment(ambient_c=-7.0))
    assert hp.energy_per_km_kwh < res.energy_per_km_kwh
    assert hp.range_km > res.range_km


def test_passengers_cost_traction_energy(bus, lib):
    light = lib.load_cycle("cbdbus").repeat(3).with_occupancy(0)
    heavy = lib.load_cycle("cbdbus").repeat(3).with_occupancy(80)
    env = Environment(ambient_c=20.0)
    a = simulate(bus, light, env).totals
    b = simulate(bus, heavy, env).totals
    assert b["rolling_kwh"] > a["rolling_kwh"]


def test_disabling_regen_costs_energy(lib, cycle):
    v = lib.load_vehicle("citybus_12m_be")
    with_regen = simulate(v, cycle, Environment(ambient_c=20.0))
    v.control.regen_enabled = False
    without = simulate(v, cycle, Environment(ambient_c=20.0))
    v.control.regen_enabled = True
    assert without.energy_per_km_kwh > with_regen.energy_per_km_kwh
    assert without.totals["regen_recovered_kwh"] == pytest.approx(0.0, abs=1e-6)


def test_gradient_energy_nets_to_zero_on_a_flat_out_and_back(bus, lib):
    """A loop that starts and ends at the same altitude must net zero grade
    energy. Catches sign errors in the gradient term."""
    d = np.arange(0.0, 4000.0, 10.0)
    elev = 50.0 + 30.0 * np.sin(2 * np.pi * d / 4000.0)
    route = Route(d, elev, np.full(d.size, 13.9), stop_positions_m=[1000.0, 3000.0])
    cyc = route.to_cycle()
    r = simulate(bus, cyc, Environment(ambient_c=20.0))
    assert abs(r.totals["grade_kwh"]) < 0.05 * r.totals["battery_energy_kwh"]


def test_climbing_costs_more_than_descending_recovers(bus):
    """Second law sanity: a bus cannot come back down with the energy it spent
    going up."""
    d = np.arange(0.0, 6000.0, 10.0)
    up = Route(d, 50.0 + 0.04 * d, np.full(d.size, 13.9))
    down = Route(d, 290.0 - 0.04 * d, np.full(d.size, 13.9))
    env = Environment(ambient_c=20.0)
    e_up = simulate(bus, up.to_cycle(), env).totals["battery_energy_kwh"]
    e_down = simulate(bus, down.to_cycle(), env).totals["battery_energy_kwh"]
    assert e_up > 0.0
    assert e_down < e_up
    assert e_up + e_down > 0.0      # the round trip is a net loss


def test_unmet_demand_is_reported_not_hidden(lib, cycle):
    """Cripple the motor and the solver must say the cycle was not achievable
    rather than quietly returning a consumption figure."""
    v = lib.load_vehicle("citybus_12m_be")
    v.motor.peak_torque_nm = 120.0
    v.motor.peak_power_w = 12_000.0
    r = simulate(v, cycle, Environment(ambient_c=20.0))
    assert not r.achievable
    assert r.unmet_samples > 0


def test_unvalidated_components_raise_a_warning(bus, cycle):
    r = simulate(bus, cycle, Environment(ambient_c=20.0))
    assert any("unvalidated" in w for w in r.warnings)


# ------------------------------------------------------- plausibility bands
def test_mild_weather_consumption_is_in_the_published_band(bus, cycle):
    """A 12 m battery city bus on an urban cycle in mild weather consumes
    roughly 0.9-1.7 kWh/km in every published fleet dataset. Outside that band
    means a unit error, not a modelling opinion."""
    r = simulate(bus, cycle, Environment(ambient_c=20.0))
    assert 0.9 < r.energy_per_km_kwh < 1.7


def test_winter_penalty_is_in_the_observed_band(bus, cycle):
    """Fleet data puts the cold-weather consumption penalty at roughly
    1.4x to 2.2x. A model outside that is telling a story nobody has seen."""
    cold = simulate(bus, cycle, Environment(ambient_c=-10.0))
    mild = simulate(bus, cycle, Environment(ambient_c=20.0))
    assert 1.4 < cold.energy_per_km_kwh / mild.energy_per_km_kwh < 2.2


def test_regen_recovers_a_plausible_share_of_braking_energy(bus, cycle):
    r = simulate(bus, cycle, Environment(ambient_c=20.0))
    assert 0.35 < r.totals["regen_fraction_of_braking"] < 0.80


# ------------------------------------------------- regressions (fixed bugs)
def test_energy_balance_closes_even_when_the_cycle_is_not_met(lib, cycle):
    """Regression: clipped motor torque used to book the shortfall as a
    driveline loss, leaving a ~4 % residual on hilly routes."""
    v = lib.load_vehicle("citybus_12m_be")
    v.motor.peak_power_w = 90_000.0
    v.motor.peak_torque_nm = 1200.0
    r = simulate(v, cycle, Environment(ambient_c=-5.0))
    assert not r.achievable
    assert r.totals["undelivered_kwh"] > 0.0
    resid = r.energy_balance()["residual (should be ~0)"]
    assert abs(resid) < 1e-6 * r.totals["cell_energy_kwh"] + 1e-9


def test_power_aware_driver_keeps_a_hilly_route_achievable(bus):
    """Regression: the fixed-acceleration driver demanded 1 m/s^2 up 8 % grades
    and reported a correctly-sized bus as unable to follow its own route."""
    route = Route.from_csv(ROOT / "library" / "route" / "demo_hilly_city.csv")
    occ = lambda f: 55 * 4 * f * (1 - f)
    naive = simulate(bus, route.to_cycle(occupancy=occ), Environment(ambient_c=20.0))
    aware = simulate(bus, route.to_cycle(occupancy=occ, vehicle=bus),
                     Environment(ambient_c=20.0))
    assert not naive.achievable
    assert aware.achievable


# --------------------------------------------------------------- validation
def _synthetic_log_from_model(bus, lib, path, *, bias_w=0.0, ambient=10.0):
    """Build a 'measured' log by running the model itself. This tests the
    validation *tool*, not the vehicle: a model replayed against its own output
    must score zero error, and a known injected bias must be found and blamed on
    the right phase. It is not, and must never be used as, evidence that the
    model matches a real bus."""
    cyc = lib.load_cycle("cbdbus").repeat(2).with_occupancy(30)
    r = simulate(bus, cyc, Environment(ambient_c=ambient))
    s = r.series
    with path.open("w", newline="") as fh:
        fh.write("# SYNTHETIC test fixture generated by the model itself\n")
        fh.write("time_s,speed_mps,battery_power_kw,soc_percent,passengers,doors_open,ambient_c\n")
        for i in range(s["time_s"].size):
            fh.write(f"{s['time_s'][i]},{s['speed_mps'][i]},"
                     f"{(s['p_battery_w'][i] + bias_w)/1e3},{s['soc'][i]*100},"
                     f"{s['passengers'][i]},{int(s['doors_open'][i])},{ambient}\n")
    return path


def test_validator_scores_the_model_against_itself_as_exact(bus, lib, tmp_path):
    from temsasim.validate import MeasuredLog, validate
    log = MeasuredLog.from_csv(_synthetic_log_from_model(bus, lib, tmp_path / "self.csv"))
    rep = validate(bus, log)
    assert abs(rep.metrics["energy_error"]) < 1e-3
    assert rep.metrics["soc_rmse"] < 1e-3
    assert rep.metrics["power_correlation"] > 0.999
    assert rep.passed


def test_validator_catches_an_unmodelled_constant_load(bus, lib, tmp_path):
    """Inject a 4 kW load the model does not know about -- the signature of a
    forgotten auxiliary. The validator must fail it and point at standstill,
    the only phase where a constant load cannot hide behind traction."""
    from temsasim.validate import MeasuredLog, validate
    log = MeasuredLog.from_csv(
        _synthetic_log_from_model(bus, lib, tmp_path / "bias.csv", bias_w=4000.0))
    rep = validate(bus, log)
    assert rep.metrics["energy_error"] < -0.05     # model under-predicts
    assert not rep.passed
    by = rep.metrics["by_phase"]
    assert by["standstill"][1] > by["standstill"][0]
    assert any("auxiliary or HVAC" in n for n in rep.notes)


def test_log_without_a_battery_signal_is_rejected(tmp_path):
    from temsasim.validate import MeasuredLog
    f = tmp_path / "nobatt.csv"
    f.write_text("time_s,speed_kmh\n0,0\n1,5\n", encoding="utf-8")
    with pytest.raises(ValueError, match="nothing to validate against"):
        MeasuredLog.from_csv(f)


def test_validator_blames_braking_when_regen_is_the_problem(bus, lib, tmp_path):
    """The complementary case: an error only while braking must be attributed
    to regen, not smeared into a constant-load diagnosis."""
    from temsasim.validate import MeasuredLog, validate
    cyc = lib.load_cycle("cbdbus").repeat(2).with_occupancy(30)
    r = simulate(bus, cyc, Environment(ambient_c=10.0))
    s = r.series
    p = s["p_battery_w"].copy()
    braking = (s["speed_mps"] > 0.3) & (s["accel_mps2"] < -0.05)
    p[braking] *= 0.4          # the real bus recovered far less than the model
    f = tmp_path / "regen.csv"
    with f.open("w", newline="") as fh:
        fh.write("time_s,speed_mps,battery_power_kw,passengers,doors_open,ambient_c\n")
        for i in range(p.size):
            fh.write(f"{s['time_s'][i]},{s['speed_mps'][i]},{p[i]/1e3},"
                     f"{s['passengers'][i]},{int(s['doors_open'][i])},10\n")
    rep = validate(bus, MeasuredLog.from_csv(f))
    assert any("concentrates in braking" in n and "regen" in n for n in rep.notes)


# ---------------------------------------------------------------------- CLI
@pytest.mark.parametrize("argv", [
    ["list"],
    ["describe", "citybus_12m_be"],
    ["run", "--vehicle", "citybus_12m_be", "--cycle", "cbdbus", "--passengers", "20"],
    ["sweep", "--vehicle", "citybus_12m_be", "--cycle", "cbdbus",
     "--t-min", "0", "--t-max", "10", "--t-step", "10"],
    ["compare", "--vehicles", "citybus_12m_be,citybus_12m_be_resistive",
     "--cycle", "cbdbus", "--ambient", "-5"],
])
def test_cli_commands_run(argv, capsys):
    """Smoke test every command end to end. The physics tests never import the
    CLI, so without this a broken CLI ships with a green test suite."""
    from temsasim.cli import main
    assert main(["--library", str(ROOT / "library")] + argv) == 0
    assert capsys.readouterr().out.strip()


def test_rate_coefficients_do_not_get_speed_unit_aliases(tmp_path):
    """Regression: coastdown_b_n_per_mps ends in '_mps', and the loader used to
    offer coastdown_b_n_per_kmh with a speed conversion factor -- converting a
    N/(m/s) coefficient as if it were a speed, and in the wrong direction."""
    root = tmp_path / "library"
    (root / "chassis").mkdir(parents=True)
    (root / "chassis" / "c.yaml").write_text(
        "kind: chassis\nname: c\ncoastdown_b_n_per_kmh: 10\n", encoding="utf-8")
    with pytest.raises(LibraryError, match="unknown field"):
        Library(root).load_component("chassis/c.yaml", "chassis")


def test_surface_factor_scales_rolling_energy_only(bus, cycle):
    """A rougher road multiplies the rolling term and nothing else; the energy
    balance still closes."""
    smooth = simulate(bus, cycle, Environment(ambient_c=20.0))
    rough_cycle = DriveCycle(cycle.time_s, cycle.speed_mps, cycle.grade_rad, cycle.passengers,
                             cycle.doors_open, name="rough",
                             surface_factor=np.full(cycle.time_s.size, 1.5))
    rough = simulate(bus, rough_cycle, Environment(ambient_c=20.0))
    a, b = smooth.totals, rough.totals
    assert b["rolling_kwh"] == pytest.approx(1.5 * a["rolling_kwh"], rel=0.02)
    assert b["aero_kwh"] == pytest.approx(a["aero_kwh"], rel=1e-6)
    assert b["battery_energy_kwh"] > a["battery_energy_kwh"]
    assert abs(rough.energy_balance()["residual (should be ~0)"]) < 0.01


def test_cycles_without_a_surface_are_unchanged(cycle):
    assert np.all(cycle.surface_factor == 1.0)
    assert np.all(cycle.resample(0.5).surface_factor == 1.0)
    assert cycle.repeat(2).surface_factor.size == 2 * cycle.time_s.size
