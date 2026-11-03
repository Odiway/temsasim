"""Cabin thermal model and HVAC electrical demand.

This is the module that does not exist in ADVISOR, where climate control is a
single constant accessory load. On a diesel bus that approximation was almost
defensible, because cabin heat came free from engine coolant. On a battery bus
it is the difference between a credible range figure and an embarrassing one:
in a cold climate the heater can draw more average power than traction.

The cabin is one lumped thermal mass with five load paths::

    C_cab * dT/dt = Q_envelope + Q_ventilation + Q_doors + Q_passengers
                    + Q_solar + Q_hvac

**Envelope** ``Q = UA * (T_amb - T_cab)``, with UA built from the glazed and
opaque areas separately. Bus glazing is enormous -- often 25-35 % of the
surface -- and single-glazed side windows run around 5.5 W/(m^2 K) against
about 1.2 for an insulated wall panel, so glazing usually dominates UA.

**Ventilation** Fresh air required for air quality, ``m_dot * cp * dT``.
Typically specified per passenger.

**Doors** This is the big one for a city bus and the reason a route model beats
a drive cycle. Every time the doors open at a stop, gravity drives a
bidirectional air exchange through the opening. The standard Brown-Solvason
relation for a vertical opening gives the one-way volumetric exchange as

    Q_door = (1/3) * C_d * W * H * sqrt(g * H * |dT| / T_mean)

which is the form ASHRAE uses for doorway infiltration. It scales with the
square root of the temperature difference, so a -10 degC morning exchanges
roughly twice the air of a +10 degC one, at every single stop. A busy urban
route with 4 doors and a stop every 300 m spends a large fraction of its day
with the cabin open to the street.

**Passengers** Sensible metabolic heat, about 70-90 W per seated adult
(ASHRAE Fundamentals, Table 1, Chapter 18). In winter this is free heating and
it is not small: 60 passengers is roughly 4.5 kW.

**Solar** ``Q = A_glazing * SHGC * I_solar``, the summer counterpart.

What is deliberately *not* modelled yet: latent load. Dehumidification and the
reheat that follows it can add 20-40 % to the cooling load in a humid coastal
summer, and a bus full of wet passengers in the rain is a genuinely hard case.
Add a humidity state before you use this for Mediterranean summer sizing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..maps import Curve1D
from ..units import CP_AIR, G, T0_K, air_density, c_to_k


@dataclass
class HVAC:
    name: str = "unnamed_hvac"
    system_type: str = "heat_pump"
    """One of ``heat_pump``, ``resistive``, ``fuel_fired`` or ``hybrid``.

    ``fuel_fired`` represents the diesel auxiliary heater still fitted to many
    cold-climate electric buses. It costs no battery energy beyond the pump and
    blower, but it burns fuel and emits, so it moves the problem rather than
    solving it. ``hybrid`` runs the heat pump down to its cutoff and then
    switches to the backup source named by ``backup_heat_source``.
    """

    backup_heat_source: str = "resistive"

    # --- cabin geometry and construction ----------------------------------
    cabin_volume_m3: float = 62.0
    """Interior air volume. A 12 m low-floor city bus is roughly 62 m^3."""

    glazing_area_m2: float = 26.0
    opaque_area_m2: float = 78.0
    u_glazing_w_per_m2k: float = 5.4
    """Single-glazed bus side window with frame. Double glazing takes this to
    about 2.8 and is one of the highest-leverage winter range measures
    available, at a mass and cost penalty."""

    u_opaque_w_per_m2k: float = 1.3
    thermal_bridge_factor: float = 1.15
    """Multiplier on the computed UA covering frames, pillars, floor joints and
    the roof hatch. Bus bodies are structurally busy and 10-20 % is typical."""

    interior_thermal_mass_j_per_k: float = 350_000.0
    """Seats, panels, floor and structure that exchange heat with cabin air.
    Far larger than the air itself, and it governs how long a pull-down takes
    after a night parked outside."""

    shgc: float = 0.45
    """Solar heat gain coefficient of the glazing including any tint."""

    # --- ventilation and doors --------------------------------------------
    fresh_air_m3_per_h_per_passenger: float = 12.0
    fresh_air_minimum_m3_per_h: float = 300.0
    door_count: int = 3
    door_width_m: float = 1.20
    door_height_m: float = 1.95
    door_discharge_coefficient: float = 0.60
    """C_d for a bus doorway with a step well. 0.6 is the usual value for a
    sharp-edged vertical opening."""

    # --- capacity and efficiency ------------------------------------------
    heating_capacity_w: float = 30_000.0
    cooling_capacity_w: float = 32_000.0

    heat_pump_cop: Curve1D | None = None
    """Heating COP against ambient temperature [degC]."""

    cooling_cop: Curve1D | None = None
    resistive_efficiency: float = 0.98
    fuel_fired_efficiency: float = 0.85
    fuel_fired_parasitic_w: float = 600.0
    heat_pump_cutoff_c: float = -12.0
    """Below this ambient the heat pump cannot maintain capacity and the backup
    source takes over."""

    blower_power_w: float = 1_400.0
    condenser_fan_power_w: float = 900.0
    """Parasitics that run whenever the system is active. Easy to forget and
    worth 1-2 kW continuously, which on a city bus is several percent of range."""

    # --- control ----------------------------------------------------------
    setpoint_heating_c: float = 18.0
    setpoint_cooling_c: float = 24.0
    """Deliberately asymmetric, with a dead band between. Holding 22 degC in
    both directions is a common specification and a large, avoidable energy
    cost."""

    control_time_constant_s: float = 420.0
    passenger_sensible_w: float = 80.0

    source: str = "unspecified"
    validated: bool = False
    notes: str = ""

    # ----------------------------------------------------------------------
    def __post_init__(self) -> None:
        if self.heat_pump_cop is None:
            self.heat_pump_cop = Curve1D.from_pairs(
                [[-20.0, 1.10], [-15.0, 1.35], [-10.0, 1.70], [-7.0, 1.95],
                 [0.0, 2.45], [7.0, 3.10], [10.0, 3.40], [20.0, 4.20]],
                name="heat pump COP",
                source="generic R744/R410A bus heat pump characteristic")
        if self.cooling_cop is None:
            self.cooling_cop = Curve1D.from_pairs(
                [[15.0, 4.20], [25.0, 3.40], [30.0, 3.00], [35.0, 2.65],
                 [40.0, 2.30], [45.0, 2.00]],
                name="cooling COP", source="generic bus rooftop A/C characteristic")

    # --- geometry-derived --------------------------------------------------
    @property
    def ua_w_per_k(self) -> float:
        """Envelope heat transfer coefficient [W/K]."""
        ua = (self.glazing_area_m2 * self.u_glazing_w_per_m2k
              + self.opaque_area_m2 * self.u_opaque_w_per_m2k)
        return ua * self.thermal_bridge_factor

    def capacitance_j_per_k(self, ambient_c: float = 20.0) -> float:
        rho = air_density(ambient_c)
        return self.cabin_volume_m3 * rho * CP_AIR + self.interior_thermal_mass_j_per_k

    # --- individual load paths --------------------------------------------
    def q_envelope(self, cabin_c: float, ambient_c: float) -> float:
        return self.ua_w_per_k * (ambient_c - cabin_c)

    def q_ventilation(self, cabin_c: float, ambient_c: float,
                      n_passengers: float) -> float:
        v_dot_m3h = max(self.fresh_air_m3_per_h_per_passenger * n_passengers,
                        self.fresh_air_minimum_m3_per_h)
        m_dot = v_dot_m3h / 3600.0 * air_density(ambient_c)
        return m_dot * CP_AIR * (ambient_c - cabin_c)

    def q_doors(self, cabin_c: float, ambient_c: float,
                doors_open: bool, doors_open_fraction: float = 1.0) -> float:
        """Gravity-driven air exchange while the doors are open [W].

        Brown-Solvason bidirectional exchange through a vertical opening::

            Q_vol = (1/3) * C_d * W * H * sqrt(g * H * |dT| / T_mean)

        with ``T_mean`` absolute. Returns zero when the doors are shut, and
        scales linearly with how many are open.
        """
        if not doors_open or doors_open_fraction <= 0.0:
            return 0.0
        dT = ambient_c - cabin_c
        if abs(dT) < 0.1:
            return 0.0
        t_mean_k = c_to_k(0.5 * (cabin_c + ambient_c))
        h = self.door_height_m
        v_dot = ((1.0 / 3.0) * self.door_discharge_coefficient
                 * self.door_width_m * h
                 * math.sqrt(G * h * abs(dT) / t_mean_k))
        v_dot *= self.door_count * doors_open_fraction
        m_dot = v_dot * air_density(ambient_c)
        return m_dot * CP_AIR * dT

    def q_passengers(self, n_passengers: float) -> float:
        return n_passengers * self.passenger_sensible_w

    def q_solar(self, solar_irradiance_w_per_m2: float,
                shading_factor: float = 1.0) -> float:
        return (self.glazing_area_m2 * self.shgc
                * solar_irradiance_w_per_m2 * shading_factor)

    # --- the step ----------------------------------------------------------
    def step(self, cabin_c: float, ambient_c: float, dt_s: float, *,
             n_passengers: float = 0.0, doors_open: bool = False,
             doors_open_fraction: float = 1.0,
             solar_irradiance_w_per_m2: float = 0.0,
             shading_factor: float = 1.0,
             recirculation: float = 0.0):
        """Advance the cabin one step and report the electrical cost.

        ``recirculation`` between 0 and 1 reduces the fresh-air ventilation load
        by that fraction. It is the cheapest winter energy saving available and
        the one most constrained by CO2 and air-quality limits, so it is exposed
        as a control input rather than a fixed parameter.

        Returns ``(new_cabin_c, electrical_power_w, detail_dict)``. Fuel-fired
        heat is reported separately in litres-equivalent rather than folded into
        electrical power, because hiding it makes an electric bus look better
        than it is.
        """
        q_env = self.q_envelope(cabin_c, ambient_c)
        q_vent = self.q_ventilation(cabin_c, ambient_c, n_passengers) * (1.0 - recirculation)
        q_door = self.q_doors(cabin_c, ambient_c, doors_open, doors_open_fraction)
        q_pax = self.q_passengers(n_passengers)
        q_sol = self.q_solar(solar_irradiance_w_per_m2, shading_factor)
        q_other = q_env + q_vent + q_door + q_pax + q_sol

        # Feed-forward plus proportional feedback: cancel the known loads and
        # close the remaining error over the control time constant.
        c_cab = self.capacitance_j_per_k(ambient_c)
        if cabin_c < self.setpoint_heating_c:
            target = self.setpoint_heating_c
        elif cabin_c > self.setpoint_cooling_c:
            target = self.setpoint_cooling_c
        else:
            target = None

        if target is None:
            q_hvac = 0.0
        else:
            q_hvac = -q_other + c_cab * (target - cabin_c) / self.control_time_constant_s
            q_hvac = float(np.clip(q_hvac, -self.cooling_capacity_w,
                                   self.heating_capacity_w))
            # Do not let the feed-forward term fight the thermostat: if we are
            # heating, never actively cool, and vice versa.
            if target == self.setpoint_heating_c:
                q_hvac = max(q_hvac, 0.0)
            else:
                q_hvac = min(q_hvac, 0.0)

        p_elec, fuel_w, mode = self._electrical_demand(q_hvac, ambient_c)

        dT = (q_other + q_hvac) * dt_s / c_cab
        cabin_new = cabin_c + dT

        detail = {
            "q_envelope_w": q_env,
            "q_ventilation_w": q_vent,
            "q_doors_w": q_door,
            "q_passengers_w": q_pax,
            "q_solar_w": q_sol,
            "q_hvac_w": q_hvac,
            "hvac_mode": mode,
            "fuel_heat_w": fuel_w,
            "saturated": target is not None and abs(q_hvac) >= min(
                self.heating_capacity_w, self.cooling_capacity_w) * 0.999,
        }
        return cabin_new, p_elec, detail

    def _electrical_demand(self, q_hvac_w: float, ambient_c: float):
        """Convert a thermal duty into electrical power drawn from the pack."""
        if abs(q_hvac_w) < 1.0:
            return 0.0, 0.0, "off"

        parasitic = self.blower_power_w
        if q_hvac_w < 0.0:  # cooling
            cop = max(float(self.cooling_cop(ambient_c)), 0.5)
            return abs(q_hvac_w) / cop + parasitic + self.condenser_fan_power_w, 0.0, "cooling"

        # heating
        use_heat_pump = (self.system_type == "heat_pump"
                         or (self.system_type == "hybrid"
                             and ambient_c > self.heat_pump_cutoff_c))
        if use_heat_pump and ambient_c > self.heat_pump_cutoff_c:
            cop = max(float(self.heat_pump_cop(ambient_c)), 0.5)
            return q_hvac_w / cop + parasitic + self.condenser_fan_power_w, 0.0, "heat_pump"

        source = self.backup_heat_source if self.system_type == "hybrid" else self.system_type
        if source == "fuel_fired" or self.system_type == "fuel_fired":
            fuel_w = q_hvac_w / self.fuel_fired_efficiency
            return parasitic + self.fuel_fired_parasitic_w, fuel_w, "fuel_fired"

        return q_hvac_w / self.resistive_efficiency + parasitic, 0.0, "resistive"

    def describe(self) -> str:
        flag = "VALIDATED" if self.validated else "UNVALIDATED"
        return (f"{self.name}: {self.system_type}, UA={self.ua_w_per_k:.0f} W/K, "
                f"{self.heating_capacity_w/1e3:.0f} kW heat / "
                f"{self.cooling_capacity_w/1e3:.0f} kW cool [{flag}]")
