"""Combustion engine: fuel map, full-load and drag curves.

The engine is described the way a VECTO engine certificate describes it,
because that is the form in which TEMSA's homologation team already holds the
data for every diesel coach:

* a **fuel consumption map** -- fuel mass flow [g/h] over (engine speed, torque),
  measured on the engine dynamometer. Negative torques (overrun) are in the map
  with zero fuel, which is how fuel cut-off is represented.
* a **full-load curve** -- the maximum torque the engine can produce at each
  speed, and the **drag curve** -- the (negative) torque it absorbs when it is
  being turned by the vehicle with the fuel cut.

Everything the solver needs is a lookup on those three tables. The small set
of scalar fields (idle speed, rated speed, rated power, displacement) comes
from the same certificate and is kept for the shift strategy, for inertia and
for the report.

Fuel properties follow VECTO's declaration table (``FuelTypes.csv``): diesel
at 836 kg/m³, 42.7 MJ/kg and 3.13 kg CO₂ per kg of fuel. They are fields, not
constants, so a biodiesel blend is one YAML edit.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..maps import Curve1D, Map2D
from ..units import RPM


@dataclass
class Engine:
    name: str = "unnamed_engine"
    fuel_type: str = "diesel"
    manufacturer: str = ""
    model: str = ""
    certification_number: str = ""

    # --- rating ------------------------------------------------------------
    displacement_cm3: float = 10_800.0
    idle_speed_rads: float = 550.0 * RPM
    rated_speed_rads: float = 1600.0 * RPM
    rated_power_w: float = 330_000.0
    max_torque_nm: float = 2358.0

    # --- maps ----------------------------------------------------------------
    fuel_map: Map2D | None = None
    """Fuel mass flow [g/h] over (speed [rad/s], torque [Nm]). From the engine
    certificate. Built synthetically from a Willans line if absent, and then
    the engine is marked unvalidated whatever the flag says."""

    full_load_curve: Curve1D | None = None
    """Maximum torque [Nm] against speed [rad/s]."""

    drag_curve: Curve1D | None = None
    """Motoring torque [Nm, negative] against speed [rad/s]: what the engine
    absorbs on the overrun with the fuel cut."""

    inertia_kgm2: float | None = None
    """Engine plus clutch inertia about the crankshaft. VECTO's generic value
    for engines above 5 litres is 1.3 (clutch) + 0.41 + 270 × displacement
    [m³]; that formula is used when the field is empty."""

    fan_power_w: float = 600.0
    """Average mechanical draw of the engine cooling fan. A belt-driven fan on
    an on/off clutch spends most of its time disengaged; VECTO's bus
    declaration tables put the average around 0.5-0.8 kW."""

    # --- certificate correction factors ------------------------------------
    whtc_urban: float = 1.0
    whtc_rural: float = 1.0
    whtc_motorway: float = 1.0
    cold_hot_factor: float = 1.0
    regper_factor: float = 1.0
    """Correction factors from the certificate. VECTO multiplies the fuel map
    by a mission-weighted WHTC factor × cold/hot × regeneration factor in
    declaration mode. They are stored so the comparison can reproduce that;
    the solver applies only ``correction_factor``."""

    correction_factor: float = 1.0
    """Multiplier on the fuel map actually used. 1.0 reproduces the raw map
    (VECTO engineering mode); set it to the WHTC-weighted product to mimic a
    declaration run on a given mission."""

    # --- fuel properties ----------------------------------------------------
    fuel_density_kg_per_m3: float = 836.0
    fuel_lhv_j_per_kg: float = 42.7e6
    co2_kg_per_kg_fuel: float = 3.13

    source: str = "unspecified"
    validated: bool = False
    notes: str = ""

    # ----------------------------------------------------------------------
    def __post_init__(self) -> None:
        self.synthetic = False
        if self.full_load_curve is None:
            self.full_load_curve = self._synthetic_full_load()
            self.synthetic = True
        if self.drag_curve is None:
            self.drag_curve = self._synthetic_drag()
            self.synthetic = True
        if self.fuel_map is None:
            self.fuel_map = self._synthetic_fuel_map()
            self.synthetic = True
        if self.synthetic:
            self.validated = False
        if self.inertia_kgm2 is None:
            self.inertia_kgm2 = 1.3 + 0.41 + 270.0 * self.displacement_cm3 * 1e-6

    def _synthetic_full_load(self) -> Curve1D:
        """Flat torque plateau up to the rated-power corner, then constant power."""
        w_corner = self.rated_power_w / self.max_torque_nm
        w = np.linspace(self.idle_speed_rads, self.rated_speed_rads * 1.25, 40)
        t = np.where(w <= w_corner, self.max_torque_nm, self.rated_power_w / w)
        # torque rise from idle to the plateau
        rise = np.clip((w - self.idle_speed_rads) / max(w_corner * 0.6 - self.idle_speed_rads, 1.0), 0.45, 1.0)
        t = t * rise
        # fall-off past rated speed
        t = np.where(w > self.rated_speed_rads, t * np.clip(1 - 2.0 * (w / self.rated_speed_rads - 1), 0.2, 1), t)
        return Curve1D(w, t, name="full load (synthetic)", source="SYNTHETIC plateau + constant power")

    def _synthetic_drag(self) -> Curve1D:
        w = np.linspace(self.idle_speed_rads, self.rated_speed_rads * 1.25, 10)
        # about 4-7 % of peak torque, rising with speed: VECTO's generic drag shape
        t = -(0.04 + 0.03 * (w - w[0]) / (w[-1] - w[0])) * self.max_torque_nm
        return Curve1D(w, t, name="drag (synthetic)", source="SYNTHETIC 4-7 % of peak torque")

    def _synthetic_fuel_map(self) -> Map2D:
        """Willans line: fuel power = (shaft power + friction power) / 0.42."""
        w = np.linspace(self.idle_speed_rads, self.rated_speed_rads * 1.25, 30)
        t = np.linspace(-0.12 * self.max_torque_nm, 1.05 * self.max_torque_nm, 40)
        W, T = np.meshgrid(w, t)
        friction = -self.drag_curve(W)
        p_fuel = np.where(T >= 0, (W * T + W * friction) / 0.42, 0.0)
        gph = p_fuel / self.fuel_lhv_j_per_kg * 1000.0 * 3600.0
        return Map2D(w, t, gph, name="fuel map (synthetic)",
                     source="SYNTHETIC Willans line, 42 % indicated efficiency",
                     x_label="speed_rad_s", y_label="torque_Nm")

    # ----------------------------------------------------------------------
    def max_torque(self, speed_rads: float) -> float:
        return float(self.full_load_curve(abs(float(speed_rads))))

    def drag_torque(self, speed_rads: float) -> float:
        """Negative: the torque the engine absorbs on the overrun."""
        return -abs(float(self.drag_curve(abs(float(speed_rads)))))

    def fuel_rate_g_per_s(self, speed_rads: float, torque_nm: float) -> float:
        """Fuel mass flow [g/s] at an operating point, map × correction."""
        w = max(float(speed_rads), 0.0)
        if w < 1e-3:
            return 0.0
        gph = float(self.fuel_map(w, float(torque_nm)))
        return max(gph, 0.0) / 3600.0 * self.correction_factor

    @property
    def idle_fuel_g_per_s(self) -> float:
        return self.fuel_rate_g_per_s(self.idle_speed_rads, 0.0)

    @property
    def idle_fuel_l_per_h(self) -> float:
        return self.idle_fuel_g_per_s * 3600.0 / self.fuel_density_kg_per_m3

    def litres(self, grams: float) -> float:
        return grams / self.fuel_density_kg_per_m3

    def co2_g(self, fuel_g: float) -> float:
        return fuel_g * self.co2_kg_per_kg_fuel

    def fuel_energy_j(self, fuel_g: float) -> float:
        return fuel_g / 1000.0 * self.fuel_lhv_j_per_kg

    def describe(self) -> str:
        flag = "VALIDATED" if self.validated else "UNVALIDATED"
        src = self.fuel_map.source if self.fuel_map is not None else "none"
        who = f"{self.manufacturer} {self.model}".strip() or self.name
        return (f"{who}: {self.fuel_type}, {self.displacement_cm3 / 1000:.1f} l, "
                f"{self.rated_power_w / 1e3:.0f} kW @ {self.rated_speed_rads / RPM:.0f} rpm, "
                f"{self.max_torque_nm:.0f} Nm, idle {self.idle_fuel_l_per_h:.2f} l/h [{flag}] map={src}")
