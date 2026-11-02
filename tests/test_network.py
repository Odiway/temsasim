"""Line and timetable simulation."""

import pathlib

import numpy as np
import pytest

from temsasim.cycle import Environment
from temsasim.library import Library
from temsasim.network import HourBand, Line, load_profile, simulate_line

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def lib():
    return Library(ROOT / "library")


@pytest.fixture(scope="module")
def line(lib):
    return Line.from_yaml(ROOT / "library" / "line" / "adana_ornek.yaml", lib.root)


def test_load_profile_conserves_passengers_and_ends_empty():
    load, alight, denied = load_profile([10, 20, 0, 5, 0], [0, 5, 10, 10, 20])
    assert load[-1] == pytest.approx(0.0)
    assert alight.sum() == pytest.approx(35.0)
    assert denied.sum() == 0
    assert load[0] == 10 and load[1] <= 30


def test_load_profile_denies_boardings_over_capacity():
    load, alight, denied = load_profile([50, 50, 0, 0], [0, 1, 1, 100], capacity=60)
    assert load.max() <= 60 + 1e-9
    assert denied.sum() > 0 and alight.sum() == pytest.approx(100 - denied.sum())


def test_example_line_loads_with_matching_stops(line):
    assert line.example and len(line.stops) == 24 == len(line.route.stop_positions_m)
    assert line.bands[1].headway_min == 8 and line.bands[1].traffic == "yogun"
    r_in, b_in, w_in, names = line.direction(inbound=True)
    assert names[0] == "Çukurova Üniversitesi" and names[-1] == "Otogar"
    assert r_in.elevation_m[0] == pytest.approx(line.route.elevation_m[-1])


def test_line_simulation_two_bands(lib, line):
    veh = lib.load_vehicle("citybus_12m_be")
    short = Line(line.code, line.name, line.route, line.stops,
                 [HourBand(7, 9, 8, "yogun", 1.0), HourBand(21, 23, 30, "akici", 0.2)], line.layover_min)
    r = simulate_line(short, veh, Environment(ambient_c=20.0))
    d = r.as_dict()
    assert len(r.bands) == 4 and r.trips_per_day == 2 * (15 + 4)
    assert r.fleet_by_band[0] > r.fleet_by_band[1] >= 1 and r.fleet == max(r.fleet_by_band)
    assert r.passengers_per_day > 0 and r.kwh_per_day > 0 and r.kwh_per_pax > 0
    assert 0 < r.mean_wait_min <= 15
    peak = r.bands[0]
    assert peak.load_factor > r.bands[2].load_factor               # the peak band is fuller than the evening
    assert peak.runtime_s >= peak.free_runtime_s                   # traffic never makes a trip faster
    assert len(d["occupancy_hist"]["hours"]) == 4
    # a shorter headway needs more buses and lowers the load per trip
    r2 = simulate_line(short, veh, Environment(ambient_c=20.0), headways={0: 5})
    assert r2.fleet > r.fleet and r2.bands[0].peak_load < peak.peak_load
