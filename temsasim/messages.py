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


def tr_num(v: float, d: int = 1) -> str:
    """Turkish number formatting: 1.234,5"""
    s = f"{v:,.{d}f}"
    return s.replace(",", " ").replace(".", ",").replace(" ", ".")
