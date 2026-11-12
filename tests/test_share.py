"""Copy, export, import and raw edit of library items."""

from __future__ import annotations

import io
import shutil
import zipfile

import pytest
import yaml

from temsasim.library import Library
from temsasim.share import (closure, copy_item, export_library, export_zip, import_zip, inspect_zip, item_files,
                            read_raw, write_raw)


@pytest.fixture()
def lib_root(tmp_path, demo_library):
    dst = tmp_path / "library"
    shutil.copytree(demo_library, dst)
    return dst


def test_copy_component_duplicates_maps_and_points_the_copy_at_them(lib_root):
    out = copy_item(lib_root, "engine", "daf_mx_11_h5_330", "Deneme motoru")
    assert out["stem"] == "deneme_motoru"
    files = item_files(lib_root, "engine", "deneme_motoru")
    assert any(p.parent.name == "maps" and p.name.startswith("deneme_motoru") for p in files)
    text = (lib_root / "engine" / "deneme_motoru.yaml").read_text(encoding="utf-8")
    assert "maps/deneme_motoru_yakit.csv" in text and "daf_mx_11_h5_330_yakit" not in text
    assert yaml.safe_load(text)["name"] == "Deneme motoru"
    eng = Library(lib_root).load_component("engine/deneme_motoru.yaml", "engine")
    assert eng.rated_power_w == pytest.approx(330e3)
    # a second copy without a name gets a free stem, never overwrites
    again = copy_item(lib_root, "engine", "daf_mx_11_h5_330")
    assert again["stem"] == "daf_mx_11_h5_330_kopya"


def test_copy_route_and_scenario(lib_root):
    r = copy_item(lib_root, "route", "sehir_ici_ornek_20_durak", "Yeni hat")
    assert (lib_root / "route" / "yeni_hat.csv").exists() and (lib_root / "route" / "yeni_hat.yaml").exists()
    s = copy_item(lib_root, "scenario", "sehir_ici_yaz_zirve")
    assert s["stem"] == "sehir_ici_yaz_zirve_kopya"
    assert yaml.safe_load((lib_root / "scenario" / f"{s['stem']}.yaml").read_text(encoding="utf-8"))["name"].endswith("(kopya)")


def test_vehicle_package_carries_its_components_and_imports_into_an_empty_library(lib_root, tmp_path):
    deps = closure(lib_root, [("vehicle", "maraton_13_dizel")])
    assert ("engine", "daf_mx_11_h5_330") in deps and deps[-1] == ("vehicle", "maraton_13_dizel")
    data = export_zip(lib_root, [("vehicle", "maraton_13_dizel")])
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "vehicle/maraton_13_dizel.yaml" in names and "engine/maps/daf_mx_11_h5_330_yakit.csv" in names and "PAKET.yaml" in names
    empty = tmp_path / "empty"; empty.mkdir()
    info = inspect_zip(empty, data)
    kinds = {it["kind"] for it in info["items"]}
    assert "vehicle" in kinds and "engine" in kinds and not any(it["exists"] for it in info["items"])
    out = import_zip(empty, data)
    assert not out["errors"] and "vehicle/maraton_13_dizel.yaml" in out["written"]
    v = Library(empty).load_vehicle("maraton_13_dizel")
    assert v.is_diesel


def test_import_modes_skip_overwrite_rename(lib_root):
    data = export_zip(lib_root, [("scenario", "otoyol_kis_gecesi")])
    info = inspect_zip(lib_root, data)
    assert all(it["exists"] for it in info["items"])
    out = import_zip(lib_root, data, mode="skip")
    assert out["written"] == [] and "scenario/otoyol_kis_gecesi" in out["skipped"]
    out = import_zip(lib_root, data, mode="overwrite", select=["scenario/otoyol_kis_gecesi"])
    assert out["replaced"] == ["scenario/otoyol_kis_gecesi"] and (lib_root / ".cop_kutusu" / "scenario").exists()
    out = import_zip(lib_root, data, mode="rename")
    assert out["renamed"]["scenario/otoyol_kis_gecesi"] == "otoyol_kis_gecesi_2"
    assert out["renamed"]["route/otoyol_ornek_45_km"] == "otoyol_ornek_45_km_2"
    sc = yaml.safe_load((lib_root / "scenario" / "otoyol_kis_gecesi_2.yaml").read_text(encoding="utf-8"))
    assert sc["duty"]["route"] == "otoyol_ornek_45_km_2" and sc["vehicles"] == ["maraton_13_dizel_2"]
    assert not out["errors"]
    veh = yaml.safe_load((lib_root / "vehicle" / "maraton_13_dizel_2.yaml").read_text(encoding="utf-8"))
    assert veh["engine"] == "engine/daf_mx_11_h5_330_2.yaml"
    assert (lib_root / "engine" / "maps" / "daf_mx_11_h5_330_2_yakit.csv").exists()


def test_whole_library_zip_and_rejects_bad_zip(lib_root, tmp_path):
    data = export_library(lib_root)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "README.md" in names and "cycle/cbdbus.csv" in names and not any(".cop_kutusu" in n for n in names)
    other = tmp_path / "other"; other.mkdir()
    out = import_zip(other, data)
    assert not out["errors"] and (other / "vehicle" / "maraton_13_dizel.yaml").exists()
    with pytest.raises(ValueError):
        inspect_zip(lib_root, b"not a zip")
    # path traversal is ignored, not written
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../evil.yaml", "name: x"); z.writestr("engine/../../x.yaml", "name: y")
    info = inspect_zip(lib_root, buf.getvalue())
    assert info["items"] == [] and len(info["skipped"]) == 2


def test_raw_edit_is_validated_and_rolled_back(lib_root):
    raw = read_raw(lib_root, "hvac", "maraton_kabin")
    assert raw["path"] == "hvac/maraton_kabin.yaml" and "name:" in raw["text"]
    with pytest.raises(ValueError):
        write_raw(lib_root, "hvac", "maraton_kabin", raw["text"].replace("kind: hvac", "kind: hvac\nbogus_field_xyz: 1"))
    assert (lib_root / "hvac" / "maraton_kabin.yaml").read_text(encoding="utf-8") == raw["text"]
    write_raw(lib_root, "hvac", "maraton_kabin", raw["text"].replace("name: ", "name: Düzenlenmiş "))
    assert Library(lib_root).load_component("hvac/maraton_kabin.yaml", "hvac").name.startswith("Düzenlenmiş")


def test_share_endpoints(lib_root):
    from temsasim.app.server import create_app
    c = create_app(lib_root).test_client()
    r = c.post("/api/copy/vehicle/maraton_13_dizel", json={"name": "Maraton deneme"}); assert r.status_code == 200, r.get_json()
    assert r.get_json()["stem"] == "maraton_deneme"
    z = c.get("/api/export/vehicle/maraton_deneme"); assert z.status_code == 200 and z.mimetype == "application/zip"
    assert "attachment" in z.headers["Content-Disposition"]
    lz = c.get("/api/export/library"); assert lz.status_code == 200 and len(lz.data) > 1000
    up = c.post("/api/import/package", data={"file": (io.BytesIO(z.data), "paket.zip")}, content_type="multipart/form-data")
    assert up.status_code == 200, up.get_json()
    j = up.get_json(); assert j["items"] and all(it["exists"] for it in j["items"])
    sv = c.post(f"/api/import/package/{j['upload_id']}/save", json={"mode": "rename", "select": ["vehicle/maraton_deneme"]}).get_json()
    assert sv["renamed"] == {"vehicle/maraton_deneme": "maraton_deneme_2"} and not sv["errors"]
    f = c.get("/api/file/vehicle/maraton_deneme").get_json(); assert f["path"] == "vehicle/maraton_deneme.yaml"
    bad = c.post("/api/file/vehicle/maraton_deneme", json={"text": "kind: vehicle\nname: x\npowertrain: ucan\n"}); assert bad.status_code == 400
    ok = c.post("/api/file/vehicle/maraton_deneme", json={"text": f["text"]}); assert ok.status_code == 200
