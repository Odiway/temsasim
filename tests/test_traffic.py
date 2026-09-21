"""Traffic: signals are obeyed, queues are consistent, and congestion costs energy.

The 3D view draws exactly what ``DriveCycle.meta`` records, so these tests are
also what keeps the picture honest: if a bus could cross on red here, it would
be shown doing so.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import numpy as np
import pytest

from temsasim.cycle import Environment, Route
from temsasim.library import Library
from temsasim.simulator import simulate
from temsasim.traffic import LEVELS, QUEUE_SPACING_M, Signal

ROOT = pathlib.Path(__file__).resolve().parents[1]
OCC = lambda f: 40 * 4 * f * (1 - f)


@pytest.fixture(scope="module")
def route():
    return Route.from_csv(ROOT / "library" / "route" / "demo_hilly_city.csv")


@pytest.fixture(scope="module")
def bus():
    return Library(ROOT / "library").load_vehicle("citybus_12m_be")


@pytest.fixture(scope="module")
def cycles(route, bus):
    return {k: route.to_cycle(vehicle=bus, occupancy=OCC, traffic=k) for k in LEVELS}


def test_empty_road_has_no_traffic_events(cycles):
    m = cycles["yok"].meta
    assert m["signal_stops"] == [] and m["jams"] == []
    assert not m["signals_active"]


@pytest.mark.parametrize("level", ["akici", "orta", "yogun", "sikisik"])
def test_the_bus_never_crosses_a_signal_on_red(cycles, route, level):
    c = cycles[level]
    signals = [Signal(**{k: g[k] for k in ("d", "cycle", "green", "amber", "offset")})
               for g in c.meta["signals"]]
    pos = np.concatenate([[0.0], np.cumsum(c.speed_mps[:-1] * np.diff(c.time_s))])
    crossings = 0
    for g in signals:
        k = np.nonzero((pos[:-1] <= g.d) & (pos[1:] > g.d))[0]
        for i in k:
            crossings += 1
            assert g.state(c.time_s[i]) != "red", (level, g.d, c.time_s[i])
    assert crossings == len(signals)            # every junction was passed exactly once


@pytest.mark.parametrize("level", ["orta", "sikisik"])
def test_red_light_waits_are_self_consistent(cycles, level):
    c = cycles[level]
    m = c.meta
    assert m["signal_stops"], "expected at least one red-light wait on a 12 km route"
    pos = np.concatenate([[0.0], np.cumsum(c.speed_mps[:-1] * np.diff(c.time_s))])
    for e in m["signal_stops"]:
        g = Signal(**{k: m["signals"][e["i"]][k] for k in ("d", "cycle", "green", "amber", "offset")})
        i0, i1 = np.searchsorted(c.time_s, [e["t0"], e["t_go"]])
        # standing still, doors shut, at the back of the queue it recorded
        assert np.all(c.speed_mps[i0:i1] == 0)
        assert not np.any(c.doors_open[i0:i1])
        assert pos[i0] == pytest.approx(e["d_line"], abs=1.0)
        assert e["d_line"] == pytest.approx(e["d_signal"] - 1.0 - e["queue"] * QUEUE_SPACING_M, abs=0.2)
        # it waited for green, then for the queue ahead to pull away
        assert g.state(e["t_green"]) == "green"
        assert e["t_go"] - e["t_green"] == pytest.approx(
            m["reaction_s"] + m["discharge_s_per_car"] * e["queue"], abs=1.01)


def test_congestion_slows_the_bus_and_shifts_energy_from_braking_to_standing(cycles, bus):
    """What traffic does, independent of climate: slower, more time standing,
    less energy thrown away in the friction brakes, more energy on time-driven
    loads (climate, auxiliaries). Which of those wins per km depends on the
    weather -- see the next test -- so kWh/km is not asserted here."""
    env = Environment(ambient_c=2.0)
    res = {k: simulate(bus, cycles[k], env) for k in ("yok", "orta", "sikisik")}
    assert cycles["yok"].mean_speed_kmh > cycles["orta"].mean_speed_kmh > cycles["sikisik"].mean_speed_kmh
    # absolute standing time (the fraction can even fall: a jam is mostly creeping)
    standing = {k: cycles[k].idle_fraction * cycles[k].duration_s for k in ("yok", "sikisik")}
    assert standing["sikisik"] > 1.5 * standing["yok"]
    T = {k: r.totals for k, r in res.items()}
    assert T["sikisik"]["friction_brake_kwh"] < T["orta"]["friction_brake_kwh"] < T["yok"]["friction_brake_kwh"]
    assert T["sikisik"]["hvac_kwh"] > 1.5 * T["yok"]["hvac_kwh"]
    assert T["sikisik"]["auxiliary_kwh"] > 1.5 * T["yok"]["auxiliary_kwh"]
    for r in res.values():
        assert abs(r.energy_balance()["residual (should be ~0)"]) < 1e-6 + 1e-6 * r.totals["cell_energy_kwh"]


def test_when_climate_dominates_congestion_costs_energy_per_km(route):
    """Resistive heating at -10 degC: standing time is paid for in heat, so
    kWh/km rises steadily with congestion."""
    bus = Library(ROOT / "library").load_vehicle("citybus_12m_be_resistive")
    env = Environment(ambient_c=-10.0)
    kwh = [simulate(bus, route.to_cycle(vehicle=bus, occupancy=OCC, traffic=k), env).energy_per_km_kwh
           for k in ("yok", "orta", "sikisik")]
    assert kwh[0] < kwh[1] < kwh[2]
    assert kwh[2] > 1.15 * kwh[0]


def test_jams_are_stop_and_go_with_doors_closed(cycles):
    """A jam is a segment of repeated creep-and-halt, not one long stand."""
    c = cycles["sikisik"]
    assert c.meta["jams"]
    for j in c.meta["jams"]:
        assert len(j["halts"]) >= 2
        for h0, h1, _ in j["halts"]:
            i0, i1 = np.searchsorted(c.time_s, [h0, h1])
            assert np.all(c.speed_mps[i0:i1] == 0)
        i0, i1 = np.searchsorted(c.time_s, [j["t0"], j["t1"]])
        assert not np.any(c.doors_open[i0:i1])
        assert c.speed_mps[i0:i1].max() <= 4.5 + 1e-9        # creeping, never cruising


def test_door_openings_are_still_only_the_bus_stops(cycles, route):
    for c in cycles.values():
        assert c.stop_count == len(route.stop_positions_m)


def test_laps_keep_signal_time_running(route, bus):
    """Repeating a route must not replay the first lap's lights: the second lap
    meets each light at a later time, so its waits differ."""
    one = route.to_cycle(vehicle=bus, traffic="orta")
    two = route.to_cycle(vehicle=bus, traffic="orta", laps=2)
    assert two.distance_km == pytest.approx(2 * one.distance_km, rel=0.01)
    L = two.meta["lap_length_m"]
    lap1 = [(e["i"], e["queue"]) for e in two.meta["signal_stops"] if e["d_signal"] < L]
    lap2 = [(e["i"] - len(one.meta["signals"]), e["queue"]) for e in two.meta["signal_stops"] if e["d_signal"] >= L]
    assert lap1 != lap2
    assert two.stop_count == 2 * one.stop_count


def test_signals_from_the_route_file_are_used(tmp_path):
    rows = ["distance_m,elevation_m,speed_limit_kmh,stop,signal"]
    for k in range(301):
        d = k * 10
        rows.append(f"{d},50,50,{1 if d in (800, 2200) else 0},{1 if d in (500, 1500, 2600) else 0}")
    p = tmp_path / "r.csv"
    p.write_text("\n".join(rows), encoding="utf-8")
    c = Route.from_csv(p).to_cycle(traffic="orta")
    assert [g["d"] for g in c.meta["signals"]] == [500.0, 1500.0, 2600.0]
    assert c.meta["signals_synthetic"] is False


def test_javascript_light_state_matches_python():
    """The 3D view colours each light with app/static/traffic.js. It must agree
    with the Python model at every second, or the picture lies."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    sigs = [Signal(d=0, offset=o, cycle=c, green=g, amber=a)
            for o, c, g, a in [(0, 90, 42, 3), (17.3, 90, 42, 3), (88.9, 60, 25, 4), (45.5, 120, 50, 3)]]
    times = np.arange(-200, 400, 0.5)
    js_path = (ROOT / "temsasim" / "app" / "static" / "traffic.js").as_uri()
    script = (f"import {{ lightState }} from {json.dumps(js_path)};"
              f"const S = {json.dumps([{'offset': g.offset, 'cycle': g.cycle, 'green': g.green, 'amber': g.amber} for g in sigs])};"
              f"const T = {json.dumps(times.tolist())};"
              "console.log(JSON.stringify(S.map(s => T.map(t => lightState(s, t)))));")
    out = subprocess.run([node, "--input-type=module", "-e", script],
                         capture_output=True, text=True, timeout=60, check=True).stdout
    js = json.loads(out)
    for g, row in zip(sigs, js):
        assert row == [g.state(t) for t in times]
