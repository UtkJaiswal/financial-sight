"""Silver -> gold orchestration and content-addressed snapshots.

A snapshot's id is derived from a hash of everything it was computed from. Recomputing with identical
inputs returns the existing snapshot, so a new snapshot exists only when something actually changed.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime

from . import config, fx, rule_engine
from .classify import engine
from .db import jdump, q, q1, tx
from .flags import rules as flag_rules
from .metrics import effective
from .metrics import score
from .metrics.engine import compute
from .validate.reconcile import case_view


def _context(case_id: str) -> dict:
    return {
        "accounts": q("SELECT account_id, acct_masked, bank_name, holder_masked, account_type, currency, currency_source FROM silver.accounts WHERE case_id = ?", [case_id]),
        "statements": q("""SELECT s.*, a.acct_masked, a.bank_name, a.currency FROM silver.statements s JOIN silver.accounts a USING (account_id)
                           WHERE s.case_id = ? ORDER BY a.bank_name, s.period_start""", [case_id]),
        "coverage": q("SELECT * FROM silver.coverage WHERE case_id = ? ORDER BY account_id, month", [case_id]),
        "cross_checks": q("SELECT * FROM silver.cross_checks WHERE case_id = ?", [case_id]),
        "patterns": [dict(p, counterparties=json.loads(p["counterparties"]), months=json.loads(p["months"]),
                          missed_months=json.loads(p["missed_months"]), txn_ids=json.loads(p["txn_ids"]))
                     for p in q("SELECT * FROM knowledge.periodic_patterns WHERE case_id = ?", [case_id])],
        "links": q("SELECT txn_a, txn_b, kind FROM silver.txn_links WHERE case_id = ?", [case_id]),
        "files": q("SELECT file_id, filename, status, reason, kind FROM bronze.files WHERE case_id = ?", [case_id]),
        "bad_pages": q("""SELECT p.*, f.filename FROM bronze.pages p JOIN bronze.files f USING (file_id)
                          WHERE f.case_id = ? AND p.status IN ('needs_ocr', 'failed', 'partial')""", [case_id]),
        "duplicate_uploads": q("SELECT filename FROM bronze.upload_events WHERE case_id = ? AND duplicate", [case_id]),
        "edit_checks": [dict(d, signals=json.loads(d["signals"])) for d in q(
            """SELECT d.*, f.filename FROM silver.document_checks d JOIN bronze.files f USING (file_id)
               WHERE d.case_id = ? ORDER BY d.score DESC, f.filename""", [case_id])],
    }


# The screen asks for analysis and transactions at the same moment. One computation at a time; the second then finds the first's result.
_computing = threading.RLock()


def _sources(case_id: str) -> dict:
    return {r["txn_id"]: r for r in q("""SELECT s.txn_id, s.statement_id, f.filename, s.page_no, s.row_no FROM silver.txn_sources s JOIN bronze.files f USING (file_id) WHERE f.case_id = ?""", [case_id])}


def run(case_id: str, trigger: str) -> dict:
    with _computing:
        return _run(case_id, trigger)


def _run(case_id: str, trigger: str) -> dict:
    case = q1("SELECT * FROM silver.cases WHERE case_id = ?", [case_id])
    run_id = engine.run(case_id, trigger)
    everything = effective.transactions(case_id)
    # a row with no exchange rate has no rupee value: it is reported, not counted
    txns = [t for t in everything if not t["fx_missing"]]
    no_rate = [t for t in everything if t["fx_missing"]]
    ctx = _context(case_id)
    table = fx.rates(case_id)
    eng = rule_engine.for_case(case_id)
    ctx["engine"] = eng
    fx_view = {"reporting": fx.REPORTING, "rates": [{"currency": k[0], "month": k[1], "rate": v["rate"], "source": v["source"], "actor": v["actor"]} for k, v in sorted(table.items())],
               "missing": sorted({(t["currency"], f"{t['txn_date']:%Y-%m}") for t in no_rate}), "currencies": sorted({a["currency"] for a in ctx["accounts"]}),
               "no_rate_txn_ids": [t["txn_id"] for t in no_rate], "no_rate_rows": len(no_rate),
               "assumed_accounts": [a["account_id"] for a in ctx["accounts"] if a["currency_source"] == "assumed"]}
    ctx["fx"] = fx_view
    versions = json.loads(q1("SELECT versions FROM silver.classification_runs WHERE run_id = ?", [run_id])["versions"]) if run_id else {}
    basis = jdump({"case": case_id, "txns": [[t["txn_id"], t["category"], t["status"]] for t in everything], "fx": fx_view["rates"], "engine": rule_engine.tag(eng), "coverage": ctx["coverage"],
                   "statements": [[s["statement_id"], s["recon_status"]] for s in ctx["statements"]],
                   "files": ctx["files"], "edit": [[d["file_id"], d["score"]] for d in ctx["edit_checks"]],
                   "versions": versions, "application_date": case["application_date"]})
    input_hash = hashlib.sha256(basis.encode()).hexdigest()
    existing = q1("SELECT * FROM gold.snapshots WHERE case_id = ? AND input_hash = ?", [case_id, input_hash])
    if existing:
        return json.loads(existing["payload"])
    gold = compute(case, txns, ctx["coverage"], fx.statements_in_inr(ctx["statements"], table), ctx["patterns"], ctx["links"], eng, ctx["accounts"], ctx["cross_checks"]) if txns else {
        "months": [], "coverage": {}, "monthly": [], "metrics": {}, "regular_outflows": [], "balance_series": [], "money_flows": {"edges": [], "cycles": [], "parties": [], "pass_through": []}, "account_view": [], "earnings": None, "statement_requests": {"prompt": None, "items": []}, "engine": {"id": eng["engine_id"], "name": eng["name"], "version": eng["version"]},
        "account_balance_series": {}, "profile": case["profile"]}
    sources = _sources(case_id)
    ctx["reconciliation"] = case_view(ctx["statements"], ctx["cross_checks"], txns, ctx["links"], sources, eng["params"])
    gold["reconciliation"] = ctx["reconciliation"]
    if txns:
        gold["metrics"]["estimated_income"]["excluded"] += [
            {"txn_id": t["txn_id"], "reason": f"No exchange rate supplied for {t['currency']} in {t['txn_date']:%Y-%m}: not counted"} for t in no_rate if t["direction"] == "C"]
    flags = flag_rules.evaluate(case, txns, gold, ctx) if txns else flag_rules.evaluate(case, [], {"metrics": {"estimated_income": {"monthly": []}}, "monthly": []}, ctx)
    gold["credit_score"] = score.compute(gold, txns, eng, ctx["reconciliation"]) if txns else None
    last = q1("SELECT seq FROM gold.snapshots WHERE case_id = ? ORDER BY seq DESC LIMIT 1", [case_id])
    seq = (last["seq"] + 1) if last else 1
    snapshot_id = f"snap_{input_hash[:12]}"
    status_counts: dict[str, int] = {}
    for t in everything:
        status_counts[t["status"]] = status_counts.get(t["status"], 0) + 1
    payload = {
        "snapshot_id": snapshot_id, "seq": seq, "case_id": case_id, "trigger": trigger, "run_id": run_id, "versions": versions,
        "created_at": datetime.utcnow().isoformat(timespec="seconds"), **gold, "flags": flags,
        "accounts": ctx["accounts"], "statements": ctx["statements"], "cross_checks": ctx["cross_checks"], "patterns": ctx["patterns"],
        "edit_checks": ctx["edit_checks"],
        "status_counts": status_counts, "n_transactions": len(everything), "fx": fx_view,
        "categories": {t["txn_id"]: [t["category"], t["status"]] for t in everything},
    }
    with tx() as c:
        c.execute("INSERT INTO gold.snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
                  [snapshot_id, case_id, input_hash, trigger, datetime.utcnow(), seq, jdump(payload)])
    return json.loads(jdump(payload))


def compare_engines(case_id: str) -> list[dict]:
    """The case's current classifications measured under each engine. Nothing is saved. An engine's own rules for labelling rows
    apply only once it is assigned, so this compares figures and scores, not labels."""
    case = q1("SELECT * FROM silver.cases WHERE case_id = ?", [case_id])
    current_engine = rule_engine.for_case(case_id)["engine_id"]
    txns = [t for t in effective.transactions(case_id) if not t["fx_missing"]]
    ctx = _context(case_id)
    table = fx.rates(case_id)
    sources = _sources(case_id)
    out = []
    for row in rule_engine.list_engines():
        e = rule_engine.with_params(row)
        res = {"engine_id": e["engine_id"], "name": e["name"], "version": e["version"], "current": e["engine_id"] == current_engine, "score": None, "band": None, "income": None, "foir": None, "reason": None}
        if txns:
            gold = compute(case, txns, ctx["coverage"], fx.statements_in_inr(ctx["statements"], table), ctx["patterns"], ctx["links"], e, ctx["accounts"], ctx["cross_checks"])
            s = score.compute(gold, txns, e, case_view(ctx["statements"], ctx["cross_checks"], txns, ctx["links"], sources, e["params"]))
            res.update(score=s["score"], band=s["band"], reason=s["reason"], income=gold["metrics"]["estimated_income"]["value"], foir=gold["metrics"]["foir"]["value"])
        out.append(res)
    return out


def current(case_id: str) -> dict:
    """The latest snapshot, recomputed first if it was made by older rules, so new rules reach existing cases without a reset."""
    def fresh(snap: dict | None) -> bool:
        v = (snap or {}).get("versions") or {}
        return bool(snap) and (not v or (v.get("rules") == config.RULES_VERSION and v.get("pipeline") == config.PIPELINE_VERSION and v.get("weights") == config.WEIGHTS_VERSION
                                         and v.get("engine") == rule_engine.tag(rule_engine.for_case(case_id))))

    snap = latest(case_id)
    if fresh(snap):
        return snap
    with _computing:
        snap = latest(case_id)  # another request may have just finished it
        return snap if fresh(snap) else _run(case_id, "rules_updated" if snap else "initial")


def latest(case_id: str) -> dict | None:
    s = q1("SELECT payload FROM gold.snapshots WHERE case_id = ? ORDER BY seq DESC LIMIT 1", [case_id])
    return json.loads(s["payload"]) if s else None


def diff(a: dict, b: dict) -> dict:
    """What changed between two snapshots: metric values and transaction categories."""
    metrics = []
    for k, mb in b.get("metrics", {}).items():
        ma = a.get("metrics", {}).get(k, {})
        if ma.get("value") != mb.get("value"):
            metrics.append({"key": k, "label": mb["label"], "unit": mb["unit"], "before": ma.get("value"), "after": mb.get("value"),
                            "method_before": ma.get("method"), "method_after": mb.get("method")})
    changed = []
    for tid, (cat, st) in b.get("categories", {}).items():
        before = a.get("categories", {}).get(tid)
        if before and (before[0] != cat or before[1] != st):
            changed.append({"txn_id": tid, "before": before, "after": [cat, st]})
    fa = {f["code"] + f["title"] for f in a.get("flags", [])}
    fb = {f["code"] + f["title"] for f in b.get("flags", [])}
    return {"from": a.get("snapshot_id"), "to": b.get("snapshot_id"), "metrics": metrics, "transactions": changed,
            "flags_added": [f for f in b.get("flags", []) if f["code"] + f["title"] not in fa],
            "flags_removed": [f for f in a.get("flags", []) if f["code"] + f["title"] not in fb]}
