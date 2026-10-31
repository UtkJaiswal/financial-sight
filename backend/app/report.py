"""The assessment as one evidence-linked document: every section names the transactions behind it, with file, page and row."""
from __future__ import annotations

from . import analysis, fx
from .db import q, q1
from .metrics import effective

NOTICE = "Information for credit review. This report does not approve or decline credit, and no indicator is a finding of fraud."


def build(case_id: str) -> dict:
    case = q1("SELECT * FROM silver.cases WHERE case_id = ?", [case_id])
    a = analysis.current(case_id)
    txns = {t["txn_id"]: t for t in effective.transactions(case_id)}
    sources: dict[str, list[dict]] = {}
    for s in q("""SELECT s.txn_id, f.filename, s.page_no, s.row_no FROM silver.txn_sources s JOIN bronze.files f USING (file_id)
                  WHERE f.case_id = ? ORDER BY f.filename, s.page_no, s.row_no""", [case_id]):
        sources.setdefault(s["txn_id"], []).append({"file": s["filename"], "page": s["page_no"], "row": s["row_no"]})
    m = a.get("metrics", {})

    def metric(key: str) -> dict:
        return m.get(key, {})

    inc, obl, aff, trend = metric("estimated_income"), metric("recurring_obligations"), metric("affordability"), metric("cash_flow_trend")
    sections = [
        {"key": "income", "title": "Sustainable income", "evidence": inc.get("included", []),
         "excluded": inc.get("excluded", []), "metric": "estimated_income"},
        {"key": "obligations", "title": "Obligations and affordability", "evidence": obl.get("included", []),
         "excluded": obl.get("excluded", []), "metric": "recurring_obligations"},
        {"key": "cash_flow", "title": "Cash-flow trend", "evidence": trend.get("included", []), "excluded": [], "metric": "cash_flow_trend"},
        {"key": "consolidation", "title": "Accounts and consolidation", "evidence": metric("money_movement").get("included", []), "excluded": [],
         "metric": "money_movement"},
    ]
    used = {i for s in sections for i in s["evidence"]} | {e["txn_id"] for s in sections for e in s["excluded"]}
    used |= {i for f in a.get("flags", []) for i in f["txn_ids"]}
    used |= {i for r in ((a.get("credit_score") or {}).get("parameters") or []) for i in r["txn_ids"]}
    used |= {i for g in ((a.get("statement_requests") or {}).get("items") or []) for i in g["txn_ids"]}
    used |= {i for p in ((a.get("earnings") or {}).get("payers") or []) for i in p["txn_ids"]}
    evidence = {}
    for i in used:
        t = txns.get(i)
        if t:
            evidence[i] = {"date": t["txn_date"], "description": t["description"], "direction": t["direction"], "amount": t["amount"],
                           "currency": t["currency"], "original": t["orig_credit"] or t["orig_debit"], "fx_rate": t["fx_rate"],
                           "category": t["category"], "status": t["status"], "why": t["explanation"], "account": f"{t['bank_name']} {t['acct_masked'][-4:]}",
                           "sources": sources.get(i, [])}
    return {"case": {"name": case["name"], "application_date": case["application_date"], "case_id": case_id},
            "notice": NOTICE, "snapshot_id": a.get("snapshot_id"), "versions": a.get("versions"), "reporting_currency": fx.REPORTING,
            "profile": a.get("profile"), "sections": sections, "metrics": {k: metric(k) for k in ("estimated_income", "recurring_obligations", "foir", "affordability",
                                                                                                  "monthly_surplus", "cash_flow_trend", "net_cash_flow", "average_balance", "money_movement")},
            "monthly": [{k: r[k] for k in ("month", "coverage", "active", "credits", "debits", "credits_adjusted", "debits_adjusted", "net", "avg_balance", "income", "obligations")} for r in a.get("monthly", [])],
            "accounts": a.get("account_view", []), "statements": a.get("statements", []), "cross_checks": a.get("cross_checks", []),
            "money_flows": a.get("money_flows", {}), "flags": a.get("flags", []), "fx": a.get("fx", {}), "rules": aff.get("rules", {}),
            "corrections": q("SELECT txn_id, from_category, to_category, reason, actor, created_at FROM silver.overrides WHERE case_id = ? ORDER BY created_at", [case_id]),
            "evidence": evidence, "credit_score": a.get("credit_score"), "engine": a.get("engine"), "reconciliation": a.get("reconciliation"), "earnings": a.get("earnings"),
            "statement_requests": a.get("statement_requests")}
