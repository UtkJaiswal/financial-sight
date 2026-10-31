"""Balance reconciliation at row, statement and cross-statement level.

Row chain: balance[i-1] + credit[i] - debit[i] must equal balance[i]. When one printed balance is wrong
but the next row continues from the computed value, it is reported as a single misprint rather than
two breaks. A jump that does not recover suggests missing rows and is reported as a gap.
"""
from __future__ import annotations

from ..money import fmt_inr


def reconcile_rows(rows: list, opening: int | None, fmt=fmt_inr) -> tuple[list[tuple[str, str]], dict]:
    """rows have .debit .credit .balance. Returns per-row (status, note) and a summary."""
    out: list[tuple[str, str]] = []
    prev = opening
    breaks = 0
    for i, r in enumerate(rows):
        if r.balance is None:
            out.append(("unverified", "no running balance on statement"))
            prev = None
            continue
        if prev is None:
            out.append(("ok", "chain starts here"))
            prev = r.balance
            continue
        expected = prev + r.credit - r.debit
        if expected == r.balance:
            out.append(("ok", ""))
            prev = r.balance
            continue
        breaks += 1
        diff = r.balance - expected
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if nxt is not None and nxt.balance is not None and expected + nxt.credit - nxt.debit == nxt.balance:
            out.append(("break", f"printed balance differs from computed by {fmt(diff)}; next row continues from the "
                                 f"computed balance, so this looks like a misprint on the statement"))
            prev = expected
        else:
            out.append(("break", f"balance jumps by {fmt(diff)} versus the previous row; possible missing or misread transaction"))
            prev = r.balance
    total_cr = sum(r.credit for r in rows)
    total_dr = sum(r.debit for r in rows)
    return out, {"breaks": breaks, "total_credits": total_cr, "total_debits": total_dr}


def statement_status(opening: int | None, closing_stated: int | None, total_cr: int, total_dr: int, breaks: int) -> tuple[str, int | None, int | None]:
    if opening is None:
        return "unverified", None, None
    computed = opening + total_cr - total_dr
    if closing_stated is None:
        return ("reconciled_no_closing" if breaks == 0 else "breaks"), computed, None
    diff = closing_stated - computed
    if diff == 0 and breaks == 0:
        return "reconciled", computed, 0
    if diff == 0:
        return "reconciled_with_row_breaks", computed, 0
    return "unreconciled", computed, diff


def case_view(statements: list[dict], cross_checks: list[dict], txns: list[dict], links: list[dict], sources: dict[str, dict], p: dict) -> dict:
    """Every statement's arithmetic judged against the lender's reconciliation rules, with the rows behind any difference."""
    rows = []
    breaks_by_stmt: dict[str, list[dict]] = {}
    for t in txns:
        if t["recon_status"] == "break":
            src = sources.get(t["txn_id"]) or {}
            breaks_by_stmt.setdefault(src.get("statement_id"), []).append({"txn_id": t["txn_id"], "date": t["txn_date"], "note": t["recon_note"], "file": src.get("filename"), "page": src.get("page_no"), "row": src.get("row_no")})
    for s in statements:
        diff, stated = s["recon_diff"], s["closing_stated"]
        n_breaks = s["n_breaks"] or 0
        reasons = []
        if stated is None and p["recon_require_closing"]:
            reasons.append("no closing balance is stated, and this engine requires one")
        if diff is not None and abs(diff) > p["recon_tolerance_paise"]:
            reasons.append(f"closing differs by more than the allowed {fmt_inr(p['recon_tolerance_paise'])}")
        if n_breaks > p["recon_max_row_breaks"]:
            reasons.append(f"{n_breaks} running-balance break(s), and {p['recon_max_row_breaks']} allowed")
        if s["opening_paise"] is None:
            reasons.append("no opening balance is stated or derivable")
        rows.append({"statement_id": s["statement_id"], "file_id": s.get("file_id"), "bank_name": s["bank_name"], "acct_masked": s["acct_masked"], "currency": s.get("currency") or "INR",
                     "period_start": s["period_start"], "period_end": s["period_end"], "opening": s["opening_paise"], "opening_source": s["opening_source"], "credits": s["total_credits"], "debits": s["total_debits"],
                     "computed_closing": s["computed_closing"], "stated_closing": stated, "difference": diff, "n_rows": s["n_rows"], "n_breaks": n_breaks,
                     "result": "outside" if reasons else "within", "reasons": reasons, "breaks": breaks_by_stmt.get(s["statement_id"], [])})
    by_id = {t["txn_id"]: t for t in txns}
    pairs = [l for l in links if l["kind"] == "transfer_pair"]
    within = [r for r in rows if r["result"] == "within"]
    return {"rules": {k: p[k] for k in ("recon_tolerance_paise", "recon_require_closing", "recon_max_row_breaks")}, "statements": rows,
            "continuity": [{"kind": c["kind"], "detail": c["detail"], "account_id": c["account_id"]} for c in cross_checks],
            "transfers": {"matched_pairs": len(pairs), "matched_amount": sum(by_id[l["txn_b"]]["credit"] for l in pairs if l["txn_b"] in by_id),
                          "held_rows": sum(1 for t in txns if "possible_rotation" in t["attributes"] and t["status"] not in ("auto", "confirmed"))},
            "summary": {"statements": len(rows), "within": len(within), "outside": len(rows) - len(within), "within_share": round(len(within) / len(rows), 3) if rows else None,
                        "row_breaks": sum(r["n_breaks"] for r in rows), "gaps": sum(1 for c in cross_checks if c["kind"] == "gap"), "overlaps": sum(1 for c in cross_checks if c["kind"] == "overlap"),
                        "balance_mismatches": sum(1 for c in cross_checks if c["kind"] == "balance_mismatch"), "outside_txn_ids": [b["txn_id"] for r in rows if r["result"] == "outside" for b in r["breaks"]]}}
