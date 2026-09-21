"""The assembled vehicle: components plus the control strategy that links them.

Keeping the control strategy on the vehicle rather than inside the solver is
deliberate. Regenerative braking limits, the SOC window and the thermal
pre-conditioning policy are engineering decisions that differ between variants
of the same bus, and they belong in the data file alongside the hardware -- not
in the code that integrates the equations.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .components.aging import AgingModel
from .components.auxiliary import Auxiliaries
from .components.battery import BatteryPack
from .components.chassis import Chassis
from .components.driveline import Driveline
from .components.hvac import HVAC
from .components.motor import Motor


@dataclass
class ControlStrategy:
    """Vehicle-level supervisory behaviour."""

    regen_enabled: bool = True

    max_regen_decel_mps2: float = 1.2
    """Deceleration the electric machine is allowed to produce on its own.
    Above this the friction brakes blend in. Set by ride comfort and by how
    much authority the brake blending system is certified for."""

    regen_blend_fraction: float = 0.95
    """Share of braking demand routed to the machine first, within its limits.
    The remainder always goes to friction. Below 1.0 represents a blending
    strategy that keeps the friction brakes warm and scrubbed."""

    min_regen_speed_mps: float = 1.4
    """Regeneration cuts out below about 5 km/h, where the machine cannot
    produce useful torque and the bus must stop smoothly on friction."""

    coast_regen_fraction: float = 0.15
    """Light retardation applied when the driver lifts off, giving the
    engine-braking feel drivers expect. It recovers energy but also slows the
    bus more than coasting would, so it is not free on a route where the
    driver would otherwise have coasted to the stop."""

    soc_start: float = 0.95
    soc_target_min: float = 0.20
    """Operational floor. Dispatchers normally will not send a bus out on a
    block that would take it below this, which is what makes usable energy
    smaller than nameplate energy."""

    cabin_preconditioned: bool = True
    """Whether the cabin is brought to setpoint on depot power before
    departure. It is the single cheapest winter range measure there is, and
    modelling it as free grid energy rather than battery energy is exactly why
    it is worth doing."""

    precondition_cabin_c: float | None = None
    pack_preconditioned: bool = False
    precondition_pack_c: float | None = None


@dataclass
class Vehicle:
    """A complete bus."""

    name: str = "unnamed_vehicle"
    variant: str = ""
    chassis: Chassis = field(default_factory=Chassis)
    driveline: Driveline = field(default_factory=Driveline)
    motor: Motor = field(default_factory=Motor)
    battery: BatteryPack = field(default_factory=BatteryPack)
    hvac: HVAC = field(default_factory=HVAC)
    auxiliaries: Auxiliaries = field(default_factory=Auxiliaries)
    aging: AgingModel = field(default_factory=AgingModel)
    control: ControlStrategy = field(default_factory=ControlStrategy)

    seats: int = 28
    standees: int = 60
    source: str = "unspecified"
    notes: str = ""

    @property
    def passenger_capacity(self) -> int:
        return self.seats + self.standees

    @property
    def validated(self) -> bool:
        """True only when every component carries measured data.

        Deliberately strict. One unvalidated component is enough to make the
        overall number indicative rather than defensible, and the report says so
        on its face.
        """
        return all(c.validated for c in (
            self.chassis, self.driveline, self.motor,
            self.battery, self.hvac, self.auxiliaries))

    def unvalidated_components(self) -> list:
        named = [("chassis", self.chassis), ("driveline", self.driveline),
                 ("motor", self.motor), ("battery", self.battery),
                 ("hvac", self.hvac), ("auxiliaries", self.auxiliaries),
                 ("aging", self.aging)]
        return [n for n, c in named if not c.validated]

    def describe(self) -> str:
        head = f"{self.name}" + (f" ({self.variant})" if self.variant else "")
        lines = [
            head,
            "-" * len(head),
            f"  capacity   {self.seats} seated + {self.standees} standing "
            f"= {self.passenger_capacity}",
            f"  chassis    {self.chassis.describe()}",
            f"  driveline  {self.driveline.describe()}",
            f"  motor      {self.motor.describe()}",
            f"  battery    {self.battery.describe()}",
            f"  hvac       {self.hvac.describe()}",
            f"  auxiliary  {self.auxiliaries.describe()}",
            f"  ageing     {self.aging.describe()}",
        ]
        missing = self.unvalidated_components()
        if missing:
            lines.append(f"  WARNING    unvalidated data in: {', '.join(missing)}")
        return "\n".join(lines)
