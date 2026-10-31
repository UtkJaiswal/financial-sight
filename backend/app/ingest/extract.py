"""Format-specific extraction into one shape: header metadata, raw rows and per-page status.

Nothing is skipped silently. Every page gets a status, every row that cannot be read becomes a
quarantine entry with a reason, and rows that are only wrapped narration continue the previous row.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field

import pdfplumber
from openpyxl import load_workbook

from ..money import cell_currency, parse_date, to_paise
from .layout import KV_PATTERNS, Meta, infer_roles, meta_from_footer, meta_from_pairs, meta_from_text, roles_from_data


class NeedsPassword(Exception):
    pass


class Unreadable(Exception):
    pass


@dataclass
class Row:
    page_no: int
    row_no: int
    cells: list  # raw cells as read
    date: object = None
    description: str = ""
    ref: str = ""
    debit: int | None = None
    credit: int | None = None
    balance: int | None = None
    currency: str | None = None  # from a currency column
    symbol: str | None = None  # from the amount cell itself


@dataclass
class PageResult:
    page_no: int
    status: str  # parsed | partial | no_transactions | needs_ocr | failed
    reason: str
    n_rows: int = 0
    method: str = ""


@dataclass
class Extracted:
    meta: Meta
    layout: str
    rows: list[Row] = field(default_factory=list)
    pages: list[PageResult] = field(default_factory=list)
    quarantine: list[dict] = field(default_factory=list)


def _join_wrapped(m: re.Match) -> str:
    """A line break inside a PDF cell. Long tokens such as reference numbers wrap mid-token, so
    join without a space when the break falls between two digits or next to a slash."""
    a, b = m.group(1), m.group(2)
    return a + ("" if (a.isdigit() and b.isdigit()) or a == "/" or b == "/" else " ") + b


def _cell_text(c) -> str:
    if c is None:
        return ""
    return re.sub(r"(\S)[ \t]*\n[ \t]*(\S)", _join_wrapped, str(c)).strip()


def _rows_from_table(table: list[list], roles: dict[str, int], page_no: int, start_row: int, out: Extracted) -> tuple[int, int]:
    """Parse body rows with a known column mapping. Returns (rows parsed, rows quarantined)."""
    parsed = bad = 0
    for i, raw in enumerate(table):
        row_no = start_row + i
        cells = [_cell_text(c) for c in raw]
        if not any(cells):
            continue

        def get(role):
            j = roles.get(role)
            return cells[j] if j is not None and j < len(cells) else ""

        d_raw, desc = get("date"), get("description")
        if re.fullmatch(r"(?i)[a-z .()/₹]*balance[a-z .()/₹]*", get("balance")):
            continue  # the table's header repeated on a later page
        if not d_raw and desc and out.rows and not any(get(r) for r in ("debit", "credit", "amount", "balance")):
            out.rows[-1].description += " " + desc  # wrapped narration
            continue
        if re.match(r"(?i)^(total|opening balance|closing balance|grand total|b/f|c/f|balance (b/f|c/f))", desc or d_raw):
            continue
        d = parse_date(d_raw)
        try:
            if "amount" in roles and "debit" not in roles:
                amt = to_paise(get("amount"))
                kind = get("drcr").upper()
                if amt is None or kind not in ("DR", "CR", "D", "C", "DEBIT", "CREDIT"):
                    raise ValueError(f"amount/Dr-Cr unreadable ({get('amount')!r}, {get('drcr')!r})")
                debit, credit = (amt, None) if kind.startswith("D") else (None, amt)
            else:
                debit, credit = to_paise(get("debit")), to_paise(get("credit"))
            balance = to_paise(get("balance")) if "balance" in roles else None
        except ValueError as e:
            out.quarantine.append({"page_no": page_no, "row_no": row_no, "kind": "unreadable_row", "reason": str(e), "detail": " | ".join(cells)})
            bad += 1
            continue
        if d is None:
            out.quarantine.append({"page_no": page_no, "row_no": row_no, "kind": "unreadable_row",
                                   "reason": f"date not recognised: {d_raw!r}", "detail": " | ".join(cells)})
            bad += 1
            continue
        if not debit and not credit:
            out.quarantine.append({"page_no": page_no, "row_no": row_no, "kind": "zero_amount_row",
                                   "reason": "row has neither debit nor credit", "detail": " | ".join(cells)})
            bad += 1
            continue
        ccy = get("currency").strip().upper()
        symbol = next((c for c in (cell_currency(get(r)) for r in ("debit", "credit", "amount", "balance")) if c), None)
        out.rows.append(Row(page_no, row_no, cells, d, desc, get("ref"), debit or 0, credit or 0, balance,
                            ccy if re.fullmatch(r"[A-Z]{3}", ccy) else None, symbol))
        parsed += 1
    return parsed, bad


def drop_rotated_text(page):
    """Remove characters drawn at an angle. Diagonal watermarks ('SAMPLE', 'GENERATED', bank logos) otherwise
    leak letters into table cells and header values; statement data is always axis-aligned."""
    def keep(obj):
        if obj.get("object_type") != "char":
            return True
        m = obj.get("matrix") or (1, 0, 0, 1, 0, 0)
        return abs(m[1]) < 0.01 and abs(m[2]) < 0.01
    return page.filter(keep)


_STOP_LINE = re.compile(r"(?i)^(statement summary|summary|closing balance|grand total|total\b|page \d+|\*+|this is a|generated)")
_DATE_START = re.compile(r"^\d{1,2}[/ .-](\d{1,2}|[A-Za-z]{3,9})[/ .-]\d{2,4}\b|^\d{4}-\d{2}-\d{2}\b")


def _word_lines(page) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for w in sorted(page.extract_words(x_tolerance=2, y_tolerance=2), key=lambda w: (round(w["top"]), w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= 3:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(l, key=lambda w: w["x0"]) for l in lines]


def _cells_from_line(line: list[dict], gap: float = 6.0) -> list[list[dict]]:
    cells: list[list[dict]] = []
    for w in line:
        if cells and w["x0"] - cells[-1][-1]["x1"] <= gap:
            cells[-1].append(w)
        else:
            cells.append([w])
    return cells


def _borderless_table(page, columns: list[tuple[str, float, float]] | None) -> tuple[list[list[str]], list[tuple[str, float, float]] | None]:
    """A statement with no ruling lines: find the header by its words, then place each word in a column by position.
    Returns rows as cells (header first when found) and the column map, which carries over to later pages."""
    lines = _word_lines(page)
    start = 0
    for i, line in enumerate(lines[:60]):
        # a label can sit on two lines ("Withdrawal" above "Amt."), so try the line alone and then with the next one
        for span in (line, line + (lines[i + 1] if i + 1 < len(lines) else [])):
            found = _cells_from_line(sorted(span, key=lambda w: w["x0"]))
            labels = [" ".join(w["text"] for w in sorted(c, key=lambda w: w["top"])) for c in found]
            if infer_roles(labels):
                edges = [(labels[j], min(w["x0"] for w in c), max(w["x1"] for w in c)) for j, c in enumerate(found)]
                columns, start = edges, i + (1 if span is line else 2)
                break
        if start:
            break
    if not columns:
        return [], None
    bounds = [(columns[j][2] + columns[j + 1][1]) / 2 for j in range(len(columns) - 1)]
    table: list[list[str]] = [[c[0] for c in columns]] if start else []
    gaps = sorted(b[0]["top"] - a[0]["top"] for a, b in zip(lines[start:], lines[start + 1:]))
    typical = gaps[len(gaps) // 2] if gaps else 0
    for k, line in enumerate(lines[start:]):
        text = " ".join(w["text"] for w in line)
        if _STOP_LINE.match(text):
            break
        dated = bool(_DATE_START.match(text))
        if not dated and k and typical and line[0]["top"] - lines[start + k - 1][0]["top"] > 2.5 * typical:
            break  # a block of footer text, not a wrapped narration
        row = [""] * len(columns)
        for w in line:
            # amounts are right-aligned under their label, so a word belongs to the column of its centre
            mid = (w["x0"] + w["x1"]) / 2
            j = next((n for n, b in enumerate(bounds) if mid < b), len(bounds))
            row[j] = (row[j] + " " + w["text"]).strip()
        table.append(row)
    return table, columns


def extract_pdf(data: bytes, password: str | None = None) -> Extracted:
    try:
        pdf = pdfplumber.open(io.BytesIO(data), password=password or "")
        pages = pdf.pages
    except Exception as e:  # pdfminer raises several types; password errors are recognisable by name
        # pdfplumber wraps pdfminer's error with an empty message, so look at the whole chain
        chain, cur = [], e
        while cur is not None and len(chain) < 5:
            chain.append(repr(cur))
            cur = cur.__cause__ or cur.__context__
        name = " ".join(chain).lower()
        if "password" in name or "decrypt" in name:
            raise NeedsPassword("PDF is password protected") from e
        raise Unreadable(f"PDF could not be opened: {type(e).__name__}: {e}".strip()) from e
    meta = Meta()
    out = Extracted(meta, layout="pdf/generic")
    roles: dict[str, int] | None = None
    columns: list[tuple[str, float, float]] | None = None
    full_text = []
    with pdf:
        for idx, page in enumerate(pages, start=1):
            try:
                page = drop_rotated_text(page)
                text = page.extract_text() or ""
                tables = page.extract_tables()
            except Exception as e:
                out.pages.append(PageResult(idx, "failed", f"page could not be parsed: {type(e).__name__}"))
                continue
            full_text.append(text)
            if idx == 1:
                meta_from_text(text, meta)
            if not text.strip() and not tables:
                out.pages.append(PageResult(idx, "needs_ocr", "no text layer on page (scanned image); OCR required"))
                continue
            n_ok = n_bad = 0
            method = "pdfplumber.tables"
            for t in tables:
                if not t:
                    continue
                header_roles = infer_roles(t[0])
                if header_roles:
                    roles = header_roles
                    body, start = t[1:], 1
                elif roles and len(t[0]) >= max(roles.values()) + 1:
                    body, start = t, 0  # table continues from the previous page without a header
                else:
                    inferred = roles_from_data(t)
                    if not inferred:
                        continue
                    roles, method = inferred, "pdfplumber.tables+columns_from_data"
                    body, start = t, 0
                a, b = _rows_from_table(body, roles, idx, start, out)
                n_ok, n_bad = n_ok + a, n_bad + b
            if n_ok == 0 and n_bad == 0:
                grid, columns = _borderless_table(page, columns)
                if grid:
                    header_roles = infer_roles(grid[0])
                    if header_roles:
                        roles = header_roles
                    if roles:
                        n_ok, n_bad = _rows_from_table(grid[1:] if header_roles else grid, roles, idx, 1 if header_roles else 0, out)
                        method = "pdfplumber.words"
            if n_ok == 0 and n_bad == 0:
                out.pages.append(PageResult(idx, "no_transactions", "no transaction table on this page", 0, "pdfplumber"))
            else:
                status = "parsed" if n_bad == 0 else "partial"
                reason = f"{n_ok} rows" + (f", {n_bad} rows quarantined" if n_bad else "")
                out.pages.append(PageResult(idx, status, reason, n_ok, method))
    meta_from_footer("\n".join(full_text[-1:]), meta)
    bank = (meta.bank_name or "").lower()
    out.layout = "pdf/" + (re.sub(r"[^a-z]+", "-", bank).strip("-") or "generic")
    return out


def _grid_extract(grid: list[list], layout: str, page_no: int = 1) -> Extracted:
    """CSV/XLSX: metadata 'Key:, value' lines above a header row, transactions below."""
    meta = Meta()
    out = Extracted(meta, layout)
    pairs, header_idx, roles = [], None, None
    for i, row in enumerate(grid):
        cells = [c for c in row]
        r = infer_roles(cells)
        if r:
            header_idx, roles = i, r
            break
        nonempty = [_cell_text(c) for c in cells if _cell_text(c)]
        if len(nonempty) >= 2:
            pairs.append((nonempty[0].rstrip(":"), nonempty[1]))
        elif len(nonempty) == 1 and not meta.bank_name and "statement" in nonempty[0].lower():
            meta.bank_name = re.split(r"\s+-\s+", nonempty[0])[0].strip()
    # account details laid out as a row of labels with the values in the row below
    above = grid[:header_idx] if header_idx is not None else grid
    for labels, values in zip(above, above[1:]):
        known = [k for k in labels if isinstance(k, str) and any(re.search(p, k.lower().strip(" :")) for p in KV_PATTERNS.values())]
        if len(known) >= 3:
            pairs += [(_cell_text(k).rstrip(":"), _cell_text(v)) for k, v in zip(labels, values) if isinstance(k, str) and v is not None]
    meta_from_pairs(pairs, meta)
    if roles is None:
        out.pages.append(PageResult(page_no, "failed", "no transaction header row found (expected Date, Description and amount columns)"))
        return out
    n_ok, n_bad = _rows_from_table(grid[header_idx + 1:], roles, page_no, header_idx + 2, out)
    status = "parsed" if n_bad == 0 else "partial"
    out.pages.append(PageResult(page_no, status, f"{n_ok} rows" + (f", {n_bad} quarantined" if n_bad else ""), n_ok, layout))
    return out


def extract_csv(data: bytes) -> Extracted:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return _grid_extract(list(csv.reader(io.StringIO(text))), "csv/generic")


def extract_xlsx(data: bytes) -> Extracted:
    try:
        wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception as e:
        raise Unreadable(f"workbook could not be opened: {type(e).__name__}") from e
    sheets = []
    for s_idx, ws in enumerate(wb.worksheets, start=1):
        sheets.append((ws.title, _grid_extract([list(r) for r in ws.iter_rows(values_only=True)], "xlsx/generic", page_no=s_idx)))
    if not sheets:
        raise Unreadable("workbook has no sheets")
    title, best = max(sheets, key=lambda s: len(s[1].rows))
    if not best.rows:
        return best
    # Other sheets either continue the statement (monthly tabs, later dates) or re-list parts of it (an analyser's
    # summaries, inside the same dates). Only the first kind adds rows; every sheet stays in the page report with what happened to it.
    lo, hi = min(r.date for r in best.rows), max(r.date for r in best.rows)
    pages, meta_sheets = list(best.pages), []
    for name, ex in sorted((s for s in sheets if s[1] is not best), key=lambda s: -len(s[1].rows)):
        meta_sheets.append(ex.meta)
        outside = [r for r in ex.rows if not lo <= r.date <= hi]
        if ex.rows and len(outside) * 2 > len(ex.rows):
            best.rows += ex.rows
            best.quarantine += ex.quarantine
            lo, hi = min(lo, *(r.date for r in ex.rows)), max(hi, *(r.date for r in ex.rows))
            pages += ex.pages
        else:
            reason = f"sheet '{name}': " + (f"{len(ex.rows)} rows inside the statement's dates, treated as a summary of it; not counted again" if ex.rows
                                            else "no statement rows (summary or analysis sheet); not used")
            pages += [PageResult(p.page_no, "no_transactions", reason) for p in ex.pages]
    best.pages = sorted(pages, key=lambda p: p.page_no)
    for m in meta_sheets:
        best.meta.account_number = best.meta.account_number or m.account_number
        best.meta.bank_name = best.meta.bank_name or m.bank_name
        best.meta.currency = best.meta.currency or m.currency
        best.meta.account_type = best.meta.account_type or m.account_type
        best.meta.period_start, best.meta.period_end = best.meta.period_start or m.period_start, best.meta.period_end or m.period_end
        best.meta.holders += [h for h in m.holders if h not in best.meta.holders]
    return best


def extract_json(data: bytes) -> Extracted:
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except Exception as e:
        raise Unreadable(f"invalid JSON: {e}") from e
    meta = Meta()
    out = Extracted(meta, "json/structured")
    txns = doc.get("transactions") if isinstance(doc, dict) else doc
    if isinstance(doc, dict):
        meta.bank_name = doc.get("bank_name") or doc.get("bank")
        holder = doc.get("account_holder") or doc.get("customer_name")
        if holder:
            from .layout import clean_holder
            meta.holders.append(clean_holder(holder))
        meta.account_number = re.sub(r"\D", "", str(doc.get("account_number") or "")) or None
        meta.account_type = (doc.get("account_type") or "").upper() or None
        per = doc.get("statement_period") or {}
        meta.period_start, meta.period_end = parse_date(per.get("from")), parse_date(per.get("to"))
        meta.currency = (str(doc.get("currency") or "").upper()[:3] or None)
        meta.opening = to_paise(doc.get("opening_balance"))
        meta.closing = to_paise(doc.get("closing_balance"))
    if not isinstance(txns, list):
        out.pages.append(PageResult(1, "failed", "no 'transactions' list in JSON"))
        return out
    n_bad = 0
    for i, t in enumerate(txns, start=1):
        try:
            d = parse_date(t.get("date"))
            debit, credit = to_paise(t.get("debit")) or 0, to_paise(t.get("credit")) or 0
            bal = to_paise(t.get("balance"))
            if d is None or (not debit and not credit):
                raise ValueError("missing date or amount")
            ccy = str(t.get("currency") or "").upper()
            out.rows.append(Row(1, i, [t], d, str(t.get("description", "")), str(t.get("reference") or ""), debit, credit, bal,
                                ccy if re.fullmatch(r"[A-Z]{3}", ccy) else None, cell_currency(t.get("debit") or t.get("credit"))))
        except (ValueError, AttributeError) as e:
            out.quarantine.append({"page_no": 1, "row_no": i, "kind": "unreadable_row", "reason": str(e), "detail": json.dumps(t, default=str)[:300]})
            n_bad += 1
    out.pages.append(PageResult(1, "parsed" if not n_bad else "partial", f"{len(out.rows)} rows" + (f", {n_bad} quarantined" if n_bad else ""),
                                len(out.rows), "json"))
    return out


def detect_kind(filename: str, data: bytes) -> str:
    """Type by content, not just extension."""
    name = filename.lower()
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:2] == b"PK" and name.endswith(".xlsx"):
        return "xlsx"
    head = data[:64].lstrip()
    if name.endswith(".json") and head[:1] in (b"{", b"["):
        return "json"
    if name.endswith(".csv"):
        try:
            data[:4096].decode("utf-8-sig")
            return "csv"
        except UnicodeDecodeError:
            return "unknown"
    return "unknown"


EXTRACTORS = {"csv": extract_csv, "xlsx": extract_xlsx, "json": extract_json}
