"""Calibrating the gearshift set points against logged gear traces.

A gearbox control unit is calibrated by its maker; VECTO's generic values are
a starting point, not the vehicle. This module replays the test team's CAN
logs through the shift strategy alone (``temsasim.shift``: the driveline
table is built once per log, a plan costs a few milliseconds), scores the
simulated gear and engine-speed trace against the logged one, and searches
the set points for the best agreement.

The score is deliberately plain, so that it can be read off a chart:

* **gear agreement**: share of the driving samples in which the simulated
  gear is the logged gear. A motorway log is mostly cruising in top gear,
  where any strategy is right, so the objective uses the mean of the overall
  agreement and the agreement outside steady cruising (pulling away,
  accelerating, braking, slow driving), where the shift logic shows;
* **engine speed RMSE** over the same samples, which punishes a gear that is
  two off harder than one that is one off;
* **start gear**: the gear the box pulls away in after every standstill;
* **shift count**: only an excess counts, the mark of a strategy that hunts.

The search is a coordinate descent over a grid: one set point at a time,
coarse first, then finer around the best value. That is slow in theory and
exactly right here: the set points are few, each has a meaning an engineer
wants to see moving, and the trace of what was tried is the calibration
record.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from .shift import SET_POINT_INFO, DrivelineTable, ShiftSetPoints, build_table, plan_gears
from .units import RPM

W_RPM = 0.25        # weight of the engine-speed error in the objective
W_LAUNCH = 0.05     # weight of the start-gear agreement
W_SHIFTS = 0.10     # weight of an excess of shifts (hunting)
SHIFT_EXCESS = 0.25  # share of extra shifts that is tolerated before it counts
MIN_GAIN = 5e-4     # a set point is only moved for a gain worth having
RPM_SCALE = 400.0   # an RMSE of this many rpm counts as a full miss

# Set points the calibration searches by default, the most influential first.
DEFAULT_PARAMS = [
    "min_engine_speed_post_upshift_rpm", "downshift_high_speed_rpm", "downshift_low_speed_rpm",
    "brake_downshift_speed_rpm", "brake_downshift_min_decel_mps2", "brake_downshift_max_speed_rpm", "upshift_speed_rpm",
    "acceleration_factor", "rating_factor_current_gear", "max_skip_down", "allowed_gear_range_fc",
    "upshift_min_acceleration_mps2", "torque_reserve", "start_speed_kmh",
    "time_between_gearshifts_s", "upshift_after_downshift_delay_s", "downshift_after_upshift_delay_s",
    "driver_decel_threshold_mps2",
]


@dataclass
class CalRun:
    """One logged run, ready to be planned: the driveline table of the
    vehicle on the logged trace and the logged gear and engine speed."""

    key: str
    table: DrivelineTable
    meas_gear: np.ndarray            # 1-based, 0 = not engaged or unknown
    meas_rpm: np.ndarray | None
    label: str = ""
    weight: float = 1.0


def auto_values(table: DrivelineTable, sp: ShiftSetPoints) -> dict:
    """The engine speeds VECTO derives when an override is 0, in rpm: what the
    calibration starts from and what the interface shows next to "auto"."""
    n_clutch = (table.rated - table.idle) * sp.clutch_closing_speed_norm + table.idle
    c = table.char0
    return {"downshift_low_speed_rpm": min(n_clutch - 10.0 * RPM, table.idle * sp.downshift_idle_factor) / RPM,
            "downshift_high_speed_rpm": c.n_tq99l / RPM, "upshift_speed_rpm": c.n_p98h / RPM}


def score_run(run: CalRun, plan) -> dict:
    tb = run.table
    sim = plan.gear + 1                      # 0 while standing
    meas = run.meas_gear
    mask = tb.moving & (meas > 0) & (sim > 0)
    n = int(mask.sum())
    out = {"key": run.key, "label": run.label, "samples": n, "agreement": 0.0, "within_one": 0.0, "mean_diff": 0.0,
           "rpm_rmse": 0.0, "shifts_sim": 0, "shifts_meas": 0, "transient_samples": 0, "transient_agreement": 0.0,
           "launches": 0, "launch_agreement": 0.0, "launch_gears": []}
    if n == 0:
        return out
    d = sim[mask] - meas[mask]
    out["agreement"] = float(np.mean(d == 0))
    # outside steady cruising: where the shift logic shows
    cruise = (tb.speed_mps > 22.0) & (np.abs(tb.accel_mps2) < 0.15)
    tr_mask = mask & ~cruise
    out["transient_samples"] = int(tr_mask.sum())
    if tr_mask.any():
        out["transient_agreement"] = float(np.mean(sim[tr_mask] == meas[tr_mask]))
    # start gears: the first engaged gear after every standstill
    launches = [e for e in plan.events if e["reason"] == "launch"]
    pairs = []
    for e in launches:
        j = e["i"]
        win = meas[j:j + 6]
        win = win[win > 0]
        if win.size:
            pairs.append((int(e["to"]), int(win[0])))
    out["launches"] = len(pairs)
    out["launch_gears"] = pairs
    if pairs:
        out["launch_agreement"] = float(np.mean([a == b for a, b in pairs]))
    out["within_one"] = float(np.mean(np.abs(d) <= 1))
    out["mean_diff"] = float(np.mean(d))
    if run.meas_rpm is not None:
        g = np.clip(plan.gear, 0, tb.n_gears - 1)
        sim_rpm = np.where(plan.clutch_open, tb.idle, tb.w_eng[np.arange(sim.size), g]) / RPM
        ok = mask & np.isfinite(run.meas_rpm)
        if ok.any():
            out["rpm_rmse"] = float(np.sqrt(np.mean((sim_rpm[ok] - run.meas_rpm[ok]) ** 2)))

    def changes(a):
        a = a[tb.moving & (a > 0)]
        return int(np.sum(a[1:] != a[:-1])) if a.size > 1 else 0

    out["shifts_sim"], out["shifts_meas"] = changes(sim), changes(meas)
    return out


def evaluate(runs: list, sp: ShiftSetPoints, *, detail: bool = False) -> dict:
    """Plan every run with these set points and score the result."""
    rows, plans = [], []
    if sp.kickdown:
        # logged traces: the demand was met in the logged gear (see shift.for_trace)
        sp = ShiftSetPoints.from_dict({**sp.to_dict(), "kickdown": False})
    for r in runs:
        plan = plan_gears(r.table, sp)
        rows.append(score_run(r, plan))
        plans.append(plan)
    w = np.array([max(x["samples"], 0) * r.weight for x, r in zip(rows, runs)], dtype=float)
    tot = float(w.sum()) or 1.0
    agree = float(sum(x["agreement"] * wi for x, wi in zip(rows, w)) / tot)
    within = float(sum(x["within_one"] * wi for x, wi in zip(rows, w)) / tot)
    rmse = float(np.sqrt(sum(x["rpm_rmse"] ** 2 * wi for x, wi in zip(rows, w)) / tot))
    wt = np.array([x["transient_samples"] * r.weight for x, r in zip(rows, runs)], dtype=float)
    transient = float(sum(x["transient_agreement"] * wi for x, wi in zip(rows, wt)) / (float(wt.sum()) or 1.0))
    n_launch = sum(x["launches"] for x in rows)
    launch = float(sum(x["launch_agreement"] * x["launches"] for x in rows) / n_launch) if n_launch else 1.0
    s_sim = sum(x["shifts_sim"] for x in rows)
    s_meas = sum(x["shifts_meas"] for x in rows)
    excess = max(0.0, (s_sim - s_meas) / max(s_meas, 10) - SHIFT_EXCESS)
    objective = ((1.0 - 0.5 * (agree + transient)) + W_RPM * min(rmse / RPM_SCALE, 1.0)
                 + W_LAUNCH * (1.0 - launch) + W_SHIFTS * min(excess, 1.0))
    out = {"objective": objective, "agreement": agree, "transient_agreement": transient, "within_one": within,
           "rpm_rmse": rmse, "launch_agreement": launch, "launches": n_launch,
           "shifts_sim": s_sim, "shifts_meas": s_meas, "runs": rows}
    if detail:
        out["plans"] = plans
    return out


def _grid(name: str, value: float, autos: dict, coarse: bool) -> list:
    info = SET_POINT_INFO[name]
    lo, hi, step = float(info["min"]), float(info["max"]), float(info["step"])
    is_int = isinstance(getattr(ShiftSetPoints(), name), int) and not isinstance(getattr(ShiftSetPoints(), name), bool)
    if isinstance(getattr(ShiftSetPoints(), name), bool):
        return [0.0, 1.0]
    base = value
    if value <= 0 and name in autos:
        base = round(autos[name] / step) * step          # "auto": start the search at VECTO's derived speed
    if is_int:
        return [float(v) for v in range(int(lo), int(hi) + 1)]
    if coarse:
        span = hi - lo
        n = 9
        pts = {round((lo + span * k / (n - 1)) / step) * step for k in range(n)}
        pts |= {base}
        if name == "brake_downshift_speed_rpm" or name in autos:
            pts.add(0.0)                                  # off / auto stays a candidate
    else:
        pts = {base + k * step for k in (-4, -2, -1, 1, 2, 4)}
    pts = {round(round(p / step) * step, 6) for p in pts}
    return sorted(p for p in pts if lo - 1e-9 <= p <= hi + 1e-9 and abs(p - value) > 1e-9)


@dataclass
class Calibration:
    setpoints: ShiftSetPoints
    before: dict
    after: dict
    history: list = field(default_factory=list)     # [{"param", "from", "to", "objective", "agreement"}]
    sensitivity: dict = field(default_factory=dict)
    """Per searched set point, how far the objective moved over its range.
    A set point with (almost) no effect cannot be identified from these logs
    and keeps its starting value."""
    evaluations: int = 0
    seconds: float = 0.0
    stopped: str = "converged"

    def changed(self) -> dict:
        return self.setpoints.changed()


# A coordinate descent cannot switch a rule on when its neighbour has already
# bent to do that rule's job badly (a braking downshift speed against a raised
# part-load downshift speed), so the search is also started with the
# extensions switched on at a typical value; the better end point wins.
EXTENSION_SEEDS = [{}, {"brake_downshift_speed_rpm": 1000.0}]


def calibrate(runs: list, start: ShiftSetPoints | None = None, *, params: list | None = None,
              budget_s: float = 40.0, max_evaluations: int = 4000, progress=None, seeds: list | None = None) -> Calibration:
    """Search the set points for the best agreement with the logged gears.

    ``params`` restricts the search (default ``DEFAULT_PARAMS``). The search
    stops when a full pass changes nothing, or at the time budget; either way
    the best set points found so far are returned."""
    start = start or ShiftSetPoints()
    params = [p for p in (params or DEFAULT_PARAMS) if p in SET_POINT_INFO]
    seeds = EXTENSION_SEEDS if seeds is None else seeds
    seeds = [s for s in seeds if all(k in params for k in s)] or [{}]
    before = evaluate(runs, start)
    results = []
    t0 = time.time()
    for k, seed in enumerate(seeds):
        left = budget_s - (time.time() - t0)
        if k and left < 3.0:
            break
        sp0 = ShiftSetPoints.from_dict({**start.to_dict(), **{n: v for n, v in seed.items() if getattr(start, n) == getattr(ShiftSetPoints(), n)}})
        if k and sp0.to_dict() == start.to_dict():
            continue
        results.append(_descend(runs, sp0, params, left / (len(seeds) - k), max_evaluations, progress))
    cal = min(results, key=lambda c: c.after["objective"])
    cal.before = before
    cal.evaluations = sum(c.evaluations for c in results)
    cal.seconds = time.time() - t0
    return cal


def _descend(runs: list, start: ShiftSetPoints, params: list, budget_s: float, max_evaluations: int, progress) -> Calibration:
    sp = ShiftSetPoints.from_dict(start.to_dict())
    t0 = time.time()
    before = evaluate(runs, sp)
    best = before
    evals = 1
    history: list = []
    sensitivity: dict = {}
    autos = auto_values(runs[0].table, sp) if runs else {}
    stopped = "converged"

    def out_of_budget():
        return time.time() - t0 > budget_s or evals >= max_evaluations

    for coarse in (True, False, False):
        changed_any = False
        for name in params:
            if out_of_budget():
                stopped = "budget"
                break
            cur = getattr(sp, name)
            best_v, best_res = cur, best
            seen = [best["objective"]]
            for v in _grid(name, float(cur), autos, coarse):
                trial = ShiftSetPoints.from_dict({**sp.to_dict(), name: v})
                res = evaluate(runs, trial)
                evals += 1
                seen.append(res["objective"])
                if res["objective"] < best_res["objective"] - MIN_GAIN:
                    best_v, best_res = getattr(trial, name), res
                if out_of_budget():
                    stopped = "budget"
                    break
            if coarse:
                sensitivity[name] = max(seen) - min(seen)
            if best_v != cur:
                history.append({"param": name, "from": cur, "to": best_v, "objective": best_res["objective"],
                                "agreement": best_res["agreement"]})
                setattr(sp, name, best_v)
                best = best_res
                changed_any = True
                if progress:
                    progress(name, best_v, best_res)
        if stopped == "budget" or (not coarse and not changed_any):
            break
    return Calibration(setpoints=sp, before=before, after=best, history=history, sensitivity=sensitivity,
                       evaluations=evals, seconds=time.time() - t0, stopped=stopped)


# --------------------------------------------------------------------------- preparing runs
def prepare_campaign(lib, name: str, *, vehicle=None, runs: list | None = None) -> tuple:
    """``(vehicle, [CalRun])`` for a test campaign in the library: every run
    with the test mass, weather and gradient the correlation uses."""
    from .diesel import accessory_pass
    from .testlog import campaign_cases, replay_cycle
    meta, veh, _, cases = campaign_cases(lib, name, vehicle=vehicle, runs=runs)
    out = []
    for c in cases:
        log = c["run"].log
        if log.gear is None:
            continue
        veh_m, cyc, _, _ = replay_cycle(veh, log, mass_kg=c["mass_kg"], grade_rad=c["grade"], distance_km=c["distance_km"])
        out.append(prepare_run(veh_m, cyc, c["env"], log, key=c["run"].key))
    return veh, out


def prepare_run(vehicle, cycle, environment, log, *, key: str = "", dt_s: float = 1.0) -> CalRun:
    from .diesel import accessory_pass
    cyc = cycle.resample(dt_s)
    acc = accessory_pass(vehicle, cyc, environment, dt_s=dt_s)
    tb = build_table(vehicle, cyc, environment, acc["total_w"])
    t_log = log.time_s - log.time_s[0]
    gear = np.rint(np.interp(cyc.time_s, t_log, np.nan_to_num(log.gear))).astype(int)
    # a sample between two logged gears is neither: nearest neighbour, not interpolation
    idx = np.clip(np.searchsorted(t_log, cyc.time_s), 0, t_log.size - 1)
    gear = np.rint(np.nan_to_num(log.gear)[idx]).astype(int)
    gear[gear < 0] = 0
    rpm = np.interp(cyc.time_s, t_log, np.nan_to_num(log.engine_rpm)) if log.engine_rpm is not None else None
    return CalRun(key=key or log.name, table=tb, meas_gear=gear, meas_rpm=rpm, label=log.name)


def run_traces(run: CalRun, plan) -> dict:
    """Time series for the calibration chart of one run."""
    tb = run.table
    g = np.clip(plan.gear, 0, tb.n_gears - 1)
    idx = np.arange(g.size)
    sim_rpm = np.where(tb.moving, np.where(plan.clutch_open, tb.idle, tb.w_eng[idx, g]), tb.idle) / RPM
    load = np.where(tb.moving, tb.t_tot[idx, g] / np.maximum(tb.t_full[idx, g], 1.0), 0.0)
    return {"time_s": tb.time_s.tolist(), "speed_kmh": (tb.speed_mps * 3.6).round(2).tolist(),
            "gear_sim": (plan.gear + 1).tolist(), "gear_meas": run.meas_gear.tolist(),
            "rpm_sim": np.round(sim_rpm, 1).tolist(),
            "rpm_meas": np.round(run.meas_rpm, 1).tolist() if run.meas_rpm is not None else None,
            "load": np.round(np.clip(load, -0.3, 1.5), 3).tolist(),
            "torque_nm": np.round(np.where(tb.moving, tb.t_in[idx, g], 0.0), 1).tolist(),
            "grade_pct": np.round(np.tan(tb.grade_rad) * 100.0, 2).tolist(),
            "clutch_open": plan.clutch_open.astype(int).tolist(),
            "events": plan.events}
