"""Tolerant importers: take whatever file an engineer actually has.

The previous workflow asked people to reshape supplier data into one exact CSV
layout by hand. Nobody's data arrives like that. A motor map comes as an Excel
sheet with a title row and merged headers, or as a Turkish-locale CSV with
semicolons and decimal commas, or as three long columns out of a dyno logger,
in rpm or rad/s, as percentages or fractions, sometimes as a loss map in kW.

So these importers never demand a format. They *detect* one, report what they
detected and how confident they are, and accept overrides for anything they
guessed. The UI shows the detection next to a preview; the engineer confirms
or flips a setting, and only then is anything written to the library.

Every detector returns plain data (dicts, lists, numpy arrays) so the web layer
can serialise it and the tests can assert on it without a browser.
"""

from __future__ import annotations

import csv
import io
import math
import pathlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import numpy as np

from .maps import Curve1D, Map2D
from .units import RPM

# ---------------------------------------------------------------- vocabulary
# Header words in English and Turkish, lower-cased and accent-folded before
# matching (see _fold). Order inside each list does not matter.
SPEED_WORDS = ["rpm", "1/min", "min-1", "min^-1", "rad/s", "rad", "speed", "devir",
               "hiz", "n [", "n[", "n_", "omega", "d/d", "turn"]
TORQUE_WORDS = ["nm", "torque", "tork", "moment", "t [", "t[", "m [", "m[", "t_", "m_"]
EFF_WORDS = ["eff", "verim", "eta", "η", "%"]
LOSS_WORDS = ["loss", "kayip", "kayıp", "p_loss", "ploss", "dissip"]


def _fold(s: str) -> str:
    """Lower-case and strip Turkish accents so 'Hız' matches 'hiz'."""
    table = str.maketrans("ıİşŞğĞüÜöÖçÇ", "iissgguuoocc")
    return str(s).translate(table).lower().strip()


# ---------------------------------------------------------------- reading
@dataclass
class Table:
    """A rectangular block of raw cells from any source, plus how it was read."""

    cells: list
    source_name: str
    kind: str                      # "excel", "text"
    delimiter: str | None = None
    decimal: str = "."
    encoding: str | None = None
    sheet: str | None = None
    sheets: list = field(default_factory=list)

    def describe(self) -> dict:
        return {"source": self.source_name, "kind": self.kind,
                "delimiter": {"\t": "tab", ";": ";", ",": ",", "|": "|",
                              None: "boşluk"}.get(self.delimiter, self.delimiter),
                "decimal": self.decimal, "encoding": self.encoding,
                "sheet": self.sheet, "sheets": self.sheets,
                "rows": len(self.cells),
                "cols": max((len(r) for r in self.cells), default=0)}


def read_table(data: bytes, filename: str, sheet: str | None = None) -> Table:
    """Read Excel or delimited text into a Table, detecting everything."""
    name = pathlib.Path(filename).name
    suffix = pathlib.Path(filename).suffix.lower()
    if suffix in (".xlsx", ".xlsm") or data[:4] == b"PK\x03\x04":
        return _read_excel(data, name, sheet)
    if suffix == ".xls":
        raise ValueError(
            "Eski .xls biçimi desteklenmiyor. Excel'de 'Farklı kaydet' ile .xlsx "
            "ya da .csv olarak kaydedip tekrar deneyin.")
    return _read_text(data, name)


def _read_excel(data: bytes, name: str, sheet: str | None) -> Table:
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - depends on install
        raise ValueError("Excel dosyaları için 'openpyxl' paketi gerekli: "
                         "pip install openpyxl") from exc
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    names = wb.sheetnames
    if sheet and sheet in names:
        ws = wb[sheet]
    else:
        # Pick the sheet with the most numeric cells: the data, not the cover page.
        best, best_n = names[0], -1
        for n in names:
            count = 0
            for row in wb[n].iter_rows(values_only=True, max_row=400):
                count += sum(isinstance(v, (int, float)) and not isinstance(v, bool)
                             for v in row)
            if count > best_n:
                best, best_n = n, count
        ws = wb[best]
    cells = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    return Table(cells=cells, source_name=name, kind="excel",
                 sheet=ws.title, sheets=names)


def _decode(data: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "cp1254", "latin-1"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace"), "latin-1"


def _read_text(data: bytes, name: str) -> Table:
    text, enc = _decode(data)
    lines = [ln for ln in text.splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        raise ValueError(f"{name}: dosyada veri satırı bulunamadı.")

    sample = lines[:60]
    best, best_score = None, 0.0
    for d in ("\t", ";", ",", "|"):
        counts = [ln.count(d) for ln in sample]
        if max(counts) == 0:
            continue
        mode = max(set(counts), key=counts.count)
        if mode == 0:
            continue
        consistency = counts.count(mode) / len(counts)
        score = consistency * math.log1p(mode)
        if score > best_score:
            best, best_score = d, score

    if best is None:
        rows = [ln.split() for ln in lines]
    else:
        rows = list(csv.reader(lines, delimiter=best))

    # Decimal comma: common in Turkish/European exports, where the delimiter is
    # then a semicolon or tab. Detect from the cells themselves.
    decimal = "."
    if best in (";", "\t", "|", None):
        flat = [c.strip() for r in rows[:60] for c in r]
        comma_nums = sum(bool(re.fullmatch(r"[-+]?\d+,\d+%?", c)) for c in flat)
        dot_nums = sum(bool(re.fullmatch(r"[-+]?\d+\.\d+%?", c)) for c in flat)
        if comma_nums > dot_nums:
            decimal = ","
    return Table(cells=rows, source_name=name, kind="text", delimiter=best,
                 decimal=decimal, encoding=enc)


def parse_number(v, decimal: str = "."):
    """Cell -> float, or None. Handles '95,5', '1.234,5', '%95', '95 %', ' 12 '."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    s = str(v).strip().replace(" ", "").replace(" ", "")
    if not s or s in ("-", "--", "n/a", "NA", "nan", "NaN"):
        return None
    s = s.replace("%", "")
    if decimal == ",":
        s = s.replace(".", "").replace(",", ".")
    elif "," in s and "." in s:
        s = s.replace(",", "")
    try:
        x = float(s)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def numeric_matrix(table: Table) -> np.ndarray:
    width = max((len(r) for r in table.cells), default=0)
    m = np.full((len(table.cells), width), np.nan)
    for i, r in enumerate(table.cells):
        for j, v in enumerate(r):
            x = parse_number(v, table.decimal)
            if x is not None:
                m[i, j] = x
    return m


def _text_of(table: Table, rows: range, cols: range) -> str:
    out = []
    for i in rows:
        if i >= len(table.cells):
            continue
        for j in cols:
            if j < len(table.cells[i]) and table.cells[i][j] is not None:
                v = table.cells[i][j]
                if parse_number(v, table.decimal) is None:
                    out.append(str(v))
    return " ".join(out)


def _header_matches(header: str, words) -> bool:
    """Does a column header name one of ``words``?

    Matches whole tokens, and token *stems* for words of three or more letters,
    because Turkish attaches suffixes: 'Araç hızı' must match 'hız', 'Batarya
    gerilimi' must match 'gerilim'. Single letters ('v', 't', 'p') only ever
    match as a whole token, or they would match everything.
    """
    f = _fold(header)
    tokens = [tok for tok in re.split(r"[\s_\[\]\(\)/,.;:\-]+", f) if tok]
    joined = "_".join(tokens)
    for w in words:
        w = _fold(w)
        if f == w or joined == w:
            return True
        if "_" in w and w in joined:
            return True
        if w in tokens:
            return True
        if len(w) >= 3 and any(tok.startswith(w) for tok in tokens):
            return True
    return False


def _has(words, text: str) -> bool:
    t = _fold(text)
    return any(w in t for w in words)


# ---------------------------------------------------------------- 2-D maps
MAP_ORIENTATIONS = ("speed_in_columns", "speed_in_rows")
SPEED_UNITS = ("rpm", "rad_s")
VALUE_TYPES = ("efficiency_fraction", "efficiency_percent", "loss_kw", "loss_w")


@dataclass
class MapImport:
    map: Map2D
    settings: dict
    detected: dict
    confidence: dict
    warnings: list
    filled_mask: np.ndarray
    layout: str
    table: dict

    def preview(self) -> dict:
        m = self.map
        z = m.z
        j, i = np.unravel_index(np.argmax(z), z.shape)
        return {
            "speed_rpm": [round(float(x) / RPM, 1) for x in m.x],
            "torque_nm": [round(float(y), 1) for y in m.y],
            "efficiency": [[round(float(v), 4) for v in row] for row in z],
            "filled": self.filled_mask.astype(int).tolist(),
            "peak": {"efficiency": round(float(z[j, i]), 4),
                     "speed_rpm": round(float(m.x[i]) / RPM),
                     "torque_nm": round(float(m.y[j]))},
            "range": [round(float(z[z > 0].min()), 4) if np.any(z > 0) else 0.0,
                      round(float(z.max()), 4)],
            "shape": [int(m.y.size), int(m.x.size)],
        }

    def to_dict(self) -> dict:
        return {"settings": self.settings, "detected": self.detected,
                "confidence": self.confidence, "warnings": self.warnings,
                "layout": self.layout, "table": self.table,
                "filled_cells": int(self.filled_mask.sum()),
                "preview": self.preview()}


def import_map(table: Table, overrides: dict | None = None) -> MapImport:
    """Detect and convert a motor/inverter efficiency or loss map.

    ``overrides`` may set ``orientation``, ``speed_unit`` or ``value_type`` to
    replace a guess. Returns the map in solver units (rad/s, Nm, efficiency
    fraction) together with what was detected, so the UI can show both.
    """
    overrides = {k: v for k, v in (overrides or {}).items() if v}
    num = numeric_matrix(table)
    warnings: list = []

    layout, axis_a, axis_b, values, hint_text = _extract_map_block(table, num)

    # --- orientation --------------------------------------------------------
    # axis_a runs across the top (columns), axis_b down the side (rows).
    det_orient, conf_orient = _guess_orientation(axis_a, axis_b, hint_text, layout)
    orient = overrides.get("orientation", det_orient)
    if orient == "speed_in_columns":
        speed, torque, z = axis_a, axis_b, values          # z[row=torque, col=speed]
    else:
        speed, torque, z = axis_b, axis_a, values.T

    # --- speed unit ---------------------------------------------------------
    det_unit, conf_unit = _guess_speed_unit(speed, hint_text)
    unit = overrides.get("speed_unit", det_unit)
    speed_rads = speed * (RPM if unit == "rpm" else 1.0)

    # --- keep the motoring quadrant ----------------------------------------
    keep_t = torque > 0
    keep_w = speed_rads >= 0
    if not keep_t.all() or not keep_w.all():
        warnings.append(
            f"{int((~keep_t).sum())} negatif tork ve {int((~keep_w).sum())} negatif hız "
            f"satırı/sütunu atlandı. Model motor bölgesini kullanır; rejenerasyon "
            f"bu haritanın aynası olarak hesaplanır.")
    speed_rads, torque = speed_rads[keep_w], torque[keep_t]
    z = z[np.ix_(keep_t, keep_w)]

    # --- sort and de-duplicate axes ----------------------------------------
    order_w = np.argsort(speed_rads)
    order_t = np.argsort(torque)
    speed_rads, torque = speed_rads[order_w], torque[order_t]
    z = z[np.ix_(order_t, order_w)]
    if np.any(np.diff(speed_rads) <= 0) or np.any(np.diff(torque) <= 0):
        speed_rads, torque, z = _merge_duplicate_axes(speed_rads, torque, z)
        warnings.append("Tekrarlanan eksen değerleri bulundu ve ortalamaları alındı.")
    if speed_rads.size < 2 or torque.size < 2:
        raise ValueError("Haritada en az 2 hız ve 2 tork değeri olmalı; "
                         "bulunan veri bir harita gibi görünmüyor.")

    # --- value type ---------------------------------------------------------
    det_vt, conf_vt = _guess_value_type(z, hint_text)
    vt = overrides.get("value_type", det_vt)
    W, T = np.meshgrid(speed_rads, torque)
    if vt == "efficiency_fraction":
        eff = z.copy()
    elif vt == "efficiency_percent":
        eff = z / 100.0
    else:
        loss_w = z * (1e3 if vt == "loss_kw" else 1.0)
        p = W * T
        with np.errstate(invalid="ignore", divide="ignore"):
            eff = np.where(p > 0, p / (p + loss_w), np.nan)

    # --- fill blanks (typically outside the torque envelope) ---------------
    blank = ~np.isfinite(eff)
    if blank.all():
        raise ValueError("Harita hücrelerinde sayı bulunamadı.")
    if blank.any():
        eff = _fill_nearest(eff, blank)
        warnings.append(
            f"{int(blank.sum())} boş hücre en yakın ölçülen değerle dolduruldu "
            f"(genellikle tork eğrisinin dışındaki bölge; model orayı zaten kullanmaz).")

    bad = (eff < 0.3) | (eff > 0.995)
    inner = bad & ~blank & (T > 0.1 * torque.max()) & (W > 0.1 * speed_rads.max())
    if inner.sum() > 0.02 * eff.size:
        warnings.append(
            f"{int(inner.sum())} hücrede verim %30'un altında ya da %99,5'in üstünde. "
            f"Değer tipi (kesir / yüzde / kayıp) doğru seçilmiş mi kontrol edin.")
    eff = np.clip(eff, 0.0, 0.995)

    m = Map2D(speed_rads, torque, eff, name=pathlib.Path(table.source_name).stem,
              source=f"import:{table.source_name}",
              x_label="speed_rad_s", y_label="torque_Nm")
    settings = {"orientation": orient, "speed_unit": unit, "value_type": vt}
    detected = {"orientation": det_orient, "speed_unit": det_unit, "value_type": det_vt}
    confidence = {"orientation": conf_orient, "speed_unit": conf_unit,
                  "value_type": conf_vt}
    return MapImport(m, settings, detected, confidence, warnings, blank, layout,
                     table.describe())


def _extract_map_block(table: Table, num: np.ndarray):
    """Find the map inside the sheet: either a grid or three long columns."""
    rows_with_data = np.where(np.sum(np.isfinite(num), axis=1) >= 3)[0]
    if rows_with_data.size < 2:
        raise ValueError("Dosyada en az 3 sayı içeren 2 satır bulunamadı. "
                         "Bu bir harita dosyası mı?")

    # Long format: exactly three dense numeric columns, many rows, and the
    # first two columns repeat values (they are grid coordinates).
    dense_cols = np.where(np.mean(np.isfinite(num[rows_with_data]), axis=0) > 0.9)[0]
    if dense_cols.size == 3 and rows_with_data.size >= 6:
        block = num[np.ix_(rows_with_data, dense_cols)]
        ua = np.unique(block[:, 0]).size
        ub = np.unique(block[:, 1]).size
        if ua < 0.6 * block.shape[0] and ub < 0.6 * block.shape[0]:
            header_row = rows_with_data[0] - 1
            headers = []
            if header_row >= 0:
                headers = [str(table.cells[header_row][c])
                           if c < len(table.cells[header_row]) else ""
                           for c in dense_cols]
            col_order = _assign_long_columns(headers, block)
            s_col, t_col, v_col = col_order
            s_vals = np.unique(block[:, s_col])
            t_vals = np.unique(block[:, t_col])
            grid = np.full((t_vals.size, s_vals.size), np.nan)
            si = np.searchsorted(s_vals, block[:, s_col])
            ti = np.searchsorted(t_vals, block[:, t_col])
            grid[ti, si] = block[:, v_col]
            hint = " ".join(headers)
            # Report as speed across columns; orientation already resolved.
            return "long", s_vals, t_vals, grid, hint + " __speed_cols__"

    # Grid: the header row is the first data row whose cells from column 1 on
    # are numeric; the side axis is column 0 (or the first numeric column).
    top = rows_with_data[0]
    row_vals = num[top]
    first_num_col = int(np.argmax(np.isfinite(row_vals)))
    # Top-left corner may be numeric in some exports (a 0); if the rest of
    # column first_num_col below is numeric and increasing, treat it as the axis.
    side_col = first_num_col - 1 if first_num_col > 0 else 0
    if first_num_col == 0:
        side_col = 0
    axis_a_cols = [c for c in range(side_col + 1, num.shape[1]) if np.isfinite(num[top, c])]
    data_rows = [r for r in range(top + 1, num.shape[0])
                 if np.isfinite(num[r, side_col]) and np.sum(np.isfinite(num[r, axis_a_cols])) > 0]
    if len(axis_a_cols) < 2 or len(data_rows) < 2:
        raise ValueError("Tablo bir ızgara (üst satır bir eksen, sol sütun diğer eksen) "
                         "ya da üç sütunlu uzun biçim olarak okunamadı.")
    axis_a = num[top, axis_a_cols]
    axis_b = num[data_rows, side_col]
    values = num[np.ix_(data_rows, axis_a_cols)]

    hint = _text_of(table, range(0, top + 1), range(0, num.shape[1]))
    hint += " " + _text_of(table, range(top, min(top + 1, num.shape[0])), range(0, side_col + 1))
    corner = table.cells[top][side_col] if side_col < len(table.cells[top]) else None
    corner_text = "" if corner is None or parse_number(corner, table.decimal) is not None else str(corner)
    return "grid", axis_a, axis_b, values, hint + " __corner__ " + corner_text


def _assign_long_columns(headers: list, block: np.ndarray) -> tuple:
    """Which of the three columns is speed, torque and value."""
    roles = {}
    for i, h in enumerate(headers):
        f = _fold(h)
        if "speed" not in roles and _has(SPEED_WORDS, f):
            roles["speed"] = i
        elif "torque" not in roles and _has(TORQUE_WORDS, f):
            roles["torque"] = i
        elif "value" not in roles and (_has(EFF_WORDS, f) or _has(LOSS_WORDS, f)):
            roles["value"] = i
    remaining = [i for i in range(3) if i not in roles.values()]
    for role in ("speed", "torque", "value"):
        if role not in roles and remaining:
            roles[role] = remaining.pop(0)
    return roles["speed"], roles["torque"], roles["value"]


def _guess_orientation(axis_a, axis_b, hint: str, layout: str):
    if "__speed_cols__" in hint:
        return "speed_in_columns", "high"
    corner = _fold(hint.split("__corner__")[-1]) if "__corner__" in hint else ""
    # Convention "rows \ columns": the first-named quantity labels the rows.
    if corner:
        ps = min((corner.find(w) for w in SPEED_WORDS if w in corner), default=-1)
        pt = min((corner.find(w) for w in TORQUE_WORDS if w in corner), default=-1)
        if ps >= 0 and pt >= 0:
            return ("speed_in_columns", "high") if pt < ps else ("speed_in_rows", "high")
    # Speed axis usually has the larger numbers (rpm), and usually runs across
    # the top in supplier sheets.
    a_max, b_max = float(np.nanmax(axis_a)), float(np.nanmax(axis_b))
    if a_max > 1.3 * b_max:
        return "speed_in_columns", "medium"
    if b_max > 1.3 * a_max:
        return "speed_in_rows", "medium"
    return "speed_in_columns", "low"


def _guess_speed_unit(speed: np.ndarray, hint: str):
    h = _fold(hint)
    if any(w in h for w in ("rpm", "1/min", "min-1", "min^-1", "devir", "d/d")):
        return "rpm", "high"
    if "rad" in h:
        return "rad_s", "high"
    vmax = float(np.nanmax(np.abs(speed)))
    if vmax > 2000:
        return "rpm", "high"
    if vmax <= 700:
        return "rad_s", "medium"
    return "rpm", "low"


def _guess_value_type(z: np.ndarray, hint: str):
    h = _fold(hint.split("__corner__")[0] + " " + hint.split("__corner__")[-1])
    finite = z[np.isfinite(z)]
    if finite.size == 0:
        return "efficiency_fraction", "low"
    vmax, med = float(finite.max()), float(np.median(finite))
    if _has(LOSS_WORDS, h):
        return ("loss_w", "high") if med > 200 else ("loss_kw", "high")
    if vmax <= 1.0:
        return "efficiency_fraction", "high"
    if vmax <= 100.0 and med >= 50.0:
        return "efficiency_percent", "high" if ("%" in h or "verim" in h or "eff" in h) else "medium"
    if med > 200:
        return "loss_w", "medium"
    return "loss_kw", "low"


def _merge_duplicate_axes(w, t, z):
    wu, wi = np.unique(w, return_inverse=True)
    tu, ti = np.unique(t, return_inverse=True)
    out = np.full((tu.size, wu.size), np.nan)
    cnt = np.zeros_like(out)
    for a in range(t.size):
        for b in range(w.size):
            v = z[a, b]
            if np.isfinite(v):
                out[ti[a], wi[b]] = np.nansum([out[ti[a], wi[b]], v])
                cnt[ti[a], wi[b]] += 1
    with np.errstate(invalid="ignore"):
        out = np.where(cnt > 0, out / np.maximum(cnt, 1), np.nan)
    return wu, tu, out


def _fill_nearest(z: np.ndarray, blank: np.ndarray) -> np.ndarray:
    """Fill blank cells from the nearest filled cell in index space."""
    from scipy.interpolate import NearestNDInterpolator
    jj, ii = np.nonzero(~blank)
    f = NearestNDInterpolator(np.c_[jj, ii], z[~blank])
    out = z.copy()
    bj, bi = np.nonzero(blank)
    out[bj, bi] = f(np.c_[bj, bi])
    return out


# ---------------------------------------------------------------- 1-D curves
CURVE_KINDS = {
    "ocv_curve": {"x": "soc", "y": "volt"},
    "resistance_soc_factor": {"x": "soc", "y": "factor"},
    "charge_accept_derate": {"x": "temp", "y": "factor"},
    "heat_pump_cop": {"x": "temp", "y": "cop"},
    "cooling_cop": {"x": "temp", "y": "cop"},
}


def import_curve(table: Table, field_name: str, overrides: dict | None = None,
                 context: dict | None = None) -> dict:
    """Two-column curve: detect header, SOC in % or fraction, pack vs cell volts."""
    overrides = overrides or {}
    context = context or {}
    num = numeric_matrix(table)
    rows = np.where(np.sum(np.isfinite(num), axis=1) >= 2)[0]
    if rows.size < 2:
        raise ValueError("En az iki satırda iki sayı bulunmalı (x ve y).")
    cols = np.where(np.mean(np.isfinite(num[rows]), axis=0) > 0.8)[0]
    if cols.size < 2:
        raise ValueError("İki sayısal sütun bulunamadı.")
    x = num[rows, cols[0]]
    y = num[rows, cols[1]]
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    warnings = []
    spec = CURVE_KINDS.get(field_name, {"x": "x", "y": "y"})
    settings = {}

    if spec["x"] == "soc":
        det = "percent" if x.max() > 1.5 else "fraction"
        settings["x_scale"] = overrides.get("x_scale", det)
        if settings["x_scale"] == "percent":
            x = x / 100.0
    if spec["y"] == "volt":
        det = "pack" if np.median(y) > 20 else "cell"
        settings["y_scale"] = overrides.get("y_scale", det)
        if settings["y_scale"] == "pack":
            ns = context.get("cells_series")
            if not ns:
                raise ValueError("Paket gerilimi algılandı. Hücreye çevirmek için "
                                 "seri hücre sayısını girin.")
            y = y / float(ns)
            warnings.append(f"Paket gerilimi {int(ns)} seri hücreye bölünerek hücre "
                            f"gerilimine çevrildi.")
    order = np.argsort(x)
    x, y = x[order], y[order]
    return {"points": [[round(float(a), 5), round(float(b), 5)] for a, b in zip(x, y)],
            "settings": settings, "warnings": warnings, "table": table.describe()}


# ---------------------------------------------------------------- routes
@dataclass
class RouteImport:
    distance_m: np.ndarray
    elevation_m: np.ndarray
    speed_limit_kmh: np.ndarray
    stops_m: list
    warnings: list
    source_format: str

    def to_dict(self) -> dict:
        d = self.distance_m
        step = max(1, d.size // 600)
        climb = float(np.sum(np.clip(np.diff(self.elevation_m), 0, None)))
        return {
            "length_km": round(float(d[-1]) / 1000, 3),
            "stops": len(self.stops_m),
            "climb_m": round(climb),
            "elev_range": [round(float(self.elevation_m.min())),
                           round(float(self.elevation_m.max()))],
            "profile": {"distance_km": [round(float(v) / 1000, 4) for v in d[::step]],
                        "elevation_m": [round(float(v), 1) for v in self.elevation_m[::step]]},
            "stops_km": [round(s / 1000, 3) for s in self.stops_m],
            "warnings": self.warnings,
            "format": self.source_format,
        }

    def write_csv(self, path) -> pathlib.Path:
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        stopset = set(round(s, 1) for s in self.stops_m)
        with path.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["distance_m", "elevation_m", "speed_limit_kmh", "stop"])
            for i in range(self.distance_m.size):
                w.writerow([f"{self.distance_m[i]:.1f}", f"{self.elevation_m[i]:.2f}",
                            f"{self.speed_limit_kmh[i]:.0f}",
                            1 if round(float(self.distance_m[i]), 1) in stopset else 0])
        return path


def import_route(data: bytes, filename: str, *, default_speed_kmh: float = 50.0,
                 stop_spacing_m: float | None = None) -> RouteImport:
    """GPX track (with waypoints as stops) or a table of distance/elevation."""
    name = filename.lower()
    if name.endswith(".gpx") or data.lstrip()[:5] in (b"<?xml", b"<gpx "):
        r = _import_gpx(data, default_speed_kmh)
    else:
        r = _import_route_table(read_table(data, filename), default_speed_kmh)
    if not r.stops_m and stop_spacing_m:
        r.stops_m = [float(s) for s in np.arange(stop_spacing_m, r.distance_m[-1] - 100,
                                                 stop_spacing_m)]
        r.warnings.append(f"Dosyada durak yoktu; her {stop_spacing_m:.0f} m'de bir durak "
                          f"varsayıldı. Gerçek durakları eklemek sonucu iyileştirir.")
    elif not r.stops_m:
        r.warnings.append("Dosyada durak bulunamadı. Durak aralığı girerseniz "
                          "kapı açılması ve kalkış enerjisi hesaba katılır.")
    # Snap stops onto the distance grid so the route CSV can flag them.
    r.stops_m = sorted({float(r.distance_m[np.argmin(np.abs(r.distance_m - s))])
                        for s in r.stops_m})
    return r


def _haversine(lat1, lon1, lat2, lon2):
    R = 6_371_000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def _import_gpx(data: bytes, default_speed_kmh: float) -> RouteImport:
    root = ET.fromstring(data)
    ns = {"g": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
    q = (lambda t: f"g:{t}") if ns else (lambda t: t)
    pts = root.findall(f".//{q('trkpt')}", ns) or root.findall(f".//{q('rtept')}", ns)
    if len(pts) < 2:
        raise ValueError("GPX dosyasında iz noktası (trkpt) bulunamadı.")
    lat = np.array([float(p.get("lat")) for p in pts])
    lon = np.array([float(p.get("lon")) for p in pts])
    ele_nodes = [p.find(q("ele"), ns) for p in pts]
    warnings = []
    if all(e is not None and e.text for e in ele_nodes):
        ele = np.array([float(e.text) for e in ele_nodes])
    else:
        ele = np.zeros(lat.size)
        warnings.append("GPX'te yükseklik (ele) yok: güzergâh düz kabul edildi. "
                        "Eğim enerji tüketimini ciddi etkiler; yükseklik ekli bir iz kullanın.")
    seg = np.r_[0.0, _haversine(lat[:-1], lon[:-1], lat[1:], lon[1:])]
    d = np.cumsum(seg)
    keep = np.r_[True, np.diff(d) > 0.5]
    lat, lon, ele, d = lat[keep], lon[keep], ele[keep], d[keep]
    d, ele = _regrid(d, ele, 10.0)
    stops, ignored = [], []
    along = np.cumsum(np.r_[0.0, _haversine(lat[:-1], lon[:-1], lat[1:], lon[1:])])
    for w in root.findall(f".//{q('wpt')}", ns):
        wl, wn = float(w.get("lat")), float(w.get("lon"))
        dist_to_track = _haversine(wl, wn, lat, lon)
        k = int(np.argmin(dist_to_track))
        if dist_to_track[k] <= 60.0:
            stops.append(float(along[k]))
        else:
            nm = w.find(q("name"), ns)
            ignored.append(f"{nm.text if nm is not None and nm.text else 'adsız'} "
                           f"({dist_to_track[k]:.0f} m)")
    if ignored:
        warnings.append(
            f"{len(ignored)} nokta güzergâha 60 m'den uzak olduğu için durak sayılmadı: "
            + ", ".join(ignored) + ". Durak ise konumunu kontrol edin.")
    return RouteImport(d, ele, np.full(d.size, default_speed_kmh), stops, warnings, "gpx")


def _regrid(d, y, step):
    grid = np.arange(0.0, d[-1] + 1e-9, step)
    return grid, np.interp(grid, d, y)


ROUTE_COLS = {
    "distance": ["distance", "mesafe", "dist", "km", "s_m", "yol"],
    "elevation": ["elevation", "ele", "rakim", "yukseklik", "altitude", "height", "alt"],
    "speed_limit": ["limit", "hiz_limiti", "speed_limit", "vmax", "azami"],
    "stop": ["stop", "durak"],
    "lat": ["lat", "enlem"],
    "lon": ["lon", "lng", "boylam"],
}


def _import_route_table(table: Table, default_speed_kmh: float) -> RouteImport:
    header_i, cols = _find_header(table, ROUTE_COLS)
    num = numeric_matrix(table)[header_i + 1:]
    warnings = []

    def col(role):
        c = cols.get(role)
        return None if c is None else num[:, c]

    if "lat" in cols and "lon" in cols and "distance" not in cols:
        lat, lon = col("lat"), col("lon")
        ok = np.isfinite(lat) & np.isfinite(lon)
        seg = np.r_[0.0, _haversine(lat[ok][:-1], lon[ok][:-1], lat[ok][1:], lon[ok][1:])]
        dist = np.cumsum(seg)
        num = num[ok]
    else:
        dist = col("distance")
        if dist is None:
            raise ValueError("Mesafe sütunu bulunamadı (distance / mesafe) ve "
                             "enlem/boylam da yok.")
        header = _fold(str(table.cells[header_i][cols["distance"]]))
        if "km" in header or np.nanmax(dist) < 200:
            dist = dist * 1000.0
            warnings.append("Mesafe km olarak algılandı ve metreye çevrildi.")
    ele = col("elevation")
    if ele is None:
        ele = np.zeros_like(dist)
        warnings.append("Yükseklik sütunu yok: güzergâh düz kabul edildi.")
    lim = col("speed_limit")
    if lim is None:
        lim = np.full_like(dist, default_speed_kmh)
    stop = col("stop")
    ok = np.isfinite(dist) & np.isfinite(ele)
    dist, ele, lim = dist[ok], ele[ok], np.nan_to_num(lim[ok], nan=default_speed_kmh)
    stops = [] if stop is None else [float(x) for x, s in zip(dist, stop[ok]) if s and s > 0]
    order = np.argsort(dist)
    dist, ele, lim = dist[order], ele[order], lim[order]
    keep = np.r_[True, np.diff(dist) > 0]
    dist, ele, lim = dist[keep], ele[keep], lim[keep]
    grid, ele_g = _regrid(dist - dist[0], ele, 10.0)
    lim_g = np.interp(grid, dist - dist[0], lim)
    stops = [s - dist[0] for s in stops]
    return RouteImport(grid, ele_g, lim_g, stops, warnings, "table")


def _find_header(table: Table, vocab: dict) -> tuple[int, dict]:
    """First row that names at least one known column; returns role->index."""
    for i, row in enumerate(table.cells[:30]):
        found = {}
        for j, cell in enumerate(row):
            if cell is None or parse_number(cell, table.decimal) is not None:
                continue
            for role, words in vocab.items():
                if role in found:
                    continue
                if _header_matches(str(cell), words):
                    found[role] = j
                    break
        if found:
            return i, found
    raise ValueError("Sütun başlıkları tanınamadı. İlk satırda başlıklar olmalı "
                     "(ör. mesafe, rakım / distance, elevation).")


# ---------------------------------------------------------------- vehicle logs
LOG_COLS = {
    "time": ["time", "zaman", "t", "timestamp", "zaman_damgasi", "datetime", "tarih", "saat", "sec", "s"],
    "speed": ["speed", "hiz", "vehicle_speed", "arac_hizi", "velocity", "v", "kmh", "km/h"],
    "power": ["power", "guc", "battery_power", "p_batt", "pbatt", "batarya_gucu", "p", "kw"],
    "voltage": ["voltage", "gerilim", "volt", "u", "v_batt", "hv_voltage", "batarya_gerilimi"],
    "current": ["current", "akim", "i", "i_batt", "hv_current", "batarya_akimi", "amp", "a"],
    "soc": ["soc", "sarj", "state_of_charge", "doluluk"],
    "grade": ["grade", "egim", "slope"],
    "passengers": ["passengers", "yolcu", "pax", "occupancy"],
    "doors": ["door", "doors", "kapi", "doors_open", "kapi_acik"],
    "ambient": ["ambient", "ortam", "dis_sicaklik", "outside_temp", "t_amb", "hava"],
}
LOG_ROLES_TR = {
    "time": "Zaman", "speed": "Hız", "power": "Batarya gücü",
    "voltage": "Batarya gerilimi", "current": "Batarya akımı", "soc": "SOC",
    "grade": "Eğim", "passengers": "Yolcu", "doors": "Kapı durumu",
    "ambient": "Dış sıcaklık",
}


def detect_log_columns(table: Table) -> dict:
    """Guess which column is which. The UI shows the guess as dropdowns."""
    header_i, _ = _find_header(table, {"any": sum(LOG_COLS.values(), [])})
    headers = [("" if c is None else str(c)) for c in table.cells[header_i]]
    mapping: dict = {}
    for role, words in LOG_COLS.items():
        for j, h in enumerate(headers):
            if j in mapping.values():
                continue
            if _header_matches(h, words):
                mapping[role] = j
                break
    return {"header_row": header_i, "headers": headers, "mapping": mapping}


def build_log(table: Table, mapping: dict, header_row: int, *,
              speed_unit: str | None = None, power_sign: str | None = None,
              current_sign: str | None = None) -> tuple:
    """Assemble a MeasuredLog from mapped columns. Returns (log, notes, settings)."""
    from .validate import MeasuredLog

    rows = table.cells[header_row + 1:]
    headers = table.cells[header_row]
    notes: list = []
    settings: dict = {}

    def raw(role):
        j = mapping.get(role)
        if j is None or j == "":
            return None
        j = int(j)
        return [r[j] if j < len(r) else None for r in rows]

    def numeric(role):
        vals = raw(role)
        if vals is None:
            return None
        return np.array([np.nan if (x := parse_number(v, table.decimal)) is None else x
                         for v in vals])

    if "time" not in mapping or "speed" not in mapping:
        raise ValueError("Zaman ve hız sütunları seçilmeli.")

    t = numeric("time")
    if np.mean(np.isfinite(t)) < 0.5:
        t = _parse_datetimes(raw("time"))
        notes.append("Zaman sütunu tarih/saat olarak okundu ve saniyeye çevrildi.")
    t = t - np.nanmin(t)

    v = numeric("speed")
    h = _fold(str(headers[int(mapping["speed"])]))
    det_unit = ("kmh" if ("km" in h or np.nanmax(v) > 40) else "mps")
    settings["speed_unit"] = speed_unit or det_unit
    if settings["speed_unit"] == "kmh":
        v = v / 3.6

    p = numeric("power")
    if p is not None:
        ph = _fold(str(headers[int(mapping["power"])]))
        if "kw" in ph or np.nanmax(np.abs(p)) < 1000:
            p = p * 1e3
    elif "voltage" in mapping and "current" in mapping:
        p = numeric("voltage") * numeric("current")
        notes.append("Batarya gücü gerilim × akım olarak hesaplandı.")
    if p is not None:
        # Sign: at standstill the pack only feeds auxiliaries, so power there
        # must be positive (discharge). If it is mostly negative, the logger
        # uses the opposite convention.
        still = np.isfinite(v) & (v < 0.3) & np.isfinite(p)
        det_sign = "discharge_positive"
        if still.sum() > 5 and np.median(p[still]) < 0:
            det_sign = "discharge_negative"
        settings["power_sign"] = power_sign or det_sign
        if settings["power_sign"] == "discharge_negative":
            p = -p
            notes.append("Güç işareti ters çevrildi (logda deşarj negatif).")

    soc = numeric("soc")
    if soc is not None and np.nanmax(soc) <= 1.5:
        soc = soc * 100.0
    grade = numeric("grade")
    pax = numeric("passengers")
    doors = numeric("doors")
    amb = numeric("ambient")

    ok = np.isfinite(t) & np.isfinite(v)
    order = np.argsort(t[ok])

    def sel(a):
        return None if a is None else np.nan_to_num(a[ok][order], nan=0.0)

    tt = t[ok][order]
    keep = np.r_[True, np.diff(tt) > 0]

    def k(a):
        return None if a is None else a[keep]

    if p is None and soc is None:
        raise ValueError("Doğrulama için batarya gücü, gerilim+akım ya da SOC sütunu gerekli.")

    log = MeasuredLog(
        time_s=k(tt), speed_mps=k(sel(v)),
        battery_power_w=k(sel(p)),
        soc=None if soc is None else k(sel(soc)) / 100.0,
        grade_rad=None if grade is None else np.arctan(k(sel(grade)) / 100.0),
        passengers=k(sel(pax)),
        doors_open=None if doors is None else k(sel(doors)) > 0.5,
        ambient_c=None if amb is None else float(np.nanmean(amb)),
        name=pathlib.Path(table.source_name).stem,
        source=f"import:{table.source_name}",
    )
    return log, notes, settings


def _parse_datetimes(values) -> np.ndarray:
    import pandas as pd
    s = pd.to_datetime(pd.Series(values), errors="coerce", dayfirst=True)
    if s.notna().mean() < 0.5:
        raise ValueError("Zaman sütunu ne sayı ne de tarih/saat olarak okunabildi.")
    base = s.dropna().iloc[0]
    return (s - base).dt.total_seconds().to_numpy(dtype=float)
