"""VECTO bridge: run the EU reference simulator headless and compare with ours.

VECTO (Vehicle Energy Consumption calculation TOol, European Commission JRC,
EUPL) is the certification simulator for heavy-duty CO2. A local release is
expected next to the repository; it is not part of the repository.

What this module does:

* ``find_vecto()``      locates ``vectocmd.exe``.
* ``write_vdri()``      writes one of our routes as a VECTO distance-based
                        driving cycle (``<s>,<v>,<grad>,<stop>``).
* ``run_job()``         runs a ``.vecto`` job in engineering mode with modal
                        output and returns the parsed ``.vmod``/``.vsum``.
* ``read_vmod()``       parses the per-step modal results into numpy arrays.
* ``cycle_from_vmod()`` turns VECTO's own driven speed trace into a
                        ``DriveCycle``, so our solver can be run on exactly the
                        trace VECTO drove.
* ``compare_roadload()`` runs our chassis model on that trace and reports the
                        energy of every road-load term next to VECTO's.

Why road load first: it is the part both tools must agree on regardless of
powertrain. Once rolling, air, grade and inertia energies match on the same
trace, every remaining difference is in the powertrain and auxiliaries, which
is where the engineering discussion belongs.
"""

from __future__ import annotations

import csv
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

import numpy as np

from .cycle import DriveCycle, Route
from .units import KWH, RHO_AIR_STD, G

VECTO_DIRNAME = "2026_09_03-VECTO-5.1.4"
VECTO_RHO_AIR = 1.188   # kg/m3, VECTO's declaration air density (20 deg C, 1 bar)


# ----------------------------------------------------------------------
# locating and running VECTO
# ----------------------------------------------------------------------
def find_vecto(start: pathlib.Path | None = None) -> pathlib.Path | None:
    """Return the path of ``vectocmd.exe``, or None if no release is around."""
    env = os.environ.get("VECTO_CMD")
    if env and pathlib.Path(env).exists():
        return pathlib.Path(env)
    here = pathlib.Path(start or pathlib.Path(__file__).resolve().parents[1])
    for base in [here, *here.parents]:
        for cand in base.glob(f"{VECTO_DIRNAME}*/**/vectocmd.exe"):
            if cand.parent.name.startswith("net"):
                continue                       # the per-runtime copies; use the launcher
            return cand
    return None


def vecto_release_dir(cmd: pathlib.Path) -> pathlib.Path:
    return cmd.parent


def generic_vehicle_dir(cmd: pathlib.Path, name: str) -> pathlib.Path:
    """A folder under 'Generic Vehicles' of the release (searched recursively)."""
    root = vecto_release_dir(cmd) / "Generic Vehicles"
    for p in root.rglob(name):
        if p.is_dir():
            return p
    raise FileNotFoundError(f"VECTO generic vehicle not found: {name}")


@dataclass
class VectoRun:
    job: pathlib.Path
    workdir: pathlib.Path
    stdout: str
    vsum: list[dict]
    vmod: dict[str, "Modal"]           # keyed by cycle name


@dataclass
class Modal:
    """One ``.vmod`` file: column name -> float array (text columns kept as object)."""
    columns: list[str]
    data: dict[str, np.ndarray]
    path: pathlib.Path

    def __getitem__(self, key: str) -> np.ndarray:
        return self.data[key]

    def col(self, prefix: str) -> np.ndarray:
        """Column by the part before the unit bracket, e.g. ``col("P_roll")``."""
        for c in self.columns:
            if c.split(" [")[0].strip() == prefix:
                return self.data[c]
        raise KeyError(prefix)

    @property
    def dt(self) -> np.ndarray:
        return self.col("dt")

    def energy_kwh(self, prefix: str) -> float:
        """Integrate a kW column over dt -> kWh."""
        return float(np.sum(self.col(prefix) * 1e3 * self.dt) / KWH)


def read_vmod(path: pathlib.Path) -> Modal:
    rows: list[list[str]] = []
    with open(path, encoding="utf-8-sig") as f:
        header = None
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split(",")
            if header is None:
                header = [p.strip() for p in parts]
                continue
            rows.append(parts)
    assert header is not None, f"no header in {path}"
    data: dict[str, np.ndarray] = {}
    for j, name in enumerate(header):
        colvals = [r[j] if j < len(r) else "" for r in rows]
        try:
            data[name] = np.array([float(v) if v != "" else np.nan for v in colvals])
        except ValueError:
            data[name] = np.array(colvals, dtype=object)
    return Modal(header, data, path)


def read_vsum(path: pathlib.Path) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        lines = [l for l in f if l.strip() and not l.startswith("#")]
    rd = csv.DictReader(lines)
    return [dict(r) for r in rd]


def run_job(job: pathlib.Path, cmd: pathlib.Path | None = None, *, timeout_s: float = 600) -> VectoRun:
    """Run a ``.vecto`` job in engineering mode with modal output.

    VECTO writes next to the job file, so the job folder should be a scratch
    copy (see ``stage_generic``)."""
    cmd = cmd or find_vecto()
    if cmd is None:
        raise FileNotFoundError("vectocmd.exe not found; put the VECTO release next to the repo or set VECTO_CMD")
    job = pathlib.Path(job).resolve()
    proc = subprocess.run([str(cmd), "-eng", "-mod", job.name], cwd=job.parent, capture_output=True,
                          text=True, timeout=timeout_s)
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(f"VECTO failed ({proc.returncode}):\n{out[-2000:]}")
    stem = job.stem
    vsum_path = job.parent / f"{stem}.vsum"
    vsum = read_vsum(vsum_path) if vsum_path.exists() else []
    vmod = {}
    for p in job.parent.glob(f"{stem}_*.vmod"):
        vmod[p.stem[len(stem) + 1:]] = read_vmod(p)
    if not vmod:
        raise RuntimeError(f"VECTO produced no .vmod next to {job}:\n{out[-2000:]}")
    return VectoRun(job, job.parent, out, vsum, vmod)


def stage_generic(cmd: pathlib.Path, name: str, workdir: pathlib.Path | None = None) -> pathlib.Path:
    """Copy a generic vehicle folder of the release into a scratch folder."""
    src = generic_vehicle_dir(cmd, name)
    dst = pathlib.Path(workdir or tempfile.mkdtemp(prefix="vecto_")) / name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return dst


def set_job_cycles(job: pathlib.Path, cycles: list[str]) -> None:
    """Point a ``.vecto`` job at the given cycle files (relative to the job)."""
    d = json.loads(pathlib.Path(job).read_text(encoding="utf-8-sig"))
    d["Body"]["Cycles"] = list(cycles)
    pathlib.Path(job).write_text(json.dumps(d, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------
# our route -> VECTO driving cycle
# ----------------------------------------------------------------------
def write_vdri(route: Route, path: pathlib.Path, *, stop_s: float | None = None,
               speed_limit_kmh: float | None = None) -> pathlib.Path:
    """Write a VECTO distance-based cycle from one of our routes.

    Columns: ``<s>`` distance [m], ``<v>`` target speed [km/h], ``<grad>`` [%],
    ``<stop>`` stop time [s]. VECTO's own driver then decides acceleration,
    coasting and braking, so the driven trace is VECTO's, not ours."""
    d = np.asarray(route.distance_m, dtype=float)
    e = np.asarray(route.elevation_m, dtype=float)
    v = np.asarray(route.speed_limit_mps, dtype=float) * 3.6
    if speed_limit_kmh is not None:
        v = np.full_like(v, float(speed_limit_kmh))
    grad = np.gradient(e, d) * 100.0
    stops = set(float(s) for s in route.stop_positions_m)
    dwell = route.stop_dwell_s if stop_s is None else float(stop_s)
    # VECTO wants a stop entry with v=0 handled by the stop time column; the
    # target speed stays the limit, the <stop> column holds the dwell.
    rows = []
    for k in range(len(d)):
        s_here = float(d[k])
        st = dwell if any(abs(s_here - s) < 1e-6 for s in stops) else 0.0
        rows.append((s_here, float(v[k]), float(grad[k]), st))
    if rows and rows[0][3] == 0.0:
        rows[0] = (rows[0][0], rows[0][1], rows[0][2], 0.0)
    path = pathlib.Path(path)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("<s>,<v>,<grad>,<stop>\n")
        for s, vv, g, st in rows:
            f.write(f"{s:.1f},{vv:.2f},{g:.3f},{st:.1f}\n")
    return path


# ----------------------------------------------------------------------
# VECTO's driven trace -> our DriveCycle, and the road-load comparison
# ----------------------------------------------------------------------
def cycle_from_vmod(m: Modal, name: str = "vecto") -> DriveCycle:
    """VECTO's actual per-step speed and grade as a time-based DriveCycle.

    VECTO's rows are intervals of length ``dt`` with mean speed ``v_act``; we
    place a sample at each interval end so that the integrated distance
    matches VECTO's ``dist``."""
    t_end = np.cumsum(m.dt)
    t = np.concatenate([[0.0], t_end])
    v = np.concatenate([[0.0], m.col("v_act") / 3.6])
    grad = np.concatenate([[m.col("grad")[0]], m.col("grad")]) / 100.0
    doors = np.concatenate([[False], m.col("v_act") < 0.01])
    return DriveCycle(t, v, np.arctan(grad), doors_open=doors, name=name, source="vecto .vmod")


@dataclass
class RoadLoadComparison:
    terms: dict[str, tuple[float, float]] = field(default_factory=dict)   # term -> (ours kWh, vecto kWh)
    distance_km: tuple[float, float] = (0.0, 0.0)
    notes: list[str] = field(default_factory=list)

    def rel(self, term: str) -> float:
        a, b = self.terms[term]
        return (a - b) / abs(b) if abs(b) > 1e-9 else float("nan")

    def table(self) -> str:
        out = [f"{'term':<10}{'ours kWh':>12}{'VECTO kWh':>12}{'diff':>9}"]
        for k, (a, b) in self.terms.items():
            out.append(f"{k:<10}{a:>12.4f}{b:>12.4f}{100 * self.rel(k):>8.1f}%")
        out.append(f"distance km: ours {self.distance_km[0]:.3f}  VECTO {self.distance_km[1]:.3f}")
        return "\n".join(out)


def compare_roadload(m: Modal, chassis, *, mass_kg: float, wheel_inertia_kgm2: float | None = None,
                     air_density: float = VECTO_RHO_AIR) -> RoadLoadComparison:
    """Run our chassis road load on VECTO's own trace and compare the energies.

    VECTO modal columns used: ``P_roll``, ``P_air``, ``P_slope``,
    ``P_veh_inertia`` + ``P_wheel_inertia`` (all kW, positive = resisting)."""
    cyc = cycle_from_vmod(m)
    dt = m.dt
    v = m.col("v_act") / 3.6                       # interval mean speed
    acc = m.col("acc")
    theta = np.arctan(m.col("grad") / 100.0)
    forces = chassis.resistive_force(v, mass_kg=mass_kg, grade_rad=theta, air_density=air_density)
    rot = wheel_inertia_kgm2 if wheel_inertia_kgm2 is not None else chassis.rotational_inertia_kgm2
    m_eq = rot / chassis.tyre_dynamic_radius_m ** 2
    p_roll = np.asarray(forces["rolling"]) * v
    p_air = np.asarray(forces["aero"]) * v
    p_grade = np.asarray(forces["grade"]) * v
    p_inertia = mass_kg * acc * v
    p_wheel_inertia = m_eq * acc * v
    kwh = lambda p: float(np.sum(p * dt) / KWH)
    pos = lambda p: float(np.sum(np.clip(p, 0, None) * dt) / KWH)
    vk = lambda name: m.col(name) * 1e3
    c = RoadLoadComparison()
    c.terms["rolling"] = (kwh(p_roll), m.energy_kwh("P_roll"))
    c.terms["air"] = (kwh(p_air), m.energy_kwh("P_air"))
    # grade and inertia change sign along the cycle and net to about zero on a
    # loop, so the positive part (energy the powertrain had to supply) is compared
    c.terms["grade+"] = (pos(p_grade), pos(vk("P_slope")))
    c.terms["inertia+"] = (pos(p_inertia), pos(vk("P_veh_inertia")))
    c.terms["wheel_in+"] = (pos(p_wheel_inertia), pos(vk("P_wheel_inertia")))
    tot = p_roll + p_air + p_grade + p_inertia + p_wheel_inertia
    vtot = vk("P_roll") + vk("P_air") + vk("P_slope") + vk("P_veh_inertia") + vk("P_wheel_inertia")
    c.terms["wheel+"] = (pos(tot), pos(vtot))        # positive tractive energy at the wheel
    c.terms["wheel-"] = (-float(np.sum(np.clip(tot, None, 0) * dt) / KWH),
                         -float(np.sum(np.clip(vtot, None, 0) * dt) / KWH))   # braking energy at the wheel
    c.distance_km = (float(cyc.distance_m) / 1000.0, float(m.col("dist")[-1]) / 1000.0)
    c.notes.append(f"air density {air_density} kg/m3 (VECTO declaration value), mass {mass_kg:.0f} kg")
    return c


def vecto_vehicle_params(vveh_path: pathlib.Path) -> dict:
    """The road-load relevant numbers of a VECTO ``.vveh`` file."""
    d = json.loads(pathlib.Path(vveh_path).read_text(encoding="utf-8-sig"))["Body"]
    axles = d.get("AxleConfig", {}).get("Axles", [])
    total = float(d.get("CurbWeight", 0)) + float(d.get("CurbWeightExtra", 0)) + float(d.get("Loading", 0))
    rrc = sum(float(a.get("RRCISO", 0)) * float(a.get("AxleWeightShare", 0)) for a in axles)
    # VECTO's load-dependent correction (VehicleData.cs ComputeRollResistanceAndReducedMassWheels):
    #   RRC_total = sum over axles of share * RRC_ISO * (Fz_wheel / Fz_ISO)^(0.9 - 1)
    # with Fz_wheel = share * m * g / wheels on the axle. Lighter than the ISO test
    # load means a slightly higher coefficient, heavier means lower.
    rrc_corr = 0.0
    for a in axles:
        share, fz_iso = float(a.get("AxleWeightShare", 0)), float(a.get("FzISO", 1))
        if share <= 0 or fz_iso <= 0:
            continue
        n_wheels = 4 if a.get("TwinTyres") else 2
        fz = total * G * share / n_wheels
        rrc_corr += share * float(a.get("RRCISO", 0)) * (fz / fz_iso) ** (0.9 - 1.0)
    inertia = sum(float(a.get("Inertia", 0)) * (4 if a.get("TwinTyres") else 2) for a in axles)
    return {"mass_kg": total, "rrc_share_weighted": rrc, "rrc_load_corrected": rrc_corr,
            "cda_m2": float(d.get("CdA", 0)), "rdyn_m": float(d.get("rdyn", 0)) / 1000.0,
            "wheel_inertia_kgm2": inertia, "n_axles": len(axles)}
