"""Gold metrics. Pure functions over the categories in effect; every value carries its lineage:
the transactions included, those excluded with a reason, and the months used or missing.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, timedelta

from .. import config, rule_engine
from ..classify.categories import CATEGORIES, EXCLUDED, INCOME, OBLIGATIONS
from ..ingest.normalize import counterparty_key
from ..money import fmt_inr
from . import history

TRUSTED = {"auto", "confirmed"}


def _m(d: date) -> str:
    return f"{d:%Y-%m}"


def _label(cat: str) -> str:
    return CATEGORIES.get(cat, {"label": cat})["label"]


def coverage_by_month(coverage: list[dict]) -> tuple[list[str], dict[str, str], dict[str, dict[str, str]]]:
    months = sorted({c["month"] for c in coverage})
    per_acct: dict[str, dict[str, str]] = defaultdict(dict)
    for c in coverage:
        per_acct[c["account_id"]][c["month"]] = c["status"]
    combined = {}
    for m in months:
        st = [per_acct[a].get(m, "missing") for a in per_acct]
        combined[m] = "full" if all(s == "full" for s in st) else ("missing" if all(s == "missing" for s in st) else "partial")
    return months, combined, per_acct


def daily_balances(txns: list[dict], statements: list[dict]) -> dict[str, dict[date, int]]:
    """End-of-day balance per account on covered days only."""
    out: dict[str, dict[date, int]] = {}
    by_acct = defaultdict(list)
    first_txn: dict[str, date] = {}
    for t in txns:
        first_txn[t["account_id"]] = min(first_txn.get(t["account_id"], t["txn_date"]), t["txn_date"])
        if t["balance"] is not None:
            by_acct[t["account_id"]].append(t)
    for s in sorted(statements, key=lambda s: s["period_start"]):
        acct = s["account_id"]
        series = out.setdefault(acct, {})
        rows = sorted((t for t in by_acct[acct] if s["period_start"] <= t["txn_date"] <= s["period_end"]), key=lambda t: (t["txn_date"], t["seq"]))
        eod = {}
        for t in rows:
            eod[t["txn_date"]] = t["balance"]
        bal = s["opening_paise"]
        d = s["period_start"]
        if not bal and acct in first_txn:
            # a zero balance before the first entry is an account not yet in use, not a low balance
            d = max(d, first_txn[acct].replace(day=1))
        while d <= s["period_end"]:
            bal = eod.get(d, bal)
            if bal is not None and d not in series:
                series[d] = bal
            d += timedelta(days=1)
    return out


def _held(t: dict) -> bool:
    """Flagged as possible rotation and not yet decided by an analyst."""
    return "possible_rotation" in t["attributes"] and t["status"] not in TRUSTED


def money_flows(txns: list[dict], links: list[dict], p: dict) -> dict:
    """Where money moved: between own accounts, in circles, to and from one party, or straight through an account."""
    by_id = {t["txn_id"]: t for t in txns}
    edges: dict[tuple, dict] = {}
    parties: dict[str, dict] = {}
    passes: dict[str, dict] = {}
    events = []
    for l in links:
        d, c = by_id.get(l["txn_a"]), by_id.get(l["txn_b"])
        if not d or not c or l["kind"] not in ("transfer_pair", "rotation_near", "rotation_split", "rotation_party", "rotation_pass"):
            continue
        undecided = _held(d) or _held(c)
        if l["kind"] != "transfer_pair" and any(t["status"] == "confirmed" and t["category"] != "INTERNAL_TRANSFER" for t in (d, c)):
            continue  # an analyst decided this leg was real, so the pair is no longer rotation
        ids = [d["txn_id"], c["txn_id"]]
        if l["kind"] == "rotation_party":
            rec = parties.setdefault(counterparty_key(d["counterparty"] or "") or "Unnamed party", {"out": 0, "back": 0, "count": 0, "txn_ids": []})
            rec["out"] += d["debit"]
            rec["back"] += c["credit"]
            rec["count"] += 1
            rec["txn_ids"] += ids
        elif l["kind"] == "rotation_pass":
            rec = passes.setdefault(c["account_id"], {"amount": 0, "count": 0, "txn_ids": []})
            rec["amount"] += c["credit"]
            rec["count"] += 1
            rec["txn_ids"] += ids
        else:
            e = edges.setdefault((d["account_id"], c["account_id"], undecided), {"amount": 0, "count": 0, "txn_ids": []})
            e["amount"] += c["credit"]
            e["count"] += 1
            e["txn_ids"] += ids
            events.append({"from": d["account_id"], "to": c["account_id"], "date": d["txn_date"], "amount": c["credit"], "ids": ids})
    # circles: follow each move onward while the amount holds, until it returns to where it started
    events.sort(key=lambda e: (e["date"], e["ids"]))
    used: set[int] = set()
    cycles: dict[tuple, dict] = {}
    for i, first in enumerate(events):
        if i in used:
            continue
        chain = [i]
        while len(chain) <= 4 and events[chain[-1]]["to"] != first["from"]:
            last = events[chain[-1]]
            nxt = next((j for j, e in enumerate(events) if j not in used and j not in chain and e["from"] == last["to"]
                        and abs(e["amount"] - first["amount"]) <= first["amount"] * 2 // 100
                        and 0 <= (e["date"] - last["date"]).days <= p["rotation_window_days"]), None)
            if nxt is None:
                break
            chain.append(nxt)
        if len(chain) >= 2 and events[chain[-1]]["to"] == first["from"]:
            used.update(chain)
            path = [events[j]["from"] for j in chain]
            start = path.index(min(path))
            key = tuple(path[start:] + path[:start])
            cy = cycles.setdefault(key, {"path": list(key) + [key[0]], "amount": 0, "count": 0, "txn_ids": []})
            cy["amount"] += first["amount"]
            cy["count"] += 1
            for j in chain:
                cy["txn_ids"] += events[j]["ids"]
    return {"edges": [{"from": k[0], "to": k[1], "possible": k[2], **v} for k, v in sorted(edges.items(), key=lambda x: -x[1]["amount"])],
            "cycles": sorted(cycles.values(), key=lambda c: -c["amount"]),
            "parties": [{"counterparty": k, **v} for k, v in sorted(parties.items(), key=lambda x: -x[1]["out"])],
            "pass_through": [{"account_id": k, **v} for k, v in sorted(passes.items(), key=lambda x: -x[1]["amount"])]}


def compute(case: dict, txns: list[dict], coverage: list[dict], statements: list[dict], patterns: list[dict], links: list[dict] | None = None,
            engine: dict | None = None, accounts: list[dict] | None = None, cross_checks: list[dict] | None = None) -> dict:
    engine = engine or rule_engine.default()
    p = engine["params"]
    months, combined, per_acct = coverage_by_month(coverage)
    # A statement period can start long before the account's first entry. Those months say nothing about income or
    # cash flow, and counting them as zero would drag a median to nothing.
    first_active = min((_m(t["txn_date"]) for t in txns), default=None)
    full_months = [m for m in months if combined[m] == "full" and first_active and m >= first_active]
    by_month = defaultdict(list)
    for t in txns:
        by_month[_m(t["txn_date"])].append(t)

    # ---------------------------------------------------------------- monthly table and balances
    balances = daily_balances(txns, statements)
    monthly = []
    for m in months:
        ts = by_month[m]
        cr = sum(t["credit"] for t in ts)
        dr = sum(t["debit"] for t in ts)
        cr_adj = sum(t["credit"] for t in ts if t["category"] not in EXCLUDED)
        dr_adj = sum(t["debit"] for t in ts if t["category"] not in EXCLUDED)
        inc = sum(t["credit"] for t in ts if t["category"] in INCOME and t["status"] in TRUSTED)
        acct_bal = {}
        for acct, series in balances.items():
            vals = [v for d, v in series.items() if _m(d) == m]
            if vals:
                acct_bal[acct] = {"avg": int(statistics.mean(vals)), "min": min(vals),
                                  "low_days": sum(1 for v in vals if v < p["low_balance_paise"]), "days": len(vals)}
        monthly.append({"month": m, "coverage": combined[m], "coverage_by_account": {a: per_acct[a].get(m, "missing") for a in per_acct},
                        "credits": cr, "debits": dr, "credits_adjusted": cr_adj, "debits_adjusted": dr_adj,
                        "income": inc, "obligations": 0, "net": cr_adj - dr_adj, "n_txns": len(ts),
                        "active": bool(first_active) and m >= first_active,
                        "credit_ids": [t["txn_id"] for t in ts if t["credit"]], "debit_ids": [t["txn_id"] for t in ts if t["debit"]],
                        "avg_balance": sum(b["avg"] for b in acct_bal.values()) if len(acct_bal) == len(balances) and acct_bal else None,
                        "balances": acct_bal})

    # ---------------------------------------------------------------- income
    income_accts = {t["account_id"] for t in txns if t["category"] in INCOME}
    income_from = min((_m(t["txn_date"]) for t in txns if t["account_id"] in income_accts), default=first_active)
    eligible = [m for m in months if m >= income_from and all(per_acct[a].get(m) == "full" for a in income_accts)] if income_accts else full_months
    inactive = [m for m in months if not income_from or m < income_from]
    salary = [t for t in txns if t["category"] == "SALARY" and t["status"] in TRUSTED]
    salary_months = {_m(t["txn_date"]) for t in salary}
    profile = "SALARIED" if len(salary_months) >= 3 else "SELF_EMPLOYED"
    income_cats = {"SALARY"} if profile == "SALARIED" else INCOME
    use_months = eligible[-p["income_max_months"]:]
    included, excluded = [], []
    totals = {m: 0 for m in use_months}
    pending_totals = {m: 0 for m in use_months}  # likely income still awaiting review: shown as provisional, never mixed in
    rotation_totals = {m: 0 for m in use_months}  # held as possible rotation: not even provisional until someone decides
    regular = {m: 0 for m in use_months}  # salary and monthly series
    variable = {m: 0 for m in use_months}
    cash_totals = {m: 0 for m in use_months}  # often real sales for a shop, but unverified: shown beside the estimate, not in it
    for t in txns:
        if t["direction"] != "C":
            continue
        m = _m(t["txn_date"])
        cat = t["category"]
        if _held(t):
            if m in rotation_totals:
                rotation_totals[m] += t["credit"]
            excluded.append({"txn_id": t["txn_id"], "reason": "Possible money rotation: left out until an analyst reviews it"})
        elif cat in income_cats and t["status"] in TRUSTED and m in totals:
            totals[m] += t["credit"]
            included.append(t["txn_id"])
            (regular if cat == "SALARY" or "recurring" in t["attributes"] else variable)[m] += t["credit"]
        elif cat in income_cats and t["status"] not in TRUSTED:
            if m in pending_totals and t["status"] == "review":
                pending_totals[m] += t["credit"]
            excluded.append({"txn_id": t["txn_id"], "reason": f"{_label(cat)} pending analyst review"})
        elif cat in income_cats:
            excluded.append({"txn_id": t["txn_id"], "reason": f"{m} has incomplete statement coverage"})
        elif cat == "CASH_DEPOSIT":
            if m in cash_totals:
                cash_totals[m] += t["credit"]
            if p["count_cash_deposits"] and t["status"] in TRUSTED and m in totals:
                totals[m] += t["credit"]
                variable[m] += t["credit"]
                included.append(t["txn_id"])
                continue
            excluded.append({"txn_id": t["txn_id"], "reason": "Cash deposit: excluded until its source is verified"})
        elif cat in EXCLUDED:
            excluded.append({"txn_id": t["txn_id"], "reason": f"{_label(cat)}: not income"})
        elif cat in INCOME:
            if t["status"] in TRUSTED and m in variable:
                variable[m] += t["credit"]  # reported beside the estimate, not counted in it
            excluded.append({"txn_id": t["txn_id"], "reason": f"{_label(cat)}: not used for a {profile.lower().replace('_', '-')} estimate"})
        else:
            excluded.append({"txn_id": t["txn_id"], "reason": f"{_label(cat)}: not counted as income"})
    # A business's receipts are not its income: take off identified costs. A salaried estimate has none to take off.
    costs = {m: 0 for m in use_months}
    cost_ids = []
    if profile == "SELF_EMPLOYED" and p["deduct_business_costs"]:
        for t in txns:
            m = _m(t["txn_date"])
            if t["direction"] == "D" and t["category"] in config.BUSINESS_COST_CATEGORIES and m in costs and not _held(t):
                costs[m] += t["debit"]
                cost_ids.append(t["txn_id"])
    included += cost_ids
    has_costs = any(costs.values())
    gross = [totals[m] for m in use_months]
    series = [totals[m] - costs[m] for m in use_months]
    notes = []
    income_value, method = None, "insufficient covered months"
    stats = {}
    if series and len(series) < p["income_min_months"]:
        method = f"this lender asks for at least {p['income_min_months']} fully covered months; {len(series)} available"
        notes.append(method[0].upper() + method[1:] + ".")
    if series and len(series) >= p["income_min_months"]:
        med = int(statistics.median(series))
        income_value = med if p["income_method"] == "median" else int(statistics.mean(series))
        method = f"{p['income_method']} of {len(series)} monthly {'salary' if profile == 'SALARIED' else 'income'} totals over fully covered months" + (", after business costs" if has_costs else "")
        stats = {"median": med, "mean": int(statistics.mean(series)), "highest": max(series), "lowest": min(series),
                 "regular_monthly": int(statistics.median(regular[m] for m in use_months)), "variable_monthly": int(statistics.median(variable[m] for m in use_months))}
        if has_costs:
            stats["gross_receipts_monthly"] = int(statistics.median(gross))
            stats["business_costs_monthly"] = int(statistics.median(costs.values()))
        prov_series = [totals[m] + pending_totals[m] - costs[m] for m in use_months]
        if prov_series != series:
            stats["provisional"] = int(statistics.median(prov_series))
            stats["pending_review_monthly"] = int(statistics.median(pending_totals[m] for m in use_months))
        if any(rotation_totals.values()):
            stats["income_if_flagged_real"] = int(statistics.median(totals[m] + pending_totals[m] + rotation_totals[m] - costs[m] for m in use_months))
        if len(series) >= 5:
            trimmed = sorted(series)[1:-1]
            stats["trimmed_mean"] = int(statistics.mean(trimmed))
        # how steady the business is reads from its receipts: net figures can be near or below zero
        if len(gross) >= 2 and statistics.mean(gross) > 0:
            stats["cv"] = round(statistics.pstdev(gross) / statistics.mean(gross), 3)
        # conservative: the level income reached in at least three of every four months (25th percentile)
        stats["conservative"] = max(0, int(statistics.quantiles(series, n=4, method="inclusive")[0]) if len(series) >= 3 else min(series))
        if len(series) >= 6:
            recent = statistics.mean(series[-3:])
            gross_recent, gross_prior = statistics.mean(gross[-3:]), statistics.mean(gross[-6:-3])
            change = (gross_recent - gross_prior) / gross_prior if gross_prior > 0 else 0
            stats["trend_3m"] = round(change, 3)
            if change <= -p["income_fall_pct"] and recent < income_value:
                income_value = int(recent)
                method = f"recent 3-month average, because {'receipts' if has_costs else 'income'} fell {abs(change) * 100:.0f}% versus the prior 3 months ({p['income_method']} {fmt_inr(income_value)})"
        if profile == "SELF_EMPLOYED" and stats.get("cv", 0) > 0.25:
            notes.append(f"Income is volatile (coefficient of variation {stats['cv']:.2f}); estimate uses the median, not the highest month. "
                         f"Conservative estimate {fmt_inr(stats['conservative'])}: income reached at least this in three of every four months.")
        pending = [e for e in excluded if "pending analyst review" in e["reason"]]
        if not any(gross) and pending:
            method = f"no confirmed income yet; {len(pending)} possible income credit(s) await analyst review"
        if "provisional" in stats and stats["provisional"] > (income_value or 0):
            notes.append(f"Confirmed income only. Including the {len(pending)} likely income credit(s) still awaiting review, the "
                         f"provisional estimate is {fmt_inr(stats['provisional'])}. Confirm them in the review queue to firm this up.")
    if "gross_receipts_monthly" in stats and any(cash_totals.values()):
        stats["income_if_cash_real"] = max(0, int(statistics.median(totals[m] + cash_totals[m] - costs[m] for m in use_months)))
        notes.append(f"Cash deposits of about {fmt_inr(int(statistics.median(cash_totals.values())))} a month are not counted until their source is verified, though the costs are. "
                     f"If all of that cash were real sales, this would read {fmt_inr(stats['income_if_cash_real'])}.")
    if income_value is not None and income_value < 0:
        notes.append(f"Identified business costs exceed confirmed receipts: the median month is {fmt_inr(income_value)}. Income is shown as zero, not as a negative amount.")
        income_value = 0
    if "gross_receipts_monthly" in stats:
        notes.append(f"Receipts of about {fmt_inr(stats['gross_receipts_monthly'])} a month, less about {fmt_inr(stats['business_costs_monthly'])} of identified suppliers, staff and tax. "
                     "General spending is not taken off, so the true margin may be lower. Rent and EMIs are counted as obligations instead.")
    if "income_if_flagged_real" in stats:
        notes.append(f"Possible money rotation is left out. If every flagged credit turned out to be real income, this would read {fmt_inr(stats['income_if_flagged_real'])}.")
    # months far from the typical level, which an average alone would hide. All income counts here, including side income a salaried estimate leaves out.
    received = {m: regular[m] + variable[m] for m in use_months}
    typical = int(statistics.median(received.values())) if received else 0
    unusual = []
    for m in use_months:
        if typical and received[m] >= p["income_spike_ratio"] * typical:
            unusual.append({"month": m, "kind": "spike", "value": received[m], "median": typical})
        elif typical and received[m] * p["income_spike_ratio"] <= typical:
            unusual.append({"month": m, "kind": "drop", "value": received[m], "median": typical})
    if profile == "SALARIED" and stats.get("variable_monthly"):
        notes.append(f"Variable income of about {fmt_inr(stats['variable_monthly'])} a month is shown separately and not counted in this salaried estimate.")
    cv = stats.get("cv", 0)
    consistency = "not enough months" if len(series) < 3 else "consistent" if cv < 0.15 else "moderately variable" if cv < p["income_volatile_cv"] else "highly variable"
    missing_for_income = [m for m in months if m not in eligible and m not in inactive]
    if missing_for_income:
        notes.append(f"Excluded months with incomplete coverage: {', '.join(missing_for_income)}. Missing months are not treated as zero.")
    if inactive:
        notes.append(f"The statement period starts before the first entry in {income_from}; the {len(inactive)} earlier month(s) with no activity are left out, not counted as zero.")
    metrics = {}
    metrics["estimated_income"] = {
        "key": "estimated_income", "label": "Estimated monthly income", "unit": "paise", "value": income_value, "method": method,
        "profile": profile, "monthly": [{"month": m, "value": totals[m] - costs[m], "gross": totals[m], "costs": costs[m], "regular": regular[m], "variable": variable[m]} for m in use_months], "stats": stats,
        "unusual_months": unusual, "consistency": consistency,
        "included": included, "excluded": excluded, "months_used": use_months, "months_missing": missing_for_income, "notes": notes}

    for r in monthly:
        if r["month"] in totals:
            r["income"] = totals[r["month"]] - costs[r["month"]]

    # ---------------------------------------------------------------- obligations
    groups = defaultdict(list)
    obl_excluded = []
    for t in txns:
        if t["direction"] != "D":
            continue
        if t["category"] in OBLIGATIONS and _held(t):
            obl_excluded.append({"txn_id": t["txn_id"], "reason": "Possible money rotation: left out until an analyst reviews it"})
        elif t["category"] in OBLIGATIONS:
            key = counterparty_key(t["counterparty"] or t["description_norm"][:24]) or "OTHER"
            groups[(t["category"], key)].append(t)
        elif t["category"] == "BOUNCE_RETURN":
            obl_excluded.append({"txn_id": t["txn_id"], "reason": "EMI debit returned unpaid (bounce); not counted as paid"})
    last_month = months[-1] if months else None
    obl_items, obl_included, obl_total, obl_pending = [], [], 0, 0
    for (cat, key), ts in sorted(groups.items()):
        per = defaultdict(int)
        for t in ts:
            per[_m(t["txn_date"])] += t["debit"]
        med = int(statistics.median(per.values()))
        recent = max(per) >= (months[-2] if len(months) >= 2 else last_month)
        recurring = len(per) >= p["obligation_min_months"] and recent
        # Unconfirmed items still count when they look like a real instalment (a missed EMI understates risk),
        # but a guessed label on payments of varying size must not add a commitment by itself.
        confirmed = any(t["status"] in TRUSTED for t in ts)
        stable = max(per.values()) <= p["stable_obligation_ratio"] * min(per.values())
        active = recurring and (confirmed or stable)
        pending = recurring and not active
        obl_items.append({"category": cat, "label": _label(cat), "counterparty": key, "monthly_amount": med, "months": sorted(per),
                          "active": active, "pending": pending, "why": ts[0]["explanation"], "txn_ids": [t["txn_id"] for t in ts]})
        if active:
            obl_total += med
            obl_included += [t["txn_id"] for t in ts]
        elif pending:
            obl_pending += med
            obl_excluded += [{"txn_id": t["txn_id"], "reason": "labelled EMI or rent but unconfirmed and not a stable monthly amount; awaiting analyst review"} for t in ts]
        else:
            obl_excluded += [{"txn_id": t["txn_id"], "reason": "not recurring or no longer active"} for t in ts]
    counted = set(obl_included)
    for r in monthly:
        r["obligations"] = sum(t["debit"] for t in by_month[r["month"]] if t["txn_id"] in counted)
    metrics["recurring_obligations"] = {
        "key": "recurring_obligations", "label": "Recurring obligations", "unit": "paise", "value": obl_total,
        "method": "sum of the median monthly amount of each active EMI and rent commitment", "items": obl_items,
        "included": obl_included, "excluded": obl_excluded, "months_used": months, "months_missing": [],
        "notes": ([f"A further {fmt_inr(obl_pending)} a month is labelled EMI or rent but is unconfirmed and irregular. Confirm it in the review queue if it is a real commitment."]
                  if obl_pending else [])}
    foir = round(obl_total / income_value, 3) if income_value else None
    metrics["foir"] = {"key": "foir", "label": "Obligation-to-income (FOIR)", "unit": "ratio", "value": foir,
                       "method": "recurring obligations / estimated monthly income",
                       "included": obl_included + included, "excluded": obl_excluded + excluded,
                       "months_used": use_months, "months_missing": [], "notes": [] if foir is not None else ["income could not be estimated"]}

    # ---------------------------------------------------------------- affordability against the rules in force
    max_foir, min_surplus = float(p["max_foir"]), int(p["min_surplus_after_obligations_paise"])
    rules = {"max_foir": max_foir, "min_surplus_after_obligations_paise": min_surplus}
    after_obligations = income_value - obl_total if income_value is not None else None
    covered = [r for r in monthly if r["coverage"] == "full" and r["active"]]
    achieved = int(statistics.mean(r["income"] - r["obligations"] for r in covered)) if covered else None

    def check(label: str, value, limit, ok: bool | None, shown: str) -> dict:
        return {"rule": label, "value": value, "limit": limit, "result": "not assessed" if ok is None else "within" if ok else "outside", "shown": shown}

    metrics["affordability"] = {
        "key": "affordability", "label": "Surplus after obligations", "unit": "paise", "value": after_obligations,
        "method": "estimated monthly income minus recurring obligations", "rules": rules, "rules_source": engine["name"],
        "checks": [check(f"Obligations at most {max_foir * 100:.0f}% of income", foir, max_foir, None if foir is None else foir <= max_foir, "" if foir is None else f"{foir * 100:.1f}%"),
                   check(f"Surplus after obligations at least {fmt_inr(min_surplus)}", after_obligations, min_surplus,
                         None if after_obligations is None else after_obligations >= min_surplus, fmt_inr(after_obligations))],
        "stats": ({"average_monthly_surplus": achieved} if achieved is not None else {}),
        "included": obl_included + included, "excluded": [], "months_used": [r["month"] for r in covered], "months_missing": [],
        "notes": ["These limits are fixed defaults, not a lender's policy: only Level 1 parameters can be set in a rule engine."]}

    # ---------------------------------------------------------------- regular outflows (informational)
    regular = []
    for cat in ("UTILITIES", "CARD_PAYMENT", "INSURANCE", "INVESTMENT", "TAX", "SUPPLIER_PAYMENT", "PAYROLL_PAYMENT"):
        ts = [t for t in txns if t["category"] == cat]
        if not ts:
            continue
        per = defaultdict(int)
        for t in ts:
            per[_m(t["txn_date"])] += t["debit"]
        regular.append({"category": cat, "label": _label(cat), "monthly_median": int(statistics.median(per.values())),
                        "months": len(per), "txn_ids": [t["txn_id"] for t in ts]})

    # ---------------------------------------------------------------- monthly surplus: what is left after typical outflows
    # unclassified debits are still money out; leaving them out would overstate what is left over
    spend_cats = [c for c, v in CATEGORIES.items() if v["treatment"] in ("expense", "obligation") or c == "UNCLASSIFIED"]
    if has_costs:  # already taken off income; counting them here too would charge them twice
        spend_cats = [c for c in spend_cats if c not in config.BUSINESS_COST_CATEGORIES]
    n_full = len(full_months)
    out_items, out_included, out_total = [], [], 0
    for cat in spend_cats:
        ts = [t for t in txns if t["category"] == cat and t["direction"] == "D" and _m(t["txn_date"]) in full_months and not _held(t)]
        if not ts or not n_full:
            continue
        per_month = int(sum(t["debit"] for t in ts) / n_full)  # average over covered months, so one-off bills are spread, not inflated
        out_total += per_month
        out_included += [t["txn_id"] for t in ts]
        out_items.append({"category": cat, "label": _label(cat), "counterparty": "", "monthly_amount": per_month,
                          "months": sorted({_m(t["txn_date"]) for t in ts}), "active": True, "txn_ids": [t["txn_id"] for t in ts]})
    out_items.sort(key=lambda i: -i["monthly_amount"])
    surplus = income_value - out_total if income_value is not None and n_full else None
    metrics["monthly_surplus"] = {
        "key": "monthly_surplus", "label": "Monthly surplus", "unit": "paise", "value": surplus,
        "method": f"estimated monthly income {fmt_inr(income_value)} minus typical monthly outflows {fmt_inr(out_total)} "
                  f"(average per fully covered month, by category)",
        "items": out_items, "outflows": out_total, "included": out_included,
        "excluded": [{"txn_id": t["txn_id"], "reason": f"{_label(t['category'])}: not spending"} for t in txns
                     if t["direction"] == "D" and t["category"] in EXCLUDED and not _held(t)]
                    + [{"txn_id": t["txn_id"], "reason": "Possible money rotation: not spending unless an analyst says so"} for t in txns
                       if t["direction"] == "D" and _held(t)]
                    + [{"txn_id": i, "reason": "Business cost: already taken off income"} for i in cost_ids],
        "months_used": full_months, "months_missing": [m for m in months if m not in full_months],
        "notes": ([] if surplus is None or surplus >= 0 else ["Typical outflows exceed estimated income."])
                 + ([f"With provisional income of {fmt_inr(stats['provisional'])}, the surplus would be {fmt_inr(stats['provisional'] - out_total)}."]
                    if stats.get("provisional", 0) > (income_value or 0) and n_full else [])}

    # ---------------------------------------------------------------- net cash flow and balances
    net_rows = [r for r in monthly if r["coverage"] == "full" and r["active"]]
    net_excl = [{"txn_id": t["txn_id"], "reason": f"{_label(t['category'])}: excluded from adjusted cash flow"}
                for t in txns if t["category"] in EXCLUDED]
    metrics["net_cash_flow"] = {
        "key": "net_cash_flow", "label": "Average monthly net cash flow", "unit": "paise",
        "value": int(statistics.mean(r["net"] for r in net_rows)) if net_rows else None,
        "method": "average of (adjusted credits - adjusted debits) over fully covered months; transfers, reversals, loan proceeds and bounces excluded",
        "monthly": [{"month": r["month"], "value": r["net"]} for r in net_rows],
        "included": [t["txn_id"] for t in txns if t["category"] not in EXCLUDED and _m(t["txn_date"]) in {r["month"] for r in net_rows}],
        "excluded": net_excl, "months_used": [r["month"] for r in net_rows],
        "months_missing": [m for m in months if combined[m] != "full"],
        "notes": ([f"Months before the first entry ({first_active}) are left out."] if first_active and months and first_active > months[0] else [])}
    combined_days = defaultdict(dict)
    for acct, ser in balances.items():
        for d, v in ser.items():
            combined_days[d][acct] = v
    total_series = sorted((d, sum(v.values())) for d, v in combined_days.items() if len(v) == len(balances))
    avg_bal = int(statistics.mean(v for _, v in total_series)) if total_series else None
    month_avgs = [(r["month"], r["avg_balance"]) for r in monthly if r["avg_balance"] is not None]
    metrics["average_balance"] = {
        "key": "average_balance", "label": "Average daily balance", "unit": "paise", "value": avg_bal,
        "method": "mean end-of-day balance across all accounts on days every account is covered",
        "monthly": [{"month": m, "value": v} for m, v in month_avgs],
        "trend": round((month_avgs[-1][1] - month_avgs[0][1]) / month_avgs[0][1], 3) if len(month_avgs) >= 2 and month_avgs[0][1] else None,
        "included": [t["txn_id"] for t in txns if t["balance"] is not None and _m(t["txn_date"]) in dict(month_avgs)],
        "excluded": [{"txn_id": t["txn_id"], "reason": "no running balance printed on this row"} for t in txns if t["balance"] is None],
        "months_used": [m for m, _ in month_avgs],
        "months_missing": [m for m in months if m not in dict(month_avgs)], "notes": []}

    # ---------------------------------------------------------------- is cash flow getting better or worse
    trend_status, trend_months, change, prior_avg, recent_avg = "not enough months", [], None, None, None
    if len(net_rows) >= 3:
        h = len(net_rows) // 2
        prior_rows, recent_rows = net_rows[:-h], net_rows[-h:]
        prior_avg, recent_avg = int(statistics.mean(r["net"] for r in prior_rows)), int(statistics.mean(r["net"] for r in recent_rows))
        change = recent_avg - prior_avg
        scale = statistics.mean(r["credits_adjusted"] for r in net_rows) * p["cash_flow_trend_share"]
        trend_status = "improving" if change > scale else "deteriorating" if change < -scale else "stable"
        # the months that moved the comparison most: those furthest from the other period's average, in the direction of the change
        ranked = sorted(recent_rows, key=lambda r: r["net"], reverse=trend_status == "improving")
        trend_months = [r["month"] for r in ranked[:2]] if trend_status != "stable" else []
    metrics["cash_flow_trend"] = {
        "key": "cash_flow_trend", "label": "Cash-flow trend", "unit": "paise", "value": change, "assessment": trend_status,
        "method": f"average net cash flow of the later months against the earlier ones; a change above {p['cash_flow_trend_share'] * 100:.0f}% of average monthly inflows counts",
        "stats": ({"earlier_months_average": prior_avg, "later_months_average": recent_avg} if prior_avg is not None else {}),
        "monthly": [{"month": r["month"], "value": r["net"]} for r in net_rows], "highlight_months": trend_months,
        "included": [i for r in net_rows if r["month"] in trend_months for i in r["credit_ids"] + r["debit_ids"]],
        "excluded": [], "months_used": [r["month"] for r in net_rows], "months_missing": [m for m in months if combined[m] != "full"], "notes": []}

    # ---------------------------------------------------------------- each account on its own
    acct_info = {a["account_id"]: a for a in (accounts or [])}
    account_view = []
    for acct in per_acct:
        rows = []
        for r in monthly:
            ts = [t for t in by_month[r["month"]] if t["account_id"] == acct]
            cr, dr = [sum(t[k] for t in ts if t["category"] not in EXCLUDED) for k in ("credit", "debit")]
            bal = r["balances"].get(acct, {})
            rows.append({"month": r["month"], "coverage": per_acct[acct].get(r["month"], "missing"), "credits": sum(t["credit"] for t in ts), "debits": sum(t["debit"] for t in ts),
                         "credits_adjusted": cr, "debits_adjusted": dr, "net": cr - dr, "avg_balance": bal.get("avg"), "min_balance": bal.get("min"), "n_txns": len(ts),
                         "credit_ids": [t["txn_id"] for t in ts if t["credit"]], "debit_ids": [t["txn_id"] for t in ts if t["debit"]]})
        info = acct_info.get(acct, {})
        account_view.append({"account_id": acct, "bank_name": info.get("bank_name"), "acct_masked": info.get("acct_masked"), "currency": info.get("currency", "INR"),
                             "months": rows, "missing_months": [x["month"] for x in rows if x["coverage"] == "missing"],
                             "partial_months": [x["month"] for x in rows if x["coverage"] == "partial"]})

    # ---------------------------------------------------------------- money moving around
    flows = money_flows(txns, links or [], p)
    transfers = [t for t in txns if t["direction"] == "C" and t["category"] == "INTERNAL_TRANSFER" and t["status"] in TRUSTED]
    held_credits = [t for t in txns if t["direction"] == "C" and _held(t)]
    all_credits = sum(t["credit"] for t in txns)
    moved = sum(t["credit"] for t in transfers) + sum(t["credit"] for t in held_credits)
    metrics["money_movement"] = {
        "key": "money_movement", "label": "Money moved around", "unit": "paise", "value": moved,
        "method": "credits that are confirmed transfers between the customer's own accounts, plus credits held as possible rotation",
        "stats": {"own_account_transfers": sum(t["credit"] for t in transfers), "possible_rotation": sum(t["credit"] for t in held_credits),
                  "share_of_all_credits": round(moved / all_credits, 3) if all_credits else 0},
        "included": [t["txn_id"] for t in transfers + held_credits],
        "excluded": [], "months_used": months, "months_missing": [], "notes": []}

    history.account_history(account_view, txns, statements, obl_items, cross_checks)
    tracked = history.earnings(metrics["estimated_income"], txns, monthly, p["customer_concentration_share"])
    asks = history.statement_requests(txns, accounts or [], patterns, links or [], profile, income_value)

    balance_series = []
    for d, total in total_series:
        balance_series.append({"date": d.isoformat(), "total": total})
    per_account_series = {acct: [{"date": d.isoformat(), "balance": v} for d, v in sorted(ser.items())] for acct, ser in balances.items()}
    return {"months": months, "coverage": combined, "monthly": monthly, "metrics": metrics, "regular_outflows": regular,
            "balance_series": balance_series, "account_balance_series": per_account_series, "profile": profile, "money_flows": flows, "account_view": account_view, "earnings": tracked, "statement_requests": asks, "engine": {"id": engine["engine_id"], "name": engine["name"], "version": engine["version"]}}
