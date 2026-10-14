"""The web interface's server side: forms, uploads and saving.

These run the Flask app in-process through its test client, so they exercise
exactly the code path a browser click takes, without needing a browser. Every
test works on a copy of the library, never on the real one.
"""

from __future__ import annotations

import io
import pathlib
import shutil

import numpy as np
import pytest

from temsasim.app.server import create_app
from temsasim.library import KINDS, Library, build
from temsasim.maps import synth_motor_efficiency
from temsasim.schema import COMPONENT_KINDS, component_values, schema, to_yaml_dict
from temsasim.units import RPM

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture()
def lib_copy(tmp_path):
    dst = tmp_path / "library"
    shutil.copytree(ROOT / "library", dst,
                    ignore=shutil.ignore_patterns(".cop_kutusu"))
    return dst


@pytest.fixture()
def client(lib_copy):
    return create_app(lib_copy).test_client()


def _map_csv() -> bytes:
    """Turkish-locale supplier-style grid: ';' separators, decimal commas, %."""
    rpm = np.arange(250, 3001, 250)
    nm = np.arange(200, 2801, 200)
    m = synth_motor_efficiency(rpm * RPM, nm, peak_efficiency=0.96,
                               peak_at_speed_rads=1300 * RPM, peak_at_torque_nm=1200)
    lines = ["Tork [Nm] / Devir [rpm];" + ";".join(str(r) for r in rpm)]
    for j, t in enumerate(nm):
        lines.append(f"{t};" + ";".join(f"{v * 100:.2f}".replace(".", ",") for v in m.z[j]))
    return "\n".join(lines).encode("utf-8"), m


def _upload_map(client):
    data, _ = _map_csv()
    return client.post("/api/import/map", data={"file": (io.BytesIO(data), "harita.csv")},
                       content_type="multipart/form-data").get_json()


def test_every_component_round_trips_through_the_form_layer():
    lib = Library(ROOT / "library")
    for kind in COMPONENT_KINDS:
        for stem in lib.list_kind(kind):
            obj = lib.load_component(f"{kind}/{stem}.yaml", kind)
            types = {f["name"]: f["type"] for f in schema(kind)["fields"]}
            vals = {k: v for k, v in component_values(obj, kind).items() if types[k] != "map"}
            rebuilt = build(KINDS[kind], to_yaml_dict(kind, vals),
                            base_dir=lib.root / kind, where="round trip")
            for name, t in types.items():
                if t in ("float", "int"):
                    a, b = getattr(obj, name), getattr(rebuilt, name)
                    assert (a is None and b is None) or a == pytest.approx(b, rel=1e-6), (kind, name)


def test_new_motor_with_uploaded_map_is_saved_with_the_map_reference(client, lib_copy):
    """Regression: the form sends a new map as an upload id, not a value, and
    the reference used to be dropped -- the CSV was written but the motor kept
    its synthetic map."""
    up = _upload_map(client)
    assert up["settings"] == {"orientation": "speed_in_columns", "speed_unit": "rpm",
                              "value_type": "efficiency_percent"}
    r = client.post("/api/component/motor", json={
        "stem": "yeni motor",
        "values": {"name": "Yeni motor", "peak_power_w": 240, "peak_torque_nm": 2800},
        "map_uploads": {"efficiency_map": up["upload_id"]}})
    assert r.status_code == 200, r.get_json()
    motor = Library(lib_copy).load_component("motor/yeni_motor.yaml", "motor")
    assert not motor.efficiency_map.source.upper().startswith("SYNTHETIC")
    assert motor.peak_power_w == pytest.approx(240_000)          # entered in kW
    assert motor.efficiency_map(1300 * RPM, 1200) == pytest.approx(0.96, abs=3e-3)


def test_attaching_a_map_keeps_the_motor_files_comments(client, lib_copy):
    up = _upload_map(client)
    path = lib_copy / "motor" / "central_250kw_pmsm.yaml"
    assert "# Calibration points" in path.read_text(encoding="utf-8")
    r = client.post(f"/api/import/map/{up['upload_id']}/attach", json={"motor": "central_250kw_pmsm"})
    assert r.status_code == 200, r.get_json()
    after = path.read_text(encoding="utf-8")
    assert "efficiency_map: maps/central_250kw_pmsm_verim.csv" in after
    assert "# Calibration points" in after                         # comments survive
    m = Library(lib_copy).load_component("motor/central_250kw_pmsm.yaml", "motor")
    assert not m.efficiency_map.source.upper().startswith("SYNTHETIC")


def test_a_failed_save_writes_no_map_file(client, lib_copy):
    up = _upload_map(client)
    r = client.post("/api/component/motor", json={
        "stem": "central_250kw_pmsm", "values": {"name": "x"},
        "map_uploads": {"efficiency_map": up["upload_id"]}})
    assert r.status_code == 400
    assert "zaten var" in r.get_json()["error"]
    assert not (lib_copy / "motor" / "maps" / "central_250kw_pmsm_verim.csv").exists()


def test_unknown_field_from_the_form_is_rejected_with_a_readable_error(client):
    r = client.post("/api/component/motor",
                    json={"stem": "x", "values": {"name": "x", "tepe_guc": 5}})
    assert r.status_code == 400
    assert "tepe_guc" in r.get_json()["error"]


def test_component_in_use_cannot_be_deleted(client):
    r = client.post("/api/component/motor/central_250kw_pmsm/delete")
    assert r.status_code == 400
    assert "citybus_12m_be" in r.get_json()["error"]


def test_unused_component_goes_to_the_trash_not_away(client, lib_copy):
    assert client.post("/api/component/hvac/resistive_30kw/delete").status_code == 400
    (lib_copy / "vehicle" / "citybus_12m_be_resistive.yaml").unlink()
    r = client.post("/api/component/hvac/resistive_30kw/delete")
    assert r.status_code == 200
    assert not (lib_copy / "hvac" / "resistive_30kw.yaml").exists()
    assert list((lib_copy / ".cop_kutusu" / "hvac").glob("resistive_30kw_*.yaml"))


def test_vehicle_built_from_the_form_loads_and_simulates(client, lib_copy):
    r = client.post("/api/vehicle", json={
        "name": "Deneme", "variant": "form", "seats": 30, "standees": 55,
        "slots": {"chassis": "citybus_12m", "driveline": "direct_drive_6r2",
                  "motor": "central_250kw_pmsm", "battery": "nmc_350kwh",
                  "hvac": "resistive_30kw", "auxiliaries": "citybus_standard",
                  "aging": "generic_nmc"},
        "control": {"soc_start": 90, "regen_enabled": True}})
    assert r.status_code == 200, r.get_json()
    stem = r.get_json()["stem"]
    v = Library(lib_copy).load_vehicle(stem)
    assert v.control.soc_start == pytest.approx(0.90)       # entered as %, stored as fraction
    s = client.post("/api/simulate", json={"vehicle": stem, "cycle": "cbdbus",
                                           "ambient": 5, "passengers": 30}).get_json()
    assert 0.8 < s["kpi"]["kwh_per_km"] < 3.0
    assert client.get(f"/report/{s['report_id']}").status_code == 200


def test_log_upload_detects_columns_and_validation_runs(client, lib_copy):
    from temsasim.cycle import Environment
    from temsasim.simulator import simulate
    lib = Library(lib_copy)
    r = simulate(lib.load_vehicle("citybus_12m_be"), lib.load_cycle("cbdbus"),   # no passengers: the log has no occupancy column
                 Environment(ambient_c=10))
    s = r.series
    rows = ["Zaman (s);Hız [km/h];Güç [kW]"] + [
        f"{s['time_s'][i]:.0f};{s['speed_mps'][i] * 3.6:.2f};{s['p_battery_w'][i] / 1e3:.3f}".replace(".", ",")
        for i in range(s["time_s"].size)]
    up = client.post("/api/import/log",
                     data={"file": (io.BytesIO("\n".join(rows).encode()), "log.csv")},
                     content_type="multipart/form-data").get_json()
    assert set(up["mapping"]) >= {"time", "speed", "power"}
    v = client.post("/api/validate", json={
        "upload_id": up["upload_id"], "mapping": up["mapping"], "header_row": up["header_row"],
        "vehicle": "citybus_12m_be", "ambient": 10}).get_json()
    assert "metrics" in v, v
    assert abs(v["metrics"]["energy_error"]) < 0.02


def test_turkish_report_is_complete_and_english_stays_english():
    """Every translation key must exist in the template (render_report raises
    otherwise), headline English must be gone from the Turkish report, and the
    English report must be untouched."""
    from temsasim.cycle import Environment
    from temsasim.report import render_report
    from temsasim.simulator import simulate
    lib = Library(ROOT / "library")
    r = simulate(lib.load_vehicle("citybus_12m_be"), lib.load_cycle("cbdbus"),
                 Environment(ambient_c=-5))
    tr, en = render_report(r, lang="tr"), render_report(r, lang="en")
    for phrase in ("Where the energy went", "Along the route", "Projected pack life",
                   "TRACTION PACK", "burned in the friction brakes"):
        assert phrase not in tr and phrase in en
    assert "toLocaleString('tr-TR'" in tr
    import json
    assert json.dumps("Araçta ölçümle doğrulanmamış")[1:-1] in tr   # warning in Turkish (JSON-escaped)


def test_simulation_returns_a_consistent_playback_timeline(client):
    """The 3D view only interpolates what this returns, so it must be aligned,
    monotonic, and agree with the run it came from."""
    r = client.post("/api/simulate", json={"vehicle": "citybus_12m_be", "route": "demo_hilly_city",
                                           "ambient": -7, "passengers": 50}).get_json()
    pl = r["play"]
    tl = pl["timeline"]
    n = len(tl["t"])
    assert n > 100 and all(len(v) == n for v in tl.values())
    assert all(b >= a for a, b in zip(tl["t"], tl["t"][1:]))
    assert all(b >= a - 1e-6 for a, b in zip(tl["d"], tl["d"][1:]))
    # elevation integrated from the solver's gradient matches the route file
    route_elev = client.get("/api/preview?route=demo_hilly_city").get_json()["elev"]
    assert abs(max(tl["elev"]) - max(route_elev)) < 3.0
    assert abs(min(tl["elev"]) - min(route_elev)) < 3.0
    # cumulative battery energy per km agrees with the headline consumption
    per_km = tl["e_used"][-1] / (tl["d"][-1] / 1000)
    assert per_km == pytest.approx(r["kpi"]["kwh_per_km"], rel=0.02)
    kinds = {e["kind"] for e in pl["events"]}
    assert {"stop", "climb"} <= kinds
    assert len(pl["profile"]["stops"]) == sum(e["kind"] == "stop" for e in pl["events"])


def test_preview_and_errors_keep_their_status_codes(client):
    assert client.get("/api/preview?cycle=cbdbus").status_code == 200
    assert client.get("/api/does-not-exist").status_code == 404


def test_free_drive_setup_and_replay(client):
    s = client.post("/api/drive/setup", json={"vehicle": "citybus_12m_be", "ambient": 5,
                                              "passengers": 30}).get_json()
    assert s["mass_kg"] > 5000 and s["motor"]["peak_power_w"] > 0 and s["battery"]["usable_kwh"] > 0
    assert s["hvac_w"] > 0                                   # 5 degC: the cabin needs heat
    assert any(x["key"] == "parke" for x in s["surfaces"])
    n = 120
    t = list(range(n))
    v = [min(10.0, 0.8 * k) if k < 100 else max(0.0, 10.0 - 0.8 * (k - 100)) for k in t]
    body = {"vehicle": "citybus_12m_be", "ambient": 5, "passengers": 30, "t": t, "v": v,
            "grade_pct": [2.0] * n, "doors": [0] * n}
    smooth = client.post("/api/drive/simulate", json={**body, "surface": [1.0] * n}).get_json()
    rough = client.post("/api/drive/simulate", json={**body, "surface": [1.8] * n}).get_json()
    assert smooth["kpi"]["distance_km"] > 0.5
    assert rough["kwh"]["rolling_kwh"] > 1.7 * smooth["kwh"]["rolling_kwh"]
    assert rough["report_id"]


def test_free_drive_vehicle_settings_apply_to_a_copy(client, lib_copy):
    base = client.post("/api/drive/setup", json={"vehicle": "citybus_12m_be"}).get_json()
    heavy = client.post("/api/drive/setup", json={"vehicle": "citybus_12m_be", "tune": {
        "kerb_mass_kg": base["mass_kg"] + 2000, "motor_peak_kw": 160, "battery_kwh": 500}}).get_json()
    assert heavy["mass_kg"] == base["mass_kg"] + 2000
    assert heavy["motor"]["peak_power_w"] == 160_000
    assert abs(heavy["battery"]["nominal_kwh"] - 500) < 0.01
    assert heavy["tune"]["base"]["kerb_mass_kg"] == base["mass_kg"]       # the library vehicle is unchanged
    again = client.post("/api/drive/setup", json={"vehicle": "citybus_12m_be"}).get_json()
    assert again["mass_kg"] == base["mass_kg"]
    bad = client.post("/api/drive/setup", json={"vehicle": "citybus_12m_be", "tune": {"drag_cd": 3}})
    assert bad.status_code >= 400
    n = 90
    body = {"vehicle": "citybus_12m_be", "t": list(range(n)), "v": [min(10.0, 0.7 * k) for k in range(n)],
            "grade_pct": [0.0] * n}
    light = client.post("/api/drive/simulate", json=body).get_json()
    heavier = client.post("/api/drive/simulate", json={**body, "tune": {"kerb_mass_kg": base["mass_kg"] + 4000}}).get_json()
    assert heavier["kwh"]["traction_kwh"] > light["kwh"]["traction_kwh"]
