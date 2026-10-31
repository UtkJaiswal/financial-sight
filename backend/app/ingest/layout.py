"""Layout understanding shared by every format: column roles and statement header fields."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ..money import parse_date, to_paise

ROLE_PATTERNS = [
    ("currency", r"^(ccy|curr|currency)$"),
    ("value_date", r"value\s*d(ate|t)"),
    ("date", r"^(txn|tran|transaction|posting|post)?\s*\.?\s*date$|^date$|txn\s*date|tran\s*date|posted\s*date"),
    ("drcr", r"^(dr\s*/\s*cr|cr\s*/\s*dr|type|txn\s*type)$"),
    ("description", r"narration|description|particulars|details|remarks|transaction\s*details"),
    ("ref", r"chq|cheque|ref"),
    ("debit", r"withdrawal|debit|dr\s*amount|^dr$|paid\s*out"),
    ("credit", r"deposit|credit|cr\s*amount|^cr$|paid\s*in"),
    ("balance", r"balance"),
    ("amount", r"amount"),
]


def _clean(h) -> str:
    return re.sub(r"[^a-z/ ]", " ", str(h or "").lower()).strip()


def infer_roles(header: list) -> dict[str, int] | None:
    """Map column roles to indexes. Returns None if the row does not look like a transaction header."""
    roles: dict[str, int] = {}
    for i, h in enumerate(header):
        raw = str(h or "")
        # a narrow column wraps its label mid-word ("Withdra\nwal"), so also try the label with line breaks closed up
        variants = [raw] + ([re.sub(r"\s*\n\s*", "", raw)] if "\n" in raw else [])
        matched = False
        for v in variants:
            c = re.sub(r"\s+", " ", _clean(v))
            for role, pat in ROLE_PATTERNS:
                if c and role not in roles and re.search(pat, c):
                    roles[role] = i
                    matched = True
                    break
            if matched:
                break
    has_money = ("debit" in roles and "credit" in roles) or "amount" in roles
    if "date" in roles and "description" in roles and has_money:
        return roles
    return None


def _is_amount(cell: str) -> bool:
    try:
        return to_paise(cell) is not None
    except ValueError:
        return False


def roles_from_data(table: list[list]) -> dict[str, int] | None:
    """Column labels are missing from the text layer: read the columns from their contents.
    Debit and credit are told apart by the running balance. If the balance cannot settle it, refuse rather than guess."""
    rows = [[re.sub(r"\s+", " ", str(c or "")).strip() for c in r] for r in table]
    body = [r for r in rows if any(parse_date(c) for c in r[:3])]
    if len(body) < 2:
        return None
    n = max(len(r) for r in body)

    def share(j: int, test) -> float:
        return sum(1 for r in body if j < len(r) and test(r[j])) / len(body)

    date_cols = [j for j in range(n) if share(j, lambda c: parse_date(c) is not None) >= 0.8]
    num_cols = [j for j in range(n) if j not in date_cols and share(j, lambda c: _is_amount(c) or c in ("", "-", "--")) >= 0.9
                and share(j, _is_amount) > 0]
    text_cols = [j for j in range(n) if j not in date_cols and j not in num_cols]
    if not date_cols or len(num_cols) < 3 or not text_cols:
        return None
    a, b, bal = num_cols[-3:]
    votes_ab = votes_ba = 0
    for prev, cur in zip(body, body[1:]):
        if not (_is_amount(prev[bal]) and _is_amount(cur[bal])):
            continue
        delta = to_paise(cur[bal]) - to_paise(prev[bal])
        va, vb = to_paise(cur[a]) if _is_amount(cur[a]) else None, to_paise(cur[b]) if _is_amount(cur[b]) else None
        if (va and delta == -va) or (vb and delta == vb):
            votes_ab += 1
        if (va and delta == va) or (vb and delta == -vb):
            votes_ba += 1
    if votes_ab == votes_ba:
        return None
    debit, credit = (a, b) if votes_ab > votes_ba else (b, a)
    desc = max(text_cols, key=lambda j: sum(len(r[j]) for r in body if j < len(r)))
    roles = {"date": date_cols[0], "description": desc, "debit": debit, "credit": credit, "balance": bal}
    if len(date_cols) > 1:
        roles["value_date"] = date_cols[1]
    return roles


@dataclass
class Meta:
    bank_name: str | None = None
    holders: list[str] = field(default_factory=list)
    account_number: str | None = None
    account_type: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    opening: int | None = None
    closing: int | None = None
    currency: str | None = None


KV_PATTERNS = {
    "holder": r"(account\s*holder|customer\s*name|account\s*name|name\s*of\s*(the\s*)?customer)",
    "account_number": r"(account\s*(number|no)|a/c\s*(no|number))",
    "account_type": r"(account\s*type|a/c\s*type)",
    "period": r"(statement\s*period|period)",
    "opening": r"opening\s*balance",
    "closing": r"closing\s*balance",
    "currency": r"^(account\s*)?currency$",
    "bank": r"^bank(\s*name)?$",
    "ifsc": r"^ifsc(\s*code)?$",
    "period_from": r"^(period\s*(from|start)|from\s*date|start\s*date)$",
    "period_to": r"^(period\s*(to|end)|to\s*date|end\s*date)$",
}
IFSC_BANKS = {"HDFC": "HDFC Bank", "ICIC": "ICICI Bank", "SBIN": "State Bank of India", "UTIB": "Axis Bank", "KKBK": "Kotak Mahindra Bank",
              "PUNB": "Punjab National Bank", "BARB": "Bank of Baroda", "CNRB": "Canara Bank", "UBIN": "Union Bank of India",
              "IDFB": "IDFC FIRST Bank", "YESB": "Yes Bank", "INDB": "IndusInd Bank", "FDRL": "Federal Bank", "IOBA": "Indian Overseas Bank",
              "IDIB": "Indian Bank", "BKID": "Bank of India", "MAHB": "Bank of Maharashtra", "CBIN": "Central Bank of India"}
_GENERIC_TITLE = re.compile(r"(?i)\b(statement|summary|detailed|account|transaction|report|page)\b")
_DATE_TOKEN = r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}[\s-][A-Za-z]{3,9}[\s-]\d{2,4}|\d{4}-\d{2}-\d{2})"
_AMT = r"(-?[\d,]+\.\d{2})"
_CUR = r"(?:Rs\.?|INR|₹)?\s*"


def clean_holder(v: str) -> str:
    v = re.sub(r"(?i)^(mr|mrs|ms|miss|dr|m/s)\.?\s+", "", v.strip())
    return re.sub(r"\s+", " ", v).strip().upper()


def parse_period(text: str) -> tuple[date | None, date | None]:
    m = re.search(_DATE_TOKEN + r"\s*(?:to|-|–|till)\s*" + _DATE_TOKEN, text, flags=re.I)
    if m:
        return parse_date(m.group(1)), parse_date(m.group(2))
    return None, None


def meta_from_pairs(pairs: list[tuple[str, str]], meta: Meta) -> Meta:
    for k, v in pairs:
        k_l, v = k.lower().strip(), str(v).strip()
        if not v:
            continue
        if re.search(KV_PATTERNS["currency"], k_l):
            meta.currency = meta.currency or (v[:3].upper() if re.fullmatch(r"[A-Za-z]{3}", v[:3]) else None)
        elif re.search(KV_PATTERNS["ifsc"], k_l):
            meta.bank_name = IFSC_BANKS.get(v[:4].upper()) or meta.bank_name
        elif re.search(KV_PATTERNS["bank"], k_l):
            meta.bank_name = meta.bank_name or v
        elif re.search(KV_PATTERNS["period_from"], k_l):
            meta.period_start = meta.period_start or parse_date(v)
        elif re.search(KV_PATTERNS["period_to"], k_l):
            meta.period_end = meta.period_end or parse_date(v)
        elif re.search(KV_PATTERNS["holder"], k_l):
            h = clean_holder(v)
            if h and h not in meta.holders:
                meta.holders.append(h)
        elif re.search(KV_PATTERNS["account_number"], k_l) and not meta.account_number:
            meta.account_number = re.sub(r"\D", "", v) or None
        elif re.search(KV_PATTERNS["account_type"], k_l):
            meta.account_type = v.upper()
        elif re.search(KV_PATTERNS["period"], k_l):
            meta.period_start, meta.period_end = parse_period(v)
        elif re.search(KV_PATTERNS["opening"], k_l) and meta.opening is None:
            meta.opening = to_paise(v)
        elif re.search(KV_PATTERNS["closing"], k_l) and meta.closing is None:
            meta.closing = to_paise(v)
    return meta


def _bank_from_header(lines: list[str]) -> str | None:
    """The first line when it names a bank, else the labelled IFSC code, else a line that is only a bank name.
    The IFSC must be labelled: narrations carry other banks' codes."""
    first = lines[0]
    if re.search(r"(?i)\bbank\b", first) and not _GENERIC_TITLE.search(first):
        return first.title() if first.isupper() else first
    for line in lines[:40]:
        m = re.search(r"IFSC(?:\s*Code)?\s*:?\s*([A-Z]{4})0[A-Z0-9]{6}\b", line)
        if m and m.group(1) in IFSC_BANKS:
            return IFSC_BANKS[m.group(1)]
    for line in lines[:15]:
        if re.fullmatch(r"[A-Z][A-Za-z&.' -]{1,40}\bBank\b(?: of [A-Z][a-z]+)?(?: Ltd\.?| Limited)?", line):
            return line
    return None if _GENERIC_TITLE.search(first) else (first.title() if first.isupper() else first)


def meta_from_text(text: str, meta: Meta) -> Meta:
    """Header fields from free PDF text. Handles 'Key : value' lines and 'From X To Y' periods."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if lines and not meta.bank_name:
        meta.bank_name = _bank_from_header(lines)
    for line in lines[:40]:
        # labels with or without a colon: "Account Holder : X", "Account Holders Name X"
        for m in re.finditer(r"(Account Holders?(?: Name)?|Customer Name|Account Name)\s*:?\s+([A-Za-z .&]+?)(?=\s{2,}|$|\s+[A-Z][a-z/]+\s*(No|Number)\s*:)", line):
            h = clean_holder(m.group(2))
            if h and h not in meta.holders:
                meta.holders.append(h)
        m = re.match(r"(?:Customer\s+)?Name\s*:\s*(.+?)(?=\s+(?:Branch|Address|Currency|IFSC)\b|\s+A/C\s+Branch\b|$)", line, flags=re.I)
        if m:
            h = clean_holder(m.group(1))
            if h and h not in meta.holders:
                meta.holders.append(h)
        m = re.search(r"(Account Number|A/c No|Account No)\.?\s*:?\s*(\d{6,20})", line, flags=re.I)
        if m and not meta.account_number:
            meta.account_number = m.group(2)
        m = re.search(r"Currency\s*:\s*([A-Z]{3})\b", line)
        if m and not meta.currency:
            meta.currency = m.group(1)
        m = re.search(r"Account Type\s*:?\s*([A-Za-z]+)", line, flags=re.I)
        if m:
            meta.account_type = m.group(1).upper()
        if meta.period_start is None and re.search(r"period|from", line, flags=re.I):
            meta.period_start, meta.period_end = parse_period(line)
        m = re.search(r"Opening Balance\s*:?\s*" + _CUR + _AMT, line, flags=re.I)
        if m and meta.opening is None:
            meta.opening = to_paise(m.group(1))
        m = re.search(r"Closing Balance\s*:?\s*" + _CUR + _AMT, line, flags=re.I)
        if m and meta.closing is None:
            meta.closing = to_paise(m.group(1))
    return meta


def meta_from_footer(text: str, meta: Meta) -> Meta:
    """Summary blocks at the end of a statement (Opening/Closing in a closing summary)."""
    for key in ("opening", "closing"):
        if getattr(meta, key) is None:
            m = re.search(rf"{key} Balance\s*:?\s*" + _CUR + _AMT, text, flags=re.I)
            if m:
                setattr(meta, key, to_paise(m.group(1)))
    # a summary laid out as a row of labels with the values on the next line
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for label, values in zip(lines, lines[1:]):
        if re.search(r"(?i)(brought forward|opening balance)", label) and re.search(r"(?i)closing balance", label):
            amounts = re.findall(r"-?[\d,]+\.\d{2}", values)
            if len(amounts) >= 2:
                meta.opening = meta.opening if meta.opening is not None else to_paise(amounts[0])
                meta.closing = meta.closing if meta.closing is not None else to_paise(amounts[-1])
    return meta
