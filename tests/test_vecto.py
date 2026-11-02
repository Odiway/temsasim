"""VECTO bridge: parsing always runs; the live comparison needs the release.

The VECTO release is not part of the repository. When ``vectocmd.exe`` is not
found the live tests are skipped, not failed, so CI without VECTO stays green
while a developer with the release gets the real comparison."""

import pathlib

import numpy as np
import pytest

from temsasim import vecto
from temsasim.components.chassis import Chassis
from temsasim.cycle import Route

ROOT = pathlib.Path(__file__).resolve().parents[1]
CMD = vecto.find_vecto(ROOT)
needs_vecto = pytest.mark.skipif(CMD is None, reason="VECTO release not found next to the repo")


def test_vmod_parser_reads_columns_and_integrates(tmp_path):
    p = tmp_path / "x_Urban.vmod"
    p.write_text("# VECTO test\n"
                 "time [s],dt [s],dist [m],v_act [km/h],acc [m/s^2],grad [%],P_roll [kW],P_air [kW],P_slope [kW],P_veh_inertia [kW],P_wheel_inertia [kW],DriverAction\n"
                 "0.5,1,0,0,0,0,0,0,0,0,0,Halt\n"
                 "1.5,1,5,36,0,1,3.6,1.0,0.5,0,0,Accelerate\n", encoding="utf-8")
    m = vecto.read_vmod(p)
    assert m.col("P_roll")[1] == 3.6
    assert m.energy_kwh("P_roll") == pytest.approx(3.6 / 3600)
    assert m["DriverAction"][1] == "Accelerate"
    cyc = vecto.cycle_from_vmod(m)
    assert cyc.time_s.size == 3 and cyc.speed_mps[2] == pytest.approx(10.0)


def test_route_writes_a_valid_vdri(tmp_path):
    r = Route(np.array([0.0, 100.0, 200.0, 300.0]), np.array([10.0, 12.0, 12.0, 9.0]),
              np.full(4, 50 / 3.6), [200.0], name="t")
    p = vecto.write_vdri(r, tmp_path / "t.vdri")
    lines = p.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "<s>,<v>,<grad>,<stop>"
    assert lines[3].startswith("200.0,50.00,") and lines[3].endswith(",18.0")   # the stop carries the dwell


@needs_vecto
def test_generic_city_bus_road_load_matches_vecto(tmp_path):
    """Same trace, same mass, same CdA, same tyres: rolling, air, grade and
    inertia energies must agree to within a few percent. The remaining gap is
    VECTO's speed-dependent rolling term and its own air-density handling."""
    d = vecto.stage_generic(CMD, "CityBus_AT", tmp_path)
    job = d / "CityBus_AT_Ser.vecto"
    vecto.set_job_cycles(job, ["UrbanDelivery.vdri"])
    run = vecto.run_job(job, CMD)
    m = run.vmod["UrbanDelivery"]
    p = vecto.vecto_vehicle_params(d / "CityBus_AT_Ser.vveh")
    ch = Chassis(kerb_mass_kg=p["mass_kg"], rolling_c0=p["rrc_load_corrected"], drag_coefficient=p["cda_m2"],
                 frontal_area_m2=1.0, tyre_dynamic_radius_m=p["rdyn_m"], rotational_inertia_kgm2=p["wheel_inertia_kgm2"])
    c = vecto.compare_roadload(m, ch, mass_kg=p["mass_kg"])
    assert abs(c.distance_km[0] - c.distance_km[1]) < 0.002 * c.distance_km[1]   # interval-end sampling, 0.2 %
    # the chassis model reproduces VECTO's road load term by term on VECTO's own trace
    for term in ("rolling", "air", "grade+", "inertia+", "wheel+", "wheel-"):
        assert abs(c.rel(term)) < 0.005, c.table()
    assert run.vsum and run.vsum[0]["Status"] == "Success"
