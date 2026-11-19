"""VECTO declaration mission cycles as library routes.

VECTO certifies a bus on fixed missions: Coach, Interurban, Suburban, Urban
and Heavy Urban. Each is a distance-based target: position [m], target speed
[km/h], road gradient [%] and, where the vehicle has to stand, the stop time
[s] (``VectoCore/Resources/Declaration/MissionCycles/*.vdri`` in the VECTO
source tree, EUPL-1.2, (c) European Union). This module turns one into a
temsasim route: the same target speed as the speed limit, the gradient
integrated to an elevation profile, the stops with their dwell times. Our own
driver model then drives it, with the vehicle's power in mind, exactly as it
drives any other route. That makes a TEMSA coach directly comparable with
the mission every coach in Europe is declared on.

The routes are written as plain route CSVs (no route-builder spec), headed
by a comment that names the source and switches traffic off: the mission
already contains every stop it wants.
"""

from __future__ import annotations

import csv
import pathlib

import numpy as np

from .cycle import Route

GRID_M = 10.0
MARK = "# VECTO görev çevrimi"

# file stem in VECTO -> (library stem, Turkish name, what it stands for)
MISSIONS = {
    "Coach": ("vecto_coach", "VECTO Coach", "şehirlerarası / turizm otobüsü görevi"),
    "Interurban": ("vecto_interurban", "VECTO Interurban", "şehirler arası hat görevi"),
    "Suburban": ("vecto_suburban", "VECTO Suburban", "banliyö hattı görevi"),
    "Urban": ("vecto_urban", "VECTO Urban", "şehir içi hat görevi"),
    "HeavyUrban": ("vecto_heavy_urban", "VECTO Heavy Urban", "yoğun şehir içi hat görevi"),
}


def read_vdri(path) -> dict:
    """Columns of a VECTO distance-based cycle: ``s`` [m], ``v`` [km/h],
    ``grad`` [%], ``stop`` [s]. Other columns (``<HW>`` ...) are ignored."""
    path = pathlib.Path(path)
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rows = [r for r in csv.reader(fh) if r and r[0].strip() and not r[0].lstrip().startswith("#")]
    head = [h.strip().strip("<>").lower() for h in rows[0]]
    need = {"s", "v", "grad", "stop"}
    if not need <= set(head):
        raise ValueError(f"{path.name}: VECTO mesafe tabanlı çevrim değil (gereken sütunlar <s>, <v>, <grad>, <stop>; bulunan {rows[0]})")
    col = {k: head.index(k) for k in need}
    data = np.array([[float(r[col[k]]) for k in ("s", "v", "grad", "stop")] for r in rows[1:]])
    order = np.argsort(data[:, 0], kind="stable")
    data = data[order]
    return {"s": data[:, 0], "v": data[:, 1], "grad": data[:, 2], "stop": data[:, 3]}


def mission_route(path, *, name: str = "", grid_m: float = GRID_M) -> Route:
    """A VECTO mission as a ``Route`` on a regular distance grid."""
    c = read_vdri(path)
    s, v, grad, stop = c["s"], c["v"], c["grad"], c["stop"]
    length = float(s[-1])
    d = np.arange(0.0, length + 1e-6, grid_m)
    if d[-1] < length - 1e-6:
        d = np.append(d, length)
    # target speed and gradient hold from an entry to the next one (VECTO's left sample)
    idx = np.clip(np.searchsorted(s, d, side="right") - 1, 0, s.size - 1)
    # a stop row carries v = 0: the limit there is the speed the vehicle leaves with
    v_run = v.copy()
    for i in range(v_run.size):
        if v_run[i] <= 0.0:
            later = v[i + 1:][v[i + 1:] > 0]
            v_run[i] = later[0] if later.size else (v_run[i - 1] if i else 30.0)
    limit = v_run[idx]
    # elevation: the gradient integrated along the original entries, then read on the grid
    z = np.concatenate(([0.0], np.cumsum(np.diff(s) * grad[:-1] / 100.0)))
    elev = np.interp(d, s, z)
    stops, detail = [], {}
    for pos, dwell in zip(s, stop):
        if dwell <= 0 or pos <= 0.5 * grid_m or pos >= length - 0.5 * grid_m:
            continue                                   # the halt at the start and at the end are not stops on the way
        p = float(d[int(np.argmin(np.abs(d - pos)))])
        if p in detail:
            detail[p]["dwell_s"] += float(dwell)
            continue
        stops.append(p)
        detail[p] = {"name": "", "dwell_s": float(dwell), "board": 0.0, "alight": 0.0}
    return Route(d, elev - float(elev.min()) + 50.0, limit / 3.6, stops, name=name or pathlib.Path(path).stem,
                 source=f"VECTO {pathlib.Path(path).name}", stops=detail, kind="vecto")


def mission_stats(route: Route) -> dict:
    dz = np.diff(route.elevation_m)
    dx = np.diff(route.distance_m)
    g = np.divide(dz, dx, out=np.zeros_like(dz), where=dx > 0) * 100.0
    return {"length_km": float(route.distance_m[-1]) / 1000.0, "stops": len(route.stop_positions_m),
            "dwell_s": float(sum(v["dwell_s"] for v in route.stops.values())),
            "v_max_kmh": float(route.speed_limit_mps.max() * 3.6),
            "grade_min_pct": float(g.min()), "grade_max_pct": float(g.max()),
            "climb_m": float(np.clip(dz, 0, None).sum())}


def write_route_csv(route: Route, path, comment: str) -> pathlib.Path:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stop_at = {round(p, 1) for p in route.stop_positions_m}
    with path.open("w", newline="", encoding="utf-8") as fh:
        fh.write(comment.rstrip("\n") + "\n")
        w = csv.writer(fh)
        w.writerow(["distance_m", "elevation_m", "speed_limit_kmh", "stop", "signal", "surface_factor", "headwind_mps",
                    "stop_name", "dwell_s", "board", "alight"])
        for d, e, v in zip(route.distance_m, route.elevation_m, route.speed_limit_mps):
            is_stop = round(float(d), 1) in stop_at
            det = route.stops.get(round(float(d), 1), {}) if is_stop else {}
            w.writerow([f"{d:g}", f"{e:.2f}", f"{v * 3.6:g}", 1 if is_stop else 0, 0, "1.000", "0.00",
                        det.get("name", ""), f"{det['dwell_s']:g}" if det else "", "", ""])
    return path


def import_missions(vecto_root, library_root, missions=None) -> list:
    """Write the bus missions found under a VECTO source or installation
    folder into ``library/route``. Returns one row per mission written."""
    vecto_root = pathlib.Path(vecto_root)
    hits = list(vecto_root.rglob("MissionCycles/Coach.vdri"))
    hits = [h for h in hits if "bin" not in h.parts and "obj" not in h.parts] or hits
    if not hits:
        raise FileNotFoundError(f"{vecto_root} altında MissionCycles/Coach.vdri bulunamadı (VECTO kaynak ya da kurulum klasörünü verin).")
    folder = hits[0].parent
    out = []
    for key, (stem, title, what) in MISSIONS.items():
        if missions and key not in missions and stem not in missions:
            continue
        src = folder / f"{key}.vdri"
        if not src.exists():
            continue
        route = mission_route(src, name=title)
        st = mission_stats(route)
        comment = (f"{MARK}: {title} ({what}); {st['length_km']:.1f} km, {st['stops']} duruş, azami {st['v_max_kmh']:.0f} km/h, "
                   f"eğim {st['grade_min_pct']:.1f}…{st['grade_max_pct']:.1f} %. Kaynak: VECTO {key}.vdri, (c) Avrupa Birliği, EUPL-1.2. "
                   f"Duruşlar çevrimin kendisinde; trafik eklenmez.")
        path = write_route_csv(route, pathlib.Path(library_root) / "route" / f"{stem}.csv", comment)
        out.append({"mission": key, "stem": stem, "file": path.name, **st})
    return out
