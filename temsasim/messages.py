"""Bilingual messages.

Warnings and diagnoses are produced deep in the solver, but are read in two
places: the English command line and the Turkish interface. A ``Note`` is an
ordinary ``str`` (the English text), so every existing caller keeps working,
and it carries its Turkish translation alongside in ``.tr``.
"""

from __future__ import annotations


class Note(str):
    """English text that also knows its Turkish translation."""

    tr: str

    def __new__(cls, en: str, tr: str | None = None):
        obj = super().__new__(cls, en)
        obj.tr = tr if tr is not None else en
        return obj


def localise(items, lang: str = "en") -> list:
    """Pick one language from a list of Notes (plain strings pass through)."""
    if lang == "tr":
        return [getattr(x, "tr", str(x)) for x in items]
    return [str(x) for x in items]


_DESCRIBE_TR = [
    ("[VALIDATED]", "[ölçüldü]"), ("[UNVALIDATED]", "[kabul değeri]"), ("[CALIBRATED]", "[kalibre edildi]"),
    ("[LITERATURE COEFFICIENTS]", "[literatür katsayıları]"),
    ("coast-down (measured)", "coast-down (ölçülmüş)"), ("tyre RRC x ", "lastik RRC × "), (" axles,", " aks,"),
    ("physical Cr/Cd", "fiziksel Cr/Cd"), (" kerb", " boş kütle"), ("diesel,", "dizel,"), ("idle ", "rölanti "),
    (" map=none", " harita yok"), (" map=", " harita: "), ("gears ", "vites "), (" x ", " × "), (" final", " son oran"),
    (" kW heat / ", " kW ısıtma / "), (" kW cool ", " kW soğutma "), ("hotel + steering + air", "otel yükü + direksiyon + hava"),
    (" kW pk / ", " kW tepe / "), (" kW cont", " kW sürekli"), (" kWh nominal / ", " kWh anma / "), (" kWh usable", " kWh kullanılabilir"),
    ("EOL at ", "ömür sonu "), ("heat_pump", "ısı pompası"), ("resistive", "rezistif"), ("fuel_fired", "yakıtlı ısıtıcı"),
    ("central_motor", "merkezi motor"), ("hub_motor", "tekerlek içi motor"), ("direct_drive", "doğrudan aks"),
]


def describe_tr(obj) -> str:
    """A component's one-line ``describe()`` with its English tokens turned
    into Turkish, for the reports. The English text stays the CLI's."""
    text = obj.describe() if hasattr(obj, "describe") else str(obj)
    for en, tr in _DESCRIBE_TR:
        text = text.replace(en, tr)
    return text


def tr_num(v: float, d: int = 1) -> str:
    """Turkish number formatting: 1.234,5"""
    s = f"{v:,.{d}f}"
    return s.replace(",", " ").replace(".", ",").replace(" ", ".")
