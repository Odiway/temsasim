"""Suspension: the textbook relations against the books' own worked examples,
then the seven-degree-of-freedom model against those relations and against
itself (statics, simulation, rollover, comfort), then the interface."""

from __future__ import annotations

import copy
import math
import pathlib

import numpy as np
import pytest

from temsasim import suspension as S
from temsasim.library import Library

ROOT = pathlib.Path(__file__).resolve().parents[1]
MARATON = ROOT / "library" / "suspension" / "maraton_13.yaml"
needs_maraton = pytest.mark.skipif(not MARATON.exists(), reason="Maraton suspension data is local only (supplier drawings)")
GENERIC = "otobus_13m_jenerik"


@pytest.fixture(scope="module")
def model(demo_library):
    return S.load_suspension(demo_library, GENERIC)


@pytest.fixture(scope="module")
def laden(model):
    return S.State(model, kerb_mass_kg=13500.0, passengers=55, passenger_mass_kg=68.0)


def variant(model, **changes):
    d = copy.deepcopy(model.to_dict())
    for path, value in changes.items():
        S.set_path(d, path.replace("__", "."), value)
    return S.SuspensionModel.from_dict(d, stem=model.stem)


# ------------------------------------------------------------------ the books
def test_ride_rate_and_frequency_reproduce_gillespie_example():
    # Gillespie, ch. 5 example: tyre 1198 lb/in, springs 143 and 100 lb/in, 957 and 730 lb per wheel
    rf, rr = S.ride_rate(143, 1198), S.ride_rate(100, 1198)
    assert rf == pytest.approx(127.7, abs=0.2) and rr == pytest.approx(92.3, abs=0.05)
    g = 386.0                                                       # in/s^2; mass = weight / g
    assert S.natural_frequency_hz(rf, 957 / g) == pytest.approx(1.14, abs=0.005)
    assert S.natural_frequency_hz(rr, 730 / g) == pytest.approx(1.11, abs=0.005)


def test_bounce_and_pitch_reproduce_gillespie_example():
    # Gillespie, ch. 5 problem 1: wheelbase 100.6 in, dynamic index 1.1
    bp = S.bounce_pitch(254.0, 184.6, 43.53, 57.07, 8.74, 2732.0)
    assert bp["alpha"] == pytest.approx(50.18, abs=0.02) and bp["beta"] == pytest.approx(-59.67, abs=0.05) and bp["gamma"] == pytest.approx(45.34, abs=0.02)
    hi, lo = bp["modes"]
    assert hi["omega_rad_s"] == pytest.approx(7.10, abs=0.01) and hi["hz"] == pytest.approx(1.13, abs=0.005) and hi["kind"] == "bounce"
    assert lo["omega_rad_s"] == pytest.approx(6.71, abs=0.01) and lo["hz"] == pytest.approx(1.07, abs=0.005) and lo["kind"] == "pitch"
    assert lo["centre_m"] == pytest.approx(11.57, abs=0.3)          # inches ahead of the CG: the pitch centre
    assert hi["centre_m"] < -200                                    # far behind the CG: the bounce centre (the book rounds to -259)
    assert bp["dynamic_index"] == pytest.approx(1.1, abs=0.001)


def test_roll_stiffness_and_roll_rate_reproduce_gillespie_example():
    # Gillespie, ch. 6 problem 2: 115 lb/in leaf springs 40 in apart, front 1500 in-lb/deg, 2750 lb at 10 in
    k = S.roll_stiffness_springs(115.0, 40.0)
    assert k == pytest.approx(92000.0) and k / 57.3 == pytest.approx(1606, abs=1)
    rate = S.roll_rate(2750.0 * 10.0, (1500.0 + 1606.0) * 57.3)
    assert math.degrees(rate) == pytest.approx(10.5, abs=0.05)


def test_rollover_thresholds_reproduce_gillespie():
    assert S.rollover_rigid(72.0, 60.0) == pytest.approx(0.6)       # heavy truck, table of ch. 9: 0.4 to 0.6 g
    assert S.rollover_rigid(60.0, 20.0) == pytest.approx(1.5)       # sports car: 1.2 to 1.7 g
    # "hr/h = 0.5 and a roll rate of 6 degrees per g (0.1 rad/g) ... approximately 0.95"
    assert S.rollover_suspended(2.0, 1.0, 0.1, 0.5) == pytest.approx(0.95, abs=0.005)


def test_pitch_angle_and_anti_squat_follow_gillespie_7_14():
    w, h, wb, kf, kr = 170e3, 1.37, 6.92, 400e3, 350e3
    plain = S.pitch_angle(w, h, wb, 0.3, kf, kr)
    assert plain == pytest.approx(-(w * 0.3 * h / wb) * (1 / kf + 1 / kr) / wb)       # nose up under traction
    assert S.pitch_angle(w, h, wb, 0.3, kf, kr, anti_rear=1.0) == pytest.approx(-(w * 0.3 * h / wb) / kf / wb)
    assert S.pitch_angle(w, h, wb, -0.3, kf, kr) == pytest.approx(-plain)             # braking: the same angle, nose down


def test_steer_angle_is_gillespie_6_16():
    k = math.radians(2.0)                                           # 2 deg/g
    d = S.steer_angle(6.92, 100.0, 20.0, k)
    assert math.degrees(d) == pytest.approx(57.2958 * 6.92 / 100 + 2.0 * (20.0 ** 2 / 100 / S.G), rel=1e-6)
    vc = S.characteristic_speed(6.92, k)
    assert S.steer_angle(6.92, 100.0, vc, k) == pytest.approx(2 * 6.92 / 100.0)       # twice the Ackermann angle
    assert S.yaw_velocity_gain(6.92, 20.0, 0.0) == pytest.approx(20.0 / 6.92)


def test_iso_tables():
    # ISO 2631-1 Wk, values of its Table 3 at third-octave centres
    for f, w in ((1.0, 0.482), (2.0, 0.531), (4.0, 0.967), (5.0, 1.039), (6.3, 1.054), (8.0, 1.036), (10.0, 0.988), (16.0, 0.768), (31.5, 0.405)):
        assert float(S.wk_weighting(f)) == pytest.approx(w, abs=0.004)
    # ISO 8608 Table C.2: each class is four times the one before, class A is 16e-6 m^3
    assert S.ISO8608["A"] == 16e-6 and all(S.ISO8608[b] == 4 * S.ISO8608[a] for a, b in zip("ABCD", "BCDE"))
    assert float(S.road_psd("C", 20.0, 2.0)) == pytest.approx(256e-6 * 0.01 * 20.0 / 4.0)
    assert S.comfort_label(0.2) == "rahatsız etmez" and S.comfort_label(0.7) == "oldukça rahatsız eder"


# ------------------------------------------------------------------ components
def test_air_spring_pressure_and_rate():
    sp = S.AirSpring(force_kn_by_bar=[[1, 4.0], [5, 20.0], [8, 32.0]], volume_dm3=14.0, rate_basis="polytropic")
    assert sp.pressure_bar(20e3) == pytest.approx(5.0) and sp.pressure_bar(36e3) == pytest.approx(9.0)    # runs on past the table
    ae = 20e3 / 5e5
    assert sp.rate_n_per_m(5.0) == pytest.approx(1.38 * (5e5 + S.P_ATM) * ae * ae / 0.014)
    # a natural-frequency chart fixes the rate through the load the spring carries
    sp2 = S.AirSpring(force_kn_by_bar=[[1, 4.0], [5, 20.0]], frequency_hz_by_bar=[[1, 1.5], [5, 1.0]], rate_n_mm_by_bar=[[1, 80], [5, 170]])
    assert sp2.basis() == "frequency" and sp2.rate_n_per_m(5.0) == pytest.approx((2 * math.pi) ** 2 * 20e3 / S.G)
    assert set(sp2.rates_n_per_m(5.0)) == {"frequency", "rate"} and sp2.rates_n_per_m(5.0)["rate"] == 170e3
    with pytest.raises(ValueError):
        S.AirSpring(force_kn_by_bar=[[1, 4.0], [5, 20.0]]).rate_n_per_m(5.0)


def test_damper_curve_and_equivalent_coefficient():
    d = S.Damper(speeds_mps=[0.1, 0.3], rebound_n=[3000.0, 6000.0], compression_n=[1000.0, 2000.0])
    assert float(d.force(0.1)) == 1000.0 and float(d.force(-0.1)) == -3000.0 and float(d.force(0.0)) == 0.0
    assert float(d.force(0.05)) == 500.0 and float(d.force(0.5)) == pytest.approx(3000.0)                # along the last segment
    lin = S.Damper(speeds_mps=[0.1, 0.5], rebound_n=[2000.0, 10000.0], compression_n=[2000.0, 10000.0])
    assert lin.equivalent_coefficient(0.2) == pytest.approx(20000.0, rel=1e-3)
    # a softer compression side halves that half of the cycle's work
    asym = S.Damper(speeds_mps=[0.1, 0.5], rebound_n=[2000.0, 10000.0], compression_n=[1000.0, 5000.0])
    assert asym.equivalent_coefficient(0.2) == pytest.approx(15000.0, rel=1e-3)
    with pytest.raises(ValueError):
        S.Damper(speeds_mps=[0.2, 0.1], rebound_n=[1, 2], compression_n=[1, 2])


def test_library_roundtrip_and_vehicle_link(tmp_path, model):
    assert GENERIC in [f["stem"] for f in S.list_suspensions(model and ROOT / "library")]
    m = variant(model, cog_z_m=1.33)
    m.name, m.stem, m.vehicles = "Deneme", "deneme", ["arac_a"]
    m.front.damper_tune = {"rebound_n": [1, 2, 3, 4, 5], "compression_n": [1, 2, 3, 4, 5]}
    p = S.save_suspension(tmp_path, m)
    assert p.read_text(encoding="utf-8").startswith("# Süspansiyon dosyası")
    back = S.load_suspension(tmp_path, "deneme")
    assert back.cog_z_m == 1.33 and back.front.damper_tune["rebound_n"] == [1, 2, 3, 4, 5] and back.to_dict() == m.to_dict()
    assert back.axle_damper(back.front).rebound_n == [1.0, 2.0, 3.0, 4.0, 5.0] and back.axle_damper(back.rear) is back.dampers[back.rear.damper]
    with pytest.raises(FileExistsError):
        S.save_suspension(tmp_path, m)
    S.save_suspension(tmp_path, variant(model))                                       # the generic file names no vehicle
    assert S.suspension_for(tmp_path, "arac_a") == ("deneme", True)
    assert S.suspension_for(tmp_path, "baska_arac") == (GENERIC, False)
    with pytest.raises(ValueError):
        variant(model, front__spring="yok")
    with pytest.raises(ValueError):
        S.SuspensionModel.from_dict({**model.to_dict(), "bilinmeyen": 1})


# ------------------------------------------------------------------ the vehicle at one load
def test_static_loads_pressures_and_summary(model, laden):
    s = laden.summary()
    assert s["mass_kg"] == pytest.approx(13500 + 55 * 68) and sum(s["axle_load_kg"]) == pytest.approx(s["mass_kg"])
    assert sum(s["wheel_load_kg"]) == pytest.approx(s["mass_kg"]) and s["axle_share"][1] == pytest.approx(s["cog"]["x_m"] / model.wheelbase_m)
    # the springs carry the sprung weight at a pressure inside their range
    assert float(laden.F0s.sum()) == pytest.approx(laden.ms * S.G)
    for sp in s["springs"]:
        assert 1.0 < sp["pressure_bar"][0] < sp["max_pressure_bar"] and not sp["over_pressure"]
    empty = S.State(model, kerb_mass_kg=13500.0)
    assert (empty.p_bar < laden.p_bar).all() and (empty.ks < laden.ks).all()          # more load, more pressure, stiffer spring
    # an air spring keeps the ride frequency nearly constant as the load changes
    assert empty.summary()["ride_hz"][1] == pytest.approx(s["ride_hz"][1], rel=0.08)
    assert 0.8 < s["ride_hz"][0] < 1.6 and 0.8 < s["ride_hz"][1] < 1.6 and 8 < s["wheel_hop_hz"][0] < 13
    assert 0.15 < s["damping_ratio"][0] < 0.6 and 0.15 < s["damping_ratio"][1] < 0.6
    with pytest.raises(ValueError):
        S.State(variant(model, cog_y_m=0.4, front__spring_track_m=0.6), kerb_mass_kg=13500.0)   # a spring would have to pull


def test_linear_statics_match_the_closed_forms(model, laden):
    s = laden.summary()
    # dive: Gillespie Eq. (7-14) with the ride rates; the seven-degree-of-freedom statics must give the same
    assert s["pitch"]["dive_deg_per_g"] == pytest.approx(s["pitch"]["dive_book_deg_per_g"], rel=1e-6)
    assert s["pitch"]["squat_deg_per_g"] == pytest.approx(s["pitch"]["dive_deg_per_g"], rel=1e-9)    # no anti-geometry in the file
    half = S.State(variant(model, front__anti_dive=0.5, rear__anti_squat=1.0), kerb_mass_kg=13500.0, passengers=55).summary()
    kf, kr = sum(s["ride_rate_n_per_m"][:2]), sum(s["ride_rate_n_per_m"][2:])
    ktf, ktr = float(laden.kt[:2].sum()), float(laden.kt[2:].sum())
    kwf, kwr = float(laden.kw[:2].sum()), float(laden.kw[2:].sum())
    dw = laden.mass * S.G * laden.cog_z / laden.L                                     # load transfer at 1 g
    # anti-dive takes its share off the springs; the tyres still carry the whole transfer
    want = math.degrees((dw * (0.5 / kwf + 1 / ktf) + dw / kr) / laden.L)
    assert half["pitch"]["dive_deg_per_g"] == pytest.approx(want, rel=1e-6)
    assert half["pitch"]["squat_deg_per_g"] == pytest.approx(math.degrees((dw / kf + dw / ktr) / laden.L), rel=1e-6)
    # roll: with rigid tyres the model's roll per g is Gillespie Eq. (6-32)
    stiff = S.State(variant(model, front__tyre_stiffness_n_per_m=1e12, rear__tyre_stiffness_n_per_m=1e12), kerb_mass_kg=13500.0, passengers=55).summary()
    assert stiff["roll"]["gradient_deg_per_g"] == pytest.approx(stiff["roll"]["gradient_book_deg_per_g"], rel=1e-3)
    assert s["roll"]["gradient_deg_per_g"] > s["roll"]["gradient_book_deg_per_g"]     # the tyres add their own lean
    assert s["roll"]["stiffness_springs_nm_per_rad"][0] == pytest.approx(S.roll_stiffness_springs(float(laden.kw[0]), model.front.spring_track_m))
    # the load transfer the model puts through the tyres is the whole overturning moment
    q = laden.static(ay=0.2 * S.G, linear=True)
    fz = laden.F0t + laden.kt * (0 - q[3:])
    moment = (fz[1] - fz[0]) * laden.track[0] / 2 + (fz[3] - fz[2]) * laden.track[1] / 2
    lean = laden.ms * S.G * laden.h1 * q[2] + float(laden.grav_axle @ (laden.Brho @ q))
    assert moment == pytest.approx(laden.mass * 0.2 * S.G * laden.cog_z + lean, rel=1e-6)


def test_modes_agree_with_the_two_mass_formulas(model, laden):
    s, modes = laden.summary(), laden.modes()
    names = [m["name"] for m in modes]
    assert names.count("ön tekerlek zıplaması") == 2 and names.count("arka tekerlek zıplaması") == 2 and {"zıplama", "yunuslama", "yalpa"} <= set(names)
    body = sorted(m["hz"] for m in modes if m["name"] in ("zıplama", "yunuslama"))
    book = sorted(m["hz"] for m in s["bounce_pitch"]["modes"])
    assert body == pytest.approx(book, rel=0.06)                                      # Gillespie Eq. (5-35), (5-36); the unsprung masses shift them a little
    roll = next(m for m in modes if m["name"] == "yalpa")
    assert roll["hz"] == pytest.approx(s["roll"]["hz"], rel=0.05) and 0.03 < roll["damping_ratio"] < 0.5
    assert all(m["damping_ratio"] > 0 for m in modes)


# ------------------------------------------------------------------ simulation
def test_braking_dive_settles_at_the_static_angle_and_returns_to_level(model, laden):
    res = S.simulate(laden, S.manoeuvre_inputs(laden, "fren", {"speed_kmh": 60, "decel_mps2": 3.0}))
    k = S.simulation_kpis(laden, res)
    want = laden.summary()["pitch"]["dive_deg_per_g"] * 3.0 / S.G
    assert k["steady_pitch_deg"] == pytest.approx(want, rel=0.06)                     # the air spring is a little progressive
    assert k["peak_dive_deg"] > k["steady_pitch_deg"] and k["peak_squat_deg"] > 0     # overshoot in, rebound at the stop
    assert abs(res["pitch_deg"][-1]) < 0.02 and 0.3 < k["after"]["settle_s"] < 4.0 and 0.6 < k["after"]["hz"] < 1.6
    assert k["peak_roll_deg"] < 1e-6 and not k["wheel_lift"]
    assert float(res["Fz"].sum(axis=1).mean()) == pytest.approx(laden.mass * S.G, rel=2e-3)
    sq = S.simulation_kpis(laden, S.simulate(laden, S.manoeuvre_inputs(laden, "kalkis", {"accel_mps2": 1.5})))
    assert sq["steady_pitch_deg"] == pytest.approx(-laden.summary()["pitch"]["squat_deg_per_g"] * 1.5 / S.G, rel=0.06)


def test_release_test_shows_the_damped_frequency_and_softer_dampers_ring_longer(model, laden):
    res = S.simulate(laden, S.manoeuvre_inputs(laden, "birakma", {"mode": "ziplama", "amount": 20}))
    a = S.simulation_kpis(laden, res)["after"]
    ride = laden.summary()["damped_hz"]
    assert min(ride) * 0.8 < a["hz"] < max(ride) * 1.25 and a["settle_s"] is not None and a["overshoot"] < 0.8
    assert abs(res["heave_mm"][0] + 20) < 1e-6 and abs(res["heave_mm"][-1]) < 0.5
    soft = variant(model)
    for ax in (soft.front, soft.rear):
        ref = soft.dampers[ax.damper]
        ax.damper_tune = {"rebound_n": [0.4 * v for v in ref.rebound_n], "compression_n": [0.4 * v for v in ref.compression_n]}
    st2 = S.State(soft, kerb_mass_kg=13500.0, passengers=55)
    b = S.simulation_kpis(st2, S.simulate(st2, S.manoeuvre_inputs(st2, "birakma", {"mode": "ziplama", "amount": 20})))["after"]
    assert b["settle_s"] > 1.5 * a["settle_s"] and b["overshoot"] > a["overshoot"]
    assert st2.summary()["damping_ratio"][0] == pytest.approx(0.4 * laden.summary()["damping_ratio"][0], rel=1e-6)
    for mode in ("yunuslama", "yalpa"):
        r = S.simulate(laden, S.manoeuvre_inputs(laden, "birakma", {"mode": mode, "amount": 1.0}))
        key = "pitch_deg" if mode == "yunuslama" else "roll_deg"
        assert r[key][0] == pytest.approx(1.0) and abs(r[key][-1]) < 0.1


def test_bump_and_random_road(model, laden):
    res = S.simulate(laden, S.manoeuvre_inputs(laden, "kasis", {"speed_kmh": 20, "height_mm": 60}))
    k = S.simulation_kpis(laden, res)
    front_hit, rear_hit = np.argmax(res["zr"][:, 0] > 0.01), np.argmax(res["zr"][:, 2] > 0.01)
    assert (rear_hit - front_hit) * 0.004 == pytest.approx(model.wheelbase_m / (20 / 3.6), abs=0.02)    # the rear axle a wheelbase later
    assert k["peak_dive_deg"] > 0.1 and k["peak_squat_deg"] > 0.1 and k["peak_roll_deg"] < 1e-6 and abs(res["heave_mm"][-1]) < 1.0
    one = S.simulation_kpis(laden, S.simulate(laden, S.manoeuvre_inputs(laden, "kasis", {"speed_kmh": 20, "height_mm": 60, "side": "sol"})))
    assert one["peak_roll_deg"] > 0.2                                                 # one wheel track only: the body rolls
    t = np.arange(0, 60, 0.005)
    z = S.road_profile("C", 20.0, t)
    f = np.geomspace(0.25, 30.0, 400)
    want = math.sqrt(float(np.trapezoid(S.road_psd("C", 20.0, f), f)))
    assert float(z.std()) == pytest.approx(want, rel=0.15)                            # the generated road has the class's roughness
    rough = S.simulation_kpis(laden, S.simulate(laden, S.manoeuvre_inputs(laden, "yol", {"road_class": "D", "duration_s": 8})))
    smooth = S.simulation_kpis(laden, S.simulate(laden, S.manoeuvre_inputs(laden, "yol", {"road_class": "B", "duration_s": 8})))
    assert rough["acc_rms_mps2"]["cog"] > 2.5 * smooth["acc_rms_mps2"]["cog"]


# ------------------------------------------------------------------ roll and rollover
def test_rollover_limits_are_ordered_and_follow_the_centre_of_gravity(model, laden):
    ro = S.rollover(laden)
    assert ro["ssf_rigid_g"] == pytest.approx(ro["half_track_m"] / laden.cog_z)
    assert ro["dynamic_g"] < ro["static_g"] < ro["ssf_rigid_g"] and ro["first_wheel_g"] <= ro["static_g"] + 1e-9
    assert 0.3 < ro["dynamic_g"] and ro["static_g"] < 0.9 and not ro["slides_first"]   # a coach rolls before it slides on a dry road
    assert ro["tilt_deg"] == pytest.approx(math.degrees(math.atan(ro["static_g"]))) and ro["tilt_deg"] < ro["tilt_rigid_deg"]
    assert ro["book_suspended_g"] == pytest.approx(S.rollover_suspended(2 * ro["half_track_m"], laden.cog_z, math.radians(laden.summary()["roll"]["gradient_book_deg_per_g"]), laden.h_roll_axis))
    assert ro["static_g"] < ro["book_suspended_g"]                                     # the book leaves out tyre compliance and the stops
    pts = ro["curve"]
    assert all(p["dynamic_kmh"] < p["static_kmh"] < p["rigid_kmh"] for p in pts) and pts[5]["radius_m"] == 100
    assert pts[5]["static_kmh"] == pytest.approx(3.6 * math.sqrt(ro["static_g"] * S.G * 100))
    assert S.speeds_on_radius(ro, 250)["static_kmh"] == pytest.approx(3.6 * math.sqrt(ro["static_g"] * S.G * 250))
    # a higher centre of gravity lowers every limit
    high = S.rollover(S.State(variant(model, cog_z_m=1.5), kerb_mass_kg=13500.0, passengers=55))
    assert high["static_g"] < ro["static_g"] and high["dynamic_g"] < ro["dynamic_g"] and high["tilt_deg"] < ro["tilt_deg"]
    # a slow build-up of the lateral acceleration comes back to the steady limit
    slow = S.rollover(laden, rise_s=4.0)
    assert ro["dynamic_g"] < slow["dynamic_g"] <= slow["static_g"] + 1e-6 and slow["dynamic_g"] > 0.9 * slow["static_g"]
    # a vehicle with no compliance left is the rigid one
    rigid = variant(model, front__tyre_stiffness_n_per_m=1e10, rear__tyre_stiffness_n_per_m=1e10,
                    front__arb_stiffness_nm_per_rad=1e10, rear__arb_stiffness_nm_per_rad=1e10)
    rr = S.rollover(S.State(rigid, kerb_mass_kg=13500.0, passengers=55), dynamic=False)
    assert rr["static_g"] == pytest.approx(rr["ssf_rigid_g"], rel=0.02)


def test_centre_of_gravity_to_one_side_makes_one_turn_worse(model):
    st = S.State(variant(model, cog_y_m=0.10), kerb_mass_kg=13500.0, passengers=55)
    assert st.F0t[1] > st.F0t[0] and st.F0t[3] > st.F0t[2] and st.p_bar[1] > st.p_bar[0]   # the right side carries more
    left, right = S._static_threshold(st, +1.0), S._static_threshold(st, -1.0)
    assert left["ay_g"] < right["ay_g"]                                                # heavy right side: the left turn tips first
    ro = S.rollover(st, dynamic=False)
    assert ro["static_side"] == "sola dönüş" and ro["static_g"] == pytest.approx(left["ay_g"])


def test_sudden_steer_and_lane_change(model, laden):
    ro = S.rollover(laden)
    v = 70 / 3.6
    below = S.simulate(laden, S.manoeuvre_inputs(laden, "direksiyon", {"speed_kmh": 70, "radius_m": v * v / (0.8 * ro["dynamic_g"] * S.G)}))
    kb = S.simulation_kpis(laden, below)
    assert not kb["wheel_lift"] and kb["ltr_max"] < 1.0 and kb["peak_roll_deg"] > abs(kb["steady_roll_deg"]) > 1.0     # it overshoots, then holds
    assert kb["steady_roll_deg"] > 0 and float(below["Fz"][-1, 1]) > float(below["Fz"][-1, 0])                       # left turn: leans right, loads the right wheels
    above = S.simulation_kpis(laden, S.simulate(laden, S.manoeuvre_inputs(laden, "direksiyon", {"speed_kmh": 70, "radius_m": v * v / (1.15 * ro["dynamic_g"] * S.G)})))
    assert above["wheel_lift"] and above["ltr_max"] == pytest.approx(1.0, abs=1e-6)
    lane = S.lift_speed(laden, "serit", {"offset_m": 3.5, "length_m": 60})
    assert 60 < lane["speed_kmh"] < 130
    slow = S.simulation_kpis(laden, S.simulate(laden, S.manoeuvre_inputs(laden, "serit", {"speed_kmh": lane["speed_kmh"] - 12})))
    assert not slow["wheel_lift"] and abs(S.simulate(laden, S.manoeuvre_inputs(laden, "serit", {"speed_kmh": 60}))["roll_deg"][-1]) < 0.2
    assert S.lift_speed(laden, "serit", {"offset_m": 1.0, "length_m": 200})["speed_kmh"] is None                      # a gentle lane change never tips it


# ------------------------------------------------------------------ comfort, signals
def test_comfort_on_iso_roads(model, laden):
    c = S.comfort(laden, 80.0)
    aw = [r["cog"]["aw_mps2"] for r in c["rows"]]
    assert [r["road_class"] for r in c["rows"]] == ["A", "B", "C", "D"] and all(1.5 < b / a < 2.3 for a, b in zip(aw, aw[1:]))   # roughness doubles per class
    assert all(r["front"]["aw_mps2"] > 0 and r["cog"]["rms_mps2"] >= 0.5 * r["cog"]["aw_mps2"] for r in c["rows"])
    assert c["transmissibility"]["cog"][0] == pytest.approx(1.0, abs=0.25) and max(c["transmissibility"]["cog"]) > 1.0     # follows the road at low frequency, resonates near 1 Hz
    assert S.comfort(laden, 40.0)["rows"][2]["cog"]["aw_mps2"] < aw[2]
    assert c["rows"][3]["damper_rms_mps"][0] > c["rows"][0]["damper_rms_mps"][0]


def test_explorer_payload_and_run_pitch_signal(model, laden, demo_library):
    res = S.simulate(laden, S.manoeuvre_inputs(laden, "fren", {}))
    p = S.explorer_payload(laden, res, max_points=400)
    n = len(p["x"][0]["values"])
    assert n <= 800 and p["x"][0]["unit"] == "s" and p["x"][0]["digits"] == 2 and all(len(s["values"]) == n for s in p["signals"])
    keys = {s["key"] for s in p["signals"]}
    assert {"pitch", "roll", "heave", "ax", "ay", "ltr", "travel_fl", "dv_rr", "fz_rl", "acc_cog"} <= keys and set(p["selected"]) <= keys
    assert all(set(sel) <= keys for _name, sel in p["presets"])
    # the run's own signals carry the steady dive and squat when the vehicle has a suspension
    from temsasim.cycle import Environment
    from temsasim.signals import explorer_payload
    from temsasim.simulator import run
    lib = Library(demo_library)
    veh = lib.load_vehicle("maraton_13_dizel")
    r = run(veh, lib.load_cycle("cbdbus"), Environment(hvac_enabled=False))
    assert "pitch_deg" not in {s["key"] for s in explorer_payload(r)["signals"]}
    r.suspension_state = S.state_for_vehicle(model, veh, passengers=30)
    sig = next(s for s in explorer_payload(r)["signals"] if s["key"] == "pitch_deg")
    acc = np.gradient(r.series["speed_mps"], r.dt_s)
    i_brake, i_go = int(np.argmin(acc)), int(np.argmax(acc))
    assert sig["group"] == "Şasi" and sig["values"][i_brake] > 0 > sig["values"][i_go]            # dive on the brakes, squat pulling away
    assert sig["values"][i_brake] == pytest.approx(-acc[i_brake] / S.G * r.suspension_state.summary()["pitch"]["dive_deg_per_g"], abs=0.006)


# ------------------------------------------------------------------ supplier data (local only)
@needs_maraton
def test_maraton_air_spring_data_is_consistent_where_it_can_be():
    m = S.load_suspension(ROOT / "library", "maraton_13")
    front, rear = m.air_springs[m.front.spring], m.air_springs[m.rear.spring]
    # front: the rate chart, the natural-frequency chart and the polytropic volume agree
    r5 = front.rates_n_per_m(5.0)
    assert r5["frequency"] == pytest.approx(r5["rate"], rel=0.03) and r5["polytropic"] == pytest.approx(r5["rate"], rel=0.08)
    # rear: the drawing's rate chart and its natural-frequency chart disagree, and the file says which one is used
    q5 = rear.rates_n_per_m(5.0)
    assert q5["rate"] / q5["frequency"] > 1.5 and rear.basis() == "frequency"
    st = S.State(m, kerb_mass_kg=13500.0, passengers=55, passenger_mass_kg=68.0)
    s = st.summary()
    assert not any(sp["over_pressure"] or sp["under_pressure"] for sp in s["springs"])
    assert 0.8 < s["ride_hz"][1] < s["ride_hz"][0] < 1.4
    assert S.rollover(st)["tilt_deg"] > 28.0


# ------------------------------------------------------------------ interface
def test_suspension_endpoints(tmp_path):
    from conftest import merged_library
    from temsasim.app.server import create_app
    root = merged_library(tmp_path / "library")
    c = create_app(root).test_client()
    info = c.get("/api/suspension/maraton_13_dizel").get_json()
    assert info["stem"] in ("maraton_13", GENERIC) and info["linked"] == (info["stem"] == "maraton_13")
    assert info["manoeuvre_order"] == list(S.MANOEUVRES) and info["vehicle"]["seats"] == 55 and len(info["params"]) == len(S.PARAMS)
    assert {p["group"] for p in info["params"]} == set(info["groups"]) and GENERIC in [f["stem"] for f in info["files"]]
    gen = c.get(f"/api/suspension/maraton_13_dizel?file={GENERIC}").get_json()
    assert gen["stem"] == GENERIC and not gen["linked"]
    model = gen["model"]
    r = c.post("/api/suspension/maraton_13_dizel/analyse", json={"model": model, "passengers": 55, "parts": ["base", "sim", "roll", "comfort"],
                                                                 "manoeuvre": {"kind": "serit", "params": {"speed_kmh": 70}}, "radius_m": 150}).get_json()
    assert set(r) == {"summary", "modes", "dampers", "sim", "rollover", "comfort"}
    assert r["summary"]["mass_kg"] == pytest.approx(17240) and not r["summary"]["over_gross"] and len(r["modes"]) == 7
    assert r["sim"]["kind"] == "serit" and r["sim"]["params"]["speed_kmh"] == 70 and r["sim"]["explorer"]["signals"]
    ro = r["rollover"]
    assert ro["at_radius"]["radius_m"] == 150 and ro["manoeuvre_lift"]["speed_kmh"] > 70 and ro["steer"]["ackermann_deg"] == pytest.approx(math.degrees(6.92 / 150))
    assert ro["steer"]["at_static_deg"] > ro["steer"]["ackermann_deg"]
    # a higher centre of gravity and a softer damper travel through the request
    model2 = copy.deepcopy(model)
    model2["cog_z_m"] = 1.5
    model2["front"]["damper_tune"] = {"rebound_n": [v * 0.5 for v in model["dampers"][model["front"]["damper"]]["rebound_n"]]}
    r2 = c.post("/api/suspension/maraton_13_dizel/analyse", json={"model": model2, "passengers": 55, "parts": ["base", "roll"]}).get_json()
    assert r2["rollover"]["static_g"] < ro["static_g"] and r2["summary"]["damping_ratio"][0] < r["summary"]["damping_ratio"][0]
    assert r2["dampers"][0]["rebound_n"][0] == 0.5 * model["dampers"][model["front"]["damper"]]["rebound_n"][0]
    heavy = c.post("/api/suspension/maraton_13_dizel/analyse", json={"model": model, "passengers": 55, "luggage_kg": 3000, "parts": ["base"]}).get_json()
    assert heavy["summary"]["over_gross"]
    bad = c.post("/api/suspension/maraton_13_dizel/analyse", json={"model": {**model, "cog_x_m": 9.0}})
    assert bad.status_code == 400 and "ağırlık merkezi" in bad.get_json()["error"]
    # saving from the borrowed file makes a file of the vehicle's own
    electric = c.get("/api/suspension/citybus_12m_be").get_json()
    assert electric["stem"] == GENERIC and not electric["linked"]
    saved = c.post("/api/suspension/citybus_12m_be/save", json={"model": {**model2, "vehicles": []}, "stem": "citybus_12m_be", "name": "Şehir içi 12 m · süspansiyon"}).get_json()
    assert saved["saved"] == "suspension/citybus_12m_be.yaml" and saved["vehicles"] == ["citybus_12m_be"]
    again = c.get("/api/suspension/citybus_12m_be").get_json()
    assert again["linked"] and again["model"]["cog_z_m"] == 1.5 and again["model"]["name"].startswith("Şehir içi")
    assert c.get("/api/suspension/maraton_13_dizel").get_json()["stem"] == info["stem"]      # the other vehicle keeps its own
    # the live drive gets its steering and lean from the same file
    setup = c.post("/api/drive/setup", json={"vehicle": "citybus_12m_be", "passengers": 20}).get_json()
    h = setup["handling"]
    assert h["wheelbase_m"] == 6.92 and h["understeer_deg_per_g"] == 2.0 and 0 < h["rollover_g"] < h["rollover_static_g"] and h["roll_deg_per_g"] > 0
