"""The category in effect: latest analyst override, otherwise the latest classification run."""
from __future__ import annotations

import json

from .. import fx
from ..db import q


def latest_run(case_id: str) -> str | None:
    r = q("SELECT run_id FROM silver.classification_runs WHERE case_id = ? ORDER BY seq DESC LIMIT 1", [case_id])
    return r[0]["run_id"] if r else None


def transactions(case_id: str) -> list[dict]:
    run_id = latest_run(case_id)
    rows = q("""
        WITH ov AS (
          SELECT txn_id, to_category, reason, actor, created_at,
                 row_number() OVER (PARTITION BY txn_id ORDER BY created_at DESC, event_id DESC) AS rn
          FROM silver.overrides WHERE case_id = ?)
        SELECT t.txn_id, t.account_id, t.txn_date, t.description, t.description_norm, t.debit, t.credit, t.balance, t.channel,
               t.counterparty, t.recon_status, t.recon_note, t.seq, a.acct_masked, a.bank_name, a.currency,
               c.category AS model_category, c.status AS model_status, c.tier, c.source, c.probability, c.margin, c.explanation,
               c.attributes, ov.to_category AS override_category, ov.reason AS override_reason, ov.actor AS override_actor,
               ov.created_at AS override_at
        FROM silver.transactions t
        JOIN silver.accounts a USING (account_id)
        LEFT JOIN silver.classifications c ON c.txn_id = t.txn_id AND c.run_id = ?
        LEFT JOIN ov ON ov.txn_id = t.txn_id AND ov.rn = 1
        WHERE t.case_id = ?
        ORDER BY t.txn_date, t.account_id, t.seq""", [case_id, run_id, case_id])
    table = fx.rates(case_id)
    for r in rows:
        fx.annotate(r, table)
        r["direction"] = "C" if r["credit"] else "D"
        r["orig_debit"], r["orig_credit"], r["orig_balance"] = r["debit"], r["credit"], r["balance"]
        rate = None if r["fx_missing"] else (r["fx_rate"] or "1")
        # an amount with no rate cannot be a rupee figure: zero here, and analysis keeps these rows out and reports them
        r["debit"], r["credit"], r["balance"] = fx.convert(r["debit"], rate) or 0, fx.convert(r["credit"], rate) or 0, fx.convert(r["balance"], rate)
        if r["override_category"]:
            r["category"], r["status"] = r["override_category"], "confirmed"
        else:
            r["category"], r["status"] = r["model_category"] or "UNCLASSIFIED", r["model_status"] or "unclassified"
        r["amount"] = r["credit"] or r["debit"]
        r["attributes"] = json.loads(r["attributes"] or "[]")
    return rows
