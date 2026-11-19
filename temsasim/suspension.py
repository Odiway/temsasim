"""Suspension: ride, dive and squat, body roll and rollover of a two-axle bus.

What the model is
-----------------
A seven-degree-of-freedom vehicle: the sprung mass heaves, pitches and rolls,
and each of the four wheel stations moves vertically on its tyre. Between body
and axle sit the air springs (pressure set by the load, because the levelling
valves hold the ride height), the dampers (their measured force-velocity
curves, different in rebound and in compression), the anti-roll bars, and the
bump and rebound stops. Longitudinal and lateral acceleration enter as the
load transfer they cause; the road enters under the tyres.

From that one model come the numbers a chassis engineer asks for: ride
frequencies and damping ratios, the dive angle under braking and the squat
angle under traction, how long the body takes to settle, the roll angle per g,
the lateral acceleration and the tilt-table angle at which the inner wheels
leave the ground, and the speed at which that happens on a given curve.

Where the equations come from
-----------------------------
Every closed-form relation is the textbook one and is exposed as a small
function so the tests can check it against the book's own worked examples:

* ride rate, natural frequency ............ Gillespie, Fundamentals of Vehicle
  Dynamics, Eq. (5-8), (5-9)
* bounce and pitch frequencies, centres ... Gillespie Eq. (5-21) to (5-36)
* roll stiffness of a pair of springs ..... Gillespie Eq. (6-26)
* roll rate (roll gradient) ............... Gillespie Eq. (6-32)
* pitch angle, anti-squat ................. Gillespie Eq. (7-14)
* rollover threshold, rigid ............... Gillespie Eq. (9-4)
* rollover threshold, suspended ........... Gillespie Eq. (9-6)
* steer angle in a steady turn ............ Gillespie Eq. (6-16)
* road roughness classes .................. ISO 8608:2016, Table C.2
* comfort weighting Wk .................... ISO 2631-1:1997, Annex A

The seven-degree-of-freedom model is the usual full-vehicle ride model (for
example Jazar, Vehicle Dynamics: Theory and Application, "full car vibrating
model"), extended with the roll-centre load transfer of Gillespie's chapter 6
and the lift-off of the tyres.

Conventions
-----------
SI inside. x forward, y to the LEFT, z up. Pitch is positive nose-down (a dive
is positive, a squat negative). Roll is positive right-side-down, which is how
the body leans in a left turn; lateral acceleration is positive in a left
turn. Suspension travel is positive in compression. The centre of gravity is
given as: x behind the front axle, y to the RIGHT of the centre line, z above
the ground.

What is simplified
------------------
Small angles; the axle load transfer of a rigid axle and of an independent
suspension are both written with a roll centre; twin tyres act at the centre
of the pair; the air spring is a polytropic column whose stiffness at the
static point is the supplier's value; tyres are linear springs that cannot
pull. "Rollover" means the moment both inner wheels carry no load: from there
on the vehicle is no longer held by anything this model contains.
"""

from __future__ import annotations

import copy
import math
import pathlib
from dataclasses import dataclass, field

import numpy as np
import yaml

G = 9.80665
_trapz = getattr(np, "trapezoid", None) or np.trapz
P_ATM = 101325.0
BAR = 1.0e5

# ---------------------------------------------------------------------------
# textbook relations (kept as plain functions; the tests reproduce the books'
# worked examples with them)
# ---------------------------------------------------------------------------


def ride_rate(spring_rate: float, tyre_rate: float) -> float:
    """Suspension and tyre in series. Gillespie Eq. (5-8)."""
    return spring_rate * tyre_rate / (spring_rate + tyre_rate)


def natural_frequency_hz(rate: float, mass: float) -> float:
    """Undamped natural frequency of a mass on a spring. Gillespie Eq. (5-9)."""
    return math.sqrt(max(rate, 0.0) / mass) / (2.0 * math.pi)


def bounce_pitch(kf: float, kr: float, b: float, c: float, mass: float, k2: float) -> dict:
    """Coupled bounce and pitch of a body on two ride rates.

    ``kf``/``kr`` are the axle ride rates (both wheels), ``b``/``c`` the
    distances from the centre of gravity to the front and rear axle, ``k2`` the
    square of the pitch radius of gyration. Gillespie Eq. (5-21) to (5-36).
    Returns the two frequencies with their oscillation centres (distance ahead
    of the centre of gravity, negative = behind).
    """
    alpha = (kf + kr) / mass
    beta = (kr * c - kf * b) / mass
    gamma = (kf * b * b + kr * c * c) / (mass * k2)
    root = math.sqrt((alpha - gamma) ** 2 / 4.0 + beta * beta / k2)
    out = []
    for w2 in ((alpha + gamma) / 2.0 + root, (alpha + gamma) / 2.0 - root):
        w = math.sqrt(max(w2, 0.0))
        # Z/theta from Eq. (5-30); a centre far from the CG is a bounce mode
        centre = -beta / (alpha - w2) if abs(alpha - w2) > 1e-12 else math.inf
        out.append({"omega_rad_s": w, "hz": w / (2 * math.pi), "centre_m": centre})
    # Gillespie's naming: a centre inside the wheelbase is a pitch mode, one outside a bounce mode
    for o in out:
        o["kind"] = "pitch" if -c < o["centre_m"] < b else "bounce"
    if out[0]["kind"] == out[1]["kind"]:
        far = max(out, key=lambda o: abs(o["centre_m"]))
        for o in out:
            o["kind"] = "bounce" if o is far else "pitch"
    return {"alpha": alpha, "beta": beta, "gamma": gamma, "modes": out, "dynamic_index": k2 / (b * c)}


def roll_stiffness_springs(spring_rate: float, separation: float) -> float:
    """Roll stiffness of two springs a distance apart [Nm/rad]. Gillespie Eq. (6-26)."""
    return 0.5 * spring_rate * separation * separation


def roll_rate(weight_times_h1: float, roll_stiffness: float) -> float:
    """Roll angle per g [rad/g]. Gillespie Eq. (6-32).

    ``weight_times_h1`` is the sprung weight times the height of its centre of
    gravity above the roll axis; ``roll_stiffness`` is front plus rear.
    """
    return weight_times_h1 / (roll_stiffness - weight_times_h1)


def rollover_rigid(track: float, cg_height: float, cross_slope: float = 0.0) -> float:
    """Lateral acceleration [g] at which a rigid vehicle lifts its inner wheels. Gillespie Eq. (9-4)."""
    return (track / 2.0 + cross_slope * cg_height) / cg_height


def rollover_suspended(track: float, cg_height: float, roll_rate_rad_per_g: float, roll_centre_height: float) -> float:
    """The same with the body leaning out on its suspension. Gillespie Eq. (9-6)."""
    return track / (2.0 * cg_height) / (1.0 + roll_rate_rad_per_g * (1.0 - roll_centre_height / cg_height))


def pitch_angle(weight: float, cg_height: float, wheelbase: float, ax_g: float, kf: float, kr: float,
                anti_front: float = 0.0, anti_rear: float = 0.0) -> float:
    """Pitch angle [rad] under a longitudinal acceleration of ``ax_g`` g.

    Gillespie Eq. (7-14) with the anti-geometry written as the share of the
    load transfer the links carry (0 = none, 1 = the axle does not deflect).
    Positive result = the nose goes down.
    """
    dw = weight * abs(ax_g) * cg_height / wheelbase
    theta = (dw * (1.0 - anti_front) / kf + dw * (1.0 - anti_rear) / kr) / wheelbase
    return theta if ax_g < 0 else -theta


def steer_angle(wheelbase: float, radius: float, speed_mps: float, understeer_rad_per_g: float) -> float:
    """Front-wheel steer angle [rad] to hold a curve of ``radius`` at a speed.

    delta = L/R + K ay, the steady-state cornering equation, Gillespie Eq. (6-16).
    """
    return wheelbase / radius + understeer_rad_per_g * speed_mps ** 2 / (radius * G)


def yaw_velocity_gain(wheelbase: float, speed_mps: float, understeer_rad_per_g: float) -> float:
    """Yaw rate per unit steer angle [1/s]: V / (L + K V^2 / g). Gillespie's yaw velocity gain."""
    return speed_mps / (wheelbase + understeer_rad_per_g * speed_mps ** 2 / G)


def characteristic_speed(wheelbase: float, understeer_rad_per_g: float) -> float:
    """Speed at which an understeering vehicle needs twice the Ackermann angle [m/s]."""
    return math.sqrt(wheelbase * G / understeer_rad_per_g) if understeer_rad_per_g > 0 else math.inf


# ISO 8608:2016 Table C.2: geometric mean of the displacement PSD at n0 = 0.1 cycle/m [m^3]
ISO8608 = {"A": 16e-6, "B": 64e-6, "C": 256e-6, "D": 1024e-6, "E": 4096e-6}
ISO8608_LABEL = {"A": "A · çok iyi", "B": "B · iyi", "C": "C · orta", "D": "D · kötü", "E": "E · çok kötü"}
ISO8608_N0 = 0.1


def road_psd(road_class: str, speed_mps: float, f_hz):
    """Displacement PSD of the road as the wheel sees it [m^2/Hz] at speed v.

    Gd(n) = Gd(n0) (n/n0)^-2 with n = f / v.
    """
    f = np.asarray(f_hz, dtype=float)
    return ISO8608[road_class] * ISO8608_N0 ** 2 * speed_mps / np.maximum(f, 1e-9) ** 2


def wk_weighting(f_hz):
    """ISO 2631-1 frequency weighting Wk (vertical, seated or standing), magnitude."""
    f = np.asarray(f_hz, dtype=float)
    p = 2j * math.pi * f
    w1, w2, w3, w4, w5, w6 = (2 * math.pi * v for v in (0.4, 100.0, 12.5, 12.5, 2.37, 3.35))
    q4, q5, q6 = 0.63, 0.91, 0.91
    hh = 1.0 / (1.0 + math.sqrt(2.0) * w1 / p + (w1 / p) ** 2)
    hl = 1.0 / (1.0 + math.sqrt(2.0) * p / w2 + (p / w2) ** 2)
    ht = (1.0 + p / w3) / (1.0 + p / (q4 * w4) + (p / w4) ** 2)
    hs = (1.0 + p / (q5 * w5) + (p / w5) ** 2) / (1.0 + p / (q6 * w6) + (p / w6) ** 2) * (w5 / w6) ** 2
    return np.abs(hh * hl * ht * hs)


COMFORT_STEPS = [(0.315, "rahatsız etmez"), (0.63, "biraz rahatsız eder"), (1.0, "oldukça rahatsız eder"),
                 (1.6, "rahatsız eder"), (2.5, "çok rahatsız eder"), (math.inf, "aşırı rahatsız eder")]


def comfort_label(aw: float) -> str:
    """ISO 2631-1 Annex C wording for a weighted r.m.s. acceleration."""
    for limit, text in COMFORT_STEPS:
        if aw < limit:
            return text
    return COMFORT_STEPS[-1][1]


# ---------------------------------------------------------------------------
# components
# ---------------------------------------------------------------------------


def _interp(x: float, pts: list) -> float:
    """Piecewise-linear through ``pts`` [[x, y], ...], extended along its end segments."""
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    if len(xs) == 1:
        return ys[0]
    if x <= xs[0]:
        i = 0
    elif x >= xs[-1]:
        i = len(xs) - 2
    else:
        i = max(j for j in range(len(xs) - 1) if xs[j] <= x)
    return ys[i] + (ys[i + 1] - ys[i]) * (x - xs[i]) / (xs[i + 1] - xs[i])


RATE_BASES = {"auto": "otomatik", "frequency": "doğal frekans eğrisi", "rate": "yay katsayısı grafiği", "polytropic": "hacimden (politropik)"}


@dataclass
class AirSpring:
    """One rolling-lobe air spring as its supplier describes it."""

    name: str = "Körük"
    source: str = ""
    force_kn_by_bar: list = field(default_factory=lambda: [[1.0, 4.5], [9.0, 40.5]])
    """Load carried at the design height against gauge pressure."""
    rate_n_mm_by_bar: list = field(default_factory=list)
    """The supplier's spring-rate chart, if there is one."""
    frequency_hz_by_bar: list = field(default_factory=list)
    """The supplier's natural-frequency chart: the spring carrying its own
    load. It fixes the rate as k = (2 pi f)^2 F / g."""
    rate_basis: str = "auto"
    volume_dm3: float = 0.0
    """Air volume at the design height, piston volume included."""
    polytropic_index: float = 1.38
    max_pressure_bar: float = 8.0
    bump_travel_mm: float = 80.0
    rebound_travel_mm: float = 140.0
    design_height_mm: float = 0.0
    isobars: dict | None = None
    """Constant-pressure curves for the chart: {deflection_mm: [...], force_kn: {"5": [...]}}."""
    note: str = ""

    def force_n(self, p_bar: float) -> float:
        return _interp(p_bar, self.force_kn_by_bar) * 1e3

    def pressure_bar(self, force_n: float) -> float:
        inv = [[p[1], p[0]] for p in self.force_kn_by_bar]
        return _interp(force_n / 1e3, inv)

    def basis(self) -> str:
        if self.rate_basis != "auto":
            return self.rate_basis
        if self.frequency_hz_by_bar:
            return "frequency"
        if self.rate_n_mm_by_bar:
            return "rate"
        return "polytropic"

    def rates_n_per_m(self, p_bar: float) -> dict:
        """Every stiffness the data supports at this pressure, by basis."""
        out = {}
        f = self.force_n(p_bar)
        if self.frequency_hz_by_bar:
            hz = _interp(p_bar, self.frequency_hz_by_bar)
            out["frequency"] = (2 * math.pi * hz) ** 2 * f / G
        if self.rate_n_mm_by_bar:
            out["rate"] = _interp(p_bar, self.rate_n_mm_by_bar) * 1e3
        if self.volume_dm3 > 0 and p_bar > 0:
            ae = f / (p_bar * BAR)
            out["polytropic"] = self.polytropic_index * (p_bar * BAR + P_ATM) * ae * ae / (self.volume_dm3 * 1e-3)
        return out

    def rate_n_per_m(self, p_bar: float) -> float:
        r = self.rates_n_per_m(p_bar)
        b = self.basis()
        if b not in r:
            if not r:
                raise ValueError(f"{self.name}: yay katsayısı için veri yok (frekans eğrisi, yay grafiği ya da hacim gerekir)")
            b = next(iter(r))
        return max(r[b], 1e3)

    def to_dict(self) -> dict:
        d = {k: copy.deepcopy(getattr(self, k)) for k in self.__dataclass_fields__}
        if not d["isobars"]:
            d.pop("isobars")
        return d


@dataclass
class Damper:
    """A telescopic damper as a force-velocity table."""

    name: str = "Amortisör"
    source: str = ""
    speeds_mps: list = field(default_factory=lambda: [0.05, 0.13, 0.26, 0.39, 0.52])
    rebound_n: list = field(default_factory=lambda: [1000.0, 2500.0, 4500.0, 6000.0, 7000.0])
    """Force when the damper is pulled apart (the body rising off the axle)."""
    compression_n: list = field(default_factory=lambda: [350.0, 900.0, 1600.0, 2100.0, 2500.0])
    note: str = ""

    def __post_init__(self) -> None:
        n = len(self.speeds_mps)
        if n < 1 or len(self.rebound_n) != n or len(self.compression_n) != n:
            raise ValueError(f"{self.name}: hız, çekme ve basma listeleri aynı uzunlukta olmalı")
        if any(b <= a for a, b in zip(self.speeds_mps, self.speeds_mps[1:])) or self.speeds_mps[0] <= 0:
            raise ValueError(f"{self.name}: hızlar sıfırdan büyük ve artan olmalı")

    def table(self) -> tuple:
        """(velocity, force) arrays over both directions, compression positive,
        through the origin and extended along the last segments."""
        v = [0.0] + [float(x) for x in self.speeds_mps]
        c = [0.0] + [float(x) for x in self.compression_n]
        r = [0.0] + [float(x) for x in self.rebound_n]
        far = 6.0

        def ext(ys):
            slope = (ys[-1] - ys[-2]) / (v[-1] - v[-2])
            return ys[-1] + max(slope, 0.0) * (far - v[-1])
        xs = [-far] + [-x for x in reversed(v[1:])] + [0.0] + v[1:] + [far]
        ys = [-ext(r)] + [-y for y in reversed(r[1:])] + [0.0] + c[1:] + [ext(c)]
        return np.array(xs), np.array(ys)

    def force(self, v):
        """Force on the body, positive pushing it up, for a compression velocity v."""
        xs, ys = self.table()
        return np.interp(v, xs, ys)

    def equivalent_coefficient(self, v_amp: float) -> float:
        """The linear damper that dissipates the same energy per cycle of a
        sinusoidal stroke with velocity amplitude ``v_amp`` [Ns/m]."""
        th = (np.arange(256) + 0.5) / 256 * 2 * math.pi
        v = max(v_amp, 1e-4) * np.sin(th)
        return float(2.0 * np.mean(self.force(v) * v) / max(v_amp, 1e-4) ** 2)

    def to_dict(self) -> dict:
        return {k: copy.deepcopy(getattr(self, k)) for k in self.__dataclass_fields__}


@dataclass
class Axle:
    """One axle's suspension. Counts are per side."""

    spring: str = ""
    springs_per_side: int = 1
    spring_track_m: float = 1.2
    """Lateral distance between the left and right springs (for an
    independent suspension: the track, with the motion ratio below)."""
    spring_motion_ratio: float = 1.0
    damper: str = ""
    damper_tune: dict | None = None
    """Node forces that replace the reference curve: {rebound_n: [...], compression_n: [...]}."""
    dampers_per_side: int = 1
    damper_track_m: float = 1.2
    damper_motion_ratio: float = 1.0
    track_m: float = 2.05
    tyres_per_side: int = 1
    tyre_stiffness_n_per_m: float = 950e3
    tyre_damping_ns_per_m: float = 1500.0
    unsprung_mass_kg: float = 700.0
    unsprung_cg_height_m: float = 0.5
    roll_centre_height_m: float = 0.4
    arb_stiffness_nm_per_rad: float = 0.0
    anti_dive: float = 0.0
    """Share of the braking load transfer carried by the links (front:
    anti-dive, rear: anti-lift)."""
    anti_squat: float = 0.0
    """Share of the traction load transfer carried by the links of a driven axle."""
    driven: bool = False

    def to_dict(self) -> dict:
        d = {k: copy.deepcopy(getattr(self, k)) for k in self.__dataclass_fields__}
        if not d["damper_tune"]:
            d.pop("damper_tune")
        return d


AXLE_FIELDS = set(Axle.__dataclass_fields__)


@dataclass
class SuspensionModel:
    """A two-axle vehicle's chassis data for ride and roll."""

    name: str = "Süspansiyon"
    stem: str = ""
    description: str = ""
    vehicles: list = field(default_factory=list)
    """Vehicle files this suspension belongs to."""
    measured: list = field(default_factory=list)
    """Parameter paths that come from a drawing or a test; everything else is an assumption."""
    wheelbase_m: float = 6.9
    cog_x_m: float = 4.4
    cog_y_m: float = 0.0
    cog_z_m: float = 1.2
    passenger_x_m: float = 3.4
    passenger_z_m: float = 2.0
    luggage_x_m: float = 3.6
    luggage_z_m: float = 0.9
    pitch_gyration_m: float = 3.5
    roll_gyration_m: float = 1.0
    friction: float = 0.8
    understeer_deg_per_g: float = 2.0
    """Understeer gradient K of delta = L/R + K ay."""
    stop_stiffness_n_per_m: float = 2.0e6
    linearisation_speed_mps: float = 0.13
    """Damper stroke velocity at which the curves are replaced by one
    equivalent coefficient for the frequencies and damping ratios."""
    air_springs: dict = field(default_factory=dict)
    dampers: dict = field(default_factory=dict)
    front: Axle = field(default_factory=Axle)
    rear: Axle = field(default_factory=lambda: Axle(driven=True, tyres_per_side=2, springs_per_side=2, dampers_per_side=2))

    def __post_init__(self) -> None:
        for label, ax in (("ön", self.front), ("arka", self.rear)):
            if ax.spring not in self.air_springs:
                raise ValueError(f"{label} aks: körük '{ax.spring}' tanımlı değil ({', '.join(self.air_springs) or 'hiç körük yok'})")
            if ax.damper not in self.dampers:
                raise ValueError(f"{label} aks: amortisör '{ax.damper}' tanımlı değil ({', '.join(self.dampers) or 'hiç amortisör yok'})")
            if ax.track_m <= 0 or ax.spring_track_m <= 0 or ax.damper_track_m <= 0:
                raise ValueError(f"{label} aks: iz genişlikleri sıfırdan büyük olmalı")
        if not 0 < self.cog_x_m < self.wheelbase_m:
            raise ValueError("ağırlık merkezi iki aksın arasında olmalı")

    def axle_damper(self, ax: Axle) -> Damper:
        ref = self.dampers[ax.damper]
        t = ax.damper_tune or {}
        n = len(ref.speeds_mps)
        reb, comp = t.get("rebound_n"), t.get("compression_n")
        if not reb and not comp:
            return ref
        return Damper(name=ref.name + " (ayarlı)", source=ref.source, speeds_mps=list(ref.speeds_mps),
                      rebound_n=[float(x) for x in reb] if reb and len(reb) == n else list(ref.rebound_n),
                      compression_n=[float(x) for x in comp] if comp and len(comp) == n else list(ref.compression_n))

    def to_dict(self) -> dict:
        d = {}
        for k in self.__dataclass_fields__:
            v = getattr(self, k)
            if k in ("front", "rear"):
                d[k] = v.to_dict()
            elif k in ("air_springs", "dampers"):
                d[k] = {name: obj.to_dict() for name, obj in v.items()}
            else:
                d[k] = copy.deepcopy(v)
        return d

    @classmethod
    def from_dict(cls, d: dict, stem: str = "") -> "SuspensionModel":
        d = dict(d or {})
        d.pop("kind", None)
        unknown = set(d) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"süspansiyon: bilinmeyen alan {sorted(unknown)}")
        d["air_springs"] = {k: (v if isinstance(v, AirSpring) else AirSpring(**v)) for k, v in (d.get("air_springs") or {}).items()}
        d["dampers"] = {k: (v if isinstance(v, Damper) else Damper(**v)) for k, v in (d.get("dampers") or {}).items()}
        for k in ("front", "rear"):
            a = d.get(k) or {}
            if not isinstance(a, Axle):
                bad = set(a) - AXLE_FIELDS
                if bad:
                    raise ValueError(f"süspansiyon {k}: bilinmeyen alan {sorted(bad)}")
                a = Axle(**a)
            d[k] = a
        d["stem"] = stem or d.get("stem") or ""
        return cls(**d)


# What the interface shows and lets people change: path, label, unit, display
# scale, min, max, step (all in display units), group, hint.
PARAM_GROUPS = ["Ağırlık merkezi", "Geometri", "Körük yerleşimi", "Amortisör yerleşimi", "Devrilme barı, lastik ve direksiyon", "Kütle ve atalet"]
PARAMS = [
    ("cog_x_m", "X · ön akstan geriye", "m", 1, 1.5, 6.5, 0.01, 0, "Boş aracın ağırlık merkezinin ön aksa uzaklığı. Aks yüklerini bu belirler."),
    ("cog_y_m", "Y · orta eksenden sağa", "m", 1, -0.4, 0.4, 0.01, 0, "Sağa kaçık ağırlık merkezi sola dönüşte devrilme eşiğini düşürür."),
    ("cog_z_m", "Z · yerden yükseklik", "m", 1, 0.7, 2.0, 0.01, 0, "Boş aracın ağırlık merkezi yüksekliği. Yalpa ve devrilmeyi en çok bu etkiler."),
    ("passenger_z_m", "Yolcu ağırlık merkezi yüksekliği", "m", 1, 1.0, 3.0, 0.01, 0, "Oturan yolcunun ağırlık merkezi: zemin + koltuk + yaklaşık 0,2 m."),
    ("passenger_x_m", "Yolcu ağırlık merkezi X", "m", 1, 0.0, 8.0, 0.01, 0, "Koltukların ortalama konumu, ön akstan geriye."),
    ("luggage_z_m", "Bagaj ağırlık merkezi yüksekliği", "m", 1, 0.3, 2.0, 0.01, 0, ""),
    ("luggage_x_m", "Bagaj ağırlık merkezi X", "m", 1, 0.0, 8.0, 0.01, 0, ""),
    ("wheelbase_m", "Dingil mesafesi", "m", 1, 3.0, 9.0, 0.01, 1, ""),
    ("front.track_m", "İz genişliği · ön", "m", 1, 1.5, 2.6, 0.01, 1, ""),
    ("rear.track_m", "İz genişliği · arka", "m", 1, 1.5, 2.6, 0.01, 1, "İkiz lastikte iki lastiğin ortası."),
    ("front.roll_centre_height_m", "Yalpa merkezi yüksekliği · ön", "m", 1, 0.0, 1.2, 0.01, 1, "Gövdenin aksa göre döndüğü nokta. Bağımsız süspansiyonda alçak, sabit aksta yüksektir."),
    ("rear.roll_centre_height_m", "Yalpa merkezi yüksekliği · arka", "m", 1, 0.0, 1.2, 0.01, 1, ""),
    ("front.anti_dive", "Dalma önleme · ön", "%", 100, 0, 100, 1, 1, "Frenlemede yük aktarımının yaylar yerine kolların taşıdığı pay."),
    ("rear.anti_dive", "Kalkma önleme · arka (fren)", "%", 100, 0, 100, 1, 1, ""),
    ("rear.anti_squat", "Çökme önleme · arka (çekiş)", "%", 100, 0, 100, 1, 1, "Kalkışta arkanın çökmesini kolların karşılama payı."),
    ("front.springs_per_side", "Körük adedi · ön (her yanda)", "", 1, 1, 2, 1, 2, ""),
    ("front.spring_track_m", "Körükler arası mesafe · ön", "m", 1, 0.6, 2.4, 0.01, 2, "Sol ve sağ körük arası. Yalpa direngenliği bunun karesiyle artar."),
    ("front.spring_motion_ratio", "Hareket oranı · ön körük", "", 1, 0.4, 1.2, 0.01, 2, "Körük stroku / tekerlek stroku."),
    ("rear.springs_per_side", "Körük adedi · arka (her yanda)", "", 1, 1, 2, 1, 2, ""),
    ("rear.spring_track_m", "Körükler arası mesafe · arka", "m", 1, 0.6, 2.4, 0.01, 2, ""),
    ("rear.spring_motion_ratio", "Hareket oranı · arka körük", "", 1, 0.4, 1.2, 0.01, 2, ""),
    ("front.dampers_per_side", "Amortisör adedi · ön (her yanda)", "", 1, 1, 2, 1, 3, ""),
    ("front.damper_track_m", "Amortisörler arası mesafe · ön", "m", 1, 0.6, 2.4, 0.01, 3, ""),
    ("front.damper_motion_ratio", "Hareket oranı · ön amortisör", "", 1, 0.4, 1.2, 0.01, 3, "Amortisör stroku / tekerlek stroku (eğik bağlanınca 1'den küçük)."),
    ("rear.dampers_per_side", "Amortisör adedi · arka (her yanda)", "", 1, 1, 2, 1, 3, ""),
    ("rear.damper_track_m", "Amortisörler arası mesafe · arka", "m", 1, 0.6, 2.4, 0.01, 3, ""),
    ("rear.damper_motion_ratio", "Hareket oranı · arka amortisör", "", 1, 0.4, 1.2, 0.01, 3, ""),
    ("linearisation_speed_mps", "Frekans hesabında amortisör hızı", "m/s", 1, 0.02, 0.6, 0.01, 3, "Frekans ve sönüm oranı için eğri bu strok hızında tek katsayıya indirilir. Zaman simülasyonu eğrinin tamamını kullanır."),
    ("front.arb_stiffness_nm_per_rad", "Devrilme barı · ön", "kNm/rad", 1e-3, 0, 1500, 10, 4, "Viraj demiri yalpa direngenliği."),
    ("rear.arb_stiffness_nm_per_rad", "Devrilme barı · arka", "kNm/rad", 1e-3, 0, 1500, 10, 4, ""),
    ("front.tyre_stiffness_n_per_m", "Lastik düşey direngenliği · ön", "kN/m", 1e-3, 400, 1600, 10, 4, "Tek lastik."),
    ("rear.tyre_stiffness_n_per_m", "Lastik düşey direngenliği · arka", "kN/m", 1e-3, 400, 1600, 10, 4, "Tek lastik; ikizde iki katı alınır."),
    ("friction", "Yol tutuş katsayısı", "", 1, 0.2, 1.1, 0.05, 4, "Bu ivmeden sonra araç devrilmeden önce kayar."),
    ("understeer_deg_per_g", "Alttan dönme gradyanı", "°/g", 1, 0.0, 8.0, 0.1, 4, "Viraj denklemi δ = L/R + K·ay (Gillespie 6-16). Hız arttıkça aynı viraj için bu kadar fazla direksiyon gerekir. Serbest sürüş bunu kullanır."),
    ("front.unsprung_mass_kg", "Yaysız kütle · ön", "kg", 1, 200, 1500, 10, 5, "Aks, tekerlekler, frenler."),
    ("rear.unsprung_mass_kg", "Yaysız kütle · arka", "kg", 1, 300, 2500, 10, 5, ""),
    ("pitch_gyration_m", "Yunuslama atalet yarıçapı", "m", 1, 1.5, 5.0, 0.05, 5, "Gövde yunuslama ataleti = kütle × yarıçap²."),
    ("roll_gyration_m", "Yalpa atalet yarıçapı", "m", 1, 0.5, 1.6, 0.01, 5, ""),
]


def get_path(d: dict, path: str):
    for part in path.split("."):
        d = d[part]
    return d


def set_path(d: dict, path: str, value) -> None:
    parts = path.split(".")
    for part in parts[:-1]:
        d = d[part]
    d[parts[-1]] = value


def param_info(model: SuspensionModel) -> list:
    meas = set(model.measured)
    out = []
    for path, label, unit, scale, lo, hi, step, grp, tip in PARAMS:
        out.append({"path": path, "label": label, "unit": unit, "scale": scale, "min": lo, "max": hi, "step": step,
                    "group": PARAM_GROUPS[grp], "tip": tip, "integer": path.endswith("_per_side"),
                    "measured": path in meas})
    return out


# ---------------------------------------------------------------------------
# the vehicle at one load
# ---------------------------------------------------------------------------

DOF = ("z", "pitch", "roll", "fl", "fr", "rl", "rr")
CORNERS = ("fl", "fr", "rl", "rr")
CORNER_TR = {"fl": "ön sol", "fr": "ön sağ", "rl": "arka sol", "rr": "arka sağ"}


class State:
    """The model at one load: static forces, linear matrices, force elements."""

    def __init__(self, model: SuspensionModel, *, kerb_mass_kg: float, passengers: float = 0.0,
                 passenger_mass_kg: float = 68.0, luggage_kg: float = 0.0) -> None:
        m = self.model = model
        L = self.L = m.wheelbase_m
        mk, mp, ml = float(kerb_mass_kg), float(passengers) * float(passenger_mass_kg), float(luggage_kg)
        mt = self.mass = mk + mp + ml
        self.passengers, self.luggage_kg, self.kerb_mass_kg = float(passengers), ml, mk
        x = self.cog_x = (mk * m.cog_x_m + mp * m.passenger_x_m + ml * m.luggage_x_m) / mt
        y = self.cog_y = mk * m.cog_y_m / mt
        z = self.cog_z = (mk * m.cog_z_m + mp * m.passenger_z_m + ml * m.luggage_z_m) / mt
        axles = self.axles = (m.front, m.rear)
        mu = self.mu = np.array([m.front.unsprung_mass_kg, m.rear.unsprung_mass_kg])
        hu = np.array([m.front.unsprung_cg_height_m, m.rear.unsprung_cg_height_m])
        ms = self.ms = mt - mu.sum()
        if ms <= 0:
            raise ValueError("yaysız kütleler araç kütlesinden büyük")
        xs = self.xs = (mt * x - mu[1] * L) / ms
        zs = self.zs = (mt * z - float(mu @ hu)) / ms
        ys = self.ys = mt * y / ms
        if not 0 < xs < L:
            raise ValueError("yaylı kütlenin ağırlık merkezi aksların dışına düşüyor")
        self.xa = np.array([xs, xs - L])                       # axle position ahead of the sprung CG
        self.axle_load = mt * G * np.array([(L - x) / L, x / L])
        self.sprung_axle_load = ms * G * np.array([(L - xs) / L, xs / L])
        t = self.track = np.array([a.track_m for a in axles])
        s = np.array([a.spring_track_m for a in axles])
        dtr = np.array([a.damper_track_m for a in axles])
        hrc = self.hrc = np.array([a.roll_centre_height_m for a in axles])
        self.h_roll_axis = hrc[0] + (hrc[1] - hrc[0]) * xs / L
        h1 = self.h1 = zs - self.h_roll_axis

        # static loads per corner (left, right); y is positive to the right
        sgn = np.array([1.0, -1.0, 1.0, -1.0])               # lateral sign of the corner (left = +)
        ax_of = np.array([0, 0, 1, 1])
        self.F0s = np.array([self.sprung_axle_load[a] * (0.5 - sg * ys / s[a]) for a, sg in zip(ax_of, sgn)])
        self.F0t = np.array([self.axle_load[a] * (0.5 - sg * y / t[a]) for a, sg in zip(ax_of, sgn)])
        if (self.F0s <= 0).any() or (self.F0t <= 0).any():
            raise ValueError("ağırlık merkezi bir yana o kadar kaçık ki bir tekerlek yük taşımıyor")

        # air springs at their static pressure
        self.springs = [m.air_springs[a.spring] for a in axles]
        self.damper_objs = [m.axle_damper(a) for a in axles]
        nsp = np.array([axles[a].springs_per_side for a in ax_of], dtype=float)
        mr = np.array([axles[a].spring_motion_ratio for a in ax_of])
        self.MR, self.nMR = mr, nsp * mr
        f_spring = self.F0sp = self.F0s / (nsp * mr)
        self.p_bar = np.array([self.springs[a].pressure_bar(f) for a, f in zip(ax_of, f_spring)])
        p_use = np.maximum(self.p_bar, 0.3)
        self.ks = np.array([self.springs[a].rate_n_per_m(p) for a, p in zip(ax_of, p_use)])
        self.kw = nsp * self.ks * mr * mr                   # wheel rate per side
        self.Ae = f_spring / (p_use * BAR)
        self.pabs0 = p_use * BAR + P_ATM
        self.npoly = np.array([self.springs[a].polytropic_index for a in ax_of])
        self.Lv = self.npoly * self.pabs0 * self.Ae / self.ks  # air column that gives the supplier's rate
        self.bump = np.array([self.springs[a].bump_travel_mm for a in ax_of]) * 1e-3
        self.reb = np.array([self.springs[a].rebound_travel_mm for a in ax_of]) * 1e-3
        self.kstop = m.stop_stiffness_n_per_m
        nty = np.array([axles[a].tyres_per_side for a in ax_of], dtype=float)
        self.kt = nty * np.array([axles[a].tyre_stiffness_n_per_m for a in ax_of])
        self.ct = nty * np.array([axles[a].tyre_damping_ns_per_m for a in ax_of])
        self.ride = self.kw * self.kt / (self.kw + self.kt)

        # dampers
        ndm = np.array([axles[a].dampers_per_side for a in ax_of], dtype=float)
        mrd = self.MRd = np.array([axles[a].damper_motion_ratio for a in ax_of])
        self.nMRd = ndm * mrd
        self._dtab = [d.table() for d in self.damper_objs]
        self.c_eq = np.array([self.damper_objs[a].equivalent_coefficient(m.linearisation_speed_mps) for a in ax_of])
        self.cw = ndm * mrd * mrd * self.c_eq                # wheel damping per side

        # deflection vectors of the force elements
        def body(xa_, ylat):
            return np.array([1.0, -xa_, ylat, 0, 0, 0, 0])

        def axle_pt(a, ylat):
            v = np.zeros(7)
            v[3 + 2 * a] = 0.5 + ylat / t[a]
            v[4 + 2 * a] = 0.5 - ylat / t[a]
            return v
        self.Bs = np.array([axle_pt(a, sg * s[a] / 2) - body(self.xa[a], sg * s[a] / 2) for a, sg in zip(ax_of, sgn)])
        self.Bd = np.array([axle_pt(a, sg * dtr[a] / 2) - body(self.xa[a], sg * dtr[a] / 2) for a, sg in zip(ax_of, sgn)])
        self.Brho = np.zeros((2, 7))
        self.Barb = np.zeros((2, 7))
        for a in (0, 1):
            self.Brho[a, 3 + 2 * a], self.Brho[a, 4 + 2 * a] = 1.0 / t[a], -1.0 / t[a]
            self.Barb[a] = -self.Brho[a]
            self.Barb[a, 2] = 1.0
        self.karb = np.array([a.arb_stiffness_nm_per_rad for a in axles])
        self.grav_axle = self.sprung_axle_load * hrc + mu * G * hu

        # mass, stiffness, damping
        self.Iyy = ms * m.pitch_gyration_m ** 2
        self.Ixx = ms * (m.roll_gyration_m ** 2 + h1 * h1)
        self.Mdiag = np.array([ms, self.Iyy, self.Ixx, mu[0] / 2, mu[0] / 2, mu[1] / 2, mu[1] / 2])
        self.Minv = 1.0 / self.Mdiag
        Klin = np.zeros((7, 7))
        for a in (0, 1):
            Klin += self.karb[a] * np.outer(self.Barb[a], self.Barb[a])
            Klin -= self.grav_axle[a] * np.outer(self.Brho[a], self.Brho[a])
        Klin[2, 2] -= ms * G * h1
        self.Klin = Klin                                      # anti-roll bars and gravity
        K = Klin.copy()
        C = np.zeros((7, 7))
        for i in range(4):
            K += self.kw[i] * np.outer(self.Bs[i], self.Bs[i])
            C += self.cw[i] * np.outer(self.Bd[i], self.Bd[i])
            K[3 + i, 3 + i] += self.kt[i]
            C[3 + i, 3 + i] += self.ct[i]
        self.K, self.C = K, C

        # unit generalised forces of the accelerations
        self.Qy = np.zeros(7)
        self.Qy[2] = ms * h1
        for a in (0, 1):
            df = (self.sprung_axle_load[a] / G * hrc[a] + mu[a] * hu[a]) / t[a]
            self.Qy[3 + 2 * a] += df
            self.Qy[4 + 2 * a] -= df
        dw = mt * z / L                                       # load transfer per unit acceleration

        def unit(pitch_moment, vf, vr):
            """Pitch moment of the load transfer plus the vertical link forces
            (body up = positive) the anti-geometry puts between body and axle."""
            q = np.zeros(7)
            q[1] = pitch_moment
            for a, v in ((0, vf), (1, vr)):
                q[0] += v
                q[1] += -self.xa[a] * v
                q[3 + 2 * a] -= v / 2
                q[4 + 2 * a] -= v / 2
            return q
        f, r = m.front, m.rear
        # per unit of traction: the nose rises; a driven axle's links hold their end
        self.Qx_acc = unit(-mt * z, -(f.anti_squat if f.driven else 0.0) * dw, (r.anti_squat if r.driven else 0.0) * dw)
        # per unit of braking: the nose dives; anti-dive lifts the front, anti-lift holds the rear down
        self.Qx_brk = unit(+mt * z, f.anti_dive * dw, -r.anti_dive * dw)

    # ---- forces ----------------------------------------------------------
    def Q(self, ax: float, ay: float) -> np.ndarray:
        return (self.Qx_acc * ax if ax >= 0 else self.Qx_brk * (-ax)) + self.Qy * ay

    def spring_force(self, delta: np.ndarray) -> np.ndarray:
        """Change of the wheel-level spring force for a wheel compression ``delta`` [m]."""
        ds = delta * self.MR
        r = 1.0 - np.minimum(ds, 0.85 * self.Lv) / self.Lv
        f = np.maximum(self.Ae * (self.pabs0 * r ** (-self.npoly) - P_ATM), 0.0)
        f = f + self.kstop * (np.maximum(ds - self.bump, 0.0) + np.minimum(ds + self.reb, 0.0))
        return self.nMR * (f - self.F0sp)

    def damper_force(self, v: np.ndarray) -> np.ndarray:
        """Wheel-level damper force for a wheel compression velocity ``v`` [m/s]."""
        vd = v * self.MRd
        out = np.empty(4)
        out[:2] = np.interp(vd[:2], *self._dtab[0])
        out[2:] = np.interp(vd[2:], *self._dtab[1])
        return self.nMRd * out

    def tyre_force(self, zu: np.ndarray, zud: np.ndarray, zr, zrd) -> np.ndarray:
        """Total tyre force per corner; a tyre cannot pull."""
        return np.maximum(self.F0t + self.kt * (zr - zu) + self.ct * (zrd - zud), 0.0)

    def accel(self, q, qd, Qext, zr, zrd) -> np.ndarray:
        f = Qext - self.Klin @ q
        f -= self.Bs.T @ self.spring_force(self.Bs @ q)
        f -= self.Bd.T @ self.damper_force(self.Bd @ qd)
        f[3:] += self.tyre_force(q[3:], qd[3:], zr, zrd) - self.F0t
        return f * self.Minv

    # ---- statics ---------------------------------------------------------
    def static(self, ax: float = 0.0, ay: float = 0.0, q0=None, linear: bool = False) -> np.ndarray:
        """Equilibrium under steady accelerations."""
        if linear:
            return np.linalg.solve(self.K, self.Q(ax, ay))
        return self.static_ok(ax, ay, q0)[0]

    def static_ok(self, ax: float = 0.0, ay: float = 0.0, q0=None) -> tuple:
        """Equilibrium of the non-linear model and whether one was found.
        Past the rollover limit there is none, and the search says so."""
        Qe = self.Q(ax, ay)
        q = np.linalg.solve(self.K, Qe) if q0 is None else np.array(q0, dtype=float)
        zero = np.zeros(4)
        z7 = np.zeros(7)

        def res(qq):
            return self.accel(qq, z7, Qe, zero, zero) * self.Mdiag
        ok = False
        for _ in range(60):
            r0 = res(q)
            if not np.isfinite(r0).all():
                break
            if np.abs(r0).max() < 0.5:
                ok = True
                break
            J = np.empty((7, 7))
            for j in range(7):
                h = 1e-6
                e = np.zeros(7)
                e[j] = h
                J[:, j] = (res(q + e) - res(q - e)) / (2 * h)
            try:
                step = np.linalg.solve(J, -r0)
            except np.linalg.LinAlgError:
                break
            lim = 0.03 / max(np.abs(step).max(), 1e-12)
            q = q + step * min(1.0, lim)
        return q, ok

    def wheel_loads(self, q, zr=0.0) -> np.ndarray:
        return self.tyre_force(q[3:], 0.0, zr, 0.0)

    # ---- summaries -------------------------------------------------------
    def summary(self) -> dict:
        m = self.model
        ms_side = self.F0s / G
        fn = np.sqrt(self.ride / ms_side) / (2 * math.pi)
        zeta = self.cw / (2.0 * np.sqrt(self.kw * ms_side))
        hop = np.sqrt((self.kw + self.kt) / (np.repeat(self.mu, 2) / 2)) / (2 * math.pi)
        kf, kr = float(self.ride[:2].sum()), float(self.ride[2:].sum())
        bp = bounce_pitch(kf, kr, self.xs, self.L - self.xs, self.ms, m.pitch_gyration_m ** 2)
        s = np.array([a.spring_track_m for a in self.axles])
        k_spr = np.array([(self.kw[0] + self.kw[1]) * (s[0] / 2) ** 2, (self.kw[2] + self.kw[3]) * (s[1] / 2) ** 2])
        k_tyr = np.array([(self.kt[0] + self.kt[1]) * (self.track[0] / 2) ** 2, (self.kt[2] + self.kt[3]) * (self.track[1] / 2) ** 2])
        k_sus = k_spr + self.karb
        k_axle = k_sus * k_tyr / (k_sus + k_tyr)
        wh1 = self.ms * G * self.h1
        rr_book = roll_rate(wh1, float(k_sus.sum())) if k_sus.sum() > wh1 else math.inf
        d = np.array([a.damper_track_m for a in self.axles])
        c_roll = (self.cw[0] + self.cw[1]) * (d[0] / 2) ** 2 + (self.cw[2] + self.cw[3]) * (d[1] / 2) ** 2
        k_roll_net = float(k_axle.sum()) - wh1
        w_roll = math.sqrt(max(k_roll_net, 0.0) / self.Ixx)
        # steady gradients from the linear seven-degree-of-freedom statics
        qy = self.static(ay=G, linear=True)
        qb = self.static(ax=-G, linear=True)
        qa = self.static(ax=G, linear=True)
        springs = []
        for a, ax in enumerate(self.axles):
            sp = self.springs[a]
            i = 2 * a
            p = float(self.p_bar[i:i + 2].mean())
            rates = sp.rates_n_per_m(max(p, 0.3))
            springs.append({
                "name": sp.name, "pressure_bar": [float(v) for v in self.p_bar[i:i + 2]], "max_pressure_bar": sp.max_pressure_bar,
                "force_n": float(self.F0sp[i:i + 2].mean()), "rate_n_per_m": float(self.ks[i:i + 2].mean()), "basis": sp.basis(),
                "rates_by_basis": {k: float(v) for k, v in rates.items()},
                "bump_travel_mm": sp.bump_travel_mm, "rebound_travel_mm": sp.rebound_travel_mm,
                "over_pressure": bool(p > sp.max_pressure_bar + 1e-9), "under_pressure": bool(p < 1.0),
            })
        return {
            "mass_kg": self.mass, "sprung_mass_kg": self.ms, "passengers": self.passengers, "luggage_kg": self.luggage_kg,
            "cog": {"x_m": self.cog_x, "y_m": self.cog_y, "z_m": self.cog_z},
            "sprung_cog": {"x_m": self.xs, "y_m": self.ys, "z_m": self.zs},
            "axle_load_kg": [float(v) / G for v in self.axle_load],
            "axle_share": [float(v) / (self.mass * G) for v in self.axle_load],
            "wheel_load_kg": [float(v) / G for v in self.F0t],
            "springs": springs,
            "wheel_rate_n_per_m": [float(v) for v in self.kw], "ride_rate_n_per_m": [float(v) for v in self.ride],
            "damping_ns_per_m": [float(v) for v in self.cw],
            "ride_hz": [float(fn[:2].mean()), float(fn[2:].mean())],
            "damping_ratio": [float(zeta[:2].mean()), float(zeta[2:].mean())],
            "damped_hz": [float(fn[:2].mean() * math.sqrt(max(1 - zeta[:2].mean() ** 2, 0))), float(fn[2:].mean() * math.sqrt(max(1 - zeta[2:].mean() ** 2, 0)))],
            "wheel_hop_hz": [float(hop[:2].mean()), float(hop[2:].mean())],
            "bounce_pitch": bp,
            "roll": {
                "stiffness_springs_nm_per_rad": [float(v) for v in k_spr], "stiffness_arb_nm_per_rad": [float(v) for v in self.karb],
                "stiffness_tyres_nm_per_rad": [float(v) for v in k_tyr], "stiffness_axle_nm_per_rad": [float(v) for v in k_axle],
                "front_share": float(k_axle[0] / k_axle.sum()),
                "h_roll_axis_m": float(self.h_roll_axis), "h1_m": float(self.h1),
                "gradient_deg_per_g": float(math.degrees(qy[2])), "gradient_book_deg_per_g": float(math.degrees(rr_book)),
                "hz": w_roll / (2 * math.pi), "damping_ratio": float(c_roll / (2 * math.sqrt(max(k_roll_net, 1.0) * self.Ixx))),
            },
            "pitch": {"dive_deg_per_g": float(math.degrees(qb[1])), "squat_deg_per_g": float(-math.degrees(qa[1])),
                      "dive_book_deg_per_g": float(math.degrees(pitch_angle(self.mass * G, self.cog_z, self.L, -1.0, kf, kr, m.front.anti_dive, m.rear.anti_dive))),
                      "inertia_kgm2": float(self.Iyy)},
            "inertia": {"pitch_kgm2": float(self.Iyy), "roll_kgm2": float(self.Ixx)},
        }

    def modes(self) -> list:
        """Damped modes of the linear model, named after the motion that carries most of the energy."""
        n = 7
        A = np.zeros((2 * n, 2 * n))
        A[:n, n:] = np.eye(n)
        A[n:, :n] = -self.K * self.Minv[:, None]
        A[n:, n:] = -self.C * self.Minv[:, None]
        val, vec = np.linalg.eig(A)
        names = {0: "zıplama", 1: "yunuslama", 2: "yalpa", 3: "ön tekerlek zıplaması", 4: "ön tekerlek zıplaması",
                 5: "arka tekerlek zıplaması", 6: "arka tekerlek zıplaması"}
        out = []
        for i in np.argsort(np.abs(val)):
            lam = val[i]
            if lam.imag < -1e-9:
                continue
            shape = vec[n:, i]                                    # velocities: kinetic energy share
            energy = self.Mdiag * np.abs(shape) ** 2
            k = int(np.argmax(energy))
            wn = abs(lam)
            if wn < 1e-6:
                continue
            zeta = -lam.real / wn
            out.append({"name": names[k], "hz": wn / (2 * math.pi), "damped_hz": abs(lam.imag) / (2 * math.pi),
                        "damping_ratio": float(zeta), "settle_s": float(4.0 / (zeta * wn)) if zeta > 1e-6 else None,
                        "real": bool(abs(lam.imag) < 1e-9), "energy": [float(e) for e in energy / energy.sum()]})
        return out


# ---------------------------------------------------------------------------
# time simulation
# ---------------------------------------------------------------------------

MANOEUVRES = {
    "fren": {"label": "Fren", "tip": "Sabit yavaşlamayla duruşa kadar fren: dalma açısı ve durunca geri yaylanma.",
             "params": [("speed_kmh", "Hız", "km/h", 60, 10, 130, 5), ("decel_mps2", "Yavaşlama", "m/s²", 4.0, 0.5, 8.0, 0.1), ("rise_s", "Fren kuruluş süresi", "s", 0.3, 0.05, 2.0, 0.05)]},
    "kalkis": {"label": "Kalkış", "tip": "Sabit ivmeyle hızlanma, sonra gazı bırakma: çökme açısı.",
               "params": [("accel_mps2", "İvme", "m/s²", 1.2, 0.2, 3.0, 0.1), ("hold_s", "Süre", "s", 4.0, 1.0, 15.0, 0.5), ("rise_s", "Kuruluş süresi", "s", 0.4, 0.05, 2.0, 0.05)]},
    "birakma": {"label": "Bırakma testi", "tip": "Gövde bastırılıp bırakılır: serbest salınımdan frekans ve sönüm süresi okunur.",
                "params": [("mode", "Hareket", "", "ziplama", None, None, None), ("amount", "Başlangıç sapması", "mm | °", 30.0, 1.0, 80.0, 1.0)],
                "choices": {"mode": [["ziplama", "Zıplama"], ["yunuslama", "Yunuslama"], ["yalpa", "Yalpa"]]}},
    "kasis": {"label": "Kasis", "tip": "Önce ön, sonra arka aks kasisten geçer.",
              "params": [("speed_kmh", "Hız", "km/h", 20, 5, 90, 5), ("height_mm", "Yükseklik", "mm", 80, 10, 150, 5), ("length_m", "Uzunluk", "m", 3.0, 0.3, 6.0, 0.1),
                         ("side", "Taraf", "", "iki", None, None, None)],
              "choices": {"side": [["iki", "İki taraf"], ["sol", "Yalnız sol"]]}},
    "direksiyon": {"label": "Ani direksiyon", "tip": "Düz giderken viraja ani giriş: yanal ivme kısa sürede kurulur.",
                   "params": [("speed_kmh", "Hız", "km/h", 70, 10, 130, 5), ("radius_m", "Viraj yarıçapı", "m", 100, 15, 800, 5), ("rise_s", "İvme kuruluş süresi", "s", 0.5, 0.1, 3.0, 0.1)]},
    "serit": {"label": "Şerit değiştirme", "tip": "Engelden kaçış: sola çık, doğrult. Yanal ivme bir tam sinüs.",
              "params": [("speed_kmh", "Hız", "km/h", 80, 20, 130, 5), ("offset_m", "Yanal kaçış", "m", 3.5, 1.0, 5.0, 0.1), ("length_m", "Manevra boyu", "m", 60, 20, 200, 5)]},
    "yol": {"label": "Bozuk yol", "tip": "ISO 8608 sınıfında rastgele yol: düşey ivme ve konfor.",
            "params": [("speed_kmh", "Hız", "km/h", 80, 10, 130, 5), ("road_class", "Yol sınıfı", "", "C", None, None, None), ("duration_s", "Süre", "s", 20, 5, 60, 5)],
            "choices": {"road_class": [[k, v] for k, v in ISO8608_LABEL.items()]}},
}


def manoeuvre_defaults(kind: str) -> dict:
    return {p[0]: p[3] for p in MANOEUVRES[kind]["params"]}


def _ramp(t, t0, rise):
    """0 before t0, 1 after t0 + rise, smooth in between."""
    u = np.clip((t - t0) / max(rise, 1e-6), 0.0, 1.0)
    return u * u * (3.0 - 2.0 * u)


def road_profile(road_class: str, speed_mps: float, t: np.ndarray, seed: int = 7) -> np.ndarray:
    """A random road of an ISO 8608 class as the height under one wheel over time [m]."""
    f = np.geomspace(0.25, 30.0, 220)
    df = np.gradient(f)
    amp = np.sqrt(2.0 * road_psd(road_class, speed_mps, f) * df)
    ph = np.random.default_rng(seed).uniform(0, 2 * math.pi, f.size)
    return (amp[None, :] * np.sin(2 * math.pi * f[None, :] * t[:, None] + ph[None, :])).sum(axis=1)


def manoeuvre_inputs(st: State, kind: str, params: dict | None = None, dt: float = 0.004) -> dict:
    """Acceleration and road histories of a manoeuvre on a half-step grid."""
    if kind not in MANOEUVRES:
        raise ValueError(f"bilinmeyen manevra: {kind}")
    p = {**manoeuvre_defaults(kind), **{k: v for k, v in (params or {}).items() if v is not None}}
    choices = MANOEUVRES[kind].get("choices", {})
    for name, _l, _u, dflt, lo, hi, _s in MANOEUVRES[kind]["params"]:
        if lo is not None:
            p[name] = float(min(max(float(p[name]), lo), hi))
        elif name in choices and p[name] not in [c[0] for c in choices[name]]:
            p[name] = dflt
    t0 = 0.5
    q0 = np.zeros(7)
    marks = {}
    if kind == "fren":
        v0 = p["speed_kmh"] / 3.6
        t_stop = t0 + v0 / p["decel_mps2"] + p["rise_s"] / 2
        T = t_stop + 4.0
    elif kind == "kalkis":
        t_stop = t0 + p["hold_s"]
        T = t_stop + 4.0
    elif kind == "birakma":
        T = 6.0
    elif kind == "kasis":
        v = p["speed_kmh"] / 3.6
        T = t0 + (p["length_m"] + st.L) / v + 5.0
    elif kind == "direksiyon":
        T = t0 + p["rise_s"] + 5.0
    elif kind == "serit":
        v = p["speed_kmh"] / 3.6
        T = t0 + p["length_m"] / v + 4.0
    else:
        T = float(p["duration_s"])
    T = min(T, 90.0)
    n = int(math.ceil(T / dt))
    th = np.arange(2 * n + 1) * (dt / 2)
    ax = np.zeros_like(th)
    ay = np.zeros_like(th)
    zr = np.zeros((th.size, 4))
    speed = np.zeros_like(th)
    if kind == "fren":
        hold = 1.0 - _ramp(th, t_stop, 0.08)
        ax = -p["decel_mps2"] * _ramp(th, t0, p["rise_s"]) * hold
        speed = np.maximum(p["speed_kmh"] / 3.6 + np.cumsum(ax) * (dt / 2), 0.0)
        marks = {"start": t0, "steady": (t0 + p["rise_s"] + 0.6 * (t_stop - t0 - p["rise_s"]), t_stop), "release": t_stop}
    elif kind == "kalkis":
        ax = p["accel_mps2"] * _ramp(th, t0, p["rise_s"]) * (1.0 - _ramp(th, t_stop, 0.3))
        speed = np.cumsum(ax) * (dt / 2)
        marks = {"start": t0, "steady": (t0 + p["rise_s"] + 0.6 * (t_stop - t0 - p["rise_s"]), t_stop), "release": t_stop}
    elif kind == "birakma":
        amt = p["amount"]
        if p["mode"] == "ziplama":
            q0[0] = -amt * 1e-3
        elif p["mode"] == "yunuslama":
            q0[1] = math.radians(min(amt, 5.0))
        else:
            q0[2] = math.radians(min(amt, 8.0))
        # the wheels start where the tyres are in balance with the displaced body
        Kuu, Kub = st.K[3:, 3:], st.K[3:, :3]
        q0[3:] = np.linalg.solve(Kuu, -Kub @ q0[:3])
        marks = {"release": 0.0}
    elif kind == "kasis":
        v = p["speed_kmh"] / 3.6
        h, ln = p["height_mm"] * 1e-3, p["length_m"]

        def bump(tt, delay):
            xpos = v * (tt - t0 - delay)
            return np.where((xpos > 0) & (xpos < ln), h / 2 * (1 - np.cos(2 * math.pi * xpos / ln)), 0.0)
        zf, zrr = bump(th, 0.0), bump(th, st.L / v)
        zr[:, 0], zr[:, 2] = zf, zrr
        if p["side"] == "iki":
            zr[:, 1], zr[:, 3] = zf, zrr
        speed[:] = v
        marks = {"start": t0, "release": t0 + (ln + st.L) / v}
    elif kind == "direksiyon":
        v = p["speed_kmh"] / 3.6
        ay = v * v / p["radius_m"] * _ramp(th, t0, p["rise_s"])
        speed[:] = v
        marks = {"start": t0, "steady": (T - 1.0, T), "release": t0 + p["rise_s"]}
    elif kind == "serit":
        v = p["speed_kmh"] / 3.6
        Tm = p["length_m"] / v
        amp = 2 * math.pi * p["offset_m"] / (Tm * Tm)
        on = (th >= t0) & (th <= t0 + Tm)
        ay = np.where(on, amp * np.sin(2 * math.pi * (th - t0) / Tm), 0.0)
        speed[:] = v
        marks = {"start": t0, "release": t0 + Tm}
    else:
        v = p["speed_kmh"] / 3.6
        fade = _ramp(th, 0.0, 1.0)
        zf = road_profile(p["road_class"], v, th) * fade
        zb = np.interp(th - st.L / v, th, zf, left=0.0)
        zr[:, 0] = zr[:, 1] = zf
        zr[:, 2] = zr[:, 3] = zb
        speed[:] = v
        marks = {"start": 1.0}
    zrd = np.gradient(zr, dt / 2, axis=0)
    return {"kind": kind, "params": p, "dt": dt, "n": n, "t_half": th, "ax": ax, "ay": ay, "zr": zr, "zrd": zrd,
            "speed": speed, "q0": q0, "marks": marks, "duration_s": n * dt}


def simulate(st: State, inp: dict, stop_on_lift: bool = False) -> dict:
    """Integrate the seven degrees of freedom through a manoeuvre (classical Runge-Kutta).

    Past the moment both inner wheels are off the ground the model no longer
    describes the vehicle; the run is cut short there when ``stop_on_lift`` is
    set, and in any case once the body has rolled half a radian."""
    dt, n = inp["dt"], inp["n"]
    ax, ay, zr, zrd = inp["ax"], inp["ay"], inp["zr"], inp["zrd"]
    q = np.array(inp["q0"], dtype=float)
    qd = np.zeros(7)
    Q = np.empty((n + 1, 7))
    Qd = np.empty((n + 1, 7))
    Q[0], Qd[0] = q, qd
    acc = st.accel
    Qf = st.Q
    for i in range(n):
        j = 2 * i
        e0, e1, e2 = Qf(ax[j], ay[j]), Qf(ax[j + 1], ay[j + 1]), Qf(ax[j + 2], ay[j + 2])
        k1 = acc(q, qd, e0, zr[j], zrd[j])
        k2 = acc(q + 0.5 * dt * qd, qd + 0.5 * dt * k1, e1, zr[j + 1], zrd[j + 1])
        k3 = acc(q + 0.5 * dt * qd + 0.25 * dt * dt * k1, qd + 0.5 * dt * k2, e1, zr[j + 1], zrd[j + 1])
        k4 = acc(q + dt * qd + 0.5 * dt * dt * k2, qd + dt * k3, e2, zr[j + 2], zrd[j + 2])
        q = q + dt * qd + dt * dt / 6.0 * (k1 + k2 + k3)
        qd = qd + dt / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)
        Q[i + 1], Qd[i + 1] = q, qd
        if stop_on_lift:
            fz = st.tyre_force(q[3:], qd[3:], zr[j + 2], zrd[j + 2])
            if (fz[0] <= 1.0 and fz[2] <= 1.0) or (fz[1] <= 1.0 and fz[3] <= 1.0):
                Q[i + 1:], Qd[i + 1:] = q, qd
                break
        if not np.isfinite(q).all() or abs(q[2]) > 0.5:
            Q[i + 1:], Qd[i + 1:] = q, 0.0
            break
    t = np.arange(n + 1) * dt
    sl = slice(0, None, 2)
    zr1, zrd1 = zr[sl], zrd[sl]
    Fz = np.maximum(st.F0t + st.kt * (zr1 - Q[:, 3:]) + st.ct * (zrd1 - Qd[:, 3:]), 0.0)
    tot = Fz.sum(axis=1)
    ltr = ((Fz[:, 1] + Fz[:, 3]) - (Fz[:, 0] + Fz[:, 2])) / np.maximum(tot, 1.0)
    travel = Q @ st.Bs.T
    dvel = (Qd @ st.Bd.T) * st.MRd
    Qdd = np.gradient(Qd, dt, axis=0)
    acc_at = {"front": Qdd[:, 0] - st.xa[0] * Qdd[:, 1], "cog": Qdd[:, 0], "rear": Qdd[:, 0] - st.xa[1] * Qdd[:, 1]}
    lift_l = (Fz[:, 0] <= 1.0) & (Fz[:, 2] <= 1.0)
    lift_r = (Fz[:, 1] <= 1.0) & (Fz[:, 3] <= 1.0)
    return {"t": t, "q": Q, "qd": Qd, "Fz": Fz, "ltr": ltr, "travel": travel, "damper_v": dvel, "acc": acc_at,
            "ax": ax[sl], "ay": ay[sl], "zr": zr1, "speed": inp["speed"][sl], "lift": lift_l | lift_r,
            "pitch_deg": np.degrees(Q[:, 1]), "roll_deg": np.degrees(Q[:, 2]), "heave_mm": Q[:, 0] * 1e3,
            "marks": inp["marks"], "kind": inp["kind"], "params": inp["params"]}


def transient_metrics(t: np.ndarray, y: np.ndarray, t_from: float, final: float | None = None, band: float = 0.05) -> dict:
    """How a signal settles after ``t_from``: final value, largest excursion,
    time to stay within ``band`` of that excursion, and the oscillation
    frequency read from its zero crossings."""
    k = int(np.searchsorted(t, t_from))
    if k >= t.size - 3:
        return {}
    tt, yy = t[k:], y[k:]
    yf = float(np.mean(yy[-max(3, yy.size // 12):])) if final is None else float(final)
    dev = yy - yf
    peak = float(np.max(np.abs(dev)))
    out = {"final": yf, "peak_dev": peak, "settle_s": None, "hz": None, "overshoot": None}
    if peak < 1e-9:
        out["settle_s"] = 0.0
        return out
    outside = np.nonzero(np.abs(dev) > band * peak)[0]
    out["settle_s"] = float(tt[min(outside[-1] + 1, tt.size - 1)] - t_from) if outside.size else 0.0
    if outside.size and outside[-1] >= tt.size - 2:
        out["settle_s"] = None                                   # still moving at the end of the window
    sgn = np.sign(dev)
    sgn[np.abs(dev) < 0.02 * peak] = 0
    nz = np.nonzero(sgn)[0]
    cross = [0.5 * (tt[a] + tt[b]) for a, b in zip(nz[:-1], nz[1:]) if sgn[a] != sgn[b]]
    if len(cross) >= 2:
        out["hz"] = float((len(cross) - 1) / (2.0 * (cross[-1] - cross[0])))
    y0 = float(y[k] - yf)
    if abs(y0) > 1e-9 and cross:
        k2 = int(np.searchsorted(tt, cross[0]))
        out["overshoot"] = float(np.max(np.abs(dev[k2:])) / abs(y0))
    return out


def simulation_kpis(st: State, res: dict) -> dict:
    t, kind, marks = res["t"], res["kind"], res["marks"]
    pitch, roll = res["pitch_deg"], res["roll_deg"]
    out = {"duration_s": float(t[-1]), "peak_dive_deg": float(pitch.max()), "peak_squat_deg": float(-pitch.min()),
           "peak_roll_deg": float(np.abs(roll).max()), "ltr_max": float(np.abs(res["ltr"]).max()),
           "min_wheel_load_kg": float(res["Fz"].min() / G), "wheel_lift": bool(res["lift"].any()),
           "wheel_lift_s": float(t[np.argmax(res["lift"])]) if res["lift"].any() else None}
    trav = res["travel"] * st.MR[None, :]
    out["max_compression_mm"] = [float(trav[:, :2].max() * 1e3), float(trav[:, 2:].max() * 1e3)]
    out["max_rebound_mm"] = [float(-trav[:, :2].min() * 1e3), float(-trav[:, 2:].min() * 1e3)]
    out["bump_stop"] = [bool(trav[:, :2].max() >= st.bump[0]), bool(trav[:, 2:].max() >= st.bump[2])]
    out["rebound_stop"] = [bool(-trav[:, :2].min() >= st.reb[0]), bool(-trav[:, 2:].min() >= st.reb[2])]
    out["peak_damper_speed_mps"] = [float(np.abs(res["damper_v"][:, :2]).max()), float(np.abs(res["damper_v"][:, 2:]).max())]
    acc = res["acc"]
    out["acc_rms_mps2"] = {k: float(np.sqrt(np.mean(v ** 2))) for k, v in acc.items()}
    out["acc_peak_mps2"] = {k: float(np.abs(v).max()) for k, v in acc.items()}
    main = {"fren": pitch, "kalkis": pitch, "kasis": pitch, "direksiyon": roll, "serit": roll, "yol": res["heave_mm"]}.get(kind)
    if kind == "birakma":
        main = {"ziplama": res["heave_mm"], "yunuslama": pitch, "yalpa": roll}[res["params"]["mode"]]
    out["main"] = {"fren": "pitch", "kalkis": "pitch", "kasis": "pitch", "direksiyon": "roll", "serit": "roll", "yol": "heave"}.get(kind, res["params"].get("mode"))
    if "steady" in marks:
        a, b = marks["steady"]
        w = (t >= a) & (t <= b)
        if w.any():
            out["steady_pitch_deg"] = float(pitch[w].mean())
            out["steady_roll_deg"] = float(roll[w].mean())
    if "release" in marks and kind != "yol":
        final = 0.0 if kind in ("fren", "kalkis", "birakma", "kasis", "serit") else None
        out["after"] = transient_metrics(t, main, marks["release"], final=final)
    return out


def explorer_payload(st: State, res: dict, max_points: int = 1600) -> dict:
    """The simulation as the signal explorer's data."""
    t = res["t"]
    stride = max(1, int(math.ceil(t.size / max_points)))
    sl = slice(0, None, stride)

    def col(v, d=3):
        return [round(float(x), d) for x in np.asarray(v)[sl]]
    trav = res["travel"] * st.MR[None, :] * 1e3
    sig = [
        {"key": "pitch", "label": "Yunuslama açısı (+ dalma)", "unit": "°", "group": "Gövde", "values": col(res["pitch_deg"], 4), "digits": 2},
        {"key": "roll", "label": "Yalpa açısı", "unit": "°", "group": "Gövde", "values": col(res["roll_deg"], 4), "digits": 2},
        {"key": "heave", "label": "Gövde düşey hareketi", "unit": "mm", "group": "Gövde", "values": col(res["heave_mm"], 2), "digits": 1},
        {"key": "acc_front", "label": "Düşey ivme · ön aks üstü", "unit": "m/s²", "group": "Gövde", "values": col(res["acc"]["front"]), "digits": 2},
        {"key": "acc_cog", "label": "Düşey ivme · ağırlık merkezi", "unit": "m/s²", "group": "Gövde", "values": col(res["acc"]["cog"]), "digits": 2},
        {"key": "acc_rear", "label": "Düşey ivme · arka aks üstü", "unit": "m/s²", "group": "Gövde", "values": col(res["acc"]["rear"]), "digits": 2},
        {"key": "ax", "label": "Boyuna ivme", "unit": "m/s²", "group": "Girdi", "values": col(res["ax"]), "digits": 2},
        {"key": "ay", "label": "Yanal ivme", "unit": "m/s²", "group": "Girdi", "values": col(res["ay"]), "digits": 2},
        {"key": "speed", "label": "Hız", "unit": "km/h", "group": "Girdi", "values": col(res["speed"] * 3.6, 1), "digits": 0},
        {"key": "road_f", "label": "Yol · ön tekerlek", "unit": "mm", "group": "Girdi", "values": col(res["zr"][:, 0] * 1e3, 2), "digits": 1},
        {"key": "road_r", "label": "Yol · arka tekerlek", "unit": "mm", "group": "Girdi", "values": col(res["zr"][:, 2] * 1e3, 2), "digits": 1},
    ]
    for i, c in enumerate(CORNERS):
        sig.append({"key": f"travel_{c}", "label": f"Körük stroku · {CORNER_TR[c]} (+ basma)", "unit": "mm", "group": "Süspansiyon", "values": col(trav[:, i], 2), "digits": 1})
    for i, c in enumerate(CORNERS):
        sig.append({"key": f"dv_{c}", "label": f"Amortisör hızı · {CORNER_TR[c]}", "unit": "m/s", "group": "Süspansiyon", "values": col(res["damper_v"][:, i], 4), "digits": 3})
    for i, c in enumerate(CORNERS):
        sig.append({"key": f"fz_{c}", "label": f"Tekerlek yükü · {CORNER_TR[c]}", "unit": "kN", "group": "Tekerlek", "values": col(res["Fz"][:, i] / 1e3, 2), "digits": 1})
    sig.append({"key": "ltr", "label": "Yük aktarım oranı (1 = iç tekerlek kalktı)", "unit": "", "group": "Tekerlek", "values": col(res["ltr"], 4), "digits": 2})
    presets = [["Yunuslama", ["pitch", "ax"]], ["Yalpa", ["roll", "ay", "ltr"]], ["Düşey", ["heave", "acc_cog", "road_f"]],
               ["Strok", ["travel_fl", "travel_fr", "travel_rl", "travel_rr"]], ["Tekerlek yükleri", ["fz_fl", "fz_fr", "fz_rl", "fz_rr"]]]
    pick = {"fren": 0, "kalkis": 0, "kasis": 2, "direksiyon": 1, "serit": 1, "yol": 2}.get(res["kind"])
    if pick is None:
        pick = {"ziplama": 2, "yunuslama": 0, "yalpa": 1}[res["params"]["mode"]]
    sel = list(presets[pick][1])
    if res["kind"] == "kasis":
        sel = ["pitch", "heave", "road_f", "road_r"]
    return {"title": MANOEUVRES[res["kind"]]["label"], "x": [{"label": "zaman", "unit": "s", "digits": 2, "values": col(t, 4)}],
            "signals": sig, "presets": presets, "selected": sel}


# ---------------------------------------------------------------------------
# rollover
# ---------------------------------------------------------------------------


def _static_threshold(st: State, sign: float = 1.0) -> dict:
    """Steady lateral acceleration at which the first inner wheel, and then both, are unloaded."""
    inner = [0, 2] if sign > 0 else [1, 3]

    def march(q, ay0, done):
        """Raise the acceleration from ``ay0`` until ``done``; return the last state before it and the bracket."""
        step = 0.04 * G
        lo, hi = ay0, ay0
        for _ in range(60):
            hi = lo + step
            qn, ok = st.static_ok(ay=sign * hi, q0=q)
            if not ok or done(st.wheel_loads(qn)[inner]):
                break
            q, lo = qn, hi
        for _ in range(12):
            mid = 0.5 * (lo + hi)
            qn, ok = st.static_ok(ay=sign * mid, q0=q)
            if not ok or done(st.wheel_loads(qn)[inner]):
                hi = mid
            else:
                lo, q = mid, qn
        return q, lo
    q, first = march(np.zeros(7), 0.0, lambda f: f.min() <= 1.0)
    q, both = march(q, first, lambda f: f.sum() <= 1.0)
    return {"ay_g": both / G, "roll_deg": abs(math.degrees(q[2])), "first_wheel_g": first / G}


def _step_lifts(st: State, ay: float, rise: float, sign: float, dt: float = 0.008) -> bool:
    n = int((0.3 + rise + 2.6) / dt)
    th = np.arange(2 * n + 1) * (dt / 2)
    inp = {"kind": "direksiyon", "params": {}, "dt": dt, "n": n, "t_half": th, "ax": np.zeros_like(th),
           "ay": sign * ay * _ramp(th, 0.3, rise), "zr": np.zeros((th.size, 4)), "zrd": np.zeros((th.size, 4)),
           "speed": np.zeros_like(th), "q0": np.zeros(7), "marks": {}}
    return bool(simulate(st, inp, stop_on_lift=True)["lift"].any())


def rollover(st: State, *, rise_s: float = 0.5, radii=None, dynamic: bool = True) -> dict:
    """Rigid, suspended and sudden-manoeuvre rollover limits, the tilt-table angle and the speeds on a curve."""
    m = st.model
    half = (st.axle_load * st.track / 2).sum() / st.axle_load.sum()     # load-weighted half track
    y = abs(st.cog_y)
    rigid = (half - y) / st.cog_z
    sides = [(+1.0, "sola dönüş")] if y < 1e-6 else [(+1.0, "sola dönüş"), (-1.0, "sağa dönüş")]
    stat = min((dict(_static_threshold(st, s), side=lab, sign=s) for s, lab in sides), key=lambda d: d["ay_g"])
    summ_rate = math.radians(st.summary()["roll"]["gradient_book_deg_per_g"])
    book = rollover_suspended(2 * (half - y), st.cog_z, summ_rate, st.h_roll_axis)
    out = {"half_track_m": float(half), "ssf_rigid_g": float(rigid), "static_g": float(stat["ay_g"]), "static_side": stat["side"],
           "static_roll_deg": float(stat["roll_deg"]), "first_wheel_g": float(stat["first_wheel_g"]),
           "book_suspended_g": float(book), "tilt_rigid_deg": float(math.degrees(math.atan(rigid))),
           "tilt_deg": float(math.degrees(math.atan(stat["ay_g"]))), "tilt_required_deg": 28.0,
           "friction": float(m.friction), "slides_first": bool(m.friction < stat["ay_g"]), "rise_s": float(rise_s)}
    dyn = stat["ay_g"]
    if dynamic:
        lo, hi = 0.3 * stat["ay_g"] * G, stat["ay_g"] * G
        if not _step_lifts(st, hi, rise_s, stat["sign"]):
            lo = hi
        else:
            for _ in range(8):
                mid = 0.5 * (lo + hi)
                if _step_lifts(st, mid, rise_s, stat["sign"]):
                    hi = mid
                else:
                    lo = mid
        dyn = lo / G
    out["dynamic_g"] = float(dyn)
    out["dynamic_slides_first"] = bool(m.friction < dyn)
    radii = list(radii) if radii is not None else [20, 30, 40, 50, 75, 100, 150, 200, 250, 300, 400, 500]
    out["curve"] = [{"radius_m": float(r), "static_kmh": 3.6 * math.sqrt(stat["ay_g"] * G * r), "dynamic_kmh": 3.6 * math.sqrt(dyn * G * r),
                     "rigid_kmh": 3.6 * math.sqrt(rigid * G * r), "slide_kmh": 3.6 * math.sqrt(m.friction * G * r)} for r in radii]
    return out


def speeds_on_radius(roll: dict, radius_m: float) -> dict:
    r = max(float(radius_m), 1.0)
    return {"radius_m": r, "static_kmh": 3.6 * math.sqrt(roll["static_g"] * G * r), "dynamic_kmh": 3.6 * math.sqrt(roll["dynamic_g"] * G * r),
            "slide_kmh": 3.6 * math.sqrt(roll["friction"] * G * r)}


def lift_speed(st: State, kind: str, params: dict, lo_kmh: float = 15.0, hi_kmh: float = 160.0) -> dict:
    """The lowest speed at which the manoeuvre lifts both inner wheels."""
    def lifts(v):
        inp = manoeuvre_inputs(st, kind, {**params, "speed_kmh": v}, dt=0.008)
        return bool(simulate(st, inp, stop_on_lift=True)["lift"].any())
    hi_cap = MANOEUVRES[kind]["params"][0][5]
    hi = min(hi_kmh, hi_cap)
    if not lifts(hi):
        return {"speed_kmh": None, "above_kmh": hi}
    lo = lo_kmh
    if lifts(lo):
        return {"speed_kmh": lo, "below": True}
    for _ in range(8):
        mid = 0.5 * (lo + hi)
        if lifts(mid):
            hi = mid
        else:
            lo = mid
    return {"speed_kmh": float(hi)}


# ---------------------------------------------------------------------------
# comfort on a random road
# ---------------------------------------------------------------------------


def comfort(st: State, speed_kmh: float = 80.0, classes=("A", "B", "C", "D")) -> dict:
    """Weighted r.m.s. vertical acceleration on ISO 8608 roads (ISO 2631-1 Wk).

    Linear frequency response of the seven-degree-of-freedom model; the rear
    wheels meet the same road a wheelbase later; the damper curve is replaced
    by the coefficient that fits the stroke velocity the road produces
    (two passes of statistical linearisation).
    """
    v = max(speed_kmh, 1.0) / 3.6
    f = np.geomspace(0.3, 80.0, 420)
    w = 2 * math.pi * f
    wk = wk_weighting(f)
    delay = np.exp(-1j * w * st.L / v)
    M = np.diag(st.Mdiag)
    pos = {"front": st.xa[0], "cog": 0.0, "rear": st.xa[1]}
    ax_of = [0, 0, 1, 1]
    rows = []
    gain_out = None
    for cls in classes:
        psd = road_psd(cls, v, f)
        c_eq = st.c_eq.copy()
        for _ in range(3):
            cw = (st.nMRd * st.MRd) * c_eq
            C = np.zeros((7, 7))
            for i in range(4):
                C += cw[i] * np.outer(st.Bd[i], st.Bd[i])
                C[3 + i, 3 + i] += st.ct[i]
            H = np.empty((f.size, 7), dtype=complex)
            for k in range(f.size):
                F = np.zeros(7, dtype=complex)
                g = st.kt + 1j * w[k] * st.ct
                F[3], F[4] = g[0], g[1]
                F[5], F[6] = g[2] * delay[k], g[3] * delay[k]
                H[k] = np.linalg.solve(-w[k] ** 2 * M + 1j * w[k] * C + st.K, F)
            vd = (H @ st.Bd.T) * (1j * w[:, None]) * st.MRd[None, :]
            sig = np.sqrt(_trapz(np.abs(vd) ** 2 * psd[:, None], f, axis=0))
            c_eq = np.array([st.damper_objs[a].equivalent_coefficient(math.sqrt(2.0) * max(s, 1e-3)) for a, s in zip(ax_of, sig)])
        row = {"road_class": cls, "label": ISO8608_LABEL[cls], "damper_rms_mps": [float(sig[0]), float(sig[2])]}
        gains = {}
        for name, xp in pos.items():
            ha = -w ** 2 * (H[:, 0] - xp * H[:, 1])
            gains[name] = np.abs(ha)
            aw = math.sqrt(float(_trapz(np.abs(ha) ** 2 * wk ** 2 * psd, f)))
            rms = math.sqrt(float(_trapz(np.abs(ha) ** 2 * psd, f)))
            row[name] = {"aw_mps2": aw, "rms_mps2": rms, "label": comfort_label(aw)}
        rows.append(row)
        if cls == "C" or gain_out is None:
            gain_out = {k: [round(float(x), 2) for x in (g / np.maximum(w ** 2, 1e-9))] for k, g in gains.items()}
    return {"speed_kmh": float(speed_kmh), "rows": rows, "f_hz": [round(float(x), 3) for x in f],
            "transmissibility": gain_out, "wk": [round(float(x), 3) for x in wk]}


# ---------------------------------------------------------------------------
# library
# ---------------------------------------------------------------------------

HEADER = ("# Süspansiyon dosyası (Çalışma masası → Süspansiyon). SI birimleri; x ön akstan geriye, y sağa, z yerden yukarı.\n"
          "# 'measured' listesindeki alanlar çizim ya da test verisidir; geri kalan her şey KABULDÜR.\n")


def safe_name(text: str) -> str:
    import re
    tr = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    s = re.sub(r"[^a-z0-9]+", "_", str(text).translate(tr).lower()).strip("_")
    return s or "suspansiyon"


def _dir(root) -> pathlib.Path:
    return pathlib.Path(root) / "suspension"


def list_suspensions(root) -> list:
    out = []
    d = _dir(root)
    if not d.exists():
        return out
    for p in sorted(d.glob("*.yaml")):
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if raw.get("kind", "suspension") != "suspension":
            continue
        out.append({"stem": p.stem, "name": raw.get("name", p.stem), "vehicles": list(raw.get("vehicles") or []),
                    "measured": len(raw.get("measured") or [])})
    return out


def load_suspension(root, stem: str) -> SuspensionModel:
    p = _dir(root) / f"{safe_name(stem)}.yaml"
    if not p.exists():
        raise FileNotFoundError(f"suspension/{p.name} bulunamadı")
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if d.get("kind", "suspension") != "suspension":
        raise ValueError(f"{p.name}: kind suspension değil")
    return SuspensionModel.from_dict(d, stem=p.stem)


def save_suspension(root, model: SuspensionModel, *, overwrite: bool = False) -> pathlib.Path:
    d = _dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stem = safe_name(model.stem or model.name)
    p = d / f"{stem}.yaml"
    if p.exists() and not overwrite:
        raise FileExistsError(f"suspension/{p.name} zaten var")
    data = {"kind": "suspension", **model.to_dict()}
    data.pop("stem", None)
    p.write_text(HEADER + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=150), encoding="utf-8")
    return p


def suspension_for(root, vehicle_stem: str) -> tuple:
    """(stem, linked) of the suspension file for a vehicle: the one that names
    it, else the first file that names no vehicle (a generic one), else none."""
    files = list_suspensions(root)
    for f in files:
        if vehicle_stem in f["vehicles"]:
            return f["stem"], True
    for f in files:
        if not f["vehicles"]:
            return f["stem"], False
    return (files[0]["stem"], False) if files else (None, False)


def state_for_vehicle(model: SuspensionModel, vehicle, passengers: float | None = None, luggage_kg: float = 0.0) -> State:
    ch = vehicle.chassis
    n = vehicle.seats if passengers is None else passengers
    return State(model, kerb_mass_kg=ch.kerb_mass_kg, passengers=n, passenger_mass_kg=ch.passenger_mass_kg, luggage_kg=luggage_kg)


def handling(st: State, roll: dict | None = None) -> dict:
    """What the live drive needs to steer and lean like this vehicle."""
    s = st.summary()
    roll = roll or rollover(st)
    k = math.radians(st.model.understeer_deg_per_g)
    return {"wheelbase_m": st.L, "understeer_deg_per_g": st.model.understeer_deg_per_g, "understeer_rad_per_mps2": k / G,
            "characteristic_speed_kmh": 3.6 * characteristic_speed(st.L, k) if k > 0 else None,
            "roll_deg_per_g": s["roll"]["gradient_deg_per_g"], "dive_deg_per_g": s["pitch"]["dive_deg_per_g"],
            "squat_deg_per_g": s["pitch"]["squat_deg_per_g"], "rollover_static_g": roll["static_g"], "rollover_g": roll["dynamic_g"],
            "friction": st.model.friction, "roll_hz": s["roll"]["hz"]}


def pitch_trace(st: State, speed_mps: np.ndarray, dt_s: float) -> np.ndarray:
    """Quasi-static pitch angle [deg] along a speed trace (+ = dive)."""
    s = st.summary()["pitch"]
    ax = np.gradient(np.asarray(speed_mps, dtype=float), dt_s) / G
    return np.where(ax < 0, -ax * s["dive_deg_per_g"], -ax * s["squat_deg_per_g"])
