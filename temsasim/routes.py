"""The route builder: a road described segment by segment, saved to the library.

GPX and elevation tables describe roads that exist. A route *spec* describes
a road you want to test: so many metres at this grade, this speed limit, this
surface, with this wind; a stop here with so many people getting on; a
traffic light there. The spec is what the engineer edits and keeps
(``route/<stem>.yaml``); the solver reads the 10 m grid generated from it
(``route/<stem>.csv``), the same file format an imported GPX route produces,
so any vehicle runs any route the same way.

Two families of template exist because the questions differ: a **city line**
(stops with boarding and alighting, signals, 30-50 km/h) and a **highway**
run (long segments at 80-100 km/h, rest-area stops with long idles). Both
are only starting points; every number in a spec is editable.
"""

from __future__ import annotations

import csv
import pathlib
from dataclasses import asdict, dataclass, field

import numpy as np
import yaml

from .surfaces import SURFACE_FACTOR, surface_factor
from .vecto_xml import safe_name

KINDS_TR = {"sehir_ici": "Şehir içi", "otoyol": "Otoyol / şehirlerarası", "serbest": "Serbest"}
GRID_M = 10.0


@dataclass
class Segment:
    length_m: float
    grade_pct: float = 0.0
    speed_limit_kmh: float = 50.0
    surface: str = "asfalt"
    headwind_mps: float = 0.0
    stop: dict | None = None          # {"name", "dwell_s", "board", "alight"} at the END of the segment
    signal: bool = False              # traffic light at the end of the segment
    name: str = ""

    def clean(self) -> "Segment":
        self.length_m = max(float(self.length_m), 1.0)
        self.grade_pct = float(self.grade_pct or 0.0)
        self.speed_limit_kmh = max(float(self.speed_limit_kmh or 50.0), 5.0)
        self.surface = self.surface if self.surface in SURFACE_FACTOR else "asfalt"
        self.headwind_mps = float(self.headwind_mps or 0.0)
        if self.stop:
            s = dict(self.stop)
            self.stop = {"name": str(s.get("name") or ""), "dwell_s": float(s.get("dwell_s") or 18.0),
                         "board": float(s.get("board") or 0.0), "alight": float(s.get("alight") or 0.0)}
        else:
            self.stop = None
        self.signal = bool(self.signal)
        return self


@dataclass
class RouteSpec:
    name: str
    stem: str = ""
    kind: str = "serbest"
    description: str = ""
    start_elevation_m: float = 50.0
    segments: list = field(default_factory=list)
    notes: str = ""
    source: str = "route builder"

    def __post_init__(self) -> None:
        self.stem = safe_name(self.stem or self.name)
        self.kind = self.kind if self.kind in KINDS_TR else "serbest"
        self.segments = [s if isinstance(s, Segment) else Segment(**s) for s in self.segments]
        for s in self.segments:
            s.clean()

    # ------------------------------------------------------------------
    @property
    def length_m(self) -> float:
        return float(sum(s.length_m for s in self.segments))

    def build(self, step_m: float = GRID_M) -> dict:
        """The 10 m grid the solver drives: arrays plus stop and signal lists."""
        if not self.segments:
            raise ValueError("Güzergâhta en az bir bölüm olmalı.")
        ends = np.cumsum([s.length_m for s in self.segments])
        starts = np.r_[0.0, ends[:-1]]
        L = float(ends[-1])
        d = np.arange(0.0, L + 1e-6, step_m)
        if d[-1] < L - 1e-6:
            d = np.r_[d, L]
        elev = np.empty_like(d); vlim = np.empty_like(d); surf = np.empty_like(d); wind = np.empty_like(d)
        e0 = float(self.start_elevation_m)
        seg_elev_start = []
        for s in self.segments:
            seg_elev_start.append(e0)
            e0 += s.length_m * s.grade_pct / 100.0
        for i, x in enumerate(d):
            k = int(np.searchsorted(ends, x, side="right"))
            k = min(k, len(self.segments) - 1)
            s = self.segments[k]
            elev[i] = seg_elev_start[k] + (x - starts[k]) * s.grade_pct / 100.0
            vlim[i] = s.speed_limit_kmh
            surf[i] = surface_factor(s.surface)
            wind[i] = s.headwind_mps
        stops, signals = [], []
        for k, s in enumerate(self.segments):
            pos = round(float(ends[k]) / step_m) * step_m
            pos = min(pos, L)
            if s.stop:
                stops.append({"distance_m": pos, **s.stop})
            if s.signal:
                signals.append(pos)
        climb = float(np.sum(np.clip(np.diff(elev), 0, None)))
        descent = float(-np.sum(np.clip(np.diff(elev), None, 0)))
        return {"distance_m": d, "elevation_m": elev, "speed_limit_kmh": vlim, "surface_factor": surf,
                "headwind_mps": wind, "stops": stops, "signals": signals,
                "totals": {"length_km": L / 1000.0, "climb_m": climb, "descent_m": descent,
                           "stops": len(stops), "signals": len(signals),
                           "mean_speed_limit_kmh": float(np.mean(vlim)),
                           "max_grade_pct": float(max((s.grade_pct for s in self.segments), default=0.0)),
                           "min_grade_pct": float(min((s.grade_pct for s in self.segments), default=0.0)),
                           "boardings": float(sum((s.stop or {}).get("board", 0) for s in self.segments)),
                           "segments": len(self.segments)}}

    def preview(self, points: int = 400) -> dict:
        b = self.build()
        d = b["distance_m"]
        step = max(1, d.size // points)
        return {"distance_km": (d[::step] / 1000).round(4).tolist(), "elevation_m": b["elevation_m"][::step].round(2).tolist(),
                "speed_limit_kmh": b["speed_limit_kmh"][::step].round(0).tolist(),
                "surface_factor": b["surface_factor"][::step].round(3).tolist(),
                "headwind_mps": b["headwind_mps"][::step].round(2).tolist(),
                "stops_km": [round(s["distance_m"] / 1000, 3) for s in b["stops"]],
                "stop_names": [s.get("name", "") for s in b["stops"]],
                "signals_km": [round(x / 1000, 3) for x in b["signals"]], "totals": b["totals"]}

    def to_dict(self) -> dict:
        out = asdict(self)
        out["segments"] = [{k: v for k, v in asdict(s).items() if v not in (None, "", False, 0.0) or k in ("length_m", "grade_pct", "speed_limit_kmh")}
                           for s in self.segments]
        return out

    # ------------------------------------------------------------------
    def write_csv(self, path) -> pathlib.Path:
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        b = self.build()
        stops = {round(s["distance_m"], 1): s for s in b["stops"]}
        sigs = {round(x, 1) for x in b["signals"]}
        with path.open("w", newline="", encoding="utf-8") as fh:
            fh.write(f"# {self.name} ({KINDS_TR.get(self.kind, self.kind)}). Güzergâh oluşturucudan; düzenlemek için {self.stem}.yaml.\n")
            w = csv.writer(fh)
            w.writerow(["distance_m", "elevation_m", "speed_limit_kmh", "stop", "signal", "surface_factor", "headwind_mps",
                        "stop_name", "dwell_s", "board", "alight"])
            for i in range(b["distance_m"].size):
                x = round(float(b["distance_m"][i]), 1)
                st = stops.get(x)
                w.writerow([f"{x:.0f}", f"{b['elevation_m'][i]:.2f}", f"{b['speed_limit_kmh'][i]:.0f}",
                            1 if st else 0, 1 if x in sigs else 0, f"{b['surface_factor'][i]:.3f}", f"{b['headwind_mps'][i]:.2f}",
                            st["name"] if st else "", f"{st['dwell_s']:.0f}" if st else "",
                            f"{st['board']:.0f}" if st else "", f"{st['alight']:.0f}" if st else ""])
        return path


def load_spec(library_root, stem: str) -> RouteSpec:
    """Read ``route/<stem>.yaml`` written by ``RouteSpec.save``."""
    p = pathlib.Path(library_root) / "route" / f"{safe_name(stem)}.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if "segments" not in data:
        raise ValueError(f"{p.name}: bir güzergâh tanımı değil")
    return RouteSpec(name=data.get("name", stem), stem=stem, kind=data.get("route_kind") or "serbest",
                     description=data.get("description", ""), start_elevation_m=float(data.get("start_elevation_m", 50.0)),
                     segments=data.get("segments", []), notes=data.get("notes", ""), source=data.get("source", "route builder"))


def save_spec(library_root, spec: RouteSpec, *, overwrite: bool = False) -> dict:
    root = pathlib.Path(library_root) / "route"
    if (root / f"{spec.stem}.csv").exists() and not overwrite:
        raise FileExistsError(f"route/{spec.stem}.csv zaten var. Üzerine yazmak için onay verin.")
    root.mkdir(parents=True, exist_ok=True)
    data = {"kind": "route_spec", "route_kind": spec.kind, **{k: v for k, v in spec.to_dict().items() if k != "kind"}}
    (root / f"{spec.stem}.yaml").write_text(
        "# Güzergâh tanımı (route builder). CSV bu dosyadan üretilir; ikisini birlikte tutun.\n"
        + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    spec.write_csv(root / f"{spec.stem}.csv")
    return {"stem": spec.stem, "yaml": f"route/{spec.stem}.yaml", "csv": f"route/{spec.stem}.csv"}


def list_routes(library_root) -> list:
    """Every route in the library with what the pages need to pick one."""
    from .cycle import Route
    root = pathlib.Path(library_root) / "route"
    out = []
    if not root.exists():
        return out
    for p in sorted(root.glob("*.csv")):
        item = {"stem": p.stem, "name": p.stem, "editable": (root / f"{p.stem}.yaml").exists(), "kind": "", "source": "csv"}
        try:
            r = Route.from_csv(p)
            climb = float(np.sum(np.clip(np.diff(r.elevation_m), 0, None)))
            item.update({"length_km": round(r.length_km, 2), "stops": len(r.stop_positions_m), "signals": len(r.signal_positions_m),
                         "climb_m": round(climb), "elev_min": round(float(r.elevation_m.min())), "elev_max": round(float(r.elevation_m.max())),
                         "mean_speed_limit_kmh": round(float(np.mean(r.speed_limit_mps)) * 3.6),
                         "has_wind": bool(np.any(r.headwind_mps != 0)), "has_surface": bool(np.any(r.surface_factor != 1.0)),
                         "boardings": round(float(sum((v.get("board") or 0) for v in r.stops.values())))})
            if item["editable"]:
                spec = load_spec(library_root, p.stem)
                item.update({"name": spec.name, "kind": spec.kind, "kind_tr": KINDS_TR.get(spec.kind, ""), "description": spec.description,
                             "segments": len(spec.segments), "source": "route builder"})
            else:
                with p.open(encoding="utf-8") as fh:
                    first = fh.readline()
                item["description"] = first.lstrip("# ").strip() if first.startswith("#") else ""
                item["kind_tr"] = "içe aktarılmış"
        except Exception as exc:          # a broken file is shown, not hidden
            item["error"] = str(exc)
        out.append(item)
    return out


# ---------------------------------------------------------------- templates
def template(kind: str, **kw) -> RouteSpec:
    """Starting points for the builder; every number is meant to be edited."""
    if kind == "sehir_ici":
        n_stops = int(kw.get("stops", 20)); spacing = float(kw.get("spacing_m", 450)); grade_amp = float(kw.get("grade_amp_pct", 3.0))
        segs = []
        for k in range(n_stops):
            g = grade_amp * np.sin(2 * np.pi * k / max(n_stops, 1) * 1.7)
            board = 6 if k < n_stops - 1 else 0
            alight = 0 if k == 0 else (4 if k < n_stops - 1 else 999)
            segs.append(Segment(spacing, round(float(g), 1), 50, "asfalt" if k % 4 else "eski_asfalt", 0.0,
                                {"name": f"Durak {k + 1}", "dwell_s": 20, "board": board, "alight": alight}, signal=(k % 2 == 1), name=f"Bölüm {k + 1}"))
        return RouteSpec(name=kw.get("name", "Şehir içi hat (şablon)"), kind="sehir_ici", start_elevation_m=30.0, segments=segs,
                         description="Şablon: duraklı, ışıklı, hafif tepeli şehir içi hat. Her bölümü düzenleyin.")
    if kind == "otoyol":
        segs = [Segment(2000, 0.0, 80, "asfalt", 0.0, name="Şehir çıkışı"),
                Segment(12000, 0.3, 100, "asfalt", 1.5, name="Hafif tırmanış"),
                Segment(6000, 2.0, 100, "asfalt", 1.5, name="Tepe"),
                Segment(8000, -1.5, 100, "asfalt", 1.5, {"name": "Dinlenme tesisi", "dwell_s": 300, "board": 0, "alight": 0}, name="İniş ve tesis"),
                Segment(15000, 0.0, 100, "asfalt", 1.5, name="Düz"),
                Segment(2000, 0.0, 70, "asfalt", 0.0, name="Gişe yaklaşımı")]
        return RouteSpec(name=kw.get("name", "Otoyol (şablon)"), kind="otoyol", start_elevation_m=50.0, segments=segs,
                         description="Şablon: 45 km otoyol, bir dinlenme tesisi, 1,5 m/s karşı rüzgâr. Her bölümü düzenleyin.")
    return RouteSpec(name=kw.get("name", "Yeni güzergâh"), kind="serbest", segments=[Segment(1000, 0.0, 50)],
                     description="")


def from_route_csv(library_root, stem: str, *, segment_m: float = 500.0) -> RouteSpec:
    """Approximate an imported route (GPX, table) as editable segments."""
    from .cycle import Route
    r = Route.from_csv(pathlib.Path(library_root) / "route" / f"{safe_name(stem)}.csv")
    L = float(r.distance_m[-1])
    edges = np.arange(0.0, L, segment_m)
    edges = np.r_[edges, L] if edges[-1] < L - 1 else edges
    segs = []
    stops = sorted(r.stop_positions_m); sigs = set(round(x, 1) for x in r.signal_positions_m)
    for a, b in zip(edges[:-1], edges[1:]):
        ea, eb = np.interp([a, b], r.distance_m, r.elevation_m)
        vl = float(np.mean(np.interp(np.linspace(a, b, 5), r.distance_m, r.speed_limit_mps))) * 3.6
        st = next((s for s in stops if a < s <= b), None)
        detail = r.stops.get(round(st, 1)) if st is not None else None
        segs.append(Segment(b - a, round(float((eb - ea) / (b - a) * 100), 2), round(vl), "asfalt",
                            float(np.mean(np.interp(np.linspace(a, b, 5), r.distance_m, r.headwind_mps))),
                            ({"name": (detail or {}).get("name", ""), "dwell_s": (detail or {}).get("dwell_s") or r.stop_dwell_s,
                              "board": (detail or {}).get("board", 0), "alight": (detail or {}).get("alight", 0)} if st is not None else None),
                            signal=any(a < g <= b for g in sigs)))
    return RouteSpec(name=f"{stem} (kopya)", stem=f"{stem}_kopya", kind=r.kind or "serbest", start_elevation_m=float(r.elevation_m[0]),
                     segments=segs, description=f"{stem} dosyasından {segment_m:.0f} m bölümlere yaklaştırıldı; düzenlenebilir.")
