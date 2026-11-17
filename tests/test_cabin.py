"""Layered cabin model: physics checks, the NGC Maraton file against the CFD
planes, and the hand-over into the lumped HVAC component."""

from __future__ import annotations

import io

import numpy as np
import pytest

from temsasim.app.server import create_app
from temsasim.cabin import (CabinModel, clear_sky, compare_with_reference, hvac_fields_from_cabin, load_cabin,
                            save_cabin, simulate, surface_irradiance)
from temsasim.library import Library


@pytest.fixture()
def lib_copy(tmp_path):
    from conftest import merged_library
    return merged_library(tmp_path / "library")


@pytest.fixture()
def client(lib_copy):
    return create_app(lib_copy).test_client()


def test_clear_sky_adana_afternoon_is_plausible():
    sun = clear_sky(37.0, 35.32, 200, 15.0, 3.0)
    assert 45 < sun["altitude_deg"] < 65 and 230 < sun["azimuth_deg"] < 270
    assert 700 < sun["i_dn"] < 950 and 60 < sun["i_dh"] < 160
    night = clear_sky(37.0, 35.32, 200, 23.0, 3.0)
    assert night["i_dn"] == 0.0
    roof = surface_irradiance(sun, "roof", 0.0)
    assert roof > surface_irradiance(sun, "front", 90.0)          # east-facing front sees diffuse only
    assert surface_irradiance(sun, "floor", 0.0) == 0.0


def test_ngc_cabin_cooldown_tracks_the_cfd_planes(lib_copy):
    m = load_cabin(lib_copy, "ngc_maraton")
    r = simulate(m)
    cmp = compare_with_reference(m, r)
    cfd = next(s for s in cmp["series"] if s["key"] == "cfd")
    assert cfd["rmse_c"] < 3.0
    for p in cfd["points"]:
        assert abs(p["diff_c"]) < 3.0, p
    # monotone cool-down from the soak, and the CFD acceptance at 30 minutes
    air = np.asarray(r["air_c"])
    assert air[0] == 55.0 and np.all(np.diff(air[30:]) <= 1e-6)
    assert r["at_minutes"][30] < 31.0
    crit = {c["at_min"]: c for c in cmp["criteria"]}
    assert crit[30.0]["ok"]
    # the sun matters: without it the cabin ends cooler
    assert simulate(m, solar=False)["at_minutes"][40] < r["at_minutes"][40]
    # the engine-bay floor is hotter than the luggage floor at the end
    assert r["inner_c"]["Taban (motor üstü)"][-1] > r["inner_c"]["Taban (bagaj üstü)"][-1]


def test_lumped_equivalent_and_hvac_override(lib_copy):
    L = Library(lib_copy)
    m = load_cabin(lib_copy, "ngc_maraton")
    f = hvac_fields_from_cabin(m)
    assert 27 < f["glazing_area_m2"] < 30 and 5.0 < f["u_glazing_w_per_m2k"] < 6.5
    assert f["interior_thermal_mass_j_per_k"] > 1.0e6 and 0.7 < f["shgc"] < 0.95
    h = L.load_component("hvac/ngc_maraton_kabin.yaml", "hvac")
    assert h.cabin_model == "cabin/ngc_maraton.yaml"
    assert h.glazing_area_m2 == pytest.approx(f["glazing_area_m2"])
    assert h.interior_thermal_mass_j_per_k == pytest.approx(f["interior_thermal_mass_j_per_k"])
    assert h.ua_w_per_k == pytest.approx(m.lumped_equivalent()["ua_w_per_k"], rel=1e-3)
    # a dangling reference is an error, not a silent fallback
    (lib_copy / "hvac" / "kirik.yaml").write_text("kind: hvac\nname: kirik\ncabin_model: cabin/yok.yaml\n", encoding="utf-8")
    with pytest.raises(Exception):
        L.load_component("hvac/kirik.yaml", "hvac")


def test_cabin_endpoints_roundtrip(client, lib_copy):
    r = client.get("/api/cabins")
    assert r.status_code == 200 and any(c["stem"] == "ngc_maraton" for c in r.get_json()["cabins"])
    assert any(hv["cabin_model"] for hv in r.get_json()["hvacs"])
    r = client.get("/api/cabin/ngc_maraton")
    cab = r.get_json()["cabin"]
    assert len(cab["surfaces"]) >= 15 and "Tavan" in r.get_json()["surface_u"]
    # edit: thicker roof insulation, save as a copy, run it
    cab["name"] = "NGC kalın tavan"
    cab["surfaces"][0]["layers"][1]["thickness_mm"] = 80.0
    r = client.post("/api/cabin", json={"cabin": cab, "stem": "", "overwrite": False})
    assert r.status_code == 200 and r.get_json()["stem"] == "ngc_kalin_tavan"
    r = client.post("/api/cabin/run", json={"stem": "ngc_kalin_tavan", "scenario": {"duration_min": 20, "dt_s": 20}})
    assert r.status_code == 200
    j = r.get_json()
    assert 20 in {int(k) for k in j["result"]["at_minutes"]} and len(j["result"]["t_s"]) == 61
    # link to an HVAC component, then unlink
    r = client.post("/api/cabin/ngc_kalin_tavan/link", json={"hvac": "maraton_kabin"})
    assert r.status_code == 200 and r.get_json()["cabin_model"] == "cabin/ngc_kalin_tavan.yaml"
    assert Library(lib_copy).load_component("hvac/maraton_kabin.yaml", "hvac").cabin_model.endswith("ngc_kalin_tavan.yaml")
    r = client.post("/api/cabin/ngc_kalin_tavan/delete", json={})
    assert r.status_code == 400                                   # in use
    r = client.post("/api/cabin/ngc_kalin_tavan/link", json={"hvac": "maraton_kabin", "unlink": True})
    assert r.status_code == 200 and r.get_json()["cabin_model"] == ""
    r = client.post("/api/cabin/ngc_kalin_tavan/delete", json={})
    assert r.status_code == 200
    # template imports through the YAML endpoint
    data = client.get("/api/template/cabin").data
    r = client.post("/api/import/yaml", data={"file": (io.BytesIO(data), "kabin_sablonu.yaml")}, content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["kind"] == "cabin"


def test_cabin_model_validation(tmp_path):
    with pytest.raises(ValueError):
        CabinModel(name="x", surfaces=[{"name": "a", "area_m2": 1, "layers": [{"material": "yok", "thickness_mm": 3}]}])
    m = CabinModel(name="Deneme", surfaces=[{"name": "a", "area_m2": 10, "layers": [{"material": "glass", "thickness_mm": 6.4}], "transmissivity": 0.8}],
                   inlets=[{"name": "b", "mass_flow_kg_s": 1.0, "supply_c": [[0, 20.0]]}])
    p = save_cabin(tmp_path, m)
    assert p.name == "deneme.yaml" and load_cabin(tmp_path, "deneme").surfaces[0].glazed
    r = simulate(m, ambient_c=30, initial_c=50, duration_s=600, solar=False)
    assert r["air_c"][-1] < 45 and r["nodes"] == 2 + 2        # air, interior, two glass cells
