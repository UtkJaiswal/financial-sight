"""What each account shows over time, what can be earned from it, and which statements to ask for next."""
from __future__ import annotations

import re
import statistics
from collections import defaultdict

from ..classify.categories import EXCLUDED, INCOME
from ..ingest.normalize import counterparty_key
from ..money import fmt_inr

TRUSTED = {"auto", "confirmed"}


def _m(d) -> str:
    return f"{d:%Y-%m}"


def _event(date, kind, title, detail="", txn_ids=None) -> dict:
    return {"date": date, "kind": kind, "title": title, "detail": detail, "txn_ids": txn_ids or []}


def _top(ts: list[dict], amount: str, n: int = 5) -> list[dict]:
    per: dict[str, dict] = {}
    for t in ts:
        key = counterparty_key(t["counterparty"] or "") or "Unnamed"
        p = per.setdefault(key, {"name": key, "amount": 0, "count": 0, "txn_ids": []})
        p["amount"] += t[amount]
        p["count"] += 1
        p["txn_ids"].append(t["txn_id"])
    return sorted(per.values(), key=lambda x: (-x["amount"], x["name"]))[:n]


def account_history(view: list[dict], txns: list[dict], statements: list[dict], ob_items: list[dict], cross_checks: list[dict] | None = None) -> None:
    """Adds a summary, a timeline and the main counterparties to each account's view."""
    for v in view:
        acct = v["account_id"]
        ts = sorted((t for t in txns if t["account_id"] == acct), key=lambda t: (t["txn_date"], t["seq"]))
        stm = sorted((s for s in statements if s["account_id"] == acct), key=lambda s: s["period_start"])
        events = [_event(s["period_start"], "statement", "Statement period", f"{s['period_start']:%d %b %Y} to {s['period_end']:%d %b %Y}, {s['n_rows']} rows, {s['recon_status'].replace('_', ' ')}") for s in stm]
        for c in cross_checks or []:
            if c["account_id"] == acct and c["kind"] in ("gap", "overlap", "balance_mismatch"):
                events.append(_event(c.get("gap_start") or (stm[-1]["period_start"] if stm else None), "statement", c["kind"].replace("_", " ").capitalize(), c["detail"]))
        if ts:
            events.append(_event(ts[0]["txn_date"], "activity", "First entry", f"{ts[0]['description'][:60]}", [ts[0]["txn_id"]]))
            events.append(_event(ts[-1]["txn_date"], "activity", "Latest entry", f"{ts[-1]['description'][:60]}", [ts[-1]["txn_id"]]))
        salary = [t for t in ts if t["category"] == "SALARY" and t["status"] in TRUSTED]
        if salary:
            events.append(_event(salary[0]["txn_date"], "income", "First salary credit", fmt_inr(salary[0]["credit"]), [salary[0]["txn_id"]]))
            events.append(_event(salary[-1]["txn_date"], "income", "Latest salary credit", fmt_inr(salary[-1]["credit"]), [salary[-1]["txn_id"]]))
        for t in ts:
            if t["category"] == "LOAN_DISBURSAL":
                events.append(_event(t["txn_date"], "loan", "Loan money received", fmt_inr(t["credit"]), [t["txn_id"]]))
            elif t["category"] == "BOUNCE_RETURN" and t["direction"] == "C":
                events.append(_event(t["txn_date"], "risk", "Payment returned unpaid", fmt_inr(t["credit"]), [t["txn_id"]]))
            elif t["category"] == "CASH_DEPOSIT" and t["credit"] >= 1_00_000_00:
                events.append(_event(t["txn_date"], "risk", "Large cash deposit", fmt_inr(t["credit"]), [t["txn_id"]]))
            elif t["override_category"]:
                events.append(_event(t["txn_date"], "correction", f"Corrected by {t['override_actor']}", f"{t['model_category']} to {t['override_category']}: {t['override_reason']}", [t["txn_id"]]))
        mine = {t["txn_id"] for t in ts}
        for i in ob_items:
            ids = [x for x in i["txn_ids"] if x in mine]
            if ids and len(i["months"]) >= 2:
                first = min(t["txn_date"] for t in ts if t["txn_id"] in ids)
                events.append(_event(first, "obligation", f"{i['label']} begins: {i['counterparty']}", f"about {fmt_inr(i['monthly_amount'])} a month over {len(i['months'])} months", ids))
        for label, key, chooser in (("Largest credit", "credit", max), ("Largest debit", "debit", max)):
            rows = [t for t in ts if t[key]]
            if rows:
                t = chooser(rows, key=lambda x: x[key])
                events.append(_event(t["txn_date"], "activity", label, f"{fmt_inr(t[key])}: {t['description'][:60]}", [t["txn_id"]]))
        for r in v["months"]:
            if r["coverage"] == "missing":
                y, mm = r["month"].split("-")
                events.append(_event(f"{y}-{mm}-01", "statement", f"No statement for {r['month']}", "Not treated as zero activity."))
        balances = [t["balance"] for t in ts if t["balance"] is not None]
        v["summary"] = {"first_entry": ts[0]["txn_date"] if ts else None, "last_entry": ts[-1]["txn_date"] if ts else None, "n_txns": len(ts),
                        "n_statements": len(stm), "total_credits": sum(t["credit"] for t in ts), "total_debits": sum(t["debit"] for t in ts),
                        "opening": stm[0]["opening_paise"] if stm else None, "latest_balance": balances[-1] if balances else None,
                        "lowest_balance": min(balances) if balances else None, "highest_balance": max(balances) if balances else None,
                        "returned_payments": sum(1 for t in ts if t["category"] == "BOUNCE_RETURN" and t["direction"] == "C"),
                        "transfers_in": sum(t["credit"] for t in ts if t["category"] == "INTERNAL_TRANSFER"), "transfers_out": sum(t["debit"] for t in ts if t["category"] == "INTERNAL_TRANSFER")}
        by_cat: dict[str, dict] = {}
        for t in ts:
            c = by_cat.setdefault(t["category"], {"category": t["category"], "credits": 0, "debits": 0, "count": 0})
            c["credits"] += t["credit"]
            c["debits"] += t["debit"]
            c["count"] += 1
        v["by_category"] = sorted(by_cat.values(), key=lambda c: -(c["credits"] + c["debits"]))
        v["top_in"] = _top([t for t in ts if t["credit"] and t["category"] not in EXCLUDED], "credit")
        v["top_out"] = _top([t for t in ts if t["debit"] and t["category"] not in EXCLUDED], "debit")
        v["events"] = sorted(events, key=lambda e: (str(e["date"]), e["kind"]))


def earnings(inc: dict, txns: list[dict], monthly: list[dict], concentration_share: float) -> dict:
    """How much the customer earns, month by month and from whom."""
    rows, cumulative = [], 0
    for x in inc.get("monthly", []):
        cumulative += x["value"]
        rows.append({"month": x["month"], "receipts": x.get("gross", x["value"]), "costs": x.get("costs", 0), "earned": x["value"], "cumulative": cumulative,
                     "regular": x.get("regular", 0), "variable": x.get("variable", 0)})
    ids = set(inc.get("included", []))
    credits = [t for t in txns if t["txn_id"] in ids and t["direction"] == "C"]
    total = sum(t["credit"] for t in credits)
    payers = _top(credits, "credit", 10)
    for p in payers:
        p["share"] = round(p["amount"] / total, 3) if total else 0
        p["months"] = len({_m(t["txn_date"]) for t in credits if t["txn_id"] in set(p["txn_ids"])})
    values = [r["earned"] for r in rows]
    trailing = values[-3:]
    out = {"monthly": rows, "total_receipts": sum(r["receipts"] for r in rows), "total_costs": sum(r["costs"] for r in rows), "total_earned": sum(values), "months": len(rows),
           "average_monthly": int(statistics.mean(values)) if values else None, "estimate": inc.get("value"), "annualised": int(statistics.mean(values) * 12) if values else None,
           "best": max(rows, key=lambda r: r["earned"], default=None), "worst": min(rows, key=lambda r: r["earned"], default=None),
           "last_3_average": int(statistics.mean(trailing)) if trailing else None, "payers": payers, "n_payers": len({p["name"] for p in _top(credits, "credit", 1000)}),
           "pending_review": sum(t["credit"] for t in txns if t["direction"] == "C" and t["category"] in INCOME and t["status"] == "review"),
           "top_payer_share": payers[0]["share"] if payers else None, "concentrated": bool(payers and payers[0]["share"] >= concentration_share and len(rows) >= 3),
           "concentration_limit": concentration_share}
    return out


def statement_requests(txns: list[dict], accounts: list[dict], patterns: list[dict], links: list[dict], profile: str, income_value: int | None) -> dict:
    """Transfers that point at an account we have not seen. Until its statement arrives, they cannot be matched or excluded with certainty."""
    own_last4 = {(a["acct_masked"] or "")[-4:] for a in accounts}
    paired = {x for l in links if l["kind"] in ("transfer_pair", "rotation_near", "rotation_split") for x in (l["txn_a"], l["txn_b"])}
    by_id = {t["txn_id"]: t for t in txns}
    groups: dict[str, dict] = {}

    def add(key: str, label: str, reason: str, t: dict) -> None:
        g = groups.setdefault(key, {"key": key, "label": label, "reason": reason, "count": 0, "amount": 0, "txn_ids": [], "kind": "own_name"})
        g["count"] += 1
        g["amount"] += t["amount"]
        g["txn_ids"].append(t["txn_id"])

    # 1. money to or from the customer's own name or number, with no matching entry in any uploaded statement
    for t in txns:
        if t["txn_id"] in paired or t["category"] in ("SALARY",):
            continue
        norm = t.get("description_norm") or ""
        names_own = "<HOLDER>" in norm or "<OWN_ACCT>" in norm or t["category"] == "INTERNAL_TRANSFER"
        if not names_own:
            continue
        nums = [n for n in re.findall(r"X{3,}(\d{4})", t["description"] or "") if n not in own_last4]
        ifsc = re.search(r"\b([A-Z]{4})0[A-Z0-9]{6}\b", t["description"] or "")
        if nums:
            add(f"acct:{nums[0]}", f"Account ending {nums[0]}", "Transfers in the customer's own name name this account, and no statement for it was uploaded.", t)
        elif ifsc:
            add(f"bank:{ifsc.group(1)}", f"An account at the bank with code {ifsc.group(1)}", "Transfers in the customer's own name go to this bank, and no statement for it was uploaded.", t)
        else:
            add("own", "Another account in the customer's name", "Transfers in the customer's own name have no matching entry in any uploaded statement.", t)
    # 2. a salaried customer moving a steady sum out every month, usually to an account of their own
    if profile == "SALARIED" and income_value:
        for p in patterns:
            if p["direction"] != "D" or p["median_amount"] < income_value * 0.10:
                continue
            members = [by_id[i] for i in p["txn_ids"] if i in by_id]
            if not members or any(i in paired for i in p["txn_ids"]):
                continue
            if all(t["category"] in ("GENERAL_EXPENSE", "UNCLASSIFIED", "INTERNAL_TRANSFER") and t["channel"] in ("UPI", "NEFT", "IMPS", "RTGS", "OTHER") for t in members):
                name = (p["counterparties"] or ["an unnamed party"])[0]
                g = {"key": f"regular:{p['pattern_id']}", "label": f"Regular transfers to {name}", "kind": "regular",
                     "reason": f"About {fmt_inr(p['median_amount'])} leaves every month. If this is the customer's own account, its statement shows whether the money stays there.",
                     "count": len(members), "amount": sum(t["amount"] for t in members), "txn_ids": [t["txn_id"] for t in members]}
                groups[g["key"]] = g
    items = sorted(groups.values(), key=lambda g: -g["amount"])
    matched = [l for l in links if l["kind"] == "transfer_pair"]
    prompt = None
    if profile == "SALARIED" and len(accounts) == 1:
        prompt = "Ask the customer for statements of any other accounts they hold, so transfers between them can be tracked and left out of income."
    elif items:
        prompt = "Ask the customer for the statements below, so these transfers can be tracked and left out of income."
    return {"prompt": prompt, "items": items, "matched_pairs": len(matched), "matched_amount": sum(by_id[l["txn_b"]]["credit"] for l in matched if l["txn_b"] in by_id), "accounts_uploaded": len(accounts)}
