"""From a folder of VECTO files to a vehicle in the library.

``read_bundle`` takes whatever the engineer uploads -- single XML files, a
zip, a whole folder -- and classifies each file with ``vecto_xml``.
``plan_twin`` resolves the bundle into one vehicle and says, for every piece
of data, where it came from and what had to be assumed. ``write_twin`` writes
the component YAML files, the map CSVs and the vehicle file, and only then
loads the result back through the normal library path to prove it is usable.

Resolution rules, in this order:

* A standalone certified component file (engine, gearbox, axle, tyre,
  retarder, air drag) beats the copy embedded in a primary-vehicle file.
  The primary file shared by homologation is an architecture example and
  says so.
* Vehicle-level numbers (masses, seats, dimensions, CdxA, HVAC fittings)
  come from the completed-bus (step) file, then the primary file.
* Axle load shares are not in any VECTO input file: VECTO takes them from
  its segmentation table by axle count, and so does this importer.
* Tyre dynamic radius and wheel inertia come from VECTO's wheel table.

Everything assumed is written into the YAML description and returned in
``warnings``, so nothing silently becomes "measured".
"""

from __future__ import annotations

import io
import math
import pathlib
import zipfile
from dataclasses import dataclass, field

import numpy as np
import yaml

from .library import Library
from .maps import LossMap
from .units import RPM
from .vecto_xml import KINDS_TR, VectoFile, read_vecto_file, safe_name

# VECTO Declaration/Wheels.csv: dimension -> (diameter mm, inertia per wheel kg m2, circumference factor)
WHEELS = {
    "245/70 R19.5": (839.0, 6.0, 3.05), "265/70 R19.5": (867.0, 6.5, 3.05), "285/70 R19.5": (895.0, 7.9, 3.05),
    "305/70 R19.5": (923.0, 9.2, 3.05), "255/70 R22.5": (930.0, 9.5, 3.05), "275/70 R22.5": (958.0, 11.9, 3.05),
    "275/80 R22.5": (1012.0, 12.8, 3.05), "295/75 R22.5": (1014.0, 14.0, 3.05), "295/80 R22.5": (1044.0, 15.5, 3.05),
    "305/70 R22.5": (1000.0, 13.9, 3.05), "315/70 R22.5": (1014.0, 14.9, 3.05), "315/80 R22.5": (1076.0, 17.6, 3.05),
    "385/65 R22.5": (1072.0, 19.2, 3.03), "11 R22.5": (1050.0, 14.4, 3.05), "12 R22.5": (1084.0, 16.85, 3.05),
}
# VECTO PrimaryBusSegmentationTable: axle weight shares by axle count (non-articulated / articulated)
AXLE_SHARES = {(2, False): [0.375, 0.625], (3, False): [0.273, 0.454, 0.273],
               (3, True): [0.243, 0.352, 0.405], (4, False): [0.214, 0.214, 0.358, 0.214]}
GENERIC_COACH_CDXA = 3.45          # VECTO P32SD standard CdxA
RETARDER_TYPES = {"transmission output retarder": "transmission_output",
                  "transmission input retarder": "transmission_input",
                  "secondary retarder": "transmission_output", "primary retarder": "transmission_input",
                  "none": "none", "engine retarder": "none", "losses included in gearbox": "none",
                  "axlegear input retarder": "transmission_output"}


@dataclass
class VectoBundle:
    files: list

    def of(self, kind: str) -> list:
        return [f for f in self.files if f.kind == kind]

    def first(self, kind: str):
        xs = self.of(kind)
        return xs[0] if xs else None

    def summary(self) -> dict:
        return {"files": [f.summary() for f in self.files],
                "kinds": sorted({f.kind for f in self.files})}


def read_bundle(items) -> VectoBundle:
    """``items`` is an iterable of ``(filename, bytes)``; zips are expanded."""
    files: list = []
    for name, data in items:
        lname = str(name).lower()
        if lname.endswith(".zip") or data[:4] == b"PK\x03\x04":
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    for info in z.infolist():
                        if info.is_dir() or not info.filename.lower().endswith((".xml", ".vecto")):
                            continue
                        files.append(read_vecto_file(z.read(info), pathlib.Path(info.filename).name))
            except zipfile.BadZipFile:
                files.append(VectoFile(pathlib.Path(name).name, "unknown", warnings=["Zip açılamadı."]))
            continue
        if not lname.endswith((".xml", ".vecto")):
            files.append(VectoFile(pathlib.Path(name).name, "unknown",
                                   warnings=["VECTO dosyası değil (.xml ya da .vecto bekleniyor); atlandı."]))
            continue
        files.append(read_vecto_file(data, pathlib.Path(name).name))
    return VectoBundle(files)


def read_folder(folder) -> VectoBundle:
    folder = pathlib.Path(folder)
    items = [(p.name, p.read_bytes()) for p in sorted(folder.rglob("*"))
             if p.is_file() and p.suffix.lower() in (".xml", ".vecto", ".zip")]
    return read_bundle(items)


# ---------------------------------------------------------------- resolution
@dataclass
class TwinPlan:
    """What the twin will be built from. Every entry says where it came from."""

    engine: dict | None = None
    gearbox: dict | None = None
    axlegear: dict | None = None
    retarder: dict | None = None
    tyres: list = field(default_factory=list)          # one dict per axle
    cdxa_m2: float | None = None
    vehicle: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    missing: list = field(default_factory=list)

    def summary(self) -> dict:
        v = self.vehicle
        rows = []

        def row(key, label, value, src):
            rows.append({"key": key, "label": label, "value": value, "source": src})

        row("engine", "Dizel motor", f"{self.engine['manufacturer']} {self.engine['model']}" if self.engine else "—", self.sources.get("engine", "yok"))
        row("gearbox", "Şanzıman", f"{self.gearbox['manufacturer']} {self.gearbox['model']} ({len(self.gearbox['gears'])} vites)" if self.gearbox else "—", self.sources.get("gearbox", "yok"))
        row("axlegear", "Aks", f"{self.axlegear['manufacturer']} {self.axlegear['model']} i={self.axlegear['ratio']:.3f}" if self.axlegear else "—", self.sources.get("axlegear", "yok"))
        row("retarder", "Retarder", (f"{v.get('retarder_type', 'none')} x{v.get('retarder_ratio', 1):g}") if self.retarder else "yok", self.sources.get("retarder", "yok"))
        row("tyres", "Lastikler", " · ".join(f"{t['dimension']} RRC {t['rrc']:.4f} (%{100 * t['share']:.1f})" for t in self.tyres) or "—", self.sources.get("tyres", "yok"))
        row("cdxa", "Hava direnci CdxA", f"{self.cdxa_m2:.2f} m²" if self.cdxa_m2 else "—", self.sources.get("cdxa", "yok"))
        row("mass", "Kütle (boş / azami)", f"{v.get('kerb_mass_kg', 0):.0f} / {v.get('gross_mass_kg', 0):.0f} kg", self.sources.get("mass", "yok"))
        row("seats", "Koltuk / ayakta", f"{v.get('seats', 0)} / {v.get('standees', 0)}", self.sources.get("seats", "yok"))
        row("dims", "Boyutlar", f"{v.get('length_m', 0):.2f} × {v.get('width_m', 0):.2f} × {v.get('height_m', 0):.2f} m" if v.get("length_m") else "—", self.sources.get("dims", "yok"))
        row("hvac", "Klima donanımı", f"yardımcı ısıtıcı {v.get('aux_heater_w', 0) / 1e3:.0f} kW, {'çift' if v.get('double_glazing') else 'tek'} cam", self.sources.get("hvac", "yok"))
        return {"rows": rows, "warnings": list(self.warnings), "missing": list(self.missing),
                "model": v.get("model", ""), "manufacturer": v.get("manufacturer", "")}


def plan_twin(bundle: VectoBundle) -> TwinPlan:
    p = TwinPlan()
    primary = bundle.first("vehicle_primary")
    completed = bundle.first("vehicle_completed")
    pcomp = (primary.data.get("components") if primary else None) or {}

    def pick(kind: str, label: str):
        f = bundle.first(kind)
        if f is not None:
            p.sources[kind] = f"{f.filename} (sertifikalı bileşen dosyası)"
            if len(bundle.of(kind)) > 1:
                p.warnings.append(f"Birden fazla {label} dosyası var; {f.filename} kullanıldı.")
            return f.data
        if kind in pcomp:
            p.sources[kind] = f"{primary.filename} içindeki kopya (örnek mimari dosyası)"
            p.warnings.append(f"{label} yalnızca birincil araç dosyasının içinden alındı; o dosya "
                              f"örnek olarak paylaşıldıysa gerçek bileşen dosyasını da yükleyin.")
            return pcomp[kind]
        p.missing.append(label)
        return None

    p.engine = pick("engine", "Dizel motor")
    p.gearbox = pick("gearbox", "Şanzıman")
    p.axlegear = pick("axlegear", "Aks")
    p.retarder = pick("retarder", "Retarder") if (bundle.first("retarder") or "retarder" in pcomp) else None

    # --- vehicle level ---------------------------------------------------------
    v: dict = {}
    if completed:
        c = completed.data
        v.update(manufacturer=c.get("manufacturer", ""), model=c.get("model", ""),
                 kerb_mass_kg=c.get("corrected_actual_mass_kg") or 0.0, gross_mass_kg=c.get("tpmlm_kg") or 0.0,
                 seats=c.get("seats", 0), standees=c.get("standees", 0),
                 length_m=c.get("length_m", 0.0), width_m=c.get("width_m", 0.0), height_m=c.get("height_m", 0.0),
                 aux_heater_w=c.get("aux_heater_w", 0.0), double_glazing=c.get("double_glazing", False),
                 hvac=c.get("hvac", {}), class_bus=c.get("class_bus", ""), bodywork_code=c.get("bodywork_code", ""),
                 door_drive=c.get("door_drive", ""))
        for k in ("mass", "seats", "dims", "hvac"):
            p.sources[k] = f"{completed.filename} (tamamlanmış araç dosyası)"
    if primary:
        pr = primary.data
        v.setdefault("manufacturer", pr.get("manufacturer", ""))
        v.setdefault("model", pr.get("model", ""))
        if not v.get("gross_mass_kg"):
            v["gross_mass_kg"] = pr.get("tpmlm_kg") or 0.0
            p.sources["mass"] = f"{primary.filename} (yalnızca azami kütle)"
        v["axle_configuration"] = pr.get("axle_configuration", "")
        v["articulated"] = bool(pr.get("articulated"))
        v["retarder_type"] = RETARDER_TYPES.get((pr.get("retarder_type") or "none").lower(), "transmission_output")
        v["retarder_ratio"] = pr.get("retarder_ratio") or 1.0
        v["adas"] = pr.get("adas", {})
        v["auxiliaries"] = pr.get("auxiliaries", {})
        v["idle_rpm"] = pr.get("idle_rpm")
    if p.retarder is not None and "retarder_type" not in v:
        v["retarder_type"], v["retarder_ratio"] = "transmission_output", 1.0
        p.warnings.append("Retarder tipi ve oranı araç dosyasında yok; şanzıman çıkışında, oran 1 kabul edildi.")
    if not v.get("kerb_mass_kg"):
        p.missing.append("Boş kütle (tamamlanmış araç dosyası)")
    if not v.get("seats"):
        p.missing.append("Koltuk sayısı (tamamlanmış araç dosyası)")

    # --- air drag ---------------------------------------------------------------
    if completed and completed.data.get("cdxa_m2"):
        p.cdxa_m2 = completed.data["cdxa_m2"]
        p.sources["cdxa"] = f"{completed.filename} (tamamlanmış araç dosyasındaki hava direnci beyanı)"
    elif bundle.first("airdrag"):
        p.cdxa_m2 = bundle.first("airdrag").data["cdxa_m2"]
        p.sources["cdxa"] = f"{bundle.first('airdrag').filename}"
    elif "airdrag" in pcomp:
        p.cdxa_m2 = pcomp["airdrag"]["cdxa_m2"]
        p.sources["cdxa"] = f"{primary.filename} içindeki hava direnci"
    else:
        p.cdxa_m2 = GENERIC_COACH_CDXA
        p.sources["cdxa"] = "VECTO jenerik şehirlerarası otobüs değeri (3,45 m²)"
        p.warnings.append("Hava direnci beyanı yok: VECTO'nun jenerik CdxA değeri 3,45 m² kullanıldı.")

    # --- tyres and axles ------------------------------------------------------------
    axles = primary.data.get("axles", []) if primary else []
    n_axles = len(axles) or 2
    shares = AXLE_SHARES.get((n_axles, bool(v.get("articulated"))), AXLE_SHARES[(2, False)])
    if len(shares) != n_axles:
        shares = [1.0 / n_axles] * n_axles
    standalone = bundle.of("tyre")
    tyres = []
    for k in range(n_axles):
        ax = axles[k] if k < len(axles) else {"axle_type": "VehicleDriven" if k == n_axles - 1 else "VehicleNonDriven",
                                              "twin_tyres": k == n_axles - 1, "tyre": None}
        if standalone:
            t = standalone[min(k, len(standalone) - 1)].data
            src = "sertifikalı lastik dosyası"
        elif ax.get("tyre"):
            t = ax["tyre"]
            src = "birincil araç dosyasındaki lastik"
        else:
            t = {"dimension": "295/80 R22.5", "rrc": 0.0055, "fz_iso": 31274.0, "manufacturer": "", "model": ""}
            src = "varsayılan (295/80 R22.5, RRC 0,0055)"
            p.warnings.append(f"{k + 1}. aks için lastik verisi yok; varsayılan kullanıldı.")
        tyres.append({"number": k + 1, "share": shares[k], "rrc": t["rrc"], "fz_iso": t["fz_iso"],
                      "dimension": t.get("dimension", ""), "twin_tyres": bool(ax.get("twin_tyres")),
                      "driven": "Driven" in str(ax.get("axle_type", "")) and "Non" not in str(ax.get("axle_type", "")),
                      "label": " ".join(x for x in (t.get("manufacturer"), t.get("model"), t.get("dimension")) if x),
                      "source": src})
    p.tyres = tyres
    p.sources["tyres"] = tyres[0]["source"] + (f"; aks yük payları VECTO segment tablosundan ({'/'.join(f'{100 * s:.1f}' for s in shares)} %)")
    if not axles:
        p.warnings.append("Birincil araç dosyası yok: 4x2 (ön tek, arka çift lastik) kabul edildi.")
    p.vehicle = v
    return p


# ---------------------------------------------------------------- writing
def _wheel(dimension: str, warnings: list) -> tuple:
    d = WHEELS.get(dimension.strip())
    if d is None:
        warnings.append(f"Lastik ebadı {dimension!r} VECTO tekerlek tablosunda yok; 295/80 R22.5 değerleri kullanıldı.")
        d = WHEELS["295/80 R22.5"]
    diam, inertia, f = d
    return diam / 1000.0 * f / (2 * math.pi), inertia


def _fuel_grid(entries: list) -> tuple:
    """Rectangular (rpm, Nm, g/h) grid from VECTO's per-speed torque lists.

    VECTO measures the map on a different set of torques at each speed and
    interpolates on a triangulation. Our lookup is a grid, so each speed's
    column is resampled onto a common torque axis by linear interpolation,
    extended flat at the ends (the solver never asks above full load).
    """
    arr = np.asarray(entries, dtype=float)
    speeds = np.unique(arr[:, 0])
    t_lo, t_hi = float(arr[:, 1].min()), float(arr[:, 1].max())
    torques = np.unique(np.round(np.r_[np.linspace(t_lo, t_hi, 48), 0.0], 2))
    z = np.zeros((torques.size, speeds.size))
    for i, sp in enumerate(speeds):
        rows = arr[arr[:, 0] == sp]
        order = np.argsort(rows[:, 1])
        t, g = rows[order, 1], rows[order, 2]
        t, idx = np.unique(t, return_index=True)
        z[:, i] = np.interp(torques, t, g[idx])
    return speeds, torques, z


def _write_grid_csv(path, x, y, z, corner: str, fmt="%.3f") -> None:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        fh.write(corner + "," + ",".join(f"{v:g}" for v in x) + "\n")
        for j, yv in enumerate(y):
            fh.write(f"{yv:g}," + ",".join(fmt % v for v in z[j]) + "\n")


def _yaml(path, data: dict, header: str) -> None:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


HEADER = ("# VECTO beyan dosyalarından temsasim içe aktarıcısıyla üretildi. Elle düzenlenebilir;\n"
          "# birim ekleri: _rpm = rpm, _kw = kW, diğer her şey SI.\n")


def write_twin(lib: Library, plan: TwinPlan, *, name: str, stem: str | None = None,
               variant: str = "", overwrite: bool = False, glazing_area_m2: float | None = None,
               notes: str = "", hvac_ref: str | None = None, aux_ref: str | None = None) -> dict:
    """Write engine, transmission, chassis, (hvac, auxiliaries) and the vehicle.

    Returns the relative paths written and the warnings accumulated. The
    vehicle is loaded back through ``Library.load_vehicle`` before returning.
    """
    if plan.missing:
        raise ValueError("Eksik veri: " + "; ".join(plan.missing))
    stem = safe_name(stem or name)
    root = lib.root
    written: dict = {}
    warnings = list(plan.warnings)
    v = plan.vehicle

    def check(path: pathlib.Path):
        if path.exists() and not overwrite:
            raise FileExistsError(f"{path.relative_to(root).as_posix()} zaten var. Üzerine yazmak için onay verin.")

    # --- engine -----------------------------------------------------------------
    e = plan.engine
    e_stem = safe_name(f"{e['manufacturer'].split()[0] if e['manufacturer'] else 'motor'}_{e['model']}")
    e_path = root / "engine" / f"{e_stem}.yaml"
    check(e_path)
    speeds, torques, z = _fuel_grid(e["fuel_map"])
    fuel_csv = root / "engine" / "maps" / f"{e_stem}_yakit.csv"
    _write_grid_csv(fuel_csv, speeds, torques, z, "torque_nm / speed_rpm", "%.1f")
    fl = np.asarray(e["full_load"], dtype=float)
    fl_csv = root / "engine" / "maps" / f"{e_stem}_tam_yuk.csv"
    with fl_csv.open("w", newline="", encoding="utf-8") as fh:
        fh.write("speed_rpm,max_torque_nm,drag_torque_nm\n")
        for r in fl:
            fh.write(f"{r[0]:g},{r[1]:.2f},{r[2]:.2f}\n")
    gbx_type = (plan.gearbox or {}).get("transmission_type", "AMT")
    inertia = (1.2 if gbx_type in ("AT", "ATSerial", "ATPowerSplit", "APTS", "APTN") else 1.3) + 0.41 + 270.0 * e["displacement_cm3"] * 1e-6
    _yaml(e_path, {
        "kind": "engine", "name": f"{e['manufacturer']} {e['model']}".strip(),
        "description": (f"VECTO motor sertifikasından içe aktarıldı ({plan.sources.get('engine', '')}). "
                        f"Sertifika {e.get('certification_number', '')}, tarih {e.get('date', '')[:10]}. "
                        f"Yakıt haritası {len(e['fuel_map'])} ölçüm noktasından {speeds.size}×{torques.size} ızgaraya yeniden örneklendi."),
        "manufacturer": e["manufacturer"], "model": e["model"], "certification_number": e.get("certification_number", ""),
        "fuel_type": "diesel" if "Diesel" in e.get("fuel_type", "Diesel") else e.get("fuel_type", "diesel"),
        "displacement_cm3": e["displacement_cm3"], "idle_speed_rpm": e["idle_rpm"], "rated_speed_rpm": e["rated_rpm"],
        "rated_power_kw": e["rated_power_w"] / 1e3, "max_torque_nm": e["max_torque_nm"],
        "fuel_map": {"path": f"maps/{fuel_csv.name}", "x_unit": "rpm", "source": f"VECTO sertifikası {e.get('certification_number', '')}"},
        "full_load_curve": {"path": f"maps/{fl_csv.name}", "columns": ["speed_rpm", "max_torque_nm"], "x_unit": "rpm"},
        "drag_curve": {"path": f"maps/{fl_csv.name}", "columns": ["speed_rpm", "drag_torque_nm"], "x_unit": "rpm"},
        "inertia_kgm2": round(inertia, 3),
        "whtc_urban": e["whtc_urban"], "whtc_rural": e["whtc_rural"], "whtc_motorway": e["whtc_motorway"],
        "cold_hot_factor": e["cold_hot_factor"], "regper_factor": e["regper_factor"],
        "validated": True,
        "notes": "Ölçülmüş sertifika verisi. Atalet VECTO'nun hacme dayalı jenerik formülüyle hesaplandı.",
    }, HEADER)
    written["engine"] = f"engine/{e_path.name}"

    # --- transmission (gearbox + axle + retarder) ---------------------------------------
    g = plan.gearbox
    a = plan.axlegear
    t_stem = safe_name(f"{(g['manufacturer'].split()[0] if g['manufacturer'] else 'sanziman')}_{g['model']}_{a['model']}")
    t_path = root / "transmission" / f"{t_stem}.yaml"
    check(t_path)
    gl = {gear["number"]: gear["loss_map"] for gear in g["gears"] if gear["loss_map"]}
    tdata: dict = {
        "kind": "transmission", "name": f"{g['manufacturer']} {g['model']} + aks {a['model']}".strip(),
        "description": (f"VECTO şanzıman ve aks sertifikalarından içe aktarıldı ({plan.sources.get('gearbox', '')}; "
                        f"{plan.sources.get('axlegear', '')}). Şanzıman sertifika yöntemi: {g.get('certification_method', '')}; "
                        f"aks: {a.get('certification_method', '')}."),
        "transmission_type": g.get("transmission_type", "AMT"), "manufacturer": g["manufacturer"], "model": g["model"],
        "certification_number": g.get("certification_number", ""),
        "gear_ratios": [round(x["ratio"], 4) for x in g["gears"]],
        "gear_max_torque_nm": [x["max_torque_nm"] for x in g["gears"]] if all(x.get("max_torque_nm") for x in g["gears"]) else None,
        "axle_ratio": a["ratio"],
    }
    if gl:
        gcsv = root / "transmission" / "maps" / f"{t_stem}_sanziman_kayip.csv"
        LossMap.from_entries(gl, speed_unit="rpm", name="gearbox").to_csv(gcsv)
        tdata["gearbox_loss_map"] = {"path": f"maps/{gcsv.name}", "source": f"VECTO sertifikası {g.get('certification_number', '')}"}
    else:
        warnings.append("Şanzıman kayıp haritası yok; sabit verim kullanılacak.")
    if a.get("loss_map"):
        acsv = root / "transmission" / "maps" / f"{t_stem}_aks_kayip.csv"
        LossMap.from_entries({0: a["loss_map"]}, speed_unit="rpm", name="axle").to_csv(acsv)
        tdata["axle_loss_map"] = {"path": f"maps/{acsv.name}", "source": f"VECTO sertifikası {a.get('certification_number', '')}"}
    else:
        warnings.append("Aks kayıp haritası yok; sabit verim kullanılacak.")
    if plan.retarder is not None and plan.retarder.get("loss_map"):
        tdata["retarder_type"] = v.get("retarder_type", "transmission_output")
        tdata["retarder_ratio"] = v.get("retarder_ratio", 1.0)
        tdata["retarder_loss_curve"] = {"points": [[round(x, 2), round(y, 3)] for x, y in plan.retarder["loss_map"]],
                                        "x_unit": "rpm", "source": f"VECTO retarder sertifikası ({plan.sources.get('retarder', '')})"}
    else:
        tdata["retarder_type"] = "none"
    # shift window from the engine's own numbers
    tdata["downshift_speed_rpm"] = round(max(e["idle_rpm"] * 1.45, 0.5 * e["rated_rpm"]))
    tdata["upshift_speed_rpm"] = round(0.95 * e["rated_rpm"])
    tdata["torque_reserve"] = 0.10
    tdata["validated"] = bool(gl) and bool(a.get("loss_map"))
    tdata["notes"] = ("Kayıp haritaları sertifikadan. Vites stratejisi penceresi motor anma devrinden türetildi; "
                      "ölçülmüş devir izine göre ayarlayın.")
    _yaml(t_path, {k: val for k, val in tdata.items() if val is not None}, HEADER)
    written["transmission"] = f"transmission/{t_path.name}"

    # --- chassis ------------------------------------------------------------------------------
    c_stem = safe_name(f"{v.get('model') or name}_vecto")
    c_path = root / "chassis" / f"{c_stem}.yaml"
    check(c_path)
    rdyn, inertia_wheel = _wheel(plan.tyres[-1]["dimension"], warnings)
    axles_yaml = []
    for t in plan.tyres:
        _, iw = _wheel(t["dimension"], [])
        axles_yaml.append({"number": t["number"], "share": round(t["share"], 4), "rrc_iso": t["rrc"], "fz_iso": t["fz_iso"],
                           "twin_tyres": t["twin_tyres"], "driven": t["driven"], "wheel_inertia_kgm2": iw,
                           "tyre": t["label"], "source": t["source"]})
    width, height = v.get("width_m") or 2.55, v.get("height_m") or 3.6
    frontal = round(width * height, 3)
    _yaml(c_path, {
        "kind": "chassis", "name": f"{v.get('model') or name} (VECTO)",
        "description": (f"VECTO araç dosyalarından içe aktarıldı. Kütle: {plan.sources.get('mass', '')}. "
                        f"CdxA: {plan.sources.get('cdxa', '')}. Lastikler: {plan.sources.get('tyres', '')}. "
                        f"Dinamik lastik yarıçapı ve tekerlek ataleti VECTO tekerlek tablosundan."),
        "kerb_mass_kg": v["kerb_mass_kg"], "gross_mass_kg": v["gross_mass_kg"], "passenger_mass_kg": 68.0,
        "cda_m2": plan.cdxa_m2, "frontal_area_m2": frontal, "drag_coefficient": round(plan.cdxa_m2 / frontal, 4),
        "tyre_dynamic_radius_m": round(rdyn, 4),
        "axles": axles_yaml,
        "validated": False,
        "notes": ("Bileşen sertifikaları ölçülmüş; aks yük payları VECTO segment tablosundan varsayım. "
                  "Araç seviyesi dosyalar örnek olarak paylaşıldıysa kütle ve CdxA'yı gerçek araçla doğrulayın."),
    }, HEADER)
    written["chassis"] = f"chassis/{c_path.name}"

    # --- hvac and auxiliaries ---------------------------------------------------------------------
    L, W, Hh = v.get("length_m") or 13.0, width, height
    if hvac_ref is None:
        h_stem = safe_name(f"{v.get('model') or name}_kabin")
        h_path = root / "hvac" / f"{h_stem}.yaml"
        check(h_path)
        glazing = glazing_area_m2 if glazing_area_m2 else round(0.30 * 2 * L * (Hh - 0.9), 1)
        shell = 2 * L * Hh + L * W + 2 * W * Hh
        _yaml(h_path, {
            "kind": "hvac", "name": f"{v.get('model') or name} kabin",
            "description": (f"Tamamlanmış araç dosyasındaki donanımdan ({plan.sources.get('hvac', '')}): "
                            f"yardımcı ısıtıcı {v.get('aux_heater_w', 0) / 1e3:.0f} kW, "
                            f"{'çift' if v.get('double_glazing') else 'tek'} cam. Kabin geometrisi araç boyutlarından; "
                            + ("cam alanı CFD/CAD ölçümünden." if glazing_area_m2 else "cam alanı yan yüzeyin %30'u KABULÜ.")),
            "system_type": "fuel_fired", "backup_heat_source": "fuel_fired",
            "cabin_volume_m3": round((L - 1.5) * (W - 0.15) * 2.0, 1),
            "glazing_area_m2": glazing, "opaque_area_m2": round(shell - glazing, 1),
            "u_glazing_w_per_m2k": 2.8 if v.get("double_glazing") else 5.4, "u_opaque_w_per_m2k": 1.1,
            "fresh_air_m3_per_h_per_passenger": 10.0, "fresh_air_minimum_m3_per_h": 300.0,
            "door_count": 2, "door_width_m": 0.9, "door_height_m": 1.95, "door_discharge_coefficient": 0.5,
            "heating_capacity_kw": max((v.get("aux_heater_w") or 0) / 1e3, 20.0), "cooling_capacity_kw": 32.0,
            "fuel_fired_efficiency": 0.85, "fuel_fired_parasitic_w": 300.0, "blower_power_w": 1200.0,
            "condenser_fan_power_w": 900.0, "setpoint_heating_c": 20.0, "setpoint_cooling_c": 24.0,
            "validated": False,
            "notes": "Soğutma kapasitesi ve COP tedarikçi verisiyle değiştirilmeli; ısıtma motor soğutma suyundan, yetmezse yakıtlı ısıtıcıdan.",
        }, HEADER)
        hvac_ref = f"hvac/{h_path.name}"
        written["hvac"] = hvac_ref
    if aux_ref is None:
        x_stem = safe_name(f"{v.get('model') or name}_yardimci")
        x_path = root / "auxiliary" / f"{x_stem}.yaml"
        check(x_path)
        auxd = v.get("auxiliaries", {}) or {}
        _yaml(x_path, {
            "kind": "auxiliary", "name": f"{v.get('model') or name} yardımcı tüketiciler",
            "description": (f"Birincil araç dosyasındaki teknoloji seçimlerinden: alternatör {auxd.get('alternator', '?')}, "
                            f"direksiyon {auxd.get('steering_pump', '?')}, hava {auxd.get('air_supply', '?')} ({auxd.get('compressor_drive', '?')}), "
                            f"fan {auxd.get('fan', '?')}. Güç değerleri VECTO'nun otobüs yardımcı tablolarına yakın KABUL değerleri."),
            "constant_w": 1000.0, "lighting_w": 400.0, "lighting_night_w": 900.0,
            "steering_pump_w": 1500.0, "steering_speed_factor": 0.6,
            "air_compressor_w": 4000.0, "air_compressor_duty_base": 0.08, "air_per_stop_j": 60000.0,
            "dcdc_efficiency": 1.0,
            "validated": False,
            "notes": "24 V yükleri alternatör verimi 0,70 ile mekanik güce çevrilir (dizel çözücü). Ölçümle değiştirin.",
        }, HEADER)
        aux_ref = f"auxiliary/{x_path.name}"
        written["auxiliaries"] = aux_ref

    # --- vehicle --------------------------------------------------------------------------------------
    v_path = root / "vehicle" / f"{stem}.yaml"
    check(v_path)
    _yaml(v_path, {
        "kind": "vehicle", "name": name, "variant": variant or f"{v.get('model', '')} dizel".strip(),
        "powertrain": "diesel",
        "description": ((notes + " ") if notes else "") + (
            f"VECTO beyan dosyalarından kurulan dijital ikiz. Üretici: {v.get('manufacturer', '')}. "
            f"Kaynaklar: motor {plan.sources.get('engine', '')}; şanzıman {plan.sources.get('gearbox', '')}; "
            f"aks {plan.sources.get('axlegear', '')}; kütle {plan.sources.get('mass', '')}."),
        "chassis": written["chassis"], "transmission": written["transmission"], "engine": written["engine"],
        "hvac": hvac_ref, "auxiliaries": aux_ref,
        "seats": int(v.get("seats", 0)), "standees": int(v.get("standees", 0)),
        "fuel_tank_l": 400.0,
    }, "# VECTO beyan dosyalarından temsasim içe aktarıcısıyla üretildi.\n")
    written["vehicle"] = f"vehicle/{v_path.name}"

    veh = lib.load_vehicle(stem)        # prove it loads; raises otherwise
    return {"stem": stem, "written": written, "warnings": warnings, "describe": veh.describe()}
