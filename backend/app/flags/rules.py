"""Risk indicators. Every flag says 'review recommended' with its reason and evidence, never 'fraud'."""
from __future__ import annotations

import statistics
from datetime import timedelta

from .. import config
from ..classify.categories import INCOME
from ..money import fmt_inr, fmt_money


def _flag(code, severity, title, detail, txn_ids=None, months=None, category="risk"):
    return {"code": code, "severity": severity, "title": title, "detail": detail, "txn_ids": txn_ids or [], "months": months or [],
            "category": category, "rule": ""}


# What triggered each indicator, shown beside it. These are information for credit review, not decisions.
def rule_texts(p: dict) -> dict:
    return {
        "income_decline": f"Income in the latest covered months (up to 3) averages at least {p['income_decline_share'] * 100:.0f}% below the same number of months before. 35% or more is high.",
        "rising_expenses": f"Average monthly outflows in the later half of covered months are at least {p['rising_expense_share'] * 100:.0f}% above the earlier half.",
        "multiple_large_credits": "Two or more non-routine credits, each at least ₹50,000 and at least 1.5 times estimated monthly income.",
        "behaviour_change": "The latest covered month has inflows, outflows or transaction count at least double or at most half the typical earlier month.",
        "negative_net_cash_flow": f"Adjusted outflows exceed inflows in at least {p['negative_cash_flow_months']} fully covered months.",
        "low_balance": f"End-of-day balance below {fmt_inr(p['low_balance_paise'])} on at least {p['low_balance_days_share'] * 100:.0f}% of covered days.",
        "returned_payment": "Any payment returned unpaid, or a charge for one.",
        "missed_salary": "A monthly salary series has no salary credit in a fully covered month.",
        "missed_obligation": "A monthly EMI or rent series has no payment in a fully covered month.",
        "large_cash_deposit": f"Cash deposit of at least {fmt_inr(p['large_cash_paise'])}, or at least ₹1,00,000 and three times the median credit.",
        "round_figure_pre_application": f"Round-figure credit of at least {fmt_inr(p['round_figure_min_paise'])} within {p['pre_application_days']} days before the application.",
        "large_unexplained_deposit": f"Credit of at least {fmt_inr(p['unexplained_deposit_paise'])} with no confirmed explanation.",
        "income_volatility": f"Monthly income's coefficient of variation is at least {p['income_volatile_cv']:.2f}, over at least 3 covered months.",
        "income_unusual_month": f"A month's income is at least {p['income_spike_ratio']} times the median, or at most the median divided by {p['income_spike_ratio']}.",
        "possible_rotation": "A similar amount across own accounts within 7 days, split into 2 or 3 entries, out and back with one party within 15 days, or repeatedly leaving within 3 days of arriving.",
        "circular_flow": "Matched movements of about the same amount, each within 7 days of the last, returning to the first account.",
        "account_missing_months": "An account has no statement for a month that other statements in this case cover.",
        "fx_rate_missing": "A foreign-currency row has no supplied exchange rate for its month.",
        "foreign_currency_income": "Part of estimated income is in a foreign currency.",
        "customer_concentration": f"One payer accounts for at least {p['customer_concentration_share'] * 100:.0f}% of counted income, over at least 3 months.",
        "request_statement": "Transfers name the customer's own name or another account of theirs with no matching entry, or a salaried customer sends a steady sum out each month.",
        "possible_document_edit": "Edit-risk score of 30 or more from the PDF's structure and arithmetic: editor or office software as producer, incremental saves, modified after creation, mismatched fonts in a table, text printed over other text, unreconciled totals or broken balance chains.",
        "reconciliation": "Opening balance plus credits minus debits differs from the stated closing balance, or a row breaks the running balance.",
        "coverage_gap": "Dates between two statements of the same account that no statement covers.",
        "statement_balance_mismatch": "A statement's opening balance differs from the balance carried from the statement before it.",
        "overlapping_statements": "Two statements of the same account cover some of the same dates. Shared rows count once.",
        "duplicate_upload": "The same file was uploaded again. It is processed once.",
        "file_not_processed": "A file yielded no transactions or could not be opened.",
        "page_not_processed": "A page has no text layer, or some of its rows could not be read.",
        "review_queue": "Evidence below 90% confidence or conflicting, so an analyst decides.",
        "several_salary_payers": f"More than {p['salary_max_payers_in_month']} different payers made salary credits in one month.",
    }


def evaluate(case: dict, txns: list[dict], gold: dict, ctx: dict) -> list[dict]:
    limits = ctx["engine"]["params"]
    flags = []
    m = gold["metrics"]
    inc = m["estimated_income"]
    # receipts before costs for a business: its net figure can be near or below zero, which makes percentages meaningless
    series = [x.get("gross", x["value"]) for x in inc["monthly"]]
    if len(series) >= 4:
        h = min(3, len(series) // 2)  # latest months vs the same number before them
        recent, prior = statistics.mean(series[-h:]), statistics.mean(series[-2 * h:-h])
        if prior and (recent - prior) / prior <= -limits["income_decline_share"]:
            drop = (prior - recent) / prior
            flags.append(_flag("income_decline", "high" if drop >= 0.35 else "medium", "Declining income",
                               f"Income in the last {h} covered months averaged {fmt_inr(int(recent))}, down {drop * 100:.0f}% from "
                               f"{fmt_inr(int(prior))} in the {h} months before.", months=[x["month"] for x in inc["monthly"][-2 * h:]],
                               txn_ids=[t["txn_id"] for t in txns if t["txn_id"] in set(inc.get("included", [])) and f"{t['txn_date']:%Y-%m}" in {x["month"] for x in inc["monthly"][-h:]}]))
    full = [r for r in gold["monthly"] if r["coverage"] == "full" and r.get("active", True)]
    # rising expenses: later half of the covered months vs the earlier half
    if len(full) >= 4:
        h = len(full) // 2
        early, late = statistics.mean(r["debits_adjusted"] for r in full[:h]), statistics.mean(r["debits_adjusted"] for r in full[-h:])
        if early and (late - early) / early >= limits["rising_expense_share"]:
            flags.append(_flag("rising_expenses", "medium", "Expenses rising",
                               f"Average monthly outflows rose {(late - early) / early * 100:.0f}%, from {fmt_inr(int(early))} in "
                               f"{full[0]['month']}–{full[h - 1]['month']} to {fmt_inr(int(late))} in {full[-h]['month']}–{full[-1]['month']}.",
                               months=[r["month"] for r in full[-h:]], txn_ids=[i for r in full[-h:] for i in r["debit_ids"]]))
    # several unusually large, non-routine credits
    income_est = inc.get("value") or 0
    routine = {"SALARY", "INTERNAL_TRANSFER", "REFUND_REVERSAL", "LOAN_DISBURSAL", "BOUNCE_RETURN"}
    credit_amts = [t["credit"] for t in txns if t["direction"] == "C" and t["category"] not in routine]
    base = income_est or (statistics.median(credit_amts) * 2 if credit_amts else 0)
    large = [t for t in txns if t["direction"] == "C" and t["category"] not in routine and base and t["credit"] >= 1.5 * base
             and t["credit"] >= 50_000_00]
    if len(large) >= 2:
        flags.append(_flag("multiple_large_credits", "medium", "Several unusually large credits",
                           f"{len(large)} credits of at least 1.5× the estimated monthly income ({fmt_inr(int(base))}): "
                           + ", ".join(f"{fmt_inr(t['credit'])} on {t['txn_date']:%d %b}" for t in large[:5]) + ". Confirm their source.",
                           txn_ids=[t["txn_id"] for t in large]))
    # sudden behaviour change: the latest covered month against the typical earlier month
    if len(full) >= 3:
        last, prior = full[-1], full[:-1]
        changes = []
        for key, label in (("credits_adjusted", "inflows"), ("debits_adjusted", "outflows"), ("n_txns", "transaction count")):
            typical = statistics.median(r[key] for r in prior)
            if typical and (last[key] >= 2 * typical or last[key] <= 0.5 * typical):
                shown = (lambda v: str(int(v))) if key == "n_txns" else (lambda v: fmt_inr(int(v)))
                changes.append(f"{label} {shown(last[key])} vs a typical {shown(typical)}")
        if changes:
            flags.append(_flag("behaviour_change", "medium", "Sudden change in account behaviour",
                               f"In {last['month']}: " + "; ".join(changes) + ".", months=[last["month"]], txn_ids=last["credit_ids"] + last["debit_ids"]))
    neg = [r for r in full if r["net"] < 0]
    if len(neg) >= limits["negative_cash_flow_months"]:
        flags.append(_flag("negative_net_cash_flow", "medium", "Repeated negative net cash flow",
                           f"Outflows exceeded inflows in {len(neg)} fully covered months: "
                           + ", ".join(f"{r['month']} ({fmt_inr(r['net'])})" for r in neg) + ".", months=[r["month"] for r in neg],
                           txn_ids=[i for r in neg for i in r["credit_ids"] + r["debit_ids"]]))
    accts = {a["account_id"]: a for a in ctx["accounts"]}
    for acct, a in accts.items():
        low = [(r["month"], r["balances"][acct]["low_days"], r["balances"][acct]["days"]) for r in gold["monthly"] if acct in r["balances"]]
        low_days, days = sum(x[1] for x in low), sum(x[2] for x in low)
        if days and low_days / days >= limits["low_balance_days_share"]:
            flags.append(_flag("low_balance", "medium", "Frequent low balances",
                               f"{a['bank_name']} {a['acct_masked']} closed below {fmt_inr(limits['low_balance_paise'])} on {low_days} of {days} "
                               f"covered days.", months=[x[0] for x in low if x[1]],
                               txn_ids=[t["txn_id"] for t in txns if t["account_id"] == acct and t["balance"] is not None and t["balance"] < limits["low_balance_paise"]]))
    bounces = [t for t in txns if t["category"] == "BOUNCE_RETURN" or "bounce_charge" in t["attributes"]]
    if bounces:
        flags.append(_flag("returned_payment", "high", "Returned / bounced payment",
                           f"{sum(1 for t in bounces if t['category'] == 'BOUNCE_RETURN' and t['direction'] == 'C')} payment(s) returned unpaid, "
                           f"with {sum(1 for t in bounces if 'bounce_charge' in t['attributes'])} return charge(s).",
                           txn_ids=[t["txn_id"] for t in bounces]))
    by_id = {t["txn_id"]: t for t in txns}
    for p in ctx["patterns"]:
        members = [by_id[i] for i in p["txn_ids"] if i in by_id]
        cats = {t["category"] for t in members}
        if not p["missed_months"] or not members:
            continue
        if "SALARY" in cats:
            # a month with any salary credit on the account is not missed, even if worded differently from the series
            paid = {f"{t['txn_date']:%Y-%m}" for t in txns if t["category"] == "SALARY" and t["account_id"] == p["account_id"]}
            p = dict(p, missed_months=[m for m in p["missed_months"] if m not in paid])
            if not p["missed_months"]:
                continue
            flags.append(_flag("missed_salary", "high", "Expected salary not received",
                               f"Monthly salary of about {fmt_inr(p['median_amount'])} (day ~{p['typical_day']}) did not arrive in "
                               f"{', '.join(p['missed_months'])}, although those months are fully covered.", txn_ids=p["txn_ids"], months=p["missed_months"]))
        elif cats & {"LOAN_EMI", "RENT"}:
            flags.append(_flag("missed_obligation", "medium", "Expected EMI or rent not seen",
                               f"A recurring payment of about {fmt_inr(p['median_amount'])} was not seen in {', '.join(p['missed_months'])}.",
                               txn_ids=p["txn_ids"], months=p["missed_months"]))
    credits = [t["credit"] for t in txns if t["direction"] == "C"]
    med_credit = statistics.median(credits) if credits else 0
    big_cash = [t for t in txns if t["category"] == "CASH_DEPOSIT"
                and (t["credit"] >= limits["large_cash_paise"] or (med_credit and t["credit"] >= 3 * med_credit and t["credit"] >= 1_00_000_00))]
    if big_cash:
        flags.append(_flag("large_cash_deposit", "medium", "Large cash deposit",
                           "Cash deposited without a documented source: " + ", ".join(f"{fmt_inr(t['credit'])} on {t['txn_date']:%d %b %Y}" for t in big_cash)
                           + ". Verify the source before relying on it.", txn_ids=[t["txn_id"] for t in big_cash]))
    app = case.get("application_date")
    if app:
        pre = [t for t in txns if t["direction"] == "C" and t["credit"] >= limits["round_figure_min_paise"] and t["credit"] % 1_000_000 == 0
               and app - timedelta(days=limits["pre_application_days"]) <= t["txn_date"] <= app
               and t["category"] not in ("SALARY", "INTERNAL_TRANSFER", "REFUND_REVERSAL", "LOAN_DISBURSAL", "CASH_DEPOSIT")]
        if pre:
            flags.append(_flag("round_figure_pre_application", "medium", "Round-figure credit shortly before application",
                               ", ".join(f"{fmt_inr(t['credit'])} on {t['txn_date']:%d %b %Y}" for t in pre)
                               + f", within {limits['pre_application_days']} days of the application date. Confirm the source.",
                               txn_ids=[t["txn_id"] for t in pre]))
    acct_label = {a["account_id"]: f"{a['bank_name']} {a['acct_masked'][-4:]}" for a in ctx["accounts"]}
    held = [t for t in txns if "possible_rotation" in t["attributes"] and t["status"] not in ("auto", "confirmed")]
    if held:
        held_credits = sum(t["credit"] for t in held)
        all_credits = sum(t["credit"] for t in txns)
        real = inc.get("stats", {}).get("income_if_flagged_real")
        flags.append(_flag("possible_rotation", "high" if all_credits and held_credits / all_credits >= 0.25 else "medium", "Possible money rotation",
                           f"{len(held)} entries, including {fmt_inr(held_credits)} of credits, look like the same money moving around. "
                           + (f"Income leaves them out and reads {fmt_inr(income_est)}; if all were real it would read {fmt_inr(real)}. " if real else "Income leaves them out. ")
                           + "Review recommended.", txn_ids=[t["txn_id"] for t in held]))
    flows = gold.get("money_flows", {})
    for cy in flows.get("cycles", []):
        path = " → ".join(acct_label.get(a, "another account") for a in cy["path"])
        flags.append(_flag("circular_flow", "medium", "Money moving in a circle",
                           f"{fmt_inr(cy['amount'])} went round {path} in {cy['count']} round(s). It is not counted as income. "
                           "Review recommended: ask what these movements are for.", txn_ids=cy["txn_ids"]))
    # income that swings a lot, and single months far from the usual level
    cv = inc.get("stats", {}).get("cv")
    if cv is not None and len(series) >= 3 and cv >= limits["income_volatile_cv"]:
        flags.append(_flag("income_volatility", "high" if cv >= 2 * limits["income_volatile_cv"] else "medium", "High income volatility",
                           f"Monthly income varies widely (coefficient of variation {cv:.2f}), from {fmt_inr(inc['stats']['lowest'])} to {fmt_inr(inc['stats']['highest'])}.",
                           txn_ids=inc.get("included", []), months=[x["month"] for x in inc["monthly"]]))
    for u in inc.get("unusual_months", []):
        word = "above" if u["kind"] == "spike" else "below"
        flags.append(_flag("income_unusual_month", "low", f"Unusual income in {u['month']}", f"{fmt_inr(u['value'])}, well {word} the median month of {fmt_inr(u['median'])}.",
                           months=[u["month"]], txn_ids=[t["txn_id"] for t in txns if t["category"] in INCOME and t["status"] in ("auto", "confirmed") and f"{t['txn_date']:%Y-%m}" == u["month"]]))
    salary_many = [t for t in txns if "several_salary_payers" in t["attributes"] and t["status"] != "confirmed"]
    if salary_many:
        flags.append(_flag("several_salary_payers", "medium", "Salary from several payers in one month",
                           f"{len(salary_many)} credits are worded as salary, but more than {limits['salary_max_payers_in_month']} different payers made them in the same month. "
                           "Income leaves them out until reviewed.", txn_ids=[t["txn_id"] for t in salary_many]))
    unexplained = [t for t in txns if t["direction"] == "C" and t["credit"] >= limits["unexplained_deposit_paise"] and t["category"] in ("UNCLASSIFIED", "OTHER_CREDIT")
                   and t["status"] != "confirmed" and "possible_rotation" not in t["attributes"]]
    if unexplained:
        flags.append(_flag("large_unexplained_deposit", "medium", "Large unexplained deposits",
                           f"{len(unexplained)} credit(s) totalling {fmt_inr(sum(t['credit'] for t in unexplained))} have no confirmed explanation. Ask for their source.",
                           txn_ids=[t["txn_id"] for t in unexplained]))
    foreign = [t for t in txns if t["currency"] != "INR" and t["txn_id"] in set(inc.get("included", []))]
    if foreign:
        share = sum(t["credit"] for t in foreign) / max(1, sum(t["credit"] for t in txns if t["txn_id"] in set(inc.get("included", []))))
        flags.append(_flag("foreign_currency_income", "info", "Income in a foreign currency",
                           f"{share * 100:.0f}% of counted income is in {', '.join(sorted({t['currency'] for t in foreign}))}, converted at supplied rates. Its rupee value moves with the rate.",
                           txn_ids=[t["txn_id"] for t in foreign]))
    tracked = gold.get("earnings") or {}
    if tracked.get("concentrated") and gold.get("profile") == "SELF_EMPLOYED":
        top = tracked["payers"][0]
        flags.append(_flag("customer_concentration", "medium", "Income depends on one payer",
                           f"{top['name']} paid {top['share'] * 100:.0f}% of counted income ({fmt_inr(top['amount'])}).", txn_ids=top["txn_ids"]))
    asks = gold.get("statement_requests") or {}
    if asks.get("items"):
        flags.append(_flag("request_statement", "medium", "Statements to ask for",
                           f"{len(asks['items'])} set(s) of transfers point at an account with no uploaded statement. Until it arrives they cannot be matched.",
                           txn_ids=[i for g in asks["items"] for i in g["txn_ids"]], category="data"))
    # data quality
    fx_view = ctx.get("fx", {})
    if fx_view.get("no_rate_rows"):
        missing = sorted({c for c, _ in fx_view["missing"]})
        flags.append(_flag("fx_rate_missing", "high", f"No exchange rate for {', '.join(missing)}",
                           f"{fx_view['no_rate_rows']} row(s) are left out of every figure until a rate is supplied. No rate was assumed.",
                           txn_ids=fx_view["no_rate_txn_ids"], months=sorted({m for _, m in fx_view["missing"]}), category="data"))
    for v in gold.get("account_view", []):
        gaps = v["missing_months"]
        if gaps:
            flags.append(_flag("account_missing_months", "medium", f"Missing statement months ({v['bank_name']} {v['acct_masked'][-4:]})",
                               f"No statement for {', '.join(gaps)}, though the case covers those months. They are not treated as zero.", months=gaps, category="data"))
    within = {x["statement_id"] for x in (ctx.get("reconciliation") or {}).get("statements", []) if x["result"] == "within"}
    for s in ctx["statements"]:
        if s["statement_id"] not in within and s["recon_status"] == "reconciled_no_closing" and limits["recon_require_closing"]:
            flags.append(_flag("reconciliation", "medium", f"No closing balance to reconcile to ({s['bank_name']} {s['acct_masked']})",
                               "This lender's engine requires a stated closing balance.", category="data"))
        if s["statement_id"] not in within and s["recon_status"] in ("unreconciled", "breaks", "reconciled_with_row_breaks"):
            breaks = [t for t in txns if t["recon_status"] == "break" and t["account_id"] == s["account_id"]
                      and s["period_start"] <= t["txn_date"] <= s["period_end"]]
            sev = "medium" if s["recon_status"] == "unreconciled" else "low"
            detail = (f"Opening + credits - debits differs from the stated closing balance by {fmt_money(s['recon_diff'], s.get('currency') or 'INR')}. "
                      if s["recon_status"] == "unreconciled" else "Totals reconcile, but ")
            detail += f"{s['n_breaks']} row(s) break the running-balance chain." if s["n_breaks"] else ""
            flags.append(_flag("reconciliation", sev, f"Statement does not fully reconcile ({s['bank_name']} {s['acct_masked']})",
                               detail.strip(), txn_ids=[t["txn_id"] for t in breaks], category="data"))
    acct_name = {a["account_id"]: f"{a['bank_name']} {a['acct_masked']}" for a in ctx["accounts"]}
    for c in ctx["cross_checks"]:
        where = acct_name.get(c["account_id"], "")
        detail = c["detail"][:1].upper() + c["detail"][1:]
        if c["kind"] == "gap":
            flags.append(_flag("coverage_gap", "medium", f"Missing statement period ({where})", detail + ". Metrics skip these months.",
                               months=[], category="data"))
        elif c["kind"] == "balance_mismatch":
            flags.append(_flag("statement_balance_mismatch", "medium", f"Statements do not chain ({where})", detail + ".", category="data"))
        elif c["kind"] == "overlap":
            flags.append(_flag("overlapping_statements", "info", f"Overlapping statements ({where})", detail + ".", category="data"))
    if ctx["duplicate_uploads"]:
        flags.append(_flag("duplicate_upload", "info", "Duplicate upload ignored",
                           f"{len(ctx['duplicate_uploads'])} file(s) were uploaded more than once: "
                           + ", ".join(sorted({d['filename'] for d in ctx['duplicate_uploads']})) + ". Each was processed once.", category="data"))
    for d in ctx.get("edit_checks", []):
        if d["level"] in ("medium", "high"):
            reasons = "; ".join(s["detail"].rstrip(".") for s in d["signals"] if s["weight"] > 0)
            flags.append(_flag("possible_document_edit", "high" if d["level"] == "high" else "medium",
                               f"Possible editing: {d['filename']} (edit risk {d['score']}/100)",
                               reasons + ". Verify against a copy obtained directly from the bank or via Account Aggregator."))
    for f in ctx["files"]:
        if f["status"] in ("failed", "needs_password"):
            flags.append(_flag("file_not_processed", "high", f"File not processed: {f['filename']}", f["reason"] or "", category="data"))
    for p in ctx["bad_pages"]:
        flags.append(_flag("page_not_processed", "high", f"Page {p['page_no']} of {p['filename']} not read", p["reason"] or "", category="data"))
    review = [t for t in txns if t["status"] in ("review", "unclassified")]
    if review:
        flags.append(_flag("review_queue", "low", f"{len(review)} transaction(s) need a category decision",
                           "Low-confidence or conflicting evidence. Income excludes these until confirmed.", txn_ids=[t["txn_id"] for t in review],
                           category="review"))
    texts = rule_texts(limits)
    for f in flags:
        f["rule"] = texts.get(f["code"], "")
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    return sorted(flags, key=lambda f: (order[f["severity"]], f["category"] != "risk", f["title"]))
