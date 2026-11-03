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
from .components.engine import Engine
from .components.hvac import HVAC
from .components.motor import Motor
from .components.transmission import Transmission

POWERTRAINS = {"battery_electric": "Bataryalı elektrikli", "diesel": "Dizel"}
# Which component slots a powertrain is built from (slot name -> library kind).
SLOTS_BY_POWERTRAIN = {
    "battery_electric": {"chassis": "chassis", "driveline": "driveline", "motor": "motor",
                         "battery": "battery", "hvac": "hvac", "auxiliaries": "auxiliary",
                         "aging": "aging"},
    "diesel": {"chassis": "chassis", "transmission": "transmission", "engine": "engine",
               "hvac": "hvac", "auxiliaries": "auxiliary"},
}


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
    powertrain: str = "battery_electric"
    """``battery_electric`` or ``diesel``. Decides which components the
    vehicle is built from and which solver runs it."""

    chassis: Chassis = field(default_factory=Chassis)
    driveline: Driveline = field(default_factory=Driveline)
    motor: Motor = field(default_factory=Motor)
    battery: BatteryPack = field(default_factory=BatteryPack)
    hvac: HVAC = field(default_factory=HVAC)
    auxiliaries: Auxiliaries = field(default_factory=Auxiliaries)
    aging: AgingModel = field(default_factory=AgingModel)
    control: ControlStrategy = field(default_factory=ControlStrategy)

    engine: Engine | None = None
    transmission: Transmission | None = None

    seats: int = 28
    standees: int = 60
    fuel_tank_l: float = 400.0
    """Diesel tank volume, only for the fuel gauge in the playback."""
    source: str = "unspecified"
    notes: str = ""

    def __post_init__(self) -> None:
        if self.powertrain not in POWERTRAINS:
            raise ValueError(f"unknown powertrain {self.powertrain!r}; "
                             f"one of {sorted(POWERTRAINS)}")
        if self.is_diesel:
            if self.engine is None:
                self.engine = Engine()
            if self.transmission is None:
                self.transmission = Transmission()

    @property
    def is_diesel(self) -> bool:
        return self.powertrain == "diesel"

    @property
    def passenger_capacity(self) -> int:
        return self.seats + self.standees

    @property
    def peak_power_w(self) -> float:
        """Rated traction power, whichever machine provides it."""
        return self.engine.rated_power_w if self.is_diesel else self.motor.peak_power_w

    def tractive_limits(self, power_margin: float = 0.90) -> tuple:
        """(wheel power [W], wheel torque [Nm], wheel drag [Nm]) the driver
        model may count on when it plans accelerations."""
        if self.is_diesel:
            # An AMT launches in its 2nd-4th gear, not 1st, and the engine also
            # carries the auxiliaries: plan with the launch gear and ~10 kW less,
            # so the route the driver builds stays achievable by construction.
            eta = 0.93
            tr = self.transmission
            launch = tr.gear_ratios[min(2, tr.n_gears - 1)] * tr.axle_ratio
            return ((self.engine.rated_power_w - 10e3) * power_margin * eta,
                    self.engine.max_torque_nm * power_margin * launch * eta, 0.0)
        dl = self.driveline
        return (self.motor.peak_power_w * power_margin * dl.gearbox_efficiency * dl.final_drive_efficiency,
                self.motor.peak_torque_nm * power_margin * dl.total_ratio(0) * dl.final_drive_efficiency,
                dl.spin_loss_nm)

    def _named_components(self) -> list:
        if self.is_diesel:
            return [("chassis", self.chassis), ("transmission", self.transmission),
                    ("engine", self.engine), ("hvac", self.hvac),
                    ("auxiliaries", self.auxiliaries)]
        return [("chassis", self.chassis), ("driveline", self.driveline),
                ("motor", self.motor), ("battery", self.battery),
                ("hvac", self.hvac), ("auxiliaries", self.auxiliaries),
                ("aging", self.aging)]

    @property
    def validated(self) -> bool:
        """True only when every component carries measured data.

        Deliberately strict. One unvalidated component is enough to make the
        overall number indicative rather than defensible, and the report says so
        on its face.
        """
        return all(c.validated for n, c in self._named_components() if n != "aging")

    def unvalidated_components(self) -> list:
        return [n for n, c in self._named_components() if not c.validated]

    def describe(self) -> str:
        head = f"{self.name}" + (f" ({self.variant})" if self.variant else "")
        lines = [
            head,
            "-" * len(head),
            f"  powertrain {POWERTRAINS.get(self.powertrain, self.powertrain)}",
            f"  capacity   {self.seats} seated + {self.standees} standing "
            f"= {self.passenger_capacity}",
            f"  chassis    {self.chassis.describe()}",
        ]
        if self.is_diesel:
            lines += [
                f"  engine     {self.engine.describe()}",
                f"  transmission {self.transmission.describe()}",
                f"  hvac       {self.hvac.describe()}",
                f"  auxiliary  {self.auxiliaries.describe()}",
            ]
        else:
            lines += [
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
