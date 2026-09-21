"""Physical constants and unit helpers.

Everything inside the solver is SI: metres, seconds, kilograms, Newtons, Watts,
Joules, Kelvin for absolute temperatures. Degrees Celsius appear only at the
user-facing boundary. Conversions live here so no magic numbers end up buried
in the physics modules.
"""

from __future__ import annotations

import math

G = 9.80665                # m/s^2   standard gravity
R_GAS = 8.314462618        # J/(mol K) universal gas constant
T0_K = 273.15              # K       0 degC

RHO_AIR_STD = 1.2256       # kg/m^3  dry air, 15 degC, 101.325 kPa
CP_AIR = 1006.0            # J/(kg K) specific heat of air, constant pressure
R_DRY_AIR = 287.058        # J/(kg K) specific gas constant, dry air

KMH = 1.0 / 3.6            # km/h -> m/s
MPH = 0.44704              # mph  -> m/s
RPM = 2.0 * math.pi / 60.0  # rpm  -> rad/s
KWH = 3.6e6                # kWh  -> J


def c_to_k(t_c: float) -> float:
    return t_c + T0_K


def k_to_c(t_k: float) -> float:
    return t_k - T0_K


def air_density(t_c: float, pressure_pa: float = 101325.0) -> float:
    """Ideal-gas air density.

    Worth carrying explicitly: a -10 degC winter morning is about 9 % denser
    than 15 degC standard air, so aerodynamic drag rises with it at exactly the
    time the heater is also drawing its maximum.
    """
    return pressure_pa / (R_DRY_AIR * c_to_k(t_c))
