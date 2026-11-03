"""The component library: YAML in, validated dataclasses out.

The whole point of this module is that adding a motor to the simulator is a
matter of adding ``library/motor/my_motor.yaml``. Nobody edits Python to
describe hardware.

Three conveniences make the YAML pleasant to write, and each exists to prevent
a specific class of mistake:

**Engineering units.** The solver is strictly SI, but nobody specifies a motor
in rad/s. Any field ending in a canonical SI suffix also accepts an
engineering-unit alias -- write ``peak_power_kw: 250`` and it becomes
``peak_power_w: 250000``. Unit confusion is the most common and most
embarrassing error in vehicle simulation, so the conversion happens in one
audited place instead of in every data file.

**References.** A vehicle names its components by file path, so one motor
definition serves every variant that uses it. Fixing a motor map fixes every
bus that shares it, which is the behaviour you want and the opposite of what
happens when data is copied into each vehicle file.

**Strict validation.** An unknown key is an error, not a silent no-op. A
typo like ``peak_torque_Nm`` would otherwise leave the default 2500 Nm quietly
in place and produce a plausible, wrong answer -- the worst possible failure
mode for a tool people make decisions with.
"""

from __future__ import annotations

import csv
import dataclasses
import pathlib
from typing import Any

import numpy as np
import yaml

from .components.aging import AgingModel
from .components.auxiliary import Auxiliaries
from .components.battery import BatteryPack
from .components.chassis import Chassis
from .components.driveline import Driveline
from .components.engine import Engine
from .components.hvac import HVAC
from .components.motor import Motor
from .components.transmission import Transmission
from .maps import Curve1D, LossMap, Map2D
from .units import KWH, MPH, RPM
from .vehicle import ControlStrategy, Vehicle

# Canonical SI suffix -> accepted aliases and their multipliers.
UNIT_ALIASES: dict[str, list[tuple[str, float]]] = {
    "_w": [("_kw", 1e3), ("_mw", 1e6)],
    "_j": [("_kj", 1e3), ("_mj", 1e6), ("_kwh", KWH)],
    "_rads": [("_rpm", RPM)],
    "_mps": [("_kmh", 1.0 / 3.6), ("_mph", MPH)],
    "_kg": [("_t", 1e3), ("_tonnes", 1e3)],
    "_ohm": [("_mohm", 1e-3)],
    "_m2": [("_m2", 1.0)],
    "_j_per_k": [("_kj_per_k", 1e3)],
    "_j_per_kgk": [("_kj_per_kgk", 1e3)],
    "_j_per_mol": [("_kj_per_mol", 1e3)],
}

KINDS: dict[str, type] = {
    "chassis": Chassis,
    "driveline": Driveline,
    "motor": Motor,
    "battery": BatteryPack,
    "hvac": HVAC,
    "auxiliary": Auxiliaries,
    "auxiliaries": Auxiliaries,
    "aging": AgingModel,
    "engine": Engine,
    "transmission": Transmission,
    "control": ControlStrategy,
    "vehicle": Vehicle,
}

VEHICLE_COMPONENT_FIELDS = {
    "chassis": "chassis",
    "driveline": "driveline",
    "motor": "motor",
    "battery": "battery",
    "hvac": "hvac",
    "auxiliaries": "auxiliary",
    "aging": "aging",
    "engine": "engine",
    "transmission": "transmission",
}


class LibraryError(ValueError):
    """Raised with the offending file and field named, always."""


def _alias_table(cls) -> dict[str, tuple[str, float]]:
    """Map every accepted alias key to (canonical field, multiplier)."""
    table: dict[str, tuple[str, float]] = {}
    fields = {f.name for f in dataclasses.fields(cls)}
    for name in fields:
        for suffix, aliases in UNIT_ALIASES.items():
            if not name.endswith(suffix):
                continue
            base = name[: -len(suffix)]
            if base.endswith("_per"):
                # e.g. coastdown_b_n_per_mps is N/(m/s), not a speed. Offering a
                # _kmh alias would convert a coefficient as if it were a speed,
                # and in the wrong direction.
                continue
            for alias_suffix, factor in aliases:
                alias = base + alias_suffix
                if alias == name or alias in fields:
                    continue
                if alias in table and table[alias][0] != name:
                    raise LibraryError(
                        f"{cls.__name__}: alias {alias!r} is ambiguous between "
                        f"{table[alias][0]!r} and {name!r}")
                table[alias] = (name, factor)
    return table


def _field_types(cls) -> dict[str, Any]:
    return {f.name: f.type for f in dataclasses.fields(cls)}


def _coerce_curve(value: Any, base_dir: pathlib.Path, field_name: str,
                  where: str) -> Curve1D:
    if isinstance(value, Curve1D):
        return value
    if isinstance(value, str):
        path = (base_dir / value).resolve()
        arr = np.loadtxt(path, delimiter=",", skiprows=1)
        return Curve1D(arr[:, 0], arr[:, 1], name=field_name,
                       source=f"csv:{path.name}")
    if isinstance(value, dict):
        if "path" in value:
            # A CSV with named columns: {path, columns: [x, y], x_unit: rpm}.
            # Lets one file (speed, max torque, drag torque) feed two curves,
            # and keeps the file in the unit people read it in.
            path = (base_dir / value["path"]).resolve()
            if not path.exists():
                raise LibraryError(f"{where}: curve file not found: {path}")
            with path.open(newline="", encoding="utf-8") as fh:
                rows = [r for r in csv.reader(fh) if r and not r[0].lstrip().startswith("#")]
            header = [h.strip() for h in rows[0]]
            cols = value.get("columns") or header[:2]
            try:
                ix, iy = header.index(cols[0]), header.index(cols[1])
            except ValueError as exc:
                raise LibraryError(f"{where}: {field_name}: column {exc} not in {path.name} "
                                   f"(has {header})") from exc
            arr = np.array([[float(r[ix]), float(r[iy])] for r in rows[1:]])
            kx = RPM if (value.get("x_unit") == "rpm" or "rpm" in cols[0].lower()) else 1.0
            return Curve1D(arr[:, 0] * kx, arr[:, 1] * float(value.get("y_scale", 1.0)),
                           name=field_name, source=value.get("source", f"csv:{path.name}"))
        pairs = value.get("points") or value.get("pairs")
        src = value.get("source", "inline")
        if pairs is None:
            raise LibraryError(f"{where}: {field_name} needs 'points' or a CSV path")
        c = Curve1D.from_pairs(pairs, name=field_name, source=src)
        if value.get("x_unit") == "rpm":
            c = Curve1D(c.x * RPM, c.y, name=c.name, source=c.source)
        return c
    if isinstance(value, (list, tuple)):
        return Curve1D.from_pairs(value, name=field_name, source="inline")
    raise LibraryError(f"{where}: cannot read {field_name} as a curve")


def _coerce_map(value: Any, base_dir: pathlib.Path, field_name: str,
                where: str) -> Map2D:
    if isinstance(value, Map2D):
        return value
    if isinstance(value, str):
        path = (base_dir / value).resolve()
        if not path.exists():
            raise LibraryError(f"{where}: map file not found: {path}")
        return _rpm_axis_to_si(Map2D.from_csv(path, name=field_name))
    if isinstance(value, dict):
        if "path" in value:
            path = (base_dir / value["path"]).resolve()
            if not path.exists():
                raise LibraryError(f"{where}: map file not found: {path}")
            m = Map2D.from_csv(path, name=field_name)
            if "source" in value:
                m.source = value["source"]
            return _rpm_axis_to_si(m, force=value.get("x_unit") == "rpm")
        return Map2D(np.array(value["x"]), np.array(value["y"]),
                     np.array(value["z"]), name=field_name,
                     source=value.get("source", "inline"))
    raise LibraryError(f"{where}: cannot read {field_name} as a map")


def _rpm_axis_to_si(m: Map2D, force: bool = False) -> Map2D:
    """A grid whose corner label says the speed axis is in rpm is converted
    to rad/s on the way in, so files stay readable and the solver stays SI."""
    if force or "rpm" in (m.x_label or "").lower():
        return Map2D(m.x * RPM, m.y, m.z, name=m.name, source=m.source,
                     x_label="speed_rad_s", y_label=m.y_label)
    return m


def _coerce_lossmap(value: Any, base_dir: pathlib.Path, field_name: str,
                    where: str) -> LossMap:
    if isinstance(value, LossMap):
        return value
    if isinstance(value, dict) and "path" in value:
        src, value = value.get("source"), value["path"]
    else:
        src = None
    if isinstance(value, str):
        path = (base_dir / value).resolve()
        if not path.exists():
            raise LibraryError(f"{where}: loss map file not found: {path}")
        m = LossMap.from_csv(path, name=field_name)
        if src:
            m.source = src
        return m
    raise LibraryError(f"{where}: cannot read {field_name} as a loss map (give a CSV path)")


def build(cls, data: dict, *, base_dir: pathlib.Path, where: str):
    """Construct a dataclass from a plain dict, applying aliases and checks."""
    data = dict(data or {})
    data.pop("kind", None)
    data.pop("description", None)

    aliases = _alias_table(cls)
    types = _field_types(cls)
    kwargs: dict[str, Any] = {}

    for key, value in data.items():
        if key in types:
            field_name, factor = key, 1.0
        elif key in aliases:
            field_name, factor = aliases[key]
            if field_name in data:
                raise LibraryError(
                    f"{where}: {key!r} and {field_name!r} both given; pick one")
        else:
            close = [f for f in types if f.startswith(key.split("_")[0])]
            hint = f" Did you mean one of {close}?" if close else ""
            raise LibraryError(f"{where}: unknown field {key!r}.{hint}")

        ftype = types[field_name]
        tname = ftype if isinstance(ftype, str) else getattr(ftype, "__name__", str(ftype))

        if value is None:
            kwargs[field_name] = None
        elif "Curve1D" in str(tname):
            kwargs[field_name] = _coerce_curve(value, base_dir, field_name, where)
        elif "LossMap" in str(tname):
            kwargs[field_name] = _coerce_lossmap(value, base_dir, field_name, where)
        elif "Map2D" in str(tname):
            kwargs[field_name] = _coerce_map(value, base_dir, field_name, where)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            converted = float(value) * factor
            # Keep integer fields integral: cells_series and seats are counts,
            # and a float count produces confusing output downstream.
            if str(tname).strip() == "int":
                if abs(converted - round(converted)) > 1e-9:
                    raise LibraryError(
                        f"{where}: {key!r} must be a whole number, got {value!r}")
                converted = int(round(converted))
            kwargs[field_name] = converted
        else:
            if factor != 1.0:
                raise LibraryError(
                    f"{where}: {key!r} needs a number to convert units, got {value!r}")
            kwargs[field_name] = value

    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise LibraryError(f"{where}: {exc}") from exc


class Library:
    """Loads component and vehicle definitions from a directory tree."""

    def __init__(self, root: str | pathlib.Path = "library"):
        self.root = pathlib.Path(root).resolve()
        if not self.root.exists():
            raise LibraryError(f"library root does not exist: {self.root}")
        self._cache: dict[pathlib.Path, Any] = {}

    # ----------------------------------------------------------------------
    def _resolve(self, ref: str, kind: str | None = None) -> pathlib.Path:
        """Turn a reference into a path. Accepts ``motor/foo.yaml``, ``foo.yaml``
        or a bare ``foo`` when the kind is known."""
        cand = [self.root / ref, self.root / f"{ref}.yaml", pathlib.Path(ref)]
        if kind:
            cand += [self.root / kind / ref, self.root / kind / f"{ref}.yaml"]
        for p in cand:
            if p.exists() and p.is_file():
                return p.resolve()
        searched = "\n  ".join(str(p) for p in cand)
        raise LibraryError(f"cannot resolve reference {ref!r}. Looked in:\n  {searched}")

    def _read_yaml(self, path: pathlib.Path) -> dict:
        try:
            with path.open(encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise LibraryError(f"{path}: invalid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise LibraryError(f"{path}: expected a mapping at the top level")
        return data

    # ----------------------------------------------------------------------
    def load_component(self, ref: str | dict, kind: str):
        """Load one component. ``ref`` is a path, or a dict with ``ref`` plus
        ``overrides``, or an inline definition."""
        overrides: dict = {}
        if isinstance(ref, dict):
            if "ref" in ref:
                overrides = dict(ref.get("overrides") or {})
                ref = ref["ref"]
            else:
                cls = KINDS[kind]
                return build(cls, ref, base_dir=self.root, where=f"inline {kind}")

        path = self._resolve(str(ref), kind)
        data = self._read_yaml(path)
        declared = data.get("kind")
        cls = KINDS[declared] if declared in KINDS else KINDS[kind]
        if declared and declared not in KINDS:
            raise LibraryError(f"{path}: unknown kind {declared!r}")
        data = {**data, **overrides}
        data.setdefault("source", f"library:{path.relative_to(self.root).as_posix()}")
        return build(cls, data, base_dir=path.parent, where=str(path))

    def load_vehicle(self, ref: str) -> Vehicle:
        """Load a vehicle and everything it references."""
        path = self._resolve(str(ref), "vehicle")
        data = self._read_yaml(path)
        if data.get("kind", "vehicle") != "vehicle":
            raise LibraryError(f"{path}: expected kind: vehicle")
        data = dict(data)
        data.pop("kind", None)

        components: dict[str, Any] = {}
        for field_name, kind in VEHICLE_COMPONENT_FIELDS.items():
            if field_name in data:
                components[field_name] = self.load_component(data.pop(field_name), kind)
        if "control" in data:
            components["control"] = build(
                ControlStrategy, data.pop("control"),
                base_dir=path.parent, where=f"{path} -> control")

        data.setdefault("source", f"library:{path.relative_to(self.root).as_posix()}")
        veh = build(Vehicle, {**data, **components},
                    base_dir=path.parent, where=str(path))
        return veh

    # ----------------------------------------------------------------------
    def list_kind(self, kind: str) -> list[str]:
        d = self.root / kind
        if not d.exists():
            return []
        return sorted(p.stem for p in d.glob("*.yaml"))

    def catalogue(self) -> dict[str, list[str]]:
        return {k: self.list_kind(k) for k in
                ("vehicle", "motor", "battery", "hvac", "driveline",
                 "chassis", "auxiliary", "aging", "engine", "transmission", "route")}

    def load_cycle(self, ref: str):
        from .cycle import DriveCycle
        for cand in (self.root / "cycle" / f"{ref}.csv", self.root / "cycle" / ref,
                     pathlib.Path(ref)):
            if cand.exists():
                return DriveCycle.from_csv(cand)
        raise LibraryError(f"cannot find cycle {ref!r} in {self.root / 'cycle'}")

    def list_cycles(self) -> list[str]:
        d = self.root / "cycle"
        return sorted(p.stem for p in d.glob("*.csv")) if d.exists() else []
