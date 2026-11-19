"""The list of what the simulator calculates: one source, three places, kept in step."""

from __future__ import annotations

import pathlib
import re

from temsasim import catalogue as C

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATIC = ROOT / "temsasim" / "app" / "static"


def test_generated_files_are_current():
    # docs/hesaplananlar.md and the first section of the calculation map are written from
    # temsasim/catalogue.py; after editing the list run: python -m temsasim hesaplananlar --write
    assert C.in_sync(), "catalogue.py changed: run `python -m temsasim hesaplananlar --write`"
    md = C.DOC_MD.read_text(encoding="utf-8")
    assert md.startswith("# Bu simülasyon neleri hesaplar") and md.count("\n## ") == len(C.GROUPS)
    page = C.MAP_HTML.read_text(encoding="utf-8")
    assert page.count(C.BEGIN) == 1 and page.count(C.END) == 1 and 'href="#liste"' in page
    assert page.index(C.BEGIN) < page.index('<section id="genel">')          # the list opens the calculation map


def test_every_group_points_at_a_real_tab_a_real_section_and_real_code():
    d = C.as_dict()
    assert d["count"] == sum(len(g["items"]) for g in d["groups"]) > 80 and len({g["key"] for g in d["groups"]}) == len(d["groups"])
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    pages = set(re.findall(r"(\w+):", re.search(r"const PAGES = \{(.*?)\};", app, re.S).group(1)))
    anchors = set(re.findall(r'id="([a-z]+)"', C.MAP_HTML.read_text(encoding="utf-8")))
    for g in d["groups"]:
        assert g["hash"].startswith("#/") and g["hash"].split("/")[1] in pages, g["key"]
        assert g["map"] in anchors, g["key"]
        for name in re.split(r"\s*·\s*", g["code"]):
            assert any((ROOT / "temsasim" / sub / name).exists() for sub in ("", "components", "app", "app/static")), f"{g['key']}: {name}"
        assert g["lede"].endswith(".") and len(g["items"]) >= 4
        for it in g["items"]:
            assert it["name"] and it["how"].endswith(".") and len(it["how"]) < 170, it["name"]
    names = [it["name"] for g in d["groups"] for it in g["items"]]
    assert len(names) == len(set(names))
    # the things the test team asked for are on the list
    text = C.to_markdown()
    for word in ("Dalma ve çökme açısı", "Devrilme eşiği", "Eğme (tipping) açısı", "Sönüm süresi", "Yakıt tüketimi", "Tüketim ve menzil",
                 "Vites hatları", "Kabin içi termal yönetim", "Yüksek voltaj ısıtma", "Veri tamlığı", "Krank gücü", "State of Health"):
        assert word in text, word


def test_help_endpoint_and_command(tmp_path, capsys):
    from conftest import merged_library
    from temsasim.app.server import create_app
    from temsasim.cli import main
    c = create_app(merged_library(tmp_path / "library")).test_client()
    d = c.get("/api/calculations").get_json()
    assert d == C.as_dict() and d["groups"][0]["items"][0]["name"] == "Yuvarlanma direnci"
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="helpBtn"' in index and "openHelp" in (STATIC / "app.js").read_text(encoding="utf-8")
    assert main(["hesaplananlar"]) == 0
    out = capsys.readouterr().out
    assert "## 6. Süspansiyon, yalpa ve devrilme" in out and "| Hesaplanan | Birim | Nasıl |" in out
