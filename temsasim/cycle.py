"""Drive cycles, routes and ambient conditions.

A ``DriveCycle`` is what the solver consumes: speed, grade, occupancy and door
state against time. It can come from three places.

1.  A **standard cycle** CSV (CBD, Manhattan, UK Bus 6, ...). Reproducible,
    comparable, regulator-friendly, and a poor description of any real route.
    Standard cycles have no gradient and no passengers.
2.  A **measured trace** from vehicle CAN and GPS. The gold standard, and the
    only thing worth validating the model against.
3.  A **synthesised route**, built by :class:`Route` from a real alignment --
    distance, elevation, speed limits and bus-stop positions -- driven by a
    simple kinematic driver model. This is what lets a tender team answer
    "will it do *our* line" rather than "it did well on CBD14".

Occupancy and door state are first-class here rather than bolted on, because
for a city bus they drive the auxiliary and climate loads that a speed trace
alone cannot see.
"""

from __future__ import annotations

import csv
import pathlib
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Environment:
    """Ambient conditions for a run."""

    ambient_c: float = 20.0
    solar_irradiance_w_per_m2: float = 0.0
    """Global irradiance on the glazing plane. A clear Mediterranean summer
    midday is roughly 850-950 W/m^2; overcast winter is under 100."""

    headwind_mps: float = 0.0
    air_pressure_pa: float = 101_325.0
    night: bool = False
    shading_factor: float = 1.0
    """Reduction for urban canyon shading, tree cover and awnings. Dense city
    centres see far less solar load than an open suburban corridor."""

    label: str = "nominal"

    def describe(self) -> str:
        return (f"{self.label}: {self.ambient_c:+.0f} degC, "
                f"{self.solar_irradiance_w_per_m2:.0f} W/m2 solar, "
                f"{'night' if self.night else 'day'}")


@dataclass
class DriveCycle:
    """Time-series duty definition."""

    time_s: np.ndarray
    speed_mps: np.ndarray
    grade_rad: np.ndarray | None = None
    passengers: np.ndarray | None = None
    doors_open: np.ndarray | None = None
    name: str = "unnamed_cycle"
    source: str = "unspecified"
    meta: dict = field(default_factory=dict)
    """What the route driver decided (traffic level, signal timings, each
    red-light wait and jam). Empty for standard cycles, whose speed trace
    already contains whatever traffic was present when it was recorded."""

    # ----------------------------------------------------------------------
    def __post_init__(self) -> None:
        self.time_s = np.asarray(self.time_s, dtype=float)
        self.speed_mps = np.asarray(self.speed_mps, dtype=float)
        n = self.time_s.size
        if self.speed_mps.size != n:
            raise ValueError(f"cycle {self.name!r}: time and speed lengths differ")
        if self.grade_rad is None:
            self.grade_rad = np.zeros(n)
        if self.passengers is None:
            self.passengers = np.zeros(n)
        if self.doors_open is None:
            self.doors_open = self._infer_door_state()
        self.grade_rad = np.asarray(self.grade_rad, dtype=float)
        self.passengers = np.asarray(self.passengers, dtype=float)
        self.doors_open = np.asarray(self.doors_open, dtype=bool)

    def _infer_door_state(self, stop_speed_mps: float = 0.2,
                          min_dwell_s: float = 6.0) -> np.ndarray:
        """Guess where the doors opened, for cycles that do not say.

        A standstill longer than ``min_dwell_s`` is treated as a passenger stop
        with doors open; shorter ones are traffic. Crude, and it will call some
        traffic lights bus stops, but it is far better than assuming the doors
        never open -- which is what a model with no door state implicitly does.
        """
        v = self.speed_mps
        stopped = v < stop_speed_mps
        doors = np.zeros(v.size, dtype=bool)
        dt = float(np.median(np.diff(self.time_s))) if v.size > 1 else 1.0
        min_n = max(int(min_dwell_s / max(dt, 1e-6)), 1)

        i = 0
        while i < stopped.size:
            if stopped[i]:
                j = i
                while j < stopped.size and stopped[j]:
                    j += 1
                if (j - i) >= min_n:
                    doors[i:j] = True
                i = j
            else:
                i += 1
        return doors

    # ----------------------------------------------------------------------
    @property
    def duration_s(self) -> float:
        return float(self.time_s[-1] - self.time_s[0])

    @property
    def distance_m(self) -> float:
        return float(np.trapezoid(self.speed_mps, self.time_s))

    @property
    def distance_km(self) -> float:
        return self.distance_m / 1000.0

    @property
    def mean_speed_kmh(self) -> float:
        return self.distance_km / (self.duration_s / 3600.0)

    @property
    def max_speed_kmh(self) -> float:
        return float(self.speed_mps.max()) * 3.6

    @property
    def stop_count(self) -> int:
        """Number of door-opening events."""
        d = self.doors_open.astype(int)
        return int(np.sum(np.diff(d) == 1) + (1 if d[0] else 0))

    @property
    def stops_per_km(self) -> float:
        return self.stop_count / max(self.distance_km, 1e-6)

    @property
    def idle_fraction(self) -> float:
        return float(np.mean(self.speed_mps < 0.2))

    def acceleration(self) -> np.ndarray:
        """Central-difference acceleration [m/s^2]."""
        return np.gradient(self.speed_mps, self.time_s)

    def resample(self, dt_s: float = 1.0) -> "DriveCycle":
        """Resample onto a uniform time step.

        Required before simulation: the ADVISOR library mixes 0.1 s and 1 s
        traces, and a fixed-step solver on a non-uniform grid silently
        misweights the energy integral.
        """
        t_new = np.arange(self.time_s[0], self.time_s[-1] + 1e-9, dt_s)
        return DriveCycle(
            time_s=t_new,
            speed_mps=np.interp(t_new, self.time_s, self.speed_mps),
            grade_rad=np.interp(t_new, self.time_s, self.grade_rad),
            passengers=np.interp(t_new, self.time_s, self.passengers),
            doors_open=np.interp(t_new, self.time_s,
                                 self.doors_open.astype(float)) > 0.5,
            name=self.name,
            source=self.source,
            meta=self.meta,
        )

    def with_occupancy(self, passengers, *, seats: int | None = None) -> "DriveCycle":
        """Return a copy carrying an occupancy profile.

        ``passengers`` may be a scalar, an array of the same length, or a
        callable of normalised distance returning passenger count. A real line
        fills toward the centre and empties at the terminus, and that shape
        matters: mass affects traction energy while headcount affects both the
        ventilation load and the free metabolic heat.
        """
        if callable(passengers):
            d = np.concatenate([[0.0], np.cumsum(
                self.speed_mps[:-1] * np.diff(self.time_s))])
            frac = d / max(d[-1], 1e-9)
            pax = np.array([float(passengers(f)) for f in frac])
        else:
            pax = np.broadcast_to(np.asarray(passengers, dtype=float),
                                  self.time_s.shape).astype(float).copy()
        if seats is not None:
            pax = np.clip(pax, 0.0, float(seats))
        return DriveCycle(self.time_s, self.speed_mps, self.grade_rad, pax,
                          self.doors_open, self.name, self.source, self.meta)

    def repeat(self, n: int) -> "DriveCycle":
        """Concatenate the cycle n times, for range-to-empty runs."""
        if n < 1:
            raise ValueError("n must be >= 1")
        dt = float(np.median(np.diff(self.time_s)))
        times, speeds, grades, pax, doors = [], [], [], [], []
        for k in range(n):
            offset = k * (self.duration_s + dt)
            times.append(self.time_s + offset)
            speeds.append(self.speed_mps)
            grades.append(self.grade_rad)
            pax.append(self.passengers)
            doors.append(self.doors_open)
        return DriveCycle(
            np.concatenate(times), np.concatenate(speeds), np.concatenate(grades),
            np.concatenate(pax), np.concatenate(doors),
            name=f"{self.name} x{n}", source=self.source)

    # ----------------------------------------------------------------------
    @classmethod
    def from_csv(cls, path, name: str = "", source: str = "") -> "DriveCycle":
        """Load a CSV with a ``time_s`` column and a speed column.

        Accepts ``speed_mps``, ``speed_kmh`` or ``speed_mph``, plus the optional
        columns ``grade_rad``, ``grade_percent``, ``passengers`` and
        ``doors_open``.
        """
        path = pathlib.Path(path)
        with path.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            raise ValueError(f"cycle file {path} is empty")
        cols = {k: np.array([float(r[k]) for r in rows]) for k in rows[0]
                if rows[0][k] not in (None, "")}

        t = cols["time_s"]
        if "speed_mps" in cols:
            v = cols["speed_mps"]
        elif "speed_kmh" in cols:
            v = cols["speed_kmh"] / 3.6
        elif "speed_mph" in cols:
            v = cols["speed_mph"] * 0.44704
        else:
            raise ValueError(f"{path}: need speed_mps, speed_kmh or speed_mph")

        grade = cols.get("grade_rad")
        if grade is None and "grade_percent" in cols:
            grade = np.arctan(cols["grade_percent"] / 100.0)

        doors = cols.get("doors_open")
        return cls(t, v, grade, cols.get("passengers"),
                   doors.astype(bool) if doors is not None else None,
                   name=name or path.stem, source=source or f"csv:{path.name}")

    def summary(self) -> str:
        return (f"{self.name}: {self.distance_km:.2f} km, {self.duration_s:.0f} s, "
                f"{self.mean_speed_kmh:.1f} km/h mean, {self.max_speed_kmh:.1f} km/h max, "
                f"{self.stop_count} stops ({self.stops_per_km:.1f}/km), "
                f"{self.idle_fraction:.0%} idle")


@dataclass
class Route:
    """A real-world alignment, converted to a drive cycle by a driver model.

    Supply distance along the route with matching elevation and speed limits,
    plus the positions of the bus stops. The driver model below is deliberately
    simple -- constant acceleration to the lower of the speed limit and the
    speed from which it can still stop at the next stop -- because a more
    elaborate driver adds parameters you cannot measure. Calibrate the two
    numbers it does have (``accel`` and ``decel``) against real traces from the
    route and it reproduces energy well.
    """

    distance_m: np.ndarray
    elevation_m: np.ndarray
    speed_limit_mps: np.ndarray
    stop_positions_m: list = field(default_factory=list)
    stop_dwell_s: float = 18.0
    name: str = "unnamed_route"
    source: str = "unspecified"
    signal_positions_m: list = field(default_factory=list)
    """Traffic lights along the route. Empty means none are known, and a
    plausible urban spacing is generated when traffic is simulated."""

    def __post_init__(self) -> None:
        self.distance_m = np.asarray(self.distance_m, dtype=float)
        self.elevation_m = np.asarray(self.elevation_m, dtype=float)
        self.speed_limit_mps = np.asarray(self.speed_limit_mps, dtype=float)

    @property
    def length_km(self) -> float:
        return float(self.distance_m[-1]) / 1000.0

    def grade_at(self, s_m, smoothing_m: float = 40.0) -> np.ndarray:
        """Gradient [rad] at given distances, from a smoothed elevation profile.

        Raw GPS or DEM elevation is noisy, and differentiating it unsmoothed
        produces gradients of tens of percent that do not exist. The smoothing
        length should be a few vehicle lengths -- long enough to kill the noise,
        short enough to keep a real bridge ramp.
        """
        step = float(np.median(np.diff(self.distance_m)))
        win = max(int(smoothing_m / max(step, 1e-6)), 1)
        if win > 1:
            kernel = np.ones(win) / win
            elev = np.convolve(self.elevation_m, kernel, mode="same")
            # convolve flattens the ends; keep the originals there
            elev[:win] = self.elevation_m[:win]
            elev[-win:] = self.elevation_m[-win:]
        else:
            elev = self.elevation_m
        slope = np.gradient(elev, self.distance_m)
        return np.arctan(np.interp(s_m, self.distance_m, slope))

    def to_cycle(self, *, accel_mps2: float = 1.0, decel_mps2: float = 1.2,
                 dt_s: float = 1.0, max_time_s: float = 60_000.0,
                 occupancy=None, seats: int | None = None,
                 vehicle=None, power_margin: float = 0.90,
                 traffic: str | None = None, laps: int = 1) -> DriveCycle:
        """Run the driver model along the route and return a drive cycle.

        Pass ``vehicle`` to make the driver power-aware. Without it, the driver
        asks for ``accel_mps2`` everywhere, including pulling away up an 8 %
        hill, and a bus with a realistic motor will be reported as unable to
        follow its own route. With it, acceleration is capped at what
        ``power_margin`` of peak motor power can deliver after grade and rolling
        resistance at the laden mass -- which is what a real driver does with
        their right foot, and it keeps the cycle achievable by construction so
        that "not achievable" is reserved for genuinely undersized hardware.

        ``traffic`` is one of the keys of ``temsasim.traffic.LEVELS`` ("yok",
        "akici", "orta", "yogun", "sikisik"); None means an empty road with no
        signals. ``laps`` drives the route that many times back to back, with
        signal timings continuing in real time -- which is why repeating a
        route is done here and not by copying the trace, where a bus would
        find every light in the state it was in on the first lap.
        """
        from .traffic import drive

        a_limit_fn = None
        if vehicle is not None:
            ch = vehicle.chassis
            p_avail = vehicle.motor.peak_power_w * power_margin * (
                vehicle.driveline.gearbox_efficiency
                * vehicle.driveline.final_drive_efficiency)
            t_wheel_max = (vehicle.motor.peak_torque_nm * power_margin
                           * vehicle.driveline.total_ratio(0)
                           * vehicle.driveline.final_drive_efficiency)
            f_torque_max = t_wheel_max / ch.tyre_dynamic_radius_m
            grade_profile = self.grade_at(self.distance_m)

            def a_limit_fn(s_m: float, v_mps: float, pax: float) -> float:
                m = ch.mass(pax)
                theta = float(np.interp(s_m, self.distance_m, grade_profile))
                f_avail = min(p_avail / max(v_mps, 0.5), f_torque_max)
                f_resist = (m * 9.80665 * (np.sin(theta) + ch.rolling_c0 * np.cos(theta))
                            + 0.5 * 1.25 * ch.drag_coefficient * ch.frontal_area_m2 * v_mps ** 2
                            + vehicle.driveline.spin_loss_nm / ch.tyre_dynamic_radius_m)
                return max((f_avail - f_resist) / (m * 1.05), 0.05)

        pax_fn = None
        if occupancy is not None:
            pax_fn = occupancy if callable(occupancy) else (lambda f, _o=float(occupancy): _o)

        times, speeds, positions, doors, meta = drive(
            self, level=traffic, laps=laps, accel_mps2=accel_mps2, decel_mps2=decel_mps2,
            dt_s=dt_s, max_time_s=max_time_s, a_limit_fn=a_limit_fn, pax_fn=pax_fn)

        L = float(self.distance_m[-1])
        lap_k = np.minimum(positions // L, max(laps, 1) - 1)
        local = positions - lap_k * L
        grade = self.grade_at(local)
        pax = None
        if pax_fn is not None:
            pax = np.array([float(pax_fn(x / L)) for x in local])
            if seats is not None:
                pax = np.clip(pax, 0.0, float(seats))
        name = self.name if laps <= 1 else f"{self.name} x{laps}"
        return DriveCycle(times, speeds, grade, pax, doors, name=name,
                          source=f"route:{self.source}", meta=meta)

    @classmethod
    def from_csv(cls, path, name: str = "") -> "Route":
        """Read a route CSV with columns ``distance_m``, ``elevation_m``,
        ``speed_limit_kmh`` and an optional ``stop`` flag column."""
        path = pathlib.Path(path)
        with path.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
        d = np.array([float(r["distance_m"]) for r in rows])
        e = np.array([float(r["elevation_m"]) for r in rows])
        if "speed_limit_kmh" in rows[0]:
            vl = np.array([float(r["speed_limit_kmh"]) for r in rows]) / 3.6
        else:
            vl = np.full(d.size, 50.0 / 3.6)
        flag = lambda r, k: str(r.get(k, "0")).strip() in ("1", "true", "True")
        stops = [float(r["distance_m"]) for r in rows if flag(r, "stop")]
        signals = [float(r["distance_m"]) for r in rows if flag(r, "signal")]
        return cls(d, e, vl, stops, name=name or path.stem, source=f"csv:{path.name}",
                   signal_positions_m=signals)

    def summary(self) -> str:
        climb = float(np.sum(np.clip(np.diff(self.elevation_m), 0, None)))
        return (f"{self.name}: {self.length_km:.2f} km, {len(self.stop_positions_m)} stops, "
                f"{climb:.0f} m total climb, "
                f"{self.elevation_m.min():.0f}-{self.elevation_m.max():.0f} m elevation")
