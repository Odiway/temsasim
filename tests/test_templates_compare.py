"""Upload templates round-trip through the importers; comparison report."""

from __future__ import annotations

import io
import pathlib
import zipfile

import pytest
import yaml

from temsasim.app.server import create_app
from temsasim.importers import detect_log_columns, import_curve, import_map, import_route, read_table
from temsasim.library import Library
from temsasim.schema import COMPONENT_KINDS
from temsasim.templates import all_templates_zip, build_template, list_templates, template_keys
from temsasim.testlog import read_can_log


@pytest.fixture()
def lib_copy(tmp_path):
    from conftest import merged_library
    return merged_library(tmp_path / "library")


@pytest.fixture()
def client(lib_copy):
    return create_app(lib_copy).test_client()


def test_catalogue_covers_every_builder(lib_copy):
    cat = list_templates(lib_copy)
    keys = {t["key"] for t in cat["items"]}
    assert keys == set(template_keys())
    groups = {g for g, _ in cat["groups"]}
    assert all(t["group"] in groups for t in cat["items"])
    for t in cat["items"]:
        assert t["upload"] and t["description"] and t["columns"]


def test_every_template_builds(lib_copy):
    for key in template_keys():
        data, fname, mime = build_template(key, lib_copy)
        assert data and fname and mime, key


def test_route_templates_import(lib_copy):
    for key in ("route_table", "route_latlon", "route_gpx"):
        data, fname, _ = build_template(key, lib_copy)
        r = import_route(data, fname)
        assert r.distance_m[-1] > 4000 and len(r.stops_m) >= 3, key


def test_map_and_curve_templates_import(lib_copy):
    for key in ("motor_map_grid", "motor_map_long"):
        data, fname, _ = build_template(key, lib_copy)
        imp = import_map(read_table(data, fname))
        pv = imp.preview()
        assert 0.7 < pv["peak"]["efficiency"] <= 1.0 and pv["shape"] == [7, 7], key
    data, fname, _ = build_template("curve_ocv", lib_copy)
    out = import_curve(read_table(data, fname), "ocv_curve")
    assert len(out["points"]) == 21 and out["points"][0][0] == 0.0
    data, fname, _ = build_template("curve_cop", lib_copy)
    out = import_curve(read_table(data, fname), "cop_curve")
    assert len(out["points"]) == 13


def test_log_templates_are_recognised(lib_copy):
    data, fname, _ = build_template("log_electric", lib_copy)
    det = detect_log_columns(read_table(data, fname))
    assert {"time", "speed", "power", "soc"} <= set(det["mapping"])
    data, fname, _ = build_template("log_diesel", lib_copy)
    log = read_can_log(data, fname)
    assert log.fuel_rate_lps is not None and log.engine_rpm is not None and log.time_s.size >= 179


def test_library_format_templates_load(lib_copy, tmp_path):
    from temsasim.cycle import DriveCycle
    data, fname, _ = build_template("cycle", lib_copy)
    p = lib_copy / "cycle" / "sablon_test.csv"; p.write_bytes(data)
    cyc = Library(lib_copy).load_cycle("sablon_test")
    assert isinstance(cyc, DriveCycle) and cyc.duration_s > 100
    from temsasim.maps import LossMap, Map2D
    for key, cls in (("engine_fuel_map", Map2D), ("loss_map", LossMap)):
        data, fname, _ = build_template(key, lib_copy)
        q = tmp_path / fname; q.write_bytes(data)
        assert cls.from_csv(q) is not None, key
    meta = yaml.safe_load(build_template("testlog_meta", lib_copy)[0].decode("utf-8"))
    assert "runs" in meta and meta["vehicle"]


def test_yaml_templates_import_through_endpoint(client, lib_copy):
    cat = {t["key"]: t for t in list_templates(lib_copy)["items"]}
    for key in ["scenario", "vehicle", "route_spec"] + [f"component_{k}" for k in COMPONENT_KINDS]:
        data, fname, _ = build_template(key, lib_copy)
        r = client.post("/api/import/yaml", data={"file": (io.BytesIO(data), fname)}, content_type="multipart/form-data")
        assert r.status_code == 200, (key, r.get_json())
        assert r.get_json()["saved"]
    # the scenario we just saved runs
    stems = [s["stem"] for s in client.get("/api/scenarios").get_json()["scenarios"]]
    stem = next(s for s in stems if "sablon" in s)
    r = client.post("/api/scenario/run", json={"stem": stem})
    assert r.status_code == 200 and r.get_json()["rows"]
    # the second upload of the same file is refused without overwrite
    data, fname, _ = build_template("scenario", lib_copy)
    r = client.post("/api/import/yaml", data={"file": (io.BytesIO(data), fname)}, content_type="multipart/form-data")
    assert r.status_code == 400
    r = client.post("/api/import/yaml", data={"file": (io.BytesIO(data), fname), "overwrite": "1"}, content_type="multipart/form-data")
    assert r.status_code == 200


def test_yaml_import_rejects_broken_file_and_leaves_nothing(client, lib_copy):
    bad = b"kind: chassis\nname: bozuk\nkerb_mass_kgg: 12000\n"
    r = client.post("/api/import/yaml", data={"file": (io.BytesIO(bad), "bozuk.yaml")}, content_type="multipart/form-data")
    assert r.status_code == 400
    assert not (lib_copy / "chassis" / "bozuk.yaml").exists()


def test_template_endpoints(client):
    r = client.get("/api/templates")
    assert r.status_code == 200 and len(r.get_json()["items"]) > 15
    r = client.get("/api/template/route_table")
    assert r.status_code == 200 and "guzergah_tablo" in r.headers["Content-Disposition"]
    r = client.get("/api/template/yok_boyle")
    assert r.status_code == 400
    r = client.get("/api/templates.zip")
    names = zipfile.ZipFile(io.BytesIO(r.data)).namelist()
    assert any(n.endswith(".gpx") for n in names) and any("OKU.txt" in n for n in names)


def test_compare_report(client):
    ov = client.get("/api/overview").get_json()
    vehicles = [v["stem"] for v in ov["vehicles"] if not v.get("error")][:2]
    route = ov["routes"][0]
    ids = []
    for v, amb in zip(vehicles * 2, (20, 35)):
        r = client.post("/api/simulate", json={"vehicle": v, "route": route, "ambient": amb, "passengers": 30, "traffic": "yok"})
        assert r.status_code == 200
        ids.append(r.get_json()["report_id"])
    r = client.post("/api/compare/report", json={"title": "Deneme", "runs": [{"report_id": ids[0], "label": "A 20 °C"}, {"report_id": ids[1], "label": "B 35 °C"}]})
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    assert len(j["facts"]) == 2 and j["facts"][0]["label"] == "A 20 °C"
    page = client.get(f"/report/{j['report_id']}")
    assert page.status_code == 200
    html = page.data.decode("utf-8")
    assert "A 20 °C" in html and "B 35 °C" in html and "<svg" in html and "Enerji nereye gitti" in html
    assert client.get(f"/report/{j['report_id']}/html").status_code == 200
    assert client.get(f"/report/{j['report_id']}/csv").status_code == 400
    r = client.post("/api/compare/report", json={"runs": [{"report_id": ids[0]}]})
    assert r.status_code == 400
