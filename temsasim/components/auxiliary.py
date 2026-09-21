"""Auxiliary electrical loads other than climate control.

On a diesel bus most of these were belt-driven and invisible. Electrifying them
makes each one a separate, measurable draw on the pack, and collectively they
are worth several kWh over a shift -- enough to decide whether a route closes.

The air compressor deserves particular attention. Buses run pneumatic brakes,
door actuators and kneeling suspension, and every stop costs air: door cycles,
a brake application, and a kneel-and-rise. So compressor duty scales with stops
per kilometre, not with distance. That is why a dense urban route consumes more
auxiliary energy per kilometre than a suburban one at the same average speed,
and it is invisible to any model driven by a speed trace alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Auxiliaries:
    name: str = "unnamed_auxiliaries"

    constant_w: float = 1_200.0
    """Always-on hotel load: controllers, telematics, ticketing, CCTV,
    passenger information displays, the 24 V DC-DC converter's own losses."""

    lighting_w: float = 700.0
    lighting_night_w: float = 1_400.0

    steering_pump_w: float = 1_800.0
    """Electro-hydraulic steering. Draw is load-dependent in reality; the
    ``steering_speed_factor`` below applies the observation that steering effort
    is highest at low speed, where the bus is manoeuvring."""

    steering_speed_factor: float = 0.55
    """Fraction of full steering draw at road speeds above 30 km/h."""

    air_compressor_w: float = 5_500.0
    """Compressor shaft power while running. It is not continuous -- see the
    duty model below."""

    air_compressor_duty_base: float = 0.06
    """Baseline duty cycle from system leakage alone, with the bus rolling and
    nothing actuating. Real fleets leak more than new vehicles; a rising
    baseline here is a genuinely useful maintenance signal from telematics."""

    air_per_stop_j: float = 95_000.0
    """Compressor work per full stop event: doors open and shut, service brake
    application, kneel and rise. Scales the duty cycle with stop frequency
    rather than with distance."""

    dcdc_efficiency: float = 0.94
    """High-voltage to 24 V conversion efficiency. Applied to the low-voltage
    loads above, since they are all fed through it."""

    source: str = "unspecified"
    validated: bool = False

    # ----------------------------------------------------------------------
    def power(self, speed_mps: float, *, night: bool = False,
              stops_per_hour: float = 0.0, doors_open: bool = False) -> dict:
        """Instantaneous auxiliary draw at the high-voltage bus [W]."""
        lv_loads = self.constant_w + (self.lighting_night_w if night else self.lighting_w)
        lv = lv_loads / max(self.dcdc_efficiency, 0.1)

        if speed_mps < 1.0:
            steer = self.steering_pump_w * 0.25
        elif speed_mps < 8.33:  # below 30 km/h, manoeuvring
            steer = self.steering_pump_w
        else:
            steer = self.steering_pump_w * self.steering_speed_factor

        duty = self.air_compressor_duty_base
        if self.air_compressor_w > 0.0:
            duty += (stops_per_hour * self.air_per_stop_j
                     / (3600.0 * self.air_compressor_w))
        duty = min(duty, 0.95)
        air = self.air_compressor_w * duty

        return {
            "lv_loads_w": lv,
            "steering_w": steer,
            "air_compressor_w": air,
            "air_compressor_duty": duty,
            "total_w": lv + steer + air,
        }

    def describe(self) -> str:
        flag = "VALIDATED" if self.validated else "UNVALIDATED"
        return (f"{self.name}: ~{(self.constant_w + self.lighting_w) / 1e3:.1f} kW hotel "
                f"+ steering + air [{flag}]")
