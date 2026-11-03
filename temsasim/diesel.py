"""Backward-facing solver for a diesel (combustion) powertrain.

Same philosophy as ``simulator.simulate`` for the battery bus: the drive cycle
is followed exactly, the model works backwards from the wheel to the fuel, and
anything the vehicle cannot deliver is recorded as ``unmet`` rather than
quietly absorbed.

Step order, once per time step:

1.  Kinematics and road load -> wheel torque, as for the electric bus.
2.  Axle: propshaft torque from the axle loss map.
3.  Retarder idle drag at the gearbox output.
4.  Gear choice by the AMT strategy; gearbox input torque from the per-gear
    loss map. Below the launch speed the clutch slips and the engine holds
    its idle speed.
5.  Auxiliaries and climate as a mechanical load on the crankshaft: the
    alternator for the 24 V loads, the steering pump, the air compressor, the
    cooling fan, and the A/C compressor when the cabin needs cooling.
6.  Engine: the demanded torque is clipped to the full-load curve (shortfall
    -> unmet) and to the drag curve (excess braking -> friction brakes, which
    on a coach means the retarder first and the discs second). Fuel comes
    from the certified map; on the overrun it is zero.
7.  Cabin heating is taken from engine coolant heat as long as there is
    enough of it; the rest comes from the fuel-fired auxiliary heater and is
    booked as heater fuel, separately.

The totals close an energy balance from fuel energy (lower heating value)
down to the wheel and every loss in between, so the report can show where
each litre went.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .cycle import DriveCycle, Environment
from .messages import Note, tr_num
from .units import KWH, RPM, air_density
from .vehicle import Vehicle

ALTERNATOR_EFFICIENCY = 0.70          # VECTO bus declaration, conventional alternator
COOLANT_HEAT_SHARE = 0.28             # share of fuel LHV that reaches the coolant, warm engine


@dataclass
class DieselResult:
    """Everything the run produced: time series, totals and warnings."""

    vehicle: Vehicle
    cycle: DriveCycle
    environment: Environment
    dt_s: float
    series: dict
    totals: dict
    warnings: list
    unmet_samples: int
    kind: str = "diesel"

    @property
    def achievable(self) -> bool:
        return self.unmet_samples == 0

    @property
    def fuel_l_per_100km(self) -> float:
        return self.totals["fuel_total_l"] / max(self.totals["distance_km"], 1e-9) * 100.0

    @property
    def co2_g_per_km(self) -> float:
        return self.totals["co2_g"] / max(self.totals["distance_km"], 1e-9)

    @property
    def energy_per_km_kwh(self) -> float:
        """Fuel energy (LHV) per km, for like-for-like comparison with a battery bus."""
        return self.totals["fuel_energy_kwh"] / max(self.totals["distance_km"], 1e-9)

    def summary(self) -> str:
        t = self.totals
        lines = [
            f"{self.vehicle.name}  |  {self.cycle.name}  |  {self.environment.describe()}",
            f"  distance            {t['distance_km']:8.2f} km over {t['duration_s']/60:.1f} min",
            f"  fuel                {self.fuel_l_per_100km:8.2f} l/100 km "
            f"({t['fuel_total_l']:.2f} l, of which heater {t['heater_fuel_l']:.2f} l)",
            f"  CO2                 {self.co2_g_per_km:8.0f} g/km",
            f"  idle                {t['idle_fuel_l']:8.2f} l while stopped "
            f"({t['idle_fraction']:.0%} of the time)",
            f"  engine shaft work   {t['engine_shaft_kwh']:8.2f} kWh  "
            f"(mean {t['mean_engine_rpm']:.0f} rpm, {t['shifts']} shifts)",
            f"  friction brakes     {t['friction_brake_kwh']:8.2f} kWh, engine braking "
            f"{t['engine_brake_kwh']:.2f} kWh",
            f"  auxiliaries + HVAC  {t['auxiliary_kwh'] + t['hvac_kwh'] + t['fan_kwh']:8.2f} kWh mechanical",
        ]
        if not self.achievable:
            lines.append(f"  NOT ACHIEVABLE      {self.unmet_samples} samples "
                         f"({self.unmet_samples * self.dt_s:.0f} s) short of demand")
        for w in self.warnings:
            lines.append(f"  ! {w}")
        return "\n".join(lines)

    def energy_balance(self) -> dict:
        """Where the fuel energy went. Shaft work is split down to the wheel;
        the residual should be ~0 or the bookkeeping is wrong."""
        t = self.totals
        sinks = {
            "engine losses (heat, exhaust)": t["engine_loss_kwh"],
            "heater fuel": t["heater_fuel_kwh"],
            "rolling resistance": t["rolling_kwh"],
            "aerodynamic drag": t["aero_kwh"],
            "gradient (net)": t["grade_kwh"],
            "inertia (net)": t["inertia_kwh"],
            "friction brakes (incl. retarder braking)": t["friction_brake_kwh"],
            "engine braking": t["engine_brake_kwh"],
            "axle losses": t["axle_loss_kwh"],
            "gearbox losses": t["gearbox_loss_kwh"],
            "retarder idle drag": t["retarder_loss_kwh"],
            "clutch slip": t["clutch_loss_kwh"],
            "auxiliaries (mechanical)": t["auxiliary_kwh"],
            "cooling fan": t["fan_kwh"],
            "HVAC compressor and blowers": t["hvac_kwh"],
            "demand not delivered (unmet)": -t["undelivered_kwh"],
        }
        total_out = sum(sinks.values())
        sinks["residual (should be ~0)"] = t["fuel_energy_kwh"] - total_out
        return sinks


def simulate_diesel(
    vehicle: Vehicle,
    cycle: DriveCycle,
    environment: Environment | None = None,
    *,
    dt_s: float = 1.0,
    initial_cabin_c: float | None = None,
) -> DieselResult:
    """Run one drive cycle through the combustion powertrain."""
    if not vehicle.is_diesel:
        raise ValueError(f"{vehicle.name}: not a diesel vehicle (powertrain={vehicle.powertrain})")
    env = environment or Environment()
    cyc = cycle.resample(dt_s)
    n = cyc.time_s.size

    ch, tr, eng = vehicle.chassis, vehicle.transmission, vehicle.engine
    hvac, aux = vehicle.hvac, vehicle.auxiliaries

    cabin_c = env.ambient_c if initial_cabin_c is None else initial_cabin_c
    rho = air_density(env.ambient_c, env.air_pressure_pa)
    accel = cyc.acceleration()
    stops_per_hour = cyc.stop_count / max(cyc.duration_s / 3600.0, 1e-9)

    keys = ["time_s", "speed_mps", "accel_mps2", "grade_rad", "passengers", "doors_open",
            "mass_kg", "distance_m",
            "f_rolling_n", "f_aero_n", "f_grade_n", "f_inertia_n",
            "p_wheel_w", "p_rolling_w", "p_aero_w", "p_grade_w", "p_inertia_w",
            "p_friction_brake_w", "p_engine_brake_w", "gear",
            "engine_speed_rads", "engine_torque_nm", "engine_torque_max_nm", "p_engine_w",
            "fuel_g_per_s", "heater_fuel_g_per_s", "fuel_cum_g",
            "axle_loss_w", "gearbox_loss_w", "retarder_loss_w", "clutch_loss_w",
            "p_aux_w", "p_hvac_w", "p_fan_w", "idle", "unmet", "p_undelivered_w",
            "cabin_c", "hvac_q_w", "hvac_mode_code", "q_engine_heat_w", "q_heater_w",
            "q_doors_w", "q_envelope_w", "q_solar_w", "q_passengers_w", "q_ventilation_w"]
    s = {k: np.zeros(n) for k in keys}
    s["doors_open"] = cyc.doors_open.astype(float)
    hvac_modes = {"off": 0, "cooling": 1, "heat_pump": 2, "resistive": 3, "fuel_fired": 4}

    unmet_tolerance_w = 0.005 * eng.rated_power_w
    gear = 0
    t_last_shift = -1e9
    shifts = 0
    distance = 0.0
    fuel_cum = 0.0
    unmet = 0
    saturated_hvac_samples = 0
    warnings: list = []

    for i in range(n):
        t_now = float(cyc.time_s[i])
        v = float(cyc.speed_mps[i])
        a = float(accel[i])
        theta = float(cyc.grade_rad[i])
        pax = float(cyc.passengers[i])
        doors = bool(cyc.doors_open[i])

        mass = ch.mass(pax)
        moving = v > 0.05

        # --- road load ---------------------------------------------------------
        m_eq = ch.equivalent_rotating_mass_kg(tr.referred_inertia_kgm2(gear, eng.inertia_kgm2) if moving else 0.0)
        forces = ch.resistive_force(v, mass_kg=mass, grade_rad=theta,
                                    air_density=rho, headwind_mps=env.headwind_mps)
        f_roll = float(forces["rolling"]) * float(cyc.surface_factor[i])
        f_aero = float(forces["aero"])
        f_grade = float(forces["grade"]) if moving else 0.0
        f_inertia = (mass + m_eq) * a if moving else 0.0
        f_total = f_roll + f_aero + f_grade + f_inertia
        w_wheel = v / ch.tyre_dynamic_radius_m
        t_wheel = f_total * ch.tyre_dynamic_radius_m
        p_wheel = f_total * v

        # --- auxiliaries and climate (mechanical, on the crankshaft) -----------
        aux_d = aux.power(v, night=env.night, stops_per_hour=stops_per_hour, doors_open=doors)
        lv_w = aux_d["lv_loads_w"] * aux.dcdc_efficiency          # the 24 V loads themselves
        p_aux = lv_w / ALTERNATOR_EFFICIENCY + aux_d["steering_w"] + aux_d["air_compressor_w"]
        p_fan = eng.fan_power_w

        cabin_c, p_hvac_elec, hv = hvac.step(
            cabin_c, env.ambient_c, dt_s, n_passengers=pax, doors_open=doors,
            solar_irradiance_w_per_m2=env.solar_irradiance_w_per_m2,
            shading_factor=env.shading_factor)
        if hv["saturated"]:
            saturated_hvac_samples += 1
        q_hvac = hv["q_hvac_w"]
        if q_hvac < 0.0:
            p_hvac = p_hvac_elec                 # compressor + fans, mechanical
        elif q_hvac > 0.0:
            p_hvac = hvac.blower_power_w         # heat comes from coolant / heater below
        else:
            p_hvac = 0.0

        # --- driveline backwards to the engine --------------------------------------
        axle_loss = gbx_loss = t_ret = clutch_loss_w = 0.0
        idle = not moving
        if moving:
            w_prop, t_prop, axle_loss = tr.axle_input(w_wheel, t_wheel)
            t_ret = tr.retarder_drag_nm(w_prop)
            t_out = t_prop + t_ret
            new_gear = tr.choose_gear(w_prop, t_out, eng, gear, t_now - t_last_shift, v)
            if new_gear != gear:
                gear, t_last_shift = new_gear, t_now
                shifts += 1
                m_eq = ch.equivalent_rotating_mass_kg(tr.referred_inertia_kgm2(gear, eng.inertia_kgm2))
            w_in, t_in, gbx_loss = tr.gearbox_input(w_prop, t_out, gear)
            t_in += tr.retarder_input_drag_nm(w_in)
            w_eng = max(w_in, eng.idle_speed_rads)
            clutch_loss_w = max(t_in, 0.0) * (w_eng - w_in)
        else:
            w_prop = w_in = 0.0
            t_in = 0.0
            w_eng = eng.idle_speed_rads

        t_aux = (p_aux + p_hvac + p_fan) / w_eng
        t_req = t_in + t_aux

        # --- engine ---------------------------------------------------------------
        t_max = eng.max_torque(w_eng)
        if tr.gear_max_torque_nm and moving:
            t_max = min(t_max, float(tr.gear_max_torque_nm[gear]))
        t_drag = eng.drag_torque(w_eng)
        p_undelivered = p_friction = 0.0
        if t_req > t_max:
            t_eng = t_max
            p_undelivered = (t_req - t_max) * w_eng
            if p_undelivered > unmet_tolerance_w:
                unmet += 1
                s["unmet"][i] = p_undelivered
        elif t_req < t_drag:
            t_eng = t_drag
            p_friction = (t_drag - t_req) * w_eng
        else:
            t_eng = t_req
        p_eng = t_eng * w_eng
        fuel_gps = eng.fuel_rate_g_per_s(w_eng, t_eng)

        # --- cabin heat source: coolant first, then the fuel-fired heater ------------
        q_engine_heat = q_heater = heater_gps = 0.0
        if q_hvac > 0.0:
            coolant_available = COOLANT_HEAT_SHARE * eng.fuel_energy_j(fuel_gps)
            q_engine_heat = min(q_hvac, coolant_available)
            q_heater = q_hvac - q_engine_heat
            if q_heater > 1.0:
                heater_gps = (q_heater / hvac.fuel_fired_efficiency) / eng.fuel_lhv_j_per_kg * 1000.0
        fuel_cum += (fuel_gps + heater_gps) * dt_s
        distance += v * dt_s

        # --- record ------------------------------------------------------------------
        s["time_s"][i] = t_now
        s["speed_mps"][i] = v
        s["accel_mps2"][i] = a
        s["grade_rad"][i] = theta
        s["passengers"][i] = pax
        s["mass_kg"][i] = mass
        s["distance_m"][i] = distance
        s["f_rolling_n"][i] = f_roll
        s["f_aero_n"][i] = f_aero
        s["f_grade_n"][i] = f_grade
        s["f_inertia_n"][i] = f_inertia
        s["p_wheel_w"][i] = p_wheel
        s["p_rolling_w"][i] = f_roll * v
        s["p_aero_w"][i] = f_aero * v
        s["p_grade_w"][i] = f_grade * v
        s["p_inertia_w"][i] = f_inertia * v
        s["p_friction_brake_w"][i] = p_friction
        s["p_engine_brake_w"][i] = -p_eng if p_eng < 0 else 0.0
        s["gear"][i] = gear + 1 if moving else 0
        s["engine_speed_rads"][i] = w_eng
        s["engine_torque_nm"][i] = t_eng
        s["engine_torque_max_nm"][i] = t_max
        s["p_engine_w"][i] = p_eng
        s["fuel_g_per_s"][i] = fuel_gps
        s["heater_fuel_g_per_s"][i] = heater_gps
        s["fuel_cum_g"][i] = fuel_cum
        s["axle_loss_w"][i] = axle_loss * w_prop
        s["gearbox_loss_w"][i] = gbx_loss * w_in
        s["retarder_loss_w"][i] = t_ret * w_prop
        s["clutch_loss_w"][i] = clutch_loss_w
        s["p_aux_w"][i] = p_aux
        s["p_hvac_w"][i] = p_hvac
        s["p_fan_w"][i] = p_fan
        s["idle"][i] = 1.0 if idle else 0.0
        s["p_undelivered_w"][i] = p_undelivered
        s["cabin_c"][i] = cabin_c
        s["hvac_q_w"][i] = q_hvac
        s["hvac_mode_code"][i] = hvac_modes.get(hv["hvac_mode"], 0)
        s["q_engine_heat_w"][i] = q_engine_heat
        s["q_heater_w"][i] = q_heater
        s["q_doors_w"][i] = hv["q_doors_w"]
        s["q_envelope_w"][i] = hv["q_envelope_w"]
        s["q_solar_w"][i] = hv["q_solar_w"]
        s["q_passengers_w"][i] = hv["q_passengers_w"]
        s["q_ventilation_w"][i] = hv["q_ventilation_w"]

    # --- integrate --------------------------------------------------------------------
    def kwh(arr) -> float:
        return float(np.sum(arr) * dt_s / KWH)

    distance_km = distance / 1000.0
    fuel_g = float(np.sum(s["fuel_g_per_s"]) * dt_s)
    heater_g = float(np.sum(s["heater_fuel_g_per_s"]) * dt_s)
    idle_g = float(np.sum(s["fuel_g_per_s"] * s["idle"]) * dt_s)
    fuel_energy_kwh = eng.fuel_energy_j(fuel_g + heater_g) / KWH
    shaft_kwh = kwh(np.clip(s["p_engine_w"], 0.0, None))
    moving_mask = s["speed_mps"] > 0.05
    gear_share = {}
    if moving_mask.any():
        g = s["gear"][moving_mask].astype(int)
        for k in range(1, tr.n_gears + 1):
            gear_share[k] = float(np.mean(g == k))

    totals = {
        "distance_km": distance_km,
        "duration_s": float(cyc.time_s[-1] - cyc.time_s[0]),
        "mean_speed_kmh": distance_km / max(float(cyc.time_s[-1] - cyc.time_s[0]) / 3600.0, 1e-9),
        "fuel_g": fuel_g, "fuel_l": eng.litres(fuel_g),
        "heater_fuel_g": heater_g, "heater_fuel_l": eng.litres(heater_g),
        "fuel_total_g": fuel_g + heater_g, "fuel_total_l": eng.litres(fuel_g + heater_g),
        "idle_fuel_g": idle_g, "idle_fuel_l": eng.litres(idle_g),
        "idle_fraction": float(np.mean(s["idle"])),
        "co2_g": eng.co2_g(fuel_g + heater_g),
        "fuel_energy_kwh": fuel_energy_kwh,
        "heater_fuel_kwh": eng.fuel_energy_j(heater_g) / KWH,
        "engine_shaft_kwh": shaft_kwh,
        "engine_brake_kwh": kwh(s["p_engine_brake_w"]),
        "engine_loss_kwh": eng.fuel_energy_j(fuel_g) / KWH - shaft_kwh,
        "rolling_kwh": kwh(s["p_rolling_w"]),
        "aero_kwh": kwh(s["p_aero_w"]),
        "grade_kwh": kwh(s["p_grade_w"]),
        "inertia_kwh": kwh(s["p_inertia_w"]),
        "friction_brake_kwh": kwh(s["p_friction_brake_w"]),
        "axle_loss_kwh": kwh(s["axle_loss_w"]),
        "gearbox_loss_kwh": kwh(s["gearbox_loss_w"]),
        "retarder_loss_kwh": kwh(s["retarder_loss_w"]),
        "clutch_loss_kwh": kwh(s["clutch_loss_w"]),
        "auxiliary_kwh": kwh(s["p_aux_w"]),
        "fan_kwh": kwh(s["p_fan_w"]),
        "hvac_kwh": kwh(s["p_hvac_w"]),
        "engine_heat_to_cabin_kwh": kwh(s["q_engine_heat_w"]),
        "heater_heat_kwh": kwh(s["q_heater_w"]),
        "undelivered_kwh": kwh(s["p_undelivered_w"]),
        "mean_engine_rpm": float(np.mean(s["engine_speed_rads"][moving_mask]) / RPM) if moving_mask.any() else eng.idle_speed_rads / RPM,
        "mean_cabin_c": float(np.mean(s["cabin_c"])),
        "shifts": shifts,
        "gear_share": gear_share,
        "stops": cyc.stop_count,
        "stops_per_km": cyc.stops_per_km,
        "peak_engine_power_kw": float(np.max(s["p_engine_w"])) / 1e3,
    }
    totals["l_per_100km"] = totals["fuel_total_l"] / max(distance_km, 1e-9) * 100.0
    totals["co2_g_per_km"] = totals["co2_g"] / max(distance_km, 1e-9)
    totals["energy_per_km_kwh"] = fuel_energy_kwh / max(distance_km, 1e-9)
    e = max(fuel_energy_kwh, 1e-9)
    totals["hvac_share"] = (totals["hvac_kwh"] + totals["heater_fuel_kwh"]) / e
    totals["auxiliary_share"] = (totals["auxiliary_kwh"] + totals["fan_kwh"]) / e
    totals["idle_share"] = eng.fuel_energy_j(idle_g) / KWH / e

    # --- warnings ---------------------------------------------------------------------
    comp_tr = {"chassis": "şasi", "transmission": "şanzıman ve aks", "engine": "motor",
               "hvac": "klima", "auxiliaries": "yardımcılar"}
    if saturated_hvac_samples > 0.02 * n:
        mins, frac = saturated_hvac_samples * dt_s / 60, saturated_hvac_samples / n
        warnings.append(Note(
            f"HVAC saturated for {mins:.0f} min ({frac:.0%} of the run)",
            f"Klima {mins:.0f} dakika boyunca tam kapasitede çalıştı (koşunun yüzde "
            f"{100 * frac:.0f}) ve kabin ayar sıcaklığına ulaşamadı."))
    if eng.synthetic:
        warnings.append(Note(
            "engine map is synthetic: fuel figures are a Willans-line estimate",
            "Motorun yakıt haritası sentetik: yakıt değerleri kaba bir tahmin, "
            "ölçülmüş haritayı yükleyin."))
    if tr.gearbox_loss_map is None or tr.axle_loss_map is None:
        warnings.append(Note(
            "driveline losses use constant efficiencies, not measured loss maps",
            "Şanzıman ya da aks kayıpları sabit verimle hesaplandı; ölçülmüş kayıp "
            "haritası yüklenirse düşük yükte sonuç değişir."))
    if moving_mask.any() and shifts > 0.4 * moving_mask.sum() * dt_s:
        warnings.append(Note(
            "the shift strategy is hunting: more than one shift every 2.5 s of driving",
            "Vites stratejisi sık vites değiştiriyor (sürüşte her 2,5 saniyede birden "
            "fazla): vites penceresi ve tork rezervi ayarlarını kontrol edin."))
    if not vehicle.validated:
        miss = vehicle.unvalidated_components()
        warnings.append(Note(
            "vehicle contains unvalidated component data (" + ", ".join(miss)
            + "): indicative only, not defensible",
            "Araçta ölçümle doğrulanmamış bileşen verisi var ("
            + ", ".join(comp_tr.get(m, m) for m in miss)
            + "): sonuçlar yol gösterici, savunulabilir değil."))

    return DieselResult(vehicle=vehicle, cycle=cyc, environment=env, dt_s=dt_s,
                        series=s, totals=totals, warnings=warnings, unmet_samples=unmet)
