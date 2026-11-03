"""Reader for VECTO declaration XML files, the form TEMSA's vehicle data arrives in.

Homologation keeps every certified component as a signed VECTO XML file:
engine, gearbox, axle, tyre, retarder, air drag, plus the primary-vehicle and
completed-bus (step input) files that assemble them. Those files are the
most reliable description of a vehicle the company has, so the simulator
reads them directly instead of asking anyone to retype numbers.

The reader is deliberately tolerant about schema versions: it strips XML
namespaces and looks for element names, so a ``DeclarationComponent:v1.0``
engine and a ``v2.0`` gearbox both read. It returns plain dicts and lists,
nothing from this module depends on the rest of the package, and nothing is
written anywhere: ``vecto_import`` does the writing after the engineer has
seen the preview.
"""

from __future__ import annotations

import json
import pathlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

KINDS_TR = {
    "engine": "Dizel motor", "gearbox": "Şanzıman", "axlegear": "Aks (diferansiyel)",
    "tyre": "Lastik", "retarder": "Retarder", "airdrag": "Hava direnci (CdxA)",
    "vehicle_primary": "Birincil araç (şasi + güç aktarma)",
    "vehicle_completed": "Tamamlanmış araç (karoser)", "vehicle": "Araç",
    "job": "VECTO iş dosyası", "angledrive": "Açılı tahrik", "torqueconverter": "Tork konvertörü",
    "unknown": "Tanınmadı",
}


@dataclass
class VectoFile:
    """One parsed file: what it is, where it came from, and its data."""

    filename: str
    kind: str
    data: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    @property
    def label(self) -> str:
        d = self.data
        who = " ".join(x for x in (d.get("manufacturer"), d.get("model")) if x)
        return who or KINDS_TR.get(self.kind, self.kind)

    def summary(self) -> dict:
        """Compact, JSON-safe description for the upload preview."""
        d = self.data
        out = {"filename": self.filename, "kind": self.kind, "kind_tr": KINDS_TR.get(self.kind, self.kind),
               "label": self.label, "manufacturer": d.get("manufacturer"), "model": d.get("model"),
               "certification": d.get("certification_number"), "date": d.get("date"),
               "warnings": list(self.warnings), "facts": self._facts()}
        return out

    def _facts(self) -> list:
        d, k = self.data, self.kind
        f = []
        if k == "engine":
            f += [("Hacim", f"{d['displacement_cm3'] / 1000:.1f} l"),
                  ("Anma gücü", f"{d['rated_power_w'] / 1e3:.0f} kW @ {d['rated_rpm']:.0f} rpm"),
                  ("Azami tork", f"{d['max_torque_nm']:.0f} Nm"), ("Rölanti", f"{d['idle_rpm']:.0f} rpm"),
                  ("Yakıt haritası", f"{len(d['fuel_map'])} nokta"),
                  ("Tam yük eğrisi", f"{len(d['full_load'])} nokta"),
                  ("WHTC kent/kırsal/otoyol", f"{d['whtc_urban']:.4f} / {d['whtc_rural']:.4f} / {d['whtc_motorway']:.4f}")]
        elif k == "gearbox":
            g = d["gears"]
            f += [("Tip", d.get("transmission_type", "")), ("Vites sayısı", str(len(g))),
                  ("Oranlar", " · ".join(f"{x['ratio']:.3f}" for x in g)),
                  ("Kayıp haritası", f"vites başına {len(g[0]['loss_map']) if g else 0} nokta"),
                  ("Sertifika yöntemi", d.get("certification_method", ""))]
        elif k == "axlegear":
            f += [("Oran", f"{d['ratio']:.3f}"), ("Tip", d.get("line_type", "")),
                  ("Kayıp haritası", f"{len(d['loss_map'])} nokta"),
                  ("Sertifika yöntemi", d.get("certification_method", ""))]
        elif k == "tyre":
            f += [("Ebat", d.get("dimension", "")), ("RRC", f"{d['rrc']:.4f}"), ("FzISO", f"{d['fz_iso']:.0f} N")]
        elif k == "retarder":
            f += [("Kayıp haritası", f"{len(d['loss_map'])} nokta"),
                  ("Sertifika yöntemi", d.get("certification_method", ""))]
        elif k == "airdrag":
            f += [("CdxA", f"{d['cdxa_m2']:.2f} m²")]
        elif k == "vehicle_primary":
            f += [("Model", d.get("model", "")), ("Aks düzeni", d.get("axle_configuration", "")),
                  ("Teknik azami kütle", f"{d.get('tpmlm_kg', 0):.0f} kg"),
                  ("Retarder", f"{d.get('retarder_type', '')} x{d.get('retarder_ratio', 1):g}"),
                  ("Bileşenler", ", ".join(KINDS_TR[c] for c in d.get("components", {}))),
                  ("Akslar", " · ".join(f"{a['axle_type']}{' çift' if a['twin_tyres'] else ''}"
                                       + (f" {a['tyre']['dimension']} RRC {a['tyre']['rrc']:.4f}" if a.get("tyre") else "")
                                       for a in d.get("axles", [])))]
        elif k == "vehicle_completed":
            f += [("Model", d.get("model", "")), ("Düzeltilmiş gerçek kütle", f"{d.get('corrected_actual_mass_kg', 0):.0f} kg"),
                  ("Teknik azami kütle", f"{d.get('tpmlm_kg', 0):.0f} kg"),
                  ("Koltuk / ayakta", f"{d.get('seats', 0)} / {d.get('standees', 0)}"),
                  ("Boyutlar", f"{d.get('length_m', 0):.2f} × {d.get('width_m', 0):.2f} × {d.get('height_m', 0):.2f} m"),
                  ("CdxA", f"{d['cdxa_m2']:.2f} m²" if d.get("cdxa_m2") else "—"),
                  ("Yardımcı ısıtıcı", f"{d.get('aux_heater_w', 0) / 1e3:.0f} kW"),
                  ("Çift cam", "evet" if d.get("double_glazing") else "hayır")]
        elif k == "job":
            f += [("Birincil araç", d.get("primary_vehicle", "")), ("Ara adım", d.get("interim_step", ""))]
        return [[a, b] for a, b in f]


# ---------------------------------------------------------------- helpers
def _strip(tag: str) -> str:
    return tag.split("}")[-1]


def _child(e, name: str):
    for c in e:
        if _strip(c.tag) == name:
            return c
    return None


def _find(e, name: str):
    """First descendant with this local name."""
    for c in e.iter():
        if _strip(c.tag) == name:
            return c
    return None


def _text(e, name: str, default=None):
    c = _find(e, name) if e is not None else None
    if c is None or c.text is None:
        return default
    return c.text.strip()


def _num(e, name: str, default=None):
    t = _text(e, name)
    if t is None or t == "":
        return default
    try:
        return float(t)
    except ValueError:
        return default


def _bool(e, name: str, default=False):
    t = _text(e, name)
    return default if t is None else t.strip().lower() in ("true", "1", "yes")


def _entries(e, *attrs):
    """Numeric attributes of every <Entry> under e, as a list of lists."""
    out = []
    for c in e.iter():
        if _strip(c.tag) == "Entry":
            try:
                out.append([float(c.get(a)) for a in attrs])
            except (TypeError, ValueError):
                continue
    return out


def _meta(data_el) -> dict:
    return {"manufacturer": _text(data_el, "Manufacturer", ""),
            "model": _text(data_el, "Model", ""),
            "certification_number": _text(data_el, "CertificationNumber", ""),
            "date": _text(data_el, "Date", ""),
            "app_version": _text(data_el, "AppVersion", ""),
            "id": data_el.get("id", "")}


def _xsi_type(e) -> str:
    for k, v in e.attrib.items():
        if _strip(k) == "type":
            return v
    return ""


# ---------------------------------------------------------------- components
def parse_engine(el) -> dict:
    data = _child(el, "Data")
    data = el if data is None else data
    d = _meta(data)
    d.update({
        "displacement_cm3": _num(data, "Displacement", 0.0),
        "idle_rpm": _num(data, "IdlingSpeed", 0.0),
        "rated_rpm": _num(data, "RatedSpeed", 0.0),
        "rated_power_w": _num(data, "RatedPower", 0.0),
        "max_torque_nm": _num(data, "MaxEngineTorque", 0.0),
        "whtc_urban": _num(data, "WHTCUrban", 1.0),
        "whtc_rural": _num(data, "WHTCRural", 1.0),
        "whtc_motorway": _num(data, "WHTCMotorway", 1.0),
        "cold_hot_factor": _num(data, "BFColdHot", 1.0),
        "regper_factor": _num(data, "CFRegPer", 1.0),
        "ncv_factor": _num(data, "CFNCV", 1.0),
        "fuel_type": _text(data, "FuelType", "Diesel CI"),
    })
    fc = _find(data, "FuelConsumptionMap")
    fl = _find(data, "FullLoadAndDragCurve")
    d["fuel_map"] = _entries(fc, "engineSpeed", "torque", "fuelConsumption") if fc is not None else []
    d["full_load"] = _entries(fl, "engineSpeed", "maxTorque", "dragTorque") if fl is not None else []
    return d


def parse_gearbox(el) -> dict:
    data = _child(el, "Data")
    data = el if data is None else data
    d = _meta(data)
    d["transmission_type"] = _text(data, "TransmissionType", "AMT")
    d["certification_method"] = _text(data, "MainCertificationMethod", "") or _text(data, "CertificationMethod", "")
    gears = []
    gl = _find(data, "Gears")
    if gl is not None:
        for g in gl:
            if _strip(g.tag) != "Gear":
                continue
            lm = _find(g, "TorqueLossMap")
            gears.append({
                "number": int(g.get("number", len(gears) + 1)),
                "ratio": _num(g, "Ratio", 1.0),
                "max_torque_nm": _num(g, "MaxTorque", None),
                "max_speed_rpm": _num(g, "MaxSpeed", None),
                "loss_map": _entries(lm, "inputSpeed", "inputTorque", "torqueLoss") if lm is not None else [],
            })
    gears.sort(key=lambda x: x["number"])
    d["gears"] = gears
    return d


def parse_axlegear(el) -> dict:
    data = _child(el, "Data")
    data = el if data is None else data
    d = _meta(data)
    d["ratio"] = _num(data, "Ratio", 1.0)
    d["line_type"] = _text(data, "LineType", "")
    d["certification_method"] = _text(data, "CertificationMethod", "")
    lm = _find(data, "TorqueLossMap")
    d["loss_map"] = _entries(lm, "inputSpeed", "inputTorque", "torqueLoss") if lm is not None else []
    return d


def parse_tyre(el) -> dict:
    data = _child(el, "Data")
    data = el if data is None else data
    d = _meta(data)
    d["dimension"] = _text(data, "Dimension", "")
    d["rrc"] = _num(data, "RRCDeclared", 0.0)
    d["fz_iso"] = _num(data, "FzISO", 0.0)
    return d


def parse_retarder(el) -> dict:
    data = _child(el, "Data")
    data = el if data is None else data
    d = _meta(data)
    d["certification_method"] = _text(data, "CertificationMethod", "")
    lm = _find(data, "RetarderLossMap")
    d["loss_map"] = _entries(lm, "retarderSpeed", "torqueLoss") if lm is not None else []
    return d


def parse_airdrag(el) -> dict:
    data = _child(el, "Data")
    data = el if data is None else data
    d = _meta(data)
    d["cdxa_m2"] = _num(data, "DeclaredCdxA", None) or _num(data, "TransferredCdxA", None) or _num(data, "CdxA_0", 0.0)
    return d


def parse_vehicle_primary(el) -> dict:
    d = {"manufacturer": _text(el, "Manufacturer", ""), "model": _text(el, "Model", ""),
         "vin": _text(el, "VIN", ""), "date": _text(el, "Date", ""),
         "legislative_category": _text(el, "LegislativeCategory", ""),
         "axle_configuration": _text(el, "AxleConfiguration", ""),
         "articulated": _bool(el, "Articulated"),
         "tpmlm_kg": _num(el, "TechnicalPermissibleMaximumLadenMass", 0.0),
         "idle_rpm": _num(el, "IdlingSpeed", None),
         "retarder_type": _text(el, "RetarderType", "None"),
         "retarder_ratio": _num(el, "RetarderRatio", 1.0),
         "angledrive_type": _text(el, "AngledriveType", "None"),
         "zero_emission": _bool(el, "ZeroEmissionVehicle")}
    adas = _find(el, "ADAS")
    if adas is not None:
        d["adas"] = {"engine_stop_start": _bool(adas, "EngineStopStart"),
                     "eco_roll": _bool(adas, "EcoRollWithoutEngineStop") or _bool(adas, "EcoRollWithEngineStop"),
                     "predictive_cruise": _text(adas, "PredictiveCruiseControl", "none")}
    comps = {}
    cel = _find(el, "Components")
    if cel is not None:
        for c in cel:
            n = _strip(c.tag)
            if n == "Engine":
                comps["engine"] = parse_engine(c)
            elif n == "Gearbox":
                comps["gearbox"] = parse_gearbox(c)
            elif n == "Axlegear":
                comps["axlegear"] = parse_axlegear(c)
            elif n == "Retarder":
                comps["retarder"] = parse_retarder(c)
            elif n == "AirDrag":
                comps["airdrag"] = parse_airdrag(c)
    d["components"] = comps
    axles = []
    aw = _find(el, "AxleWheels")
    if aw is not None:
        for ax in aw.iter():
            if _strip(ax.tag) != "Axle":
                continue
            tyre_el = _child(ax, "Tyre")
            axles.append({"number": int(ax.get("axleNumber", len(axles) + 1)),
                          "axle_type": _text(ax, "AxleType", ""),
                          "twin_tyres": _bool(ax, "TwinTyres"),
                          "steered": _bool(ax, "Steered"),
                          "tyre": parse_tyre(tyre_el) if tyre_el is not None else None})
    d["axles"] = axles
    aux = _find(el, "Auxiliaries")
    if aux is not None:
        d["auxiliaries"] = {
            "fan": _text(_find(aux, "Fan"), "Technology", ""),
            "steering_pump": _text(_find(aux, "SteeringPump"), "Technology", ""),
            "alternator": _text(aux, "AlternatorTechnology", ""),
            "air_supply": _text(aux, "SizeOfAirSupply", ""),
            "compressor_drive": _text(aux, "CompressorDrive", ""),
            "compressor_ratio": _num(aux, "CompressorRatio", None),
        }
    return d


def parse_vehicle_completed(el) -> dict:
    d = {"manufacturer": _text(el, "Manufacturer", ""), "model": _text(el, "Model", ""),
         "vin": _text(el, "VIN", ""), "date": _text(el, "Date", ""),
         "corrected_actual_mass_kg": _num(el, "CorrectedActualMass", 0.0),
         "tpmlm_kg": _num(el, "TechnicalPermissibleMaximumLadenMass", 0.0),
         "class_bus": _text(el, "ClassBus", ""),
         "seats": int(_num(el, "NumberPassengerSeatsLowerDeck", 0) + _num(el, "NumberPassengerSeatsUpperDeck", 0)),
         "standees": int(_num(el, "NumberPassengersStandingLowerDeck", 0) + _num(el, "NumberPassengersStandingUpperDeck", 0)),
         "bodywork_code": _text(el, "BodyworkCode", ""),
         "low_entry": _bool(el, "LowEntry"),
         "height_m": _num(el, "HeightIntegratedBody", 0.0) / 1000.0,
         "length_m": _num(el, "VehicleLength", 0.0) / 1000.0,
         "width_m": _num(el, "VehicleWidth", 0.0) / 1000.0,
         "entrance_height_m": _num(el, "EntranceHeight", 0.0) / 1000.0,
         "door_drive": _text(el, "DoorDriveTechnology", ""),
         "declaration_type": _text(el, "VehicleDeclarationType", "")}
    ad = _find(el, "AirDrag")
    d["cdxa_m2"] = parse_airdrag(ad)["cdxa_m2"] if ad is not None else None
    hv = _find(el, "HVAC")
    if hv is not None:
        d["hvac"] = {"system_configuration": _text(hv, "SystemConfiguration", ""),
                     "cooling_passenger": _text(_find(hv, "HeatPumpTypePassengerCompartment"), "Cooling", ""),
                     "heating_passenger": _text(_find(hv, "HeatPumpTypePassengerCompartment"), "Heating", ""),
                     "aux_heater_w": _num(hv, "AuxiliaryHeaterPower", 0.0),
                     "double_glazing": _bool(hv, "DoubleGlazing"),
                     "separate_ducts": _bool(hv, "SeparateAirDistributionDucts")}
        d["aux_heater_w"] = d["hvac"]["aux_heater_w"]
        d["double_glazing"] = d["hvac"]["double_glazing"]
    led = _find(el, "LEDLights")
    if led is not None:
        d["led_lights"] = {_strip(c.tag): (c.text or "").strip().lower() == "true" for c in led}
    return d


# ---------------------------------------------------------------- entry point
def read_vecto_file(data: bytes, filename: str) -> VectoFile:
    """Parse one VECTO file (XML component/vehicle, or a .vecto job)."""
    name = pathlib.Path(filename).name
    head = data[:400].lstrip()
    if name.lower().endswith(".vecto") or head.startswith(b"{"):
        try:
            j = json.loads(data.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as exc:
            return VectoFile(name, "unknown", warnings=[f"JSON okunamadı: {exc}"])
        body = j.get("Body", {})
        return VectoFile(name, "job", {"primary_vehicle": body.get("PrimaryVehicle", ""),
                                       "interim_step": body.get("InterimStep", ""),
                                       "completed": body.get("Completed", False),
                                       "header": j.get("Header", {})},
                         warnings=["İş dosyası yalnızca başka dosyalara işaret eder; verileri "
                                   "içermez. Bileşen XML dosyalarını yükleyin."])
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        return VectoFile(name, "unknown", warnings=[f"XML okunamadı: {exc}"])

    first = next((c for c in root if isinstance(c.tag, str)), None)
    if first is None:
        return VectoFile(name, "unknown", warnings=["Boş XML."])
    tag = _strip(first.tag)
    xt = _xsi_type(first)
    if tag == "Engine":
        return VectoFile(name, "engine", parse_engine(first))
    if tag == "Gearbox":
        return VectoFile(name, "gearbox", parse_gearbox(first))
    if tag == "Axlegear":
        return VectoFile(name, "axlegear", parse_axlegear(first))
    if tag == "Tyre":
        return VectoFile(name, "tyre", parse_tyre(first))
    if tag == "Retarder":
        return VectoFile(name, "retarder", parse_retarder(first))
    if tag == "AirDrag":
        return VectoFile(name, "airdrag", parse_airdrag(first))
    if tag == "Angledrive":
        return VectoFile(name, "angledrive", _meta(_child(first, "Data") or first),
                         warnings=["Açılı tahrik henüz modelde yok; atlandı."])
    if tag == "TorqueConverter":
        return VectoFile(name, "torqueconverter", _meta(_child(first, "Data") or first),
                         warnings=["Tork konvertörü henüz modelde yok; atlandı."])
    if tag == "Vehicle":
        if "Completed" in xt or "Interim" in xt or _find(first, "CorrectedActualMass") is not None:
            return VectoFile(name, "vehicle_completed", parse_vehicle_completed(first))
        if "Primary" in xt or _find(first, "AxleWheels") is not None:
            vf = VectoFile(name, "vehicle_primary", parse_vehicle_primary(first))
            if vf.data.get("zero_emission"):
                vf.warnings.append("Sıfır emisyonlu araç dosyası; dizel ikiz için kullanılmaz.")
            return vf
        return VectoFile(name, "vehicle", {"model": _text(first, "Model", "")},
                         warnings=["Araç dosyasının türü tanınmadı."])
    return VectoFile(name, "unknown", warnings=[f"Tanınmayan VECTO öğesi: {tag}"])


def read_vecto_path(path) -> VectoFile:
    p = pathlib.Path(path)
    return read_vecto_file(p.read_bytes(), p.name)


def safe_name(text: str) -> str:
    table = str.maketrans("ıİşŞğĞüÜöÖçÇ", "iISsGgUuOoCc")
    s = re.sub(r"[^a-z0-9]+", "_", text.translate(table).lower()).strip("_")
    return s or "vecto"
