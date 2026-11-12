"""Copy, export and import of library items.

A library item is a small set of files: a component is its YAML plus the map
CSVs it references, a route is a CSV plus (if built in the route builder) its
YAML spec, a vehicle is one YAML that references components, a scenario is one
YAML that references a route and vehicles. This module knows those shapes so
that an item can be

* copied inside the library (``copy_item``), maps and all, under a new name;
* packed into a zip with everything it depends on (``export_zip``), so a
  colleague drops the zip into their own library and the vehicle runs;
* unpacked from such a zip (``inspect_zip`` / ``import_zip``) with a clear
  list of what exists already and what will be written.

Zip layout mirrors the library (``engine/x.yaml``, ``engine/maps/x.csv``), so
a package is also readable without the program, and a package of the whole
library is just the library folder zipped. ``PAKET.yaml`` at the root records
what the package contains.
"""

from __future__ import annotations

import io
import json
import pathlib
import re
import time
import zipfile

import yaml

from .schema import COMPONENT_KINDS, KIND_LABELS_TR, safe_stem
from .vehicle import SLOTS_BY_POWERTRAIN

ITEM_KINDS = list(COMPONENT_KINDS) + ["vehicle", "route", "scenario", "line", "cycle"]
LABELS_TR = {**KIND_LABELS_TR, "route": "Güzergâh", "scenario": "Senaryo", "line": "Hat", "cycle": "Çevrim"}
PACKAGE_VERSION = 1
_SKIP_DIRS = {".cop_kutusu", "__pycache__"}


# ---------------------------------------------------------------- files of an item
def _yaml(path: pathlib.Path) -> dict:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _map_refs(data, out: list) -> None:
    """Every ``path:`` or bare ``maps/x.csv`` string in a component's YAML."""
    if isinstance(data, dict):
        for k, v in data.items():
            if k == "path" and isinstance(v, str):
                out.append(v)
            else:
                _map_refs(v, out)
    elif isinstance(data, list):
        for v in data:
            _map_refs(v, out)
    elif isinstance(data, str) and data.endswith(".csv") and "/" in data and not data.startswith(("route/", "cycle/")):
        out.append(data)


def item_files(root: pathlib.Path, kind: str, stem: str) -> list[pathlib.Path]:
    """Files that make up one item, as absolute paths under the library root."""
    root = pathlib.Path(root).resolve()
    if kind not in ITEM_KINDS:
        raise ValueError(f"Bilinmeyen tür: {kind}")
    if kind == "route":
        return [p for p in (root / "route" / f"{stem}.csv", root / "route" / f"{stem}.yaml") if p.exists()]
    if kind == "cycle":
        p = root / "cycle" / f"{stem}.csv"
        return [p] if p.exists() else []
    p = root / kind / f"{stem}.yaml"
    if not p.exists():
        return []
    files = [p]
    if kind in COMPONENT_KINDS:
        refs: list = []
        _map_refs(_yaml(p), refs)
        for r in refs:
            q = (p.parent / r).resolve()
            if q.exists() and q not in files and root.resolve() in q.parents:
                files.append(q)
    return files


def item_deps(root: pathlib.Path, kind: str, stem: str) -> list[tuple[str, str]]:
    """Other items this one references (vehicle -> components, scenario -> route + vehicles, line -> route)."""
    root = pathlib.Path(root).resolve()
    out: list[tuple[str, str]] = []
    if kind == "vehicle":
        raw = _yaml(root / "vehicle" / f"{stem}.yaml")
        slots = SLOTS_BY_POWERTRAIN.get(raw.get("powertrain", "battery_electric"), {})
        for slot, slot_kind in slots.items():
            ref = raw.get(slot)
            ref = ref.get("ref") if isinstance(ref, dict) else ref
            if ref:
                out.append((slot_kind, pathlib.Path(str(ref)).stem))
    elif kind == "scenario":
        raw = _yaml(root / "scenario" / f"{stem}.yaml")
        duty = raw.get("duty") or {}
        if duty.get("route"):
            out.append(("route", str(duty["route"])))
        if duty.get("cycle"):
            out.append(("cycle", str(duty["cycle"])))
        for v in raw.get("vehicles") or []:
            out.append(("vehicle", str(v)))
    elif kind == "line":
        raw = _yaml(root / "line" / f"{stem}.yaml")
        if raw.get("route"):
            out.append(("route", pathlib.Path(str(raw["route"])).stem))
    return out


def closure(root: pathlib.Path, items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The items plus everything they depend on, dependencies first."""
    seen: list[tuple[str, str]] = []

    def visit(kind, stem):
        if (kind, stem) in seen:
            return
        for d in item_deps(root, kind, stem):
            visit(*d)
        seen.append((kind, stem))

    for k, s in items:
        visit(k, s)
    return seen


def item_name(root: pathlib.Path, kind: str, stem: str) -> str:
    files = item_files(root, kind, stem)
    for p in files:
        if p.suffix == ".yaml":
            n = _yaml(p).get("name")
            if n:
                return str(n)
    return stem


def _free_stem(folder: pathlib.Path, stem: str, exts: tuple[str, ...]) -> str:
    cand, i = stem, 2
    while any((folder / f"{cand}{e}").exists() for e in exts):
        cand = f"{stem}_{i}"
        i += 1
    return cand


def _set_key(text: str, key: str, value: str) -> str:
    """Set one top-level scalar key in YAML text, keeping comments and order."""
    line = f"{key}: {json.dumps(value, ensure_ascii=False)}"
    pat = re.compile(rf"^{re.escape(key)}\s*:[^\n]*(\n[ \t]+[^\n]*)*", re.M)
    m = pat.search(text)
    if not m:
        return text.rstrip("\n") + "\n" + line + "\n"
    # a folded/quoted multi-line value: replace the whole block
    return text[: m.start()] + line + text[m.end():]


# ---------------------------------------------------------------- copy
def copy_item(root: pathlib.Path, kind: str, stem: str, new_name: str | None = None) -> dict:
    """Duplicate an item under a new stem. Maps are copied too and the copy's
    YAML points at the copies, so editing one never touches the other."""
    root = pathlib.Path(root).resolve()
    files = item_files(root, kind, stem)
    if not files:
        raise FileNotFoundError(f"{LABELS_TR.get(kind, kind)} bulunamadı: {stem}")
    old_name = item_name(root, kind, stem)
    new_name = (new_name or f"{old_name} (kopya)").strip()
    folder = root / kind
    exts = (".csv", ".yaml") if kind in ("route", "cycle") else (".yaml",)
    new_stem = _free_stem(folder, safe_stem(new_name) if new_name != f"{old_name} (kopya)" else f"{stem}_kopya", exts)
    written = []
    for p in files:
        if p.parent.name == "maps":
            continue
        text = p.read_text(encoding="utf-8")
        if p.suffix == ".yaml":
            text = _set_key(text, "name", new_name)
            if kind in COMPONENT_KINDS:
                for m in [q for q in files if q.parent.name == "maps"]:
                    tail = m.name[len(stem):] if m.name.startswith(stem) else "_" + m.name
                    new_map = m.with_name(new_stem + tail)
                    new_map = new_map.with_name(_free_stem(m.parent, new_map.stem, (".csv",)) + ".csv")
                    new_map.write_text(m.read_text(encoding="utf-8"), encoding="utf-8")
                    text = text.replace(f"maps/{m.name}", f"maps/{new_map.name}")
                    written.append(new_map)
            if kind == "vehicle" or kind == "scenario" or kind == "route":
                text = f"# {old_name} kopyasından ({time.strftime('%Y-%m-%d')}).\n" + text
        dst = folder / (new_stem + p.suffix)
        dst.write_text(text, encoding="utf-8")
        written.append(dst)
    return {"stem": new_stem, "name": new_name, "kind": kind,
            "files": [str(w.relative_to(root)).replace("\\", "/") for w in written]}


# ---------------------------------------------------------------- export
def _manifest(root: pathlib.Path, items: list[tuple[str, str]], note: str = "") -> str:
    data = {"temsasim_package": PACKAGE_VERSION, "created": time.strftime("%Y-%m-%d %H:%M"),
            "note": note or "temsasim kütüphane paketi. Zip'i 'Veri yükle → Paket' ile ya da "
                            "'python -m temsasim import' ile kendi kütüphanenize alın.",
            "items": [{"kind": k, "stem": s, "name": item_name(root, k, s)} for k, s in items]}
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def export_zip(root: pathlib.Path, items: list[tuple[str, str]], *, with_deps: bool = True) -> bytes:
    """Zip the items (and, by default, everything they reference)."""
    root = pathlib.Path(root).resolve()
    todo = closure(root, items) if with_deps else list(items)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        seen = set()
        for k, s in todo:
            files = item_files(root, k, s)
            if not files:
                raise FileNotFoundError(f"{LABELS_TR.get(k, k)} bulunamadı: {s}")
            for p in files:
                rel = str(p.relative_to(root)).replace("\\", "/")
                if rel not in seen:
                    z.write(p, rel)
                    seen.add(rel)
        z.writestr("PAKET.yaml", _manifest(root, todo))
    return buf.getvalue()


def export_library(root: pathlib.Path) -> bytes:
    """The whole library folder as a zip (minus the trash)."""
    root = pathlib.Path(root).resolve()
    buf = io.BytesIO()
    items = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if p.is_dir() or any(part in _SKIP_DIRS for part in rel.parts):
                continue
            z.write(p, str(rel).replace("\\", "/"))
            if len(rel.parts) == 2 and rel.parts[0] in ITEM_KINDS and rel.suffix in (".yaml", ".csv"):
                if rel.parts[0] == "route" and rel.suffix == ".yaml":
                    continue
                items.append((rel.parts[0], rel.stem))
        z.writestr("PAKET.yaml", _manifest(root, items, note="Kütüphanenin tamamı."))
    return buf.getvalue()


# ---------------------------------------------------------------- import
def _safe_rel(name: str) -> pathlib.PurePosixPath | None:
    p = pathlib.PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] not in ITEM_KINDS:
        return None
    if len(p.parts) == 2 and p.suffix in (".yaml", ".csv"):
        return p
    if len(p.parts) == 3 and p.parts[1] == "maps" and p.suffix == ".csv":
        return p
    return None


def inspect_zip(root: pathlib.Path, data: bytes) -> dict:
    """What a package contains, grouped into items, and whether each exists here."""
    root = pathlib.Path(root).resolve()
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("Bu bir zip dosyası değil. Paketler 'İndir' ile üretilen .zip dosyalarıdır.")
    names = [n for n in z.namelist() if not n.endswith("/")]
    # Allow a zip made of the library folder itself (library/engine/x.yaml).
    prefix = ""
    if names and all(n.split("/")[0] not in ITEM_KINDS and n != "PAKET.yaml" for n in names):
        first = names[0].split("/")[0]
        if all(n.startswith(first + "/") for n in names):
            prefix = first + "/"
    manifest = {}
    if prefix + "PAKET.yaml" in names:
        try:
            manifest = yaml.safe_load(z.read(prefix + "PAKET.yaml").decode("utf-8")) or {}
        except Exception:
            manifest = {}
    items: dict[tuple[str, str], dict] = {}
    skipped = []
    # maps first, so the YAML that references them finds them already listed
    for n in sorted(names, key=lambda x: (x.count("/") != 2, x)):
        rel = _safe_rel(n[len(prefix):]) if n.startswith(prefix) else None
        if rel is None:
            if n != prefix + "PAKET.yaml":
                skipped.append(n)
            continue
        kind = rel.parts[0]
        if len(rel.parts) == 3:
            # maps are attached later via the YAML that references them
            items.setdefault(("_map", kind + "/" + rel.name), {"kind": kind, "stem": rel.stem, "map": True, "files": [str(rel)]})
            continue
        stem = rel.stem
        key = (kind, stem)
        it = items.setdefault(key, {"kind": kind, "kind_tr": LABELS_TR.get(kind, kind), "stem": stem, "name": stem, "files": []})
        it["files"].append(str(rel))
        if rel.suffix == ".yaml":
            try:
                raw = yaml.safe_load(z.read(n).decode("utf-8")) or {}
                it["name"] = str(raw.get("name") or stem)
                it["description"] = " ".join(str(raw.get("description", "")).split())[:200]
                refs: list = []
                _map_refs(raw, refs)
                for r in refs:
                    mp = f"{kind}/{r}"
                    mkey = ("_map", f"{kind}/{pathlib.PurePosixPath(r).name}")
                    if mkey in items:
                        it["files"].append(mp)
                        items[mkey]["used"] = True
                    else:
                        it.setdefault("missing", []).append(r)
            except Exception as exc:
                it["error"] = f"YAML okunamadı: {exc}"
    out = []
    for key, it in items.items():
        if key[0] == "_map":
            if not it.get("used"):
                skipped.append(it["files"][0])
            continue
        main = next((f for f in it["files"] if len(f.split("/")) == 2 and (f.endswith(".yaml") or it["kind"] in ("route", "cycle"))), it["files"][0])
        it["exists"] = (root / main).exists()
        out.append(it)
    order = {k: i for i, k in enumerate(ITEM_KINDS)}
    out.sort(key=lambda it: (order.get(it["kind"], 99), it["stem"]))
    return {"items": out, "manifest": manifest, "skipped": skipped, "prefix": prefix}


def import_zip(root: pathlib.Path, data: bytes, *, mode: str = "skip", select: list[str] | None = None) -> dict:
    """Write the package's items into the library.

    ``mode``: ``skip`` leaves existing items untouched, ``overwrite`` replaces
    them, ``rename`` writes conflicting items under a free stem and rewrites
    the references inside the package to the new stems. ``select`` limits the
    import to the listed ``kind/stem`` keys."""
    root = pathlib.Path(root).resolve()
    info = inspect_zip(root, data)
    z = zipfile.ZipFile(io.BytesIO(data))
    prefix = info["prefix"]
    chosen = [it for it in info["items"] if not it.get("error") and (select is None or f"{it['kind']}/{it['stem']}" in select)]
    renames: dict[tuple[str, str], str] = {}
    written, skipped, replaced = [], [], []
    if mode == "rename":
        for it in chosen:
            if it["exists"]:
                exts = (".csv", ".yaml") if it["kind"] in ("route", "cycle") else (".yaml",)
                renames[(it["kind"], it["stem"])] = _free_stem(root / it["kind"], it["stem"], exts)

    def rewrite(kind: str, text: str) -> str:
        for (k, old), new in renames.items():
            text = text.replace(f"{k}/{old}.yaml", f"{k}/{new}.yaml").replace(f"{k}/{old}.csv", f"{k}/{new}.csv")
            if kind == "scenario":
                text = re.sub(rf"(route:\s*){re.escape(old)}\b", rf"\g<1>{new}", text) if k == "route" else text
                text = re.sub(rf"(^\s*-\s*){re.escape(old)}\s*$", rf"\g<1>{new}", text, flags=re.M) if k == "vehicle" else text
                text = re.sub(rf"(vehicles:\s*\[[^\]]*)\b{re.escape(old)}\b", rf"\g<1>{new}", text) if k == "vehicle" else text
        return text

    for it in chosen:
        kind, stem = it["kind"], it["stem"]
        if it["exists"] and mode == "skip":
            skipped.append(f"{kind}/{stem}")
            continue
        new_stem = renames.get((kind, stem), stem)
        for f in it["files"]:
            rel = pathlib.PurePosixPath(f)
            raw = z.read(prefix + f)
            if rel.suffix == ".yaml":
                text = rewrite(kind, raw.decode("utf-8"))
                if new_stem != stem and kind in COMPONENT_KINDS:
                    for mf in it["files"]:
                        mrel = pathlib.PurePosixPath(mf)
                        if len(mrel.parts) == 3:
                            text = text.replace(f"maps/{mrel.name}", f"maps/{_renamed_map(mrel.name, stem, new_stem)}")
                raw = text.encode("utf-8")
            if len(rel.parts) == 3:
                dst = root / kind / "maps" / (_renamed_map(rel.name, stem, new_stem) if new_stem != stem else rel.name)
            else:
                dst = root / kind / f"{new_stem}{rel.suffix}"
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() and mode == "overwrite":
                _to_trash(root, dst)
            dst.write_bytes(raw)
            written.append(str(dst.relative_to(root)).replace("\\", "/"))
        if it["exists"] and mode == "overwrite":
            replaced.append(f"{kind}/{stem}")
    errors = _check(root, [(it["kind"], renames.get((it["kind"], it["stem"]), it["stem"])) for it in chosen
                           if not (it["exists"] and mode == "skip")])
    return {"written": written, "skipped": skipped, "replaced": replaced,
            "renamed": {f"{k}/{s}": n for (k, s), n in renames.items()}, "errors": errors}


def _renamed_map(name: str, stem: str, new_stem: str) -> str:
    return new_stem + name[len(stem):] if name.startswith(stem) else f"{new_stem}_{name}"


def _to_trash(root: pathlib.Path, path: pathlib.Path) -> None:
    trash = root / ".cop_kutusu" / path.relative_to(root).parent
    trash.mkdir(parents=True, exist_ok=True)
    path.replace(trash / f"{path.stem}_{time.strftime('%Y%m%d_%H%M%S')}{path.suffix}")


def _check(root: pathlib.Path, items: list[tuple[str, str]]) -> list[str]:
    """Load every written item once; a package from an older version may not fit."""
    from .library import Library
    errors = []
    L = Library(root)
    for kind, stem in items:
        try:
            if kind in COMPONENT_KINDS:
                L.load_component(f"{kind}/{stem}.yaml", kind)
            elif kind == "vehicle":
                L.load_vehicle(stem)
            elif kind == "route":
                from .cycle import Route
                Route.from_csv(root / "route" / f"{stem}.csv")
            elif kind == "scenario":
                from .scenarios import load_scenario
                load_scenario(root, stem)
        except Exception as exc:
            errors.append(f"{kind}/{stem}: {exc}")
    return errors


# ---------------------------------------------------------------- raw file access
def read_raw(root: pathlib.Path, kind: str, stem: str) -> dict:
    root = pathlib.Path(root).resolve()
    files = item_files(root, kind, stem)
    if not files:
        raise FileNotFoundError(f"{LABELS_TR.get(kind, kind)} bulunamadı: {stem}")
    main = next((p for p in files if p.suffix == ".yaml"), files[0])
    return {"kind": kind, "stem": stem, "path": str(main.relative_to(root)).replace("\\", "/"),
            "text": main.read_text(encoding="utf-8"),
            "files": [str(p.relative_to(root)).replace("\\", "/") for p in files]}


def write_raw(root: pathlib.Path, kind: str, stem: str, text: str) -> dict:
    """Replace the item's main file with hand-edited text, but only if the
    loader accepts it: the old text is put back otherwise."""
    root = pathlib.Path(root).resolve()
    info = read_raw(root, kind, stem)
    p = root / info["path"]
    if p.suffix == ".yaml":
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError(f"YAML okunamadı: {exc}")
        if not isinstance(parsed, dict):
            raise ValueError("Dosya bir YAML eşlemesi (anahtar: değer) olmalı.")
    old = p.read_text(encoding="utf-8")
    p.write_text(text, encoding="utf-8")
    errors = _check(root, [(kind, stem)])
    if errors:
        p.write_text(old, encoding="utf-8")
        raise ValueError("Dosya kaydedilmedi, yükleyici kabul etmedi: " + errors[0].split(": ", 1)[-1])
    return {"saved": info["path"]}
