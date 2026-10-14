"""The backward-facing quasi-static solver.

Backward-facing means the drive cycle is assumed to be followed exactly, and
the model works backwards from the required wheel speed and torque to the
current the battery must supply. It is fast -- a full day of bus duty runs in
under a second -- and it is the right tool for energy, range and sizing work.

It is the wrong tool for controls development, because nothing in it can fail
to track the cycle. Where the vehicle physically cannot deliver the demand, the
solver does not silently produce a wrong number: it records an ``unmet``
sample, reports how far it fell short, and the results object refuses to
present the run as achievable. That honesty is what makes it safe to build a
tender answer on.

Step order, once per time step:

1.  Kinematics: speed, acceleration, gradient, occupancy, door state.
2.  Road load and inertia -> force and torque at the wheel.
3.  Driveline -> motor shaft speed and torque, gear selection.
4.  Brake blending, if decelerating: how much the machine takes, how much the
    friction brakes take.
5.  Motor map -> DC power.
6.  Auxiliaries and HVAC -> DC power. The cabin thermal state advances here.
7.  Battery -> current, voltage, SOC, ohmic heat, and any clipping.
8.  Pack thermal state advances, and its own conditioning power is charged to
    the next step.

Charging the thermal management power to the next step is a deliberate
one-sample lag that avoids an algebraic loop between pack heat and pack
cooling. At a 1 s step on a thermal mass with a time constant of tens of
minutes, the error is immaterial.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .cycle import DriveCycle, Environment
from .messages import Note, tr_num
from .units import KWH, air_density
from .vehicle import Vehicle


@dataclass
class SimulationResult:
    """Everything the run produced: time series, totals and warnings."""

    vehicle: Vehicle
    cycle: DriveCycle
    environment: Environment
    dt_s: float
    series: dict
    totals: dict
    warnings: list
    unmet_samples: int
    initial_soc: float
    soh: float

    @property
    def achievable(self) -> bool:
        return self.unmet_samples == 0

    @property
    def energy_per_km_kwh(self) -> float:
        return self.totals["battery_energy_kwh"] / max(self.totals["distance_km"], 1e-9)

    @property
    def range_km(self) -> float:
        """Range on the usable SOC window at this consumption rate."""
        return self.vehicle.battery.usable_energy_kwh * self.soh / max(
            self.energy_per_km_kwh, 1e-9)

    def summary(self) -> str:
        t = self.totals
        lines = [
            f"{self.vehicle.name}  |  {self.cycle.name}  |  {self.environment.describe()}",
            f"  distance            {t['distance_km']:8.2f} km over {t['duration_s']/60:.1f} min",
            f"  consumption         {self.energy_per_km_kwh:8.3f} kWh/km",
            f"  battery energy      {t['battery_energy_kwh']:8.2f} kWh "
            f"(SOC {self.initial_soc:.0%} -> {self.series['soc'][-1]:.0%})",
            f"  regen recovered     {t['regen_recovered_kwh']:8.2f} kWh "
            f"({t['regen_fraction_of_braking']:.0%} of braking energy)",
            f"  regen lost to limits{t['regen_lost_kwh']:8.2f} kWh",
            f"  HVAC                {t['hvac_kwh']:8.2f} kWh "
            f"({t['hvac_share']:.0%} of consumption)",
            f"  auxiliaries         {t['auxiliary_kwh']:8.2f} kWh "
            f"({t['auxiliary_share']:.0%})",
            f"  traction net        {t['traction_kwh']:8.2f} kWh "
            f"({t['traction_share']:.0%})",
            f"  estimated range     {self.range_km:8.1f} km on usable SOC window",
        ]
        if not self.achievable:
            lines.append(f"  NOT ACHIEVABLE      {self.unmet_samples} samples "
                         f"({self.unmet_samples * self.dt_s:.0f} s) short of demand")
        for w in self.warnings:
            lines.append(f"  ! {w}")
        return "\n".join(lines)

    def energy_balance(self) -> dict:
        """Where every kilowatt-hour went, as a closed balance.

        The residual is reported rather than hidden. If it is not close to zero
        the model has a bookkeeping bug, and you should not trust the rest of
        the numbers.
        """
        t = self.totals
        sinks = {
            "rolling resistance": t["rolling_kwh"],
            "aerodynamic drag": t["aero_kwh"],
            "gradient (net)": t["grade_kwh"],
            "inertia (net)": t["inertia_kwh"],
            "friction brakes": t["friction_brake_kwh"],
            "driveline losses": t["driveline_loss_kwh"],
            "motor + inverter losses": t["motor_loss_kwh"],
            "battery internal losses": t["battery_loss_kwh"],
            "HVAC": t["hvac_kwh"],
            "auxiliaries": t["auxiliary_kwh"],
            "pack thermal management": t["pack_thermal_kwh"],
            "demand not delivered (unmet)": -t["undelivered_kwh"],
        }
        total_out = sum(sinks.values())
        sinks["residual (should be ~0)"] = t["cell_energy_kwh"] - total_out
        return sinks


def simulate(
    vehicle: Vehicle,
    cycle: DriveCycle,
    environment: Environment | None = None,
    *,
    dt_s: float = 1.0,
    initial_soc: float | None = None,
    initial_cabin_c: float | None = None,
    initial_pack_c: float | None = None,
    soh: float = 1.0,
) -> SimulationResult:
    """Run one drive cycle and return the full result."""
    env = environment or Environment()
    cyc = cycle.resample(dt_s)
    n = cyc.time_s.size

    ctrl = vehicle.control
    ch, dl, mot = vehicle.chassis, vehicle.driveline, vehicle.motor
    batt, hvac, aux = vehicle.battery, vehicle.hvac, vehicle.auxiliaries

    soc = ctrl.soc_start if initial_soc is None else initial_soc

    if initial_cabin_c is not None:
        cabin_c = initial_cabin_c
    elif ctrl.cabin_preconditioned:
        cabin_c = ctrl.precondition_cabin_c if ctrl.precondition_cabin_c is not None else (
            hvac.setpoint_heating_c if env.ambient_c < hvac.setpoint_heating_c
            else hvac.setpoint_cooling_c if env.ambient_c > hvac.setpoint_cooling_c
            else env.ambient_c)
    else:
        cabin_c = env.ambient_c

    if initial_pack_c is not None:
        pack_c = initial_pack_c
    elif ctrl.pack_preconditioned:
        pack_c = ctrl.precondition_pack_c if ctrl.precondition_pack_c is not None else 20.0
    else:
        pack_c = env.ambient_c

    rho = air_density(env.ambient_c, env.air_pressure_pa)
    accel = cyc.acceleration()
    stops_per_hour = cyc.stop_count / max(cyc.duration_s / 3600.0, 1e-9)

    keys = ["time_s", "speed_mps", "accel_mps2", "grade_rad", "passengers",
            "doors_open", "mass_kg", "distance_m",
            "f_rolling_n", "f_aero_n", "f_grade_n", "f_inertia_n",
            "p_wheel_w", "p_rolling_w", "p_aero_w", "p_grade_w", "p_inertia_w",
            "p_friction_brake_w", "motor_speed_rads", "motor_torque_nm",
            "motor_efficiency", "p_motor_mech_w", "p_motor_elec_w",
            "driveline_loss_w", "motor_loss_w", "gear",
            "p_aux_w", "p_hvac_w", "p_pack_thermal_w", "p_battery_w",
            "battery_current_a", "battery_voltage_v", "battery_loss_w",
            "soc", "cabin_c", "pack_c", "hvac_q_w", "hvac_mode_code",
            "q_doors_w", "q_envelope_w", "q_solar_w", "q_passengers_w",
            "q_ventilation_w", "fuel_heat_w", "unmet", "regen_lost_w", "p_undelivered_w",
            "air_compressor_w"]
    s = {k: np.zeros(n) for k in keys}
    s["doors_open"] = cyc.doors_open.astype(float)
    s["unmet"] = np.zeros(n)

    hvac_modes = {"off": 0, "cooling": 1, "heat_pump": 2, "resistive": 3, "fuel_fired": 4}

    # A sample counts as unmet only when the shortfall exceeds 0.5 % of peak
    # motor power. Below that it is the numerical noise of differentiating a
    # 1 s speed trace at the edge of the power envelope, not a vehicle that
    # cannot do the job. The undelivered energy is still booked in full in the
    # energy balance either way, so nothing is hidden.
    unmet_tolerance_w = 0.005 * mot.peak_power_w

    gear = 0
    distance = 0.0
    p_pack_thermal = 0.0
    throughput_ah = 0.0
    warnings: list = []
    unmet = 0
    saturated_hvac_samples = 0
    above_continuous_samples = 0

    for i in range(n):
        v = float(cyc.speed_mps[i])
        a = float(accel[i])
        theta = float(cyc.grade_rad[i])
        pax = float(cyc.passengers[i])
        doors = bool(cyc.doors_open[i])

        mass = ch.mass(pax)
        gear = dl.gear_for_speed(v, gear)
        m_eq = ch.equivalent_rotating_mass_kg(dl.referred_inertia_kgm2(gear))

        forces = ch.resistive_force(v, mass_kg=mass, grade_rad=theta,
                                    air_density=rho, headwind_mps=env.headwind_mps)
        f_roll = float(forces["rolling"]) * float(cyc.surface_factor[i])
        f_aero = float(forces["aero"])
        f_grade = float(forces["grade"])
        f_inertia = (mass + m_eq) * a
        f_total = f_roll + f_aero + f_grade + f_inertia

        w_wheel = v / ch.tyre_dynamic_radius_m
        t_wheel = f_total * ch.tyre_dynamic_radius_m
        p_wheel = f_total * v

        # --- brake blending ------------------------------------------------
        p_friction = 0.0
        t_wheel_machine = t_wheel
        if t_wheel < 0.0:
            if (not ctrl.regen_enabled) or v < ctrl.min_regen_speed_mps:
                t_wheel_machine = 0.0
            else:
                f_brake = -f_total
                f_comfort = (mass + m_eq) * ctrl.max_regen_decel_mps2
                f_machine = min(f_brake * ctrl.regen_blend_fraction, f_comfort)
                t_wheel_machine = -f_machine * ch.tyre_dynamic_radius_m
            p_friction = (t_wheel - t_wheel_machine) * w_wheel  # negative

        # --- driveline and motor -------------------------------------------
        w_mot, t_mot, eta_dl = dl.wheel_to_motor(w_wheel, t_wheel_machine, gear)

        p_undelivered = 0.0
        t_limit = mot.max_torque(w_mot) if t_mot >= 0 else mot.max_regen_torque(w_mot)
        if abs(t_mot) > t_limit:
            t_mot = np.sign(t_mot) * t_limit
            # What the clipped machine actually puts through the driveline to
            # the wheel. Every downstream loss must be computed from this, not
            # from the demand, or the shortfall gets booked as a driveline loss.
            t_wheel_actual, _, _ = _motor_to_wheel(dl, w_mot, t_mot, gear)
            if t_wheel > 0:
                p_undelivered = (t_wheel_machine - t_wheel_actual) * w_wheel
                if p_undelivered > unmet_tolerance_w:
                    unmet += 1
                    s["unmet"][i] = p_undelivered
            else:
                # Machine cannot absorb it all; friction brakes take the rest.
                p_friction = (t_wheel - t_wheel_actual) * w_wheel
            t_wheel_machine = t_wheel_actual

        if abs(t_mot) > mot.continuous_torque_nm:
            above_continuous_samples += 1

        p_elec_mot, eta_mot, p_mech_mot = mot.electrical_power(w_mot, t_mot)
        p_wheel_machine = t_wheel_machine * w_wheel
        dl_loss = abs(p_wheel_machine - p_mech_mot)
        mot_loss = abs(p_elec_mot - p_mech_mot)

        # --- auxiliaries and climate ---------------------------------------
        aux_d = aux.power(v, night=env.night, stops_per_hour=stops_per_hour,
                          doors_open=doors)
        p_aux = aux_d["total_w"]

        cabin_c, p_hvac, hv = hvac.step(
            cabin_c, env.ambient_c, dt_s,
            n_passengers=pax, doors_open=doors,
            solar_irradiance_w_per_m2=env.solar_irradiance_w_per_m2,
            shading_factor=env.shading_factor)
        if hv["saturated"]:
            saturated_hvac_samples += 1

        # --- battery ---------------------------------------------------------
        p_thermal_used = p_pack_thermal
        p_demand = p_elec_mot + p_aux + p_hvac + p_thermal_used
        b = batt.solve(p_demand, soc, pack_c, dt_s, soh)
        soc = b["soc"]

        regen_lost = 0.0
        if b["limited"] and p_demand < 0.0:
            regen_lost = b["power_w"] - p_demand  # positive: energy not accepted
            p_friction -= regen_lost
        elif b["limited"] and p_demand > 0.0:
            shortfall_elec = p_demand - b["power_w"]
            p_undelivered += shortfall_elec
            if shortfall_elec > unmet_tolerance_w and s["unmet"][i] == 0.0:
                unmet += 1
            s["unmet"][i] = max(s["unmet"][i], shortfall_elec)

        throughput_ah += abs(b["current_a"]) * dt_s / 3600.0
        pack_c, p_pack_thermal = batt.thermal_step(pack_c, b["heat_w"],
                                                   env.ambient_c, dt_s)

        distance += v * dt_s

        # --- record ----------------------------------------------------------
        s["time_s"][i] = cyc.time_s[i]
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
        s["motor_speed_rads"][i] = w_mot
        s["motor_torque_nm"][i] = t_mot
        s["motor_efficiency"][i] = eta_mot
        s["p_motor_mech_w"][i] = p_mech_mot
        s["p_motor_elec_w"][i] = p_elec_mot
        s["driveline_loss_w"][i] = dl_loss
        s["motor_loss_w"][i] = mot_loss
        s["gear"][i] = gear
        s["p_aux_w"][i] = p_aux
        s["air_compressor_w"][i] = aux_d["air_compressor_w"]
        s["p_hvac_w"][i] = p_hvac
        s["p_pack_thermal_w"][i] = p_thermal_used
        s["p_undelivered_w"][i] = p_undelivered
        s["p_battery_w"][i] = b["power_w"]
        s["battery_current_a"][i] = b["current_a"]
        s["battery_voltage_v"][i] = b["voltage_v"]
        s["battery_loss_w"][i] = b["heat_w"]
        s["soc"][i] = soc
        s["cabin_c"][i] = cabin_c
        s["pack_c"][i] = pack_c
        s["hvac_q_w"][i] = hv["q_hvac_w"]
        s["hvac_mode_code"][i] = hvac_modes.get(hv["hvac_mode"], 0)
        s["q_doors_w"][i] = hv["q_doors_w"]
        s["q_envelope_w"][i] = hv["q_envelope_w"]
        s["q_solar_w"][i] = hv["q_solar_w"]
        s["q_passengers_w"][i] = hv["q_passengers_w"]
        s["q_ventilation_w"][i] = hv["q_ventilation_w"]
        s["fuel_heat_w"][i] = hv["fuel_heat_w"]
        s["regen_lost_w"][i] = regen_lost

    # --- integrate ---------------------------------------------------------
    def kwh(arr) -> float:
        return float(np.sum(arr) * dt_s / KWH)

    distance_km = distance / 1000.0
    p_batt = s["p_battery_w"]
    cell_power = s["p_battery_w"] + s["battery_loss_w"]

    braking_energy = -kwh(np.clip(s["p_wheel_w"], None, 0.0))
    regen_recovered = -kwh(np.clip(s["p_motor_elec_w"], None, 0.0))

    totals = {
        "distance_km": distance_km,
        "duration_s": float(cyc.time_s[-1] - cyc.time_s[0]),
        "battery_energy_kwh": kwh(p_batt),
        "cell_energy_kwh": kwh(cell_power),
        "traction_kwh": kwh(np.clip(s["p_motor_elec_w"], 0.0, None)),
        "regen_recovered_kwh": regen_recovered,
        "regen_lost_kwh": kwh(s["regen_lost_w"]),
        "braking_energy_kwh": braking_energy,
        "regen_fraction_of_braking": regen_recovered / max(braking_energy, 1e-9),
        "hvac_kwh": kwh(s["p_hvac_w"]),
        "auxiliary_kwh": kwh(s["p_aux_w"]),
        "pack_thermal_kwh": kwh(s["p_pack_thermal_w"]),
        "fuel_heat_kwh": kwh(s["fuel_heat_w"]),
        "rolling_kwh": kwh(s["p_rolling_w"]),
        "aero_kwh": kwh(s["p_aero_w"]),
        "grade_kwh": kwh(s["p_grade_w"]),
        "inertia_kwh": kwh(s["p_inertia_w"]),
        "friction_brake_kwh": -kwh(s["p_friction_brake_w"]),
        "driveline_loss_kwh": kwh(s["driveline_loss_w"]),
        "motor_loss_kwh": kwh(s["motor_loss_w"]),
        "battery_loss_kwh": kwh(s["battery_loss_w"]),
        "undelivered_kwh": kwh(s["p_undelivered_w"]),
        "throughput_ah": throughput_ah,
        "throughput_ah_per_cell": throughput_ah / max(batt.cells_parallel, 1),
        "mean_c_rate": float(np.mean(np.abs(s["battery_current_a"]))
                             / max(batt.capacity_ah * soh, 1e-9)),
        "mean_soc": float(np.mean(s["soc"])),
        "mean_pack_c": float(np.mean(s["pack_c"])),
        "mean_cabin_c": float(np.mean(s["cabin_c"])),
        "peak_motor_power_kw": float(np.max(np.abs(s["p_motor_mech_w"]))) / 1e3,
        "peak_battery_power_kw": float(np.max(np.abs(s["p_battery_w"]))) / 1e3,
        "stops": cyc.stop_count,
        "stops_per_km": cyc.stops_per_km,
    }
    energy = max(totals["battery_energy_kwh"], 1e-9)
    totals["hvac_share"] = totals["hvac_kwh"] / energy
    totals["auxiliary_share"] = totals["auxiliary_kwh"] / energy
    totals["traction_share"] = (totals["traction_kwh"] - regen_recovered) / energy
    totals["energy_per_km_kwh"] = totals["battery_energy_kwh"] / max(distance_km, 1e-9)

    # --- warnings ----------------------------------------------------------
    comp_tr = {"chassis": "şasi", "driveline": "aktarma", "motor": "motor",
               "battery": "batarya", "hvac": "klima", "auxiliaries": "yardımcılar",
               "aging": "yaşlanma"}
    if saturated_hvac_samples > 0.02 * n:
        mins, frac = saturated_hvac_samples * dt_s / 60, saturated_hvac_samples / n
        warnings.append(Note(
            f"HVAC saturated for {mins:.0f} min ({frac:.0%} of the run): the cabin never "
            f"reached setpoint, so this run understates the real climate energy for a bus "
            f"that would have kept trying with a larger system",
            f"Klima {mins:.0f} dakika boyunca tam kapasitede çalıştı (koşunun yüzde "
            f"{100 * frac:.0f}) ve kabin ayar sıcaklığına ulaşamadı. Daha büyük bir sistem "
            f"daha fazla enerji harcardı; bu koşu gerçek klima enerjisini olduğundan düşük "
            f"gösteriyor."))
    if above_continuous_samples > 0.10 * n:
        frac = above_continuous_samples / n
        warnings.append(Note(
            f"motor above continuous torque for {frac:.0%} of the run: check the thermal "
            f"rating with the supplier",
            f"Motor koşunun yüzde {100 * frac:.0f} kadarında sürekli torkunun üstünde "
            f"çalıştı: ısıl sınırları tedarikçiyle kontrol edin."))
    if totals["regen_lost_kwh"] > 0.02 * energy:
        lost = totals["regen_lost_kwh"]
        warnings.append(Note(
            f"{lost:.2f} kWh of regen was refused by the pack (cold charge acceptance or "
            f"high SOC) and burned in the friction brakes",
            f"{tr_num(lost, 2)} kWh rejeneratif enerjiyi batarya kabul etmedi (soğuk paket "
            f"ya da yüksek SOC); bu enerji sürtünmeli frenlerde ısıya dönüştü."))
    if s["soc"][-1] <= batt.soc_min + 1e-6:
        warnings.append(Note("pack hit its minimum SOC during the run",
                             "Batarya koşu sırasında alt SOC sınırına indi."))
    if not vehicle.validated:
        miss = vehicle.unvalidated_components()
        warnings.append(Note(
            "vehicle contains unvalidated component data (" + ", ".join(miss)
            + "): indicative only, not defensible",
            "Araçta ölçümle doğrulanmamış bileşen verisi var ("
            + ", ".join(comp_tr.get(m, m) for m in miss)
            + "): sonuçlar yol gösterici, savunulabilir değil."))

    return SimulationResult(
        vehicle=vehicle, cycle=cyc, environment=env, dt_s=dt_s, series=s,
        totals=totals, warnings=warnings, unmet_samples=unmet,
        initial_soc=ctrl.soc_start if initial_soc is None else initial_soc,
        soh=soh)


def _motor_to_wheel(driveline, motor_speed_rads: float, motor_torque_nm: float,
                    gear: int = 0):
    """Forward step, used only to work out what the machine actually absorbed
    after its torque limit clipped the demand."""
    ratio = driveline.total_ratio(gear)
    w_wheel = motor_speed_rads / ratio
    eta = driveline.efficiency(w_wheel, motor_torque_nm * ratio)
    if motor_torque_nm >= 0.0:
        t_wheel = motor_torque_nm * ratio * eta
    else:
        t_wheel = motor_torque_nm * ratio / eta
    # Exact inverse of Driveline.wheel_to_motor, which adds the spin loss on the
    # way in; leaving it out here overstates delivered torque by ~45 Nm.
    t_wheel -= np.sign(w_wheel) * driveline.spin_loss_nm
    return t_wheel, w_wheel, eta
