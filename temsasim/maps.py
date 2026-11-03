"""Component maps: lookup tables and synthetic map generation.

There are two ways to describe a component's behaviour in this library:

1.  A *measured* map -- a CSV grid from a dynamometer run or a supplier data
    sheet. Use this for anything you quote a number on to a customer.
2.  A *synthetic* map generated from a physical loss model and calibrated to a
    handful of catalogue figures. Use it while you wait for real data, and
    leave the component marked ``validated: false``.

Every map carries a ``source`` string, and that string travels with the map
into the results. A report can therefore always state where its efficiency
numbers came from, which is the difference between a simulation someone acts
on and one they argue about.
"""

from __future__ import annotations

import csv
import pathlib
from dataclasses import dataclass

import numpy as np


@dataclass
class Map2D:
    """Bilinear lookup over a rectangular grid, clamped at the edges.

    ``x`` and ``y`` must be strictly increasing. ``z`` has shape
    ``(len(y), len(x))`` -- row index is y, column index is x, i.e. the same
    orientation as a spreadsheet with speeds across the top and torques down
    the side.
    """

    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    name: str = ""
    source: str = "unspecified"
    x_label: str = "x"
    y_label: str = "y"

    def __post_init__(self) -> None:
        self.x = np.asarray(self.x, dtype=float)
        self.y = np.asarray(self.y, dtype=float)
        self.z = np.asarray(self.z, dtype=float)
        if self.z.shape != (self.y.size, self.x.size):
            raise ValueError(
                f"map {self.name!r}: z shape {self.z.shape} does not match "
                f"(len(y), len(x)) = ({self.y.size}, {self.x.size})"
            )
        if np.any(np.diff(self.x) <= 0) or np.any(np.diff(self.y) <= 0):
            raise ValueError(f"map {self.name!r}: x and y must be strictly increasing")

    def __call__(self, xq, yq):
        """Interpolate at (xq, yq), scalars or arrays.

        Queries outside the grid clamp to the edge value rather than
        extrapolating, because an extrapolated efficiency map produces
        confident nonsense.
        """
        xq = np.clip(np.asarray(xq, dtype=float), self.x[0], self.x[-1])
        yq = np.clip(np.asarray(yq, dtype=float), self.y[0], self.y[-1])

        i = np.clip(np.searchsorted(self.x, xq) - 1, 0, self.x.size - 2)
        j = np.clip(np.searchsorted(self.y, yq) - 1, 0, self.y.size - 2)

        x0, x1 = self.x[i], self.x[i + 1]
        y0, y1 = self.y[j], self.y[j + 1]
        tx = (xq - x0) / (x1 - x0)
        ty = (yq - y0) / (y1 - y0)

        top = self.z[j, i] * (1 - tx) + self.z[j, i + 1] * tx
        bot = self.z[j + 1, i] * (1 - tx) + self.z[j + 1, i + 1] * tx
        out = top * (1 - ty) + bot * ty
        return float(out) if np.ndim(out) == 0 else out

    @classmethod
    def from_csv(cls, path, name: str = "", source: str = "", **kw) -> "Map2D":
        """Read a grid CSV: first cell is a label, first row is x, first column
        is y, the interior is z. Lines starting with ``#`` are ignored."""
        path = pathlib.Path(path)
        with path.open(newline="") as fh:
            rows = [r for r in csv.reader(fh) if r and not r[0].lstrip().startswith("#")]
        x = np.array([float(v) for v in rows[0][1:]])
        y = np.array([float(r[0]) for r in rows[1:]])
        z = np.array([[float(v) for v in r[1:]] for r in rows[1:]])
        # The corner cell names the axes as "y_label / x_label"; keep the names
        # so a loader can tell an rpm axis from a rad/s one.
        corner = rows[0][0] if rows[0] else ""
        if "/" in corner:
            yl, xl = (p.strip() for p in corner.split("/", 1))
            kw.setdefault("x_label", xl)
            kw.setdefault("y_label", yl)
        return cls(x, y, z, name=name or path.stem,
                   source=source or f"csv:{path.name}", **kw)

    def to_csv(self, path) -> None:
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow([f"{self.y_label} / {self.x_label}"] + [f"{v:g}" for v in self.x])
            for j, yv in enumerate(self.y):
                w.writerow([f"{yv:g}"] + [f"{v:.5f}" for v in self.z[j]])


@dataclass
class LossMap:
    """Torque loss of a gear stage, looked up on the input side, per gear.

    This is the form VECTO certifies gearboxes and axles in: at an input speed
    [rad/s] and input torque [Nm] (negative when the wheels drive the engine)
    the stage loses ``loss`` Nm, so the input torque is ``T_out / i + loss``.
    One ``Map2D`` per gear; an axle or a single-ratio stage has only gear 0.
    Queries outside the measured grid clamp to its edge, like every other map.
    """

    gears: dict
    name: str = ""
    source: str = "unspecified"

    def __post_init__(self) -> None:
        self.gears = {int(k): v for k, v in self.gears.items()}
        if not self.gears:
            raise ValueError(f"loss map {self.name!r}: no gears")

    def __call__(self, speed_rads, torque_nm, gear: int = 0) -> float:
        m = self.gears.get(int(gear))
        if m is None:
            m = self.gears[min(self.gears)]
        return float(m(speed_rads, torque_nm))

    @property
    def gear_numbers(self) -> list:
        return sorted(self.gears)

    @classmethod
    def from_entries(cls, entries_by_gear: dict, *, speed_unit: str = "rpm",
                     name: str = "", source: str = "") -> "LossMap":
        """Build from ``{gear: [(speed, input_torque, loss), ...]}``.

        VECTO maps are rectangular (every speed is measured at every torque);
        if a cell is missing it is filled by interpolating along the torque axis
        at that speed, which is how VECTO itself treats sparse maps.
        """
        from .units import RPM
        k = RPM if speed_unit == "rpm" else 1.0
        gears = {}
        for g, entries in entries_by_gear.items():
            arr = np.asarray(entries, dtype=float)
            if arr.ndim != 2 or arr.shape[1] != 3 or arr.shape[0] < 4:
                raise ValueError(f"loss map {name!r} gear {g}: need (speed, torque, loss) rows")
            xs = np.unique(arr[:, 0]) * k
            ys = np.unique(arr[:, 1])
            z = np.full((ys.size, xs.size), np.nan)
            xi = np.searchsorted(xs, arr[:, 0] * k)
            yi = np.searchsorted(ys, arr[:, 1])
            z[yi, xi] = arr[:, 2]
            for i in range(xs.size):
                col = z[:, i]
                ok = np.isfinite(col)
                if not ok.any():
                    raise ValueError(f"loss map {name!r} gear {g}: no data at speed {xs[i]:.1f}")
                if not ok.all():
                    z[:, i] = np.interp(ys, ys[ok], col[ok])
            gears[int(g)] = Map2D(xs, ys, z, name=f"{name}:g{g}", source=source,
                                  x_label="input_speed_rad_s", y_label="input_torque_Nm")
        return cls(gears, name=name, source=source)

    @classmethod
    def from_csv(cls, path, name: str = "", source: str = "") -> "LossMap":
        """Long-format CSV: ``gear,input_speed_rpm,input_torque_nm,loss_nm``.
        The gear column may be omitted for a single-ratio stage."""
        path = pathlib.Path(path)
        with path.open(newline="") as fh:
            rows = [r for r in csv.reader(fh) if r and not r[0].lstrip().startswith("#")]
        header = [h.strip().lower() for h in rows[0]]
        has_gear = header and header[0].startswith("gear")
        by_gear: dict = {}
        for r in rows[1:]:
            vals = [float(v) for v in r if v.strip() != ""]
            if has_gear:
                g, rest = int(vals[0]), vals[1:]
            else:
                g, rest = 0, vals
            by_gear.setdefault(g, []).append(rest[:3])
        unit = "rpm" if any("rpm" in h for h in header) else "rad_s"
        return cls.from_entries(by_gear, speed_unit=unit, name=name or path.stem,
                                source=source or f"csv:{path.name}")

    def to_csv(self, path) -> None:
        from .units import RPM
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["gear", "input_speed_rpm", "input_torque_nm", "loss_nm"])
            for g in self.gear_numbers:
                m = self.gears[g]
                for i, xv in enumerate(m.x):
                    for j, yv in enumerate(m.y):
                        w.writerow([g, f"{xv / RPM:.2f}", f"{yv:.2f}", f"{m.z[j, i]:.3f}"])


@dataclass
class Curve1D:
    """Monotone 1-D lookup with edge clamping: OCV vs SOC, COP vs ambient, ..."""

    x: np.ndarray
    y: np.ndarray
    name: str = ""
    source: str = "unspecified"

    def __post_init__(self) -> None:
        self.x = np.asarray(self.x, dtype=float)
        self.y = np.asarray(self.y, dtype=float)
        order = np.argsort(self.x)
        self.x, self.y = self.x[order], self.y[order]

    def __call__(self, xq):
        out = np.interp(np.asarray(xq, dtype=float), self.x, self.y)
        return float(out) if np.ndim(out) == 0 else out

    @classmethod
    def from_pairs(cls, pairs, name: str = "", source: str = "") -> "Curve1D":
        arr = np.asarray(pairs, dtype=float)
        if arr.ndim != 2 or arr.shape[1] != 2:
            raise ValueError(f"curve {name!r}: expected a list of [x, y] pairs")
        return cls(arr[:, 0], arr[:, 1], name=name, source=source)


def synth_motor_efficiency(
    speed_rads,
    torque_nm,
    *,
    peak_efficiency: float,
    peak_at_speed_rads: float,
    peak_at_torque_nm: float,
    copper_frac: float = 0.45,
    iron_frac: float = 0.35,
    windage_frac: float = 0.10,
    constant_frac: float = 0.10,
) -> Map2D:
    """Build a plausible motor + inverter efficiency map from a loss model.

    Losses split into four physically-motivated terms::

        P_loss = k_cu * T**2  +  k_fe * w**1.5  +  k_w * w**3  +  P_const

    copper (I^2 R, hence torque squared), iron (hysteresis plus eddy current,
    roughly w^1.5), windage and bearing drag (roughly w^3), and a constant
    inverter/controller draw. The four ``*_frac`` arguments say how the loss
    budget splits *at the peak-efficiency point*; the absolute scale is then
    solved so the map passes through ``peak_efficiency`` exactly at
    (``peak_at_speed_rads``, ``peak_at_torque_nm``).

    This is a stand-in, not a measurement. Replace it with a dyno map before
    quoting a range figure.
    """
    fracs = np.array([copper_frac, iron_frac, windage_frac, constant_frac], dtype=float)
    if np.any(fracs < 0):
        raise ValueError("loss fractions must be non-negative")
    fracs = fracs / fracs.sum()
    if not 0.0 < peak_efficiency < 1.0:
        raise ValueError("peak_efficiency must be a fraction strictly between 0 and 1")

    w_pk, t_pk = float(peak_at_speed_rads), float(peak_at_torque_nm)
    p_mech_pk = w_pk * t_pk
    p_loss_pk = p_mech_pk * (1.0 / peak_efficiency - 1.0)

    # Each coefficient is sized so its term contributes its share of p_loss_pk.
    k_cu = fracs[0] * p_loss_pk / (t_pk ** 2)
    k_fe = fracs[1] * p_loss_pk / (w_pk ** 1.5)
    k_w = fracs[2] * p_loss_pk / (w_pk ** 3)
    p_const = fracs[3] * p_loss_pk

    w = np.asarray(speed_rads, dtype=float)
    t = np.asarray(torque_nm, dtype=float)
    W, T = np.meshgrid(w, t)  # shape (len(t), len(w))

    loss = k_cu * T ** 2 + k_fe * W ** 1.5 + k_w * W ** 3 + p_const
    p_mech = W * T
    eff = np.where(p_mech > 0, p_mech / (p_mech + loss), 0.0)
    eff = np.clip(eff, 0.0, 0.995)

    rpm_pk = w_pk * 60.0 / (2.0 * np.pi)
    return Map2D(
        w, t, eff,
        name="synthetic_motor_efficiency",
        source=(f"SYNTHETIC loss model, calibrated to peak eta={peak_efficiency:.3f} "
                f"at {rpm_pk:.0f} rpm / {t_pk:.0f} Nm"),
        x_label="speed_rad_s",
        y_label="torque_Nm",
    )
