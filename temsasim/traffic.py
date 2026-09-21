"""Traffic: flow speed, stop-and-go congestion and signalised junctions.

A city bus's energy per kilometre depends heavily on traffic, and not mainly
through speed. What costs energy is:

* **stops that are not bus stops** -- every red light and every jam is another
  acceleration from standstill, which regen only partly returns;
* **time** -- heating, air conditioning and the auxiliaries draw power whether
  the bus moves or not, so a congested route spends far more of its energy
  standing still.

This module turns a route into a drive cycle that contains those effects. It is
a single-vehicle model of the bus's own experience of traffic, not a traffic
microsimulation: other vehicles are represented by what they do to the bus --
a lower flow speed, fluctuations in it, queues at red lights, and jams.

Everything it decides is recorded in ``DriveCycle.meta`` (signal timings, each
red-light wait with its queue length, each jam) so the 3D view can draw exactly
what the model did. The signal state function below is mirrored line for line
in ``app/static/traffic.js``; a test runs both and compares them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# Car length plus gap in a standing queue.
QUEUE_SPACING_M = 7.5
# Deceleration a driver will use to stop for a signal that has just changed.
SIGNAL_DECEL_MAX = 3.0
# Time for each queued car ahead to pull away once the light turns green.
DISCHARGE_S_PER_CAR = 1.9
REACTION_S = 1.0
# Stop-and-go inside a jam: creep speed, distance between halts, halt length.
JAM_CREEP_MPS = 4.5
JAM_STEP_M = (12.0, 32.0)
JAM_HALT_S = (3.0, 9.0)


@dataclass(frozen=True)
class TrafficLevel:
    key: str
    label: str
    speed_factor: float      # flow speed as a fraction of the posted limit
    fluctuation: float       # +/- variation of flow speed (car-following)
    jam_rate_per_km: float   # stop-and-go segments per km
    jam_s: tuple             # (min, max) length of one jam segment [m]
    queue_cars: int          # cars ahead at a red light that has been red a while
    signals: bool = True     # obey traffic lights at all
    oncoming_per_km: float = 0.0   # visual only: oncoming density


LEVELS = {
    "yok": TrafficLevel("yok", "Yok", 1.00, 0.00, 0.0, (0, 0), 0, signals=False),
    "akici": TrafficLevel("akici", "Akıcı", 0.95, 0.05, 0.0, (0, 0), 1, oncoming_per_km=4),
    "orta": TrafficLevel("orta", "Orta", 0.80, 0.12, 0.5, (40, 90), 3, oncoming_per_km=9),
    "yogun": TrafficLevel("yogun", "Yoğun", 0.60, 0.18, 1.2, (60, 160), 6, oncoming_per_km=15),
    "sikisik": TrafficLevel("sikisik", "Sıkışık", 0.40, 0.25, 2.2, (100, 260), 10, oncoming_per_km=22),
}


@dataclass(frozen=True)
class Signal:
    """A fixed-time traffic light. Times in seconds, position in metres."""
    d: float
    cycle: float = 90.0
    green: float = 42.0
    amber: float = 3.0
    offset: float = 0.0

    def state(self, t: float) -> str:
        # Mirrored in app/static/traffic.js::lightState -- keep them identical.
        ph = (t + self.offset) % self.cycle
        if ph < self.green:
            return "green"
        if ph < self.green + self.amber:
            return "amber"
        return "red"

    def red_elapsed(self, t: float) -> float:
        ph = (t + self.offset) % self.cycle
        return max(0.0, ph - self.green - self.amber)

    @property
    def red(self) -> float:
        return self.cycle - self.green - self.amber

    def as_dict(self) -> dict:
        return {"d": round(self.d, 1), "cycle": self.cycle, "green": self.green,
                "amber": self.amber, "offset": round(self.offset, 2)}


def place_signals(length_m: float, stops_m, *, given=None, seed: int = 11) -> tuple:
    """Signal positions: from the route file when it has them, otherwise a
    plausible urban spacing (every 380-720 m, never on top of a bus stop).
    Returns (signals, synthetic_flag)."""
    rng = np.random.default_rng(seed)
    if given:
        pos = sorted(float(x) for x in given)
        synthetic = False
    else:
        pos, s = [], 250.0 + rng.uniform(0, 150)
        while s < length_m - 150:
            if all(abs(s - b) > 70 for b in stops_m):
                pos.append(round(s, 1))
            s += rng.uniform(380, 720)
        synthetic = True
    sig = [Signal(d=p, offset=float(rng.uniform(0, 90))) for p in pos]
    return sig, synthetic


def _noise(t: float, ph1: float, ph2: float) -> float:
    """Smooth, deterministic flow-speed fluctuation in about [-1, 1]."""
    return 0.6 * math.sin(2 * math.pi * t / 47.0 + ph1) + 0.4 * math.sin(2 * math.pi * t / 113.0 + ph2)


def drive(route, *, level: str | TrafficLevel | None = None, laps: int = 1,
          accel_mps2: float = 1.0, decel_mps2: float = 1.2, dt_s: float = 1.0,
          max_time_s: float = 60_000.0, a_limit_fn=None, pax_fn=None, seed: int = 11):
    """Drive a route under traffic. Returns (time, speed, position, doors, meta).

    ``position`` is distance along the whole run (laps included); the caller
    maps it back onto the route for gradient.
    """
    lv = LEVELS[level] if isinstance(level, str) else (level or LEVELS["yok"])
    L = float(route.distance_m[-1])
    laps = max(1, int(laps))
    s_end = L * laps
    rng = np.random.default_rng(seed + 101)
    ph1, ph2 = rng.uniform(0, 6.28, 2)

    base_stops = sorted(float(x) for x in route.stop_positions_m)
    stops = [b + k * L for k in range(laps) for b in base_stops]
    base_sig, synthetic = place_signals(L, base_stops, given=getattr(route, "signal_positions_m", None), seed=seed)
    signals = [Signal(d=g.d + k * L, cycle=g.cycle, green=g.green, amber=g.amber, offset=g.offset)
               for k in range(laps) for g in base_sig] if lv.signals else []

    # Jams: stop-and-go segments placed as a Poisson process in distance.
    # Inside one, the bus creeps a few car lengths behind the car ahead and
    # stops, again and again -- each restart is an acceleration from rest,
    # which is where congestion really costs energy. Segments are kept clear
    # of bus stops and signals so the three kinds of stop never overlap.
    segments, jams_plan = [], []
    if lv.jam_rate_per_km > 0:
        s = rng.exponential(1000.0 / lv.jam_rate_per_km)
        while s < s_end - 100:
            length = float(rng.uniform(*lv.jam_s))
            end = min(s + length, s_end - 60)
            clear = all(b < s - 40 or b > end + 40 for b in stops) and \
                all(g.d < s - 60 or g.d > end + 60 for g in signals)
            if clear and end - s > 20:
                segments.append((s, end))
                x = s
                while x < end:
                    jams_plan.append((x, float(rng.uniform(*JAM_HALT_S)), len(segments) - 1))
                    x += float(rng.uniform(*JAM_STEP_M))
            s = end + rng.exponential(1000.0 / lv.jam_rate_per_km)

    def local(sv):
        k = min(int(sv // L), laps - 1)
        return sv - k * L

    t = s = v = 0.0
    i_stop = i_sig = i_jam = 0
    hold = None                 # ("dwell"|"signal"|"jam", remaining or signal index)
    arriving = None             # hold that starts once the final approach step is done
    plan_stop_d = None          # stop line chosen for the current signal
    plan_queue = 0
    committed = False           # decided to go through the current signal
    discharge_left = None
    times, speeds, positions, doors = [], [], [], []
    sig_events, jam_events = [], []

    while s < s_end - 1e-6 and t < max_time_s:
        door = False
        if arriving is not None:
            # The bus came to rest at the end of the previous step; the hold
            # starts now, and so does its record.
            hold, arriving = arriving, None
            if hold[0] == "jam":
                seg = hold[2]
                if not jam_events or jam_events[-1]["seg"] != seg:
                    a0, a1 = segments[seg]
                    jam_events.append({"seg": seg, "t0": t, "t1": None, "d": round(a0, 1),
                                       "d_end": round(a1, 1), "halts": []})
                jam_events[-1]["halts"].append([t, t + hold[1], round(s, 1)])
            elif hold[0] == "signal":
                sig_events.append({"i": hold[1], "t0": t, "t_green": None, "t_go": None,
                                   "d_line": round(s, 1), "d_signal": round(signals[hold[1]].d, 1),
                                   "queue": plan_queue})
        if hold is not None:
            v = 0.0
            kind = hold[0]
            if kind == "dwell":
                door = True
                hold = (kind, hold[1] - dt_s) if hold[1] - dt_s > 0 else None
            elif kind == "jam":
                if hold[1] - dt_s > 0:
                    hold = (kind, hold[1] - dt_s, hold[2])
                else:
                    jam_events[-1]["halts"][-1][1] = t + dt_s
                    hold = None
            else:  # waiting at a signal
                g = signals[hold[1]]
                if discharge_left is None and g.state(t) == "green":
                    discharge_left = REACTION_S + DISCHARGE_S_PER_CAR * plan_queue
                    sig_events[-1]["t_green"] = t
                if discharge_left is not None:
                    discharge_left -= dt_s
                    if discharge_left <= 0:
                        sig_events[-1]["t_go"] = t
                        hold, discharge_left, committed = None, None, True
        else:
            v_lim = float(np.interp(local(s), route.distance_m, route.speed_limit_mps))
            sf = min(1.0, lv.speed_factor * (1.0 + lv.fluctuation * _noise(t, ph1, ph2)))
            v_free = v_lim * sf
            if any(a0 - 1.0 <= s <= a1 for a0, a1 in segments):
                v_free = min(v_free, JAM_CREEP_MPS)

            targets = [(s_end - s, "end")]
            if i_stop < len(stops):
                targets.append((stops[i_stop] - s, "stop"))
            if i_jam < len(jams_plan):
                targets.append((jams_plan[i_jam][0] - s, "jam"))

            # --- the next signal --------------------------------------------
            if i_sig < len(signals):
                g = signals[i_sig]
                d_line = g.d - s
                st = g.state(t)
                if st == "green" and plan_stop_d is not None:
                    plan_stop_d = None          # turned green before we got there
                if st != "green" and not committed and plan_stop_d is None:
                    stop_dist = v * v / (2 * SIGNAL_DECEL_MAX)
                    if st == "amber" and stop_dist > d_line:
                        committed = True        # inside the dilemma zone: go
                    else:
                        frac = min(1.0, 0.3 + g.red_elapsed(t) / max(g.red, 1.0))
                        plan_queue = int(round(lv.queue_cars * frac))
                        plan_stop_d = g.d - 1.0 - plan_queue * QUEUE_SPACING_M
                        if plan_stop_d < s:     # queue already reaches past us
                            plan_queue = max(0, int((g.d - 1.0 - s) // QUEUE_SPACING_M))
                            # never a stopping point behind the bus: it would roll back
                            plan_stop_d = max(s, g.d - 1.0 - plan_queue * QUEUE_SPACING_M)
                if plan_stop_d is not None:
                    targets.append((plan_stop_d - s, "signal"))

            d_t, what = min(targets, key=lambda x: x[0])
            d_t = max(d_t, 0.0)
            v_target = min(v_free, math.sqrt(2.0 * decel_mps2 * d_t))
            if what == "signal" and d_t > 0.1:
                need = v * v / (2 * d_t)
                if need > decel_mps2:           # late decision: brake harder, never run it
                    v_target = min(v_target, max(0.0, v - min(need, SIGNAL_DECEL_MAX) * dt_s))
            if v_target > v:
                a = accel_mps2
                if a_limit_fn is not None:
                    a = min(a, a_limit_fn(local(s), v, pax_fn(local(s) / L) if pax_fn else 0.0))
                v = min(v + a * dt_s, v_target)
            else:
                v = max(v - SIGNAL_DECEL_MAX * dt_s, v_target, 0.0) if what == "signal" \
                    else max(v - decel_mps2 * dt_s, v_target, 0.0)

            if d_t < max(v * dt_s, 0.5) and what != "end":
                # Final approach: cover exactly the distance left in this step
                # and stand still from the next one. Snapping the position
                # instead would move the bus without the speed trace knowing,
                # and the integrated distance would drift a few metres per stop.
                v = d_t / dt_s
                if what == "stop":
                    i_stop += 1
                    arriving = ("dwell", route.stop_dwell_s)
                elif what == "jam":
                    arriving = ("jam", jams_plan[i_jam][1], jams_plan[i_jam][2]); i_jam += 1
                else:
                    arriving = ("signal", i_sig)

        times.append(t); speeds.append(v); positions.append(s); doors.append(door)
        s += v * dt_s
        t += dt_s
        if jam_events and jam_events[-1]["t1"] is None and s > segments[jam_events[-1]["seg"]][1]:
            jam_events[-1]["t1"] = t
        # passed the signal line: move on to the next one
        while i_sig < len(signals) and s > signals[i_sig].d + 0.5:
            i_sig += 1
            plan_stop_d, plan_queue, committed = None, 0, False

    meta = {
        "traffic": lv.key, "traffic_label": lv.label,
        "signals": [g.as_dict() for g in signals] if lv.signals else [g.as_dict() for g in base_sig],
        "signals_active": lv.signals, "signals_synthetic": synthetic,
        "signal_stops": [e for e in sig_events if e["t_go"] is not None],
        "jams": [e for e in jam_events if e["t1"] is not None], "lap_length_m": L, "laps": laps,
        "queue_spacing_m": QUEUE_SPACING_M, "discharge_s_per_car": DISCHARGE_S_PER_CAR,
        "reaction_s": REACTION_S, "oncoming_per_km": lv.oncoming_per_km,
    }
    return (np.array(times), np.array(speeds), np.array(positions),
            np.array(doors, dtype=bool), meta)
