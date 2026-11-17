"""Multi-speed gearbox, axle and retarder for a combustion powertrain.

The electric ``Driveline`` models a fixed ratio with a constant efficiency,
which is honest for a direct-drive e-bus. A diesel coach has a 12-speed AMT
whose losses depend on gear, speed and load, an axle with its own measured
loss map, and a retarder that drags a little even when it is off. VECTO
certifies each of those as a torque-loss map on the input side of the stage,
and this component keeps that form so a certificate can be loaded without
re-fitting anything.

Power flows (backward-facing) wheel -> axle -> retarder drag -> gearbox ->
engine. Each stage adds its loss torque to what the stage after it demands::

    T_in = T_out / i + T_loss(n_in, T_in)

which is implicit in ``T_in``; two fixed-point passes are enough because the
loss is a small fraction of the torque.

**Shift strategy.** An AMT shifts by its own rules, not the driver's, so the
model carries them: an engine-speed window, a torque reserve and a minimum
time between shifts. The highest gear that keeps the engine inside the window
with ``torque_reserve`` still in hand is chosen; the box stays in its current
gear while that gear is still acceptable, so it does not hunt. This is the
structure of VECTO's AMT strategy with its tuning constants exposed as fields;
calibrate the window against a logged engine-speed trace before quoting a
fuel figure on a hilly route, where the choice of gear is most of the answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..maps import Curve1D, LossMap
from ..units import RPM


@dataclass
class Transmission:
    name: str = "unnamed_transmission"
    transmission_type: str = "AMT"
    manufacturer: str = ""
    model: str = ""
    certification_number: str = ""

    # --- gearbox -------------------------------------------------------------
    gear_ratios: list = field(default_factory=lambda: [16.688, 12.924, 9.926, 7.688, 5.895, 4.565,
                                                       3.655, 2.831, 2.174, 1.684, 1.291, 1.0])
    gear_max_torque_nm: list | None = None
    """Input torque limit per gear from the certificate; the engine is clipped
    to it in that gear."""

    gearbox_loss_map: LossMap | None = None
    """Per-gear torque loss at the input shaft. Replaces the constant
    efficiency when present."""

    gearbox_efficiency: float = 0.96
    gearbox_inertia_kgm2: float = 0.0

    # --- axle ------------------------------------------------------------------
    axle_ratio: float = 2.0
    axle_loss_map: LossMap | None = None
    """Torque loss at the propshaft (axle input)."""
    axle_efficiency: float = 0.96

    # --- retarder ----------------------------------------------------------------
    retarder_type: str = "none"
    """``none``, ``transmission_output`` (secondary retarder on the propshaft
    side of the gearbox, e.g. ZF Intarder) or ``transmission_input`` (primary
    retarder on the engine side)."""
    retarder_ratio: float = 1.0
    retarder_loss_curve: Curve1D | None = None
    """Drag torque [Nm] of the idling retarder against its own shaft speed
    [rad/s]. VECTO certifies this; it is not the braking torque."""

    # --- shift strategy ----------------------------------------------------------
    downshift_speed_rads: float = 800.0 * RPM
    upshift_speed_rads: float = 1500.0 * RPM
    """Engine-speed window the strategy tries to stay in."""
    torque_reserve: float = 0.10
    """Share of full-load torque kept in hand when choosing a gear; the box
    does not upshift into a gear that would need more than (1 − reserve) of
    the full-load curve."""
    min_shift_interval_s: float = 2.0
    start_speed_mps: float = 2.2
    """Below this road speed the box is in its launch gear."""
    eco_upshift_speed_rads: float = 950.0 * RPM
    """At partial load the box may shift up early, as long as the new gear
    keeps the engine at or above this speed. This is what puts a coach in top
    gear at ~1000 rpm on the motorway without ever letting the engine lug at
    the downshift line in town."""
    partial_load_share: float = 0.60
    """"Partial load" for the early upshift: the input torque the new gear
    needs is at most this share of the full-load torque at its engine speed."""
    lug_speed_rads: float = 875.0 * RPM
    """Under load the box shifts down before the engine falls to this speed;
    the line slides towards ``downshift_speed_rads`` as the load gets lighter,
    so a coach cruising at ~1000 rpm in top gear is left alone while a bus
    pulling away in a tall gear at 830 rpm is not."""
    launch_gear: int = 3
    """The gear the box pulls away in on a normal start (1-based). Heavier
    starts drop to 2 or 1 (the AMT's clutch-protection logic); light ones may
    go up to ``launch_gear_max``."""
    launch_gear_max: int = 5

    source: str = "unspecified"
    validated: bool = False
    notes: str = ""

    # ----------------------------------------------------------------------
    @property
    def n_gears(self) -> int:
        return len(self.gear_ratios)

    def total_ratio(self, gear: int) -> float:
        return float(self.gear_ratios[gear]) * self.axle_ratio

    def referred_inertia_kgm2(self, gear: int, engine_inertia_kgm2: float) -> float:
        """Engine and gearbox inertia referred to the wheel axis."""
        return (engine_inertia_kgm2 + self.gearbox_inertia_kgm2) * self.total_ratio(gear) ** 2

    # --- stages -----------------------------------------------------------
    def axle_input(self, wheel_speed_rads: float, wheel_torque_nm: float):
        """Propshaft speed, torque and loss torque for a wheel demand."""
        w_in = wheel_speed_rads * self.axle_ratio
        if self.axle_loss_map is not None:
            t_in = wheel_torque_nm / self.axle_ratio
            loss = 0.0
            for _ in range(3):
                loss = self.axle_loss_map(abs(w_in), t_in, 0)
                t_in = wheel_torque_nm / self.axle_ratio + loss
            return w_in, t_in, loss
        eta = self.axle_efficiency
        if wheel_torque_nm >= 0:
            t_in = wheel_torque_nm / (self.axle_ratio * eta)
        else:
            t_in = wheel_torque_nm * eta / self.axle_ratio
        return w_in, t_in, t_in - wheel_torque_nm / self.axle_ratio

    def gearbox_input(self, out_speed_rads: float, out_torque_nm: float, gear: int):
        """Engine-side speed, torque and loss torque for an output demand."""
        i = float(self.gear_ratios[gear])
        w_in = out_speed_rads * i
        if self.gearbox_loss_map is not None:
            t_in = out_torque_nm / i
            loss = 0.0
            for _ in range(3):
                loss = self.gearbox_loss_map(abs(w_in), t_in, gear + 1)
                t_in = out_torque_nm / i + loss
            return w_in, t_in, loss
        eta = self.gearbox_efficiency
        if out_torque_nm >= 0:
            t_in = out_torque_nm / (i * eta)
        else:
            t_in = out_torque_nm * eta / i
        return w_in, t_in, t_in - out_torque_nm / i

    def retarder_drag_nm(self, out_speed_rads: float) -> float:
        """Idle drag of the retarder, as a positive torque at the gearbox output."""
        if self.retarder_type == "none" or self.retarder_loss_curve is None:
            return 0.0
        if self.retarder_type == "transmission_input":
            return 0.0      # handled on the engine side by the caller
        w_ret = abs(out_speed_rads) * self.retarder_ratio
        return abs(float(self.retarder_loss_curve(w_ret))) * self.retarder_ratio

    def retarder_input_drag_nm(self, engine_speed_rads: float) -> float:
        if self.retarder_type != "transmission_input" or self.retarder_loss_curve is None:
            return 0.0
        w_ret = abs(engine_speed_rads) * self.retarder_ratio
        return abs(float(self.retarder_loss_curve(w_ret))) * self.retarder_ratio

    # --- shift strategy -----------------------------------------------------
    def choose_gear(self, out_speed_rads: float, out_torque_nm: float, engine,
                    current: int, since_shift_s: float, speed_mps: float) -> int:
        """Gear index for this step. ``engine`` supplies idle speed and the
        full-load curve; ``current`` is the gear in use."""
        n = self.n_gears
        lo, hi = self.downshift_speed_rads, self.upshift_speed_rads

        def n_eng(g):
            return out_speed_rads * float(self.gear_ratios[g])

        def torque_ok(g):
            w = max(n_eng(g), engine.idle_speed_rads)
            _, t_in, _ = self.gearbox_input(out_speed_rads, out_torque_nm, g)
            limit = engine.max_torque(w)
            if self.gear_max_torque_nm:
                limit = min(limit, float(self.gear_max_torque_nm[g]))
            return t_in <= (1.0 - self.torque_reserve) * limit

        def share(g):
            """Input torque the gear needs as a share of the full-load torque there."""
            w = max(n_eng(g), engine.idle_speed_rads)
            _, t_in, _ = self.gearbox_input(out_speed_rads, out_torque_nm, g)
            limit = engine.max_torque(w)
            if self.gear_max_torque_nm:
                limit = min(limit, float(self.gear_max_torque_nm[g]))
            return t_in / max(limit, 1.0)

        # launch: the standard launch gear, one or two lower when the start is
        # heavy (laden, uphill), up to launch_gear_max when it is light. Judged
        # on the torque the gear would need at the start speed.
        if speed_mps < self.start_speed_mps and out_torque_nm > 0:
            w_ref = self.start_speed_mps / max(speed_mps, 0.3) * out_speed_rads if speed_mps > 0.3 else out_speed_rads

            def launch_share(g):
                w = max(w_ref * float(self.gear_ratios[g]), engine.idle_speed_rads)
                _, t_in, _ = self.gearbox_input(w_ref, out_torque_nm, g)
                limit = engine.max_torque(w)
                if self.gear_max_torque_nm:
                    limit = min(limit, float(self.gear_max_torque_nm[g]))
                return t_in / max(limit, 1.0)
            g = min(max(int(self.launch_gear), 1), n) - 1
            while g > 0 and launch_share(g) > 1.0 - self.torque_reserve:
                g -= 1
            top = min(max(int(self.launch_gear_max), 1), n) - 1
            while g < top and launch_share(g + 1) <= 0.25:
                g += 1
            return g

        window = [g for g in range(n) if lo <= n_eng(g) <= hi]
        if not window:
            # Nothing keeps the engine inside the window: take the gear whose
            # speed is nearest to it.
            if n_eng(n - 1) > hi:
                return n - 1
            if n_eng(0) < lo:
                return 0
            return min(range(n), key=lambda g: min(abs(n_eng(g) - lo), abs(n_eng(g) - hi)))

        if out_torque_nm <= 0.0:
            # coasting or braking: hold the gear while it is in the window
            if current in window:
                return current
            return max(window) if n_eng(current) > hi else min(window)

        good = [g for g in window if torque_ok(g)]
        eco = self.eco_upshift_speed_rads
        if current in good:
            # Hold the gear (hysteresis), except for the early upshift at
            # partial load. Skipping gears is allowed: the highest gear that
            # satisfies the eco rules is taken, as an AMT does on a light bus.
            if since_shift_s < self.min_shift_interval_s:
                return current
            lug = lo + (self.lug_speed_rads - lo) * min(1.0, share(current) / max(self.partial_load_share, 1e-6))
            if n_eng(current) < lug:
                # lugging under load (coasted down into the gear): the smallest
                # downshift that brings the engine back above the eco line
                down = [g for g in good if g < current and n_eng(g) >= eco]
                return max(down) if down else current
            up = [g for g in good if g > current and n_eng(g) >= eco and share(g) <= self.partial_load_share]
            return max(up) if up else current
        if good:
            if n_eng(current) > hi or current >= n:
                # over-revving: up to the highest gear that still keeps the
                # engine at the eco line, never straight down to the lug line
                cand = [g for g in good if n_eng(g) >= eco] or good
                return max(cand)
            # lugging or short of torque: the smallest downshift that restores both
            return max(good)
        # Even the lowest gear in the window lacks torque: drop one more
        # gear, accepting a higher engine speed, before declaring a shortfall.
        target = min(window)
        if target > 0 and n_eng(target - 1) <= 1.08 * hi:
            target -= 1
        return target

    def describe(self) -> str:
        flag = "VALIDATED" if self.validated else "UNVALIDATED"
        who = f"{self.manufacturer} {self.model}".strip() or self.name
        maps = ("ölçülmüş kayıp haritası" if self.gearbox_loss_map is not None else "sabit verim")
        ret = "" if self.retarder_type == "none" else f", retarder {self.retarder_type} x{self.retarder_ratio:g}"
        return (f"{who}: {self.transmission_type} {self.n_gears} vites "
                f"({self.gear_ratios[0]:.2f}…{self.gear_ratios[-1]:.2f}) x aks {self.axle_ratio:.2f}, "
                f"{maps}{ret} [{flag}]")
