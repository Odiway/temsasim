"""Traction motor and inverter.

The pair is modelled as one unit because that is how it is measured and how it
is bought: an efficiency map from the DC terminals to the output shaft. Keeping
them separate invites double-counting the inverter losses.

Two envelopes are tracked:

* **peak**       -- what the machine can do for a limited time (typically 30 s
  or 60 s), used for launch, gradeability and overtake checks.
* **continuous** -- what it can do indefinitely at rated cooling. A city bus on
  a hilly route can sit above continuous for long enough to matter, so the
  simulator records time spent above it rather than silently allowing peak
  forever.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..maps import Map2D, synth_motor_efficiency
from ..units import RPM


@dataclass
class Motor:
    name: str = "unnamed_motor"
    technology: str = "PMSM"

    peak_power_w: float = 250_000.0
    continuous_power_w: float = 160_000.0
    peak_torque_nm: float = 2500.0
    continuous_torque_nm: float = 1200.0
    max_speed_rads: float = 314.0

    efficiency_map: Map2D | None = None
    """Efficiency from DC terminals to shaft, as a function of
    (speed [rad/s], |torque| [Nm]). Built synthetically if absent."""

    peak_efficiency: float = 0.95
    peak_efficiency_at_rpm: float = 1200.0
    peak_efficiency_at_torque_nm: float = 900.0
    """Used only to calibrate the synthetic map. Ignored once a measured map
    is supplied."""

    regen_efficiency_penalty: float = 0.0
    """Some machines are measurably worse in generating than motoring at the
    same operating point. Subtracted from the map value when regenerating.
    Leave at 0 unless you have data saying otherwise."""

    max_regen_power_w: float | None = None
    """Generating power limit, if it differs from the motoring peak."""

    field_weakening_exponent: float = 1.0
    """Torque above base speed falls as P_peak / w by default (exponent 1.0).
    Above about twice base speed real machines fall off faster; raise this
    toward 1.2-1.4 to represent that."""

    source: str = "unspecified"
    validated: bool = False
    notes: str = ""

    # ----------------------------------------------------------------------
    def __post_init__(self) -> None:
        if self.efficiency_map is None:
            self.efficiency_map = self._build_synthetic_map()

    def _build_synthetic_map(self) -> Map2D:
        speeds = np.linspace(1.0, max(self.max_speed_rads, 10.0), 80)
        torques = np.linspace(1.0, self.peak_torque_nm, 80)
        return synth_motor_efficiency(
            speeds, torques,
            peak_efficiency=self.peak_efficiency,
            peak_at_speed_rads=max(self.peak_efficiency_at_rpm * RPM, 1.0),
            peak_at_torque_nm=max(self.peak_efficiency_at_torque_nm, 1.0),
        )

    # ----------------------------------------------------------------------
    @property
    def base_speed_rads(self) -> float:
        """Corner point: below it the machine is torque-limited, above it
        power-limited."""
        return self.peak_power_w / self.peak_torque_nm

    def max_torque(self, speed_rads: float, *, continuous: bool = False) -> float:
        """Torque envelope [Nm] at a given shaft speed."""
        w = abs(float(speed_rads))
        t_flat = self.continuous_torque_nm if continuous else self.peak_torque_nm
        p_lim = self.continuous_power_w if continuous else self.peak_power_w
        if w > self.max_speed_rads:
            return 0.0
        if w < 1e-3:
            return t_flat
        w_base = p_lim / t_flat
        if w <= w_base:
            return t_flat
        return t_flat * (w_base / w) ** self.field_weakening_exponent

    def max_regen_torque(self, speed_rads: float) -> float:
        """Generating torque limit [Nm], positive magnitude."""
        t = self.max_torque(speed_rads)
        if self.max_regen_power_w is not None and abs(speed_rads) > 1e-3:
            t = min(t, self.max_regen_power_w / abs(speed_rads))
        return t

    # ----------------------------------------------------------------------
    def electrical_power(self, speed_rads: float, torque_nm: float):
        """DC-side power [W] for a shaft operating point.

        Sign convention: positive torque and positive speed means the machine
        drives the bus and draws from the pack. Negative torque at positive
        speed means it generates and returns power to the pack, so the returned
        power is negative.
        """
        w = float(speed_rads)
        t = float(torque_nm)
        p_mech = w * t

        eta = float(self.efficiency_map(abs(w), abs(t)))
        eta = float(np.clip(eta, 0.30, 0.995))

        if p_mech >= 0.0:
            p_elec = p_mech / eta if p_mech > 0 else 0.0
        else:
            eta_gen = max(eta - self.regen_efficiency_penalty, 0.30)
            p_elec = p_mech * eta_gen

        return p_elec, eta, p_mech

    def describe(self) -> str:
        flag = "VALIDATED" if self.validated else "UNVALIDATED"
        src = self.efficiency_map.source if self.efficiency_map else "none"
        return (f"{self.name}: {self.technology}, {self.peak_power_w/1e3:.0f} kW pk / "
                f"{self.continuous_power_w/1e3:.0f} kW cont, {self.peak_torque_nm:.0f} Nm "
                f"[{flag}] map={src}")
