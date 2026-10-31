"""Independent arithmetic checks: recompute what the app reports, from its own rows, and require them to agree.

Runs on the demo cases. Extra statements can be checked too, one case per argument, with files in a case separated by commas;
only counts are printed, so private statements stay private.

  python backend/tests/test_invariants.py [file1.pdf,file2.pdf file3.csv ...]
"""
import os
import statistics
import sys
import tempfile
from collections import Counter, defaultdict
from decimal import ROUND_HALF_EVEN, Decimal

os.environ["FINSIGHT_DATA_DIR"] = tempfile.mkdtemp(prefix="finsight_inv_")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

from app.classify.categories import CATEGORIES, EXCLUDED, OBLIGATIONS  # noqa: E402
from app.db import q  # noqa: E402
from app.main import app  # noqa: E402

problems = []


def check(case, label, ok, detail=""):
    if not ok:
        problems.append(f"{case}: {label} {detail}")


def audit_case(c, cid, name):
    a = c.get(f"/api/cases/{cid}/analysis").json()
    all_rows = c.get(f"/api/cases/{cid}/transactions").json()
    rows = [t for t in all_rows if not t["fx_missing"]]
    by_id = {t["txn_id"]: t for t in rows}
    check(name, "row counts", a["n_transactions"] == len(all_rows))

    # every transaction traces to a file, page and row
    sourced = {r["txn_id"] for r in q("SELECT DISTINCT s.txn_id FROM silver.txn_sources s JOIN silver.transactions t USING (txn_id) WHERE t.case_id = ? AND s.page_no IS NOT NULL AND s.row_no IS NOT NULL", [cid])}
    check(name, "rows without a source", {t["txn_id"] for t in all_rows} <= sourced, str(len({t["txn_id"] for t in all_rows} - sourced)))

    # each row has one known category that fits its direction, and a stated reason
    for t in all_rows:
        meta = CATEGORIES.get(t["category"])
        check(name, "unknown category", meta is not None, t["category"])
        check(name, "category against direction", meta and meta["dir"] in (t["direction"], "B"), f"{t['category']}/{t['direction']}")
        check(name, "no reason shown", bool(t["explanation"]), t["txn_id"])
        check(name, "bad status", t["status"] in ("auto", "review", "unclassified", "confirmed"), t["status"])

    # statements add up in their own currency
    for s in a["statements"]:
        if s["opening_paise"] is not None:
            check(name, "computed closing", s["computed_closing"] == s["opening_paise"] + s["total_credits"] - s["total_debits"], s["statement_id"])
        if s["recon_status"] == "reconciled":
            check(name, "reconciled but differs", s["closing_stated"] == s["computed_closing"] and s["n_breaks"] == 0, s["statement_id"])

    # foreign amounts are the printed amount times the recorded rate
    for t in all_rows:
        if t["currency"] != "INR" and not t["fx_missing"]:
            want = int((Decimal(t["orig_credit"] or t["orig_debit"]) * Decimal(t["fx_rate"])).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
            check(name, "conversion", t["amount"] == want, t["txn_id"])
        if t["fx_missing"]:
            check(name, "unconverted row has a rupee value", not t["amount"], t["txn_id"])

    m = a["metrics"]
    if not m:
        check(name, "no figures despite usable rows", not rows)
        return a, all_rows

    # monthly table equals the rows
    month_of = lambda t: t["txn_date"][:7]  # noqa: E731
    for r in a["monthly"]:
        ts = [t for t in rows if month_of(t) == r["month"]]
        check(name, "monthly credits", r["credits"] == sum(t["credit"] for t in ts), r["month"])
        check(name, "monthly debits", r["debits"] == sum(t["debit"] for t in ts), r["month"])
        cr = sum(t["credit"] for t in ts if t["category"] not in EXCLUDED)
        dr = sum(t["debit"] for t in ts if t["category"] not in EXCLUDED)
        check(name, "adjusted cash flow", (r["credits_adjusted"], r["debits_adjusted"], r["net"]) == (cr, dr, cr - dr), r["month"])
        check(name, "monthly drill-down ids", set(r["credit_ids"] + r["debit_ids"]) == {t["txn_id"] for t in ts}, r["month"])

    # every credit is accounted for in income exactly once: used or left out with a reason
    inc = m["estimated_income"]
    credits = {t["txn_id"] for t in rows if t["direction"] == "C"}
    used = [i for i in inc["included"] if i in credits]
    left = [e["txn_id"] for e in inc["excluded"] if e["txn_id"] in credits]
    seen = Counter(used + left)
    check(name, "credit not accounted for", credits <= set(seen), str(len(credits - set(seen))))
    check(name, "credit accounted for twice", all(n == 1 for n in seen.values()), str([i for i, n in seen.items() if n > 1][:2]))
    check(name, "every exclusion has a reason", all(e["reason"] for e in inc["excluded"]))
    check(name, "transfers in income", not [i for i in used if by_id[i]["category"] in EXCLUDED])
    check(name, "unconfirmed in income", not [i for i in used if by_id[i]["status"] not in ("auto", "confirmed")])

    # the income figure follows from the rows
    per_gross, per_cost = defaultdict(int), defaultdict(int)
    for i in inc["included"]:
        t = by_id[i]
        if t["direction"] == "C":
            per_gross[month_of(t)] += t["credit"]
        else:
            per_cost[month_of(t)] += t["debit"]
    for x in inc["monthly"]:
        check(name, "monthly income receipts", x.get("gross", x["value"]) == per_gross[x["month"]], x["month"])
        check(name, "monthly business costs", x.get("costs", 0) == per_cost[x["month"]], x["month"])
        check(name, "monthly income value", x["value"] == per_gross[x["month"]] - per_cost[x["month"]], x["month"])
        check(name, "regular + variable covers income", x["regular"] + x["variable"] >= x.get("gross", x["value"]), x["month"])
    series = [x["value"] for x in inc["monthly"]]
    if series and inc["value"] is not None:
        med = int(statistics.median(series))
        if "recent 3-month" in inc["method"]:
            want = max(0, int(statistics.mean(series[-3:])))
        else:
            want = max(0, med)
        check(name, "income value", inc["value"] == want, f"{inc['value']} vs {want}")
    check(name, "negative income", inc["value"] is None or inc["value"] >= 0)
    check(name, "income months are fully covered", all(a["coverage"].get(mm) == "full" or len(a["account_view"]) > 1 for mm in inc["months_used"]), str(inc["months_used"]))

    # obligations, ratio and surpluses follow
    ob = m["recurring_obligations"]
    check(name, "obligations total", ob["value"] == sum(i["monthly_amount"] for i in ob["items"] if i["active"]))
    check(name, "obligation rows", all(by_id[i]["category"] in OBLIGATIONS for i in ob["included"]))
    check(name, "obligations say why", all(i.get("why") for i in ob["items"]))
    if inc["value"]:
        check(name, "ratio", m["foir"]["value"] == round(ob["value"] / inc["value"], 3))
        check(name, "surplus after obligations", m["affordability"]["value"] == inc["value"] - ob["value"])
        check(name, "monthly surplus", m["monthly_surplus"]["value"] == inc["value"] - m["monthly_surplus"]["outflows"])
    check(name, "outflows charged once", not (set(m["monthly_surplus"]["included"]) & {i for i in inc["included"] if by_id[i]["direction"] == "D"}))
    check(name, "outflows total", abs(m["monthly_surplus"]["outflows"] - sum(i["monthly_amount"] for i in m["monthly_surplus"]["items"])) == 0)

    # net cash flow and its assessment
    nets = [r["net"] for r in a["monthly"] if r["coverage"] == "full" and r["active"]]
    if nets:
        check(name, "average net", m["net_cash_flow"]["value"] == int(statistics.mean(nets)))
    check(name, "trend label", m["cash_flow_trend"]["assessment"] in ("improving", "stable", "deteriorating", "not enough months"))

    # account views add up to the consolidated view
    for r in a["monthly"]:
        parts = [next((x for x in v["months"] if x["month"] == r["month"]), None) for v in a["account_view"]]
        parts = [p for p in parts if p]
        for k in ("credits", "debits", "credits_adjusted", "debits_adjusted", "net"):
            check(name, f"accounts add up: {k}", sum(p[k] for p in parts) == r[k], r["month"])

    # matched pairs: both legs share a category and an amount within tolerance, in different accounts
    for l in q("SELECT txn_a, txn_b FROM silver.txn_links WHERE case_id = ? AND kind = 'transfer_pair'", [cid]):
        d, cr = by_id.get(l["txn_a"]), by_id.get(l["txn_b"])
        if d and cr and d["status"] != "confirmed" and cr["status"] != "confirmed":
            check(name, "transfer legs", d["category"] == cr["category"] == "INTERNAL_TRANSFER" and d["account_id"] != cr["account_id"])

    # indicators: a rule, resolvable evidence, and no accusation
    ids = {t["txn_id"] for t in all_rows}
    for f in a["flags"]:
        check(name, "indicator without a rule", f["category"] != "risk" or f.get("rule"), f["code"])
        check(name, "indicator evidence", set(f["txn_ids"]) <= ids, f["code"])
        check(name, "accusing wording", "fraud" not in (f["title"] + f["detail"]).lower(), f["code"])

    # the report: every evidence row resolves to file, page and row
    rep = c.get(f"/api/cases/{cid}/report").json()
    check(name, "report states it is not a decision", "does not approve or decline" in rep["notice"])
    for s in rep["sections"]:
        for i in s["evidence"]:
            e = rep["evidence"].get(i)
            check(name, "report evidence", e and e["sources"] and e["sources"][0]["file"], i)

    # same inputs, same result
    again = c.get(f"/api/cases/{cid}/analysis").json()
    check(name, "repeatable", again["snapshot_id"] == a["snapshot_id"])
    return a, all_rows


with TestClient(app) as c:
    c.post("/api/demo/load").raise_for_status()
    n = 0
    for case in c.get("/api/cases").json():
        audit_case(c, case["case_id"], case["name"])
        n += 1
    for i, group in enumerate(sys.argv[1:]):
        cid = c.post("/api/cases", json={"name": f"extra {i + 1}"}).json()["case_id"]
        for path in group.split(","):
            with open(path, "rb") as fh:
                r = c.post(f"/api/cases/{cid}/files", files={"file": (os.path.basename(path), fh.read())})
                assert r.status_code == 200, r.status_code
        a, rows = audit_case(c, cid, f"extra {i + 1}")
        print(f"extra {i + 1}: {len(group.split(','))} file(s), {len(rows)} rows, {len(a['statements'])} statement(s), currencies {a['fx']['currencies']}")
        n += 1

print(f"checked {n} case(s)")
for p in problems[:40]:
    print("PROBLEM", p)
if problems:
    print(f"{len(problems)} PROBLEM(S)")
    sys.exit(1)
print("ALL INVARIANTS HOLD")
