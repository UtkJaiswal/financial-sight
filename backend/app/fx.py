"""Currencies. Every amount is stored as printed, in hundredths of its account's currency; reconciliation stays in that currency.

Figures are reported in INR using rates someone supplied, with the rate and its source kept beside each figure.
No rate is ever fetched or assumed: a row without one is left out of the figures and reported.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from . import config
from .db import q

REPORTING = "INR"


def parse_rate(text: str) -> Decimal:
    try:
        rate = Decimal(str(text).strip())
    except InvalidOperation as e:
        raise ValueError(f"not a rate: {text!r}") from e
    if not rate.is_finite() or rate <= 0:
        raise ValueError("a rate must be a positive number")
    return rate


def rates(case_id: str) -> dict[tuple[str, str], dict]:
    """{(currency, month or ''): {rate, source, ...}}. A month's own rate wins over the currency's general rate."""
    return {(r["currency"], r["month"]): r for r in q("SELECT * FROM silver.fx_rates WHERE case_id = ? ORDER BY currency, month", [case_id])}


def rate_for(table: dict, currency: str, month: str) -> str | None:
    if currency == REPORTING:
        return "1"
    r = table.get((currency, month)) or table.get((currency, ""))
    return r["rate"] if r else None


def convert(amount: int | None, rate: str | None) -> int | None:
    if amount is None or rate is None:
        return None
    return int((Decimal(amount) * Decimal(rate)).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def annotate(t: dict, table: dict) -> dict:
    """Adds the row's INR amounts. Without a rate, *_inr stay as printed and fx_missing says so; nothing downstream may treat them as rupees."""
    cur = t.get("currency") or REPORTING
    rate = rate_for(table, cur, f"{t['txn_date']:%Y-%m}")
    t.update(currency=cur, fx_rate=rate if cur != REPORTING else None, fx_missing=rate is None,
             debit_inr=convert(t["debit"], rate) if rate else t["debit"], credit_inr=convert(t["credit"], rate) if rate else t["credit"])
    return t


def same_money(d: dict, c: dict) -> bool:
    """A debit and a credit that could be one movement. Across currencies the supplied rate is rarely the rate applied, so allow for that."""
    if d["fx_missing"] or c["fx_missing"]:
        return False
    if d["currency"] == c["currency"]:
        return d["debit"] == c["credit"]
    return abs(d["debit_inr"] - c["credit_inr"]) <= max(d["debit_inr"], c["credit_inr"]) * config.FX_MATCH_TOLERANCE_PCT // 100


def statements_in_inr(statements: list[dict], table: dict) -> list[dict]:
    """Opening balances for the balance series. An account with no usable rate has no place in a rupee series."""
    out = []
    for s in statements:
        cur = s.get("currency") or REPORTING
        rate = rate_for(table, cur, f"{s['period_start']:%Y-%m}")
        if cur != REPORTING and rate is None and not any(k[0] == cur for k in table):
            continue
        out.append(dict(s, opening_paise=convert(s["opening_paise"], rate)))
    return out
