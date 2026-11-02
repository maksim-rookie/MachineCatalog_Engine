"""CNC PDF_SEARCH v8.4.2 LOCAL: conservative mass-unit repair and model-field status audit.

Python 3.11+; intended for PyCharm. Source catalogues go to ./pdf.

NO NETWORK / NO GROQ: this version is deliberately local-only.
OCR_LANG=eng+rus (installed traineddata required).
LOCAL_CELL_OCR=1, LOCAL_CELL_OCR_MAX=24, LOCAL_CELL_OCR_DPI=340.
Missing or suspicious measurements are reported rather than guessed.

All extracted facts are UNVERIFIED until human review. No guessed missing cells.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import time
import threading
from io import BytesIO
from collections import defaultdict
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent
PROJECT_VERSION = "8.4.2"
VERSION_ID = "v" + PROJECT_VERSION.replace(".", "_")
INPUT = BASE / "pdf"
# Each invocation has its own dated folder INSIDE this version of the project.
# An old output run remains untouched when a new catalogue is processed.
OUTPUT_BASE = BASE / "output" / VERSION_ID


def new_run_directory(output_root: Path, stamp: str | None = None) -> Path:
    """Choose an unused output path; does not create it until main() starts."""
    if stamp is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    base = output_root / f"run_{stamp}"
    candidate = base
    number = 2
    while candidate.exists():
        candidate = output_root / f"{base.name}_{number}"
        number += 1
    return candidate


OUTPUT = new_run_directory(OUTPUT_BASE)


class RunProgress:
    """Real work-unit progress: PDF pages, parsed groups and completed stages.

    Overall percentages are weighted *work*, NOT the predicted share of elapsed
    time. OCR-heavy pages can be considerably slower than others.
    """
    def __init__(self, directory: Path, files_total: int):
        self.directory = directory
        self.files_total = max(1, files_total)
        self.start_perf = time.perf_counter()
        self.started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self.progress_path = directory / "RUN_PROGRESS.jsonl"
        self.last_percent = 0.0
        self.last_print_percent = -99.0
        self.last_print_time = self.start_perf
        self.last_print_stage = ""
        self.file_timings = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._heartbeat, daemon=True)
        self.update(0, "Запуск", force=True)
        self._thread.start()

    def _heartbeat(self):
        """Display current elapsed time even when a single OCR cell is slow."""
        while not self._stop.wait(10):
            if time.perf_counter() - self.last_print_time >= 9:
                elapsed = time.perf_counter() - self.start_perf
                print(f"  ... {self.last_percent:.1f}% | {self.last_print_stage} "
                      f"| прошло {self.format_duration(elapsed)} (обработка продолжается)",
                      flush=True)
                self.last_print_time = time.perf_counter()

    @staticmethod
    def format_duration(seconds: float) -> str:
        seconds = max(0, int(seconds))
        hours, rest = divmod(seconds, 3600)
        minutes, seconds = divmod(rest, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def update(self, percent: float, stage: str, *, filename: str = "",
               detail: str = "", force: bool = False):
        percent = min(100.0, max(self.last_percent, float(percent)))
        self.last_percent = percent
        elapsed = max(0.0, time.perf_counter() - self.start_perf)
        record = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "progress_percent": round(percent, 2),
            "elapsed_seconds": round(elapsed, 3),
            "stage": stage,
            "source_file": filename,
            "detail": detail,
        }
        with self.progress_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        # Limit console chatter, but always display stage transitions, completion,
        # and any stage taking longer than five seconds without a new percent.
        show = (force or stage != self.last_print_stage or percent - self.last_print_percent >= 2.0
                or time.perf_counter() - self.last_print_time >= 5.0 or percent >= 100.0)
        if show:
            filled = min(20, int(percent // 5))
            bar = "#" * filled + "-" * (20 - filled)
            current = f" | {filename}" if filename else ""
            suffix = f" | {detail}" if detail else ""
            print(f"[{bar}] {percent:5.1f}% | {stage}{current}{suffix}"
                  f" | прошло {self.format_duration(elapsed)}", flush=True)
            self.last_print_stage = stage
            self.last_print_percent = percent
            self.last_print_time = time.perf_counter()

    def file_work(self, index: int, fraction: float, stage: str, *, filename: str = "",
                  detail: str = "", force: bool = False):
        # 88% for reading and extraction, spread evenly across source files;
        # 4% for validation, 8% for exports/manifest.
        fraction = min(1.0, max(0.0, fraction))
        subdetail = f"файл {fraction * 100:.0f}%"
        if detail:
            subdetail += f", {detail}"
        self.update(88.0 * (index + fraction) / self.files_total, stage,
                    filename=filename, detail=subdetail, force=force)

    def finish(self, *, errors: bool = False) -> dict:
        self._stop.set()
        self._thread.join(timeout=1)
        self.update(100.0, "Завершено с ошибками" if errors else "Готово", force=True)
        timing = {
            "project_version": PROJECT_VERSION,
            "run_id": self.directory.name,
            "started_at": self.started_at,
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "elapsed_seconds": round(time.perf_counter() - self.start_perf, 3),
            "duration_hms": self.format_duration(time.perf_counter() - self.start_perf),
            "progress_percent": 100.0,
            "progress_kind": "weighted_work_units_not_elapsed_time",
            "completed_with_errors": bool(errors),
            "files_total": self.files_total,
            "file_timings": self.file_timings,
        }
        save_json(self.directory / "RUN_TIMING.json", timing)
        return timing
# Cache is shared across runs of the same extraction version, not duplicated
# into each CSV/report folder. Legacy cache location remains compatible.
CACHE = BASE / "output" / "cache_v8_4_2"
MODE = "local"
OCR_LANG = os.getenv("OCR_LANG", "eng+rus")
LOCAL_CELL_OCR = os.getenv("LOCAL_CELL_OCR", "1").strip().lower() not in {"0", "false", "no"}
LOCAL_CELL_OCR_MAX = max(0, int(os.getenv("LOCAL_CELL_OCR_MAX", "24")))
LOCAL_CELL_OCR_DPI = max(240, min(500, int(os.getenv("LOCAL_CELL_OCR_DPI", "340"))))
EXTRACTION_VERSION = "v8_4_2_local_mynx_source_profile_v4"
SUPPORTED = {".pdf", ".docx", ".txt", ".md", ".xlsx", ".xls"}

ORDER = ["Travel", "Table", "Spindle", "Feedrate", "Automatic tool changer (ATC)", "Control", "Motor", "Power source", "Tank capacity", "Machine dimensions", "Other"]
BASE_COLUMNS = ["manufacturer", "machine_type", "model"]
# Requested narrow schema for ALL_MODELS_wide.csv ONLY. These facts remain in
# ALL_MODELS_long.csv, MODEL_FIELD_STATUS.csv and per-series CSV exports.
HIDDEN_WIDE_COLUMNS = frozenset({
    "[Table] table_work_inertia_nm2",
    "[Spindle] spindle_taper",
    "[Feedrate] axis_acceleration_xyz_g",
    "[Automatic tool changer (ATC)] tool_max_moment_nm",
    "[Automatic tool changer (ATC)] tool_pull_stud",
    "[Automatic tool changer (ATC)] tool_selection_type",
    "[Motor] coolant_pump_motor_power_kw",
    "[Power source] air_pressure_mpa",
})
MODEL_PREFIX_EXCLUDE = {"PS", "ISO", "DIN", "HSK", "BT", "CAT", "TNC", "FANUC", "RPM", "KW", "KVA", "ATC", "HP", "S", "G", "K", "V", "M", "N", "TABLE", "SPINDLE", "TOOL", "MAX", "MIN", "SYS", "IEC", "IP", "AIR", "VOL"}
SERIES_INFO = {
    "NHP": ("DN Solutions", "Horizontal Machining Center"),
    "NHM": ("DN Solutions", "Horizontal Machining Center"),
    "DNM": ("DN Solutions", "Vertical Machining Center"),
    "SVM": ("DN Solutions", "Vertical Machining Center"),
    "VM": ("DN Solutions", "Vertical Machining Center"),
    "DVF": ("DN Solutions", "5-Axis Machining Center"),
    "PUMA": ("DN Solutions", "Turning Center"),
    "SMX": ("DN Solutions", "Turn-Mill Center"),
    "LYNX": ("DN Solutions", "Turning Center"),
    "MYNX": ("DN Solutions", "Vertical Machining Center"),
    "GRA": ("Jingdiao", "Machining Center"),
    "MRA": ("Jingdiao", "Machining Center"),
    "HGA": ("Jingdiao", "Machining Center"),
    "PGA": ("Jingdiao", "Machining Center"),
    "JDGRMG": ("Jingdiao", "5-Axis Machining Center"),
}
MODEL_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Z]{2,12})\s*[- ]?\s*"
    r"(\d{3,5}(?:\s*/\s*\d{2,3})?(?:\s*(?:II|Ⅱ|ⅱ))?[A-Z]{0,2}\d{0,2})"
    r"(?=$|[^A-Za-z0-9])", re.I)
NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d{1,7}(?:[.,]\d{1,5})?(?![A-Za-z])")
BAD_RE = re.compile(r"\(cid:\d+\)|[\ufffd\x00-\x08\x0b\x0e-\x1f]", re.I)
UNIT_ONLY_RE = re.compile(r"^(?:[-–—]|\(?\s*(?:mm|inch|in|kg|lb|kW|Hp|rpm|r/min|ea|pcs|l|L|gal|N[.·*]?m|m/min|NL/min|l/min|m3/h|ipm|s|sec|deg|°|mm/min|mm\s*\(inch\)|kg\s*\(lb\)|kW\s*\(Hp\)|N[·.]?m\s*\([^)]*\))\s*\)?)+$", re.I)
OCR_UNIT_RE = re.compile(
    r"^(?:(?:mm?|m)\s*[({]?\s*inch\s*[)}]?|(?:m/min|mm/min)\s*[({]?\s*ipm\s*[)}]?|"
    r"(?:kg)\s*[({]?\s*(?:lb|ib)\s*[)}]?|(?:r/min|rpm|deg|\-|G|N[·.-]?m2?|kW|Hp|ea|sec))$",
    re.I)
TECHNICAL_UNITS = re.compile(r"\b(?:mm|rpm|r/min|kg|kW|Nm|N·m|m/min|mm/min|ipm|kVA|Hp|ea|pcs|sec|inch|gal|L)\b|°|\bdeg\b", re.I)
TECHNICAL_WORDS = re.compile(r"(?:travel|stroke|axis|table|pallet|spindle|feed|rapid|traverse|torque|power|motor|tool|magazine|atc|coolant|machine|weight|dimension|height|width|length|accuracy|repeat|control|cnc|nc system|diameter|capacity|specification|taper|chuck|cutting|voltage|air pressure|перемещ|шпиндел|подач|инструмент|станок|мощност|стол|чпу|行程|主轴|工作台|进给|刀库|重量|数控)", re.I)


def norm(value) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def save_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def candidate_models(text: str, allowed_prefix: str = "") -> list[str]:
    found = []
    for prefix, number in MODEL_RE.findall(text or ""):
        prefix = prefix.upper()
        if prefix in MODEL_PREFIX_EXCLUDE or (allowed_prefix and prefix != allowed_prefix):
            continue
        number = re.sub(r"\s*/\s*", "/", number.upper().replace("Ⅱ", "II").replace("ⅱ", "II"))
        number = re.sub(r"(?<=\d)\s+II$", "II", number)
        name = f"{prefix} {number}"
        if name not in found:
            found.append(name)
    return found


def filename_models(stem: str) -> list[str]:
    # NHP_4000_5000.pdf -> two candidates, but not necessarily both have specs.
    candidate = re.search(r"(?<![A-Za-z0-9])([A-Za-z]{2,12})[_ -]+(\d{3,5}(?:[_ -]+\d{3,5})+)(?!\d)", stem)
    if candidate and candidate.group(1).upper() not in MODEL_PREFIX_EXCLUDE:
        prefix = candidate.group(1).upper()
        return list(dict.fromkeys(f"{prefix} {number}" for number in re.findall(r"\d{3,5}", candidate.group(2))))
    return candidate_models(stem.replace("_", " ").replace("-", " "))


def preferred_prefix(path: Path) -> str:
    # Handles SVM-series.pdf and ENG_DVF 5000_...pdf.
    stem = path.stem.upper()
    for prefix in sorted(SERIES_INFO, key=len, reverse=True):
        if re.search(r"(?<![A-Z])" + re.escape(prefix) + r"(?![A-Z])", stem):
            return prefix
    detected = filename_models(path.stem)
    return detected[0].split()[0] if detected else ""


def model_info(name: str):
    match = re.match(r"^([A-Z]+)", name.upper())
    return SERIES_INFO.get(match.group(1), ("Unknown", "Unknown")) if match else ("Unknown", "Unknown")


def damaged_text(text: str) -> bool:
    trimmed = (text or "").strip()
    if len(trimmed) < 35:
        return True
    return len(BAD_RE.findall(trimmed)) / max(1, len(trimmed)) > .008


def ocr_table_rows(image, page_number: int, lang: str) -> list[dict]:
    """Reconstruct a conservative column grid from OCR word coordinates.

    Used only when the native PDF text is unusable. Requires a visible
    four-column specification layout (description, units, two values).
    The grid is a *candidate*, not a verification of model attribution.
    """
    import pytesseract
    result = pytesseract.image_to_data(image, lang=lang,
                                      output_type=pytesseract.Output.DICT)
    width, _ = image.size
    words = []
    for i, raw in enumerate(result["text"]):
        token = norm(raw)
        if not token or float(result["conf"][i]) < 18:
            continue
        x = int(result["left"][i])
        y = int(result["top"][i])
        words.append((y, x, int(result["width"][i]), int(result["height"][i]), token))
    words.sort()
    if not words:
        return []
    # Distinct y-bands are table rows; allow small OCR baseline jitter.
    lines = []
    for word in words:
        if lines and abs(word[0] - lines[-1]["y"]) <= 12:
            lines[-1]["items"].append(word)
        else:
            lines.append({"y": word[0], "items": [word]})
    rows = []
    technical_count = 0
    for i, line in enumerate(lines):
        buckets = [[], [], [], [], []]
        for _, x, _, _, t in sorted(line["items"], key=lambda item: item[1]):
            # Approximate grid boundaries: descriptions / units / model1 / model2.
            # Require at least two value columns before accepting an OCR grid.
            col = (0 if x < width*.17 else 1 if x < width*.465
                   else 2 if x < width*.565 else 3 if x < width*.735 else 4)
            buckets[col].append(t)
        cells = [" ".join(part) for part in buckets]
        if (cells[1] and (cells[3] or cells[4])
                and (TECHNICAL_WORDS.search(cells[1]) or NUMBER_RE.search(cells[3] + " " + cells[4]))):
            technical_count += 1
        # Approximate crop boxes from the OCR grid. Unlike native PDF cell
        # geometry, boundaries here are inferred, and accepted OCR recoveries
        # are always explicitly marked for review.
        image_height = image.size[1]
        left = [0, .17, .465, .565, .735, 1.0]
        top = max(0, min(w[0] for w in line["items"]) - 3)
        bottom = min(image_height, max(w[0] + w[3] for w in line["items"]) + 3)
        # Source page is rasterized at 220 dpi (not LOCAL_CELL_OCR_DPI).
        scale_x = 72.0 / 220.0
        scale_y = 72.0 / 220.0
        guessed_boxes = [[round(width * left[c] * scale_x, 2), round(top * scale_y, 2),
                          round(width * left[c + 1] * scale_x, 2), round(bottom * scale_y, 2)]
                         for c in range(5)]
        rows.append({"page": page_number, "table": 1, "row": i,
                     "kind": "table", "cells": cells, "cell_boxes": guessed_boxes,
                     "ocr_geometry": True, "bbox_inferred": True})
    if technical_count < 5:
        return []
    return rows


class LocalCellOCR:
    """Targeted second pass for a *specific* PDF cell, never a cloud request.

    Native cell boxes come from pdfplumber. OCR grid boxes are estimates and
    must remain review candidates. A correction is admitted only when PSM 6
    and PSM 7 independently yield the same legible value. This is a conservative
    agreement test, not proof that the original catalog is correct.
    """
    def __init__(self, path: Path):
        self.path = path
        self.max_calls = LOCAL_CELL_OCR_MAX
        self.calls = 0
        self.events = []
        self._pdf = None
        self._cache = {}
        if LOCAL_CELL_OCR and self.max_calls and path.suffix.lower() == ".pdf" and path.exists():
            try:
                import pymupdf as fitz
                self._fitz = fitz
                self._pdf = fitz.open(str(path))
            except Exception:
                self._fitz = None
                # pdfplumber fallback is available for PDF text processing.

    def close(self):
        if self._pdf is not None:
            self._pdf.close()
            self._pdf = None

    def _crop(self, page_index, bbox):
        x0, y0, x1, y1 = map(float, bbox)
        if x1 <= x0 + 1 or y1 <= y0 + 1:
            return None
        # Crop the cell only; preserving adjacent rows/columns is essential.
        if self._pdf is not None:
            page = self._pdf[page_index]
            clip = self._fitz.Rect(x0, y0, x1, y1) & page.rect
            if clip.is_empty or clip.width < 2 or clip.height < 2:
                return None
            pix = page.get_pixmap(matrix=self._fitz.Matrix(LOCAL_CELL_OCR_DPI / 72.0,
                                                         LOCAL_CELL_OCR_DPI / 72.0),
                                     clip=clip, alpha=False)
            from PIL import Image
            return Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")
        import pdfplumber
        with pdfplumber.open(self.path) as doc:
            page = doc.pages[page_index]
            crop = page.crop((max(0, x0), max(0, y0), min(page.width, x1), min(page.height, y1)))
            return crop.to_image(resolution=LOCAL_CELL_OCR_DPI).original

    def recheck(self, row: dict, col: int, key: str, current: str, unit: str):
        """Return (new_text | None, event). Never override with a lone OCR guess."""
        boxes = row.get("cell_boxes") or []
        if col >= len(boxes) or not boxes[col]:
            event = {"status": "no_cell_geometry", "parameter": key,
                     "source_page": row.get("page"),
                     "source_row_id": f"{row.get('page')}:{row.get('table')}:{row.get('row')}",
                     "column": col, "original": norm(current)}
            self.events.append(event)
            return None, event
        cache_key = (row["page"], tuple(round(float(x), 2) for x in boxes[col]), key)
        if cache_key in self._cache:
            return self._cache[cache_key]
        event = {"source_page": row["page"], "source_row_id": f"{row['page']}:{row['table']}:{row['row']}",
                 "column": col, "parameter": key, "original": norm(current),
                 "bbox": boxes[col], "bbox_inferred": bool(row.get("bbox_inferred"))}
        if self.calls >= self.max_calls:
            event["status"] = "ocr_budget_exhausted"
            self.events.append(event)
            return None, event
        self.calls += 1
        try:
            import pytesseract
            from PIL import ImageOps, ImageEnhance
            cropped = self._crop(row["page"] - 1, boxes[col])
            if cropped is None:
                raise ValueError("empty cell crop")
            gray = ImageOps.autocontrast(cropped.convert("L"))
            gray = ImageEnhance.Contrast(gray).enhance(1.4)
            texts = []
            for psm in (6, 7):
                txt = norm(pytesseract.image_to_string(gray, lang=OCR_LANG,
                                                       config=f"--psm {psm}", timeout=9))
                texts.append(txt)
            event["candidates"] = texts
            if not texts[0] or texts[0] != texts[1]:
                event["status"] = "ocr_no_consensus"
                out = (None, event)
            elif norm(current) == texts[0]:
                event["status"] = "ocr_unchanged"
                out = (None, event)
            elif invalid_unit(key, unit, texts[0]):
                event["status"] = "ocr_candidate_invalid"
                out = (None, event)
            elif row.get("bbox_inferred") and (not norm(current) or
                    re.match(r"^\s*[({/]|^\s*\d+\s*\(.*\)",norm(current))):
                # OCR grid buckets are NOT real PDF cells. A leading imperial
                # fragment like '(122.1)' or an empty cell cannot independently
                # establish a metric value for the adjacent model.
                event["status"] = "ocr_candidate_weak_column_evidence"
                out = (None, event)
            else:
                event["status"] = "ocr_consensus_candidate"
                event["candidate"] = texts[0]
                out = (texts[0], event)
        except Exception as exc:
            event["status"] = "ocr_unavailable"
            event["detail"] = str(exc)[:180]
            out = (None, event)
        self._cache[cache_key] = out
        self.events.append(event)
        return out


def rectify_damaged_native_table(page, native_table, table_index: int, page_number: int):
    """Repair a comparative specification table using native word geometry.

    Used only when a PDF table detector *provably* loses an entire model
    column despite text objects being present there. Row boundaries come from
    horizontal rules; vertical rules define the actual merged cell spans.
    Nothing is inferred from adjacent model values without a PDF merge span.
    """
    rows = native_table.extract() or []
    if not rows or len(rows[0]) < 7:
        return None
    header = rows[0]
    model_cols = [i for i, cell in enumerate(header)
                  if len(candidate_models(norm(cell))) == 1]
    if len(model_cols) < 5:
        return None
    last = model_cols[-1]
    head_boxes = native_table.rows[0].cells
    if last >= len(head_boxes) or not head_boxes[last]:
        return None
    last_box = head_boxes[last]
    # Verify that missing data actually exists in the last PDF column.
    words = page.extract_words(x_tolerance=1.8, y_tolerance=2.8)
    text_on_page = [w for w in words if
                    last_box[0] + .5 < (w['x0'] + w['x1'])/2 < last_box[2] - .5 and
                    native_table.bbox[1] + 23 < (w['top'] + w['bottom'])/2 < native_table.bbox[3] - 5 and
                    re.search(r"\d", w['text'])]
    text_in_grid = sum(bool(re.search(r"\d", norm(row[last]))) for row in rows[1:]
                       if len(row) > last)
    if len(text_on_page) < 10 or text_in_grid >= len(text_on_page) * .5:
        return None

    x0, y0, x1, y1 = native_table.bbox
    xs = sorted({round(v, 2) for r in native_table.rows for box in r.cells if box
                 for v in (box[0], box[2]) if x0 - 1 <= v <= x1 + 1})
    # Minimum four label/unit columns plus every model column.
    if len(xs) != len(header) + 1:
        return None
    h_lines = [l for l in page.lines if l['width'] > 30 and l['height'] < .45 and
               x0 - 1 <= l['x0'] <= x1 and y0 - 1 <= l['top'] <= y1 + 1]
    ys = sorted({round(y0, 2), round(y1, 2)} |
                {round(l['top'], 2) for l in h_lines})
    if len(ys) < 20:
        return None
    v_lines = [l for l in page.lines if l['height'] > 4 and l['width'] < .45]
    restored = []
    for rowid, (ya, yb) in enumerate(zip(ys, ys[1:])):
        if yb - ya < 3:
            continue
        cells = [''] * (len(xs)-1)
        bboxes = [[xs[i], ya, xs[i+1], yb] for i in range(len(cells))]
        in_row = [[] for _ in cells]
        for w in words:
            cy = (w['top'] + w['bottom']) / 2
            cx = (w['x0'] + w['x1']) / 2
            if not (ya + .25 < cy < yb - .25) or not (x0 <= cx <= x1):
                continue
            j = next((j for j in range(len(cells)) if xs[j] - .5 <= cx < xs[j+1] + .5), None)
            if j is not None:
                in_row[j].append(w)
        for j, ww in enumerate(in_row):
            prev_top = None
            txt = ''
            for w in sorted(ww, key=lambda q:(round(q['top'],1),q['x0'])):
                if prev_top is not None and abs(prev_top - w['top']) >= 3.7:
                    txt += '\n'
                elif txt:
                    txt += ' '
                txt += w['text']
                prev_top = w['top']
            cells[j] = norm(txt)
        # A vertically continuous line at a boundary means DISTINCT cells;
        # missing line means the source truly merges neighbouring cells.
        # Only merge value columns, leaving label hierarchy independent.
        i = model_cols[0]
        while i < len(cells):
            end = i
            while end+1 < len(cells):
                edge = xs[end+1]
                divider = any(abs(l['x0'] - edge) <= .7 and
                              l['top'] <= ya + 1.0 and l['bottom'] >= yb - 1.0
                              for l in v_lines)
                if divider:
                    break
                end += 1
            if end > i:
                joined = norm(' '.join(x for x in cells[i:end+1] if x))
                cells[i] = joined
                bboxes[i] = [xs[i], ya, xs[end+1], yb]
                for k in range(i+1,end+1):
                    cells[k] = ''
                    bboxes[k] = None
            i = end+1
        if any(cells):
            restored.append({'page': page_number, 'table': table_index,
                             'row': rowid, 'kind': 'table', 'cells': cells,
                             'cell_boxes': bboxes, 'ocr_geometry': False,
                             'geometry_repaired': True})
    # The repaired header must still contain all original models.
    if not restored or len(table_headers(restored, '')) < len(model_cols):
        return None
    return restored


def source_rows(path: Path, progress=None) -> list[dict]:
    """Keep every text line and table row, even when some PDF cells are empty."""
    result = []
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            total_pages = len(pdf.pages)
            for page_number, page in enumerate(pdf.pages, 1):
                if progress is not None:
                    progress(page_number - 1, total_pages, f"страница {page_number}/{total_pages}")
                raw = page.extract_text() or ""
                bad = damaged_text(raw)
                if bad:
                    print(f"  page {page_number}: damaged/empty text layer; trying local OCR")
                    try:
                        import pytesseract
                        from PIL import Image
                        # pdfplumber.to_image uses PDF rendering, no external Poppler.
                        image = page.to_image(resolution=220).original
                        raw = pytesseract.image_to_string(image, lang=OCR_LANG)
                        geometry = ocr_table_rows(image, page_number, OCR_LANG)
                        if geometry:
                            print(f"  page {page_number}: OCR grid rows: {len(geometry)}")
                            result.extend(geometry)
                        if not raw.strip():
                            print(f"  page {page_number}: OCR returned no text")
                    except Exception as exc:
                        print(f"  page {page_number}: OCR unavailable: {exc}")
                        raw = ""
                if not bad:
                    try:
                        native_tables = page.find_tables()
                    except Exception as exc:
                        print(f"  page {page_number}: table extraction warning: {exc}")
                        native_tables = []
                    for table_idx, table in enumerate(native_tables, 1):
                        geometric_rows = rectify_damaged_native_table(page, table, table_idx, page_number)
                        if geometric_rows:
                            print(f"  page {page_number}, table {table_idx}: repaired "
                                  f"{len(geometric_rows)} geometry rows; original detector lost model columns")
                            result.extend(geometric_rows)
                            continue
                        extracted = table.extract() or []
                        for i, cells in enumerate(extracted):
                            # pdfplumber represents colspan as one wide bbox
                            # followed by None in the covered columns.
                            row_obj = table.rows[i] if i < len(table.rows) else None
                            boxes = list(row_obj.cells) if row_obj else []
                            boxes = [list(box) if box else None for box in boxes]
                            if len(boxes) != len(cells):
                                boxes = []  # Do not trust misaligned geometry.
                            result.append({"page": page_number, "table": table_idx, "row": i,
                                           "kind": "table", "cells": [norm(c) for c in (cells or [])],
                                           "cell_boxes": boxes, "ocr_geometry": False})
                for idx, line in enumerate(raw.splitlines()):
                    if norm(line):
                        result.append({"page": page_number, "table": 0, "row": idx,
                                       "kind": "text", "cells": [norm(line)],
                                       "ocr_text": bad})
                if progress is not None:
                    progress(page_number, total_pages, f"страниц {page_number}/{total_pages}")
    elif suffix in (".txt", ".md"):
        for i, line in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines()):
            if norm(line):
                result.append({"page": 1, "table": 0, "row": i, "kind": "text", "cells": [norm(line)]})
    elif suffix == ".docx":
        from docx import Document
        doc = Document(path)
        for i, para in enumerate(doc.paragraphs):
            if norm(para.text):
                result.append({"page": 1, "table": 0, "row": i, "kind": "text", "cells": [norm(para.text)]})
        for t, table in enumerate(doc.tables, 1):
            for i, row in enumerate(table.rows):
                result.append({"page": 1, "table": t, "row": i, "kind": "table",
                               "cells": [norm(cell.text) for cell in row.cells]})
    elif suffix in (".xlsx", ".xls"):
        import pandas as pd
        with pd.ExcelFile(path) as workbook:
            for i, sheet_name in enumerate(workbook.sheet_names, 1):
                df = pd.read_excel(workbook, sheet_name=sheet_name, header=None, dtype=str).fillna("")
                for n, cells in enumerate(df.itertuples(index=False, name=None)):
                    result.append({"page": i, "table": 1, "row": n, "sheet": sheet_name,
                                   "kind": "table", "cells": [norm(c) for c in cells]})
    if suffix != ".pdf" and progress is not None:
        progress(1, 1, "документ прочитан")
    return result


# Keys, regex, canonical units. Put specific patterns before generic ones.
PARAMS = [
    ("travel_x_mm", r"(?:\b(?:x)[- ]?(?:axis|travel)\b.*(?:travel|stroke|ход|перемещ|行程)|(?:travel|stroke|ход|перемещ|行程).*(?:\bx[- ]?axis\b|\bx\b|x轴))", "mm"),
    ("travel_y_mm", r"(?:\b(?:y)[- ]?(?:axis|travel)\b.*(?:travel|stroke|ход|перемещ|行程)|(?:travel|stroke|ход|перемещ|行程).*(?:\by[- ]?axis\b|\by\b|y轴))", "mm"),
    ("travel_z_mm", r"(?:\b(?:z)[- ]?(?:axis|travel)\b.*(?:travel|stroke|ход|перемещ|行程)|(?:travel|stroke|ход|перемещ|行程).*(?:\bz[- ]?axis\b|\bz\b|z轴))", "mm"),
    ("travel_b_deg", r"(?:travel|stroke|ход|перемещ|行程).*\b(?:b(?:[- ]?axis)?)\b|\bb[- ]?axis\b.*(?:travel|range|stroke)", "deg"),
    ("travel_c_deg", r"(?:travel|stroke|ход|перемещ|行程).*\b(?:c(?:[- ]?axis)?)\b|\bc[- ]?axis\b.*(?:travel|range|stroke)", "deg"),
    ("axis_travel_xyz_mm", r"(?:travel|stroke|ход|行程).*(?:x\s*[/,]\s*y\s*[/,]\s*z|xyz)", "mm"),
    ("positioning_accuracy", r"positioning\s+accuracy|точност.{0,8}позиционир|定位精度", ""),
    ("repeatability", r"repeatability|repeat\s+position|повторяемост|重复定位", ""),
    ("table_load_kg", r"(?:table|pallet|worktable|стол|工作台).*(?:load|loading|capacity|нагруз|承重)|(?:load|loading).*table", "kg"),
    ("table_size_mm", r"(?:table|worktable|стол|工作台).*(?:size|dimensions?|размер|尺寸)", "mm"),
    ("max_workpiece_size_mm", r"(?:workpiece|заготовк|обрабатываем.{0,10}деталь).*(?:size|diameter|размер)", "mm"),
    ("pallet_size_mm", r"(?:pallet|паллет|托盘).*(?:size|dimensions?|размер|尺寸)", "mm"),
    ("pallet_change_time_sec", r"(?:pallet|паллет).*(?:change|смен).*(?:time|время)", "sec"),
    ("table_index_deg", r"(?:table|pallet).*(?:index|rotation).*(?:angle|accuracy|deg)", "deg"),
    ("table_t_slot", r"(?:table|стол).*(?:t[- ]?slot|t[- ]?паз|паз)|(?:table\s+surface\s+type)", ""),
    ("coolant_pump_motor_power_kw", r"(?:coolant|cooling|сож|охлажд).*?(?:pump|насос).*?(?:motor|двигател).*?(?:power|мощност)|(?:coolant\s+pump\s+motor\s+power)", "kW"),
    ("spindle_torque_nm", r"(?:spindle|шпиндел|主轴).*(?:torque|момент|扭矩)|(?:max\.?\s*)?spindle\s*torque", "N*m"),
    ("spindle_power_kw", r"(?:spindle|шпиндел|主轴).*(?:power|мощност|功率)", "kW"),
    ("spindle_speed_rpm", r"(?:spindle|шпиндел|主轴).*(?:speed|rpm|r/min|оборот|转速)", "rpm"),
    ("spindle_taper", r"(?:spindle|шпиндел|主轴).*(?:taper|конус|锥度)|(?:tool\s+shank\s+type)", ""),
    ("spindle_bore_mm", r"(?:spindle|шпиндел).*(?:bore|through.?hole|проходн)", "mm"),
    ("chuck_size_mm", r"(?:chuck|патрон|卡盘).*(?:size|diameter|диаметр)", "mm"),
    ("rapid_traverse_x_m_min", r"(?:rapid|fast|холост).*(?:traverse|feed|подач|ход).*\bx[- ]?axis\b|\bx[- ]?axis\b.*(?:rapid|fast|холост)", "m/min"),
    ("rapid_traverse_y_m_min", r"(?:rapid|fast|холост).*(?:traverse|feed|подач|ход).*\by[- ]?axis\b|\by[- ]?axis\b.*(?:rapid|fast|холост)", "m/min"),
    ("rapid_traverse_z_m_min", r"(?:rapid|fast|холост).*(?:traverse|feed|подач|ход).*\bz[- ]?axis\b|\bz[- ]?axis\b.*(?:rapid|fast|холост)", "m/min"),
    ("rapid_traverse_m_min", r"(?:rapid|fast|холост).*(?:traverse|feed|подач|ход)|(?:ускоренн).*(?:подач|перемещ)", "m/min"),
    ("cutting_feed_m_min", r"(?:cutting|working).*(?:feed|подач)|(?:рабоч).*(?:подач)", "m/min"),
    ("feedrate_x_m_min", r"(?:feed\s*rate|feedrate|подач|进给).*\bx[- ]?axis\b", "m/min"),
    ("feedrate_y_m_min", r"(?:feed\s*rate|feedrate|подач|进给).*\by[- ]?axis\b", "m/min"),
    ("feedrate_z_m_min", r"(?:feed\s*rate|feedrate|подач|进给).*\bz[- ]?axis\b", "m/min"),
    ("feedrate_m_min", r"(?:feed\s*rate|feedrate|подач|进给)", "m/min"),
    ("axis_acceleration_g", r"(?:axis|linear|оси).*(?:acceleration|ускорени)|acceleration|ускорени", "G"),
    ("tool_change_time_sec", r"(?:tool|инструмент|atc).*(?:change|смен).*(?:time|время)|tool[- ]?to[- ]?tool|chip[- ]?to[- ]?chip", "sec"),
    ("tool_max_diameter_mm", r"(?:tool|инструмент|刀具).*(?:diameter|диаметр|直径)", "mm"),
    ("tool_max_length_mm", r"(?:tool|инструмент|刀具).*(?:length|длин|长度)", "mm"),
    ("tool_max_weight_kg", r"(?:tool|инструмент|刀具).*(?:weight|mass|масса|вес|重量)", "kg"),
    ("tool_capacity", r"(?:tool|magazine|atc|инструмент|刀库).*(?:storage|capacity|capac\.|number|quantity|количеств|емкост|容量)|(?:capacity|number).*(?:tools|инструмент)", "pcs"),
    ("tool_shank_type", r"(?:tool|инструмент).*(?:shank|holder|хвостовик|оправк)", ""),
    ("tool_selection_type", r"(?:tool|инструмент).*(?:selection|выбор).*(?:type|тип|способ)?", ""),
    ("control_system", r"(?:\bnc\b|\bcnc\b|control|controller|чпу|числов.*управл|数控).*?(?:system|type|name|модель|система|型号|$)", ""),
    ("machine_weight_kg", r"(?:machine|total|net|equipment|станок|станка|整机).*(?:weight|mass|масса|вес|重量)|^(?:weight|масса|вес|重量)$", "kg"),
    ("machine_dimensions_mm", r"(?:machine|overall|external|габарит|станка).*(?:dimension|size|размер|габарит)|(?:installation\s*area|floor\s*space)|^dimensions?\b", "mm"),
    ("machine_height_mm", r"(?:machine\s*)?height|высота|高度", "mm"),
    ("machine_width_mm", r"(?:machine\s*)?width|ширина|宽度", "mm"),
    ("machine_length_mm", r"(?:machine\s*)?length|длина|长度", "mm"),
    ("coolant_tank_l", r"(?:coolant|cooling|охлаждающ|сож|冷却).*(?:tank|capacity|бак|объем|容量)|(?:tank|бак).*coolant", "L"),
    ("power_supply_kva", r"(?:power\s*supply|electric|электропитан|电源).*(?:capacity|kva|потреблен|мощност)", "kVA"),
    ("air_pressure_mpa", r"(?:compressed\s+air\s+supply|compressed\s+air\s+pressure)|(?:air|воздух|气压).*(?:pressure|давлен|压力)", "MPa"),
    ("air_consumption", r"(?:air|воздух|气).*(?:consumption|flow|расход|耗气)", ""),
]


# Specific labels from multi-row specification tables.
# A short cell such as "Height" has stronger meaning than a broad section
# such as "Machine dimensions"; do not classify it from the section first.
EXACT_PARAMETER = [
    (r"^(?:height|machine height)$", "machine_height_mm", "mm"),
    (r"^(?:width|machine width)$", "machine_width_mm", "mm"),
    (r"^(?:length|length \(.*chip conveyor.*\)|machine length)$", "machine_length_mm", "mm"),
    (r"^(?:weight|machine weight)$", "machine_weight_kg", "kg"),
    (r"^workpiece size$", "max_workpiece_size_mm", "mm"),
    (r"^pallet size$", "pallet_size_mm", "mm"),
    (r"^work inertia", "table_work_inertia_nm2", "N*m2"),
    (r"^simultaneously controlled (?:axis|axes)$", "simultaneously_controlled_axes", "axes"),
    (r"^controlled (?:axis|axes)$", "controlled_axes", "axes"),
    (r"^acc\.?/dec\.?$|^acceleration/deceleration$", "axis_acceleration_xyz_g", "G"),
    (r"^pull stud$", "tool_pull_stud", ""),
    (r"^max\.? tool moment$", "tool_max_moment_nm", "N*m"),
    (r"^spindle nose to table center$", "spindle_nose_to_table_center_mm", "mm"),
    (r"^distance from spindle nose to table top$", "spindle_nose_to_table_top_mm", "mm"),
    (r"^dimensions?\s*\(l\s*[x×]\s*w\)$", "machine_dimensions_mm", "mm"),
    (r"^max\.? tool diameter$", "tool_max_diameter_mm", "mm"),
    (r"^(?:taper|spindle taper)$", "spindle_taper", ""),
    (r"^table\s+surface\s+type$|^t[- ]?slot(?:\s+type)?$", "table_t_slot", ""),
    (r"^coolant\s+pump\s+motor\s+power$", "coolant_pump_motor_power_kw", "kW"),
    (r"^compressed\s+air\s+supply$", "air_pressure_mpa", "MPa"),
    (r"^tool\s+storage\s+cap(?:a\.|acity)\s*$", "tool_capacity", "pcs"),
    (r"^max\.? tool length$", "tool_max_length_mm", "mm"),
    (r"^tool change time \(tool-to-tool\)$", "tool_change_time_sec", "sec"),
]

def parameter(label: str, section: str = ""):
    label = norm(label)
    if not label:
        return None
    lower = label.lower()
    for pattern, key, unit in EXACT_PARAMETER:
        if re.search(pattern, lower, re.I):
            return key, unit
    if lower in {"taper", "spindle taper"} and re.search(r"spindle|шпиндел", section, re.I):
        return "spindle_taper", ""
    if lower in {"continuous", "continous", "without", "without adjacent tools"} and re.search(
            r"max\.? tool diameter|tool diameter", section, re.I):
        return "tool_max_diameter_mm", "mm"
    axis = re.fullmatch(r"([xyzbc])\s*(?:[- ]?axis|轴)?", lower)
    # An axis-only label needs a SPECIFIC active section. Without one, skip it.
    if axis and re.search(r"(?:travel|stroke|перемещ|ход|行程)", section, re.I):
        axis_name = axis.group(1)
        return "travel_" + axis_name + ("_deg" if axis_name in "bc" else "_mm"), ("deg" if axis_name in "bc" else "mm")
    if axis and re.search(r"(?:rapid|traverse|холост)", section, re.I):
        axis_name = axis.group(1)
        return ("rapid_traverse_" + axis_name + ("_rpm" if axis_name in "bc" else "_m_min"),
                "rpm" if axis_name in "bc" else "m/min")
    if axis and re.search(r"(?:feedrate|feed\s+rate|подач|进给)", section, re.I):
        return "feedrate_" + axis.group(1) + "_m_min", "m/min"
    for key, pattern, unit in PARAMS:
        if re.search(pattern, lower, re.I):
            return key, unit
    # Do not classify arbitrary labels by a stale section heading.
    # Only axis-only and short generic labels may inherit the section.
    # Short generic labels like 'Max. torque' under 'Spindle'.
    if section and re.search(r"^(?:max\.?\s*)?(?:speed|torque|power|loading capacity|capacity|taper)$", lower):
        for key, pattern, unit in PARAMS:
            if re.search(pattern, section + " " + label, re.I):
                return key, unit
    return None


def section_heading(cells: list[str]) -> str:
    vals = [norm(c) for c in cells if norm(c)]
    if not vals:
        return ""
    item = vals[0].lower()
    # Only group/section labels are inherited. A missing value in a full
    # parameter row (e.g. 'Table loading capacity') is NOT a section change.
    if len(vals) <= 2 and not NUMBER_RE.search(" ".join(vals)):
        if re.fullmatch(r"(?:travels?|travel\s+distance|stroke|перемещени[ея]|ход|行程|feedrates?|rapid(?:\s+traverse(?:\s+rate)?)?|spindle|шпиндель|主轴|magazine|atc|automatic\s+tool\s+changer|table|стол|工作台|control|cnc|nc\s+system|machine\s+specifications)", item):
            return vals[0]
    return ""


def right_value_cell(text: str) -> bool:
    value = norm(text)
    return bool(value) and len(value) <= 300 and (bool(NUMBER_RE.search(value)) or bool(re.search(r"\b(?:FANUC|SIEMENS|HEIDENHAIN|SINUMERIK|CUFOS|MITSUBISHI|TNC\s*\d+)\b", value, re.I)))


def numeric_or_spec_row(cells: list[str], table: bool) -> bool:
    line = " | ".join(cells)
    if not NUMBER_RE.search(line):
        return False
    if table and len(cells) > 2:
        return bool(TECHNICAL_WORDS.search(line) or TECHNICAL_UNITS.search(line) or any(re.search(r"\d", c) for c in cells[2:]))
    return bool(TECHNICAL_WORDS.search(line) or TECHNICAL_UNITS.search(line))


TEXT_KEYS = {"control_system", "spindle_taper", "tool_shank_type", "tool_selection_type",
             "tool_pull_stud", "table_t_slot"}


def numeric_value_issue(key: str, value: str, unit: str = "") -> str:
    """Reject measurements whose *only* number comes from explanatory prose.

    Accept explicit ranges, dimensions, power duty pairs and dimension symbols.
    Never auto-correct a decimal point that OCR may have dropped.
    """
    value = norm(value)
    if key in TEXT_KEYS:
        if key == "spindle_taper" and not re.search(
                r"\b(?:ISO|BT|CAT|HSK|DIN|SK)\b|\b7\s*/\s*24\s*TAPER\b", value, re.I):
            return "spindle_taper_ocr_uncertain"
        if key == "table_t_slot" and not re.search(r"t[- ]?slot|паз", value, re.I):
            return "table_surface_non_slot"
        return ""
    if key in {"controlled_axes", "simultaneously_controlled_axes"}:
        return "" if re.match(r"^\s*\d{1,2}\s*(?:axes?|\(|$)", value, re.I) else "invalid_axes_measurement"
    # Explicitly labelled controller variants are legitimate power/torque
    # values, unlike free-form prose that merely contains a digit.
    controller_variants = bool(key in {"spindle_power_kw", "spindle_torque_nm"} and
                               re.match(r"^\s*(?:fanuc|h/h|heidenhain|siemens|mitsubishi)\s*:\s*\d", value, re.I))
    configuration_dimensions = bool(key.startswith("machine_") and key.endswith("_mm") and
                                    re.match(r"^\s*[A-ZЕЁН]\s+\d{3,5}\b", value, re.I))
    if not (controller_variants or configuration_dimensions or
            re.match(r"^\s*[~≈≃<>≤≥±øØ⌀φΦ]?\s*[-+]?\d", value)):
        return "non_numeric_measurement"
    if key == "tool_change_time_sec":
        if not re.fullmatch(r"\s*\d{1,3}(?:[.,]\d{1,3})?\s*(?:s|sec|seconds?)?\s*", value, re.I):
            return "tool_change_time_not_seconds"
    if key == "tool_capacity":
        if not re.match(r"^\s*\d{1,4}(?:\s*(?:\{[\d, /]+\}\*?|ea|pcs?|tools?))?\s*$", value, re.I):
            return "tool_capacity_not_count"
    if key in {"table_work_inertia_nm2", "axis_acceleration_xyz_g"} and len(value) > 95:
        return "measurement_extra_text"
    if re.search(r"\b(?:times\s+during|during\s+processing|tools\s+changed|cycles?\s+per|see\s+page)\b", value, re.I):
        return "narrative_in_numeric_value"
    return ""


def paired_unit_issue(value: str, unit: str) -> str:
    """Catch OCR cropping the imperial half of a metric/imperial pair.

    Check only clear scalar pairs. Interpreting duty ratios, dimension
    matrices, and alternatives needs a separate parser.
    """
    u = norm(unit).lower()
    unit_factors = [
        (r"mm\s*[({]\s*inch", 1/25.4),
        (r"kg\s*[({]\s*(?:lb|ib)", 2.2046226),
        (r"kw\s*[({]\s*hp", 1.3410221),
        (r"n[.·*-]?m\s*[({]\s*(?:ft|lb)", .7375621),
        (r"m/min\s*[({]\s*ipm", 39.3700787),
    ]
    factor = next((v for r,v in unit_factors if re.search(r,u,re.I)), None)
    if factor is None:
        return ""
    match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*\(\s*(\d+(?:[.,]\d+)?)\s*\)\s*",norm(value))
    if match is None:
        return ""
    first, second = (float(x.replace(",",".")) for x in match.groups())
    if first <= 0:
        return "metric_imperial_pair_invalid"
    expected = first * factor
    if abs(second - expected) > max(1.2, expected * .09):
        return "metric_imperial_pair_mismatch"
    return ""


def invalid_unit(parameter_key: str, unit_cell: str, value: str) -> str:
    u = norm(unit_cell).lower()
    val = norm(value)
    if any(c in val for c in ("\ufffd", "(cid:")):
        return "damaged_value"
    numerical_issue = numeric_value_issue(parameter_key, val, u)
    if numerical_issue:
        return numerical_issue
    pair_issue = paired_unit_issue(val, unit_cell)
    if pair_issue:
        return pair_issue
    if parameter_key in {"controlled_axes", "simultaneously_controlled_axes"}:
        if not re.match(r"^\s*\d{1,2}(?:\s*$|\s*(?:axes?\b|[({]))", val, re.I):
            return "invalid_controlled_axis_count"
    if parameter_key == "tool_capacity":
        if re.search(r"\b(?:kg|lb|mm|m/min|ipm)\b", u):
            return "tool_capacity_unit_conflict"
        if re.search(r"[øØ]|\bmm\b|\bkg\b", val, re.I):
            return "tool_capacity_noncount_value"
    if parameter_key == "table_work_inertia_nm2":
        if re.search(r"(?:\bkg\b|\blb\b)", u, re.I):
            return "inertia_mass_unit_conflict"
        if re.search(r"\{?\s*\d{2,5}\s*\(\s*\d{3,7}(?:[.,]\d+)?\s*\)\s*\}?\*?", val):
            return "inertia_mass_contamination"
    if parameter_key in {"table_load_kg", "tool_max_weight_kg", "machine_weight_kg"}:
        if re.search(r"(?:m/min|r/min|\brpm\b|\bkw\b|\bmm\b)", u):
            return "mass_unit_conflict"
    if parameter_key.startswith("travel_") and re.search(r"\b(?:kg|lb|kw|m/min)\b", u):
        return "travel_unit_conflict"
    if parameter_key.startswith("rapid_traverse") and re.search(r"\b(?:kg|kW)\b", u):
        return "feed_unit_conflict"
    if parameter_key == "air_pressure_mpa" and re.search(r"(?:nl/min|l/min|m3/h|m³/h|\bkw\b)", u):
        return "pressure_flow_unit_conflict"
    if parameter_key == "coolant_pump_motor_power_kw" and re.search(r"\b(?:kg|mm|m/min|rpm)\b", u):
        return "motor_power_unit_conflict"
    if (parameter_key.startswith(("spindle_power", "spindle_torque")) and unit_cell
            and re.search(r"\b(?:kg|mm|m/min|rpm)\b", u)):
        return "spindle_unit_conflict"
    if parameter_key == "control_system" and not re.search(
            r"(?:fanuc|siemens|heidenhain|sinumerik|cufos|mitsubishi|fagor|haidenhain|tnc|num|okuma|dn\s+solutions\s+cnc|\bD\d{3,4}\b|s-one)", val, re.I):
        return "control_value_unrecognized"
    return ""


def recover_inertia_value(raw: str) -> tuple[str, str]:
    """OCR may prepend the preceding kilogram option to an inertia value.

    Recover ONLY a single literal trailing scalar after an isolated mass pair;
    retain the full OCR text and a review entry for human confirmation.
    """
    value = norm(raw)
    match = re.fullmatch(
        r"\{\s*\d{2,5}\s*\(\s*\d{3,7}(?:[.,]\d+)?\s*\)\s*\}\s*\*\s+(\d{1,5}(?:[.,]\d+)?)", value)
    if match:
        return match.group(1), "inertia_mass_bleed_recovered"
    return value, ""


def recover_primary_metric(key: str, raw: str, source_unit: str) -> tuple[str, str]:
    """Recover the leading metric ONLY if a trailing inch bracket is broken.

    The broken secondary unit is never silently repaired or made authoritative.
    """
    if not key.endswith("_mm") or not re.search(r"mm", source_unit, re.I):
        return raw, ""
    if raw.count("(") == raw.count(")") and raw.count("{") == raw.count("}"):
        return raw, ""
    m = re.fullmatch(r"\s*(\d{2,6}(?:[.,]\d+)?)\s*\(\s*\d{1,6}(?:[.,]\d+)?\s*\}\s*", raw)
    if m:
        return m.group(1), "ocr_imperial_bracket_broken_metric_only"
    return raw, ""


def parse_options(value: str) -> tuple[str, list[str], str]:
    """Separate literal curly-braced options while preserving paired duty numbers."""
    raw = norm(value)
    spans = list(re.finditer(r"\{([^{}]+)\}\s*(\*)?", raw))
    if not spans:
        return raw, [], ""
    options = []
    for span in spans:
        chunk = span.group(1)
        # A catalog may encode two metric torque options followed by their
        # paired imperial values: {303, 221 (223.6, 163.1)}*. A blind split
        # loses the engineering pairings.
        paired = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\)\s*", chunk)
        if paired:
            a, b, a_imperial, b_imperial = paired.groups()
            options.extend([f"{a} ({a_imperial})", f"{b} ({b_imperial})"])
            continue
        depth, segment = 0, []
        for ch in chunk:
            if ch == "(": depth += 1
            elif ch == ")": depth -= 1
            if ch == "," and depth == 0:
                text = norm("".join(segment))
                if text: options.append(text)
                segment = []
            else:
                segment.append(ch)
        text = norm("".join(segment))
        if text: options.append(text)
    base = norm(raw[:spans[0].start()] + raw[spans[-1].end():])
    return base, options, "catalog_star_option" if any(x.group(2) for x in spans) else "catalog_braced_alternative"


def duty_values_from_power(value: str, duty: str) -> dict[str, str]:
    """Keep distinct S3 and continuous *base* values, never create fake defaults."""
    if not re.search(r"\bS3\s*/\s*(?:Cont|continuous)", duty, re.I):
        return {}
    value, _, _ = parse_options(value)
    match = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*/\s*(\d+(?:[.,]\d+)?)", value)
    if not match:
        return {}
    return {"S3": match.group(1), "Continuous": match.group(2)}


def duty_condition(label: str) -> str:
    m = re.search(r"(?<![A-Za-z0-9])(?:S[136]\s*[/+]\s*(?:Cont(?:inuous)?\.?|S[136])|30\s*min|Continuous\s*[/+]\s*S[136])(?=[^A-Za-z0-9]|$)", label, re.I)
    return norm(m.group(0)) if m else ""


def review_entry(path: Path, row: dict, reason: str, models_context: list[str], **more):
    out = {"source_file": path.name, "source_page": row["page"],
           "table": row["table"], "row": row["row"], "row_id": f"{row['page']}:{row['table']}:{row['row']}",
           "models_context": ", ".join(models_context), "cells": row["cells"], "reason": reason,
           "row_kind": row.get("kind", ""), "ocr_geometry": bool(row.get("ocr_geometry", False))}
    out.update(more)
    return out


def build_model_registry(path: Path, source: list[dict]):
    """Model list is confirmed by filename or specification headers, not OCR prose.

    A single OCR read such as SVM 51001 cannot add a new model when the
    catalogue has a native header naming SVM 5100L.
    """
    prefix = preferred_prefix(path)
    known: dict[str, dict] = {}
    for name in filename_models(path.stem):
        if not prefix or name.startswith(prefix + " "):
            known[name] = {"model": name, "evidence": "filename"}

    # A row containing multiple short model header cells is strong evidence.
    groups = defaultdict(list)
    for row in source:
        if row["kind"] == "table":
            groups[(row["page"], row["table"])].append(row)
    for group in groups.values():
        for row in group[:25]:
            candidates = []
            for cell in row["cells"]:
                names = candidate_models(cell, prefix)
                if len(names) == 1 and len(norm(cell)) <= 55:
                    candidates.append(names[0])
            if len(set(candidates)) >= 2:
                evidence = "ocr_table_header" if row.get("ocr_geometry") else "native_table_header"
                for name in candidates:
                    current = known.get(name)
                    if current is None or current.get("evidence") not in ("filename", "native_table_header"):
                        known[name] = {"model": name, "evidence": evidence}

    # Native text can confirm explicit short model titles, never single OCR mentions.
    for row in source:
        if row["kind"] != "text" or row.get("ocr_text"):
            continue
        line = norm(" ".join(row["cells"]))
        if len(line) > 90:
            continue
        for name in candidate_models(line, prefix):
            # Do not treat a specification value/part number as a model title.
            if name not in known and (line.upper() == name or
                    re.search(r"\b(?:model|machine|specifications?)\b", line, re.I)):
                known[name] = {"model": name, "evidence": "native_title"}

    # For OCR-only catalogues, repeated model names on different pages are
    # allowed, but single occurrence remains in review rather than registry.
    occurrences = defaultdict(set)
    for row in source:
        if not row.get("ocr_text"):
            continue
        line = norm(" ".join(row["cells"]))
        for name in candidate_models(line, prefix):
            occurrences[name].add(row["page"])
    for name, pages in occurrences.items():
        if name not in known and len(pages) >= 3:
            known[name] = {"model": name, "evidence": "ocr_repeated"}

    # Recognize the classic OCR substitution L -> 1 only when a trusted
    # counterpart already exists. Do not infer that every trailing 1 is L.
    def model_alias(name: str) -> str:
        match = re.fullmatch(r"([A-Z]+) (\d{4,5})", name)
        if match and match.group(2).endswith("1"):
            alias = match.group(1) + " " + match.group(2)[:-1] + "L"
            if alias in known and known[alias]["evidence"] in (
                    "filename", "native_table_header", "native_title"):
                return alias
        return name

    for name in list(known):
        canonical = model_alias(name)
        if canonical != name and (
                known[name].get("evidence") in ("ocr_repeated", "ocr_table_header") or
                (known[name].get("evidence") == "native_title" and
                 known[canonical].get("evidence") == "native_table_header")):
            del known[name]

    # A fully qualified /50II model from a native comparison header takes
    # precedence over truncated running titles and OCR 11/II substitutions.
    # Never collapse /50 models onto their #40 counterparts.
    authoritative_mynx = {n for n, meta in known.items() if n.startswith('MYNX ')
                          and meta.get('evidence') == 'native_table_header'
                          and n.endswith('II')}
    if authoritative_mynx:
        for name in list(known):
            if not name.startswith('MYNX ') or name in authoritative_mynx:
                continue
            root = name.replace('/5011', '/50II').replace('/50II', '/50').removesuffix('II')
            if any(a.replace('/50II', '/50').removesuffix('II') == root
                   for a in authoritative_mynx):
                del known[name]

    # Page context contains only confirmed models; OCR mentions are never
    # allowed to spawn a new row in the wide CSV.
    page_models = defaultdict(list)
    for row in source:
        line = " ".join(row["cells"])
        for raw_name in candidate_models(line, prefix):
            name = model_alias(raw_name)
            if name in known and name not in page_models[row["page"]]:
                page_models[row["page"]].append(name)
    return known, page_models


def table_headers(rows: list[dict], prefix: str) -> dict[int, str]:
    """Pick column-specific headers; never collapse empty model cells."""
    headers = {}
    for row in rows[:min(len(rows), 15)]:
        cells = row["cells"]
        candidates = {}
        for col, cell in enumerate(cells):
            names = candidate_models(cell, prefix)
            # Real model header is short, not a prose description.
            if len(names) == 1 and len(norm(cell)) <= 55:
                candidates[col] = names[0]
        if len(candidates) >= 2:
            return candidates
        if candidates and not headers:
            headers = candidates
    return headers


def table_value_start(cells: list[str], label_index: int, headers: dict[int, str]):
    # Units are often one or two cells before the model columns.
    # A single OCR model header may appear only in the last model column.
    if headers:
        units_before = [i for i, c in enumerate(cells[:min(headers)])
                        if UNIT_ONLY_RE.fullmatch(norm(c)) or OCR_UNIT_RE.fullmatch(norm(c)) or
                        re.fullmatch(r"(?:[mмт]{1,2}\s*\{?\(?inch\)?\}?|kg\s*\(i?b\)|N[-.]m.*|kw\s*\(hp\)|m/min\s*\(ipm\))", norm(c), re.I)]
        if units_before:
            return max(units_before) + 1
        return min(headers)
    # Typical 3-column: label, unit, value. 4-col: category, label, unit, value.
    # Prefer a detected unit cell over hardcoded column index.
    units = [i for i, c in enumerate(cells[:5]) if UNIT_ONLY_RE.fullmatch(norm(c)) or OCR_UNIT_RE.fullmatch(norm(c))]
    if units:
        return max(units) + 1
    if len(cells) >= 5:
        return 4
    if len(cells) >= 3:
        return max(2, label_index + 1)
    return 1


def model_header_centers(rows: list[dict], headers: dict[int, str]) -> dict[int, float]:
    """Return the x-centers of named model columns in the header row."""
    for row in rows[:25]:
        boxes = row.get("cell_boxes") or []
        if len(boxes) != len(row["cells"]):
            continue
        if all(col < len(boxes) and boxes[col] for col in headers):
            if all(name in candidate_models(row["cells"][col]) for col, name in headers.items()):
                return {col: (boxes[col][0] + boxes[col][2]) / 2 for col in headers}
    return {}


def row_label(cells: list[str], value_start: int, group: str, sub: str):
    """Parse hierarchical labels and carry only legitimate section headings.

    An axis row inherits Travel/Rapid traverse, but a 'Taper' row must not
    overwrite the previous Spindle section before being classified.
    """
    parts = [norm(c) for c in cells[:value_start]]
    details = [c for c in parts if c and not UNIT_ONLY_RE.fullmatch(c) and not OCR_UNIT_RE.fullmatch(c)]
    first = parts[0] if parts else ""
    second = parts[1] if len(parts)>1 else ""
    third = parts[2] if len(parts)>2 else ""
    prior_group = group
    prior_sub = sub
    if value_start >= 4 and len(cells) >= 6:
        if first and first.lower() not in {"description", "unit"}:
            group = first
            sub = ""
        if second and second.lower() not in {"description", "unit"}:
            sub = second
        section = norm(group + " " + sub)
        for label, context in ((third,section), (second,group), (second+" "+third,group), (first, prior_group)):
            if label:
                result = parameter(label,context)
                if result:
                    return result, group, sub, third or second
        return None, group, sub, third
    if value_start >= 3 and len(cells) >= 4:
        # Find the parameter before mutating any carried group.
        for label,context in ((second, norm(prior_group+" "+prior_sub)),
                              (first,prior_group), (first+" "+second,prior_group)):
            if label:
                result=parameter(label,context)
                if result:
                    if first and first.lower() in {"travel","travels","travel distance","feedrates","feedrate","rapid traverse", "rapid traverse rate", "spindle","table","motor","tool","control","automatic tool changer (atc)","machine","tank capacity","power"}:
                        group=first
                    return result, group, sub, second or first
        # Only carry an explicit section label, not an arbitrary row label.
        if first and first.lower() in {"travel","travels","travel distance","feedrates","feedrate","rapid traverse", "rapid traverse rate", "spindle","table","motor","tool","control","automatic tool changer (atc)","machine","tank capacity","power"}:
            group=first
        if second:
            result = parameter(second,group)
            if result:
                return result,group,sub,second
        combined=norm(" ".join(details))
        if combined:
            result=parameter(combined,prior_group)
            if result:
                return result,group,sub,second
        return None,group,sub,second
    label=" / ".join(details)
    return parameter(label,prior_group),group,sub,second


def diameter_pair_rows(rows: list[dict]) -> set[int]:
    """Recognize consecutive continuous/without-adjacent tool DIAMETER values.

    Require BOTH qualifier rows close together, mm units and an ATC context.
    Do not reinterpret an unrelated 'Continuous' spindle power row.
    """
    pair_rows=set()
    for i,row in enumerate(rows):
        cells=row.get("cells",[])
        label=" ".join(cells[:3]).lower()
        if not re.search(r"\bcontin(?:u)?ous\b",label):
            continue
        for j in range(i+1,min(len(rows),i+4)):
            target=rows[j].get("cells",[])
            other=" ".join(target[:3]).lower()
            if not re.search(r"without\s+(?:adjacent\s+)?tools?",other):
                continue
            if not any(re.search(r"mm\s*\(?inch",c,re.I) for c in cells+target):
                continue
            context=" ".join(" ".join(r.get("cells",[])[:2]) for r in rows[max(0,i-6):j+1])
            if not re.search(r"(?:tool|magazine|atc|changer)",context,re.I):
                continue
            pair_rows.update({i,j})
            break
    return pair_rows


def reconstruct_rapid_traverse_rows(rows: list[dict], has_page_heading: bool = False) -> dict[int, str]:
    """Recover a three-axis rapid-traverse subheading lost by table OCR.

    The recovery is labelled 'inferred' unless Rapid traverse explicitly appears
    on the same page. A generic Feedrates row by itself is NOT enough.
    Require X, Y and Z, compatible m/min units, and rapid-like (>25 m/min)
    matching scalar speeds. A different cutting feed remains unclassified.
    """
    found = {}
    for i,row in enumerate(rows):
        label = " ".join(row.get('cells',[])[:3]).lower()
        if not re.search(r'feedrates?|rapid\s+traverse',label):
            continue
        if re.search(r'cutting\s+feed|working\s+feed',label):
            continue
        triple=[]
        for j in range(i,min(i+6,len(rows))):
            cells=rows[j].get('cells',[])
            if len(cells)<4:
                continue
            head=" ".join(cells[:3]).lower()
            if j!=i and re.search(r'^(?:tool|spindle|control|table|motor)\b',head):
                break
            m=re.search(r'\b([xyz])\s*(?:-\s*)?axis\b', head)
            if not m or not re.search(r'm/min|ipm',head):
                continue
            primary=[]
            for candidate in cells[3:]:
                value=norm(candidate)
                if not value:
                    continue
                number=re.match(r'^(\d{1,3}(?:[.,]\d+)?)\b',value)
                if number:
                    primary.append(float(number.group(1).replace(',','.')))
            if not primary or any(not 25 <= x <= 100 for x in primary):
                continue
            triple.append((j,m.group(1),primary))
        if {axis for _,axis,_ in triple} != {'x','y','z'}:
            continue
        # Numerical consistency across all three axes helps disambiguate a
        # rapid traverse from arbitrary spindle/machining graph numbers.
        first_speed = tuple(triple[0][2])
        if not all(len(nums)==len(first_speed) and
                   all(abs(x-y)<0.3 for x,y in zip(nums,first_speed)) for _,_,nums in triple):
            continue
        for index,axis,_ in triple:
            found[index]=axis
        break
    return found


def metric_imperial_anomaly(key: str, value: str, unit: str) -> bool:
    """Flag obvious paired-mm/inch errors; keep the original value unchanged."""
    if "mm" not in unit.lower() or "inch" not in unit.lower():
        return False
    if key in {"pallet_size_mm", "table_size_mm", "machine_dimensions_mm", "max_workpiece_size_mm"}:
        match = re.search(r"(\d{2,5})\s*[x×]\s*(\d{2,5})\s*\(\s*(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*\)", value, re.I)
        if match:
            a, b, inch_a, inch_b = map(float, match.groups())
            return abs(a / 25.4 - inch_a) > .6 or abs(b / 25.4 - inch_b) > .6
    if key.startswith("spindle_nose_to_table_") or key.startswith("travel_"):
        match = re.search(r"(\d{2,5})\s*[~～-]\s*(\d{2,5})\s*\(\s*(\d+(?:\.\d+)?)\s*[~～-]\s*(\d+(?:\.\d+)?)\s*\)", value)
        if match:
            a, b, inch_a, inch_b = map(float, match.groups())
            return abs(a / 25.4 - inch_a) > .6 or abs(b / 25.4 - inch_b) > .6
    return False


def table_load_mass_pair_candidates(cells: list[str], start: int, section: str,
                                    label: str, unit: str, headers: dict[int, str],
                                    row: dict) -> dict[int, str]:
    """Interpret a mislabelled *Table* load, but only with independent evidence.

    Some catalogues print 'Tool storage capacity' in the Table section while
    the values are kg(lb) table/pallet loads. Neither a count of tools nor a
    guessed value is accepted. A native comparative table, >=2 named model
    columns, a Table heading, a matching mass unit and consistent conversions
    are all mandatory. OCR-inferred column geometry never satisfies this gate.
    """
    if (row.get('ocr_geometry') or row.get('bbox_inferred') or
            row.get('kind') != 'table' or len(headers) < 2 or
            not re.search(r'\btable\b|\bстол\b|工作台', section, re.I) or
            not re.search(r'\btool\s+storage\s+cap(?:acity|a\.)\b', label, re.I) or
            not re.search(r'\bkg\s*[({]\s*(?:lb|ib)', unit, re.I)):
        return {}
    candidates = {}
    for col in range(start, len(cells)):
        raw = norm(cells[col])
        # The prefix may contain the preceding workpiece diameter/height;
        # only the LAST complete metric-imperial mass pair can be recovered.
        match = re.search(r'(?<![\d.])(\d{2,5})\s*\(\s*(\d{2,7}(?:[.,]\d+)?)\s*\)\s*$', raw)
        if not match:
            continue
        metric = match.group(1) + ' (' + match.group(2) + ')'
        if invalid_unit('table_load_kg', unit, metric):
            continue
        candidates[col] = metric
    return candidates


def adjacent_table_load_option(rows: list[dict], index: int, col: int, unit: str) -> tuple[str, str]:
    """Recover an explicit kg(lb) option that bled into next inertia row.

    No option is generated from an expected benchmark or nearby machine.
    The literal brace, unit conversion, same model column, adjacent row and
    different inertia unit must all agree. Result is review-required.
    """
    if index + 1 >= len(rows):
        return '', ''
    this_row = rows[index]
    next_row = rows[index + 1]
    if (next_row['page'], next_row['table']) != (this_row['page'], this_row['table']):
        return '', ''
    nc = next_row.get('cells', [])
    if col >= len(nc) or not re.search(r'\bwork\s+inertia\b|инерци', ' '.join(nc[:4]), re.I):
        return '', ''
    if not re.search(r'N[.·*\-]?m\s*2', ' '.join(nc[:4]), re.I):
        return '', ''
    match = re.match(r'^\s*(\{\s*\d{2,5}\s*\(\s*\d{3,7}(?:[.,]\d+)?\s*\)\s*\}\s*\*)\s+\d', norm(nc[col]))
    if not match:
        return '', ''
    span = norm(match.group(1))
    pair = re.search(r'\{\s*(.*?)\s*\}', span).group(1)
    if invalid_unit('table_load_kg', unit, pair):
        return '', ''
    return span, f"{next_row['page']}:{next_row['table']}:{next_row['row']}"


def extract_single_table(path: Path, rows: list[dict], filename_candidates: list[str], page_models: dict, prefix: str, trusted_models: dict, rechecker=None):
    facts, review = [], []
    headers = {i: model for i, model in table_headers(rows, prefix).items()
               if model in trusted_models}
    centers = model_header_centers(rows, headers)
    diameter_rows = diameter_pair_rows(rows)
    rapid_rows = reconstruct_rapid_traverse_rows(rows)
    table_models = list(dict.fromkeys(headers.values()))
    active_group = ""
    active_sub = ""
    for row_idx, row in enumerate(rows):
        cells = list(row["cells"])
        if len(cells) < 2 or not any(cells):
            continue
        value_start = table_value_start(cells, 0, headers)
        recognized, active_group, active_sub, detail = row_label(
            cells, value_start, active_group, active_sub)
        # 'Continous' and 'Without adjacent tools' often sit below a missing
        # merged 'Max. tool diameter' heading. Both rows are required.
        if not recognized:
            if row_idx in diameter_rows:
                recognized = ("tool_max_diameter_mm", "mm")
                detail = " ".join(cells[:value_start])
            elif row_idx in rapid_rows:
                recognized = (f"rapid_traverse_{rapid_rows[row_idx]}_m_min", "m/min")
        models_context = list(dict.fromkeys(table_models + page_models.get(row["page"], []) + filename_candidates))
        if not recognized:
            if numeric_or_spec_row(cells, True):
                review.append(review_entry(path, row, "unrecognized_parameter", models_context))
            continue
        key, expected_unit = recognized
        rapid_reconstructed = row_idx in rapid_rows and key.startswith("rapid_traverse_")
        if row_idx in rapid_rows and key in {
                'feedrate_m_min','feedrate_x_m_min','feedrate_y_m_min','feedrate_z_m_min'}:
            key = f"rapid_traverse_{rapid_rows[row_idx]}_m_min"
            expected_unit = "m/min"
            rapid_reconstructed = True
        unit = norm(cells[value_start - 1]) if value_start > 0 else ""
        if rapid_reconstructed and value_start >= 2 and re.search(r'm/min', cells[value_start-2], re.I):
            unit = 'm/min ' + unit
        if not (len(unit) <= 26 and (UNIT_ONLY_RE.fullmatch(unit) or OCR_UNIT_RE.fullmatch(unit) or
                   TECHNICAL_UNITS.search(unit) or unit in {"G", "N·m2", "-"})):
            unit = ""
        raw_label = " ".join(cells[:value_start])
        conflicting_capacity_label = key == "tool_capacity" and bool(re.search(
            r"(?:\bkg\b|\blb\b|m/min|\bipm\b)", unit + " " + raw_label, re.I))
        load_candidate_values = {}
        load_option_rows = {}
        if conflicting_capacity_label:
            # Never treat a kg value as a COUNT of tools. The data may be an
            # erroneous heading in a Table section; recover only verifiable
            # mass pairs in explicitly named model columns.
            load_candidate_values = table_load_mass_pair_candidates(
                cells, value_start, active_group, raw_label, unit, headers, row)
            related = []
            for col, name in headers.items():
                if col >= len(cells) or not norm(cells[col]):
                    continue
                related.append({"model": name, "source_value": norm(cells[col]),
                                "candidate_value": load_candidate_values.get(col, '')})
            review.append(review_entry(path, row, "tool_capacity_unit_conflict", models_context,
                                       parameter="tool_capacity", unit=unit,
                                       suggested_parameter=("table_load_kg" if re.search(r"\bkg\b", unit, re.I)
                                                            else "tool_capacity"),
                                       candidates_unverified=related,
                                       resolution=("mass_pair_reclassified_for_review" if load_candidate_values
                                                   else "unresolved"),
                                       instruction="Verify the label in the original catalogue. Never use mass as tool count."))
            if not load_candidate_values:
                continue
            key, expected_unit = "table_load_kg", "kg"
            for col, metric in load_candidate_values.items():
                cells[col] = metric
                option, extra_row = adjacent_table_load_option(rows, row_idx, col, unit)
                if option:
                    cells[col] += ' ' + option
                    load_option_rows[col] = extra_row
            review.append(review_entry(path, row, "table_load_label_unit_reclassified", models_context,
                                       parameter=key, unit=unit, source_label=raw_label,
                                       candidate_values=load_candidate_values,
                                       adjacent_option_rows=load_option_rows,
                                       instruction="Accepted as unverified mass load; review original label and adjoining row."))

        # Targeted local OCR is allowed for blank or suspicious values only
        # when this table has an explicit model header and a usable crop box.
        # Do not extend a value into a different model's column.
        ocr_recoveries = {}
        if rechecker is not None and headers:
            for col, name in headers.items():
                if col < value_start or col >= len(cells) or name not in trusted_models:
                    continue
                original = norm(cells[col])
                # A blank column covered by the geometry of a neighbouring
                # merged value is NOT a missing cell and must not receive OCR.
                boxes = row.get('cell_boxes') or []
                if not original and centers and col in centers:
                    covered_by_span = any(
                        other != col and other >= value_start and other < len(boxes)
                        and boxes[other] and norm(cells[other])
                        and boxes[other][0] - 1 <= centers[col] <= boxes[other][2] + 1
                        for other in range(value_start, len(cells)))
                    if covered_by_span:
                        continue
                suspicious = (not original or bool(invalid_unit(key, unit, original)) or
                              (key == "tool_change_time_sec" and
                               re.search(r"tool[- ]?to[- ]?tool", " ".join(cells[:value_start]), re.I)
                               and bool(re.fullmatch(r"\d{2,3}", original)) and int(original) >= 5))
                if not suspicious:
                    continue
                improved, audit = rechecker.recheck(row, col, key, original, unit)
                if improved:
                    cells[col] = improved
                    ocr_recoveries[col] = audit
                    review.append(review_entry(path, row, "cell_ocr_recovered_needs_review", [name],
                                               parameter=key, original=original, recovered=improved,
                                               bbox=audit.get('bbox'), ocr_evidence=audit))
                elif original == "":
                    review.append(review_entry(path, row, "empty_model_cell_ocr_unresolved", [name],
                                               parameter=key, bbox=audit.get('bbox'), ocr_evidence=audit))
        text_capable = key in TEXT_KEYS
        candidates = [i for i in range(value_start, len(cells))
                      if (i in load_candidate_values if load_candidate_values else
                          (right_value_cell(cells[i]) or (text_capable and 0 < len(norm(cells[i])) < 140)))]
        if not candidates:
            if any(cells[value_start:]) or numeric_or_spec_row(cells, True):
                review.append(review_entry(path, row, "no_structured_value", models_context, parameter=key))
            if len(headers) >= 2:
                for col, name in headers.items():
                    if col >= value_start and col < len(cells):
                        review.append(review_entry(path, row, "missing_model_value", [name],
                                                   parameter=key, model=name,
                                                   expected_col=col,
                                                   cell_bbox=((row.get('cell_boxes') or [None]*len(cells))[col])))
            continue

        # Coordinate-based mapping is authoritative for real PDF tables.
        # For example, a box spanning columns 5000 and 5000S applies to both.
        # A blank cell with no geometrical colspan is NEVER copied.
        mapping: list[tuple[int, str, str]] = []
        boxes = row.get("cell_boxes") or []
        for col in candidates:
            assigned = []
            method = "explicit_table_header"
            if centers and col < len(boxes) and boxes[col]:
                x0, _, x1, _ = boxes[col]
                assigned = [headers[hcol] for hcol, x in centers.items() if x0 - 1 <= x <= x1 + 1]
                if len(assigned) > 1:
                    method = "merged_geometry"
            if not assigned and col in headers:
                assigned = [headers[col]]
            if not assigned and len(headers) == 1 and len(filename_candidates) == 2:
                # One OCR header may be unreadable; map by the filename ONLY
                # when the other header agrees with the position and exactly
                # two value columns follow the unit cell.
                other_col, other_model = next(iter(headers.items()))
                if len(cells) - value_start == 2 and other_col in (value_start, value_start + 1):
                    other_position = other_col - value_start
                    if filename_candidates[other_position] == other_model:
                        position = col - value_start
                        if 0 <= position < 2:
                            assigned = [filename_candidates[position]]
                            method = "partial_ocr_header_filename_candidate"
            if not assigned and not headers:
                # Never map a multi-model PDF by column index unless this very
                # table names the models. Controls/marketing tables can contain
                # equally many unrelated columns (NHP S-ONE false fact).
                if len(filename_candidates) == 1 and len(candidates) == 1:
                    assigned = filename_candidates[:]
                    method = "single_filename_model"
                elif len(page_models.get(row["page"], [])) == 1 and len(candidates) == 1 and not filename_candidates:
                    assigned = page_models[row["page"]][:]
                    method = "single_page_model"
            for name in dict.fromkeys(assigned):
                if name not in trusted_models:
                    review.append(review_entry(path, row, "model_not_confirmed", [name], parameter=key))
                    continue
                mapping.append((col, name, method))

        mapped_models = {model for _,model,_ in mapping}
        if len(headers) >= 2:
            for col, name in headers.items():
                if name in mapped_models or col < value_start or col >= len(cells):
                    continue
                review.append(review_entry(path, row, "missing_model_value", [name],
                                           parameter=key, model=name, expected_col=col,
                                           unit=unit, original_cell=norm(row['cells'][col]),
                                           cell_bbox=((row.get('cell_boxes') or [None]*len(cells))[col])))
        if not mapping:
            review.append(review_entry(path, row, "model_column_mapping_unresolved", models_context,
                                       parameter=key, unit=unit))
            continue
        mapped_cols = set()
        accepted_models = set()
        for col, model, method in mapping:
            raw_value = norm(row["cells"][col])
            val = norm(cells[col])
            recovery = ("table_load_context_mass_pair_recovered" if col in load_candidate_values else
                        "cell_ocr_consensus_candidate" if col in ocr_recoveries else "")
            if key == "table_work_inertia_nm2" and not recovery:
                val, recovery = recover_inertia_value(raw_value)
            if not recovery:
                val, recovery = recover_primary_metric(key, raw_value, unit)
            conflict = invalid_unit(key, unit, val)
            if row.get("ocr_geometry"):
                if re.match(r"^\s*[(/]\s*\d", val):
                    conflict = conflict or "ocr_primary_measurement_missing"
                if val.count("(") != val.count(")") or val.count("{") != val.count("}"):
                    conflict = conflict or "ocr_unbalanced_delimiters"
            if conflict:
                review.append(review_entry(path, row, conflict, [model], parameter=key,
                                           value=raw_value, unit=unit))
                continue
            label = " / ".join(c for c in cells[:value_start] if c)
            if rapid_reconstructed:
                review.append(review_entry(path, row, 'rapid_traverse_header_inferred', [model],
                                           parameter=key, value=raw_value,
                                           instruction='Verify header in source PDF before marking confirmed'))
            duty = duty_condition(label)
            condition = ""
            if key == "tool_max_diameter_mm":
                if re.search(r"contin(?:u)?ous", detail, re.I):
                    condition = "continuous_adjacent_tools"
                elif re.search(r"without", detail, re.I):
                    condition = "without_adjacent_tools"
            if key == "tool_change_time_sec":
                if re.search(r"tool[- ]?to[- ]?tool", label, re.I):
                    condition = "tool_to_tool"
                elif re.search(r"chip[- ]?to[- ]?chip", label, re.I):
                    condition = "chip_to_chip"
            if key in {"cutting_feed_m_min", "rapid_traverse_m_min", "rapid_traverse_x_m_min",
                       "rapid_traverse_y_m_min", "rapid_traverse_z_m_min"}:
                head_num = re.match(r"^\s*(\d{4,})", val)
                if head_num and int(head_num.group(1)) > 1000 and re.search(r"m/min", unit, re.I):
                    review.append(review_entry(path, row, "possible_feedrate_unit_anomaly", [model],
                                               parameter=key, value=raw_value, unit=unit))
                    continue
            bad_imperial = metric_imperial_anomaly(key, val, unit)
            if bad_imperial:
                review.append(review_entry(path, row, "metric_imperial_mismatch", [model],
                                           parameter=key, value=raw_value, unit=unit))
            if recovery:
                review.append(review_entry(path, row, recovery, [model],
                                           parameter=key, value_raw=raw_value,
                                           value_recovered=val, unit=unit))
            base, options, option_kind = parse_options(val)
            facts.append({"source_file": path.name, "source_page": row["page"],
                          "source_table": row["table"], "source_row": row["row"],
                          "source_row_id": f"{row['page']}:{row['table']}:{row['row']}",
                          "model": model, "parameter": key, "value": val, "value_raw": raw_value,
                          "base_value": base, "options": options, "option_kind": option_kind,
                          "unit_expected": expected_unit, "unit_source": unit,
                          "condition": condition, "duty_cycle": duty,
                          "duty_values": duty_values_from_power(val, duty) if key == 'spindle_power_kw' else {},
                          "cell_bbox": ((row.get('cell_boxes') or [None]*len(cells))[col]),
                          "ocr_evidence": ocr_recoveries.get(col, {}),
                          "source_label": label,
                          "source_quote": " | ".join(row["cells"]) + (
                              " | adjacent mass option row " + load_option_rows[col]
                              if col in load_option_rows else ""),
                          "source_extra_row_ids": ([load_option_rows[col]] if col in load_option_rows else []),
                          "method": "local_table:" + method,
                          "scope": "shared_merged_cell" if method == "merged_geometry" else "individual_cell",
                          "recovery_note": recovery or ('rapid_traverse_header_inferred' if rapid_reconstructed else ''),
                          "review_status": ("recovered_needs_review" if (recovery or rapid_reconstructed) else
                                             "unit_mismatch" if bad_imperial else "needs_review")})
            mapped_cols.add(col)
            accepted_models.add(model)
        if len(headers) >= 2:
            for col, name in headers.items():
                if name in accepted_models or col < value_start or col >= len(row['cells']):
                    continue
                # This includes failed validation, in addition to blank PDF
                # cells. All of these are observable coverage gaps.
                if not any(x.get('reason') == 'missing_model_value' and
                           x.get('row_id') == f"{row['page']}:{row['table']}:{row['row']}" and
                           x.get('model') == name for x in review):
                    review.append(review_entry(path, row, "missing_model_value", [name],
                                               model=name, parameter=key, expected_col=col,
                                               unit=unit, original_cell=norm(row['cells'][col]),
                                               cell_bbox=((row.get('cell_boxes') or [None]*len(cells))[col])))
        if any(col not in mapped_cols for col in candidates):
            review.append(review_entry(path, row, "partial_model_mapping", models_context, parameter=key))
    return facts, review


def text_model_from_line(line: str, prefix: str):
    names = candidate_models(line, prefix)
    return names if len(names) == 1 and len(line) < 100 else []


def extract_text_page(path: Path, rows: list[dict], page_models: dict, filename_candidates: list[str], prefix: str, trusted_models: dict):
    facts, review = [], []
    heading = ""
    # For text-only specs pages, a single explicit model can be scoped locally.
    models_here = list(dict.fromkeys(page_models.get(rows[0]["page"], []) if rows else []))
    if not models_here and len(filename_candidates) == 1:
        models_here = filename_candidates[:]
    for row_index, row in enumerate(rows):
        line = row["cells"][0]
        if not line:
            continue
        names = [x for x in text_model_from_line(line, prefix) if x in trusted_models]
        if names and ("specification" in line.lower() or line.strip().upper() in {x.upper() for x in names}):
            models_here = names
        new_heading = section_heading([line])
        if new_heading:
            heading = new_heading
        elif re.search(r"(?:travels?|travel distance|stroke|行程)", line, re.I) and re.search(r"\b[xyz][- ]?axis\b", line, re.I):
            heading = "Travel distance"
        elif re.search(r"rapid\s+(?:traverse|feed)", line, re.I) and re.search(r"\b[xyz][- ]?axis\b", line, re.I):
            heading = "Rapid traverse rate"
        elif re.search(r"(?:feedrate|feed rate)", line, re.I) and re.search(r"\b[xyz][- ]?axis\b", line, re.I):
            heading = "Feedrate"
        # Explicit section words can reset stale Travel/Feedrate inference.
        elif re.match(r"^(?:table|spindle|magazine|automatic tool|coolant|tank capacity)", line, re.I):
            heading = ""
        match = parameter(line, heading)
        if not NUMBER_RE.search(line):
            # Power labels can contain NO numerical measurement: their Fanuc
            # and H/H values are on adjacent lines. Preserve those variants.
            neighbors = []
            if match and match[0] in {"spindle_power_kw", "spindle_torque_nm"}:
                for j in (row_index - 1, row_index + 1):
                    if 0 <= j < len(rows) and re.match(
                            r"^(?:fanuc|h/h|heidenhain|siemens|mitsubishi)\s*:\s*\d",
                            rows[j]["cells"][0], re.I):
                        neighbors.append(rows[j]["cells"][0])
            has_control_value = bool(match and match[0] == "control_system" and
                                     re.search(r"fanuc|siemens|heidenhain|sinumerik|cufos", line, re.I))
            if not neighbors and not has_control_value:
                continue
        if not match:
            if numeric_or_spec_row([line], False):
                review.append(review_entry(path, row, "unrecognized_text", models_here or filename_candidates))
            continue
        key, expected_unit = match
        # Explicit key and inline value after ':' or 'unit' are easier to parse than prose.
        # Never extract a collection of numerical chart ticks as spindle speeds.
        trimmed = norm(line)
        likely_chart = re.search(r"spindle\s+speed\s*:\s*r/min\s+\d+\s+\d+\s+\d+", trimmed, re.I)
        if likely_chart:
            review.append(review_entry(path, row, "possible_chart_axis", models_here or filename_candidates, parameter=key))
            continue
        if len(models_here) != 1:
            review.append(review_entry(path, row, "text_model_ambiguous", models_here or filename_candidates, parameter=key))
            continue
        # For text rows, keep the entire labeled row rather than risking incorrect
        # splitting of multi-values, power duty variants or option braces.
        adjacent = []
        separated = re.search(r"\s+(?:mm\s*\(inch\)|kg\s*\(lb\)|kW\s*\(Hp\)|r/min|rpm|mm|kg|kW|N[·.]?m|m/min|ea|pcs|sec|L)(?:\s*\([^)]{1,32}\))?\s+(.+)$", trimmed, re.I)
        if separated and right_value_cell(separated.group(1).strip()):
            val = separated.group(1).strip()
        elif ":" in trimmed and not re.match(r"^\s*(?:Fanuc|Siemens|H/H)\s*:", trimmed, re.I):
            val = trimmed.split(":", 1)[1].strip()
        else:
            # Some catalogues place the configuration-specific values on
            # BOTH sides of the labelled row (e.g. Fanuc / H/H power).
            # Keep the variants together; NEVER reinterpret braces as duty.
            adjacent = []
            if key in {"spindle_power_kw", "spindle_torque_nm"}:
                for j in (row_index - 1, row_index + 1):
                    if 0 <= j < len(rows):
                        line_neighbor = rows[j]["cells"][0]
                        if re.match(r"^(?:fanuc|h/h|heidenhain|siemens|mitsubishi)\s*:\s*\d", line_neighbor, re.I):
                            adjacent.append(line_neighbor)
            if adjacent:
                val = " | ".join(adjacent)
            else:
                review.append(review_entry(path, row, "text_value_boundary_unclear", models_here, parameter=key))
                continue
        if len(val) > 210 or not val or not right_value_cell(val):
            review.append(review_entry(path, row, "text_value_boundary_unclear", models_here, parameter=key))
            continue
        text_problem = invalid_unit(key, "", val)
        if text_problem:
            review.append(review_entry(path, row, text_problem, models_here, parameter=key, value=val))
            continue
        duty = re.search(r"\((?:S[136](?:\s*[/+]\s*S[136])?|S[136]\s*[/+]\s*Cont\.?|30\s*min|continuous|cont\.)\)", trimmed, re.I)
        base_value, option_values, option_kind = parse_options(val)
        facts.append({"source_file": path.name, "source_page": row["page"],
                      "source_table": row["table"], "source_row": row["row"],
                      "source_row_id": f"{row['page']}:{row['table']}:{row['row']}",
                      "model": models_here[0], "parameter": key, "value": val,
                      "value_raw": val, "base_value": base_value,
                      "options": option_values, "option_kind": option_kind,
                      "unit_expected": expected_unit, "unit_source": "",
                      "condition": "", "duty_cycle": duty_condition(trimmed),
                      "source_label": trimmed, "source_quote": trimmed + (" | " + val if adjacent else ""),
                      "method": "local_text_adjacent" if adjacent else "local_text",
                      "scope": "explicit_single_model_text",
                      "recovery_note": "", "review_status": "needs_review"})
    return facts, review


def evidence_rank(fact: dict) -> int:
    method = fact.get("method", "")
    rank = (100 if "merged_geometry" in method else
            95 if "explicit_table_header" in method else
            75 if "partial_ocr_header" in method else
            70 if "single_filename_model" in method else
            55 if "single_page_model" in method else
            35 if "filename_order_candidate" in method else
            30 if method.startswith("local_text") else 20)
    if fact.get("recovery_note"):
        rank -= 8
    return rank


def dedupe_facts(facts: list[dict]):
    unique = {}
    for fact in sorted(facts, key=lambda f: -evidence_rank(f)):
        key = (fact["source_file"], fact["model"], fact["parameter"],
               norm(fact["value"]).lower(), fact.get("condition", ""),
               fact.get("duty_cycle", ""))
        unique.setdefault(key, fact)
    return list(unique.values())


def resolve_quality_conflicts(path: Path, facts: list[dict], review: list[dict]):
    """No contradictory facts in CSV; never blend measurements across pages.

    A faster tool-to-tool than chip-to-chip measurement is expected for the
    same ATC. A suspicious OCR integer such as 13 vs chip 3.3 is BLOCKED,
    not rewritten as 1.3. Contradictory page-specific claims require review.
    """
    facts = dedupe_facts(facts)
    blocked_ids = set()
    by_model_page = defaultdict(list)
    for fact in facts:
        by_model_page[(fact['model'], fact.get('source_page'), fact.get('source_table'))].append(fact)
    for (model, page, table), group in by_model_page.items():
        times = {f.get('condition'): f for f in group
                 if f.get('parameter') == 'tool_change_time_sec'
                 and f.get('condition') in {'tool_to_tool', 'chip_to_chip'}}
        if len(times) == 2:
            tool = times['tool_to_tool']
            chip = times['chip_to_chip']
            try:
                t = float(str(tool['value']).replace(',', '.'))
                c = float(str(chip['value']).replace(',', '.'))
            except ValueError:
                continue
            if t > c:
                blocked_ids.add(id(tool))
                review.append({"source_file": path.name, "source_page": page,
                               "table": table, "row": tool.get('source_row', 0),
                               "row_id": tool.get('source_row_id', ''),
                               "models_context": model,
                               "reason": "tool_to_tool_exceeds_chip_to_chip",
                               "parameter": "tool_change_time_sec", "condition": "tool_to_tool",
                               "value": tool['value'], "paired_chip_to_chip": chip['value'],
                               "cells": [tool.get('source_quote','')],
                               "instruction": "Review against source image; never change 13 to 1.3 automatically."})
    remaining = [f for f in facts if id(f) not in blocked_ids]
    groups = defaultdict(list)
    for fact in remaining:
        key = (fact['source_file'], fact['model'], fact['parameter'],
               fact.get('condition',''), fact.get('duty_cycle',''))
        groups[key].append(fact)
    result = []
    for (_, model, key, condition, duty), candidates in groups.items():
        if len(candidates) == 1:
            result.extend(candidates)
            continue
        # Same value on different pages: keep only the strongest provenance.
        alternatives = defaultdict(list)
        for fact in candidates:
            alternatives[norm(fact['value']).casefold()].append(fact)
        if len(alternatives) == 1:
            original_selected = max(candidates, key=evidence_rank)
            selected = dict(original_selected)
            selected['duplicate_sources'] = [f.get('source_row_id','') for f in candidates if f is not original_selected]
            result.append(selected)
            continue
        ranked = sorted(candidates, key=evidence_rank, reverse=True)
        best, runner_up = ranked[0], ranked[1]
        winner = best if evidence_rank(best) - evidence_rank(runner_up) >= 20 else None
        for fact in ranked:
            if winner is not None and fact is winner:
                continue
            review.append({"source_file": path.name, "source_page": fact.get('source_page', 0),
                           "table": fact.get('source_table',0), "row": fact.get('source_row',0),
                           "row_id": fact.get('source_row_id',''), "models_context": model,
                           "reason": "cross_page_conflict" if len({f.get('source_page') for f in candidates})>1
                                     else "same_page_parameter_conflict",
                           "parameter": key, "condition": condition, "duty_cycle": duty,
                           "value": fact['value'],
                           "competing_values": [{'page': x.get('source_page'), 'value': x['value'],
                                                 'source_row_id': x.get('source_row_id'),
                                                 'method': x.get('method')}
                                                for x in candidates],
                           "cells": [fact.get('source_quote','')],
                           "instruction": "Do not concatenate conflicting measurements from different sources."})
        if winner is not None:
            result.append(winner)
    return dedupe_facts(result), dedupe_review(review)


def dedupe_review(review: list[dict]):
    out = {}
    for row in review:
        signature = (row.get("source_file"), row.get("row_id"),
                     row.get("reason"), row.get("parameter"),
                     row.get("models_context"), row.get("condition"))
        out[signature] = row
    return list(out.values())


def recover_source_aligned_mynx_ii(path: Path, registry: dict):
    """Recover words, including colspans, from the verified 7-model Mynx II layout.

    This is deliberately SOURCE VERSION GATED. It does not copy values from
    other models or claim to be a universal OCR rule. The generic model/header
    fixes and table geometry fallback continue to work on other catalogues.
    Source notes document inconsistent units printed on catalogue page 18.
    """
    if (not path.is_file() or path.name != 'dn-solutions-vertical-machining-center-mynx-ii-series.pdf'
        or hash_file(path) != 'bbfdec06e81515e1946fe7dcb909e5ef303f2e842be1d41e9c32a02c4c13d37f'):
        return None
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) < 18:
            return None
        page = pdf.pages[17]
        tables = page.find_tables()
        if not tables:
            return None
        table = next((t for t in tables if len(t.extract()[0]) >= 11), None)
        if table is None:
            return None
        header = table.extract()[0]
        model_cols = [(i, candidate_models(norm(cell))[0]) for i, cell in enumerate(header)
                      if len(candidate_models(norm(cell))) == 1 and
                      candidate_models(norm(cell))[0] in registry]
        if len(model_cols) != 7:
            return None
        wordlist = page.extract_words(x_tolerance=1.8,y_tolerance=2.8)
        cellboxes = table.rows[0].cells
        vl = [l for l in page.lines if l['height'] > 4 and l['width'] < .45]

        # Relative to the native PDF text layer, not a screenshot/OCR. Each
        # window is a row (or a wrapped row) in THIS catalogue edition.
        # No value literals are inserted anywhere in the recovery schema.
        specs = [
          ('travel_x_mm', 142,151,'','mm'),
          ('travel_y_mm',153,163,'','mm'),
          ('travel_z_mm',164,173,'','mm'),
          ('spindle_nose_to_table_top_mm',175,191,'','mm'),
          ('table_size_mm',194,210,'','mm'),
          ('table_load_kg',213,223,'','kg'),
          ('table_t_slot',225,240,'',''),
          ('spindle_speed_rpm',243,252,'drive_direct','r/min'),
          ('spindle_speed_rpm',254,270,'drive_belt','r/min'),
          ('spindle_speed_rpm',274,284,'drive_gear','r/min'),
          ('spindle_speed_rpm',285,296,'drive_builtin','r/min'),
          ('spindle_taper',297,307,'',''),
          ('spindle_power_kw',308,327,'drive_direct','kW'),
          ('spindle_power_kw',328,369,'drive_belt','kW'),
          ('spindle_power_kw',370,387,'drive_gear','kW'),
          ('spindle_power_kw',390,406,'drive_builtin','kW'),
          ('spindle_torque_nm',409,427,'drive_direct','N*m'),
          ('spindle_torque_nm',428,461,'drive_belt','N*m'),
          ('spindle_torque_nm',464,475,'drive_gear','N*m'),
          ('spindle_torque_nm',476,485,'drive_builtin','N*m'),
          ('rapid_traverse_x_m_min',487,497,'','m/min'),
          ('rapid_traverse_y_m_min',498,509,'','m/min'),
          ('rapid_traverse_z_m_min',510,520,'','m/min'),
          ('tool_shank_type',521,538,'',''),
          ('tool_pull_stud',539,557,'',''),
          ('tool_capacity',559,570,'','pcs'),
          ('tool_max_diameter_mm',571,583,'continuous_adjacent_tools','mm'),
          ('tool_max_diameter_mm',584,597,'without_adjacent_tools','mm'),
          ('tool_max_length_mm',601,611,'','mm'),
          ('tool_max_weight_kg',613,623,'','kg'),
          ('tool_max_moment_nm',624,635,'','N*m'),
          ('tool_selection_type',636,645,'',''),
          ('tool_change_time_sec',646,656,'tool_to_tool','sec'),
          ('tool_change_time_sec',658,669,'chip_to_chip','sec'),
          ('power_supply_kva',670,681,'drive_direct','kVA'),
          ('power_supply_kva',682,700,'drive_belt','kVA'),
          ('power_supply_kva',701,711,'drive_gear','kVA'),
          ('power_supply_kva',712,721,'drive_builtin','kVA'),
          ('air_pressure_mpa',723,734,'','MPa'),
          ('coolant_tank_l',734,746,'','L'),
          ('machine_height_mm',747,763,'','mm'),
          ('machine_length_mm',765,775,'','mm'),
          ('machine_width_mm',777,785,'','mm'),
          ('machine_weight_kg',787,797,'','kg'),
          ('control_system',799,808,'',''),
        ]
        facts, reviews = [], []
        def joined(ws):
            result, prev_y = [], None
            for w in sorted(ws, key=lambda v:(round(v['top'],1),v['x0'])):
                if prev_y is not None and abs(w['top'] - prev_y)>3.7:
                    result.append('|')
                result.append(w['text'])
                prev_y=w['top']
            return norm(' '.join(result)).replace(' | ', ' / ')

        for row_num,(key,lo,hi,condition,unit) in enumerate(specs):
            # A number from source's explicit unit-typo rows is preserved,
            # but the incompatible secondary imperial unit is not treated as
            # a trustworthy converted measurement.
            relevant = [w for w in wordlist if lo <= w['top'] <= hi and
                        table.bbox[0] <= (w['x0']+w['x1'])/2 <= table.bbox[2] and
                        (w['x0']+w['x1'])/2 >= cellboxes[model_cols[0][0]][0]]
            if key=='control_system' or key=='tool_selection_type':
                shared=joined(relevant)
                if not shared:
                    continue
                groups=[(model,shared,cellboxes[i],True) for i,model in model_cols]
            else:
                group_words={i:[] for i,_ in model_cols}
                for w in relevant:
                    cx=(w['x0']+w['x1'])/2
                    for i,model in model_cols:
                        box=cellboxes[i]
                        if box[0] <= cx <= box[2]:
                            group_words[i].append(w)
                            break
                # Respects actual source columns even when native PDF table
                # extraction leaves their content blank.
                groups=[]
                start_idx=0
                while start_idx<len(model_cols):
                    end_idx=start_idx
                    while end_idx+1<len(model_cols):
                        x=cellboxes[model_cols[end_idx][0]][2]
                        border_present=any(abs(v['x0']-x)<.7 and
                                           v['top']<=(lo+hi)/2 and v['bottom']>=(lo+hi)/2
                                           for v in vl)
                        if border_present:
                            break
                        end_idx+=1
                    group_indices=[model_cols[j][0] for j in range(start_idx,end_idx+1)]
                    texts={i:joined(group_words[i]) for i in group_indices}
                    nonempty=[i for i,v in texts.items() if v and v.strip(' -/|')]
                    # When different full numeric readings appear in distinct
                    # columns, they are independent even if the source drew
                    # no vertical separator on that particular row.
                    independents=(len(nonempty)>1 and all(
                        re.search(r'\d',texts[i]) and not texts[i].strip().startswith('(')
                        for i in nonempty))
                    if independents:
                        for j in range(start_idx,end_idx+1):
                            i,model=model_cols[j]
                            groups.append((model,texts[i],cellboxes[i],False))
                    else:
                        shared=joined([w for i in group_indices for w in group_words[i]])
                        for j in range(start_idx,end_idx+1):
                            i,model=model_cols[j]
                            groups.append((model,shared,[cellboxes[group_indices[0]][0],lo,
                                                         cellboxes[group_indices[-1]][2],hi],
                                           end_idx>start_idx))
                    start_idx=end_idx+1
            for model,raw,box,is_shared in groups:
                raw=norm(raw)
                if not raw or re.fullmatch(r'(?:-\s*/?\s*)+',raw):
                    continue
                if key not in {'control_system','tool_selection_type','table_t_slot',
                               'spindle_taper','tool_shank_type','tool_pull_stud'} and not re.search(r'\d',raw):
                    continue
                value=raw
                note='native_pdf_word_geometry_source_profile'
                if key in ('rapid_traverse_x_m_min','rapid_traverse_y_m_min','rapid_traverse_z_m_min'):
                    # Page 18 incorrectly labels feed rate N*m(ft-lbs), while
                    # PDF page 4 explicitly confirms m/min, 30/30/24 and 16/16/16.
                    num=re.search(r'(?<![a-zA-Z])\d+(?:\.\d+)?',value)
                    if not num:
                        continue
                    value=num.group()
                    note+='; original_page18_unit_misprinted_page4_confirms_m_min'
                if key=='table_load_kg':
                    note+='; original_page18_unit_kW_Hp_misprinted; page5_indicates_kg'
                    measurement=re.match(r'\s*(\d+(?:\.\d+)?)',value)
                    if not measurement:
                        continue
                    value=measurement.group(1)
                # Remove visual newline delimiters, preserve source braces.
                value=norm(value.replace(' / ', ' '))
                # A tool shank span should not incorporate adjacent Z-axis
                # feed figures; this window starts after the Z row.
                if key=='tool_shank_type':
                    value=value.replace('BT 40','BT40').replace('BT 50','BT50')
                base,options,option_kind=parse_options(value)
                facts.append({'source_file':path.name,'source_page':18,'source_table':1,
                    'source_row':row_num,'source_row_id':f'18:geometry:{row_num}',
                    'model':model,'parameter':key,'value':value,'value_raw':raw,
                    'base_value':base,'options':options,'option_kind':option_kind,
                    'unit_expected':unit,'unit_source':('N*m (ft-lbs)' if key.startswith('rapid_traverse')
                        else 'kW (Hp)' if key=='table_load_kg' else unit),
                    'condition':condition,'duty_cycle':'','duty_values':{},
                    'cell_bbox':box,'ocr_evidence':{},
                    'source_label':key.replace('_',' '),
                    'source_quote':f'Page 18; vertical word band {lo}..{hi}: {raw}',
                    'method':'local_table:source_word_geometry',
                    'scope':'shared_merged_cell' if is_shared else 'individual_cell',
                    'recovery_note':note,'review_status':'recovered_needs_review'})
        reviews.append({'source_file':path.name,'source_page':18,'table':1,'row':0,
            'row_id':'18:geometry','models_context':', '.join(x for _,x in model_cols),
            'reason':'native_pdf_table_grid_repaired_from_words',
            'cells':[], 'detail': 'Known source revision layout: 7 models, all values taken from original PDF word coordinates; source printed incorrect units for table load and rapid traverse; verify options/drive configuration.'})
        return facts,reviews


def local_extract(path: Path, source: list[dict], rechecker=None, progress=None):
    registry, page_models = build_model_registry(path, source)
    prefix = preferred_prefix(path)
    models_in_filename = [x for x in filename_models(path.stem)
                          if x.startswith(prefix + " ")]
    groups = defaultdict(list)
    for row in source:
        groups[(row["page"], row["table"])].append(row)
    facts, review = [], []
    own_checker = rechecker is None
    checker = rechecker if rechecker is not None else (LocalCellOCR(path) if LOCAL_CELL_OCR else None)
    # Spend the limited per-cell OCR budget on comparative specification
    # tables first, not decorative tables and charts early in the catalog.
    def group_priority(item):
        (page_no, table_id), rows = item
        if not table_id:
            return (2, page_no, table_id)
        headers = table_headers(rows, prefix)
        confirmed = sum(name in registry for name in headers.values())
        technical = sum(bool(re.search(r"(?:travel|spindle|tool|table|motor|power|rapid|feedrate)",
                                      " ".join(r['cells'][:3]), re.I)) for r in rows)
        return (0 if confirmed >= 2 and technical >= 5 else 1, -technical, page_no, table_id)
    ordered_groups = sorted(groups.items(), key=group_priority)
    if progress is not None:
        progress(0, max(1, len(ordered_groups)), "начало разбора")
    try:
        for group_index, ((page_number, table_id), rows) in enumerate(ordered_groups, 1):
            if table_id:
                f, r = extract_single_table(path, rows, models_in_filename, page_models, prefix,
                                            registry, rechecker=checker)
            else:
                f, r = extract_text_page(path, rows, page_models, models_in_filename, prefix, registry)
            facts.extend(f)
            review.extend(r)
            if progress is not None:
                progress(group_index, max(1, len(ordered_groups)),
                         f"группа {group_index}/{len(ordered_groups)}, стр. {page_number}")
    finally:
        if own_checker and checker is not None:
            checker.close()
    # A new model created by an explicit table/header must not be omitted.
    for fact in facts:
        if fact["model"] in registry:
            continue
        # Never manufacture a new model from an OCR-only line.
        if fact["method"].startswith("local_table"):
            registry.setdefault(fact["model"], {"model": fact["model"], "evidence": "extracted_fact"})
    source_profile = recover_source_aligned_mynx_ii(path, registry) if path.suffix.lower()=='.pdf' else None
    if source_profile is not None:
        recovered, annotations = source_profile
        # Native table segmentation was demonstrably corrupted. Keep all
        # other pages but replace the broken comparison-table facts only.
        facts = [f for f in facts if not (f.get('source_page')==18 and f.get('source_table')==1)
                 and not (f.get('source_page') in (13,17) and f.get('method','').startswith('local_text'))]
        facts.extend(recovered)
        review.extend(annotations)
    facts, review = resolve_quality_conflicts(path, facts, review)
    return facts, review, registry


def category(key: str):
    if key in {"controlled_axes", "simultaneously_controlled_axes"}:
        return "Control"
    if key.startswith(("travel_", "axis_travel", "spindle_nose_to_table_", "positioning_accuracy", "repeatability")):
        return "Travel"
    if key.startswith(("table_", "pallet_", "worktable_", "max_workpiece_size")):
        return "Table"
    if key.startswith(("spindle_", "main_spindle_", "left_spindle_", "right_spindle_", "milling_spindle_", "chuck_")):
        return "Spindle"
    if key.startswith(("rapid_", "feed", "cutting_feed", "axis_acceleration", "acceleration")):
        return "Feedrate"
    if key.startswith(("tool_", "atc_", "magazine_")):
        return "Automatic tool changer (ATC)"
    if key.startswith(("control_", "cnc_", "nc_", "controller_")):
        return "Control"
    if key.startswith(("coolant_pump_motor", "motor_")):
        return "Motor"
    if key.startswith(("power_supply", "electric_", "air_pressure", "air_consumption")):
        return "Power source"
    if key.startswith(("coolant_tank", "tank_")):
        return "Tank capacity"
    if key.startswith(("machine_height", "machine_width", "machine_length", "machine_weight", "machine_dimension")):
        return "Machine dimensions"
    return "Other"


def export_csv(path: Path, rows: list[dict], columns: list[str]):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def output_model_status(enriched: list[dict], all_review: list[dict],
                        wide: list[dict], technical: list[str]):
    """A blank CSV cell is not evidence that a feature is physically absent.

    FOUND_UNVERIFIED - recorded fact, not independently checked.
    RECOVERED_REVIEW_REQUIRED - OCR or conflicting-label interpretation.
    REVIEW_REQUIRED - a model-specific candidate was rejected.
    NOT_FOUND - series has the field, but this model does not.
    NOT_LISTED_FOR_SERIES - union-wide field appears in other series only.
    NOT_APPLICABLE - *only* a literal N/A from this model's source cell.
    """
    def series(model):
        return model.split()[0].upper() if model.split() else model
    sources = {(r['model'], r['source_file']): r for r in wide}
    found = defaultdict(list)
    series_keys = defaultdict(set)
    for f in enriched:
        found[(f['model'], f['source_file'], f['parameter'])].append(f)
        series_keys[series(f['model'])].add(f['parameter'])
    models = {(r['model'], r['source_file']) for r in wide}
    relevant_review = defaultdict(list)
    for r in all_review:
        key = r.get('suggested_parameter') or r.get('parameter')
        if not key:
            continue
        filename = r.get('source_file', '')
        explicit = r.get('model')
        mentioned = {x.strip() for x in norm(r.get('models_context', '')).split(',') if x.strip()}
        if explicit:
            mentioned = {explicit}
        for model in mentioned:
            if (model, filename) in models:
                relevant_review[(model, filename, key)].append(r)
                series_keys[series(model)].add(key)
    key_by_column = {f"[{category(param)}] {param}": param
                     for param in {f['parameter'] for f in enriched}}
    key_by_column.update({f"[{category(k)}] {k}": k for keys in series_keys.values() for k in keys})
    status_rows = []
    for machine in wide:
        model, filename = machine['model'], machine['source_file']
        for col in technical:
            key = key_by_column.get(col)
            if key is None:
                continue
            facts = found.get((model, filename, key), [])
            issues = relevant_review.get((model, filename, key), [])
            status = 'NOT_LISTED_FOR_SERIES'
            if facts:
                status = ('RECOVERED_REVIEW_REQUIRED' if any(f.get('recovery_note') or
                          f.get('review_status') in ('unit_mismatch', 'recovered_needs_review') for f in facts)
                          else 'FOUND_UNVERIFIED')
            elif issues:
                if any(re.fullmatch(r'\s*(?:n/?a|not\s+applicable)\s*',
                                    norm(r.get('original_cell','')), re.I) for r in issues):
                    status = 'NOT_APPLICABLE'
                else:
                    status = 'REVIEW_REQUIRED'
            elif key in series_keys[series(model)]:
                status = 'NOT_FOUND'
            all_reasons = '; '.join(sorted({str(r.get('reason','')) for r in issues if r.get('reason')}))
            cited = '; '.join(sorted({f"{f.get('source_page',0)}:{f.get('source_table',0)}:{f.get('source_row',0)}"
                                     for f in facts}))
            status_rows.append({'manufacturer': machine['manufacturer'],
                'machine_type': machine['machine_type'], 'model': model,
                'source_file': filename, 'category': category(key), 'parameter': key,
                'status': status, 'value': machine.get(col,''),
                'review_reasons': all_reasons, 'source_row_ids': cited})
    export_csv(OUTPUT / 'MODEL_FIELD_STATUS.csv', status_rows,
               BASE_COLUMNS + ['source_file','category','parameter','status','value',
                               'review_reasons','source_row_ids'])
    # Per-series views do not inherit irrelevant columns from other machine
    # families. A missing field can still appear if it had a review candidate.
    folder = OUTPUT / 'by_series'
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob('*_models.csv'):
        old.unlink()
    by_series = defaultdict(list)
    for machine in wide:
        by_series[series(machine['model'])].append(machine)
    for name, machines in by_series.items():
        selected = [col for col in technical if key_by_column.get(col) in series_keys[name]]
        export_csv(folder / f'{name}_models.csv', machines, BASE_COLUMNS + selected + ['source_file'])
    return status_rows


def write_outputs(documents: list[dict], router=None):
    all_facts = dedupe_facts([f for document in documents for f in document["facts"]])
    all_review = dedupe_review([r for document in documents for r in document["review"]])
    enriched = []
    for fact in all_facts:
        manufacturer, kind = model_info(fact["model"])
        enriched.append({**fact, "manufacturer": manufacturer, "machine_type": kind,
                         "category": category(fact["parameter"])})
    enriched.sort(key=lambda f: (f["manufacturer"], f["machine_type"], f["model"],
                                 ORDER.index(f["category"]), f["parameter"], f["source_page"]))
    long_columns = BASE_COLUMNS + ["category", "parameter", "value", "base_value", "options_json",
                                   "option_kind", "condition", "duty_cycle", "duty_values_json", "unit_expected", "unit_source",
                                   "source_file", "source_page", "source_table", "source_row", "source_row_id",
                                   "source_label", "source_quote", "value_raw", "cell_bbox_json", "ocr_evidence_json",
                                   "method", "scope", "recovery_note", "review_status"]
    for fact in enriched:
        fact['options_json'] = json.dumps(fact.get('options', []), ensure_ascii=False)
        fact['duty_values_json'] = json.dumps(fact.get('duty_values', {}), ensure_ascii=False)
        fact['cell_bbox_json'] = json.dumps(fact.get('cell_bbox'), ensure_ascii=False)
        fact['ocr_evidence_json'] = json.dumps(fact.get('ocr_evidence', {}), ensure_ascii=False)
    export_csv(OUTPUT / "ALL_MODELS_long.csv", enriched, long_columns)

    by_doc_model = {}
    for doc in documents:
        fact_models = {f["model"] for f in doc["facts"]}
        for name, meta in doc["registry"].items():
            if name not in fact_models and meta.get("evidence") in {"ocr_repeated", "ocr_table_header", "native_title"}:
                continue
            manufacturer, kind = model_info(name)
            key = (manufacturer, kind, name, doc["source_file"])
            by_doc_model[key] = {"manufacturer": manufacturer, "machine_type": kind,
                                 "model": name, "source_file": doc["source_file"]}
    for fact in enriched:
        key = (fact["manufacturer"], fact["machine_type"], fact["model"], fact["source_file"])
        row = by_doc_model.setdefault(key, dict(zip(["manufacturer", "machine_type", "model", "source_file"], key)))
        # Stable schema: the same parameter always has one column.
        # Store machine duty, option and tool-change subtype with the value;
        # never create a separate sparse column for every condition.
        col = f"[{fact['category']}] {fact['parameter']}"
        condition = norm(fact.get("condition", ""))
        base_value = norm(fact.get("base_value", fact["value"]))
        option_values = fact.get("options", [])
        display_value = base_value or fact["value"]
        if option_values:
            display_value += " [options: " + "; ".join(option_values) + "]"
        if condition:
            display_value += f" [condition: {condition}]"
        if fact.get('duty_cycle'):
            display_value += f" [duty: {fact['duty_cycle']}]"
        if fact.get('recovery_note'):
            display_value += " [REVIEW: OCR recovery]"
        existing = row.get(col, "")
        vals = existing.split(" || ") if existing else []
        if display_value not in vals:
            vals.append(display_value)
        row[col] = " || ".join(vals)
    wide = sorted(by_doc_model.values(), key=lambda r: (r["manufacturer"], r["machine_type"], r["model"]))
    technical = sorted({col for row in wide for col in row if col.startswith("[")},
                       key=lambda col: (next((i for i, cat in enumerate(ORDER) if col.startswith(f"[{cat}]")), 999), col))
    export_csv(OUTPUT / "ALL_MODELS_wide.csv", wide,
               BASE_COLUMNS + [col for col in technical if col not in HIDDEN_WIDE_COLUMNS] + ["source_file"])
    # A parameter present ONLY in review still needs a visible status even
    # when no machine was assigned a trustworthy fact for it.
    candidate_keys = {r.get('suggested_parameter') or r.get('parameter')
                      for r in all_review if (r.get('model') or r.get('models_context'))
                      and (r.get('suggested_parameter') or r.get('parameter'))}
    status_columns = sorted(set(technical) | {f"[{category(key)}] {key}" for key in candidate_keys},
                            key=lambda col: (next((i for i, cat in enumerate(ORDER)
                                        if col.startswith(f'[{cat}]')), 999), col))
    status_rows = output_model_status(enriched, all_review, wide, status_columns)
    options_rows = []
    for fact in enriched:
        for option in fact.get("options", []):
            options_rows.append({"manufacturer": fact['manufacturer'], "machine_type": fact['machine_type'],
                                 "model": fact['model'], "parameter": fact['parameter'],
                                 "base_value": fact.get('base_value',''), "option_value": option,
                                 "option_kind": fact.get('option_kind',''),
                                 "condition": fact.get('condition',''), "duty_cycle": fact.get('duty_cycle',''),
                                 "source_file": fact['source_file'], "source_page": fact['source_page'],
                                 "source_row_id": fact.get('source_row_id',''),
                                 "review_status": fact.get('review_status','')})
    export_csv(OUTPUT / "ALL_MODELS_options.csv", options_rows,
               BASE_COLUMNS + ["parameter", "base_value", "option_value", "option_kind",
                               "condition", "duty_cycle", "source_file", "source_page",
                               "source_row_id", "review_status"])
    save_json(OUTPUT / "NEEDS_REVIEW.json", all_review)
    empty_or_bad = [r for r in all_review if r.get("reason") in {
        "missing_model_value", "empty_model_cell_ocr_unresolved", "tool_capacity_unit_conflict",
        "tool_to_tool_exceeds_chip_to_chip", "spindle_taper_ocr_uncertain",
        "ocr_unbalanced_delimiters", "ocr_primary_measurement_missing",
        "no_structured_value", "non_numeric_measurement", "metric_imperial_mismatch",
        "metric_imperial_pair_mismatch", "metric_imperial_pair_invalid"}]
    export_csv(OUTPUT / "CELL_ISSUES.csv", [dict(r, cells=json.dumps(r.get("cells", []), ensure_ascii=False),
                  cell_bbox=json.dumps(r.get("cell_bbox"), ensure_ascii=False),
                  ocr_evidence=json.dumps(r.get("ocr_evidence", {}), ensure_ascii=False),
                  candidates_unverified=json.dumps(r.get("candidates_unverified", []), ensure_ascii=False))
                  for r in empty_or_bad],
               ["source_file", "source_page", "row_id", "models_context", "model", "parameter",
                "reason", "original_cell", "value", "unit", "expected_col", "cell_bbox", "ocr_evidence",
                "suggested_parameter", "candidates_unverified", "cells"])
    save_json(OUTPUT / "LOCAL_OCR_AUDIT.json",
              [{'record_type': 'attempt', 'source_file': doc['source_file'], **event}
               for doc in documents for event in doc.get('ocr_events', [])] +
              [{'record_type': 'review', **r} for r in all_review if
               r.get('reason') in {'cell_ocr_recovered_needs_review',
                                   'empty_model_cell_ocr_unresolved'}])
    save_json(OUTPUT / "RUN_SUMMARY.json", {
        "mode": MODE, "project_version": PROJECT_VERSION,
        "hidden_wide_columns": sorted(HIDDEN_WIDE_COLUMNS),
        "files": len(documents), "facts": len(enriched), "models": len(wide),
        "review_rows": len(all_review),
        "recovery_candidates": sum(bool(f.get('recovery_note')) for f in enriched),
        "optional_values": sum(len(f.get('options',[])) for f in enriched),
        "conflict_reviews": sum(r.get('reason') in {'tool_to_tool_exceeds_chip_to_chip',
            'cross_page_conflict', 'same_page_parameter_conflict', 'inertia_mass_contamination',
            'tool_capacity_unit_conflict'} for r in all_review),
        "local_only": True, "network_calls": 0,
        "missing_cell_issues": sum(r.get('reason') == 'missing_model_value' for r in all_review),
        "ocr_recovered_cells": sum(r.get('reason') == 'cell_ocr_recovered_needs_review' for r in all_review),
        "cell_issues": len(empty_or_bad),
        "unit_conflicts": sum(r.get('reason','').endswith('unit_conflict') for r in all_review),
        "suspected_table_load_rows": sum(r.get('suggested_parameter') == 'table_load_kg' for r in all_review),
        "table_load_recovered_unverified": sum(f.get('parameter')=='table_load_kg' and
             f.get('recovery_note')=='table_load_context_mass_pair_recovered' for f in enriched),
        "ocr_attempts": sum(len(doc.get('ocr_events', [])) for doc in documents),
        "model_field_status_counts": dict(__import__('collections').Counter(r['status'] for r in status_rows)),
        "series_exports": sorted({f"{r['model'].split()[0].upper()}_models.csv" for r in wide}),
    })
    return len(enriched), len(wide), len(all_review)


def main():
    if os.getenv("ANALYSIS_MODE", "local").strip().lower() != "local":
        raise SystemExit("v8.4 is LOCAL ONLY. Set ANALYSIS_MODE=local (Groq is disabled).")
    global OUTPUT
    if OUTPUT.exists():
        OUTPUT = new_run_directory(OUTPUT_BASE)
    INPUT.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=False)
    CACHE.mkdir(parents=True, exist_ok=True)
    paths = sorted(p for p in INPUT.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED)
    if not paths:
        raise SystemExit("No source PDF/DOCX/XLSX files. Put catalogues in the pdf/ directory.")
    print(f"PDF_SEARCH v8.4.2 LOCAL PATCH3; files: {len(paths)}; cell OCR: {LOCAL_CELL_OCR}; "
          f"max second-pass cells/file: {LOCAL_CELL_OCR_MAX}")
    tracker = RunProgress(OUTPUT, len(paths))
    documents = []
    failed = False
    for index, path in enumerate(paths):
        file_start = time.perf_counter()
        record = {"source_file": path.name, "cache_hit": False,
                  "read_seconds": 0.0, "extract_seconds": 0.0,
                  "validation_seconds": 0.0, "status": "ok"}
        tracker.file_timings.append(record)
        tracker.file_work(index, 0, "Открытие файла", filename=path.name, force=True)
        print(f"\nProcessing: {path.name}")
        try:
            read_start = time.perf_counter()
            local_key = hashlib.sha256((hash_file(path) + EXTRACTION_VERSION + OCR_LANG +
                                        str(LOCAL_CELL_OCR) + str(LOCAL_CELL_OCR_DPI) +
                                        str(LOCAL_CELL_OCR_MAX) + os.getenv("PDF_SEARCH_CACHE_BUST", "")).encode("utf-8")).hexdigest()
            cache_path = CACHE / f"{local_key}.json"
            if cache_path.exists():
                local = json.loads(cache_path.read_text(encoding="utf-8"))
                record["cache_hit"] = True
                print("  Local cache hit")
                tracker.file_work(index, 1, "Загрузка из кэша", filename=path.name)
            else:
                source = source_rows(path, progress=lambda done, total, detail:
                    tracker.file_work(index, 0.55 * done / max(1, total),
                                      "Чтение страниц", filename=path.name, detail=detail))
                record["read_seconds"] = round(time.perf_counter() - read_start, 3)
                tracker.file_work(index, 0.55, "Чтение завершено", filename=path.name)
                extract_start = time.perf_counter()
                cell_checker = LocalCellOCR(path) if LOCAL_CELL_OCR else None
                try:
                    facts, review, registry = local_extract(
                        path, source, rechecker=cell_checker,
                        progress=lambda done, total, detail:
                            tracker.file_work(index, 0.55 + 0.45 * done / max(1, total),
                                              "Разбор таблиц", filename=path.name, detail=detail))
                    events = list(cell_checker.events) if cell_checker is not None else []
                finally:
                    if cell_checker is not None:
                        cell_checker.close()
                record["extract_seconds"] = round(time.perf_counter() - extract_start, 3)
                local = {"facts": facts, "review": review, "registry": registry,
                         "ocr_events": events}
                save_json(cache_path, local)
                tracker.file_work(index, 1, "Файл обработан", filename=path.name)
            facts, review, registry = local["facts"], local["review"], local["registry"]
            print(f"  Models={len(registry)}; facts={len(facts)}; review={len(review)}")
            documents.append({"source_file": path.name, "source_path": path, "facts": facts,
                              "review": review, "registry": registry,
                              "ocr_events": local.get('ocr_events', [])})
        except Exception as exc:
            failed = True
            record["status"] = "failed"
            record["error"] = str(exc)
            print(f"  DOCUMENT ERROR: {exc}")
            documents.append({"source_file": path.name, "source_path": path, "facts": [],
                              "registry": {}, "review": [{"source_file": path.name, "source_page": 0,
                              "table": 0, "row": 0, "row_id": "0:0:0", "models_context": "",
                              "cells": [], "reason": "processing_error", "detail": str(exc)}]})
            tracker.file_work(index, 1, "Ошибка файла", filename=path.name, force=True)
        finally:
            record["processing_seconds"] = round(time.perf_counter() - file_start, 3)
    for index, doc in enumerate(documents):
        check_start = time.perf_counter()
        doc["facts"], doc["review"] = resolve_quality_conflicts(
            doc["source_path"], doc["facts"], doc["review"])
        save_json(OUTPUT / (Path(doc["source_file"]).stem + "_report.json"),
                  {k: v for k, v in doc.items() if k != "source_path"})
        tracker.file_timings[index]["validation_seconds"] = round(time.perf_counter() - check_start, 3)
        tracker.update(88 + 4 * (index + 1) / len(documents), "Проверка результатов",
                       filename=doc["source_file"], detail=f"{index + 1}/{len(documents)}")
    tracker.update(92, "Подготовка CSV", force=True)
    export_start = time.perf_counter()
    fact_count, model_count, reviews = write_outputs(documents)
    export_seconds = round(time.perf_counter() - export_start, 3)
    tracker.update(97, "Экспорт CSV завершён")
    save_json(OUTPUT / "AI_QUEUE_SUMMARY.json", {"version": "8.4.2", "mode": "local",
                                               "enabled": False, "api_calls": 0,
                                               "note": "No AI queue in v8.4 LOCAL."})
    tracker.update(99, "Запись отчётов")
    timing = tracker.finish(errors=failed)
    timing["export_seconds"] = export_seconds
    save_json(OUTPUT / "RUN_TIMING.json", timing)
    summary_path = OUTPUT / "RUN_SUMMARY.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["elapsed_seconds"] = timing["elapsed_seconds"]
    summary["duration_hms"] = timing["duration_hms"]
    summary["timing_report"] = "RUN_TIMING.json"
    summary["progress_log"] = "RUN_PROGRESS.jsonl"
    save_json(summary_path, summary)
    save_json(OUTPUT / "RUN_MANIFEST.json", {
        "project_version": PROJECT_VERSION,
        "run_id": OUTPUT.name,
        "folder_in_project": str(OUTPUT.relative_to(BASE)),
        "completed_with_errors": failed,
        "elapsed_seconds": timing["elapsed_seconds"],
        "source_files": [doc["source_file"] for doc in documents],
        "artifacts": sorted(str(p.relative_to(OUTPUT)) for p in OUTPUT.rglob("*") if p.is_file()),
    })
    pointer = OUTPUT_BASE / "LATEST_RUN.txt"
    pointer.write_text(str(OUTPUT.relative_to(BASE)) + "\n", encoding="utf-8")
    print(f"\nFacts: {fact_count}; models: {model_count}; review: {reviews}; Groq calls: 0")
    print(f"Общее время работы: {timing['duration_hms']} ({timing['elapsed_seconds']:.2f} c)")
    print("Отчёт о времени:", OUTPUT / "RUN_TIMING.json")
    print("История прогресса:", OUTPUT / "RUN_PROGRESS.jsonl")
    print("Results:", OUTPUT)
    print("Latest run pointer:", pointer)
    if failed:
        raise SystemExit("Some files failed. See output/NEEDS_REVIEW.json")

if __name__ == "__main__":
    main()
