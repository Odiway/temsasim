"""Gearbox, final drive and the mechanical path from motor to wheel.

Covers the three layouts a TEMSA e-bus realistically uses:

* ``central_motor``  -- motor, optional 1- or 2-speed box, propshaft, hypoid
  final drive into a conventional beam axle.
* ``direct_drive``   -- motor with a single reduction stage straight to the axle.
* ``portal_axle``    -- in-wheel or near-wheel motors in a low-floor portal axle
  (ZF AxTrax / AVE-style), where the "final drive" is the portal reduction.

Losses are modelled as a constant mechanical efficiency per stage by default,
which is the honest level of detail until someone measures the box. A speed-
and load-dependent loss map can be supplied instead; churning and bearing drag
at low load is where a constant efficiency is most wrong, and on a city bus the
driveline spends most of its life at low load.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..maps import Map2D


@dataclass
class Driveline:
    name: str = "unnamed_driveline"
    layout: str = "central_motor"

    gear_ratios: list = field(default_factory=lambda: [1.0])
    """Gearbox ratios, input speed / output speed. A single entry means a
    fixed-ratio box; two or more enable the shift logic below."""

    final_drive_ratio: float = 6.20
    """Axle reduction. Together with gear ratio and tyre radius this sets the
    motor speed at a given road speed, and it is the single cheapest lever for
    trading gradeability against top speed."""

    gearbox_efficiency: float = 0.97
    final_drive_efficiency: float = 0.96

    efficiency_map: Map2D | None = None
    """Optional efficiency as a function of (output speed [rad/s], output torque
    [Nm]). Overrides the constant efficiencies when present."""

    motor_inertia_kgm2: float = 0.35
    """Rotor plus input-shaft inertia, referred to the motor shaft. It is
    multiplied by the square of the total ratio when referred to the wheel, so
    on a high-ratio portal axle it is not a small number."""

    shift_up_speed_mps: float = 13.9
    shift_down_speed_mps: float = 11.1
    """Simple speed-based shift thresholds for a 2-speed box, with hysteresis
    between them so the model does not chatter around the shift point."""

    spin_loss_nm: float = 0.0
    """Constant drag torque at the wheel [Nm], whether or not power flows.
    Brake drag and seal friction live here."""

    source: str = "unspecified"
    validated: bool = False
    notes: str = ""

    # ----------------------------------------------------------------------
    def gear_for_speed(self, speed_mps: float, current_gear: int = 0) -> int:
        """Pick a gear index with hysteresis. Returns 0 for a fixed-ratio box."""
        if len(self.gear_ratios) < 2:
            return 0
        g = current_gear
        if speed_mps > self.shift_up_speed_mps and g < len(self.gear_ratios) - 1:
            g += 1
        elif speed_mps < self.shift_down_speed_mps and g > 0:
            g -= 1
        return g

    def total_ratio(self, gear: int = 0) -> float:
        return self.gear_ratios[gear] * self.final_drive_ratio

    def referred_inertia_kgm2(self, gear: int = 0) -> float:
        """Motor-side inertia referred to the wheel axis."""
        return self.motor_inertia_kgm2 * self.total_ratio(gear) ** 2

    def efficiency(self, wheel_speed_rads: float, wheel_torque_nm: float) -> float:
        if self.efficiency_map is not None:
            return float(np.clip(
                self.efficiency_map(wheel_speed_rads, abs(wheel_torque_nm)), 0.5, 0.999))
        return self.gearbox_efficiency * self.final_drive_efficiency

    # ----------------------------------------------------------------------
    def wheel_to_motor(self, wheel_speed_rads: float, wheel_torque_nm: float,
                       gear: int = 0):
        """Backward step: wheel demand in, motor demand out.

        The efficiency applies in opposite senses depending on power direction.
        Driving, the motor must supply more torque than the wheel needs because
        the driveline eats some. Regenerating, the motor recovers less than the
        wheel gives up, for the same reason. Getting this backwards is the
        classic sign error in a backward-facing model: it shows up as a bus
        that recovers more energy downhill than physics allows.
        """
        ratio = self.total_ratio(gear)
        eta = self.efficiency(wheel_speed_rads, wheel_torque_nm)

        t_wheel = wheel_torque_nm + np.sign(wheel_speed_rads) * self.spin_loss_nm
        motor_speed = wheel_speed_rads * ratio
        if t_wheel >= 0.0:
            motor_torque = t_wheel / (ratio * eta)
        else:
            motor_torque = t_wheel * eta / ratio
        return motor_speed, motor_torque, eta

    def describe(self) -> str:
        gears = "/".join(f"{g:.2f}" for g in self.gear_ratios)
        flag = "VALIDATED" if self.validated else "UNVALIDATED"
        return (f"{self.name}: {self.layout}, gears {gears} x "
                f"{self.final_drive_ratio:.2f} final [{flag}]")
