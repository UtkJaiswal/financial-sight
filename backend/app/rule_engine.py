"""Rule engines: each lender sets up its own reconciliation policy, classification rules, income and obligation rules, and score.

What can be set is limited to the parameters in Level 1 of the problem statement. Other thresholds keep fixed defaults. An engine is versioned: every save adds a version with
who changed it, and every analysis records the engine version it used, so a figure can always be traced to the rules behind it.
No engine approves or declines credit: it decides what is counted, what is flagged and how the score is built.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime

from . import config
from .classify.categories import CATEGORIES
from .db import connect, q, q1

STANDARD = "standard"

# key, group, label, kind, default, where the parameter comes from in the problem statement, help
_P = [
    # ---- reconciliation
    ("recon_tolerance_paise", "Reconciliation", "Allowed difference at closing", "paise", 0, "Level 1: reconcile opening, transactions and closing", "A statement within this difference counts as reconciled."),
    ("recon_require_closing", "Reconciliation", "Closing balance must be stated", "bool", False, "Level 1: reconcile opening, transactions and closing", "Treat a statement with no stated closing balance as outside the rules."),
    ("recon_max_row_breaks", "Reconciliation", "Running-balance breaks allowed", "count", 0, "Level 1: highlight any discrepancy", "Rows whose balance does not follow from the row before."),
    # ---- income
    ("income_method", "Income", "Monthly figure", "choice:median,average", "median", "Scenario 2: sustainable income; Level 2: average or median", "How months are combined into one figure."),
    ("income_max_months", "Income", "Months of history used", "count", 12, "Scenario 2: several months of history", "The most recent fully covered months."),
    ("income_min_months", "Income", "Months needed for a figure", "count", 1, "Scenario 2: not a single month", "With fewer covered months, no income figure is given."),
    ("income_fall_pct", "Income", "Fall that switches to the recent average", "share", 0.15, "Scenario 4: declining income", "Compares the last 3 months with the 3 before."),
    ("deduct_business_costs", "Income", "Take business costs off a business's receipts", "bool", True, "Scenario 2: sustainable income", "Suppliers, staff and tax."),
    ("count_cash_deposits", "Income", "Count cash deposits as income", "bool", False, "Scenario 6: unexplained cash deposits", "Off means cash waits until its source is verified."),
    # ---- obligations and affordability
    ("obligation_min_months", "Obligations and affordability", "Months before a payment counts as recurring", "count", 2, "Scenario 4: recurring obligations", "EMI and rent."),
    ("stable_obligation_ratio", "Obligations and affordability", "How steady an unconfirmed EMI must be", "ratio", config.STABLE_OBLIGATION_RATIO, "Scenario 4: recurring obligations", "Highest month divided by lowest month."),
    ("max_foir", "Obligations and affordability", "Most obligations can be of income", "share", 0.50, "Level 2: ratio of obligations to sustainable income", ""),
    ("min_surplus_after_obligations_paise", "Obligations and affordability", "Least surplus after obligations", "paise", 10_000_00, "Level 2: average monthly surplus after obligations", ""),
    # ---- transfers and rotation
    ("transfer_max_days", "Transfers and rotation", "Days apart for a certain own-account transfer", "days", config.TRANSFER_MAX_DAYS, "Scenario 3: internal transfers", "Same amount, opposite direction, two own accounts."),
    ("reversal_max_days", "Transfers and rotation", "Days apart for a refund or reversal", "days", config.REVERSAL_MAX_DAYS, "Scenario 3: refunds and reversals", ""),
    ("rotation_enabled", "Transfers and rotation", "Check for money rotation", "bool", True, "Scenario 3 and feedback", "Late, split or fee-reduced transfers, and money out and back."),
    ("rotation_min_paise", "Transfers and rotation", "Smallest amount checked", "paise", config.ROTATION_MIN_PAISE, "Scenario 3", ""),
    ("rotation_part_min_paise", "Transfers and rotation", "Smallest part of a split", "paise", config.ROTATION_PART_MIN_PAISE, "Scenario 3", ""),
    ("rotation_fee_max_paise", "Transfers and rotation", "Largest fee allowed between two legs", "paise", config.ROTATION_FEE_MAX_PAISE, "Scenario 3", ""),
    ("rotation_window_days", "Transfers and rotation", "Days between two legs", "days", config.ROTATION_WINDOW_DAYS, "Scenario 3", ""),
    ("rotation_party_days", "Transfers and rotation", "Days for money out and back with one party", "days", config.ROTATION_PARTY_DAYS, "Scenario 3", ""),
    ("rotation_pass_min_paise", "Transfers and rotation", "Smallest amount passing straight through", "paise", config.ROTATION_PASS_MIN_PAISE, "Scenario 3", ""),
    ("rotation_pass_days", "Transfers and rotation", "Days for money passing straight through", "days", config.ROTATION_PASS_DAYS, "Scenario 3", ""),
    ("rotation_pass_min_count", "Transfers and rotation", "Times before that counts as a habit", "count", config.ROTATION_PASS_MIN_COUNT, "Scenario 3", ""),
    # ---- risk indicators
    ("low_balance_paise", "Risk indicators", "Low balance below", "paise", config.LOW_BALANCE_PAISE, "Scenario 4: frequent low balances; Level 2: insufficient balances", ""),
    ("low_balance_days_share", "Risk indicators", "Share of days that counts as frequent", "share", 0.08, "Scenario 4: frequent low balances", ""),
    ("negative_cash_flow_months", "Risk indicators", "Negative months that count as sustained", "count", config.NEGATIVE_CASH_FLOW_MONTHS, "Scenario 4; Level 2: sustained negative cash flow", ""),
    ("income_decline_share", "Risk indicators", "Fall that counts as declining income", "share", 0.20, "Scenario 4: declining income", ""),
    ("rising_expense_share", "Risk indicators", "Rise that counts as rising expenses", "share", 0.25, "Scenario 4: cash-flow deterioration", ""),
    ("cash_flow_trend_share", "Risk indicators", "Change in net cash flow that counts", "share", config.CASH_FLOW_TREND_SHARE, "Level 2: improving, stable or deteriorating", "As a share of average monthly inflows."),
    ("income_volatile_cv", "Risk indicators", "Income variation that counts as high", "ratio", config.INCOME_VOLATILE_CV, "Level 2: high income volatility", "Coefficient of variation."),
    ("income_spike_ratio", "Risk indicators", "How far from the median a month is unusual", "ratio", config.INCOME_SPIKE_RATIO, "Level 2: unusual spikes or drops", ""),
    ("large_cash_paise", "Risk indicators", "Large cash deposit from", "paise", config.LARGE_CASH_PAISE, "Scenario 6: large unexplained cash deposits", ""),
    ("unexplained_deposit_paise", "Risk indicators", "Large unexplained deposit from", "paise", config.UNEXPLAINED_DEPOSIT_PAISE, "Level 2: large unexplained deposits", ""),
    ("round_figure_min_paise", "Risk indicators", "Round-figure transfer from", "paise", config.ROUND_FIGURE_MIN_PAISE, "Scenario 6: round-figure transfers before an application", ""),
    ("pre_application_days", "Risk indicators", "Days before the application to check", "days", config.PRE_APPLICATION_DAYS, "Scenario 6: round-figure transfers before an application", ""),
    ("customer_concentration_share", "Risk indicators", "One payer's share that counts as concentrated", "share", 0.60, "Scenario 2: many counterparties", "For self-employed income."),
    # ---- classification
    ("auto_min_p", "Classification", "Confidence for an automatic label", "share", config.AUTO_MIN_P, "Level 1: classification", "Below this, an analyst decides."),
    ("auto_min_margin", "Classification", "Lead needed over the next category", "share", config.AUTO_MIN_MARGIN, "Level 1: classification", ""),
    ("review_min_p", "Classification", "Confidence for a suggestion", "share", config.REVIEW_MIN_P, "Level 1: classification", "Below this, the row stays unclassified."),
    ("knn_min_sim", "Classification", "Similarity needed to count a past example", "share", config.KNN_MIN_SIM, "Level 1: classification", ""),
    ("unexplained_credit_paise", "Classification", "Credit with no evidence left unclassified from", "paise", config.UNEXPLAINED_CREDIT_PAISE, "Scenario 6", ""),
    ("unexplained_debit_paise", "Classification", "Debit with no evidence sent to review from", "paise", config.UNEXPLAINED_DEBIT_PAISE, "Level 1: classification", ""),
    ("salary_max_payers_in_month", "Classification", "Salary payers allowed in a month", "count", config.SALARY_MAX_PAYERS_IN_MONTH, "Scenario 1: salary identification", ""),
]
DEFAULT_PARAMS = {k: d for k, _, _, _, d, _, _ in _P}
# Level 1 of the problem statement: reconciliation, classification, estimated income, recurring obligations.
LEVEL_1 = {
    "recon_tolerance_paise": ("Reconciliation", "Check that opening balance, transactions and closing balance reconcile"),
    "recon_require_closing": ("Reconciliation", "Check that opening balance, transactions and closing balance reconcile"),
    "recon_max_row_breaks": ("Reconciliation", "Highlight any discrepancy"),
    "auto_min_p": ("Classification", "Classify transactions, showing the reason"),
    "review_min_p": ("Classification", "Classify transactions, showing the reason"),
    "transfer_max_days": ("Classification", "Internal transfers between the customer's own accounts"),
    "reversal_max_days": ("Classification", "Pair reversed or refunded transactions"),
    "income_method": ("Estimated income", "Estimated income"),
    "income_max_months": ("Estimated income", "Estimated income"),
    "income_min_months": ("Estimated income", "Estimated income"),
    "obligation_min_months": ("Recurring obligations", "Identify recurring loan repayments and EMIs"),
}
SCHEMA = [{"key": k, "group": LEVEL_1[k][0], "label": lb, "kind": kd, "default": d, "source": LEVEL_1[k][1], "help": h} for k, _, lb, kd, d, _, h in _P if k in LEVEL_1]
EDITABLE_DEFAULTS = {p["key"]: p["default"] for p in SCHEMA}
# The Level 1 categories a lender's keyword rules can point at. Common expenses are rent, utilities, card payments and other regular outflows.
RULE_CATEGORIES = ["SALARY", "BUSINESS_RECEIPT", "INTERNAL_TRANSFER", "REFUND_REVERSAL", "LOAN_EMI", "RENT", "UTILITIES", "CARD_PAYMENT", "GENERAL_EXPENSE"]

# Each score parameter is a Level 1 calculation. Points run in a straight line between zero_at and full_at.
SCORE_DEFAULTS = {
    "estimated_income": {"label": "Estimated monthly income", "weight": 25, "full_at": 1_00_000_00, "zero_at": 0, "unit": "paise", "source": "Level 1: estimated income"},
    "recurring_obligations": {"label": "Recurring obligations, as a share of income", "weight": 20, "full_at": 0.30, "zero_at": 0.70, "unit": "share", "source": "Level 1: recurring obligations"},
    "net_cash_flow": {"label": "Net cash flow, as a share of monthly credits", "weight": 20, "full_at": 0.20, "zero_at": -0.10, "unit": "share", "source": "Level 1: net cash flow"},
    "monthly_credits": {"label": "How much monthly credits vary", "weight": 10, "full_at": 0.15, "zero_at": 0.75, "unit": "ratio", "source": "Level 1: monthly credits"},
    "monthly_debits": {"label": "Monthly debits, as a share of monthly credits", "weight": 10, "full_at": 0.70, "zero_at": 1.10, "unit": "share", "source": "Level 1: monthly debits"},
    "balance_trend": {"label": "Change in average balance, first month to last", "weight": 10, "full_at": 0.0, "zero_at": -0.50, "unit": "share", "source": "Level 1: balance trends"},
    "reconciliation": {"label": "Share of statements within reconciliation rules", "weight": 5, "full_at": 1.0, "zero_at": 0.5, "unit": "share", "source": "Level 1: balance reconciliation"},
}
DEFAULT_CONFIG = {"params": EDITABLE_DEFAULTS, "custom_rules": [],
                  "score": {"parameters": {k: {"enabled": True, "weight": v["weight"], "full_at": v["full_at"], "zero_at": v["zero_at"]} for k, v in SCORE_DEFAULTS.items()},
                            "bands": [{"min": 75, "label": "Strong"}, {"min": 55, "label": "Moderate"}, {"min": 35, "label": "Weak"}, {"min": 0, "label": "High concern"}],
                            "min_assessed_weight": 60}}
_KINDS = {p["key"]: p["kind"] for p in SCHEMA}


def _merged(cfg: dict | None) -> dict:
    """An older engine picks up new parameters at their defaults."""
    cfg = cfg or {}
    out = copy.deepcopy(DEFAULT_CONFIG)
    out["params"].update({k: v for k, v in (cfg.get("params") or {}).items() if k in EDITABLE_DEFAULTS})
    out["custom_rules"] = list(cfg.get("custom_rules") or [])
    s = cfg.get("score") or {}
    for k, v in (s.get("parameters") or {}).items():
        if k in out["score"]["parameters"]:
            out["score"]["parameters"][k].update(v)
    if s.get("bands"):
        out["score"]["bands"] = sorted(s["bands"], key=lambda b: -b["min"])
    if s.get("min_assessed_weight") is not None:
        out["score"]["min_assessed_weight"] = s["min_assessed_weight"]
    return out


def validate(cfg: dict) -> dict:
    """Refuses anything that would make figures meaningless, saying which field and why."""
    if not isinstance(cfg, dict):
        raise ValueError("an engine's settings must be an object")
    for k in (cfg.get("params") or {}):
        if k not in EDITABLE_DEFAULTS:
            raise ValueError(f"{k!r} is not a Level 1 parameter, so an engine cannot set it")
    out = _merged(cfg)
    label = {p["key"]: p["label"] for p in SCHEMA}
    for k, v in out["params"].items():
        kind = _KINDS[k]
        try:
            if kind == "bool":
                if not isinstance(v, bool):
                    raise ValueError
            elif kind.startswith("choice:"):
                if v not in kind[7:].split(","):
                    raise ValueError
            elif kind in ("paise", "count", "days"):
                out["params"][k] = int(v)
                if out["params"][k] < 0:
                    raise ValueError
            else:
                out["params"][k] = float(v)
                if out["params"][k] < 0 or (kind == "share" and out["params"][k] > 1):
                    raise ValueError
        except (TypeError, ValueError):
            raise ValueError(f"{label[k]}: {v!r} is not a valid value") from None
    p = out["params"]
    if p["review_min_p"] > p["auto_min_p"]:
        raise ValueError("Confidence for a suggestion cannot be above the confidence for an automatic label")
    if p["income_min_months"] > p["income_max_months"]:
        raise ValueError("Months needed for a figure cannot be above the months of history used")
    rules = []
    for r in out["custom_rules"]:
        word = str(r.get("keyword") or "").strip().upper()
        cat, direction = r.get("category"), r.get("direction", "B")
        if len(word) < 3:
            raise ValueError("A custom rule's keyword needs at least 3 characters")
        if cat not in RULE_CATEGORIES:
            raise ValueError(f"A keyword rule must point at a Level 1 category, not {cat!r}")
        if direction not in ("C", "D", "B") or (direction != "B" and CATEGORIES[cat]["dir"] not in (direction, "B")):
            raise ValueError(f"{CATEGORIES[cat]['label']} does not apply to that direction")
        try:
            weight = float(r.get("weight", 3))
        except (TypeError, ValueError):
            raise ValueError("A custom rule's points must be a number") from None
        if not 0.5 <= weight <= 6:
            raise ValueError("A custom rule's points must be between 0.5 and 6")
        rules.append({"keyword": word, "direction": direction, "category": cat, "weight": weight})
    out["custom_rules"] = rules
    sp = out["score"]["parameters"]
    for k, v in sp.items():
        for f in ("weight", "full_at", "zero_at"):
            try:
                v[f] = float(v[f])
            except (TypeError, ValueError):
                raise ValueError(f"{SCORE_DEFAULTS[k]['label']}: {f.replace('_', ' ')} must be a number") from None
        v["enabled"] = bool(v.get("enabled", True))
        if v["weight"] < 0:
            raise ValueError(f"{SCORE_DEFAULTS[k]['label']}: weight cannot be negative")
        if v["full_at"] == v["zero_at"]:
            raise ValueError(f"{SCORE_DEFAULTS[k]['label']}: full points and zero points need different values")
    if sum(v["weight"] for v in sp.values() if v["enabled"]) <= 0:
        raise ValueError("At least one score parameter needs a weight")
    bands = out["score"]["bands"]
    if not bands or any(not str(b.get("label") or "").strip() or not 0 <= float(b["min"]) <= 100 for b in bands) or min(float(b["min"]) for b in bands) != 0:
        raise ValueError("Score bands need labels and must run down to 0")
    if not 0 <= float(out["score"]["min_assessed_weight"]) <= 100:
        raise ValueError("Minimum assessed weight must be between 0 and 100")
    return out


SAMPLES = [
    (STANDARD, "Standard", "Level 1 defaults from the problem statement. Copy it to set up your own.", {}),
    ("sample_conservative", "Conservative bank (sample)", "An example of a stricter lender: reconciliation must be exact, more history, and tighter score limits.",
     {"params": {"recon_require_closing": True, "income_min_months": 6, "auto_min_p": 0.95, "obligation_min_months": 2},
      "score": {"parameters": {"recurring_obligations": {"full_at": 0.20, "zero_at": 0.50}, "net_cash_flow": {"full_at": 0.30, "zero_at": 0.0}, "reconciliation": {"weight": 10}},
                "bands": [{"min": 80, "label": "Strong"}, {"min": 65, "label": "Moderate"}, {"min": 45, "label": "Weak"}, {"min": 0, "label": "High concern"}]}}),
    ("sample_sme", "SME growth lender (sample)", "An example of a business lender: averages rather than medians, a small reconciliation allowance, and more room on ratios.",
     {"params": {"income_method": "average", "recon_tolerance_paise": 100_00, "recon_max_row_breaks": 1},
      "score": {"parameters": {"estimated_income": {"full_at": 3_00_000_00}, "recurring_obligations": {"full_at": 0.40, "zero_at": 0.85}, "monthly_credits": {"weight": 5, "zero_at": 1.2}, "net_cash_flow": {"weight": 25}}}}),
]


def seed() -> None:
    """Idempotent. Never touches an engine that already exists, so a lender's edits survive restarts."""
    con = connect()
    for engine_id, name, desc, cfg in SAMPLES:
        if not q1("SELECT 1 FROM silver.rule_engines WHERE engine_id = ?", [engine_id]):
            full = validate(cfg)
            con.execute("INSERT INTO silver.rule_engines VALUES (?, ?, ?, 1, ?, ?, 'system', ?)",
                        [engine_id, name, desc, json.dumps(full, sort_keys=True), engine_id != STANDARD, datetime(2026, 1, 1)])
            con.execute("INSERT INTO silver.rule_engine_history VALUES (?, 1, ?, 'system', 'Created', ?)", [engine_id, json.dumps(full, sort_keys=True), datetime(2026, 1, 1)])


def _row(r: dict) -> dict:
    return {"engine_id": r["engine_id"], "name": r["name"], "description": r["description"], "version": r["version"], "config": _merged(json.loads(r["config"])),
            "is_sample": r["is_sample"], "editable": r["engine_id"] != STANDARD, "updated_by": r["updated_by"], "updated_at": r["updated_at"]}


def default() -> dict:
    return {"engine_id": STANDARD, "name": "Standard", "version": 1, "config": copy.deepcopy(DEFAULT_CONFIG), "params": dict(DEFAULT_PARAMS)}


def list_engines() -> list[dict]:
    rows = [_row(r) for r in q("SELECT * FROM silver.rule_engines ORDER BY engine_id != 'standard', is_sample DESC, name")]
    use = {r["engine_id"]: r["n"] for r in q("SELECT engine_id, count(*) AS n FROM silver.case_engine GROUP BY engine_id")}
    for r in rows:
        r["cases"] = use.get(r["engine_id"], 0)
    return rows


def get(engine_id: str) -> dict | None:
    r = q1("SELECT * FROM silver.rule_engines WHERE engine_id = ?", [engine_id])
    return _row(r) if r else None


def history(engine_id: str) -> list[dict]:
    return q("SELECT version, actor, note, created_at FROM silver.rule_engine_history WHERE engine_id = ? ORDER BY version DESC", [engine_id])


def create(name: str, based_on: str | None, description: str, actor: str) -> dict:
    name = (name or "").strip()
    if len(name) < 3:
        raise ValueError("Give the engine a name of at least 3 characters")
    if q1("SELECT 1 FROM silver.rule_engines WHERE lower(name) = lower(?)", [name]):
        raise ValueError(f"An engine called {name!r} already exists")
    base = get(based_on or STANDARD)
    if not base:
        raise ValueError("The engine to copy was not found")
    engine_id = "e_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:30]
    if q1("SELECT 1 FROM silver.rule_engines WHERE engine_id = ?", [engine_id]):
        engine_id += f"_{int(datetime.utcnow().timestamp())}"
    now = datetime.utcnow()
    text = json.dumps(base["config"], sort_keys=True)
    con = connect()
    con.execute("INSERT INTO silver.rule_engines VALUES (?, ?, ?, 1, ?, FALSE, ?, ?)", [engine_id, name, (description or "").strip()[:200], text, actor, now])
    con.execute("INSERT INTO silver.rule_engine_history VALUES (?, 1, ?, ?, ?, ?)", [engine_id, text, actor, f"Copied from {base['name']} v{base['version']}", now])
    return get(engine_id)


def update(engine_id: str, cfg: dict, actor: str, note: str = "", name: str | None = None, description: str | None = None) -> dict:
    if engine_id == STANDARD:
        raise ValueError("The Standard engine is the reference and cannot be changed. Copy it instead.")
    cur = get(engine_id)
    if not cur:
        raise LookupError("engine not found")
    full = validate(cfg)
    text = json.dumps(full, sort_keys=True)
    if name is not None and len(name.strip()) < 3:
        raise ValueError("Give the engine a name of at least 3 characters")
    if text == json.dumps(cur["config"], sort_keys=True) and name in (None, cur["name"]) and description in (None, cur["description"]):
        return cur
    version, now = cur["version"] + 1, datetime.utcnow()
    con = connect()
    con.execute("UPDATE silver.rule_engines SET version = ?, config = ?, updated_by = ?, updated_at = ?, name = ?, description = ? WHERE engine_id = ?",
                [version, text, actor, now, (name or cur["name"]).strip(), cur["description"] if description is None else description.strip()[:200], engine_id])
    con.execute("INSERT INTO silver.rule_engine_history VALUES (?, ?, ?, ?, ?, ?)", [engine_id, version, text, actor, (note or "Settings changed").strip()[:200], now])
    return get(engine_id)


def delete(engine_id: str) -> None:
    if engine_id == STANDARD:
        raise ValueError("The Standard engine cannot be deleted")
    if q1("SELECT 1 FROM silver.case_engine WHERE engine_id = ?", [engine_id]):
        raise ValueError("Move the cases using this engine to another one first")
    con = connect()
    con.execute("DELETE FROM silver.rule_engines WHERE engine_id = ?", [engine_id])


def for_case(case_id: str) -> dict:
    """The engine in force for a case, flattened for the code that reads it."""
    r = q1("SELECT e.* FROM silver.case_engine c JOIN silver.rule_engines e USING (engine_id) WHERE c.case_id = ?", [case_id]) \
        or q1("SELECT * FROM silver.rule_engines WHERE engine_id = ?", [STANDARD])
    if not r:
        return default()
    e = _row(r)
    e["params"] = {**DEFAULT_PARAMS, **e["config"]["params"]}
    return e


def assign(case_id: str, engine_id: str, actor: str) -> dict:
    e = get(engine_id)
    if not e:
        raise LookupError("engine not found")
    con = connect()
    con.execute("DELETE FROM silver.case_engine WHERE case_id = ?", [case_id])
    con.execute("INSERT INTO silver.case_engine VALUES (?, ?, ?, ?)", [case_id, engine_id, actor, datetime.utcnow()])
    return e


def with_params(e: dict) -> dict:
    return {**e, "params": {**DEFAULT_PARAMS, **e["config"]["params"]}}


def tag(e: dict) -> str:
    return f"{e['engine_id']}:v{e['version']}"
