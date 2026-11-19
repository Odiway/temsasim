"""Gearshift logic for multi-speed gearboxes: VECTO's AMT "EffShift" strategy.

This module is a Python port of the gearshift model of VECTO, the European
Commission's vehicle energy consumption calculation tool
(https://code.europa.eu/vecto/vecto, licence EUPL-1.2, (c) European Union):

* ``DeclarationData.Gearbox.ComputeEfficiencyShiftPolygon`` -- the shift
  polygon of every gear, built from the engine full-load curve;
* ``EngineFullLoadCurve`` -- the characteristic engine speeds the polygons
  and the strategy read (n_Tq99l, n_Tq98h, n_P98h, n_95h, rated speed);
* ``ShiftPolygon`` -- the left-of / right-of tests on those lines;
* ``AMTShiftStrategyOptimized`` ("AMT - EffShift"; its parameter interface
  calls them the ACEA/Scania gearshift parameters) with ``BaseShiftStrategy``:
  emergency shifts, polygon shifts with skipped gears, fuel-efficiency shifts
  within +/- 2 gears, start gear selection, and the timers between shifts.

The logic and the default values are VECTO's; every one of them is a set
point here (``ShiftSetPoints``), because a real gearbox is calibrated, not
generic, and the point of the exercise is to fit the set points to a logged
gear trace. This file is a derivative work of VECTO and is therefore
distributed under the EUPL-1.2 as well; see ``THIRD_PARTY.md``.

What is adapted, and why
------------------------
VECTO is a forward-and-back simulator: its driver asks for an acceleration,
the powertrain answers, and "what would happen in gear g" is answered by a
dry run on a copy of the powertrain. temsasim follows a prescribed 1 Hz
speed trace backwards. So:

* the dry runs become a table: for every sample and every gear the engine
  speed, the gearbox input torque through that gear's loss map, the
  crankshaft torque with auxiliaries, full load, drag and fuel flow. The
  strategy then only looks things up, which is what makes a calibration run
  of several hundred evaluations a matter of seconds;
* the driver's state (driving / coasting / braking) is read off the trace:
  slowing down faster than ``driver_decel_threshold`` is coasting, or
  braking when the engine's drag is not enough;
* the 1 s traction interruption of an AMT cannot change a prescribed speed
  trace. It is kept in the decision (the speed the vehicle would have lost
  enters the post-shift engine speed) and in the timers, not in the energy;
* a trace is a demand that has to be met, so a gear that cannot deliver the
  demanded torque is left for a lower one (``kickdown``). VECTO's driver
  would settle for less acceleration instead.

Three things a TraXon does and VECTO's generic model does not are available
as extensions, all off by default so that the defaults are VECTO's:
downshifts on the overrun to raise the engine speed for the engine brake and
the retarder (``brake_downshift_speed_rpm``), more than one gear per polygon
downshift (``max_skip_down``), and a cap on the start gear
(``launch_gear_cap``, the clutch-protection mode that pulls away in 1st or
2nd).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields

import numpy as np

from .units import G, KMH, RPM

STRATEGY_WINDOW = "window"
STRATEGY_EFFSHIFT = "vecto_effshift"
STRATEGIES = (STRATEGY_WINDOW, STRATEGY_EFFSHIFT)


# --------------------------------------------------------------------------- set points
@dataclass
class ShiftSetPoints:
    """Every tunable of the strategy. Defaults are VECTO's declaration values
    (``DeclarationData.Gearbox``, ``DeclarationData.GearboxTCU``,
    ``Constants.SimulationSettings``); a value of 0 for an ``*_rpm`` override
    means "derive it from the full-load curve as VECTO does"."""

    # --- timers --------------------------------------------------------------
    time_between_gearshifts_s: float = 2.0
    downshift_after_upshift_delay_s: float = 6.0
    upshift_after_downshift_delay_s: float = 6.0
    traction_interruption_s: float = 1.0

    # --- start gear ----------------------------------------------------------
    start_speed_kmh: float = 8.0
    start_acceleration_mps2: float = 0.8
    start_torque_reserve: float = 0.20
    clutch_closing_speed_norm: float = 0.065
    launch_gear_cap: int = 0

    # --- shift polygon -------------------------------------------------------
    polygon_full_load_margin: float = 0.98
    downshift_idle_factor: float = 1.10
    downshift_low_speed_rpm: float = 0.0
    downshift_high_speed_rpm: float = 0.0
    upshift_speed_rpm: float = 0.0
    max_skip_down: int = 1

    # --- efficiency shifts ---------------------------------------------------
    rating_factor_current_gear: float = 0.97
    allowed_gear_range_fc: int = 2
    ratio_early_upshift_fc: float = 24.0
    ratio_early_downshift_fc: float = 24.0
    torque_reserve: float = 0.0
    min_engine_speed_post_upshift_rpm: float = 0.0
    upshift_min_acceleration_mps2: float = 0.10
    acceleration_factor: float = 0.5
    velocity_drop_factor: float = 1.0
    drag_margin_factor: float = 0.7
    min_speed_after_traction_interruption_kmh: float = 5.0

    # --- driver state and halting ---------------------------------------------
    driver_decel_threshold_mps2: float = 0.10
    disengage_when_halting_speed_kmh: float = 10.0

    # --- adaptations and extensions (not in VECTO) ----------------------------
    kickdown: bool = True
    kickdown_overload_share: float = 0.03
    brake_downshift_speed_rpm: float = 0.0
    brake_downshift_max_speed_rpm: float = 1700.0
    brake_downshift_min_decel_mps2: float = 0.30

    @classmethod
    def from_dict(cls, d: dict | None) -> "ShiftSetPoints":
        sp = cls()
        for k, v in (d or {}).items():
            if not hasattr(sp, k) or v is None:
                continue
            cur = getattr(sp, k)
            if isinstance(cur, bool):
                v = bool(v) if not isinstance(v, str) else v.strip().lower() in ("1", "true", "evet", "yes", "on")
            elif isinstance(cur, int):
                v = int(round(float(v)))
            else:
                v = float(v)
            setattr(sp, k, v)
        return sp

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def changed(self) -> dict:
        """Only the set points that differ from VECTO's defaults: what a
        transmission file needs to store."""
        ref = ShiftSetPoints()
        return {k: v for k, v in self.to_dict().items() if v != getattr(ref, k)}


def _info(group, label, unit, lo, hi, step, vecto, tip, kind="vecto"):
    return {"group": group, "label": label, "unit": unit, "min": lo, "max": hi, "step": step,
            "vecto": vecto, "tip": tip, "kind": kind}


SET_POINT_INFO = {
    # timers
    "time_between_gearshifts_s": _info("Zamanlayıcılar", "İki vites arası asgari süre", "s", 1, 5, 0.5,
                                       "Gearbox.MinTimeBetweenGearshifts", "Acil durum dışında bu süre dolmadan yeni vites değişimi olmaz."),
    "downshift_after_upshift_delay_s": _info("Zamanlayıcılar", "Büyütmeden sonra küçültme gecikmesi", "s", 1, 15, 0.5,
                                             "Gearbox.DownshiftAfterUpshiftDelay", "Vites büyüttükten sonra küçültmeye izin verilmeyen süre."),
    "upshift_after_downshift_delay_s": _info("Zamanlayıcılar", "Küçültmeden sonra büyütme gecikmesi", "s", 1, 15, 0.5,
                                             "Gearbox.UpshiftAfterDownshiftDelay", "Vites küçülttükten sonra büyütmeye izin verilmeyen süre."),
    "traction_interruption_s": _info("Zamanlayıcılar", "Çekiş kesintisi", "s", 0, 2, 0.1,
                                     "GearboxType.TractionInterruption (AMT 1 s)", "Vites değişirken çekişin kesildiği süre; değişim sonrası devir tahminine girer."),
    # start
    "start_speed_kmh": _info("Kalkış", "Kalkış hızı", "km/h", 3, 16, 0.5, "GearboxTCU.StartSpeed",
                             "Bu hızda motor devri debriyaj kapanma devrinin üstünde kalan en yüksek vites, azami kalkış vitesidir."),
    "start_acceleration_mps2": _info("Kalkış", "Kalkış ivmesi", "m/s²", 0.2, 2.0, 0.05, "GearboxTCU.StartAcceleration",
                                     "Kalkış vitesinin tork rezervi bu ivme için gereken torkla sınanır."),
    "start_torque_reserve": _info("Kalkış", "Kalkış tork rezervi", "oran", 0, 0.9, 0.01, "GearboxTCU.TorqueReserveStart",
                                  "Kalkış vitesinde tam yük torkunun en az bu kadarı boşta kalmalı; büyüdükçe ağır/yokuş kalkışta vites düşer."),
    "clutch_closing_speed_norm": _info("Kalkış", "Debriyaj kapanma devri (normalize)", "oran", 0, 0.3, 0.005,
                                       "SimulationSettings.ClutchClosingSpeedNorm", "Rölanti ile anma devri arasındaki oran; debriyajın tam kapandığı devir."),
    "launch_gear_cap": _info("Kalkış", "Kalkış vitesi üst sınırı", "vites", 0, 6, 1, "",
                             "0 = sınır yok. Debriyaj aşırı ısınma modunu denemek için 1 ya da 2 girin.", "temsa"),
    # polygon
    "polygon_full_load_margin": _info("Vites hatları", "Tam yük eğrisi payı", "oran", 0.80, 1.0, 0.01,
                                      "Gearbox.ShiftPolygonEngineFldMargin", "Küçültme hattı, düşük devirde tam yük eğrisinin bu oranından geçer."),
    "downshift_idle_factor": _info("Vites hatları", "Küçültme alt devri / rölanti", "oran", 1.0, 2.0, 0.01,
                                   "ComputeEfficiencyShiftPolygon p2", "Kısmi yükte küçültme devri: min(debriyaj kapanma − 10 d/d, rölanti × bu oran)."),
    "downshift_low_speed_rpm": _info("Vites hatları", "Kısmi yükte küçültme devri", "d/d", 0, 1400, 10, "p2 (0 = VECTO)",
                                     "0 = VECTO kuralı. Değer girilirse motor bu devrin altına indiğinde yükten bağımsız küçültülür."),
    "downshift_high_speed_rpm": _info("Vites hatları", "Tam yükte küçültme devri", "d/d", 0, 1600, 10, "p3 = n_Tq99l (0 = VECTO)",
                                      "0 = azami torkun %99'una ulaşılan devir. Tam yüke yakın çalışırken bu devrin altında küçültülür."),
    "upshift_speed_rpm": _info("Vites hatları", "Büyütme devri", "d/d", 0, 2400, 10, "p5 = n_P98h (0 = VECTO)",
                               "0 = azami gücün %98'ine düşülen üst devir. Motor bu devri aşınca yükten bağımsız büyütülür."),
    "max_skip_down": _info("Vites hatları", "Küçültmede atlanabilen vites", "vites", 1, 4, 1, "",
                           "1 = VECTO (tek tek). 2-3: hat ihlali sürdükçe bir kerede birden çok vites küçültülür.", "temsa"),
    # efficiency
    "rating_factor_current_gear": _info("Verim vitesi", "Mevcut vites lehine pay", "oran", 0.80, 1.0, 0.005,
                                        "GearboxTCU.RatingFactorCurrentGear", "Aday vitesin yakıtı mevcut vitesin bu oranından düşükse değişilir (0,97 = en az %3 kazanç)."),
    "allowed_gear_range_fc": _info("Verim vitesi", "Aranan vites aralığı", "vites", 1, 3, 1, "GearboxTCU.AllowedGearRangeFCAMT",
                                   "Verim için kaç vites yukarı / aşağı bakılır; 2 = ikişer vites atlayabilir."),
    "ratio_early_upshift_fc": _info("Verim vitesi", "Erken büyütme oran sınırı", "toplam oran", 4, 60, 1, "GearboxTCU.RatioEarlyUpshiftFC",
                                    "Şanzıman × aks toplam oranı bu değerin altındaki viteslere verim için büyütülür; alt viteslerde gezinmeyi önler."),
    "ratio_early_downshift_fc": _info("Verim vitesi", "Erken küçültme oran sınırı", "toplam oran", 4, 60, 1, "GearboxTCU.RatioEarlyDownshiftFC",
                                      "Toplam oranı bu değeri aşmayan viteslere verim için küçültülür."),
    "torque_reserve": _info("Verim vitesi", "Tork rezervi", "oran", 0, 0.5, 0.01, "GearboxTCU.TorqueReserve",
                            "Verim için büyütülecek viteste tam yükün en az bu kadarı boşta kalmalı."),
    "min_engine_speed_post_upshift_rpm": _info("Verim vitesi", "Büyütme sonrası asgari devir", "d/d", 0, 1400, 10,
                                               "GearboxTCU.MinEngineSpeedPostUpshift", "Büyütmeden sonra tahmin edilen devir bunun altındaysa büyütülmez. TraXon için en etkili ayar."),
    "upshift_min_acceleration_mps2": _info("Verim vitesi", "Büyütme sonrası asgari ivme", "m/s²", 0, 0.5, 0.01,
                                           "Gearbox.UpshiftMinAcceleration", "Yeni viteste tam yükle ulaşılabilen ivme bunun altındaysa büyütülmez."),
    "acceleration_factor": _info("Verim vitesi", "Tam gazda ivme katsayısı", "oran", 0.1, 1.0, 0.05, "GearboxTCU.AccelerationFactor",
                                 "Yüksek devirde, büyütme için ivme torkunun bu orana kadar düşmesine razı olunur; küçüldükçe tam gazda daha erken büyütülür."),
    "velocity_drop_factor": _info("Verim vitesi", "Hız kaybı katsayısı", "oran", 0, 2, 0.1, "GearboxTCU.VelocityDropFactor",
                                  "Çekiş kesintisinde kaybedilen hızın, büyütme sonrası devir tahminine girme oranı."),
    "drag_margin_factor": _info("Verim vitesi", "Sürükleme payı", "oran", 0, 1, 0.05, "GearboxTCU.DragMarginFactor",
                                "Motor torku sürükleme torkunun bu oranından düşükse (motor freni) verim için vites değişmez."),
    "min_speed_after_traction_interruption_kmh": _info("Verim vitesi", "Kesinti sonrası asgari hız", "km/h", 0, 20, 1,
                                                       "GearboxTCU.MIN_SPEED_AFTER_TRACTION_INTERRUPTION", "Kesinti sonunda hız bunun altına düşecekse verim için vites değişmez."),
    # driver state
    "driver_decel_threshold_mps2": _info("Sürüş durumu", "Yavaşlama eşiği", "m/s²", 0.02, 0.6, 0.01, "",
                                         "Araç bundan hızlı yavaşlıyorsa sürücü gazı bırakmış sayılır; o sırada vites büyütülmez.", "adapt"),
    "disengage_when_halting_speed_kmh": _info("Sürüş durumu", "Duruşta debriyaj ayırma hızı", "km/h", 0, 20, 1,
                                              "SimulationSettings.ClutchDisengageWhenHaltingSpeed", "Fren yaparken bu hızın altında, devir rölantiye inince debriyaj açılır."),
    # extensions
    "kickdown": _info("Uyarlama ve eklentiler", "Talep karşılanmazsa küçült", "", 0, 1, 1, "",
                      "Hız izi bir taleptir: mevcut vites istenen torku veremiyorsa verebilen vitese inilir. VECTO sürücüsü ivmeyi düşürür.", "adapt"),
    "kickdown_overload_share": _info("Uyarlama ve eklentiler", "Küçültme için aşım payı", "oran", 0, 0.3, 0.01, "",
                                     "Talep tam yükü bu oranda aşarsa küçültülür.", "adapt"),
    "brake_downshift_speed_rpm": _info("Uyarlama ve eklentiler", "Frenlemede küçültme devri", "d/d", 0, 1600, 10, "",
                                       "0 = kapalı (VECTO). Motor freni / retarder için: fren talebi varken devir bunun altına inince küçültülür.", "temsa"),
    "brake_downshift_min_decel_mps2": _info("Uyarlama ve eklentiler", "Fren küçültmesi için asgari yavaşlama", "m/s²", 0.05, 1.5, 0.05, "",
                                            "Fren küçültmesi yalnızca araç bundan hızlı yavaşlarken (servis freni / retarder devredeyken) yapılır.", "temsa"),
    "brake_downshift_max_speed_rpm": _info("Uyarlama ve eklentiler", "Frenlemede küçültme sonrası azami devir", "d/d", 1000, 2400, 10, "",
                                           "Fren küçültmesi, yeni vitesteki devir bunu aşmayacaksa yapılır.", "temsa"),
}


# --------------------------------------------------------------------------- full-load characteristics
@dataclass
class EngineCharacteristics:
    """Characteristic speeds of a full-load curve, as VECTO's
    ``EngineFullLoadCurve`` computes them. All speeds in rad/s."""

    max_torque_nm: float
    max_drag_torque_nm: float       # most negative drag torque
    max_power_w: float
    rated_speed: float              # speed of maximum power
    n_tq99l: float                  # first speed at 99 % of max torque
    n_tq98h: float                  # last speed at 98 % of max torque
    n_p98h: float                   # last speed at 98 % of max power
    n_95h: float                    # last speed at 95 % of max power

    def as_rpm(self) -> dict:
        return {"max_torque_nm": self.max_torque_nm, "max_power_kw": self.max_power_w / 1e3,
                "rated_speed_rpm": self.rated_speed / RPM, "n_tq99l_rpm": self.n_tq99l / RPM,
                "n_tq98h_rpm": self.n_tq98h / RPM, "n_p98h_rpm": self.n_p98h / RPM, "n_95h_rpm": self.n_95h / RPM}


def _speeds_for_torque(x, y, torque) -> list:
    out = []
    for k in range(1, len(x)):
        y1, y2 = y[k - 1], y[k]
        if abs(y1 - y2) < 1e-9 and abs(y1 - torque) < 1e-9:
            out.append(x[k])
        elif min(y1, y2) <= torque <= max(y1, y2) and abs(y2 - y1) > 1e-12:
            out.append(x[k - 1] + (x[k] - x[k - 1]) * (torque - y1) / (y2 - y1))
    return sorted(out)


def _speeds_for_power(x, y, power) -> list:
    out = []
    for i in range(1, len(x)):
        x1, x2, y1, y2 = x[i - 1], x[i], y[i - 1], y[i]
        if x2 <= x1:
            continue
        k = (y2 - y1) / (x2 - x1)
        d = y2 - k * x2
        if abs(k) < 1e-4:
            sols = [] if abs(d) < 1e-4 else [power / d]
        else:
            disc = d * d + 4.0 * k * power
            if disc < 0:
                sols = []
            else:
                r = math.sqrt(disc)
                sols = [(-d + r) / (2.0 * k), (-d - r) / (2.0 * k)]
        out.extend(s for s in sols if x1 - 1e-9 <= s <= x2 + 1e-9)
    return sorted(out)


def engine_characteristics(speed_rads, full_load_nm, drag_nm=None) -> EngineCharacteristics:
    """VECTO ``EngineFullLoadCurve``: rated speed (maximum power on the
    piecewise-linear curve), n_Tq99l / n_Tq98h, n_P98h and n_95h."""
    x = np.asarray(speed_rads, dtype=float)
    y = np.asarray(full_load_nm, dtype=float)
    if x.size < 2:
        raise ValueError("full-load curve needs at least two points")
    t_max = float(np.max(y))
    best = (float(x[0]), float(x[0] * y[0]))
    for i in range(1, x.size):
        x1, x2, y1, y2 = float(x[i - 1]), float(x[i]), float(y[i - 1]), float(y[i])
        if x2 <= x1:
            continue
        k = (y2 - y1) / (x2 - x1)
        d = y2 - k * x2
        cand = (x2, y2 * x2) if y2 * x2 > y1 * x1 else (x1, y1 * x1)
        if abs(k) > 1e-12:
            xs = -d / (2.0 * k)
            if x1 <= xs <= x2:
                cand = (xs, (k * xs + d) * xs)
        if cand[1] > best[1]:
            best = cand
    rated, p_max = best

    def last_above_rated(share, name):
        s = [v for v in _speeds_for_power(x, y, share * p_max) if v > rated]
        # a curve that stops before the power has fallen far enough: its last point
        return s[-1] if s else float(x[-1])

    tq99 = _speeds_for_torque(x, y, 0.99 * t_max)
    tq98 = _speeds_for_torque(x, y, 0.98 * t_max)
    drag_min = float(np.min(drag_nm)) if drag_nm is not None else -0.05 * t_max
    return EngineCharacteristics(
        max_torque_nm=t_max, max_drag_torque_nm=drag_min, max_power_w=p_max, rated_speed=rated,
        n_tq99l=tq99[0] if tq99 else float(x[int(np.argmax(y))]),
        n_tq98h=tq98[-1] if tq98 else float(x[int(np.argmax(y))]),
        n_p98h=last_above_rated(0.98, "n_P98h"), n_95h=last_above_rated(0.95, "n_95h"))


# --------------------------------------------------------------------------- shift polygon
class ShiftPolygon:
    """Downshift and upshift line of one gear in the (engine speed, gearbox
    input torque) plane. ``down`` / ``up`` are arrays of (torque, speed) rows
    in VECTO's order; the tests are VECTO's ``ShiftPolygon`` predicates."""

    def __init__(self, down, up):
        self.down = np.asarray(down, dtype=float).reshape(-1, 2)
        self.up = np.asarray(up, dtype=float).reshape(-1, 2)

    # -- VECTO GetSection(entry.speed < n): the pair where the predicate flips
    @staticmethod
    def _section(line, n):
        n = np.asarray(n, dtype=float)
        sp = line[:, 1]
        # index of "second": the first entry at or after index 1 whose speed is not below n
        if sp.size > 2 and np.all(np.diff(sp[1:]) >= 0):
            j = np.minimum(np.searchsorted(sp[1:], n, side="left") + 1, sp.size - 1)
        else:
            ge = sp[None, 1:] >= n[..., None]
            j = np.where(ge.any(axis=-1), ge.argmax(axis=-1) + 1, sp.size - 1)
        return line[j - 1], line[j]

    @staticmethod
    def _cross(n, t, a, b):
        return (b[..., 1] - a[..., 1]) * (t - a[..., 0]) - (b[..., 0] - a[..., 0]) * (n - a[..., 1])

    def is_below_downshift(self, torque, speed):
        """Left of the downshift line: the gear is too tall for this point."""
        n = np.asarray(speed, dtype=float)
        t = np.asarray(torque, dtype=float)
        if self.down.shape[0] < 2:
            return np.zeros(np.broadcast(n, t).shape, dtype=bool)
        a, b = self._section(self.down, n)
        out = (b[..., 1] >= n) & ~((a[..., 1] < n) & (b[..., 1] < n)) & (self._cross(n, t, a, b) > 1e-9)
        return out

    def is_above_downshift(self, torque, speed):
        n = np.asarray(speed, dtype=float)
        t = np.asarray(torque, dtype=float)
        if self.down.shape[0] < 2:
            return np.ones(np.broadcast(n, t).shape, dtype=bool)
        a, b = self._section(self.down, n)
        right = ~((a[..., 1] > n) & (b[..., 1] > n)) & (self._cross(n, t, a, b) < -1e-9)
        return (b[..., 1] < n) | right

    def is_above_upshift(self, torque, speed):
        """Right of the upshift line: the engine is turning too fast."""
        n = np.asarray(speed, dtype=float)
        t = np.asarray(torque, dtype=float)
        if self.up.shape[0] < 2:
            return np.zeros(np.broadcast(n, t).shape, dtype=bool)
        a, b = self._section(self.up, n)
        right = ~((a[..., 1] > n) & (b[..., 1] > n)) & (self._cross(n, t, a, b) < -1e-9)
        return (b[..., 1] < n) | right

    def interpolate_downshift(self, speed):
        """Torque of the downshift line at an engine speed; infinite on its
        vertical ends (VECTO ``InterpolateDownshift``)."""
        n = np.asarray(speed, dtype=float)
        if self.down.shape[0] < 2:
            return np.full(n.shape, np.inf)
        a, b = self._section(self.down, n)
        dn = b[..., 1] - a[..., 1]
        with np.errstate(divide="ignore", invalid="ignore"):
            t = a[..., 0] + (b[..., 0] - a[..., 0]) * (n - a[..., 1]) / dn
        return np.where(np.abs(dn) < 1e-9, np.inf, t)

    def downshift_speed_at_zero_torque(self) -> float:
        return float(self.down[0, 1]) if self.down.shape[0] else 0.0

    def as_rpm(self) -> dict:
        return {"down": [[float(t), float(n / RPM)] for t, n in self.down],
                "up": [[float(t), float(n / RPM)] for t, n in self.up]}


def efficiency_shift_polygon(gear_idx: int, n_gears: int, speed_rads, full_load_nm, char: EngineCharacteristics,
                             idle_rads: float, rated_rads: float, sp: ShiftSetPoints) -> ShiftPolygon:
    """VECTO ``ComputeEfficiencyShiftPolygon`` for one gear (0-based index).

    Downshift line: vertical at p2 from below the drag curve up to the
    full-load curve, then along the full-load curve times the margin up to
    p3 = n_Tq99l, then vertical above the curve. Upshift line: vertical at
    p5 = n_P98h. The first gear has no downshift line, the last no upshift
    line. ``speed_rads`` / ``full_load_nm`` is the curve of this gear, i.e.
    already clipped to the gear's torque limit."""
    x = np.asarray(speed_rads, dtype=float)
    y = np.asarray(full_load_nm, dtype=float)
    m = sp.polygon_full_load_margin
    fld = lambda n: float(np.interp(n, x, y))
    clutch_closing = (rated_rads - idle_rads) * sp.clutch_closing_speed_norm + idle_rads
    p2 = min(clutch_closing - 10.0 * RPM, idle_rads * sp.downshift_idle_factor)
    if sp.downshift_low_speed_rpm > 0:
        p2 = sp.downshift_low_speed_rpm * RPM
    p3 = char.n_tq99l if sp.downshift_high_speed_rpm <= 0 else sp.downshift_high_speed_rpm * RPM
    p3 = max(p3, p2 + 1.0 * RPM)
    p5 = char.n_p98h if sp.upshift_speed_rpm <= 0 else sp.upshift_speed_rpm * RPM

    down = []
    if gear_idx > 0:
        inner = [(float(xi), float(yi) * m) for xi, yi in zip(x, y) if p2 <= xi <= p3]
        down.append((char.max_drag_torque_nm * 1.1, p2))
        if not inner:
            down.append((fld(p2) * m, p2))
            down.append((fld(p3) * m, p3))
        else:
            if min(p[0] for p in inner) > p2:
                down.append((fld(p2) * m, p2))
            # VECTO applies the margin a second time to the curve's own points
            down.extend((yi * m, xi) for xi, yi in inner)
            if max(p[0] for p in inner) < p3:
                down.append((fld(p3) * m, p3))
        down.append((char.max_torque_nm * 1.1, p3))
    up = []
    if gear_idx < n_gears - 1:
        up = [(char.max_drag_torque_nm * 1.1, p5), (char.max_torque_nm * 1.1, p5)]
    return ShiftPolygon(down, up)


def polygon_from_table(rows, speed_unit: str = "rpm") -> ShiftPolygon:
    """A user-supplied polygon in VECTO's ``.vgbs`` form: rows of
    (engine torque [Nm], downshift speed, upshift speed)."""
    k = RPM if speed_unit == "rpm" else 1.0
    arr = np.asarray(rows, dtype=float)
    arr = arr[np.argsort(arr[:, 0])]
    down = [(t, n * k) for t, n, _ in arr if np.isfinite(n) and n > 0]
    up = [(t, n * k) for t, _, n in arr if np.isfinite(n) and n > 0]
    return ShiftPolygon(down, up)


# --------------------------------------------------------------------------- driveline table
@dataclass
class DrivelineTable:
    """Everything the strategy may ask about any gear at any sample.

    It replaces VECTO's dry runs. Arrays with two axes are (sample, gear).
    Nothing in here depends on the set points, so one table serves a whole
    calibration."""

    vehicle: object
    dt_s: float
    time_s: np.ndarray
    speed_mps: np.ndarray
    accel_mps2: np.ndarray
    grade_rad: np.ndarray
    mass_kg: np.ndarray
    moving: np.ndarray
    p_acc_w: np.ndarray             # auxiliaries + climate + fan on the crankshaft
    w_prop: np.ndarray              # gearbox output speed [rad/s]
    t_out: np.ndarray               # gearbox output torque without engine inertia [Nm]
    acc_torque_out: np.ndarray      # share of t_out that accelerates the vehicle mass [Nm]
    a_roll: np.ndarray              # acceleration with the driveline open [m/s2]
    n: np.ndarray                   # gearbox input speed [rad/s]
    w_eng: np.ndarray               # engine speed with the clutch slipping at idle [rad/s]
    t_in: np.ndarray                # gearbox input torque [Nm]
    t_tot: np.ndarray               # t_in + auxiliaries [Nm]
    t_inert: np.ndarray             # torque that accelerates engine and gearbox inertia [Nm]
    t_full: np.ndarray
    t_drag: np.ndarray
    fc_tot: np.ndarray              # fuel flow at t_tot [g/h]
    fc_out: np.ndarray              # fuel flow at t_in [g/h]
    a_est: np.ndarray               # acceleration available at full load [m/s2]
    ratios: np.ndarray
    axle_ratio: float
    tyre_radius_m: float
    idle: float
    rated: float                    # speed of maximum power on the full-load curve
    chars: list                     # EngineCharacteristics per gear (torque-limited curve)
    char0: EngineCharacteristics    # of the engine's own curve
    fld: tuple                      # (speed, torque) of the engine's own curve
    limits: list                    # gear torque limits or None
    m_wheels: float
    rho: float
    _cache: dict = field(default_factory=dict, repr=False)

    @property
    def n_gears(self) -> int:
        return int(self.ratios.size)

    def lists(self) -> dict:
        """The arrays the plan loop indexes, as plain lists (made once)."""
        c = self._cache.get("lists")
        if c is None:
            c = {k: getattr(self, k).tolist() for k in
                 ("time_s", "speed_mps", "accel_mps2", "moving", "t_out", "n", "w_eng", "t_in", "t_tot", "t_inert",
                  "t_full", "t_drag", "fc_tot", "fc_out", "a_est")}
            self._cache["lists"] = c
        return c

    def polygon_tables(self, sp: "ShiftSetPoints", polygons: list | None = None) -> tuple:
        """Shift polygons for these set points and, per sample and gear, on
        which side of them the operating point lies. Cached, because most set
        points do not move the polygons; polygons handed in are used as they
        are and not cached."""
        key = ("poly", sp.polygon_full_load_margin, sp.downshift_idle_factor, sp.downshift_low_speed_rpm,
               sp.downshift_high_speed_rpm, sp.upshift_speed_rpm, sp.clutch_closing_speed_norm)
        hit = self._cache.get(key) if polygons is None else None
        if hit is not None:
            return hit
        polys = polygons if polygons is not None else self.polygons(sp)
        N, ng = self.n.shape
        below = np.zeros((N, ng), dtype=bool)
        above_dn = np.ones((N, ng), dtype=bool)
        above_up = np.zeros((N, ng), dtype=bool)
        interp_dn = np.full((N, ng), np.inf)
        for g in range(ng):
            if g > 0:
                below[:, g] = polys[g].is_below_downshift(self.t_in[:, g], self.n[:, g])
                above_dn[:, g] = polys[g].is_above_downshift(self.t_in[:, g], self.n[:, g])
                interp_dn[:, g] = polys[g].interpolate_downshift(self.w_eng[:, g])
            if g < ng - 1:
                above_up[:, g] = polys[g].is_above_upshift(self.t_in[:, g], self.n[:, g])
        hit = (polys, below.tolist(), above_dn.tolist(), above_up.tolist(), interp_dn.tolist())
        if polygons is not None:
            return hit
        for k in [k for k in self._cache if isinstance(k, tuple) and k[0] == "poly"][:-7]:
            del self._cache[k]                       # keep the last few
        self._cache[key] = hit
        return hit

    def gear_full_load(self, g: int):
        x, y = self.fld
        lim = self.limits[g] if self.limits else None
        return x, (np.minimum(y, lim) if lim else y)

    def polygons(self, sp: ShiftSetPoints) -> list:
        tr = self.vehicle.transmission
        custom = getattr(tr, "shift_polygons", None) or {}
        out = []
        for g in range(self.n_gears):
            rows = custom.get(g + 1) or custom.get(str(g + 1))
            if rows:
                out.append(polygon_from_table(rows))
                continue
            x, y = self.gear_full_load(g)
            out.append(efficiency_shift_polygon(g, self.n_gears, x, y, self.chars[g], self.idle, self.rated, sp))
        return out


def _stage_input(loss_map, gear_key, ratio, eta, w_out, t_out):
    """Input speed, torque and loss torque of a gear stage for arrays of
    output speed and torque (the vector form of ``Transmission.*_input``)."""
    w_in = w_out * ratio
    if loss_map is not None:
        m = loss_map.gears.get(int(gear_key))
        if m is None:
            m = loss_map.gears[min(loss_map.gears)]
        t_in = t_out / ratio
        loss = np.zeros_like(t_in)
        for _ in range(3):
            loss = m(np.abs(w_in), t_in)
            t_in = t_out / ratio + loss
        return w_in, t_in, loss
    t_in = np.where(t_out >= 0, t_out / (ratio * eta), t_out * eta / ratio)
    return w_in, t_in, t_in - t_out / ratio


def build_table(vehicle, cycle, environment=None, p_acc_w=None, *, rho: float | None = None) -> DrivelineTable:
    """Operating points of every gear along a (resampled) drive cycle."""
    from .cycle import Environment
    from .units import air_density
    env = environment or Environment()
    ch, tr, eng = vehicle.chassis, vehicle.transmission, vehicle.engine
    t = np.asarray(cycle.time_s, dtype=float)
    v = np.asarray(cycle.speed_mps, dtype=float)
    n = t.size
    dt = float(t[1] - t[0]) if n > 1 else 1.0
    a = np.asarray(cycle.acceleration(), dtype=float)
    theta = np.asarray(cycle.grade_rad, dtype=float)
    mass = ch.kerb_mass_kg + np.asarray(cycle.passengers, dtype=float) * ch.passenger_mass_kg
    if rho is None:
        rho = air_density(env.ambient_c, env.air_pressure_pa)
    p_acc = np.zeros(n) if p_acc_w is None else np.asarray(p_acc_w, dtype=float)
    moving = v > 0.05
    r = ch.tyre_dynamic_radius_m
    m_wheels = ch.equivalent_rotating_mass_kg(0.0)

    forces = ch.resistive_force(v, mass_kg=mass, grade_rad=theta, air_density=rho,
                                headwind_mps=env.headwind_mps + np.asarray(cycle.headwind_mps, dtype=float))
    f_roll = np.asarray(forces["rolling"], dtype=float) * np.asarray(cycle.surface_factor, dtype=float)
    f_aero = np.asarray(forces["aero"], dtype=float)
    f_grade = np.where(moving, np.asarray(forces["grade"], dtype=float), 0.0)
    f_res = f_roll + f_aero + f_grade
    f_veh = f_res + (mass + m_wheels) * a
    w_wheel = v / r
    w_prop, t_prop, axle_loss = _stage_input(tr.axle_loss_map, 0, tr.axle_ratio, tr.axle_efficiency, w_wheel, f_veh * r)
    t_ret = np.zeros(n)
    if tr.retarder_type == "transmission_output" and tr.retarder_loss_curve is not None:
        t_ret = np.abs(tr.retarder_loss_curve(np.abs(w_prop) * tr.retarder_ratio)) * tr.retarder_ratio
    t_out = t_prop + t_ret
    # the part of the output torque that accelerates the vehicle mass (VECTO: vehicleMass * a * v / w_out)
    with np.errstate(divide="ignore", invalid="ignore"):
        acc_torque_out = np.where(w_prop > 1e-6, mass * a * v / w_prop, 0.0)
    # rolling with the driveline open: road load plus the idle drag of axle and retarder
    _, _, axle_idle = _stage_input(tr.axle_loss_map, 0, tr.axle_ratio, tr.axle_efficiency, w_wheel, np.zeros(n))
    f_drv = (np.abs(axle_idle) + t_ret) * tr.axle_ratio / r
    a_roll = -(f_res + np.where(moving, f_drv, 0.0)) / (mass + m_wheels)

    ratios = np.asarray(tr.gear_ratios, dtype=float)
    ng = ratios.size
    limits = [float(x) for x in tr.gear_max_torque_nm] if tr.gear_max_torque_nm else None
    idle = float(eng.idle_speed_rads)
    fx, fy = np.asarray(eng.full_load_curve.x, dtype=float), np.asarray(eng.full_load_curve.y, dtype=float)
    drag_y = -np.abs(np.asarray(eng.drag_curve.y, dtype=float))
    char0 = engine_characteristics(fx, fy, drag_y)
    chars = []
    for g in range(ng):
        chars.append(engine_characteristics(fx, np.minimum(fy, limits[g]), drag_y) if limits else char0)

    shape = (n, ng)
    N, W, TI, TT, TJ, TF, TD, FT, FO, AE = (np.zeros(shape) for _ in range(10))
    inertia = float(eng.inertia_kgm2) + float(tr.gearbox_inertia_kgm2)
    # mean slope over the next 100 m, for the acceleration estimate (VECTO GearboxLookahead)
    s = np.concatenate(([0.0], np.cumsum(v[:-1] * dt)))
    z = np.concatenate(([0.0], np.cumsum(v[:-1] * dt * np.sin(theta[:-1]))))
    slope = (np.interp(s + 100.0, s, z) - z) / 100.0
    p_res_est = (f_aero + f_roll + mass * G * np.sin(slope)) * v + np.abs(axle_loss) * np.abs(w_prop)
    primary_ret = tr.retarder_type == "transmission_input" and tr.retarder_loss_curve is not None
    corr = float(eng.correction_factor)
    for g in range(ng):
        i_g = float(ratios[g])
        w_in, t_in, _ = _stage_input(tr.gearbox_loss_map, g + 1, i_g, tr.gearbox_efficiency, w_prop, t_out)
        if primary_ret:
            t_in = t_in + np.abs(tr.retarder_loss_curve(np.abs(w_in) * tr.retarder_ratio)) * tr.retarder_ratio
        w_e = np.maximum(w_in, idle)
        t_full = eng.full_load_curve(w_e)
        if limits:
            t_full = np.minimum(t_full, limits[g])
        t_drag = -np.abs(eng.drag_curve(w_e))
        t_tot = t_in + p_acc / w_e
        N[:, g], W[:, g], TI[:, g], TT[:, g] = w_in, w_e, t_in, t_tot
        TJ[:, g] = inertia * (a / r) * tr.axle_ratio * i_g
        TF[:, g], TD[:, g] = t_full, t_drag
        FT[:, g] = np.maximum(eng.fuel_map(w_e, np.clip(t_tot, t_drag, t_full)), 0.0) * corr
        FO[:, g] = np.maximum(eng.fuel_map(w_e, np.clip(t_in, t_drag, t_full)), 0.0) * corr
        # acceleration the gear could give at full load (VECTO EstimateAccelerationForGear)
        t_fl = eng.full_load_curve(w_in)
        if limits:
            t_fl = np.minimum(t_fl, limits[g])
        _, _, loss_fl = _stage_input(tr.gearbox_loss_map, g + 1, i_g, tr.gearbox_efficiency, w_prop, t_fl * i_g)
        p_avail = t_fl * w_in - np.abs(loss_fl) * w_in - p_res_est
        with np.errstate(divide="ignore", invalid="ignore"):
            AE[:, g] = np.where(moving, p_avail / np.maximum(v, 0.1) / (mass + m_wheels), 0.0)

    return DrivelineTable(
        vehicle=vehicle, dt_s=dt, time_s=t, speed_mps=v, accel_mps2=a, grade_rad=theta, mass_kg=mass, moving=moving,
        p_acc_w=p_acc, w_prop=w_prop, t_out=t_out, acc_torque_out=acc_torque_out, a_roll=a_roll,
        n=N, w_eng=W, t_in=TI, t_tot=TT, t_inert=TJ, t_full=TF, t_drag=TD, fc_tot=FT, fc_out=FO, a_est=AE,
        ratios=ratios, axle_ratio=float(tr.axle_ratio), tyre_radius_m=float(r), idle=idle, rated=char0.rated_speed,
        chars=chars, char0=char0, fld=(fx, fy), limits=limits, m_wheels=float(m_wheels), rho=float(rho))


# --------------------------------------------------------------------------- the strategy
DRIVING, COASTING, BRAKING = 2, 1, 0

REASONS = {
    "launch": "kalkış vitesi", "engage": "yeniden kavrama", "emergency": "acil (devir sınırı)",
    "polygon_down": "küçültme hattı", "polygon_up": "büyütme hattı", "eff_down": "verim (küçültme)",
    "eff_up": "verim (büyütme)", "brake_down": "fren küçültmesi", "kickdown": "talep karşılanmıyor",
    "halt": "duruşa yaklaşma",
}


@dataclass
class ShiftPlan:
    gear: np.ndarray                # 0-based gear per sample; -1 while the vehicle stands
    clutch_open: np.ndarray         # moving with the clutch open (rolling to a halt)
    events: list                    # {"i", "t_s", "from", "to", "reason", "rpm", "speed_kmh"}; gears 1-based
    polygons: list
    max_start_gear: int             # 1-based
    setpoints: ShiftSetPoints

    @property
    def shifts(self) -> int:
        return sum(1 for e in self.events if e["reason"] not in ("launch", "engage"))

    def reason_counts(self) -> dict:
        out: dict = {}
        for e in self.events:
            out[e["reason"]] = out.get(e["reason"], 0) + 1
        return out


def max_start_gear(tb: DrivelineTable, sp: ShiftSetPoints) -> int:
    """Highest gear whose engine speed at the start speed is above the clutch
    closing speed (VECTO ``MaxStartGear``), 0-based."""
    n_clutch = (tb.rated - tb.idle) * sp.clutch_closing_speed_norm + tb.idle
    k = sp.start_speed_kmh * KMH * tb.axle_ratio / tb.tyre_radius_m
    g_max = 0
    for g in range(tb.n_gears - 1, -1, -1):
        if k * float(tb.ratios[g]) > n_clutch:
            g_max = g
            break
    if sp.launch_gear_cap > 0:
        g_max = min(g_max, int(sp.launch_gear_cap) - 1)
    return max(g_max, 0)


def plan_gears(tb: DrivelineTable, sp: ShiftSetPoints | None = None, polygons: list | None = None) -> ShiftPlan:
    """Gear for every sample of the trace."""
    sp = sp or ShiftSetPoints()
    veh = tb.vehicle
    tr, eng, ch = veh.transmission, veh.engine, veh.chassis
    N = tb.time_s.size
    ng = tb.n_gears
    last = ng - 1
    polys, be_l, ad_l, au_l, id_l = tb.polygon_tables(sp, polygons)

    # plain lists: the loop below indexes them several times per sample
    L = tb.lists()
    t_l, v_l, a_l, mov, to_l = L["time_s"], L["speed_mps"], L["accel_mps2"], L["moving"], L["t_out"]
    n_l, we_l = L["n"], L["w_eng"]
    ti_l, tt_l, tj_l = L["t_in"], L["t_tot"], L["t_inert"]
    tf_l, td_l = L["t_full"], L["t_drag"]
    ft_l, fo_l, ae_l = L["fc_tot"], L["fc_out"], L["a_est"]
    ratios = [float(x) for x in tb.ratios]
    r = tb.tyre_radius_m
    k_tr = tb.axle_ratio / r
    idle = tb.idle
    n_clutch = (tb.rated - idle) * sp.clutch_closing_speed_norm + idle
    n_high = [min(tb.chars[g].n_95h, tb.char0.n_95h) - 1.0 * RPM for g in range(ng)]
    fx, fy = tb.fld
    t_int = max(float(sp.traction_interruption_s), 0.0)
    k_delay = max(1, int(round(t_int / tb.dt_s))) if t_int > 0 else 0
    v_post = np.maximum(tb.speed_mps + tb.a_roll * t_int, 0.0)
    v_post_l = v_post.tolist()
    v_min_ti = sp.min_speed_after_traction_interruption_kmh * KMH
    v_dis = sp.disengage_when_halting_speed_kmh * KMH
    r_up = sp.ratio_early_upshift_fc / tb.axle_ratio
    r_dn = sp.ratio_early_downshift_fc / tb.axle_ratio
    rng_fc = max(1, int(sp.allowed_gear_range_fc))
    n_post_min = sp.min_engine_speed_post_upshift_rpm * RPM
    brake_n = sp.brake_downshift_speed_rpm * RPM
    brake_n_max = sp.brake_downshift_max_speed_rpm * RPM
    brake_decel = -abs(sp.brake_downshift_min_decel_mps2)
    skip_dn = max(1, int(sp.max_skip_down))
    g_start_max = max_start_gear(tb, sp)
    inf = float("inf")
    start_cache: dict = {}

    def gear_limit(g):
        return tb.limits[g] if tb.limits else inf

    def full_load(w, g):
        return min(float(np.interp(w, fx, fy)), gear_limit(g))

    def start_gear(i) -> int:
        """VECTO ``InitStartGear``: from the highest start gear downwards, the
        first one that keeps ``start_torque_reserve`` in hand while pulling
        away with the start acceleration."""
        v0 = sp.start_speed_kmh * KMH
        m = float(tb.mass_kg[i])
        key = (round(m, 0), round(float(tb.grade_rad[i]), 3), round(float(tb.p_acc_w[i]), -2))
        if key in start_cache:
            return start_cache[key]
        start_cache[key] = g0 = _start_gear(i, v0, m)
        return g0

    def _start_gear(i, v0, m) -> int:
        f = ch.resistive_force(v0, mass_kg=m, grade_rad=float(tb.grade_rad[i]), air_density=tb.rho)
        force = float(f["rolling"]) + float(f["aero"]) + float(f["grade"]) + (m + tb.m_wheels) * sp.start_acceleration_mps2
        w_prop0, t_prop0, _ = tr.axle_input(v0 / r, force * r)
        t_out0 = t_prop0 + tr.retarder_drag_nm(w_prop0)
        for g in range(g_start_max, -1, -1):
            w_in = w_prop0 * ratios[g]
            if w_in > tb.rated or w_in <= 0:
                continue
            _, t_in0, _ = tr.gearbox_input(w_prop0, t_out0, g)
            w_e = max(w_in, n_clutch)
            reserve = 1.0 - (t_in0 + float(tb.p_acc_w[i]) / w_e) / max(full_load(w_e, g), 1.0)
            if w_e > idle and reserve >= sp.start_torque_reserve:
                return g
        return 0

    def init_gear_moving(i) -> int:
        """VECTO ``InitGear`` for a vehicle that is already rolling."""
        for g in range(last, -1, -1):
            reserve = 1.0 - tt_l[i][g] / max(tf_l[i][g], 1.0)
            if not be_l[i][g] and not au_l[i][g] and reserve >= sp.start_torque_reserve:
                if (n_l[i][g] - idle) / max(tb.rated - idle, 1e-9) < sp.clutch_closing_speed_norm and g > 0:
                    return g - 1
                return g
            if au_l[i][g] and g < last:
                return g + 1
        return 0

    def early_upshift(i, cur, g_act):
        """VECTO ``CheckEarlyUpshift``: the most fuel-efficient of the next
        gears, if it is better than the current one by the rating factor."""
        if ti_l[i][g_act] < sp.drag_margin_factor * td_l[i][g_act]:
            return cur
        if not v_post_l[i] > v_min_ti:
            return cur
        v_now = v_l[i]
        v_ps = v_now - (v_now - v_post_l[i]) * sp.velocity_drop_factor
        best, min_fc, fc_cur = cur, inf, None
        for c in range(cur + 1, min(cur + rng_fc, last) + 1):
            if not ratios[c] < r_up:
                continue
            if be_l[i][c]:
                continue
            n_post = v_ps * k_tr * ratios[c]
            if n_post < n_post_min:
                continue
            if not tt_l[i][c] * we_l[i][c] < full_load(n_post, c) * n_post:
                continue
            t_full_c = max(tf_l[i][c], 1.0)
            reserve = 1.0 - tt_l[i][c] / t_full_c
            fc_c = ft_l[i][c]
            if reserve < sp.torque_reserve:
                n_cur = n_l[i][cur]
                c0 = tb.char0
                if n_cur < c0.n_tq98h or c0.n_p98h <= c0.n_tq98h:
                    continue
                af = 1.0 + (sp.acceleration_factor - 1.0) * (n_cur - c0.n_tq98h) / (c0.n_p98h - c0.n_tq98h)
                if abs(af - 1.0) < 1e-9:
                    continue
                reduced = float(tb.t_out[i]) - float(tb.acc_torque_out[i]) * (1.0 - af)
                _, t_in_r, _ = tr.gearbox_input(float(tb.w_prop[i]), reduced, c)
                t_tot_r = t_in_r + float(tb.p_acc_w[i]) / we_l[i][c]
                reserve = 1.0 - t_tot_r / t_full_c
                if reserve < sp.torque_reserve:
                    continue
                fc_c = max(float(eng.fuel_map(we_l[i][c], min(max(t_tot_r, td_l[i][c]), tf_l[i][c]))), 0.0) * eng.correction_factor
            elif sp.kickdown and tt_l[i][c] + tj_l[i][c] > tf_l[i][c] * (1.0 + sp.kickdown_overload_share):
                continue                    # it would be kicked straight back down
            if fc_cur is None:
                fc_cur = ft_l[i][cur]
            if fc_c < fc_cur * sp.rating_factor_current_gear and fc_c < min_fc:
                best, min_fc = c, fc_c
        return best

    def early_downshift(i, g):
        """VECTO ``CheckEarlyDownshift``."""
        if not v_post_l[i] > v_min_ti:
            return g
        if ti_l[i][g] < sp.drag_margin_factor * td_l[i][g]:
            return g
        best, min_fc, fc_cur = g, inf, fo_l[i][g]
        for c in range(g - 1, max(g - rng_fc, 0) - 1, -1):
            if not ratios[c] <= r_dn:
                continue
            if au_l[i][c]:
                continue
            fc_c = fo_l[i][c]
            if fc_c < fc_cur * sp.rating_factor_current_gear and fc_c < min_fc:
                best, min_fc = c, fc_c
        return best

    def decide(i, g, state, last_up, last_down, t_eng):
        t = t_l[i]
        # --- emergency: engine speed outside its limits ---------------------
        c = g
        while c < last and n_l[i][c] >= n_high[c]:
            c += 1
        if c != g:
            return c, "emergency"
        if t - t_eng < sp.time_between_gearshifts_s:
            return g, ""
        brake_demand = tt_l[i][g] < td_l[i][g]
        # --- downshift ------------------------------------------------------
        if t - last_up >= sp.downshift_after_upshift_delay_s and g > 0:
            if be_l[i][g]:
                c, k = g - 1, 1
                while k < skip_dn and c > 0 and be_l[i][c]:
                    c -= 1
                    k += 1
                return c, "polygon_down"
            if (brake_n > 0 and brake_demand and a_l[i] <= brake_decel and n_l[i][g] < brake_n
                    and n_l[i][g - 1] <= brake_n_max):
                c, k = g - 1, 1
                while k < skip_dn and c > 0 and n_l[i][c] < brake_n and n_l[i][c - 1] <= brake_n_max:
                    c -= 1
                    k += 1
                return c, "brake_down"
            c = early_downshift(i, g)
            if c != g:
                return c, "eff_down"
        # --- upshift --------------------------------------------------------
        if state == DRIVING and t - last_down >= sp.upshift_after_downshift_delay_s and g < last:
            cur, reason = g, "eff_up"
            if au_l[i][g]:
                cur, reason = g + 1, "polygon_up"
                while cur < last:
                    c = cur + 1
                    max_t = min(tf_l[i][c] * we_l[i][c] / ((we_l[i][g] + we_l[i][c]) / 2.0), id_l[i][c])
                    if max_t > 0 and 1.0 - ti_l[i][c] / max_t >= 0 and ad_l[i][c]:
                        cur = c
                        continue
                    break
            if cur < last:
                cur = early_upshift(i, cur, g)
            if cur != g:
                if ae_l[i][cur] < sp.upshift_min_acceleration_mps2:
                    if cur - g == 1 or ae_l[i][g + 1] < sp.upshift_min_acceleration_mps2:
                        cur = g
                    else:
                        cur = g + 1
                if cur != g:
                    return cur, reason
        # --- the trace is a demand: a gear that cannot deliver it is left ----
        if sp.kickdown and state == DRIVING and g > 0:
            over = tt_l[i][g] + tj_l[i][g] - tf_l[i][g]
            if over > sp.kickdown_overload_share * tf_l[i][g]:
                best, p_best = None, tf_l[i][g] * we_l[i][g]
                for c in range(g - 1, max(g - 4, -1), -1):
                    if n_l[i][c] >= n_high[c]:
                        break
                    if tt_l[i][c] + tj_l[i][c] <= tf_l[i][c]:
                        best = c
                        break
                    if tf_l[i][c] * we_l[i][c] > p_best * 1.02:
                        best, p_best = c, tf_l[i][c] * we_l[i][c]
                if best is not None:
                    return best, "kickdown"
        return g, ""

    gear = np.full(N, -1, dtype=int)
    clutch_open = np.zeros(N, dtype=bool)
    events: list = []
    g = None
    launching = False
    pending = None                  # (gear, sample index at which it is engaged, reason, decided at)
    t_eng = last_up = last_down = -inf
    prev_state = None
    floor = 0

    def log(i, g_from, g_to, reason):
        events.append({"i": i, "t_s": t_l[i], "from": (g_from + 1) if g_from is not None else 0, "to": g_to + 1,
                       "reason": reason, "rpm": (n_l[i][g_from] / RPM) if g_from is not None else idle / RPM,
                       "speed_kmh": v_l[i] * 3.6})

    for i in range(N):
        if not mov[i]:
            g, launching, pending, prev_state = None, False, None, None
            t_eng = last_up = last_down = -inf
            continue
        t = t_l[i]
        if g is None:
            from_rest = (i > 0 and not mov[i - 1]) or v_l[i] < sp.start_speed_kmh * KMH
            g = start_gear(i) if from_rest else init_gear_moving(i)
            launching = from_rest
            floor = g if from_rest else 0
            t_eng = t
            log(i, None, g, "launch" if from_rest else "engage")
        state = DRIVING
        if a_l[i] <= -sp.driver_decel_threshold_mps2:
            state = BRAKING if tt_l[i][g] < td_l[i][g] else COASTING
        if prev_state is not None and state != prev_state and state != COASTING and pending is None:
            last_up = last_down = -inf          # VECTO resets the shift delays when the driver changes mode
        prev_state = state

        if pending is not None and i >= pending[1]:
            g_new = pending[0]
            # VECTO Engage(): correct the gear chosen a moment ago for the speed the vehicle has now
            while g_new > 0 and n_l[i][g_new] < idle and state == DRIVING:
                g_new -= 1
            while g_new < last and n_l[i][g_new] >= n_high[g_new]:
                g_new += 1
            if g_new > g:
                last_up = t
            elif g_new < g:
                last_down = t
            g, t_eng, pending = g_new, t, None

        if n_l[i][g] < idle and to_l[i] <= 0.0 and v_l[i] < v_dis:
            # rolling to a halt (VECTO: disengage when halting): no traction is
            # asked for and the engine cannot be turned this slowly, so the
            # clutch is open and the gear follows the speed down to the start gear
            if pending is None:
                if floor == 0:
                    floor = min(start_gear(i), g)
                c = g
                while c > floor and n_l[i][c] < idle:
                    c -= 1
                if c != g:
                    log(i, g, c, "halt")
                    g = c
            clutch_open[i] = True
            launching = False
            gear[i] = g
            continue
        if n_l[i][g] < idle and not launching and pending is None:
            # VECTO's emergency downshift: the gear that keeps the engine turning
            c = g
            while c > 0 and n_l[i][c] < idle:
                c -= 1
            if n_l[i][c] < idle:
                if to_l[i] > 0.0:
                    c = min(start_gear(i), g)       # too slow for any gear: pull away again on the clutch
                    launching = True
                else:
                    clutch_open[i] = True
            if c != g:
                log(i, g, c, "emergency")
                g, t_eng, last_down = c, t, t
            if clutch_open[i]:
                gear[i] = g
                continue
        if launching:
            if n_l[i][g] >= n_clutch:
                launching = False
            else:
                gear[i] = g
                continue
        if pending is None:
            floor = 0
            c, reason = decide(i, g, state, last_up, last_down, t_eng)
            if c != g:
                log(i, g, c, reason)
                if k_delay == 0:
                    if c > g:
                        last_up = t
                    else:
                        last_down = t
                    g, t_eng = c, t
                else:
                    pending = (c, i + k_delay, reason, i)
        gear[i] = g

    return ShiftPlan(gear=gear, clutch_open=clutch_open, events=events, polygons=polys,
                     max_start_gear=g_start_max + 1, setpoints=sp)


def setpoints_of(transmission) -> ShiftSetPoints:
    return ShiftSetPoints.from_dict(getattr(transmission, "shift_setpoints", None))


def plan_for_cycle(vehicle, cycle, environment, p_acc_w, *, rho: float | None = None,
                   setpoints: ShiftSetPoints | None = None) -> ShiftPlan:
    """Table plus plan for an already resampled cycle: what the diesel solver calls."""
    tb = build_table(vehicle, cycle, environment, p_acc_w, rho=rho)
    sp = setpoints or setpoints_of(vehicle.transmission)
    return plan_gears(tb, for_trace(sp, cycle))


def for_trace(sp: ShiftSetPoints, cycle) -> ShiftSetPoints:
    """Set points as they apply to this trace. A measured trace was driven by
    the real vehicle, so whatever it asks for was delivered in the gear the
    real box chose: when the model finds the demand above full load there,
    the model's road load is off, and answering with a kick-down would hide
    that behind a wrong gear. On a generated trace the kick-down stays."""
    if sp.kickdown and (getattr(cycle, "meta", None) or {}).get("measured"):
        return ShiftSetPoints.from_dict({**sp.to_dict(), "kickdown": False})
    return sp
