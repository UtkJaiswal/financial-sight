"""Possible money rotation: the same money going round so that it looks like more income.

Exact own-account pairs are hard facts and are settled by the Tier 1 gate. What is found here is less certain:
a credit that arrives days later, is split, has a fee taken off, goes out to a party and comes back, or leaves as
fast as it arrives. These are never settled automatically. They go to an analyst, and income leaves them out until
then. Each finding says "review recommended"; only a person can say what the money really was.
"""
from __future__ import annotations

from itertools import combinations

from rapidfuzz import fuzz

from .. import config
from ..ingest.normalize import counterparty_key


def _close(a: int, b: int, fee_max: int, across_currencies: bool = False) -> bool:
    """Equal apart from a transfer fee: at most ₹1,000 and at most 0.5% of the amount. Across currencies, the rate difference is allowed for."""
    if across_currencies:
        return abs(a - b) <= max(a, b) * config.FX_MATCH_TOLERANCE_PCT // 100
    return abs(a - b) <= min(fee_max, max(a, b) * 5 // 1000)


def _order(t: dict) -> tuple:
    return (t["txn_date"], t["txn_id"])


def detect(txns: list[dict], gates: dict, results: dict[str, dict], p: dict) -> tuple[dict[str, dict], list[tuple]]:
    """Returns ({txn_id: {kind, why}}, [(debit_id, credit_id, link_kind, reason)]). Earlier checks are the more specific ones."""
    if not p["rotation_enabled"]:
        return {}, []
    fee_max = p["rotation_fee_max_paise"]
    free = [t for t in txns if t["txn_id"] not in gates]
    debits = sorted((t for t in free if t["debit"]), key=_order)
    credits = sorted((t for t in free if t["credit"]), key=_order)
    used: set[str] = set()
    holds: dict[str, dict] = {}
    links: list[tuple] = []

    def hold(t: dict, kind: str, why: str) -> None:
        used.add(t["txn_id"])
        holds[t["txn_id"]] = {"kind": kind, "why": why}

    window = p["rotation_window_days"]

    # 1. one debit and one credit across the customer's own accounts, later than the gate allows or short by a fee
    for d in debits:
        if d["debit"] < p["rotation_min_paise"]:
            continue
        cands = [c for c in credits if c["txn_id"] not in used and c["account_id"] != d["account_id"] and _close(c["credit"], d["debit"], fee_max, c["currency"] != d["currency"])
                 and -1 <= (c["txn_date"] - d["txn_date"]).days <= window]
        if cands:
            c = min(cands, key=lambda c: (abs((c["txn_date"] - d["txn_date"]).days), abs(c["credit"] - d["debit"]), c["txn_id"]))
            gap = abs((c["txn_date"] - d["txn_date"]).days)
            why = f"Possible money rotation: a similar amount moved between the customer's own accounts {gap} day(s) apart"
            hold(d, "own_accounts", why)
            hold(c, "own_accounts", why)
            links.append((d["txn_id"], c["txn_id"], "rotation_near", f"similar amount across own accounts, {gap} day(s) apart"))

    # 2. one entry split into two or three on the other side, in either direction
    def splits(ones: list[dict], parts: list[dict], one_amt: str, part_amt: str) -> None:
        for one in ones:
            if one["txn_id"] in used or one[one_amt] < p["rotation_min_paise"]:
                continue
            near = [x for x in parts if x["txn_id"] not in used and x["account_id"] != one["account_id"]
                    and p["rotation_part_min_paise"] <= x[part_amt] < one[one_amt] and abs((x["txn_date"] - one["txn_date"]).days) <= window]
            near = sorted(near, key=lambda x: (abs((x["txn_date"] - one["txn_date"]).days), x["txn_id"]))[:config.ROTATION_SPLIT_CANDIDATES]
            for size in (2, 3):
                match = next((c for c in combinations(near, size) if _close(sum(x[part_amt] for x in c), one[one_amt], fee_max)), None)
                if match:
                    why = f"Possible money rotation: one entry split into {size} across the customer's own accounts"
                    hold(one, "own_accounts", why)
                    for x in match:
                        hold(x, "own_accounts", why)
                        pair = (one, x) if one_amt == "debit" else (x, one)
                        links.append((pair[0]["txn_id"], pair[1]["txn_id"], "rotation_split", f"split into {size} entries"))
                    break

    splits(debits, credits, "debit", "credit")
    splits(credits, debits, "credit", "debit")

    # 3. out to a party and back from the same party for about the same amount, in either order
    big = p["rotation_min_paise"]
    for d in debits:
        key = counterparty_key(d["counterparty"] or "")
        if d["txn_id"] in used or d["debit"] < big or not key or d["counterparty"] == "<HOLDER>":
            continue
        cands = [c for c in credits if c["txn_id"] not in used and abs(c["credit"] - d["debit"]) <= d["debit"] * 2 // 100
                 and abs((c["txn_date"] - d["txn_date"]).days) <= p["rotation_party_days"]
                 and fuzz.token_set_ratio(key, counterparty_key(c["counterparty"] or "")) >= 88]
        if cands:
            c = min(cands, key=lambda c: (abs((c["txn_date"] - d["txn_date"]).days), c["txn_id"]))
            gap = abs((c["txn_date"] - d["txn_date"]).days)
            why = f"Possible money rotation: about the same amount went to and came from {key} {gap} day(s) apart"
            hold(d, "same_party", why)
            hold(c, "same_party", why)
            links.append((d["txn_id"], c["txn_id"], "rotation_party", f"same party, {gap} day(s) apart"))

    # 4. a repeated habit of money leaving as fast as it arrives, between different parties. One case could be a coincidence,
    # so it takes several. A payroll credit is left alone: a salary swept out on payday says nothing about the salary.
    by_account: dict[str, list[tuple[dict, dict]]] = {}
    taken: set[str] = set()
    for c in credits:
        if c["txn_id"] in used or c["credit"] < p["rotation_pass_min_paise"] or results[c["txn_id"]]["category"] == "SALARY":
            continue
        d = next((d for d in debits if d["txn_id"] not in used and d["txn_id"] not in taken and d["account_id"] == c["account_id"]
                  and abs(d["debit"] - c["credit"]) <= c["credit"] // 100
                  and 0 <= (d["txn_date"] - c["txn_date"]).days <= p["rotation_pass_days"]), None)
        if d:
            taken.add(d["txn_id"])
            by_account.setdefault(c["account_id"], []).append((c, d))
    for pairs in by_account.values():
        if len(pairs) < p["rotation_pass_min_count"]:
            continue
        for c, d in pairs:
            why = f"Possible money rotation: this credit left the account again within {(d['txn_date'] - c['txn_date']).days} day(s), one of {len(pairs)} such cases"
            hold(c, "pass_through", why)
            hold(d, "pass_through", why)
            links.append((d["txn_id"], c["txn_id"], "rotation_pass", "similar amount in and straight back out"))
    return holds, links
