"""Road load and vehicle mass.

The tractive force at the wheels is the sum of four terms::

    F = F_roll + F_aero + F_grade + F_inertia

Two parameterisations are supported, and which one you use says a lot about
how much you should trust the answer:

``coastdown``
    ``F = A + B*v + C*v**2`` with A [N], B [N/(m/s)], C [N/(m/s)^2] measured by
    a coast-down test to SAE J1263 / J2263 (or the UNECE equivalent used for
    VECTO road-load determination). These coefficients lump rolling resistance,
    aerodynamic drag and driveline spin losses into one curve that was actually
    measured on the vehicle. **Prefer this.** Note that A and B were measured at
    one test mass and one ambient condition; ``coastdown_test_mass_kg`` and
    ``coastdown_air_density`` let the model rescale them.

``physical``
    Separate rolling-resistance and aerodynamic coefficients. Useful before the
    vehicle exists, for sensitivity studies, and for splitting the energy
    balance into named buckets in the report. Less trustworthy in absolute
    terms.

Both give a force; the difference is whether you can defend the number.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..units import G, RHO_AIR_STD


@dataclass
class Chassis:
    """Glider: mass, road load and wheels."""

    name: str = "unnamed_chassis"

    # --- mass -------------------------------------------------------------
    kerb_mass_kg: float = 12000.0
    """Mass of the complete vehicle, ready to drive, without passengers."""

    gross_mass_kg: float = 19000.0
    """Technically permissible maximum laden mass; used to flag overload."""

    passenger_mass_kg: float = 68.0
    """Mass per passenger. UNECE R107 uses 68 kg per passenger (plus 3 kg of
    hand luggage for some classes); VECTO bus payload uses 68 kg as well."""

    rotational_inertia_kgm2: float = 0.0
    """Sum of wheel and hub inertia about the wheel axis [kg m^2]. Motor and
    driveline inertia is added by the driveline model, which knows the gear
    ratio it must be referred through."""

    # --- road load, physical parameterisation ------------------------------
    rolling_c0: float = 0.007
    """Speed-independent rolling resistance coefficient. A modern low-rolling-
    resistance city-bus tyre sits around 0.006-0.008; a worn or under-inflated
    one is materially worse, which is why fleet data beats a catalogue value."""

    rolling_c1_s_per_m: float = 0.0
    """Speed-proportional rolling term [1/(m/s)], usually small for buses."""

    drag_coefficient: float = 0.62
    """Cd. A boxy city bus is typically 0.55-0.70; an intercity coach with
    faired underbody and mirrors replaced by cameras can reach 0.40-0.50."""

    frontal_area_m2: float = 7.5
    """Frontal area [m^2]. Roughly width x height minus glazing curvature; a
    12 m city bus is about 2.55 m x 3.2 m gross."""

    cda_m2: float | None = None
    """Drag area Cd·A measured directly, as an air-drag certificate (VECTO
    CdxA) states it. When given it replaces ``drag_coefficient`` ×
    ``frontal_area_m2``; the two are kept only for the report."""

    axles: list | None = None
    """Per-axle tyre data in VECTO's form, one dict per axle with ``share``
    (of vehicle mass), ``rrc_iso`` (tyre label coefficient), ``fz_iso`` (the
    ISO 28580 test load [N]), ``twin_tyres`` and optionally
    ``wheel_inertia_kgm2`` per wheel. When present the rolling resistance is
    computed from the tyres with VECTO's load correction, so it follows the
    laden mass, and ``rolling_c0`` is ignored."""

    # --- road load, coast-down parameterisation ----------------------------
    coastdown_a_n: float | None = None
    coastdown_b_n_per_mps: float | None = None
    coastdown_c_n_per_mps2: float | None = None
    coastdown_test_mass_kg: float | None = None
    coastdown_air_density: float = RHO_AIR_STD

    # --- wheels ------------------------------------------------------------
    tyre_dynamic_radius_m: float = 0.478
    """Dynamic rolling radius under load, not the unloaded radius. For a
    295/80 R22.5 bus tyre this is about 0.478 m; using the unloaded radius
    biases both motor speed and torque by a couple of percent."""

    source: str = "unspecified"
    validated: bool = False
    notes: str = ""

    # ----------------------------------------------------------------------
    @property
    def uses_coastdown(self) -> bool:
        return None not in (
            self.coastdown_a_n,
            self.coastdown_b_n_per_mps,
            self.coastdown_c_n_per_mps2,
        )

    def mass(self, n_passengers: float) -> float:
        """Laden mass at a given occupancy [kg]."""
        return self.kerb_mass_kg + n_passengers * self.passenger_mass_kg

    @property
    def drag_area_m2(self) -> float:
        return self.cda_m2 if self.cda_m2 is not None else self.drag_coefficient * self.frontal_area_m2

    @property
    def wheel_inertia_kgm2(self) -> float:
        """Wheel inertia from the axle table when it has it, else the field."""
        if self.axles:
            total = 0.0
            for a in self.axles:
                per_wheel = float(a.get("wheel_inertia_kgm2", 0.0) or 0.0)
                total += per_wheel * (4 if a.get("twin_tyres") else 2)
            if total > 0:
                return total
        return self.rotational_inertia_kgm2

    def rolling_coefficient(self, mass_kg: float) -> float:
        """Speed-independent rolling coefficient at a laden mass.

        With an axle table this is VECTO's load-corrected tyre coefficient::

            c_r = Σ share · RRC_ISO · (F_z,wheel / F_z,ISO) ** (0.9 − 1)

        with ``F_z,wheel = share · m · g / wheels on the axle``: a wheel carrying
        less than the ISO test load rolls slightly worse than its label says,
        one carrying more slightly better. Otherwise it is ``rolling_c0``.
        """
        if not self.axles:
            return self.rolling_c0
        total = 0.0
        for a in self.axles:
            share, rrc, fz_iso = float(a["share"]), float(a["rrc_iso"]), float(a.get("fz_iso") or 0)
            if share <= 0:
                continue
            if fz_iso <= 0:
                total += share * rrc
                continue
            n_wheels = 4 if a.get("twin_tyres") else 2
            fz = mass_kg * G * share / n_wheels
            total += share * rrc * (fz / fz_iso) ** (0.9 - 1.0)
        return total

    def equivalent_rotating_mass_kg(self, driveline_inertia_kgm2: float = 0.0) -> float:
        """Rotating inertia expressed as an equivalent translational mass.

        ``m_eq = I_total / r_dyn**2``. For a bus this is typically 3-6 % of kerb
        mass, and it is not negligible on a stop-start city duty cycle where the
        vehicle accelerates several hundred times per shift.
        """
        i_total = self.wheel_inertia_kgm2 + driveline_inertia_kgm2
        return i_total / (self.tyre_dynamic_radius_m ** 2)

    # ----------------------------------------------------------------------
    def resistive_force(
        self,
        speed_mps,
        *,
        mass_kg: float,
        grade_rad=0.0,
        air_density: float = RHO_AIR_STD,
        headwind_mps: float = 0.0,
    ):
        """Total resistive force [N], positive opposing motion.

        Returns a dict of the named contributions so the energy balance in the
        report can attribute every joule to rolling, aero or grade.
        """
        v = np.asarray(speed_mps, dtype=float)
        theta = np.asarray(grade_rad, dtype=float)
        v_air = v + headwind_mps

        if self.uses_coastdown:
            # Coast-down lumps rolling and aero together. Split them by the
            # functional form: constant + linear is "rolling", quadratic is
            # "aero". Rescale the mass-dependent constant term if the test mass
            # differed from the simulated laden mass, and rescale the quadratic
            # term for air density.
            m_ref = self.coastdown_test_mass_kg or mass_kg
            mass_ratio = mass_kg / m_ref
            rho_ratio = air_density / self.coastdown_air_density
            f_roll = self.coastdown_a_n * mass_ratio + self.coastdown_b_n_per_mps * v
            f_aero = self.coastdown_c_n_per_mps2 * rho_ratio * v_air ** 2
        else:
            f_roll = (mass_kg * G * np.cos(theta)
                      * (self.rolling_coefficient(mass_kg) + self.rolling_c1_s_per_m * v))
            f_aero = 0.5 * air_density * self.drag_area_m2 * v_air ** 2

        # Rolling resistance and aero always oppose motion; grade does not.
        f_roll = np.where(v > 0.01, f_roll, 0.0)
        f_aero = np.where(v > 0.01, f_aero, 0.0)
        f_grade = mass_kg * G * np.sin(theta)

        return {"rolling": f_roll, "aero": f_aero, "grade": f_grade}

    def describe(self) -> str:
        if self.uses_coastdown:
            road_load = "coast-down (measured)"
        elif self.axles:
            road_load = (f"tyre RRC x {len(self.axles)} axles, CdA {self.drag_area_m2:.2f} m2")
        else:
            road_load = "physical Cr/Cd"
        flag = "VALIDATED" if self.validated else "UNVALIDATED"
        return f"{self.name}: {road_load}, {self.kerb_mass_kg:.0f} kg kerb [{flag}]"
