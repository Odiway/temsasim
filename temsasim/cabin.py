"""Layered cabin thermal model: the cabin as surfaces with material layers.

The lumped HVAC model (``components/hvac.py``) sees the cabin as one air node
with a UA value, a glazing area and an interior thermal mass. Those three
numbers decide the climate energy of every run, and until now they were
typed in by hand. This module builds them from what the CAE team actually
models in CFD: each surface of the body (roof, side panels, windows, floor
over the engine, dashboard...) as a stack of material layers with thickness,
density, specific heat and conductivity, an outside boundary (ambient air at
30 W/m²K, or the 80 °C engine bay), radiative absorptivity and transmissivity,
plus the HVAC supply as mass flow and supply-air temperature.

It then runs the same transient the CFD runs -- a cabin soaked at 55 °C in a
40 °C Adana afternoon, A/C switched on, 40 minutes -- as a one-dimensional
multi-node conduction problem: every layer is split into cells, the air is one
node, seats and trims another, all solved implicitly (backward Euler) so a
3 mm FRP skin next to a 43 mm insulation blanket needs no tiny time step.
The result is compared with the CFD planes and the Maraton A/C test in the
interface, and the same layer stacks are collapsed into the UA / mass / SHGC
the lumped model needs (``lumped_equivalent``). That is the verified path from
the CAE thermal model into the simulator: one file, two uses, one source.

Solar load is a clear-sky model (ASHRAE A/B/C form) at the site, day and hour
the CFD used; it replaces Fluent's solar calculator, not a measurement.
"""

from __future__ import annotations

import math
import pathlib
import time
from dataclasses import asdict, dataclass, field

import numpy as np
import yaml

from .units import CP_AIR, air_density
from .vecto_xml import safe_name

ORIENTATIONS = {"roof": "Tavan", "side_left": "Sol yan", "side_right": "Sağ yan", "front": "Ön", "rear": "Arka",
                "floor": "Taban", "interior": "İç (güneş görmez)"}
OUTER_KINDS = {"ambient": "Dış hava", "fixed": "Sabit sıcaklık (ör. motor bölmesi)"}
LUMPED_RADIATIVE_FILM = 4.0
"""W/m²K added to the inner film when collapsing to the one-node model: the
layered solver keeps convection only (surface-to-surface radiation is not
modelled), the lumped model's single node stands for air and interior
together, so its inner film is convection + radiation (~4 + 4)."""

DEFAULT_MATERIALS = {
    # from the NGC Maraton CFD report, slide "Material properties" (A1319)
    "frp": {"label": "FRP (cam elyaf)", "density_kg_m3": 1400.0, "cp_j_kgk": 1130.0, "k_w_mk": 0.21},
    "insulation": {"label": "Yalıtım (cam yünü)", "density_kg_m3": 9.5, "cp_j_kgk": 2300.0, "k_w_mk": 0.035},
    "abs": {"label": "İç ABS kaplama", "density_kg_m3": 850.0, "cp_j_kgk": 1440.76, "k_w_mk": 0.188},
    "plywood": {"label": "Kontrplak", "density_kg_m3": 700.0, "cp_j_kgk": 1215.0, "k_w_mk": 0.12},
    "seat": {"label": "Koltuk", "density_kg_m3": 200.0, "cp_j_kgk": 1075.0, "k_w_mk": 0.05},
    "steel": {"label": "Çelik", "density_kg_m3": 8030.0, "cp_j_kgk": 502.48, "k_w_mk": 16.27},
    "glass": {"label": "Temperli cam", "density_kg_m3": 2500.0, "cp_j_kgk": 800.0, "k_w_mk": 0.8},
}


@dataclass
class Layer:
    material: str
    thickness_mm: float


@dataclass
class Surface:
    name: str
    area_m2: float
    layers: list                       # outer -> inner, Layer or dict
    orientation: str = "side_left"
    outer: str = "ambient"             # "ambient" or "fixed"
    outer_c: float = 80.0              # used when outer == "fixed"
    h_out_w_m2k: float = 30.0
    h_in_w_m2k: float = 8.0
    absorptivity: float = 0.8
    transmissivity: float = 0.0
    note: str = ""

    def __post_init__(self) -> None:
        self.layers = [l if isinstance(l, Layer) else Layer(str(l["material"]), float(l["thickness_mm"])) for l in self.layers]
        self.area_m2 = float(self.area_m2)
        if self.orientation not in ORIENTATIONS:
            raise ValueError(f"{self.name}: bilinmeyen yön {self.orientation!r}")
        if self.outer not in OUTER_KINDS:
            raise ValueError(f"{self.name}: dış sınır 'ambient' ya da 'fixed' olmalı")

    @property
    def glazed(self) -> bool:
        return self.transmissivity > 0.0


@dataclass
class Inlet:
    name: str
    mass_flow_kg_s: float
    supply_c: list                     # [[t_s, T_c], ...]; held before the first and after the last point
    count: int = 1

    def temperature(self, t_s: float) -> float:
        pts = self.supply_c
        if not pts:
            return 20.0
        ts = [p[0] for p in pts]; Ts = [p[1] for p in pts]
        return float(np.interp(t_s, ts, Ts))


@dataclass
class CabinModel:
    name: str = "Kabin"
    stem: str = ""
    description: str = ""
    materials: dict = field(default_factory=lambda: {k: dict(v) for k, v in DEFAULT_MATERIALS.items()})
    surfaces: list = field(default_factory=list)
    air_volume_m3: float = 60.0
    interior_mass_kg: float = 1000.0
    """Seats, trims, luggage racks: the mass the transmitted sun lands on and
    the air exchanges heat with. The NGC CFD meshes every seat; here they are
    one node."""
    interior_cp_j_kgk: float = 1075.0
    interior_contact_area_m2: float = 120.0
    interior_h_w_m2k: float = 8.0
    occupants: int = 50
    occupant_w: float = 100.0
    inlets: list = field(default_factory=list)
    infiltration_ach: float = 0.3
    location: dict = field(default_factory=lambda: {"lat": 37.0, "lon": 35.32, "day_of_year": 200, "hour_local": 15.0,
                                                    "utc_offset_h": 3.0, "heading_deg": 0.0})
    reference: dict = field(default_factory=dict)
    source: str = ""
    validated: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        self.stem = safe_name(self.stem or self.name)
        self.surfaces = [s if isinstance(s, Surface) else Surface(**s) for s in self.surfaces]
        self.inlets = [i if isinstance(i, Inlet) else Inlet(**i) for i in self.inlets]
        for s in self.surfaces:
            for l in s.layers:
                if l.material not in self.materials:
                    raise ValueError(f"{s.name}: malzeme tanımsız: {l.material}")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = "cabin"
        return d

    @classmethod
    def from_dict(cls, d: dict, stem: str = "") -> "CabinModel":
        d = dict(d); d.pop("kind", None)
        if stem:
            d["stem"] = stem
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    # ---------------------------------------------------------------- derived
    def surface_u(self, s: Surface, radiative_film: float = 0.0) -> float:
        """Overall coefficient outer air -> cabin air [W/m²K]. ``radiative_film``
        is added to the inner convective coefficient when the single-node lumped
        model is the consumer: there the interior radiates into the same node."""
        r = 1.0 / max(s.h_out_w_m2k, 1e-6) + 1.0 / max(s.h_in_w_m2k + radiative_film, 1e-6)
        for l in s.layers:
            r += (l.thickness_mm / 1000.0) / max(self.materials[l.material]["k_w_mk"], 1e-6)
        return 1.0 / r

    def surface_capacitance(self, s: Surface) -> float:
        """Heat capacity of all the layers of one surface [J/K]."""
        return sum(self.materials[l.material]["density_kg_m3"] * self.materials[l.material]["cp_j_kgk"]
                   * l.thickness_mm / 1000.0 * s.area_m2 for l in s.layers)

    def lumped_equivalent(self) -> dict:
        """The numbers the lumped HVAC model takes, derived from the layers."""
        ua_g = ua_o = a_g = a_o = 0.0
        cap = 0.0
        shgc_num = 0.0
        for s in self.surfaces:
            u = self.surface_u(s, radiative_film=LUMPED_RADIATIVE_FILM)
            cap += self.surface_capacitance(s)
            if s.outer != "ambient":
                # engine-bay floor: not part of the ambient UA; its loss is a steady source
                continue
            if s.glazed:
                ua_g += u * s.area_m2; a_g += s.area_m2
                shgc_num += s.area_m2 * (s.transmissivity + 0.3 * s.absorptivity)
            else:
                ua_o += u * s.area_m2; a_o += s.area_m2
        return {"ua_w_per_k": ua_g + ua_o, "glazing_area_m2": a_g, "opaque_area_m2": a_o,
                "u_glazing_w_per_m2k": ua_g / a_g if a_g else 0.0, "u_opaque_w_per_m2k": ua_o / a_o if a_o else 0.0,
                "shgc": shgc_num / a_g if a_g else 0.0,
                "interior_thermal_mass_j_per_k": cap + self.interior_mass_kg * self.interior_cp_j_kgk,
                "cabin_volume_m3": self.air_volume_m3,
                "envelope_capacitance_j_per_k": cap,
                "supply_mass_flow_kg_s": sum(i.mass_flow_kg_s * i.count for i in self.inlets)}


# ---------------------------------------------------------------- solar
def clear_sky(lat_deg: float, lon_deg: float, day_of_year: int, hour_local: float, utc_offset_h: float = 3.0) -> dict:
    """Sun position and clear-sky irradiance (ASHRAE A·exp(-B/sin β), C·I_dn).

    July coefficients (A = 1085 W/m², B = 0.207, C = 0.136) scaled lightly by
    the season; good to ~10 % on a clear summer afternoon, which is what the
    cooldown case is about. Returns altitude, azimuth (from north, clockwise),
    direct normal and diffuse horizontal irradiance."""
    n = int(day_of_year)
    decl = math.radians(23.45 * math.sin(math.radians(360.0 * (284 + n) / 365.0)))
    b = math.radians(360.0 * (n - 1) / 365.0)
    eot_min = 229.2 * (0.000075 + 0.001868 * math.cos(b) - 0.032077 * math.sin(b) - 0.014615 * math.cos(2 * b) - 0.04089 * math.sin(2 * b))
    solar_time = hour_local + eot_min / 60.0 + (lon_deg - 15.0 * utc_offset_h) / 15.0
    hour_angle = math.radians(15.0 * (solar_time - 12.0))
    lat = math.radians(lat_deg)
    sin_beta = math.cos(lat) * math.cos(decl) * math.cos(hour_angle) + math.sin(lat) * math.sin(decl)
    beta = math.asin(max(-1.0, min(1.0, sin_beta)))
    if beta <= 0.0:
        return {"altitude_deg": math.degrees(beta), "azimuth_deg": 0.0, "i_dn": 0.0, "i_dh": 0.0}
    cos_phi = (math.sin(beta) * math.sin(lat) - math.sin(decl)) / max(math.cos(beta) * math.cos(lat), 1e-9)
    phi = math.acos(max(-1.0, min(1.0, cos_phi)))          # from south
    az = 180.0 + math.degrees(phi) * (1 if hour_angle > 0 else -1)   # compass: 180 = south, afternoon -> west
    season = 1.0 - 0.06 * math.cos(math.radians(360.0 * (n - 200) / 365.0))   # A varies ~±6 % over the year
    a, bb, c = 1085.0 * season, 0.207, 0.136
    i_dn = a * math.exp(-bb / max(sin_beta, 0.02))
    return {"altitude_deg": math.degrees(beta), "azimuth_deg": az % 360.0, "i_dn": i_dn, "i_dh": c * i_dn}


def surface_irradiance(sun: dict, orientation: str, heading_deg: float, ground_albedo: float = 0.2) -> float:
    """Irradiance on one surface of a bus pointing ``heading_deg`` [W/m²]."""
    if sun["i_dn"] <= 0.0 or orientation in ("floor", "interior"):
        return 0.0
    beta = math.radians(sun["altitude_deg"])
    horiz = sun["i_dn"] * math.sin(beta) + sun["i_dh"]
    if orientation == "roof":
        return horiz
    face = {"front": 0.0, "rear": 180.0, "side_left": -90.0, "side_right": 90.0}[orientation]
    psi = (heading_deg + face) % 360.0
    cos_theta = math.cos(beta) * math.cos(math.radians(sun["azimuth_deg"] - psi))
    return sun["i_dn"] * max(0.0, cos_theta) + 0.5 * sun["i_dh"] + 0.5 * ground_albedo * horiz


# ---------------------------------------------------------------- transient
def simulate(model: CabinModel, *, ambient_c: float = 40.0, initial_c: float = 55.0, duration_s: float = 2400.0,
             dt_s: float = 10.0, solar: bool = True, hvac: bool = True, occupants: int | None = None,
             supply_offset_c: float = 0.0) -> dict:
    """Cool-down (or warm-up) of the soaked cabin with the HVAC supplying air.

    Every wall layer becomes one or more cells (5 mm or thinner, at most six
    per layer); the air and the interior mass are two more nodes. The system
    is advanced with backward Euler, so the step can be 10 s. Returns the
    time series of the air temperature, each surface's inner skin, the heat
    flows into the air, and the summary at 10/20/30/40 minutes.
    """
    occ = model.occupants if occupants is None else int(occupants)
    nodes: list = []            # (capacitance J/K)
    conds: list = []            # (i, j, G) between nodes
    bnd: list = []              # (i, G, kind, value) kind: "ambient" | "fixed" | "supply"
    src: list = []              # (i, kind, value) kind: "solar_out" (per surface) | "const"
    i_air = 0; nodes.append(air_density(ambient_c) * CP_AIR * model.air_volume_m3)
    i_int = 1; nodes.append(model.interior_mass_kg * model.interior_cp_j_kgk)
    conds.append((i_air, i_int, model.interior_h_w_m2k * model.interior_contact_area_m2))
    sun = {"i_dn": 0.0, "i_dh": 0.0, "altitude_deg": 0.0, "azimuth_deg": 0.0}
    if solar:
        loc = model.location
        sun = clear_sky(float(loc.get("lat", 37.0)), float(loc.get("lon", 35.32)), int(loc.get("day_of_year", 200)),
                        float(loc.get("hour_local", 15.0)), float(loc.get("utc_offset_h", 3.0)))
    heading = float(model.location.get("heading_deg", 0.0))
    surf_nodes: list = []       # (surface, outer node, inner node)
    transmitted_w = 0.0
    irr: dict = {}
    for s in model.surfaces:
        irr[s.name] = surface_irradiance(sun, s.orientation, heading) if solar else 0.0
        cells: list = []        # (node index, half resistance K/W on each side)
        first = None; prev = None; prev_half = 0.0
        for l in s.layers:
            m = model.materials[l.material]
            th = l.thickness_mm / 1000.0
            n_cells = max(1, min(6, int(math.ceil(th / 0.005))))
            dx = th / n_cells
            for _ in range(n_cells):
                idx = len(nodes)
                nodes.append(m["density_kg_m3"] * m["cp_j_kgk"] * dx * s.area_m2)
                half = dx / 2.0 / max(m["k_w_mk"], 1e-6) / s.area_m2        # K/W from cell centre to its face
                if prev is None:
                    first = (idx, half)
                else:
                    conds.append((prev, idx, 1.0 / (prev_half + half)))
                prev, prev_half = idx, half
                cells.append((idx, half))
        if first is None:
            continue
        o_idx, o_half = first
        g_out = 1.0 / (o_half + 1.0 / max(s.h_out_w_m2k, 1e-6) / s.area_m2)
        bnd.append((o_idx, g_out, s.outer, float(s.outer_c) if s.outer == "fixed" else 0.0))
        i_idx, i_half = cells[-1]
        conds.append((i_idx, i_air, 1.0 / (i_half + 1.0 / max(s.h_in_w_m2k, 1e-6) / s.area_m2)))
        # sun: absorbed on the outer skin, transmitted part lands on the interior mass
        src.append((o_idx, "const", s.absorptivity * irr[s.name] * s.area_m2 * (1.0 if s.outer == "ambient" else 0.0)))
        transmitted_w += s.transmissivity * irr[s.name] * s.area_m2
        surf_nodes.append((s, o_idx, i_idx))
    src.append((i_int, "const", transmitted_w))
    src.append((i_air, "const", occ * model.occupant_w))
    g_inf = model.infiltration_ach * model.air_volume_m3 / 3600.0 * air_density(ambient_c) * CP_AIR
    bnd.append((i_air, g_inf, "ambient", 0.0))
    if hvac:
        for inl in model.inlets:
            bnd.append((i_air, inl.mass_flow_kg_s * inl.count * CP_AIR, "supply", inl))

    N = len(nodes)
    C = np.asarray(nodes, dtype=float)
    K = np.zeros((N, N))
    for i, j, g in conds:
        K[i, i] += g; K[j, j] += g; K[i, j] -= g; K[j, i] -= g
    for i, g, kind, val in bnd:
        K[i, i] += g
    steps = int(round(duration_s / dt_s))
    T = np.full(N, float(initial_c))
    t_out = [0.0]; air = [float(initial_c)]; inner = {s.name: [float(initial_c)] for s, _, _ in surf_nodes}
    interior_c = [float(initial_c)]
    q_walls = [0.0]; q_supply = [0.0]; q_int = [0.0]; supply_c = [model.inlets[0].temperature(0.0) + supply_offset_c if model.inlets else ambient_c]
    A = np.diag(C / dt_s) + K
    for k in range(1, steps + 1):
        t = k * dt_s
        b = C / dt_s * T
        for i, g, kind, val in bnd:
            if kind == "ambient":
                b[i] += g * ambient_c
            elif kind == "fixed":
                b[i] += g * val
            else:
                b[i] += g * (val.temperature(t) + supply_offset_c)
        for i, kind, val in src:
            b[i] += val
        T = np.linalg.solve(A, b)
        t_out.append(t); air.append(float(T[i_air])); interior_c.append(float(T[i_int]))
        qw = 0.0
        for s, o, i_ in surf_nodes:
            inner[s.name].append(float(T[i_]))
            g = [gg for (a_, b_, gg) in conds if (a_ == i_ and b_ == i_air)][0]
            qw += g * (T[i_] - T[i_air])
        q_walls.append(float(qw))
        qs = 0.0
        if hvac:
            for inl in model.inlets:
                qs += inl.mass_flow_kg_s * inl.count * CP_AIR * (inl.temperature(t) + supply_offset_c - T[i_air])
        q_supply.append(float(qs))
        q_int.append(float(model.interior_h_w_m2k * model.interior_contact_area_m2 * (T[i_int] - T[i_air])))
        supply_c.append(model.inlets[0].temperature(t) + supply_offset_c if model.inlets else ambient_c)
    t_arr = np.asarray(t_out); air_arr = np.asarray(air)
    at = {m: float(np.interp(m * 60.0, t_arr, air_arr)) for m in (10, 20, 30, 40, 45) if m * 60.0 <= duration_s + 1e-6}
    lump = model.lumped_equivalent()
    return {"t_s": t_out, "air_c": air, "inner_c": inner, "interior_c": interior_c, "q_walls_w": q_walls, "q_supply_w": q_supply, "q_interior_w": q_int,
            "supply_c": supply_c, "at_minutes": at, "sun": {**sun, "irradiance": irr, "transmitted_w": transmitted_w},
            "occupants_w": occ * model.occupant_w, "lumped": lump,
            "capacitance_total_j_per_k": float(C.sum()), "nodes": N}


def compare_with_reference(model: CabinModel, result: dict) -> dict:
    """Model vs the CFD planes stored in the file: per time point, and the
    acceptance criteria of the attribute report."""
    ref = model.reference or {}
    out: dict = {"series": [], "criteria": []}
    for key, r in ref.items():
        if key == "criteria" or not isinstance(r, dict) or "times_min" not in r:
            continue
        pts = []
        for tm, tc in zip(r["times_min"], r.get("avg_c") or []):
            if tm * 60.0 <= result["t_s"][-1] + 1e-6:
                sim = float(np.interp(tm * 60.0, result["t_s"], result["air_c"]))
                pts.append({"t_min": tm, "ref_c": tc, "sim_c": sim, "diff_c": sim - tc})
        out["series"].append({"key": key, "label": r.get("label", key), "points": pts,
                              "rmse_c": float(np.sqrt(np.mean([p["diff_c"] ** 2 for p in pts]))) if pts else None})
    for c in ref.get("criteria", []):
        tm = float(c["at_min"])
        if tm * 60.0 <= result["t_s"][-1] + 1e-6:
            sim = float(np.interp(tm * 60.0, result["t_s"], result["air_c"]))
            out["criteria"].append({"at_min": tm, "max_c": c["max_c"], "sim_c": sim, "ok": sim <= c["max_c"], "label": c.get("label", "")})
    return out


# ---------------------------------------------------------------- storage
def _dir(root) -> pathlib.Path:
    return pathlib.Path(root) / "cabin"


def list_cabins(root) -> list:
    out = []
    for p in sorted(_dir(root).glob("*.yaml")) if _dir(root).exists() else []:
        try:
            m = load_cabin(root, p.stem)
            lump = m.lumped_equivalent()
            out.append({"stem": p.stem, "name": m.name, "surfaces": len(m.surfaces), "validated": m.validated,
                        "ua_w_per_k": round(lump["ua_w_per_k"], 1), "glazing_area_m2": round(lump["glazing_area_m2"], 2),
                        "summary": f"{len(m.surfaces)} yüzey · UA {lump['ua_w_per_k']:.0f} W/K · cam {lump['glazing_area_m2']:.1f} m²",
                        "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))})
        except Exception as exc:
            out.append({"stem": p.stem, "name": p.stem, "error": str(exc)})
    return out


def load_cabin(root, stem: str) -> CabinModel:
    p = _dir(root) / f"{safe_name(stem)}.yaml"
    if not p.exists():
        raise FileNotFoundError(f"cabin/{p.name} bulunamadı")
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if d.get("kind", "cabin") != "cabin":
        raise ValueError(f"{p.name}: kind cabin değil")
    return CabinModel.from_dict(d, stem=p.stem)


def save_cabin(root, model: CabinModel, *, overwrite: bool = False) -> pathlib.Path:
    d = _dir(root); d.mkdir(parents=True, exist_ok=True)
    p = d / f"{model.stem}.yaml"
    if p.exists() and not overwrite:
        raise FileExistsError(f"cabin/{p.name} zaten var")
    data = model.to_dict(); data.pop("stem", None)
    p.write_text("# Kabin katman modeli (kabin termal modeli sayfası). Yüzeyler dıştan içe katmanlardır.\n"
                 + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return p


def hvac_fields_from_cabin(model: CabinModel) -> dict:
    """What gets written into (or overrides in) the HVAC component."""
    lump = model.lumped_equivalent()
    return {"cabin_volume_m3": round(lump["cabin_volume_m3"], 1),
            "glazing_area_m2": round(lump["glazing_area_m2"], 3),
            "opaque_area_m2": round(lump["opaque_area_m2"], 2),
            "u_glazing_w_per_m2k": round(lump["u_glazing_w_per_m2k"], 3),
            "u_opaque_w_per_m2k": round(lump["u_opaque_w_per_m2k"], 3),
            "thermal_bridge_factor": 1.0,
            "interior_thermal_mass_j_per_k": round(lump["interior_thermal_mass_j_per_k"], -3),
            "shgc": round(lump["shgc"], 3)}
