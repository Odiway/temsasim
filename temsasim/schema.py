"""Form schemas generated from the component dataclasses.

The UI never hard-codes a field list. It asks this module, which reads the
dataclasses themselves -- field names, types, defaults, the ``# --- section ---``
comments and the attribute docstrings -- so adding a parameter to a component
in Python makes it appear in the form automatically, with its help text.

Two presentation layers sit on top of the SI dataclasses:

* **display units** -- a field stored as ``peak_power_w`` is shown and entered
  in kW, a speed in km/h, a fraction such as ``soc_min`` in percent. The
  conversion is defined once here and applied in both directions.
* **Turkish labels** and an **essential / advanced** split, so the first thing
  an engineer sees is what a supplier data sheet actually gives them.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import pathlib
import re
import textwrap
from typing import Any

import yaml

from .components.aging import AgingModel
from .components.auxiliary import Auxiliaries
from .components.battery import BatteryPack
from .components.chassis import Chassis
from .components.driveline import Driveline
from .components.hvac import HVAC
from .components.motor import Motor
from .library import KINDS, Library, LibraryError, _alias_table, build
from .maps import Curve1D, Map2D
from .units import RPM
from .vehicle import ControlStrategy, Vehicle

COMPONENT_KINDS = {
    "chassis": Chassis, "driveline": Driveline, "motor": Motor,
    "battery": BatteryPack, "hvac": HVAC, "auxiliary": Auxiliaries,
    "aging": AgingModel,
}
KIND_LABELS_TR = {
    "vehicle": "Araç", "chassis": "Şasi ve yol direnci", "driveline": "Aktarma organları",
    "motor": "Motor ve invertör", "battery": "Batarya paketi", "hvac": "Klima (HVAC)",
    "auxiliary": "Yardımcı tüketiciler", "aging": "Batarya yaşlanma modeli",
    "control": "Kontrol stratejisi",
}
VEHICLE_SLOTS = {"chassis": "chassis", "driveline": "driveline", "motor": "motor",
                 "battery": "battery", "hvac": "hvac", "auxiliaries": "auxiliary",
                 "aging": "aging"}

# Display units: (suffix, unit label, multiplier from SI to display).
# Longest suffixes first so '_n_per_mps' wins over '_mps'.
UNIT_RULES = [
    ("_n_per_mps2", "N/(m/s)²", 1.0), ("_n_per_mps", "N/(m/s)", 1.0),
    ("_w_per_m2k", "W/m²K", 1.0), ("_w_per_k", "W/K", 1.0),
    ("_j_per_kgk", "J/kgK", 1.0), ("_j_per_mol", "kJ/mol", 1e-3),
    ("_j_per_k", "kJ/K", 1e-3), ("_m3_per_h_per_passenger", "m³/h/yolcu", 1.0),
    ("_m3_per_h", "m³/h", 1.0), ("_s_per_m", "s/m", 1.0),
    ("_kgm2", "kg·m²", 1.0), ("_mps2", "m/s²", 1.0), ("_mps", "km/h", 3.6),
    ("_rads", "rpm", 1.0 / RPM), ("_rpm", "rpm", 1.0), ("_ohm", "mΩ", 1e3),
    ("_w", "kW", 1e-3), ("_j", "kJ", 1e-3), ("_kg", "kg", 1.0), ("_nm", "Nm", 1.0),
    ("_n", "N", 1.0), ("_m3", "m³", 1.0), ("_m2", "m²", 1.0), ("_m", "m", 1.0),
    ("_ah", "Ah", 1.0), ("_v", "V", 1.0), ("_c", "°C", 1.0), ("_s", "s", 1.0),
]
# Fields that are fractions in the solver and percentages to humans.
PERCENT_FIELDS = {
    "soc_min", "soc_max", "soc_start", "soc_target_min", "coulombic_efficiency",
    "gearbox_efficiency", "final_drive_efficiency", "peak_efficiency",
    "resistive_efficiency", "fuel_fired_efficiency", "dcdc_efficiency",
    "end_of_life_capacity", "regen_blend_fraction", "coast_regen_fraction",
    "air_compressor_duty_base", "steering_speed_factor", "regen_efficiency_penalty",
}
PLAIN_UNITS = {"max_discharge_c_rate": "C", "max_charge_c_rate": "C",
               "cyc_c_rate_alpha": "", "air_density": "kg/m³",
               "coastdown_air_density": "kg/m³", "power_margin": ""}

CHOICES = {
    "system_type": [("heat_pump", "Isı pompası"), ("hybrid", "Isı pompası + yedek ısıtıcı"),
                    ("resistive", "Rezistif ısıtıcı"), ("fuel_fired", "Yakıtlı ısıtıcı (dizel)")],
    "backup_heat_source": [("resistive", "Rezistif"), ("fuel_fired", "Yakıtlı (dizel)")],
    "chemistry": [("NMC", "NMC"), ("LFP", "LFP"), ("LTO", "LTO"), ("NCA", "NCA")],
    "technology": [("PMSM", "PMSM (sürekli mıknatıslı)"), ("ASM", "ASM (asenkron)"),
                   ("SRM", "SRM (anahtarlamalı relüktans)")],
    "layout": [("direct_drive", "Merkezi motor, doğrudan aks"),
               ("central_motor", "Merkezi motor + şanzıman"),
               ("portal_axle", "Portal aks / tekerlek içi motor")],
}

LABELS_TR = {
    # common
    "name": "Ad", "notes": "Notlar", "validated": "Ölçümle doğrulandı",
    "chemistry": "Kimya",
    # chassis
    "kerb_mass_kg": "Boş ağırlık", "gross_mass_kg": "Azami yüklü ağırlık",
    "passenger_mass_kg": "Yolcu başına kütle", "rotational_inertia_kgm2": "Tekerlek atalet momenti",
    "rolling_c0": "Yuvarlanma direnci katsayısı", "rolling_c1_s_per_m": "Hıza bağlı yuvarlanma terimi",
    "drag_coefficient": "Hava direnci katsayısı (Cd)", "frontal_area_m2": "Ön alan",
    "coastdown_a_n": "Coast-down A", "coastdown_b_n_per_mps": "Coast-down B",
    "coastdown_c_n_per_mps2": "Coast-down C", "coastdown_test_mass_kg": "Coast-down test kütlesi",
    "coastdown_air_density": "Coast-down hava yoğunluğu", "tyre_dynamic_radius_m": "Dinamik lastik yarıçapı",
    # driveline
    "layout": "Yerleşim", "gear_ratios": "Vites oranları", "final_drive_ratio": "Diferansiyel oranı",
    "gearbox_efficiency": "Şanzıman verimi", "final_drive_efficiency": "Diferansiyel verimi",
    "efficiency_map": "Verim haritası", "motor_inertia_kgm2": "Rotor ataleti",
    "shift_up_speed_mps": "Vites yükseltme hızı", "shift_down_speed_mps": "Vites düşürme hızı",
    "spin_loss_nm": "Sürtünme kayıp torku",
    # motor
    "technology": "Teknoloji", "peak_power_w": "Tepe güç", "continuous_power_w": "Sürekli güç",
    "peak_torque_nm": "Tepe tork", "continuous_torque_nm": "Sürekli tork",
    "max_speed_rads": "Azami devir", "peak_efficiency": "Tepe verim",
    "peak_efficiency_at_rpm": "Tepe verim devri", "peak_efficiency_at_torque_nm": "Tepe verim torku",
    "regen_efficiency_penalty": "Rejenerasyon verim kaybı", "max_regen_power_w": "Azami rejenerasyon gücü",
    "field_weakening_exponent": "Alan zayıflatma üssü",
    # battery
    "cells_series": "Seri hücre sayısı", "cells_parallel": "Paralel hücre sayısı",
    "cell_capacity_ah": "Hücre kapasitesi", "cell_nominal_voltage_v": "Hücre nominal gerilimi",
    "ocv_curve": "Açık devre gerilimi (OCV) eğrisi", "cell_resistance_ohm": "Hücre iç direnci",
    "resistance_soc_factor": "Direncin SOC'ye göre çarpanı",
    "resistance_activation_j_per_mol": "Direnç aktivasyon enerjisi",
    "resistance_reference_temp_c": "Direnç referans sıcaklığı",
    "coulombic_efficiency": "Kulombik verim", "cell_voltage_min_v": "Hücre alt gerilim sınırı",
    "cell_voltage_max_v": "Hücre üst gerilim sınırı", "max_discharge_c_rate": "Azami deşarj hızı",
    "max_charge_c_rate": "Azami şarj hızı", "soc_min": "Kullanılabilir SOC alt sınırı",
    "soc_max": "Kullanılabilir SOC üst sınırı", "charge_accept_derate": "Soğukta şarj kabulü eğrisi",
    "pack_mass_kg": "Paket kütlesi", "specific_heat_j_per_kgk": "Paket özgül ısısı",
    "thermal_conductance_to_ambient_w_per_k": "Ortama ısıl iletkenlik",
    "cooling_capacity_w": "Soğutma kapasitesi", "heating_capacity_w": "Isıtma kapasitesi",
    "cooling_cop": "Soğutma COP", "heating_cop": "Isıtma COP",
    "thermal_target_c": "Hedef paket sıcaklığı", "cooling_on_above_c": "Soğutma devreye girme sıcaklığı",
    "heating_on_below_c": "Isıtma devreye girme sıcaklığı",
    # hvac
    "system_type": "Isıtma sistemi", "backup_heat_source": "Yedek ısı kaynağı",
    "cabin_volume_m3": "Kabin hacmi", "glazing_area_m2": "Cam alanı", "opaque_area_m2": "Opak gövde alanı",
    "u_glazing_w_per_m2k": "Cam U-değeri", "u_opaque_w_per_m2k": "Gövde U-değeri",
    "thermal_bridge_factor": "Isı köprüsü çarpanı", "interior_thermal_mass_j_per_k": "İç ısıl kütle",
    "shgc": "Güneş ısı kazanç katsayısı (SHGC)",
    "fresh_air_m3_per_h_per_passenger": "Yolcu başına taze hava",
    "fresh_air_minimum_m3_per_h": "Asgari taze hava", "door_count": "Kapı sayısı",
    "door_width_m": "Kapı genişliği", "door_height_m": "Kapı yüksekliği",
    "door_discharge_coefficient": "Kapı akış katsayısı",
    "heat_pump_cop": "Isı pompası COP eğrisi", "resistive_efficiency": "Rezistif ısıtıcı verimi",
    "fuel_fired_efficiency": "Yakıtlı ısıtıcı verimi", "fuel_fired_parasitic_w": "Yakıtlı ısıtıcı yardımcı tüketimi",
    "heat_pump_cutoff_c": "Isı pompası kesme sıcaklığı", "blower_power_w": "Fan (blower) gücü",
    "condenser_fan_power_w": "Kondenser fan gücü", "setpoint_heating_c": "Isıtma ayar sıcaklığı",
    "setpoint_cooling_c": "Soğutma ayar sıcaklığı", "control_time_constant_s": "Kontrol zaman sabiti",
    "passenger_sensible_w": "Yolcu başına duyulur ısı",
    # auxiliaries
    "constant_w": "Sabit yük (kontrolcüler, telematik, ekranlar)", "lighting_w": "Aydınlatma (gündüz)",
    "lighting_night_w": "Aydınlatma (gece)", "steering_pump_w": "Direksiyon pompası",
    "steering_speed_factor": "Yüksek hızda direksiyon yük oranı", "air_compressor_w": "Hava kompresörü gücü",
    "air_compressor_duty_base": "Kompresör temel çalışma oranı",
    "air_per_stop_j": "Durak başına kompresör işi", "dcdc_efficiency": "DC-DC verimi",
    # aging
    "cal_prefactor": "Takvim yaşlanma katsayısı", "cal_activation_j_per_mol": "Takvim aktivasyon enerjisi",
    "cal_reference_temp_c": "Takvim referans sıcaklığı", "cal_time_exponent": "Takvim zaman üssü",
    "cal_soc_reference": "Takvim referans SOC", "cal_soc_exponent": "Takvim SOC üssü",
    "cyc_prefactor": "Çevrim yaşlanma katsayısı", "cyc_activation_j_per_mol": "Çevrim aktivasyon enerjisi",
    "cyc_c_rate_alpha": "C-oranı katsayısı (alfa)", "cyc_throughput_exponent": "Çevrim Ah üssü",
    "cyc_reference_temp_c": "Çevrim referans sıcaklığı", "end_of_life_capacity": "Ömür sonu kapasite",
    # control
    "regen_enabled": "Rejeneratif frenleme açık", "max_regen_decel_mps2": "Azami rejenerasyon yavaşlaması",
    "regen_blend_fraction": "Frenin motora verilen payı", "min_regen_speed_mps": "Rejenerasyonun kesildiği hız",
    "coast_regen_fraction": "Gaz bırakınca rejenerasyon", "soc_start": "Başlangıç SOC",
    "soc_target_min": "Operasyonel asgari SOC", "cabin_preconditioned": "Kabin depoda ön ısıtılır/soğutulur",
    "precondition_cabin_c": "Ön koşullandırma kabin sıcaklığı",
    "pack_preconditioned": "Paket depoda ön ısıtılır", "precondition_pack_c": "Ön koşullandırma paket sıcaklığı",
    # vehicle
    "variant": "Varyant", "seats": "Oturan yolcu", "standees": "Ayakta yolcu",
}
SECTIONS_TR = {
    "mass": "Kütle", "road load, physical parameterisation": "Yol direnci (fiziksel)",
    "road load, coast-down parameterisation": "Yol direnci (coast-down ölçümü)",
    "wheels": "Tekerlek", "topology": "Hücre yapısı",
    "electrical characteristics": "Elektriksel özellikler", "operating limits": "Çalışma sınırları",
    "cold charge-acceptance derating": "Soğukta şarj kabulü", "thermal": "Isıl yönetim",
    "cabin geometry and construction": "Kabin geometrisi ve yalıtım",
    "ventilation and doors": "Havalandırma ve kapılar",
    "capacity and efficiency": "Kapasite ve verim", "control": "Kontrol",
    "calendar": "Takvim yaşlanması", "cycling": "Çevrim yaşlanması",
}
ESSENTIAL = {
    "chassis": {"name", "kerb_mass_kg", "gross_mass_kg", "drag_coefficient", "frontal_area_m2",
                "rolling_c0", "tyre_dynamic_radius_m", "coastdown_a_n", "coastdown_b_n_per_mps",
                "coastdown_c_n_per_mps2", "coastdown_test_mass_kg", "validated"},
    "driveline": {"name", "layout", "gear_ratios", "final_drive_ratio", "gearbox_efficiency",
                  "final_drive_efficiency", "validated"},
    "motor": {"name", "technology", "peak_power_w", "continuous_power_w", "peak_torque_nm",
              "continuous_torque_nm", "max_speed_rads", "efficiency_map", "peak_efficiency",
              "peak_efficiency_at_rpm", "peak_efficiency_at_torque_nm", "validated"},
    "battery": {"name", "chemistry", "cells_series", "cells_parallel", "cell_capacity_ah",
                "cell_nominal_voltage_v", "cell_resistance_ohm", "ocv_curve", "soc_min", "soc_max",
                "max_discharge_c_rate", "max_charge_c_rate", "pack_mass_kg", "validated"},
    "hvac": {"name", "system_type", "heating_capacity_w", "cooling_capacity_w", "heat_pump_cop",
             "cooling_cop", "cabin_volume_m3", "glazing_area_m2", "u_glazing_w_per_m2k",
             "door_count", "setpoint_heating_c", "setpoint_cooling_c", "validated"},
    "auxiliary": {"name", "constant_w", "lighting_w", "steering_pump_w", "air_compressor_w",
                  "validated"},
    "aging": {"name", "chemistry", "end_of_life_capacity", "validated"},
    "control": {"regen_enabled", "soc_start", "soc_target_min", "cabin_preconditioned",
                "pack_preconditioned"},
}
# Fields the form greys out when another field is set.
IGNORED_IF = {"peak_efficiency": "efficiency_map", "peak_efficiency_at_rpm": "efficiency_map",
              "peak_efficiency_at_torque_nm": "efficiency_map"}
HIDDEN = {"source"}


# ---------------------------------------------------------------- introspection
def _class_doc_and_sections(cls) -> tuple[dict, dict]:
    """Attribute docstrings and section headings, read from the source."""
    src = textwrap.dedent(inspect.getsource(cls))
    tree = ast.parse(src)
    cdef = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    lines = src.splitlines()
    docs, sections = {}, {}
    current = None
    last_line = 0
    body = cdef.body
    for k, node in enumerate(body):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            for ln in lines[last_line:node.lineno - 1]:
                m = re.match(r"\s*#\s*-+\s*(.+?)\s*-+\s*$", ln)
                if m:
                    current = m.group(1).strip()
            name = node.target.id
            sections[name] = current
            if k + 1 < len(body):
                nxt = body[k + 1]
                if (isinstance(nxt, ast.Expr) and isinstance(nxt.value, ast.Constant)
                        and isinstance(nxt.value.value, str)):
                    docs[name] = " ".join(nxt.value.value.split())
            last_line = node.lineno
    return docs, sections


def unit_for(name: str) -> tuple[str, float]:
    if name in PERCENT_FIELDS:
        return "%", 100.0
    if name in PLAIN_UNITS:
        return PLAIN_UNITS[name], 1.0
    for suffix, label, factor in UNIT_RULES:
        if name.endswith(suffix):
            return label, factor
    return "", 1.0


def _ftype(f) -> str:
    t = str(f.type)
    if "Curve1D" in t:
        return "curve"
    if "Map2D" in t:
        return "map"
    if t.startswith("bool"):
        return "bool"
    if t.startswith("int"):
        return "int"
    if t.startswith("list"):
        return "list"
    if t.startswith("str"):
        return "choice" if f.name in CHOICES else ("text" if f.name == "notes" else "str")
    if "float" in t:
        return "float"
    return "other"


def schema(kind: str) -> dict:
    """Field metadata for one component kind (or 'control')."""
    cls = ControlStrategy if kind == "control" else COMPONENT_KINDS[kind]
    docs, sections = _class_doc_and_sections(cls)
    defaults = cls()
    fields = []
    for f in dataclasses.fields(cls):
        if f.name in HIDDEN:
            continue
        ft = _ftype(f)
        if ft == "other":
            continue
        unit, factor = unit_for(f.name) if ft in ("float", "int") else ("", 1.0)
        sec = sections.get(f.name)
        fields.append({
            "name": f.name,
            "label": LABELS_TR.get(f.name, f.name.replace("_", " ")),
            "type": ft,
            "unit": unit,
            "factor": factor,
            "optional": "None" in str(f.type),
            "default": _to_display(f.name, ft, getattr(defaults, f.name), factor),
            "doc": docs.get(f.name, ""),
            "section": SECTIONS_TR.get(sec, sec.capitalize() if sec else "Genel"),
            "essential": f.name in ESSENTIAL.get(kind, set()),
            "choices": CHOICES.get(f.name),
            "ignored_if": IGNORED_IF.get(f.name),
        })
    return {"kind": kind, "label": KIND_LABELS_TR.get(kind, kind), "fields": fields,
            "doc": " ".join((cls.__doc__ or "").split())}


def _to_display(name, ftype, value, factor):
    if value is None:
        return None
    if ftype == "float":
        v = float(value) * factor
        return float(f"{v:.6g}")
    if ftype == "int":
        return int(value)
    if ftype == "curve":
        return {"points": [[float(a), float(b)] for a, b in zip(value.x, value.y)],
                "source": value.source}
    if ftype == "map":
        return {"source": value.source}
    if ftype == "list":
        return list(value)
    return value


def component_values(obj, kind: str) -> dict:
    """Current values of a built component, in display units."""
    out = {}
    for fdef in schema(kind)["fields"]:
        v = getattr(obj, fdef["name"])
        out[fdef["name"]] = _to_display(fdef["name"], fdef["type"], v, fdef["factor"])
    return out


# ---------------------------------------------------------------- saving
def to_yaml_dict(kind: str, values: dict, *, map_refs: dict | None = None) -> dict:
    """Display-unit form values -> a YAML-ready dict the loader accepts.

    Writes an engineering-unit alias key (``peak_power_kw``) where the loader
    supports one, because a human will read these files; otherwise the SI key.
    """
    cls = ControlStrategy if kind == "control" else COMPONENT_KINDS[kind]
    table = _alias_table(cls)
    meta = {f["name"]: f for f in schema(kind)["fields"]}
    out: dict = {} if kind == "control" else {"kind": kind}
    map_refs = map_refs or {}

    for name, raw in values.items():
        if name not in meta:
            raise LibraryError(f"{name!r} bu bileşen tipinde bir alan değil")
        f = meta[name]
        if f["type"] == "map":
            if name in map_refs:
                out[name] = map_refs[name]
            elif isinstance(raw, str) and raw:
                out[name] = raw
            continue
        if raw is None or raw == "":
            if f["optional"]:
                continue
            continue          # leave the dataclass default in place
        if f["type"] == "float":
            si = float(raw) / f["factor"]
            # Prefer a readable alias whose factor reproduces the same SI value.
            alias = _best_alias(name, f["unit"], table)
            if alias:
                out[alias] = float(f"{si / table[alias][1]:.8g}")
            else:
                out[name] = float(f"{si:.8g}")
        elif f["type"] == "int":
            out[name] = int(round(float(raw)))
        elif f["type"] == "bool":
            out[name] = bool(raw)
        elif f["type"] == "list":
            if isinstance(raw, str):
                raw = [float(x) for x in re.split(r"[;,\s]+", raw.replace(",", " ").strip()) if x]
            out[name] = [float(x) for x in raw]
        elif f["type"] == "curve":
            pts = raw.get("points") if isinstance(raw, dict) else raw
            if pts:
                out[name] = {"points": [[float(a), float(b)] for a, b in pts],
                             "source": (raw.get("source") if isinstance(raw, dict) else None)
                             or "entered in UI"}
        else:
            out[name] = str(raw)
    # Newly uploaded maps arrive as references, not as form values; make sure
    # every one lands in the file even when the form did not send the field.
    for name, ref in map_refs.items():
        if name not in meta or meta[name]["type"] != "map":
            raise LibraryError(f"{name!r} bir harita alanı değil")
        out[name] = ref
    return out


SI_TO_ALIAS = {"kW": ("_w", "_kw"), "rpm": ("_rads", "_rpm"), "mΩ": ("_ohm", "_mohm"),
               "km/h": ("_mps", "_kmh"), "kJ/K": ("_j_per_k", "_kj_per_k"),
               "kJ/mol": ("_j_per_mol", "_kj_per_mol"), "kJ": ("_j", "_kj")}


def _best_alias(name, unit, table):
    """The loader-accepted alias for this field in its display unit, if any."""
    pair = SI_TO_ALIAS.get(unit)
    if not pair or not name.endswith(pair[0]):
        return None
    alias = name[: -len(pair[0])] + pair[1]
    return alias if table.get(alias, (None,))[0] == name else None


def safe_stem(text: str) -> str:
    table = str.maketrans("ıİşŞğĞüÜöÖçÇ", "iISsGgUuOoCc")
    s = text.translate(table).lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "bilesen"


def save_component(lib: Library, kind: str, stem: str, values: dict, *,
                   description: str = "", map_refs: dict | None = None,
                   overwrite: bool = False) -> pathlib.Path:
    """Validate by building, then write library/<kind>/<stem>.yaml."""
    stem = safe_stem(stem)
    data = to_yaml_dict(kind, values, map_refs=map_refs)
    if description:
        data["description"] = description
    folder = lib.root / kind
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stem}.yaml"
    if path.exists() and not overwrite:
        raise FileExistsError(f"{kind}/{stem}.yaml zaten var")
    # Build it first: never write a file the loader would then reject.
    build(KINDS[kind], dict(data), base_dir=folder, where=f"{kind}/{stem}.yaml (form)")
    header = ("# Created or edited in the temsasim interface. Units in keys: _kw = kW,\n"
              "# _rpm = rpm, _mohm = milliohm, _kmh = km/h; everything else is SI.\n")
    path.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")
    return path


def save_vehicle(lib: Library, stem: str, *, name: str, variant: str, seats: int,
                 standees: int, slots: dict, control: dict, description: str = "",
                 overwrite: bool = False) -> pathlib.Path:
    stem = safe_stem(stem)
    data: dict = {"kind": "vehicle", "name": name, "variant": variant}
    if description:
        data["description"] = description
    for slot, kind in VEHICLE_SLOTS.items():
        ref = slots.get(slot)
        if not ref:
            raise LibraryError(f"{KIND_LABELS_TR[kind]} seçilmedi")
        data[slot] = f"{kind}/{ref}.yaml"
    data["seats"] = int(seats)
    data["standees"] = int(standees)
    data["control"] = to_yaml_dict("control", control)
    path = lib.root / "vehicle" / f"{stem}.yaml"
    if path.exists() and not overwrite:
        raise FileExistsError(f"vehicle/{stem}.yaml zaten var")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Created or edited in the temsasim interface.\n"
                    + yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")
    try:
        lib.load_vehicle(stem)
    except Exception:
        path.unlink()
        raise
    return path


def raw_description(lib: Library, kind: str, stem: str) -> str:
    p = lib.root / kind / f"{stem}.yaml"
    if not p.exists():
        return ""
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return " ".join(str(data.get("description", "")).split())
