"""Credit score: one number from the lender's own parameters and the Level 1 calculations, with every point shown.

Each parameter is measured from the statements and earns points in a straight line between the engine's zero_at and full_at.
The score is points earned as a share of the weight that could be assessed. A parameter that cannot be measured is left out and
named, never assumed; below the engine's minimum assessed weight no score is given at all.

This is a statement-based score built on hand-set parameters. It is not a bureau score, and it has not been calibrated against
repayment outcomes. It informs a credit review; it does not decide one.
"""
from __future__ import annotations

import statistics

from ..rule_engine import SCORE_DEFAULTS

NOTICE = ("Based only on these statements and this lender's rule engine. Not a bureau score, not calibrated against repayment outcomes, "
          "and not a credit decision.")


def _points(value: float, weight: float, full_at: float, zero_at: float) -> float:
    share = (value - zero_at) / (full_at - zero_at)
    return round(weight * min(1.0, max(0.0, share)), 2)


def compute(gold: dict, txns: list[dict], engine: dict, recon: dict) -> dict:
    m = gold["metrics"]
    inc, ob, bal = m["estimated_income"], m["recurring_obligations"], m["average_balance"]
    income = inc.get("value")
    gross_confirmed = sum(x.get("gross", x["value"]) for x in inc.get("monthly", []))
    # Costs above confirmed receipts is a real result, and scores as such. No confirmed receipts at all means income is still unknown.
    income_known = gross_confirmed > 0 and income is not None
    full = [r for r in gold["monthly"] if r["coverage"] == "full" and r.get("active", True)]
    credits, debits = [r["credits_adjusted"] for r in full], [r["debits_adjusted"] for r in full]
    all_ids = [i for r in full for i in r["credit_ids"] + r["debit_ids"]]
    credit_ids = [i for r in full for i in r["credit_ids"]]
    debit_ids = [i for r in full for i in r["debit_ids"]]
    avg_credits = statistics.mean(credits) if credits else 0
    if not income_known:
        foir = None
    elif income == 0:
        foir = 1.0  # costs and obligations already take everything confirmed
    else:
        foir = ob["value"] / income
    values = {
        "estimated_income": (income if income_known else None, inc.get("included", []), "No confirmed income yet."),
        "recurring_obligations": (foir, ob.get("included", []), "Income is not confirmed yet, so this share cannot be worked out."),
        "net_cash_flow": (statistics.mean(r["net"] for r in full) / avg_credits if avg_credits else None, all_ids, "No fully covered months with credits."),
        "monthly_credits": (statistics.pstdev(credits) / avg_credits if len(credits) >= 3 and avg_credits else None, credit_ids, "Needs at least 3 fully covered months."),
        "monthly_debits": (sum(debits) / sum(credits) if credits and sum(credits) else None, debit_ids, "No fully covered months with credits."),
        "balance_trend": (bal.get("trend"), bal.get("included", []), "Needs a positive average balance in the first month and at least 2 months."),
        "reconciliation": (recon["summary"]["within_share"], recon["summary"]["outside_txn_ids"], "No statements."),
    }
    cfg = engine["config"]["score"]
    rows, earned, assessed, total = [], 0.0, 0.0, 0.0
    for key, defaults in SCORE_DEFAULTS.items():
        p = cfg["parameters"][key]
        if not p.get("enabled", True) or not p["weight"]:
            continue
        total += p["weight"]
        value, ids, why_missing = values[key]
        row = {"key": key, "label": defaults["label"], "unit": defaults["unit"], "source": defaults["source"], "weight": p["weight"], "full_at": p["full_at"], "zero_at": p["zero_at"],
               "value": None if value is None else round(value, 4), "txn_ids": ids}
        if value is None:
            row.update(points=None, assessed=False, reason=why_missing)
        else:
            pts = _points(value, p["weight"], p["full_at"], p["zero_at"])
            row.update(points=pts, assessed=True)
            earned += pts
            assessed += p["weight"]
        rows.append(row)
    share = round(100 * assessed / total, 1) if total else 0
    out = {"engine": engine["name"], "engine_version": engine["version"], "notice": NOTICE, "parameters": rows, "assessed_weight": share, "min_assessed_weight": cfg["min_assessed_weight"],
           "score": None, "band": None, "reason": None, "scale": "0 to 100", "bands": cfg["bands"], "months_used": [r["month"] for r in full]}
    if not income_known:
        out["reason"] = "No confirmed income yet." + (" Confirm the credits in the review queue to get a score." if inc.get("stats", {}).get("provisional") else "")
    elif share < cfg["min_assessed_weight"]:
        out["reason"] = f"Only {share:.0f}% of this engine's weight could be assessed; it asks for at least {cfg['min_assessed_weight']:.0f}%."
    else:
        out["score"] = round(100 * earned / assessed) if assessed else None
        out["band"] = next((b["label"] for b in sorted(cfg["bands"], key=lambda b: -b["min"]) if out["score"] >= b["min"]), None)
    return out
