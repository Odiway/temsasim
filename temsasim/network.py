"""Line and timetable simulation: from one bus to a whole line for a day.

A *line* is a route with named stops, a timetable (headway per hour band)
and a demand profile (boardings per hour at each stop). The simulator drives
one representative trip per hour band and direction through the full
per-vehicle model (``simulate``), with that band's traffic level and the
passenger load that the demand implies, and scales by the number of trips.

From that it answers the questions an operator asks before changing a
timetable:

* how many buses the line needs at each hour and over the day;
* how full the buses are, stop by stop and hour by hour, against a target;
* how long passengers wait;
* how much energy the line uses per day and per bus, and whether the pack
  lasts the day or opportunity charging is needed;
* how all of these move when a headway is changed.

What it does not do: it has no AVL data, so it cannot report measured
punctuality. It reports the *planned* runtime per hour band and the minutes
lost to traffic (runtime with traffic minus free-flow runtime), which is what
the timetable has to absorb. Measured AVL can be imported later to replace
the traffic model with observed runtimes (see ``RuntimeSource``).

Demand model: a passenger boarding at stop i travels to stop j > i with a
probability proportional to the *attractiveness* of j (its boardings in the
opposite direction, or an explicit weight). The expected load after stop k is
therefore sum over i <= k of b_i * (sum_{j>k} w_j / sum_{j>i} w_j). Boardings
beyond the vehicle's capacity are counted as denied.
"""

from __future__ import annotations

import math
import pathlib
from dataclasses import dataclass, field

import numpy as np
import yaml

from .cycle import Environment, Route
from .simulator import simulate
from .traffic import LEVELS as TRAFFIC_LEVELS

OCCUPANCY_TARGET = 0.85        # load factor above which a trip is "over target"
OCCUPANCY_LOW = 0.30           # below which it is "under-used"


@dataclass
class HourBand:
    start_h: float
    end_h: float
    headway_min: float
    traffic: str = "orta"
    demand: float = 1.0        # multiplier on the peak boardings

    @property
    def hours(self) -> float:
        return max(0.0, self.end_h - self.start_h)

    @property
    def trips_per_direction(self) -> int:
        return int(round(self.hours * 60.0 / max(self.headway_min, 1e-6)))

    def label(self) -> str:
        return f"{int(self.start_h):02d}:00–{int(self.end_h):02d}:00"


@dataclass
class Stop:
    name: str
    board_out: float = 0.0     # boardings per hour at the peak, outbound direction
    board_in: float = 0.0      # boardings per hour at the peak, inbound direction
    weight_out: float | None = None   # attractiveness as a destination outbound (default: board_in)
    weight_in: float | None = None


@dataclass
class Line:
    code: str
    name: str
    route: Route
    stops: list[Stop]
    bands: list[HourBand]
    layover_min: float = 6.0
    notes: str = ""
    source: str = ""
    example: bool = False

    @classmethod
    def from_yaml(cls, path: pathlib.Path, library_root: pathlib.Path) -> "Line":
        d = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8")) or {}
        if d.get("kind", "line") != "line":
            raise ValueError(f"{path}: kind is not 'line'")
        route_ref = d["route"]
        rp = pathlib.Path(route_ref)
        if not rp.is_absolute():
            rp = library_root / route_ref
        route = Route.from_csv(rp, name=rp.stem)
        stops = [Stop(name=str(s.get("name", f"Durak {k + 1}")), board_out=float(s.get("board", s.get("board_out", 0)) or 0),
                      board_in=float(s.get("board_in", 0) or 0),
                      weight_out=(None if s.get("weight_out") is None else float(s["weight_out"])),
                      weight_in=(None if s.get("weight_in") is None else float(s["weight_in"])))
                 for k, s in enumerate(d.get("stops", []))]
        n_flags = len(route.stop_positions_m)
        if len(stops) != n_flags:
            raise ValueError(f"{path}: {len(stops)} stops listed but the route has {n_flags} stop flags")
        bands = [HourBand(float(b["from"]), float(b["to"]), float(b["headway_min"]),
                          str(b.get("traffic", "orta")), float(b.get("demand", 1.0)))
                 for b in d.get("timetable", [])]
        for b in bands:
            if b.traffic not in TRAFFIC_LEVELS:
                raise ValueError(f"{path}: unknown traffic level {b.traffic!r}")
        return cls(code=str(d.get("code", pathlib.Path(path).stem)), name=str(d.get("name", "")), route=route,
                   stops=stops, bands=bands, layover_min=float(d.get("layover_min", 6.0)),
                   notes=str(d.get("notes", "")), source=f"library:{pathlib.Path(path).name}",
                   example=bool(d.get("example", False)))

    # ------------------------------------------------------------------
    def direction(self, inbound: bool) -> tuple[Route, list[float], list[float], list[str]]:
        """Route, boardings/h (peak) and destination weights in trip order."""
        r = self.route
        if not inbound:
            b = [s.board_out for s in self.stops]
            w = [(s.weight_out if s.weight_out is not None else s.board_in) for s in self.stops]
            return r, b, w, [s.name for s in self.stops]
        L = float(r.distance_m[-1])
        d = L - np.asarray(r.distance_m)[::-1]
        e = np.asarray(r.elevation_m)[::-1]
        v = np.asarray(r.speed_limit_mps)[::-1]
        stops = sorted(L - float(s) for s in r.stop_positions_m)
        rr = Route(d, e, v, stops, stop_dwell_s=r.stop_dwell_s, name=f"{r.name} (dönüş)", source=r.source)
        st = list(reversed(self.stops))
        b = [s.board_in for s in st]
        w = [(s.weight_in if s.weight_in is not None else s.board_out) for s in st]
        return rr, b, w, [s.name for s in st]


def load_profile(boardings: list[float], weights: list[float], capacity: float | None = None):
    """Expected load after every stop, and the boardings denied for lack of room.

    ``boardings`` are passengers per trip at each stop; ``weights`` are the
    destination attractiveness of each stop. Returns (load_after_stop,
    alightings, denied) as arrays of the same length."""
    n = len(boardings)
    b = np.asarray(boardings, dtype=float)
    w = np.asarray(weights, dtype=float)
    w = np.where(w > 0, w, 0.0)
    if w[1:].sum() <= 0:
        w = np.ones(n)
    w[0] = 0.0                                      # nobody travels to the first stop
    # passengers on board after stop k, by origin i: b_i * remaining share of destinations
    onboard = np.zeros(n)                           # by origin, currently on board
    load = np.zeros(n)
    alight = np.zeros(n)
    denied = np.zeros(n)
    tail = np.cumsum(w[::-1])[::-1]                 # sum of weights from k to the end
    for k in range(n):
        # alight: of each origin's passengers still on board, the share whose destination is k
        if k > 0:
            for i in range(k):
                if onboard[i] <= 0:
                    continue
                share = w[k] / tail[k] if tail[k] > 0 else 1.0
                a = onboard[i] * share
                onboard[i] -= a
                alight[k] += a
        # board (last stop: nobody boards)
        if k < n - 1:
            room = math.inf if capacity is None else max(0.0, capacity - onboard.sum())
            take = min(b[k], room)
            denied[k] = b[k] - take
            onboard[k] += take
        load[k] = onboard.sum()
    return load, alight, denied


@dataclass
class BandResult:
    band: HourBand
    direction: str
    runtime_s: float
    free_runtime_s: float
    distance_km: float
    kwh: float
    kwh_per_km: float
    soc_drop: float
    peak_load: float
    mean_load: float
    load_factor: float
    boardings: float
    denied: float
    pax_km: float
    stop_loads: list[float]
    stop_boardings: list[float]
    stop_denied: list[float]
    trips: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class LineResult:
    line_code: str
    vehicle: str
    capacity: int
    bands: list[BandResult]
    fleet: int
    fleet_by_band: list[int]
    cycle_time_by_band_min: list[float]
    trips_per_day: int
    vehicle_km: float
    vehicle_hours: float
    passengers_per_day: float
    denied_per_day: float
    pax_km_per_day: float
    mean_wait_min: float
    kwh_per_day: float
    kwh_per_bus_day: float
    kwh_per_pax: float
    usable_kwh: float
    charge_sessions_per_bus: int
    traffic_loss_min_per_day: float
    occupancy_hist: dict
    peak_band_index: int

    def as_dict(self) -> dict:
        def band(b: BandResult) -> dict:
            return {"label": b.band.label(), "from": b.band.start_h, "to": b.band.end_h, "headway_min": b.band.headway_min,
                    "traffic": b.band.traffic, "demand": b.band.demand, "direction": b.direction,
                    "runtime_min": round(b.runtime_s / 60, 1), "free_runtime_min": round(b.free_runtime_s / 60, 1),
                    "distance_km": round(b.distance_km, 2), "kwh": round(b.kwh, 2), "kwh_per_km": round(b.kwh_per_km, 3),
                    "soc_drop": round(b.soc_drop, 3), "peak_load": round(b.peak_load, 1), "mean_load": round(b.mean_load, 1),
                    "load_factor": round(b.load_factor, 3), "boardings": round(b.boardings, 1), "denied": round(b.denied, 1),
                    "pax_km": round(b.pax_km, 1), "trips": b.trips,
                    "stop_loads": [round(x, 1) for x in b.stop_loads], "stop_boardings": [round(x, 1) for x in b.stop_boardings],
                    "stop_denied": [round(x, 1) for x in b.stop_denied], "warnings": b.warnings}
        return {"line": self.line_code, "vehicle": self.vehicle, "capacity": self.capacity,
                "bands": [band(b) for b in self.bands], "fleet": self.fleet, "fleet_by_band": self.fleet_by_band,
                "cycle_time_by_band_min": [round(x, 1) for x in self.cycle_time_by_band_min],
                "trips_per_day": self.trips_per_day, "vehicle_km": round(self.vehicle_km, 1),
                "vehicle_hours": round(self.vehicle_hours, 1), "passengers_per_day": round(self.passengers_per_day),
                "denied_per_day": round(self.denied_per_day), "pax_km_per_day": round(self.pax_km_per_day),
                "mean_wait_min": round(self.mean_wait_min, 2), "kwh_per_day": round(self.kwh_per_day, 1),
                "kwh_per_bus_day": round(self.kwh_per_bus_day, 1), "kwh_per_pax": round(self.kwh_per_pax, 3),
                "usable_kwh": round(self.usable_kwh, 1), "charge_sessions_per_bus": self.charge_sessions_per_bus,
                "traffic_loss_min_per_day": round(self.traffic_loss_min_per_day, 1),
                "occupancy_hist": self.occupancy_hist, "peak_band_index": self.peak_band_index}


def simulate_line(line: Line, vehicle, env: Environment, *, headways: dict[int, float] | None = None,
                  demand_scale: float = 1.0, traffic_override: dict[int, str] | None = None,
                  capacity: int | None = None, free_flow_reference: bool = True) -> LineResult:
    """Drive one representative trip per hour band and direction, scale by trips.

    ``headways`` and ``traffic_override`` map band index to a new value for
    what-if runs; the line object is not modified."""
    cap = int(capacity if capacity is not None else vehicle.passenger_capacity)
    bands = []
    for k, b in enumerate(line.bands):
        hb = HourBand(b.start_h, b.end_h, (headways or {}).get(k, b.headway_min),
                      (traffic_override or {}).get(k, b.traffic), b.demand)
        bands.append(hb)
    usable = float(vehicle.battery.usable_energy_kwh)
    results: list[BandResult] = []
    free_cache: dict[str, float] = {}
    for k, hb in enumerate(bands):
        for inbound in (False, True):
            route, board_h, weights, names = line.direction(inbound)
            per_trip = [x * hb.demand * demand_scale * hb.headway_min / 60.0 for x in board_h]
            load, alight, denied = load_profile(per_trip, weights, cap)
            stops_m = sorted(float(s) for s in route.stop_positions_m)
            L = float(route.distance_m[-1])
            frac_stops = [s / L for s in stops_m]

            def occupancy(f, _fs=frac_stops, _load=load):
                # passengers on board between stop k and k+1 are those loaded after stop k
                idx = int(np.searchsorted(_fs, f, side="right")) - 1
                if idx < 0:
                    return 0.0
                return float(_load[min(idx, len(_load) - 1)])

            cyc = route.to_cycle(vehicle=vehicle, occupancy=occupancy, traffic=hb.traffic)
            res = simulate(vehicle, cyc, env)
            t = res.totals
            dist_km = float(t["distance_km"])
            seg = np.diff(np.concatenate([stops_m, [L]])) / 1000.0        # km from each stop to the next
            pax_km = float(np.sum(load * seg))
            direction = "dönüş" if inbound else "gidiş"
            free_key = direction
            if free_flow_reference and free_key not in free_cache:
                free_cache[free_key] = float(route.to_cycle(vehicle=vehicle, occupancy=occupancy, traffic="yok").duration_s)
            br = BandResult(band=hb, direction=direction, runtime_s=float(cyc.duration_s),
                            free_runtime_s=free_cache.get(free_key, float(cyc.duration_s)),
                            distance_km=dist_km, kwh=float(t["battery_energy_kwh"]),
                            kwh_per_km=float(res.energy_per_km_kwh),
                            soc_drop=float(res.initial_soc - res.series["soc"][-1]),
                            peak_load=float(load.max()) if len(load) else 0.0,
                            mean_load=float(pax_km / max(dist_km, 1e-6)),
                            load_factor=float(load.max() / cap) if cap else 0.0,
                            boardings=float(sum(per_trip) - denied.sum()), denied=float(denied.sum()), pax_km=pax_km,
                            stop_loads=[float(x) for x in load], stop_boardings=[float(x) for x in per_trip],
                            stop_denied=[float(x) for x in denied], trips=hb.trips_per_direction,
                            warnings=[w.tr if hasattr(w, "tr") else str(w) for w in res.warnings][:3])
            results.append(br)

    # fleet: cycle time / headway in each band, the day needs the maximum
    fleet_by_band, cycle_min = [], []
    for k, hb in enumerate(bands):
        out, back = results[2 * k], results[2 * k + 1]
        ct = (out.runtime_s + back.runtime_s) / 60.0 + 2 * line.layover_min
        cycle_min.append(ct)
        fleet_by_band.append(int(math.ceil(ct / max(hb.headway_min, 1e-6))) if hb.hours > 0 else 0)
    fleet = max(fleet_by_band) if fleet_by_band else 0
    trips = sum(r.trips for r in results)
    veh_km = sum(r.trips * r.distance_km for r in results)
    veh_h = sum(r.trips * (r.runtime_s / 60.0 + line.layover_min) for r in results) / 60.0
    pax = sum(r.trips * r.boardings for r in results)
    denied = sum(r.trips * r.denied for r in results)
    pax_km = sum(r.trips * r.pax_km for r in results)
    kwh = sum(r.trips * r.kwh for r in results)
    # waiting: half the headway, weighted by the passengers who wait in that band
    wait_num = sum(r.trips * r.boardings * r.band.headway_min / 2.0 for r in results)
    mean_wait = wait_num / pax if pax > 0 else 0.0
    loss = sum(r.trips * max(0.0, r.runtime_s - r.free_runtime_s) for r in results) / 60.0
    kwh_bus = kwh / fleet if fleet else 0.0
    sessions = int(math.ceil(kwh_bus / usable)) if usable > 0 and kwh_bus > 0 else 0
    # occupancy classes per hour of the day, by the trips in each band
    hist = {"hours": [], "within": [], "above": [], "below": [], "load_factor": []}
    for k, hb in enumerate(bands):
        out, back = results[2 * k], results[2 * k + 1]
        for h in range(int(hb.start_h), int(math.ceil(hb.end_h))):
            n_h = 60.0 / hb.headway_min * 2            # trips in both directions in this hour
            lf = max(out.load_factor, back.load_factor)
            cls = "above" if lf > OCCUPANCY_TARGET else "below" if lf < OCCUPANCY_LOW else "within"
            hist["hours"].append(h)
            hist["within"].append(round(n_h if cls == "within" else 0, 1))
            hist["above"].append(round(n_h if cls == "above" else 0, 1))
            hist["below"].append(round(n_h if cls == "below" else 0, 1))
            hist["load_factor"].append(round(lf, 3))
    peak_idx = int(np.argmax([max(results[2 * k].load_factor, results[2 * k + 1].load_factor) for k in range(len(bands))])) if bands else 0
    return LineResult(line_code=line.code, vehicle=f"{vehicle.name} — {vehicle.variant}", capacity=cap, bands=results,
                      fleet=fleet, fleet_by_band=fleet_by_band, cycle_time_by_band_min=cycle_min, trips_per_day=trips,
                      vehicle_km=veh_km, vehicle_hours=veh_h, passengers_per_day=pax, denied_per_day=denied,
                      pax_km_per_day=pax_km, mean_wait_min=mean_wait, kwh_per_day=kwh, kwh_per_bus_day=kwh_bus,
                      kwh_per_pax=(kwh / pax if pax > 0 else 0.0), usable_kwh=usable,
                      charge_sessions_per_bus=sessions, traffic_loss_min_per_day=loss, occupancy_hist=hist,
                      peak_band_index=peak_idx)


def list_lines(library_root: pathlib.Path) -> list[pathlib.Path]:
    d = pathlib.Path(library_root) / "line"
    return sorted(d.glob("*.yaml")) if d.exists() else []


def line_summary(line: Line) -> dict:
    return {"code": line.code, "name": line.name, "stops": [s.name for s in line.stops],
            "length_km": round(float(line.route.distance_m[-1]) / 1000.0, 2),
            "bands": [{"from": b.start_h, "to": b.end_h, "headway_min": b.headway_min, "traffic": b.traffic, "demand": b.demand}
                      for b in line.bands],
            "layover_min": line.layover_min, "example": line.example, "notes": line.notes,
            "peak_boardings_per_hour": round(sum(s.board_out + s.board_in for s in line.stops))}
