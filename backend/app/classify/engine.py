"""Classification: Tier 1 hard gates, Tier 2 weighted log-odds fusion, Tier 3 decision policy.

score(c) = sum of weighted signal contributions;  P = softmax over candidate categories plus the baseline.
Hard facts (transfer pairs, bounce pairs, reversal pairs) are never averaged. Analyst overrides are applied
on top as events and never change these stored results.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import datetime

from .. import config, fx, rule_engine
from ..db import connect, jdump, q, tx
from . import jev_client, periodic, rotation, rules, semantic
from ..ingest.normalize import counterparty_key
from .categories import BASELINE, CATEGORIES, ROLLUP, valid_for


def _versions(memory_version: str, eng: dict) -> dict:
    return {"pipeline": config.PIPELINE_VERSION, "rules": config.RULES_VERSION, "weights": config.WEIGHTS_VERSION, "engine": rule_engine.tag(eng), "engine_name": eng["name"],
            "question_bank": config.QUESTION_BANK_VERSION, "memory": memory_version,
            "jev": "enabled" if jev_client.available() else "disabled"}


def _amount(t: dict) -> int:
    return t["credit"] or t["debit"]


def _dir(t: dict) -> str:
    return "C" if t["credit"] else "D"


def _gates(txns: list[dict], p: dict) -> tuple[dict, list]:
    """Tier 1. Returns {txn_id: gate} and links."""
    gates: dict[str, dict] = {}
    links: list[tuple] = []
    by_id = {t["txn_id"]: t for t in txns}

    # bounce / return of a mandate or cheque, paired with the original debit
    for t in txns:
        n = t["description_norm"]
        if rules.BOUNCE.search(n) and rules.CHARGE.search(n) and _dir(t) == "D":
            gates[t["txn_id"]] = {"category": "BANK_CHARGES", "source": "gate:bounce_charge",
                                  "explanation": "Charge levied for a returned payment", "attrs": ["bounce_charge"]}
        elif rules.BOUNCE.search(n) and rules.BOUNCE_CONTEXT.search(n) and _dir(t) == "C":
            gates[t["txn_id"]] = {"category": "BOUNCE_RETURN", "source": "gate:bounce",
                                  "explanation": "Narration reports a returned / dishonoured payment", "attrs": ["bounce"]}
            for o in txns:
                if (o["account_id"] == t["account_id"] and _dir(o) == "D" and o["debit"] == t["credit"]
                        and 0 <= (t["txn_date"] - o["txn_date"]).days <= 3 and o["txn_id"] not in gates):
                    gates[o["txn_id"]] = {"category": "BOUNCE_RETURN", "source": "gate:bounce_pair",
                                          "explanation": "Debit returned unpaid by the paired return entry", "attrs": ["bounce"]}
                    links.append((o["txn_id"], t["txn_id"], "bounce_pair", "same amount returned within 3 days"))
                    break

    # internal transfers: opposite entries in two of the customer's own accounts
    debits = [t for t in txns if _dir(t) == "D" and t["txn_id"] not in gates]
    used: set[str] = set()
    for c in [t for t in txns if _dir(t) == "C" and t["txn_id"] not in gates]:
        best = None
        for d in debits:
            if d["txn_id"] in used or d["account_id"] == c["account_id"] or not fx.same_money(d, c):
                continue
            gap = abs((d["txn_date"] - c["txn_date"]).days)
            if gap <= p["transfer_max_days"] and (best is None or gap < best[1]):
                best = (d, gap)
        if best:
            d = best[0]
            used.add(d["txn_id"])
            across = f", across currencies at the supplied rate ({best[0]['currency']} to {c['currency']})" if best[0]["currency"] != c["currency"] else ""
            why = f"Matched to an equal debit in the customer's other account {best[1]} day(s) apart{across}"
            gates[c["txn_id"]] = {"category": "INTERNAL_TRANSFER", "source": "gate:transfer_pair", "explanation": why, "attrs": ["paired"]}
            gates[d["txn_id"]] = {"category": "INTERNAL_TRANSFER", "source": "gate:transfer_pair",
                                  "explanation": f"Matched to an equal credit in the customer's other account {best[1]} day(s) apart{across}", "attrs": ["paired"]}
            links.append((d["txn_id"], c["txn_id"], "transfer_pair", f"same amount, opposite direction, {best[1]} day(s) apart"))

    # refunds and reversals paired with the original entry: a refunded debit, or a reversed credit
    for r in txns:
        if r["txn_id"] in gates or not rules.REVERSAL.search(r["description_norm"]):
            continue
        r_refs = set(json.loads(r["ref_tokens"] or "[]"))
        cands = []
        for o in txns:
            if (o["txn_id"] in gates or o["txn_id"] == r["txn_id"] or o["account_id"] != r["account_id"]
                    or _dir(o) == _dir(r) or _amount(o) != _amount(r)):
                continue
            days = (r["txn_date"] - o["txn_date"]).days
            if 0 <= days <= p["reversal_max_days"]:
                shared_ref = bool(r_refs & set(json.loads(o["ref_tokens"] or "[]")))
                cands.append((not shared_ref, days, o, shared_ref))
        if cands:
            _, days, o, shared_ref = sorted(cands, key=lambda x: (x[0], x[1]))[0]
            basis = "shared reference number" if shared_ref else "same amount"
            what = "debit" if _dir(o) == "D" else "credit"
            gates[r["txn_id"]] = {"category": "REFUND_REVERSAL", "source": "gate:reversal_pair",
                                  "explanation": f"Reverses a {what} of the same amount {days} day(s) earlier ({basis})", "attrs": ["paired"]}
            gates[o["txn_id"]] = {"category": "REFUND_REVERSAL", "source": "gate:reversal_pair",
                                  "explanation": f"Reversed {days} day(s) later by a matching entry ({basis})", "attrs": ["reversed"]}
            links.append((o["txn_id"], r["txn_id"], "reversal_pair", f"{basis}, {days} day(s) apart"))
    return gates, links


def _unreadable(norm: str) -> bool:
    """Narration in a script the keyword rules do not cover yet; no evidence there is not evidence of ordinary spending."""
    return any(ch.isalpha() and ord(ch) > 0x24F for ch in norm)


def _bank_words(accounts: list[dict]) -> dict[str, str]:
    out = {}
    for a in accounts:
        for w in re.findall(r"[A-Z]{4,}", (a["bank_name"] or "").upper()):
            if w not in ("BANK", "LTD.", "LIMITED", "UNION", "CO-OPERATIVE", "OPERATIVE"):
                out[a["account_id"]] = w
                break
    return out


def run(case_id: str, trigger: str = "manual") -> str:
    con = connect()
    txns = q("""SELECT t.*, a.bank_name, a.currency FROM silver.transactions t JOIN silver.accounts a USING (account_id)
                WHERE t.case_id = ? ORDER BY t.account_id, t.txn_date, t.seq""", [case_id])
    table = fx.rates(case_id)
    for t in txns:
        fx.annotate(t, table)
    accounts = q("SELECT * FROM silver.accounts WHERE case_id = ?", [case_id])
    memory = semantic.Memory(case_id)
    eng = rule_engine.for_case(case_id)
    p = eng["params"]
    custom = [(r, re.compile(rf"(?<![A-Z0-9]){re.escape(r['keyword'])}(?![A-Z0-9])")) for r in eng["config"]["custom_rules"]]
    versions = _versions(memory.version, eng)
    input_hash = hashlib.sha256(jdump([[t["txn_id"], t["description_norm"]] for t in txns] + [versions, [[k[0], k[1], v["rate"]] for k, v in sorted(table.items())]]).encode()).hexdigest()
    last = q("SELECT run_id, input_hash, seq FROM silver.classification_runs WHERE case_id = ? ORDER BY seq DESC LIMIT 1", [case_id])
    if last and last[0]["input_hash"] == input_hash:
        return last[0]["run_id"]
    seq = (last[0]["seq"] + 1) if last else 1
    run_id = f"run_{case_id[-6:]}_{seq:04d}"

    gates, links = _gates(txns, p)
    full = defaultdict(set)
    for r in q("SELECT account_id, month FROM silver.coverage WHERE case_id = ? AND status = 'full'", [case_id]):
        full[r["account_id"]].add(r["month"])

    # periodic memory over everything that is not a hard fact
    patterns, member_of = [], {}
    for acct in {t["account_id"] for t in txns}:
        items = [{"txn_id": t["txn_id"], "txn_date": t["txn_date"], "amount": _amount(t), "direction": _dir(t),
                  "counterparty": t["counterparty"]} for t in txns if t["account_id"] == acct and t["txn_id"] not in gates]
        for pat in periodic.detect(acct, items, full[acct]):
            members = [t for t in txns if t["txn_id"] in set(pat["txn_ids"])]
            payroll_hits = sum(1 for t in members if rules.COMPILED[0][3].search(t["description_norm"]))
            pat["payroll_share"] = payroll_hits / len(members)
            patterns.append(pat)
            for tid in pat["txn_ids"]:
                member_of[tid] = pat

    # A salary has one payer at a time. A regular credit running alongside another, with no payroll wording, is a client or a tenant,
    # however steady it looks. Renamed employers are not affected: their months do not overlap.
    for pat in patterns:
        if pat["direction"] == "C" and pat["payroll_share"] < 0.34:
            others = [o for o in patterns if o is not pat and o["direction"] == "C" and o["account_id"] == pat["account_id"]
                      and len(set(o["months"]) & set(pat["months"])) >= 2]
            if others:
                pat["several_payers"] = len(others) + 1

    # account-level evidence: many distinct paying counterparties means a trading account
    business_accts = set()
    for acct in {t["account_id"] for t in txns}:
        cps = defaultdict(set)
        for t in txns:
            if t["account_id"] == acct and _dir(t) == "C" and t["counterparty"] and t["counterparty"] != "<HOLDER>":
                cps[f"{t['txn_date']:%Y-%m}"].add(t["counterparty"])
        if cps and sum(len(v) for v in cps.values()) / len(cps) >= 6:
            business_accts.add(acct)

    bank_words = _bank_words(accounts)
    neighbours = memory.neighbours([semantic.knn_text(t["description_norm"]) for t in txns])
    results = []
    for t, nbrs in zip(txns, neighbours):
        direction = _dir(t)
        if t["txn_id"] in gates:
            g = gates[t["txn_id"]]
            results.append({"txn_id": t["txn_id"], "category": g["category"], "status": "auto", "tier": "gate", "source": g["source"],
                            "probability": 1.0, "margin": 1.0, "explanation": g["explanation"], "contributions": [],
                            "candidates": [{"category": g["category"], "p": 1.0}], "attributes": g["attrs"]})
            continue
        n = t["description_norm"]
        contrib: list[dict] = []

        def add(signal, cat, value, detail):
            if cat in valid_for(direction):
                contrib.append({"signal": signal, "category": cat, "value": round(value, 3), "detail": detail})

        says_salary = direction == "C" and bool(rules.COMPILED[0][3].search(n))
        # "school fees" and "hospital fees" are what is being paid for, not a charge by the bank
        says_service = direction == "D" and bool(rules.PATTERNS["R_EDUCATION"].search(n) or rules.PATTERNS["R_MEDICAL"].search(n))
        for rid, d, cat, pat, w in rules.COMPILED:
            if rid == "R_COMPANY_PAY" and says_salary:
                continue  # with a payroll keyword, the company named is the employer, not a customer
            if rid == "R_CHARGES" and says_service:
                continue
            if d in (direction, "B") and pat.search(n):
                add(f"rule:{rid}", cat, w, f"keyword rule {rid} matched")
        for r, pat in custom:
            if r["direction"] in (direction, "B") and pat.search(n):
                add("rule:custom", r["category"], r["weight"], f"this lender's rule: {r['keyword']}")
        # two kinds of spending in one narration ("KIRANA/PETROL") is still spending; don't let them cancel out into unclassified
        fine = {c["category"] for c in contrib if c["signal"].startswith("rule:") and c["category"] in ROLLUP}
        if len(fine) > 1:
            contrib[:] = [c for c in contrib if c["category"] not in fine]
            add("rule:R_MIXED_SPEND", "GENERAL_EXPENSE", 2.7, "narration mentions more than one kind of spending")
        # naming the merchant says more than the payment rail: the generic UPI clue must not argue against it
        if any(c["signal"].startswith("rule:") and c["category"] != "GENERAL_EXPENSE" and CATEGORIES[c["category"]]["treatment"] == "expense" for c in contrib):
            contrib[:] = [c for c in contrib if c["signal"] != "rule:R_UPI_SPEND"]
        for lender in rules.LENDERS:
            if lender in n:
                add("entity:lender", "LOAN_EMI" if direction == "D" else "LOAN_DISBURSAL", 1.5, f"known lender {lender}")
        for util in rules.UTILITIES:
            if re.search(rf"\b{re.escape(util)}\b", n):
                add("entity:utility", "UTILITIES", 1.8, f"known utility provider {util}")
        for acct_id, word in bank_words.items():
            if acct_id != t["account_id"] and word in n:
                add("entity:own_bank", "INTERNAL_TRANSFER", 1.0, f"mentions {word.title()}, where the customer holds another account")
        if direction == "C" and t["account_id"] in business_accts and t["channel"] in ("UPI", "NEFT", "IMPS", "RTGS", "CHQ", "OTHER") \
                and t["counterparty"] not in ("", "<HOLDER>"):
            add("account:trading_pattern", "BUSINESS_RECEIPT", 1.3, "account receives from many different payers each month")
        series = member_of.get(t["txn_id"])
        attrs = []
        if series:
            attrs.append("recurring")
            months = len(series["months"])
            if direction == "C":
                has_payroll = series["payroll_share"] >= 0.34
                says_business = any(c["category"] == "BUSINESS_RECEIPT" and c["signal"].startswith("rule:") for c in contrib)
                # a regular customer of a shop, or a retainer client of a freelancer, is not an employer
                if series.get("several_payers"):
                    add("periodic:several_payers", "BUSINESS_RECEIPT", 1.6, f"one of {series['several_payers']} different regular payers on this account; a salary has one payer")
                elif (t["account_id"] not in business_accts and not says_business) or has_payroll:
                    add("periodic:monthly_credit", "SALARY", 1.4, f"part of a monthly credit series across {months} months on day ~{series['typical_day']}")
                    if series["amount_cv"] < 0.10:
                        add("periodic:stable_amount", "SALARY", 0.8, f"amount varies only {series['amount_cv'] * 100:.1f}% month to month")
                if has_payroll and not rules.COMPILED[0][3].search(n):
                    add("periodic:series_payroll", "SALARY", 2.0,
                        f"same series carries a payroll keyword in {round(series['payroll_share'] * len(series['txn_ids']))} of {len(series['txn_ids'])} months"
                        + (" (employer name changed)" if series["renamed"] else ""))
            elif series["amount_cv"] < 0.02:
                # recurrence says a payment is regular, not what it is: it strengthens an obligation that has its own evidence
                for cat in ("LOAN_EMI", "RENT"):
                    if any(c["category"] == cat for c in contrib):
                        add("periodic:fixed_monthly_debit", cat, 0.9, f"fixed amount every month across {months} months")
        # One analyst correction is about one row. A row whose own evidence already decides it is left alone, so a
        # correction never moves its siblings; it only helps rows that have nothing better to go on.
        own: dict[str, float] = defaultdict(float)
        for c in contrib:
            own[c["category"]] += c["value"]
        own.setdefault(BASELINE[direction], 0.0)
        own_z = sum(math.exp(v) for v in own.values())
        own_p = sorted((math.exp(v) / own_z for v in own.values()), reverse=True) + [0.0]
        settled = bool(contrib) and own_p[0] >= p["auto_min_p"] and own_p[0] - own_p[1] >= p["auto_min_margin"]
        best_by_cat: dict[str, float] = {}
        for ex, sim in nbrs:
            if sim >= p["knn_min_sim"] and ex["category"] in valid_for(direction):
                best_by_cat[ex["category"]] = max(best_by_cat.get(ex["category"], 0), sim)
        for cat, sim in best_by_cat.items():
            ex_scope = next((ex["scope"] for ex, s in nbrs if ex["category"] == cat), "global")
            if ex_scope == "case" and settled:
                continue
            add(f"memory:{ex_scope}", cat, 1.5 * sim, f"similar to approved {ex_scope} example(s), similarity {sim:.2f}")
        if jev_client.available():
            try:
                for sig, cat, val, det in jev_client.signals(jev_client.ask(n, {"channel": t["channel"], "direction": direction}), direction):
                    add(sig, cat, val, det)
            except Exception:
                pass  # a missing signal contributes zero

        # Tier 2: fusion
        scores: dict[str, float] = defaultdict(float)
        for c in contrib:
            scores[c["category"]] += c["value"]
        baseline = BASELINE[direction]
        scores.setdefault(baseline, 0.0)
        z = sum(math.exp(v) for v in scores.values())
        probs = sorted(((cat, math.exp(v) / z) for cat, v in scores.items()), key=lambda x: -x[1])
        top, p1 = probs[0]
        p2 = probs[1][1] if len(probs) > 1 else 0.0
        margin = p1 - p2
        evidence = bool(contrib)

        # Tier 3: policy
        knn_top = max(best_by_cat.items(), key=lambda x: x[1]) if best_by_cat else None
        disagree = knn_top and knn_top[1] >= config.KNN_DISAGREE_SIM and knn_top[0] != top
        if disagree and settled and any(ex["scope"] == "case" and ex["category"] == knn_top[0] and s == knn_top[1] for ex, s in nbrs):
            disagree = False
        if not evidence:
            if direction == "C" and t["credit_inr"] >= p["unexplained_credit_paise"]:
                status, category, why = "unclassified", "UNCLASSIFIED", "No evidence for any category; large credit left for analyst review"
            elif direction == "D" and _unreadable(n):
                status, category, why = "review", baseline, "Narration is in a script the rules cannot read yet; General spending is only a placeholder"
            elif direction == "D" and t["debit_inr"] >= p["unexplained_debit_paise"]:
                status, category, why = "review", baseline, "Large payment with no evidence of what it is; General spending is only a placeholder"
            else:
                status, category, why = "auto", baseline, "No specific evidence; default category for this direction"
        elif p1 >= p["auto_min_p"] and margin >= p["auto_min_margin"] and not disagree:
            status, category, why = "auto", top, f"{CATEGORIES[top]['label']} with probability {p1:.2f}"
        elif p1 >= p["review_min_p"]:
            status, category = "review", top
            why = (f"Similarity memory suggests {CATEGORIES[knn_top[0]]['label']}; " if disagree else "") + \
                  f"{CATEGORIES[top]['label']} at {p1:.2f}" + (f", close to {CATEGORIES[probs[1][0]]['label']}" if margin < p["auto_min_margin"] else "")
        else:
            status, category, why = "unclassified", "UNCLASSIFIED", f"Evidence too weak or conflicting (best {CATEGORIES[top]['label']} at {p1:.2f})"
        for c in contrib:
            c["weight_share"] = round(c["value"] / sum(x["value"] for x in contrib if x["category"] == c["category"]), 3)
        results.append({"txn_id": t["txn_id"], "category": category, "status": status, "tier": "fusion",
                        "source": "fusion" if evidence else "default", "probability": round(p1, 4), "margin": round(margin, 4),
                        "explanation": why, "contributions": contrib,
                        "candidates": [{"category": c, "p": round(p, 4)} for c, p in probs[:4]], "attributes": attrs})

    # Salary from many payers in one month is not a job, whatever the narration says. An analyst decides.
    by_id = {r["txn_id"]: r for r in results}
    salary_payers: dict[tuple, set] = defaultdict(set)
    for t in txns:
        if by_id[t["txn_id"]]["category"] == "SALARY":
            salary_payers[(t["account_id"], f"{t['txn_date']:%Y-%m}")].add(counterparty_key(t["counterparty"] or "") or t["txn_id"])
    for t in txns:
        r = by_id[t["txn_id"]]
        n_payers = len(salary_payers.get((t["account_id"], f"{t['txn_date']:%Y-%m}"), ()))
        if r["category"] == "SALARY" and n_payers > p["salary_max_payers_in_month"]:
            r.update(status="review", explanation=f"Worded as salary, but {n_payers} different payers paid salary this month")
            r["attributes"] = r["attributes"] + ["several_salary_payers"]

    # Possible rotation is never settled automatically. Rows between the customer's own accounts are suggested as
    # transfers; the rest keep their label. Everything held waits for an analyst, and income leaves it out until then.
    # compared in rupees; a row with no rate has no rupee amount to compare
    in_inr = [dict(t, debit=t["debit_inr"], credit=t["credit_inr"]) for t in txns if not t["fx_missing"]]
    holds, rotation_links = rotation.detect(in_inr, gates, by_id, p)
    links += rotation_links
    for txn_id, h in holds.items():
        r = by_id[txn_id]
        if h["kind"] == "own_accounts":
            r["category"] = "INTERNAL_TRANSFER"
        r.update(status="review", tier="rotation", source=f"rotation:{h['kind']}", explanation=h["why"])
        r["attributes"] = r["attributes"] + ["possible_rotation"]

    with tx() as c:
        c.execute("INSERT INTO silver.classification_runs VALUES (?, ?, ?, ?, ?, ?)",
                  [run_id, case_id, input_hash, jdump(versions), datetime.utcnow(), seq])
        for r in results:
            c.execute("INSERT INTO silver.classifications VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      [run_id, case_id, r["txn_id"], r["category"], r["status"], r["tier"], r["source"], r["probability"], r["margin"],
                       r["explanation"], jdump(r["contributions"]), jdump(r["candidates"]), jdump(r["attributes"])])
        c.execute("DELETE FROM silver.txn_links WHERE case_id = ?", [case_id])
        for a, b, kind, reason in links:
            c.execute("INSERT INTO silver.txn_links VALUES (?, ?, ?, ?, ?, ?)", [case_id, a, b, kind, reason, run_id])
        c.execute("DELETE FROM knowledge.periodic_patterns WHERE case_id = ?", [case_id])
        for pat in patterns:
            label = ("Recurring credit" if pat["direction"] == "C" else "Recurring debit")
            c.execute("INSERT INTO knowledge.periodic_patterns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      [case_id, pat["pattern_id"], pat["account_id"], pat["direction"], label, jdump(pat["counterparties"]), pat["median_amount"],
                       pat["amount_cv"], pat["typical_day"], jdump(pat["months"]), jdump(pat["missed_months"]), pat["expected_next"],
                       jdump(pat["txn_ids"]), run_id])
    return run_id
