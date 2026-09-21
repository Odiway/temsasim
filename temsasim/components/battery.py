"""Traction battery: equivalent-circuit electrical model plus lumped thermal.

Electrically this is an ``Rint`` model -- an open-circuit voltage source in
series with an internal resistance, both functions of state of charge and
temperature::

    V_terminal = V_oc(SOC, T) - I * R(SOC, T)
    P_terminal = V_terminal * I

Given a demanded terminal power the current follows from solving the quadratic

    R*I**2 - V_oc*I + P = 0   =>   I = (V_oc - sqrt(V_oc**2 - 4*R*P)) / (2*R)

taking the lower-current root. A negative discriminant means the demand exceeds
what the pack can physically deliver, whose maximum is ``V_oc**2 / (4*R)``.

An Rint model is enough for energy and range work over a drive cycle. It will
not reproduce the voltage relaxation you see in a pulse test, so if you move on
to fast-charge or pack-protection studies, extend this to a 1-RC Thevenin model
-- the interface here is written so that is a drop-in change.

The parts that matter most for a bus, and that ADVISOR does not have at all:

* **Resistance rises steeply as the pack gets cold.** Modelled with an
  Arrhenius factor. At -10 degC a cell can easily show two to three times its
  25 degC resistance.
* **Charge acceptance collapses when cold.** Lithium plating risk means the BMS
  throttles regenerative braking hard below about 5 degC and blocks it near
  0 degC. On a winter city route this removes most of the regen the summer
  figures were built on, and it is a large part of why winter range disappoints.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..maps import Curve1D
from ..units import R_GAS, T0_K, c_to_k


@dataclass
class BatteryPack:
    name: str = "unnamed_pack"
    chemistry: str = "NMC"

    # --- topology ---------------------------------------------------------
    cells_series: int = 180
    cells_parallel: int = 3
    cell_capacity_ah: float = 60.0
    cell_nominal_voltage_v: float = 3.65

    # --- electrical characteristics ---------------------------------------
    ocv_curve: Curve1D | None = None
    """Cell open-circuit voltage [V] against SOC [0-1]. A generic curve for the
    declared chemistry is used if none is given."""

    cell_resistance_ohm: float = 0.0015
    """Cell internal resistance at the reference temperature and mid SOC."""

    resistance_soc_factor: Curve1D | None = None
    """Multiplier on resistance against SOC. Resistance rises at both ends of
    the SOC window, sharply so below about 15 %."""

    resistance_activation_j_per_mol: float = 20_000.0
    """Arrhenius activation energy for the resistance temperature dependence.
    R(T) = R_ref * exp(Ea/R_gas * (1/T - 1/T_ref)). 20 kJ/mol gives roughly a
    2.5x rise from 25 degC to -10 degC, which is typical for a large-format
    automotive cell."""

    resistance_reference_temp_c: float = 25.0

    coulombic_efficiency: float = 0.995
    """Charge accepted per charge delivered. Applied on the charging path only."""

    # --- operating limits -------------------------------------------------
    cell_voltage_min_v: float = 2.80
    cell_voltage_max_v: float = 4.15
    max_discharge_c_rate: float = 4.0
    max_charge_c_rate: float = 2.0
    soc_min: float = 0.05
    soc_max: float = 1.00
    """Usable SOC window. Buses are normally operated inside a narrower band
    than the pack allows, to protect warranty life."""

    # --- cold charge-acceptance derating ----------------------------------
    charge_accept_derate: Curve1D | None = None
    """Fraction of nominal charge current accepted, against cell temperature
    [degC]. This is the regen killer in winter."""

    # --- thermal ----------------------------------------------------------
    pack_mass_kg: float = 1800.0
    specific_heat_j_per_kgk: float = 1000.0
    """Effective pack specific heat including cells, busbars, housing and
    coolant. Cells alone are around 1000-1100 J/(kg K); the module and housing
    drag the effective value down a little but add mass."""

    thermal_conductance_to_ambient_w_per_k: float = 45.0
    """Passive UA of the pack enclosure to ambient air."""

    cooling_capacity_w: float = 12_000.0
    heating_capacity_w: float = 6_000.0
    cooling_cop: float = 2.2
    heating_cop: float = 1.0
    """Pack heating is usually resistive or a coolant heater, hence COP 1."""

    thermal_target_c: float = 25.0
    cooling_on_above_c: float = 32.0
    heating_on_below_c: float = 10.0

    source: str = "unspecified"
    validated: bool = False

    # ----------------------------------------------------------------------
    def __post_init__(self) -> None:
        if self.ocv_curve is None:
            self.ocv_curve = _generic_ocv(self.chemistry)
        if self.resistance_soc_factor is None:
            self.resistance_soc_factor = Curve1D.from_pairs(
                [[0.00, 2.20], [0.05, 1.60], [0.10, 1.30], [0.20, 1.08],
                 [0.50, 1.00], [0.80, 1.02], [0.95, 1.12], [1.00, 1.30]],
                name="R(SOC) shape", source="generic large-format cell shape")
        if self.charge_accept_derate is None:
            # Typical BMS regen limit line. Below 0 degC essentially no regen.
            self.charge_accept_derate = Curve1D.from_pairs(
                [[-20.0, 0.00], [-10.0, 0.05], [0.0, 0.15], [5.0, 0.40],
                 [10.0, 0.70], [15.0, 0.90], [25.0, 1.00], [45.0, 1.00],
                 [55.0, 0.50]],
                name="charge accept vs T",
                source="generic BMS plating-protection limit")

    # --- derived pack-level quantities ------------------------------------
    @property
    def capacity_ah(self) -> float:
        return self.cell_capacity_ah * self.cells_parallel

    @property
    def nominal_voltage_v(self) -> float:
        return self.cell_nominal_voltage_v * self.cells_series

    @property
    def nominal_energy_kwh(self) -> float:
        return self.capacity_ah * self.nominal_voltage_v / 1000.0

    @property
    def usable_energy_kwh(self) -> float:
        return self.nominal_energy_kwh * (self.soc_max - self.soc_min)

    @property
    def voltage_min_v(self) -> float:
        return self.cell_voltage_min_v * self.cells_series

    @property
    def voltage_max_v(self) -> float:
        return self.cell_voltage_max_v * self.cells_series

    # --- state-dependent properties ---------------------------------------
    def ocv(self, soc: float) -> float:
        """Pack open-circuit voltage [V]."""
        return float(self.ocv_curve(np.clip(soc, 0.0, 1.0))) * self.cells_series

    def resistance(self, soc: float, temp_c: float, soh: float = 1.0) -> float:
        """Pack internal resistance [ohm].

        ``soh`` is the capacity-based state of health; resistance grows as the
        cell ages roughly twice as fast as capacity falls, hence the exponent.
        """
        r_cell = self.cell_resistance_ohm * float(self.resistance_soc_factor(soc))
        t_k = c_to_k(temp_c)
        t_ref_k = c_to_k(self.resistance_reference_temp_c)
        arr = math.exp(self.resistance_activation_j_per_mol / R_GAS
                       * (1.0 / t_k - 1.0 / t_ref_k))
        r_cell *= arr
        r_cell *= (1.0 / max(soh, 0.5)) ** 2
        return r_cell * self.cells_series / self.cells_parallel

    def power_limits(self, soc: float, temp_c: float, soh: float = 1.0):
        """Available discharge and charge power at the terminals [W].

        Returns ``(p_discharge_max, p_charge_max)`` with the charge limit
        negative. Three constraints are applied and the tightest wins: the
        cell C-rate rating, the cell voltage window, and -- on the charge side
        only -- the temperature-dependent charge-acceptance derating.
        """
        v_oc = self.ocv(soc)
        r = self.resistance(soc, temp_c, soh)
        cap = self.capacity_ah * soh

        i_dis_rate = self.max_discharge_c_rate * cap
        i_chg_rate = self.max_charge_c_rate * cap * float(
            self.charge_accept_derate(temp_c))

        i_dis_volt = max((v_oc - self.voltage_min_v) / r, 0.0)
        i_chg_volt = max((self.voltage_max_v - v_oc) / r, 0.0)

        i_dis = min(i_dis_rate, i_dis_volt)
        i_chg = min(i_chg_rate, i_chg_volt)

        if soc <= self.soc_min:
            i_dis = 0.0
        if soc >= self.soc_max:
            i_chg = 0.0

        p_dis = (v_oc - i_dis * r) * i_dis
        p_chg = -(v_oc + i_chg * r) * i_chg
        return p_dis, p_chg

    # --- the step ---------------------------------------------------------
    def solve(self, power_demand_w: float, soc: float, temp_c: float,
              dt_s: float, soh: float = 1.0):
        """Advance the pack one step.

        ``power_demand_w`` is terminal power, positive discharging. Returns a
        dict with the achieved power, current, voltage, new SOC, ohmic heat and
        a flag saying whether the demand had to be clipped -- the caller needs
        that flag, because a clipped discharge means the bus could not follow
        the drive cycle and a clipped charge means regen was thrown away in the
        friction brakes.
        """
        p_dis_max, p_chg_max = self.power_limits(soc, temp_c, soh)
        p_req = float(power_demand_w)
        p = float(np.clip(p_req, p_chg_max, p_dis_max))
        limited = abs(p - p_req) > 1e-6

        v_oc = self.ocv(soc)
        r = self.resistance(soc, temp_c, soh)

        disc = v_oc * v_oc - 4.0 * r * p
        if disc < 0.0:
            # Should not happen after clipping, but stay defensive: fall back to
            # the maximum-power point rather than returning a complex current.
            i = v_oc / (2.0 * r)
            p = v_oc * v_oc / (4.0 * r)
            limited = True
        else:
            i = (v_oc - math.sqrt(disc)) / (2.0 * r)

        v_term = v_oc - i * r
        q_ah = self.capacity_ah * soh
        eta_c = self.coulombic_efficiency if i < 0.0 else 1.0
        soc_new = soc - (i * eta_c * dt_s) / (3600.0 * q_ah)
        soc_new = float(np.clip(soc_new, 0.0, 1.0))

        heat_w = i * i * r

        return {
            "power_w": p,
            "power_requested_w": p_req,
            "current_a": i,
            "voltage_v": v_term,
            "ocv_v": v_oc,
            "resistance_ohm": r,
            "soc": soc_new,
            "heat_w": heat_w,
            "limited": limited,
            "c_rate": abs(i) / max(q_ah, 1e-6),
        }

    def thermal_step(self, temp_c: float, heat_w: float, ambient_c: float,
                     dt_s: float):
        """Lumped pack temperature update.

        ``m*cp*dT/dt = Q_ohmic - Q_cooling + Q_heating - UA*(T - T_amb)``

        Returns the new temperature and the electrical power the thermal
        management system drew to achieve it -- that power comes out of the same
        pack and is a real part of the energy balance.
        """
        q_amb = self.thermal_conductance_to_ambient_w_per_k * (ambient_c - temp_c)

        q_cool = 0.0
        q_heat = 0.0
        p_thermal_elec = 0.0
        if temp_c > self.cooling_on_above_c:
            span = max(temp_c - self.thermal_target_c, 0.0)
            q_cool = min(self.cooling_capacity_w, span * self.cooling_capacity_w / 10.0)
            p_thermal_elec += q_cool / max(self.cooling_cop, 0.1)
        elif temp_c < self.heating_on_below_c:
            span = max(self.thermal_target_c - temp_c, 0.0)
            q_heat = min(self.heating_capacity_w, span * self.heating_capacity_w / 15.0)
            p_thermal_elec += q_heat / max(self.heating_cop, 0.1)

        c_th = self.pack_mass_kg * self.specific_heat_j_per_kgk
        dT = (heat_w + q_amb - q_cool + q_heat) * dt_s / c_th
        return temp_c + dT, p_thermal_elec

    def describe(self) -> str:
        flag = "VALIDATED" if self.validated else "UNVALIDATED"
        return (f"{self.name}: {self.chemistry} {self.cells_series}s{self.cells_parallel}p, "
                f"{self.nominal_energy_kwh:.0f} kWh nominal / "
                f"{self.usable_energy_kwh:.0f} kWh usable, "
                f"{self.nominal_voltage_v:.0f} V [{flag}]")


def _generic_ocv(chemistry: str) -> Curve1D:
    """Cell OCV against SOC for the common traction chemistries.

    These are generic literature shapes, good enough to get the energy right
    and the voltage roughly right. They are not a substitute for a measured
    pseudo-OCV curve from your cell supplier, and the LFP plateau in particular
    is very sensitive to exactly which cell you have.
    """
    chem = chemistry.upper()
    if chem in ("LFP", "LIFEPO4"):
        pairs = [[0.00, 2.90], [0.05, 3.12], [0.10, 3.20], [0.20, 3.25],
                 [0.40, 3.28], [0.60, 3.30], [0.80, 3.32], [0.90, 3.35],
                 [0.95, 3.40], [1.00, 3.60]]
        src = "generic LFP pseudo-OCV shape"
    elif chem in ("LTO",):
        pairs = [[0.00, 1.80], [0.10, 2.10], [0.30, 2.25], [0.50, 2.32],
                 [0.70, 2.40], [0.90, 2.55], [1.00, 2.80]]
        src = "generic LTO pseudo-OCV shape"
    else:  # NMC / NCA
        pairs = [[0.00, 3.00], [0.05, 3.35], [0.10, 3.50], [0.20, 3.62],
                 [0.30, 3.68], [0.40, 3.73], [0.50, 3.79], [0.60, 3.86],
                 [0.70, 3.94], [0.80, 4.02], [0.90, 4.10], [1.00, 4.18]]
        src = "generic NMC pseudo-OCV shape"
    return Curve1D.from_pairs(pairs, name=f"{chem} OCV", source=src)
