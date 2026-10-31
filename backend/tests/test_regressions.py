"""Failures found by testing real statements, reproduced on generated files so no customer data enters the repo.

  python backend/tests/test_regressions.py
"""
import io
import logging
import os
import sys
import tempfile

os.environ["FINSIGHT_DATA_DIR"] = tempfile.mkdtemp(prefix="finsight_reg_")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle  # noqa: E402

from app.main import app  # noqa: E402
from app.security.log_scrub import PiiScrubber  # noqa: E402

CSV_HEAD = ("Bank X - Account Statement\nAccount Name:,ASHA RAO\nAccount Number:,11112222333\nStatement Period:,{period}\n"
            "Opening Balance:,{opening:.2f}\n\nDate,Description,Debit,Credit,Balance\n")


def csv(rows, period="01-07-2026 to 31-12-2026", opening=1000.0):
    bal, lines = opening, []
    for d, n, dr, cr in sorted(rows, key=lambda r: (r[0][6:], r[0][3:5], r[0][:2])):
        bal += cr - dr
        lines.append(f"{d},{n},{dr or ''},{cr or ''},{bal:.2f}")
    return (CSV_HEAD.format(period=period, opening=opening) + "\n".join(lines)).encode()


def table_pdf(header, body, lead=()):
    """A ruled table, the way iText-based bank exports draw one."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20, rightMargin=20)
    t = Table([header] + body, repeatRows=0)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, (0, 0, 0)), ("FONTSIZE", (0, 0), (-1, -1), 7)]))
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph
    style = getSampleStyleSheet()["Normal"]
    doc.build([Paragraph(x, style) for x in lead] + [t])
    return buf.getvalue()


def borderless_pdf(rows):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 8)
    y = 800
    for line in ("HDFC BANK Ltd", "Account No : 501000123456", "Customer Name : ASHA RAO", "Statement From : 01/07/2026 To : 30/09/2026", ""):
        c.drawString(30, y, line)
        y -= 12
    y -= 10
    cols = [(30, "Date"), (90, "Narration"), (290, "Chq./Ref.No."), (360, "Withdrawal Amt."), (440, "Deposit Amt."), (510, "Closing Balance")]
    for x, label in cols:
        c.drawString(x, y, label)
    y -= 14
    for date, narr, wrap, dr, cr, bal in rows:
        c.drawString(30, y, date)
        c.drawString(90, y, narr)
        for x, v in ((410, dr), (490, cr), (565, bal)):
            if v:
                c.drawRightString(x, y, v)
        y -= 11
        if wrap:
            c.drawString(90, y, wrap)
            y -= 11
    y -= 60
    c.drawString(30, y, "Closing Balance : 1,41,000.00")
    c.save()
    return buf.getvalue()


with TestClient(app) as c:
    def new_case(name):
        return c.post("/api/cases", json={"name": name}).json()["case_id"]

    def upload(cid, name, data, **form):
        r = c.post(f"/api/cases/{cid}/files", files={"file": (name, data)}, data=form)
        assert r.status_code == 200, r.text
        return r.json()["file"]

    def analysis(cid):
        return c.get(f"/api/cases/{cid}/analysis").json()

    def txns(cid):
        return c.get(f"/api/cases/{cid}/transactions").json()

    # ---- a header wrapped mid-word, with dates such as 01/Apr/2024
    body = [["1", "S100", "01/Apr/2024", "01/Apr/2024", "01/04/2024 10:00:00 AM", "", "NEFT/RENT/CLIENT A", "", "1,00,000.00", "1,00,000.00"],
            ["2", "S101", "02/Apr/2024", "02/Apr/2024", "02/04/2024 10:00:00 AM", "", "RTGS/SUPPLIER B", "40,000.00", "", "60,000.00"]]
    head = ["Sl\nNo", "Tran\nId", "Value\nDate", "Transaction\nDate", "Transaction\nPosted\nDate", "Cheque no /\nRef No",
            "Transaction\nRemarks", "Withdra\nwal (Dr)", "Deposit\n(Cr)", "Balance"]
    lead = ["Detailed Statement", "Name: ACME BUILDERS PRIVATE LIMITED A/C Branch: MUMBAI", "A/C No: 000405112233 A/C Type: CAA",
            "Transaction Period: From 01/04/2024 To 30/04/2024", "IFSC Code: ICIC0000405"]
    f = upload(new_case("wrapped header"), "wrapped.pdf", table_pdf(head, body, lead))
    assert f["status"] == "processed" and f["statements"][0]["n_rows"] == 2, f
    s = f["statements"][0]
    assert s["n_breaks"] == 0 and s["bank_name"] == "ICICI Bank", s
    assert s["total_debits"] == 40_000_00 and s["total_credits"] == 1_00_000_00, s
    print("ok: wrapped header and 01/Apr/2024 dates; bank read from the IFSC code, not the title")

    # ---- no column labels in the text layer: columns come from the data, debit and credit from the balance
    def rows_for(dr_col):
        out = []
        for d, n, dr, cr, bal in (("01/07/2026", "UPI/A", "", "5,000.00", "15,000.00"), ("02/07/2026", "UPI/B", "1,000.00", "", "14,000.00"),
                                  ("03/07/2026", "UPI/C", "2,000.00", "", "12,000.00")):
            a, b = (dr, cr) if dr_col == 4 else (cr, dr)
            out.append([d, d, n, "-", a or "-", b or "-", bal])
        return out

    for dr_col, label in ((4, "debit first"), (5, "credit first")):
        f = upload(new_case(label), f"{label}.pdf", table_pdf(["", "", "", "", "", "", "Balance"], rows_for(dr_col), ["Account Number : 11122233344"]))
        s = f["statements"][0]
        assert s["n_rows"] == 3 and s["n_breaks"] == 0 and s["total_debits"] == 3_000_00 and s["total_credits"] == 5_000_00, (label, s)
    print("ok: labels missing from the text layer, debit and credit told apart by the running balance")

    # ---- no ruling lines at all, with wrapped narrations and a footer that must not join the last row
    f = upload(new_case("borderless"), "borderless.pdf", borderless_pdf([
        ("01/07/26", "NEFT-ACME TECHNOLOGIES-SALARY JUL", "", "", "60,000.00", "1,60,000.00"),
        ("05/07/26", "IMPS-LANDLORD-HOUSE RENT", "SECOND LINE OF NARRATION", "12,000.00", "", "1,48,000.00"),
        ("10/07/26", "NACH-BAJAJ FINANCE-EMI", "", "7,000.00", "", "1,41,000.00")]))
    s = f["statements"][0]
    assert f["pages"][0]["method"] == "pdfplumber.words" and s["n_rows"] == 3 and s["n_breaks"] == 0, f
    assert s["total_debits"] == 19_000_00 and s["total_credits"] == 60_000_00 and s["bank_name"].upper().startswith("HDFC BANK"), s
    print("ok: borderless table read by word position")

    # ---- statements printed newest first
    cid = new_case("newest first")
    grid = "Date,Description,Debit,Credit,Balance\n03-07-2026,UPI/C,2000,,12000.00\n02-07-2026,UPI/B,1000,,14000.00\n01-07-2026,NEFT/A,,5000,15000.00\n"
    f = upload(cid, "newest.csv", ("Account Number:,11122233355\n\n" + grid).encode())
    assert f["statements"][0]["n_breaks"] == 0 and f["statements"][0]["opening_paise"] == 10_000_00, f["statements"]
    assert analysis(cid)["monthly"][0]["debits"] == 3_000_00
    print("ok: newest-first statement reconciles")

    # ---- password protected PDF: asks for the password, and retrying with it works
    cid = new_case("locked")
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(table_pdf(head, body, lead))))
    w.encrypt("secret123")
    b = io.BytesIO()
    w.write(b)
    f = upload(cid, "locked.pdf", b.getvalue())
    assert f["status"] == "needs_password", f
    f = upload(cid, "locked.pdf", b.getvalue(), password="wrong")
    assert f["status"] == "needs_password", f
    f = upload(cid, "locked.pdf", b.getvalue(), password="secret123")
    assert f["status"] == "processed" and f["statements"][0]["n_rows"] == 2, f
    print("ok: password flow")

    # ---- a correction changes the corrected row and what depends on it, not its neighbours
    salary = [(f"01-{m:02d}-2026", "NEFT/ABC TECHNOLOGIES SALARY", 0, 60000) for m in range(7, 13)]
    for name, other in (("near identical", ("15-08-2026", "NEFT/ABC TECHNOLOGIES SALARY BONUS", 0, 50000)),
                        ("same employer", ("15-08-2026", "NEFT/ABC TECHNOLOGIES REIMBURSEMENT", 0, 50000)),
                        ("unrelated text", ("15-08-2026", "IMPS/ASHA RAO/SAVINGS TRF", 0, 50000))):
        cid = new_case(name)
        upload(cid, "s.csv", csv(salary + [other]))
        wrong = next(t for t in txns(cid) if t["credit"] == 50000_00)
        r = c.post(f"/api/cases/{cid}/transactions/{wrong['txn_id']}/override", json={"category": "INTERNAL_TRANSFER", "reason": "own account"})
        assert r.status_code == 200, r.text
        inc = analysis(cid)["metrics"]["estimated_income"]
        after = txns(cid)
        assert inc["value"] == 60000_00 and inc["stats"]["highest"] == 60000_00, (name, inc["value"], inc["stats"])
        assert all(t["status"] == "auto" and t["category"] == "SALARY" for t in after if t["credit"] == 60000_00), (name, after)
        d = c.get(f"/api/cases/{cid}/transactions/{wrong['txn_id']}").json()
        assert d["txn"]["status"] == "confirmed" and d["txn"]["category"] == "INTERNAL_TRANSFER" and d["txn"]["model_category"], d["txn"]
        assert r.json()["diff"]["transactions"] == [], (name, r.json()["diff"]["transactions"])
    # and on the demo case a judge sees first: no neighbour moves, and with June's salary gone the falling-income rule applies
    c.post("/api/demo/load").raise_for_status()
    rohan = next(x for x in c.get("/api/cases").json() if "Rohan" in x["name"])["case_id"]
    last = [t for t in txns(rohan) if t["category"] == "SALARY"][-1]
    res = c.post(f"/api/cases/{rohan}/transactions/{last['txn_id']}/override", json={"category": "INTERNAL_TRANSFER", "reason": "own account"}).json()
    inc = analysis(rohan)["metrics"]["estimated_income"]
    assert all(x["before"][1] not in ("auto", "confirmed") for x in res["diff"]["transactions"]), res["diff"]["transactions"]
    assert inc["value"] == 64133_33 and "recent 3-month average" in inc["method"], (inc["value"], inc["method"])
    print("ok: correcting one row leaves the salary series and the income figure intact")

    # ---- the same statement exported twice with different spacing counts once, and rewording is recorded
    cid = new_case("cross format")
    upload(cid, "a.csv", csv(salary))
    before = analysis(cid)
    f = upload(cid, "b.csv", csv([(d, n.replace("/", " / "), x, y) for d, n, x, y in salary]))
    after = analysis(cid)
    assert after["n_transactions"] == before["n_transactions"] == 6 and after["metrics"]["estimated_income"]["value"] == 60000_00
    assert not [q for q in f["quarantine"] if q["kind"] == "narration_differs"], "spacing alone is not a rewording"
    f = upload(cid, "c.csv", csv([(d, "SAL CREDIT FROM SOMEONE ELSE", x, y) for d, n, x, y in salary]))
    assert analysis(cid)["n_transactions"] == 6 and len([q for q in f["quarantine"] if q["kind"] == "narration_differs"]) == 6, f["quarantine"]
    print("ok: overlapping exports counted once; a reworded row is counted once and recorded")

    # ---- statement period starting long before the first entry: no zero months, no false low balance
    cid = new_case("quiet start")
    rows = [(f"05-{m:02d}-2026", f"NEFT/CLIENT {i} PAYMENT/INV{m}", 0, 1_00_000) for i, m in enumerate((10, 11, 12))]
    upload(cid, "q.csv", csv(rows, period="01-01-2026 to 31-12-2026", opening=0.0))
    a = analysis(cid)
    inc = a["metrics"]["estimated_income"]
    assert inc["value"] == 1_00_000_00 and inc["months_used"] == ["2026-10", "2026-11", "2026-12"], (inc["value"], inc["months_used"])
    assert "low_balance" not in {x["code"] for x in a["flags"]}, a["flags"]
    assert a["metrics"]["net_cash_flow"]["value"] == 1_00_000_00, a["metrics"]["net_cash_flow"]["value"]
    assert any("first entry" in n for n in inc["notes"])
    print("ok: months before the first entry are left out and said so")

    # ---- real money is still missing: a gap inside the period stays out of income and is not zero
    cid = new_case("real gap")
    upload(cid, "j.csv", csv([("01-07-2026", "NEFT/ABC SALARY", 0, 60000)], period="01-07-2026 to 31-07-2026"))
    upload(cid, "s.csv", csv([("01-09-2026", "NEFT/ABC SALARY", 0, 60000)], period="01-09-2026 to 30-09-2026"))
    a = analysis(cid)
    assert a["coverage"]["2026-08"] == "missing" and "2026-08" in a["metrics"]["estimated_income"]["months_missing"]
    print("ok: a statement gap is still missing, not inactive")

    # ---- a guessed EMI on irregular payments adds no commitment; a stable one still counts until reviewed
    cid = new_case("obligations")
    rows = [(f"03-{m:02d}-2026", "ACH/VENDOR ONE", (40000 + (m - 7) * 30000), 0) for m in range(7, 11)]
    rows += [(f"05-{m:02d}-2026", "ACH/VENDOR TWO", 9000, 0) for m in range(7, 11)]
    rows += [(f"01-{m:02d}-2026", "NEFT/ABC TECHNOLOGIES SALARY", 0, 300000) for m in range(7, 11)]
    upload(cid, "o.csv", csv(rows, period="01-07-2026 to 31-10-2026"))
    ob = analysis(cid)["metrics"]["recurring_obligations"]
    items = {i["counterparty"]: i for i in ob["items"]}
    one = next(i for k, i in items.items() if "ONE" in k)
    two = next(i for k, i in items.items() if "TWO" in k)
    assert one["pending"] and not one["active"], one
    assert two["active"] and ob["value"] == 9000_00, (two, ob["value"])
    assert any("unconfirmed" in n for n in ob["notes"]), ob["notes"]
    print("ok: an irregular guessed EMI is held back and reported; a stable one counts")

    # ---- no evidence is not evidence: large or unreadable debits go to review
    cid = new_case("no evidence")
    upload(cid, "n.csv", csv([("01-07-2026", "NEFT/AB", 90000, 0), ("02-07-2026", "भुगतान", 100, 0), ("03-07-2026", "NEFT/CD", 100, 0)], period="01-07-2026 to 31-07-2026", opening=100000.0))
    by = {t["description"]: t["status"] for t in txns(cid)}
    assert by == {"NEFT/AB": "review", "भुगतान": "review", "NEFT/CD": "auto"}, by
    print("ok: unexplained large or unreadable debits go to review")

    # ---- every figure has a breakdown
    cid = new_case("lineage")
    upload(cid, "l.csv", csv(salary + [(f"05-{m:02d}-2026", "NACH/BAJAJ FINANCE LOAN EMI", 9000, 0) for m in range(7, 13)]))
    a = analysis(cid)
    assert a["metrics"]["foir"]["included"] and a["metrics"]["average_balance"]["included"]
    assert all(r["credit_ids"] and r["debit_ids"] for r in a["monthly"]), a["monthly"][0]
    print("ok: FOIR, average balance and monthly totals list their transactions")

    # ---- a workbook from another analyser: statement sheet plus summary sheets
    wb = Workbook()
    ws = wb.active
    ws.title = "Account Details"
    ws.append(["Sl No.", "Account Number", "Account Holder Name", "IFSC Code", "Bank Name", "Account Type", "Period From", "Period To"])
    ws.append([1, "000405112233", "ACME BUILDERS", "ICIC0000405", "ICICI", "Current Account", "01-07-2026", "31-07-2026"])
    tx = wb.create_sheet("Transactions")
    tx.append(["Date", "Narration", "Cheque No", "Credit", "Debit", "Balance"])
    tx.append(["01-07-2026", "NEFT/CLIENT A", "", 5000, None, 5000])
    tx.append(["02-07-2026", "RTGS/VENDOR B", "", None, 1000, 4000])
    sub = wb.create_sheet("Fund Received")
    sub.append(["Date", "Narration", "Cheque No", "Credit", "Debit", "Balance"])
    sub.append(["01-07-2026", "NEFT/CLIENT A", "", 5000, None, 5000])
    wb.create_sheet("Empty notes").append(["nothing to see"])
    b = io.BytesIO()
    wb.save(b)
    cid = new_case("workbook")
    f = upload(cid, "analyser.xlsx", b.getvalue())
    s = f["statements"][0]
    assert s["n_rows"] == 2 and s["acct_masked"].endswith("2233") and s["bank_name"] == "ICICI Bank", s
    assert f["status"] == "processed", (f["status"], f["reason"])
    assert not [x for x in analysis(cid)["flags"] if x["code"] == "page_not_processed"]
    # tabs that continue a statement are merged, not dropped
    wb = Workbook()
    a1 = wb.active
    a1.append(["Date", "Narration", "Credit", "Debit", "Balance"])
    a1.append(["01-07-2026", "NEFT/CLIENT A", 5000, None, 5000])
    a2 = wb.create_sheet("Aug")
    a2.append(["Date", "Narration", "Credit", "Debit", "Balance"])
    a2.append(["01-08-2026", "NEFT/CLIENT B", 2000, None, 7000])
    b = io.BytesIO()
    wb.save(b)
    f = upload(new_case("monthly tabs"), "tabs.xlsx", b.getvalue())
    assert f["statements"][0]["n_rows"] == 2 and f["statements"][0]["n_breaks"] == 0, f["statements"]
    print("ok: analyser workbook uses the statement sheet once; monthly tabs merge")

    # ---- money rotating: real income ₹1 lakh a month, ₹5 lakh going round on top. Income must read the real figure every way
    real = [(f"08-{m:02d}-2026", f"NEFT/CLIENT ABC/INV{m}", 0, 100000) for m in range(7, 13)]
    A, B, C = "11110000001", "22220000002", "33330000003"

    def day(d, m):
        return f"{d:02d}-{m:02d}-2026"

    def out(d, text=None):
        return [(day(d, m), text or f"NEFT/PAYMENT{m}", 500000, 0) for m in range(7, 13)]

    def rot_case(name, files):
        cid = new_case(name)
        for i, (acct, rows) in enumerate(files.items()):
            body = CSV_HEAD.format(period="01-07-2026 to 31-12-2026", opening=2000000.0).replace("11112222333", acct)
            bal, lines = 2000000.0, []
            for d, n, dr, cr in sorted(rows, key=lambda r: (r[0][3:5], r[0][:2])):
                bal += cr - dr
                lines.append(f"{d},{n},{dr or ''},{cr or ''},{bal:.2f}")
            upload(cid, f"{i}.csv", (body + "\n".join(lines)).encode())
        return cid, analysis(cid)

    scenarios = {
        "same day": ({A: real + out(12), B: [(day(12, m), f"NEFT/PAYMENT{m}", 0, 500000) for m in range(7, 13)]}, None),
        "three days late": ({A: real + out(12), B: [(day(15, m), f"NEFT/SHARMA TRADERS/INV{m}", 0, 500000) for m in range(7, 13)]}, "possible_rotation"),
        "split in two": ({A: real + out(12), B: [(day(12, m), f"NEFT/SHARMA TRADERS/INV{m}", 0, 200000) for m in range(7, 13)]
                          + [(day(13, m), f"NEFT/GUPTA ENTERPRISES/INV{m}", 0, 300000) for m in range(7, 13)]}, "possible_rotation"),
        "fee taken off": ({A: real + out(12), B: [(day(12, m), f"NEFT/SHARMA TRADERS/INV{m}", 0, 499975) for m in range(7, 13)]}, "possible_rotation"),
        "out and back": ({A: real + out(12, "NEFT/SHARMA TRADERS/ADVANCE") + [(day(16, m), f"NEFT/SHARMA TRADERS/INV{m}", 0, 500000) for m in range(7, 13)]}, "possible_rotation"),
        "account not uploaded": ({A: real + [(day(12, m), f"NEFT/GUPTA ENTERPRISES/INV{m}", 0, 500000) for m in range(7, 13)]
                                  + [(day(14, m), f"NEFT/VERMA AND CO/PO{m}", 500000, 0) for m in range(7, 13)]}, "possible_rotation"),
    }
    for name, (files, flag) in scenarios.items():
        cid, a = rot_case(name, files)
        inc = a["metrics"]["estimated_income"]
        assert inc["value"] == 1_00_000_00, (name, inc["value"])
        assert not inc["stats"].get("provisional"), (name, inc["stats"])
        codes = {f["code"] for f in a["flags"]}
        assert (flag in codes) if flag else ("possible_rotation" not in codes), (name, codes)
        if flag:
            assert inc["stats"]["income_if_flagged_real"] >= 5_00_000_00, (name, inc["stats"])
            detail = next(f["detail"] for f in a["flags"] if f["code"] == flag)
            assert "Review recommended" in detail and "fraud" not in detail.lower(), detail

    # a full circle through three accounts is found and shown, and is not income
    cid, a = rot_case("circle", {
        A: real + out(12, "RTGS/R") + [(day(14, m), "RTGS/S", 0, 500000) for m in range(7, 13)],
        B: [(day(12, m), "RTGS/R", 0, 500000) for m in range(7, 13)] + out(13, "RTGS/Q"),
        C: [(day(13, m), "RTGS/Q", 0, 500000) for m in range(7, 13)] + out(14, "RTGS/S")})
    assert a["metrics"]["estimated_income"]["value"] == 1_00_000_00
    cy = a["money_flows"]["cycles"]
    assert len(cy) == 1 and cy[0]["count"] == 6 and len(cy[0]["path"]) == 4 and cy[0]["path"][0] == cy[0]["path"][-1], cy
    assert "circular_flow" in {f["code"] for f in a["flags"]}

    # the analyst decides: a held credit confirmed as a real sale counts, and leaves the rotation view
    cid, a = rot_case("analyst decides", scenarios["three days late"][0])
    held = next(t for t in txns(cid) if t["credit"] == 5_00_000_00 and "possible_rotation" in t["attributes"])
    assert held["status"] == "review" and held["category"] == "INTERNAL_TRANSFER", held
    r = c.post(f"/api/cases/{cid}/transactions/{held['txn_id']}/override", json={"category": "BUSINESS_RECEIPT", "reason": "invoice checked"})
    assert r.status_code == 200, r.text
    after = analysis(cid)
    assert sum(x["value"] for x in after["metrics"]["estimated_income"]["monthly"]) == 6_00_000_00 + 5_00_000_00
    assert held["txn_id"] in after["metrics"]["estimated_income"]["included"]
    assert sum(e["count"] for e in after["money_flows"]["edges"] if e["possible"]) == 5, after["money_flows"]["edges"]

    # honest activity raises no alarm: suppliers of different amounts, a salary swept out on payday, one coincidence
    cid = new_case("honest trader")
    rows = [(day(8, m), f"NEFT/ACME TRADERS/INV{m}", 0, 500000 + m * 7000) for m in range(7, 13)]
    rows += [(day(10, m), f"RTGS/STEEL SUPPLIERS/PO{m}", 390000 - m * 3000, 0) for m in range(7, 13)]
    rows += [("20-08-2026", "NEFT/ONE OFF CUSTOMER/INV", 0, 200000), ("21-08-2026", "RTGS/BUILDER ADVANCE", 200000, 0)]
    upload(cid, "t.csv", csv(rows, opening=500000.0))
    assert not [t for t in txns(cid) if "possible_rotation" in t["attributes"]]
    cid = new_case("salary swept out")
    rows = [(day(1, m), "NEFT/ABC TECHNOLOGIES SALARY", 0, 150000) for m in range(7, 13)] + [(day(2, m), "RTGS/SOMEONE ELSE", 150000, 0) for m in range(7, 13)]
    upload(cid, "s.csv", csv(rows))
    assert analysis(cid)["metrics"]["estimated_income"]["value"] == 1_50_000_00
    print("ok: rotation in every tested form reads the real income, is flagged for review, and honest activity is left alone")

    # ---- finer spending labels, and two kinds of spending in one narration stay spending
    cid = new_case("spending")
    rows = [("02-07-2026", "UPI/BIGBASKET/GROCERY", 3000, 0), ("03-07-2026", "UPI/UBER/RIDE", 400, 0), ("04-07-2026", "POS/AMAZON", 900, 0),
            ("05-07-2026", "UPI/HPCL/PETROL", 2000, 0), ("06-07-2026", "UPI/APOLLO PHARMACY", 700, 0), ("07-07-2026", "NEFT/VIDYA MANDIR SCHOOL/TERM FEES", 9000, 0),
            ("08-07-2026", "UPI/SHARMA KIRANA/PETROL", 500, 0)]
    upload(cid, "p.csv", csv(rows, period="01-07-2026 to 31-07-2026"))
    by = {t["description"]: t["category"] for t in txns(cid)}
    assert by == {"UPI/BIGBASKET/GROCERY": "GROCERIES", "UPI/UBER/RIDE": "TRAVEL", "POS/AMAZON": "SHOPPING", "UPI/HPCL/PETROL": "FUEL",
                  "UPI/APOLLO PHARMACY": "MEDICAL", "NEFT/VIDYA MANDIR SCHOOL/TERM FEES": "EDUCATION", "UPI/SHARMA KIRANA/PETROL": "GENERAL_EXPENSE"}, by
    print("ok: spending is categorised by what it was for")

    # ---- currencies: no rate is ever invented, and amounts stay as printed beside the rupee figure
    usd_head = "Globe Bank - Account Statement\nAccount Number:,80050060075\nAccount Currency:,USD\nStatement Period:,01-07-2026 to 31-12-2026\nOpening Balance:,1000.00\n\nDate,Description,Debit,Credit,Balance\n"
    bal, lines = 1000, []
    for m in range(7, 13):
        bal += 3000
        lines.append(f"05-{m:02d}-2026,SWIFT/CLIENT NORTHSTAR INC/INV{m},,3000,{bal:.2f}")
        bal -= 2000
        lines.append(f"10-{m:02d}-2026,FX SALE TO INR ACCOUNT,2000,,{bal:.2f}")
    inr_rows = [(f"10-{m:02d}-2026", "FX PURCHASE FROM USD ACCOUNT", 0, 166500) for m in range(7, 13)]
    cid = new_case("currency")
    f = upload(cid, "usd.csv", (usd_head + "\n".join(lines)).encode())
    assert f["statements"][0]["n_breaks"] == 0 and f["statements"][0]["currency"] == "USD", f["statements"]
    a = analysis(cid)
    assert not a["metrics"], "with no rate there is no rupee figure to state"
    assert a["fx"]["no_rate_rows"] == 12 and "fx_rate_missing" in {x["code"] for x in a["flags"]}, a["fx"]
    assert all(t["fx_missing"] and t["orig_credit"] + t["orig_debit"] for t in txns(cid))
    assert c.post(f"/api/cases/{cid}/fx-rates", json={"currency": "USD", "rate": "83.5", "source": ""}).status_code == 422, "a rate needs a source"
    assert c.post(f"/api/cases/{cid}/fx-rates", json={"currency": "USD", "rate": "-1", "source": "bank advice"}).status_code == 422
    assert c.post(f"/api/cases/{cid}/fx-rates", json={"currency": "USD", "rate": "83.50", "source": "bank advice"}).status_code == 200
    a = analysis(cid)
    assert a["metrics"]["estimated_income"]["value"] == 250_500_00, a["metrics"]["estimated_income"]["value"]
    t0 = next(t for t in txns(cid) if t["credit"])
    assert t0["orig_credit"] == 3000_00 and t0["credit"] == 250_500_00 and t0["currency"] == "USD" and t0["fx_rate"] == "83.50", t0
    assert "foreign_currency_income" in {x["code"] for x in a["flags"]} and not a["fx"]["no_rate_rows"]
    # a month's own rate wins, and the transfer into the rupee account is matched across currencies
    assert c.post(f"/api/cases/{cid}/fx-rates", json={"currency": "USD", "month": "2026-12", "rate": "85", "source": "bank advice"}).status_code == 200
    assert next(t for t in txns(cid) if t["credit"] and t["txn_date"].startswith("2026-12"))["credit"] == 255_000_00
    upload(cid, "inr.csv", csv(inr_rows))
    fx_rows = [t for t in txns(cid) if "FX " in t["description"]]
    assert fx_rows and all(t["category"] == "INTERNAL_TRANSFER" and t["status"] == "auto" for t in fx_rows if not t["txn_date"].startswith("2026-12")), [(t["description"], t["category"], t["status"]) for t in fx_rows][:4]
    a = analysis(cid)
    assert a["metrics"]["estimated_income"]["stats"]["median"] == 250_500_00, a["metrics"]["estimated_income"]["stats"]
    # symbols in cells and a currency column: one account per currency, each with its own balance chain
    cid = new_case("wallet")
    f = upload(cid, "w.csv", ("Account Number:,90060070097\n\nDate,Description,Currency,Debit,Credit,Balance\n"
                              "01-07-2026,NEFT/CLIENT A,USD,,100.00,100.00\n01-07-2026,BACS/CLIENT B,GBP,,50.00,50.00\n02-07-2026,UPI/SHOP,USD,40.00,,60.00\n").encode())
    assert sorted(s["currency"] for s in f["statements"]) == ["GBP", "USD"] and all(s["n_breaks"] == 0 for s in f["statements"]), f["statements"]
    cid = new_case("symbols")
    f = upload(cid, "s.csv", ("Account Number:,90060070098\n\nDate,Description,Debit,Credit,Balance\n01-07-2026,WIRE/CLIENT,,$1,200.50,\"$1,200.50\"\n".replace("$1,200.50,", "\"$1,200.50\",")).encode())
    assert f["statements"][0]["currency"] == "USD" and f["statements"][0]["total_credits"] == 1200_50, f["statements"]
    assert analysis(cid)["accounts"][0]["currency_source"] == "stated"
    # an unlabelled account is assumed rupees, and says so
    cid = new_case("plain")
    upload(cid, "p.csv", b"Account Number:,90060070099\n\nDate,Description,Debit,Credit,Balance\n01-07-2026,UPI/SHOP,10,,90.00\n")
    assert analysis(cid)["accounts"][0]["currency"] == "INR" and analysis(cid)["accounts"][0]["currency_source"] == "assumed"
    print("ok: currencies are kept as printed, converted only at supplied rates, and rows without a rate are left out and reported")

    # ---- Level 2 assessment
    cid = new_case("assessment")
    rows = [(f"01-{m:02d}-2026", "NEFT/ABC TECHNOLOGIES SALARY", 0, 60000) for m in range(7, 13)]
    rows += [(f"15-{m:02d}-2026", f"NEFT/CLIENT XYZ/INV{m}", 0, a_) for m, a_ in zip(range(7, 13), (20000, 25000, 15000, 22000, 18000, 200000))]
    rows += [(f"10-{m:02d}-2026", "NACH/BAJAJ FINANCE LOAN EMI", 15000, 0) for m in range(7, 13)]
    rows += [(f"20-{m:02d}-2026", "UPI/DMART", s_, 0) for m, s_ in zip(range(7, 13), (5000, 20000, 40000, 60000, 80000, 95000))]
    upload(cid, "a.csv", csv(rows, opening=10000.0))
    a = analysis(cid)
    inc = a["metrics"]["estimated_income"]
    assert inc["stats"]["regular_monthly"] == 60000_00 and inc["stats"]["variable_monthly"] == 21000_00, inc["stats"]
    assert [u["month"] for u in inc["unusual_months"] if u["kind"] == "spike"] == ["2026-12"], inc["unusual_months"]
    assert inc["monthly"][0]["regular"] == 60000_00 and inc["monthly"][0]["variable"] == 20000_00
    aff = a["metrics"]["affordability"]
    assert aff["value"] == inc["value"] - 15000_00 and all(x["result"] == "within" for x in aff["checks"]), aff["checks"]
    assert a["metrics"]["recurring_obligations"]["items"][0]["why"], "each obligation says why"
    trend = analysis(cid)["metrics"]["cash_flow_trend"]
    assert trend["assessment"] in ("improving", "stable", "deteriorating") and trend["stats"]
    flags = {f["code"]: f for f in a["flags"]}
    assert all(f["rule"] for f in a["flags"] if f["category"] == "risk"), [f["code"] for f in a["flags"] if not f["rule"]]
    assert flags["income_unusual_month"]["txn_ids"] and flags["rising_expenses"]["txn_ids"], flags.keys()

    cid = new_case("worsening")
    rows = [(f"01-{m:02d}-2026", "NEFT/ABC TECHNOLOGIES SALARY", 0, 60000) for m in range(7, 13)]
    rows += [(f"20-{m:02d}-2026", "UPI/DMART", s_, 0) for m, s_ in zip(range(7, 13), (10000, 12000, 11000, 50000, 70000, 90000))]
    upload(cid, "w.csv", csv(rows, opening=500000.0))
    t = analysis(cid)["metrics"]["cash_flow_trend"]
    assert t["assessment"] == "deteriorating" and t["highlight_months"] and t["included"], t

    cid = new_case("volatile and unexplained")
    rows = [(f"08-{m:02d}-2026", f"NEFT/CLIENT XYZ/INV{m}", 0, a_) for m, a_ in zip(range(7, 13), (10000, 90000, 5000, 120000, 8000, 70000))]
    rows += [("21-09-2026", "NEFT/AB", 0, 300000)]
    upload(cid, "v.csv", csv(rows))
    codes = {f["code"] for f in analysis(cid)["flags"]}
    assert {"income_volatility", "large_unexplained_deposit"} <= codes, codes

    # consolidated and per-account views; a missing month on one account is flagged
    cid = new_case("two accounts")
    upload(cid, "a.csv", csv([(f"01-{m:02d}-2026", "NEFT/ABC TECHNOLOGIES SALARY", 0, 60000) for m in range(7, 13)]))
    upload(cid, "b.csv", csv([("05-08-2026", "UPI/SHOP", 100, 0)], period="01-08-2026 to 30-09-2026").replace(b"11112222333", b"99998888777"))
    a = analysis(cid)
    views = {v["acct_masked"][-4:]: v for v in a["account_view"]}
    assert len(views) == 2 and {"2026-07", "2026-10", "2026-11", "2026-12"} <= set(views["8777"]["missing_months"]), views["8777"]["missing_months"]
    assert "account_missing_months" in {f["code"] for f in a["flags"]}
    assert sum(r["credits"] for r in views["2333"]["months"]) == 3_60_000_00

    # the report ties it together: every section's evidence resolves to a file, page and row
    r = c.get(f"/api/cases/{cid}/report").json()
    assert "does not approve or decline" in r["notice"] and r["metrics"]["estimated_income"]["value"] == 60000_00
    for s in r["sections"]:
        for i in s["evidence"]:
            assert r["evidence"][i]["sources"] and r["evidence"][i]["sources"][0]["file"], (s["key"], i)
    assert all("fraud" not in f["detail"].lower() for f in r["flags"])
    print("ok: regular and variable income, unusual months, affordability rules, trend, account views, rule-backed indicators and the report")

    # ---- several regular payers are not salary, and one employer still is
    cid = new_case("three repeat clients")
    rows = [(f"{d:02d}-{m:02d}-2026", f"IMPS/{n}", 0, a_) for m in range(7, 13) for d, n, a_ in ((6, "RAHUL VERMA", 18000), (14, "PRIYA NAIR", 9000), (22, "AMIT DAS", 22000))]
    upload(cid, "c.csv", csv(rows))
    a = analysis(cid)
    assert a["profile"] == "SELF_EMPLOYED" and not [t for t in txns(cid) if t["category"] == "SALARY"], [(t["description"], t["category"]) for t in txns(cid)][:3]
    assert a["metrics"]["estimated_income"]["stats"]["provisional"] == 49000_00, a["metrics"]["estimated_income"]["stats"]
    cid = new_case("salary and a tenant")
    rows = [(f"01-{m:02d}-2026", "NEFT/ABC TECHNOLOGIES SALARY", 0, 60000) for m in range(7, 13)] + [(f"05-{m:02d}-2026", "UPI/RAVI KUMAR", 0, 15000) for m in range(7, 13)]
    upload(cid, "t.csv", csv(rows))
    a = analysis(cid)
    assert a["profile"] == "SALARIED" and a["metrics"]["estimated_income"]["value"] == 60000_00, a["metrics"]["estimated_income"]["value"]
    assert all(t["category"] != "SALARY" for t in txns(cid) if t["credit"] == 15000_00)
    cid = new_case("renamed employer")
    rows = [(f"01-{m:02d}-2026", "NEFT/ACME SOFTWARE PVT LTD/SALARY", 0, 90000) for m in (7, 8, 9)] + [(f"01-{m:02d}-2026", "NEFT/ACME TECH RAZORPAYX", 0, 90000) for m in (10, 11, 12)]
    upload(cid, "r.csv", csv(rows))
    a = analysis(cid)
    assert a["profile"] == "SALARIED" and all(t["category"] == "SALARY" for t in txns(cid)), [(t["description"], t["category"]) for t in txns(cid)]
    cid = new_case("many salary payers")
    rows = [(f"{5 + i:02d}-{m:02d}-2026", f"IMPS SALARY TRF BY-COMPANY {chr(65 + i + (m - 7) * 4)} LTD", 0, 5000) for m in range(7, 13) for i in range(4)]
    upload(cid, "m.csv", csv(rows))
    a = analysis(cid)
    assert "several_salary_payers" in {f["code"] for f in a["flags"]} and a["metrics"]["estimated_income"]["value"] == 0, ({f["code"] for f in a["flags"]}, a["metrics"]["estimated_income"]["value"])
    print("ok: salary needs one payer at a time; a renamed employer and a real salary beside a tenant still read correctly")

    # ---- a business's income is receipts less identified costs, charged once
    cid = new_case("trader")
    rows = [(f"08-{m:02d}-2026", f"NEFT/CLIENT ACME TRADERS/INV{m}", 0, 500000) for m in range(7, 13)] + [(f"10-{m:02d}-2026", f"RTGS/STEEL SUPPLIERS/PO{m}", 380000, 0) for m in range(7, 13)]
    rows += [(f"11-{m:02d}-2026", "NEFT/STAFF WAGES", 20000, 0) for m in range(7, 13)] + [(f"12-{m:02d}-2026", "GST PAYMENT/CBIC", 10000, 0) for m in range(7, 13)]
    rows += [(f"05-{m:02d}-2026", "IMPS/LANDLORD/SHOP RENT", 30000, 0) for m in range(7, 13)] + [(f"20-{m:02d}-2026", "UPI/BIGBASKET/GROCERY", 5000, 0) for m in range(7, 13)]
    upload(cid, "b.csv", csv(rows, opening=500000.0))
    a = analysis(cid)
    inc, m_ = a["metrics"]["estimated_income"], a["metrics"]
    assert inc["profile"] == "SELF_EMPLOYED" and inc["value"] == 90000_00, (inc["value"], inc["stats"])
    assert inc["stats"]["gross_receipts_monthly"] == 500000_00 and inc["stats"]["business_costs_monthly"] == 410000_00, inc["stats"]
    assert inc["monthly"][0]["gross"] == 500000_00 and inc["monthly"][0]["costs"] == 410000_00 and inc["monthly"][0]["value"] == 90000_00
    assert m_["recurring_obligations"]["value"] == 30000_00 and m_["foir"]["value"] == round(30000 / 90000, 3), (m_["recurring_obligations"]["value"], m_["foir"]["value"])
    assert m_["monthly_surplus"]["outflows"] == 35000_00 and m_["monthly_surplus"]["value"] == 55000_00, (m_["monthly_surplus"]["outflows"], m_["monthly_surplus"]["value"])
    assert any("identified suppliers" in n for n in inc["notes"]) and a["monthly"][0]["income"] == 90000_00
    assert all(c_ in inc["included"] for c_ in [t["txn_id"] for t in txns(cid) if t["category"] == "SUPPLIER_PAYMENT"]), "costs are part of the breakdown"
    # costs above receipts read as zero with a note, never as negative income; a salaried estimate takes nothing off
    cid = new_case("costs above receipts")
    upload(cid, "x.csv", csv([(f"08-{m:02d}-2026", f"NEFT/CLIENT ABC/INV{m}", 0, 100000) for m in range(7, 13)] + [(f"10-{m:02d}-2026", f"RTGS/STEEL SUPPLIERS/PO{m}", 150000, 0) for m in range(7, 13)], opening=500000.0))
    inc = analysis(cid)["metrics"]["estimated_income"]
    assert inc["value"] == 0 and any("exceed" in n for n in inc["notes"]), (inc["value"], inc["notes"])
    cid = new_case("salaried with a supplier row")
    upload(cid, "y.csv", csv([(f"01-{m:02d}-2026", "NEFT/ABC TECHNOLOGIES SALARY", 0, 60000) for m in range(7, 13)] + [("10-08-2026", "RTGS/STEEL SUPPLIERS/PO1", 9000, 0)]))
    assert analysis(cid)["metrics"]["estimated_income"]["value"] == 60000_00
    print("ok: business income is receipts less identified costs, charged once; never negative; salaried income is untouched")

    # ---- the screen asks for analysis and transactions at once: a new case and a rules update must both survive that
    from concurrent.futures import ThreadPoolExecutor
    cid = new_case("asked at once")
    with ThreadPoolExecutor(8) as pool:
        got = list(pool.map(lambda p: c.get(p).status_code, [f"/api/cases/{cid}/analysis", f"/api/cases/{cid}/transactions"] * 4))
    assert got == [200] * 8, got
    # two empty cases have the same contents and must still have their own snapshots
    assert c.get(f"/api/cases/{new_case('empty one')}/analysis").status_code == 200
    assert c.get(f"/api/cases/{new_case('empty two')}/analysis").status_code == 200
    upload(cid, "a.csv", csv(salary))
    import app.config as app_config
    was = app_config.RULES_VERSION
    app_config.RULES_VERSION = "r-test"
    try:
        with ThreadPoolExecutor(8) as pool:
            got = list(pool.map(lambda p: c.get(p).status_code, [f"/api/cases/{cid}/analysis", f"/api/cases/{cid}/transactions", f"/api/cases/{cid}/report"] * 3))
        assert got == [200] * 9, got
        assert analysis(cid)["versions"]["rules"] == "r-test", "new rules reach an existing case without a reset"
    finally:
        app_config.RULES_VERSION = was
    print("ok: simultaneous requests do not collide, on a new case or after a rules update")

    # ---- rule engines: a lender sets its own, limited to Level 1 parameters
    senior = {"X-Role": "senior", "X-User": "senior.demo"}
    listing = c.get("/api/rule-engines").json()
    assert {e["engine_id"] for e in listing["engines"]} >= {"standard", "sample_conservative", "sample_sme"}
    assert {p["group"] for p in listing["schema"]} == {"Reconciliation", "Classification", "Estimated income", "Recurring obligations"}, {p["group"] for p in listing["schema"]}
    assert set(listing["score_parameters"]) == {"estimated_income", "recurring_obligations", "net_cash_flow", "monthly_credits", "monthly_debits", "balance_trend", "reconciliation"}
    assert c.post("/api/rule-engines", json={"name": "Analyst Bank"}).status_code == 403, "an analyst cannot change engines"
    made = c.post("/api/rule-engines", json={"name": "Acme Bank", "based_on": "standard"}, headers=senior).json()
    eid = made["engine_id"]
    assert made["version"] == 1 and c.post("/api/rule-engines", json={"name": "acme bank"}, headers=senior).status_code == 422, "names are unique"
    assert c.put("/api/rule-engines/standard", json={"config": made["config"]}, headers=senior).status_code == 422, "Standard stays the reference"
    bad = dict(made["config"], params={**made["config"]["params"], "low_balance_paise": 1})
    assert c.put(f"/api/rule-engines/{eid}", json={"config": bad}, headers=senior).status_code == 422, "only Level 1 parameters can be set"
    bad = dict(made["config"], custom_rules=[{"keyword": "XYZ", "direction": "C", "category": "INSURANCE", "weight": 3}])
    assert c.put(f"/api/rule-engines/{eid}", json={"config": bad}, headers=senior).status_code == 422, "keyword rules point at Level 1 categories"
    bad = dict(made["config"], params={**made["config"]["params"], "auto_min_p": 1.5})
    assert c.put(f"/api/rule-engines/{eid}", json={"config": bad}, headers=senior).status_code == 422

    # the engine changes what happens: a keyword rule, income method, months, reconciliation and the score
    cid = new_case("lender rules")
    rows = [(f"01-{m:02d}-2026", "NEFT/ZORBA CO/TANKHA", 0, 60000) for m in range(7, 13)] + [("15-12-2026", "NEFT/ZORBA CO/TANKHA", 0, 60000)]
    rows += [(f"10-{m:02d}-2026", "NACH/BAJAJ FINANCE LOAN EMI", 12000, 0) for m in range(7, 13)]
    upload(cid, "l.csv", csv(rows, opening=5000.0))
    std = analysis(cid)
    assert std["engine"]["id"] == "standard" and std["credit_score"]["engine"] == "Standard", std["engine"]
    cfg = made["config"]
    cfg["custom_rules"] = [{"keyword": "TANKHA", "direction": "C", "category": "SALARY", "weight": 4}]
    cfg["params"].update(income_method="average", income_max_months=3, recon_require_closing=True)
    cfg["score"]["parameters"]["estimated_income"].update(full_at=20_00_000_00)
    saved = c.put(f"/api/rule-engines/{eid}", json={"config": cfg, "note": "Our wording"}, headers=senior).json()
    assert saved["version"] == 2
    assert [h["version"] for h in c.get(f"/api/rule-engines/{eid}").json()["history"]] == [2, 1]
    assert c.post(f"/api/cases/{cid}/engine", json={"engine_id": eid}).status_code == 200
    mine = analysis(cid)
    assert mine["engine"] == {"id": eid, "name": "Acme Bank", "version": 2} and mine["versions"]["engine"] == f"{eid}:v2", mine["versions"]
    d = next(t for t in txns(cid) if t["credit"])
    detail = c.get(f"/api/cases/{cid}/transactions/{d['txn_id']}").json()
    assert d["category"] == "SALARY" and any(x["signal"] == "rule:custom" and "TANKHA" in x["detail"] for x in detail["classification"]["contributions"]), detail["classification"]["contributions"]
    inc = mine["metrics"]["estimated_income"]
    assert inc["method"].startswith("average of 3") and len(inc["months_used"]) == 3, inc["method"]
    assert mine["reconciliation"]["statements"][0]["result"] == "outside" and "closing" in mine["reconciliation"]["statements"][0]["reasons"][0], mine["reconciliation"]["statements"][0]
    assert mine["credit_score"]["engine"] == "Acme Bank" and mine["credit_score"]["score"] != std["credit_score"]["score"], (mine["credit_score"]["score"], std["credit_score"]["score"])
    cmp_ = c.get(f"/api/cases/{cid}/engine-comparison").json()
    assert {x["engine_id"] for x in cmp_} >= {"standard", eid} and next(x for x in cmp_ if x["engine_id"] == eid)["current"]
    assert c.get(f"/api/cases/{cid}/analysis").json()["engine"]["id"] == eid, "comparing changes nothing"
    # editing an engine reaches the cases using it; deleting one in use is refused
    cfg["params"]["income_max_months"] = 6
    c.put(f"/api/rule-engines/{eid}", json={"config": cfg}, headers=senior).raise_for_status()
    assert analysis(cid)["versions"]["engine"] == f"{eid}:v3" and len(analysis(cid)["metrics"]["estimated_income"]["months_used"]) == 6
    assert c.delete(f"/api/rule-engines/{eid}", headers=senior).status_code == 422
    c.post(f"/api/cases/{cid}/engine", json={"engine_id": "standard"}).raise_for_status()
    assert c.delete(f"/api/rule-engines/{eid}", headers=senior).status_code == 200
    # an engine asking for more months than exist gives no income figure, even for a business with costs, and says why
    cid = new_case("too few months")
    upload(cid, "f.csv", csv([(f"08-{m:02d}-2026", f"NEFT/CLIENT ABC/INV{m}", 0, 100000) for m in range(7, 10)] + [(f"10-{m:02d}-2026", f"RTGS/STEEL SUPPLIERS/PO{m}", 40000, 0) for m in range(7, 10)]
                               + [("12-08-2026", "CASH DEPOSIT BRANCH", 0, 50000)], period="01-07-2026 to 30-09-2026", opening=100000.0))
    got = c.get(f"/api/cases/{cid}/engine-comparison")
    assert got.status_code == 200, got.status_code
    assert next(x for x in got.json() if x["engine_id"] == "sample_conservative")["income"] is None, got.json()
    c.post(f"/api/cases/{cid}/engine", json={"engine_id": "sample_conservative"}).raise_for_status()
    a = analysis(cid)
    assert a["metrics"]["estimated_income"]["value"] is None and any("at least 6" in n for n in a["metrics"]["estimated_income"]["notes"]) and a["credit_score"]["score"] is None, a["credit_score"]["reason"]
    print("ok: lenders set up their own engines, limited to Level 1; versions, keyword rules, comparison and audit work")

    # ---- reconciliation setup: a small difference can be allowed
    cid = new_case("reconciliation setup")
    head = "Bank X\nAccount Number:,55556666777\nOpening Balance:,1000.00\nClosing Balance:,1205.00\nStatement Period:,01-07-2026 to 31-07-2026\n\nDate,Description,Debit,Credit,Balance\n"
    upload(cid, "r.csv", (head + "02-07-2026,NEFT/CLIENT A/INV1,,200,1200.00\n").encode())
    a = analysis(cid)
    st = a["reconciliation"]["statements"][0]
    assert st["difference"] == 5_00 and st["result"] == "outside" and st["computed_closing"] == 1200_00 and st["stated_closing"] == 1205_00, st
    assert "reconciliation" in {f["code"] for f in a["flags"]}
    loose = c.post("/api/rule-engines", json={"name": "Loose Bank"}, headers=senior).json()
    cfg = loose["config"]
    cfg["params"]["recon_tolerance_paise"] = 10_00
    c.put(f"/api/rule-engines/{loose['engine_id']}", json={"config": cfg}, headers=senior).raise_for_status()
    c.post(f"/api/cases/{cid}/engine", json={"engine_id": loose["engine_id"]}).raise_for_status()
    a = analysis(cid)
    assert a["reconciliation"]["statements"][0]["result"] == "within" and "reconciliation" not in {f["code"] for f in a["flags"]}, a["reconciliation"]["statements"][0]
    print("ok: reconciliation follows the lender's rules, and shows the arithmetic")

    # ---- the score: built only from Level 1 parameters, every point shown, never invented
    cid = new_case("score")
    rows = [(f"01-{m:02d}-2026", "NEFT/ABC TECHNOLOGIES SALARY", 0, 100000) for m in range(7, 13)] + [(f"10-{m:02d}-2026", "NACH/BAJAJ FINANCE LOAN EMI", 15000, 0) for m in range(7, 13)]
    rows += [(f"12-{m:02d}-2026", "UPI/BIGBASKET/GROCERY", 20000, 0) for m in range(7, 13)]
    upload(cid, "s.csv", csv(rows, opening=50000.0))
    s = analysis(cid)["credit_score"]
    assert s["score"] is not None and s["band"] and s["assessed_weight"] == 100, s
    pts = {p["key"]: p for p in s["parameters"]}
    assert pts["estimated_income"]["points"] == 25 and pts["recurring_obligations"]["points"] == 20, pts["estimated_income"]
    assert s["score"] == round(100 * sum(p["points"] for p in s["parameters"]) / sum(p["weight"] for p in s["parameters"] if p["assessed"]))
    assert "Not a bureau score" in s["notice"] and "not a credit decision" in s["notice"]
    weak = new_case("weak score")
    rows = [(f"01-{m:02d}-2026", "NEFT/ABC TECHNOLOGIES SALARY", 0, 30000) for m in range(7, 13)] + [(f"10-{m:02d}-2026", "NACH/BAJAJ FINANCE LOAN EMI", 25000, 0) for m in range(7, 13)]
    rows += [(f"12-{m:02d}-2026", "UPI/BIGBASKET/GROCERY", 15000, 0) for m in range(7, 13)]
    upload(weak, "w.csv", csv(rows, opening=200000.0))
    assert analysis(weak)["credit_score"]["score"] < s["score"], analysis(weak)["credit_score"]["score"]
    # with no confirmed income there is no score, and it says why
    unknown = new_case("no income yet")
    upload(unknown, "u.csv", csv([(f"05-{m:02d}-2026", "NEFT/ACME TRADERS/INV", 0, 50000) for m in range(7, 13)]))
    un = analysis(unknown)["credit_score"]
    assert un["score"] is None and "No confirmed income" in un["reason"], un
    print("ok: the score is built from Level 1 parameters with every point shown, and is withheld when income is unknown")

    # ---- account history
    cid = new_case("history")
    rows = [(f"01-{m:02d}-2026", "NEFT/ABC TECHNOLOGIES SALARY", 0, 60000) for m in range(7, 13)] + [(f"10-{m:02d}-2026", "NACH/BAJAJ FINANCE LOAN EMI", 15000, 0) for m in range(7, 13)]
    rows += [("11-08-2026", "NACH RTN BAJAJ FINANCE EMI INSUFFICIENT FUNDS", 0, 15000), ("20-09-2026", "CASH DEPOSIT BRANCH", 0, 300000), ("05-10-2026", "NEFT/BAJAJ FINANCE LOAN DISBURSAL", 0, 200000)]
    upload(cid, "h.csv", csv(rows, opening=50000.0))
    v = analysis(cid)["account_view"][0]
    titles = " | ".join(e["title"] for e in v["events"])
    for want in ("Statement period", "First entry", "Latest entry", "First salary credit", "Loan money received", "Payment returned unpaid", "Large cash deposit", "Largest credit", "Largest debit", "Loan EMI begins"):
        assert want in titles, (want, titles)
    assert v["events"] == sorted(v["events"], key=lambda e: (str(e["date"]), e["kind"])) and v["summary"]["returned_payments"] == 1 and v["summary"]["n_txns"] == len(txns(cid))
    assert v["summary"]["total_credits"] == sum(t["credit"] for t in txns(cid)) and v["top_in"] and v["top_out"] and v["by_category"]
    print("ok: each account has a history of what it shows")

    # ---- a salaried customer is asked for the statements needed to track internal transfers
    cid = new_case("salaried, one account")
    rows = [(f"01-{m:02d}-2026", "NEFT/ABC TECHNOLOGIES SALARY", 0, 80000) for m in range(7, 13)]
    upload(cid, "a.csv", csv(rows))
    a = analysis(cid)
    assert a["profile"] == "SALARIED" and "other accounts" in a["statement_requests"]["prompt"] and not a["statement_requests"]["items"], a["statement_requests"]
    rows += [(f"03-{m:02d}-2026", "NEFT/ASHA RAO/XXXXXXXX9981/HDFC0001234", 30000, 0) for m in range(7, 13)]
    rows += [(f"06-{m:02d}-2026", "IMPS/VIKRAM NAIR", 20000, 0) for m in range(7, 13)]
    cid = new_case("salaried, transfers out")
    upload(cid, "b.csv", csv(rows))
    a = analysis(cid)
    req = {g["key"]: g for g in a["statement_requests"]["items"]}
    assert "acct:9981" in req and req["acct:9981"]["count"] == 6 and req["acct:9981"]["amount"] == 6 * 30000_00, a["statement_requests"]
    assert any(g["kind"] == "regular" and "VIKRAM" in g["label"] for g in req.values()), req.keys()
    assert "request_statement" in {f["code"] for f in a["flags"]}
    # once the other account's statement arrives, the transfers pair up and the request goes away
    other = CSV_HEAD.format(period="01-07-2026 to 31-12-2026", opening=1000.0).replace("11112222333", "77778888999")
    bal, lines = 1000.0, []
    for m in range(7, 13):
        bal += 30000
        lines.append(f"03-{m:02d}-2026,NEFT/ASHA RAO/SAVINGS,,30000,{bal:.2f}")
    upload(cid, "other.csv", (other + "\n".join(lines)).encode())
    a = analysis(cid)
    assert a["statement_requests"]["matched_pairs"] == 6 and not any(g["key"].startswith("acct:") for g in a["statement_requests"]["items"]), a["statement_requests"]
    assert a["metrics"]["estimated_income"]["value"] == 80000_00
    print("ok: salaried customers are asked for the statements needed to track transfers, and the request clears when they arrive")

    # ---- a self-employed customer's earnings are tracked
    cid = new_case("earnings")
    amounts = (100000, 200000, 150000, 50000, 180000, 120000)
    rows = [(f"08-{m:02d}-2026", f"NEFT/CLIENT ALPHA/INV{m}", 0, a_ * 3 // 4) for m, a_ in zip(range(7, 13), amounts)] + [(f"18-{m:02d}-2026", f"NEFT/CLIENT BETA/INV{m}", 0, a_ // 4) for m, a_ in zip(range(7, 13), amounts)]
    rows += [(f"10-{m:02d}-2026", f"RTGS/STEEL SUPPLIERS/PO{m}", 30000, 0) for m in range(7, 13)]
    upload(cid, "e.csv", csv(rows, opening=100000.0))
    a = analysis(cid)
    e = a["earnings"]
    assert e["total_receipts"] == sum(amounts) * 100 and e["total_costs"] == 6 * 30000_00 and e["total_earned"] == (sum(amounts) - 180000) * 100, e
    assert e["monthly"][1]["earned"] == 170000_00 and e["monthly"][-1]["cumulative"] == e["total_earned"] and e["best"]["month"] == "2026-08" and e["worst"]["month"] == "2026-10"
    assert e["annualised"] == e["total_earned"] * 2 and e["n_payers"] == 2 and e["payers"][0]["name"] == "CLIENT ALPHA" and e["payers"][0]["share"] == 0.75 and e["payers"][0]["months"] == 6, e["payers"][0]
    assert e["concentrated"] and "customer_concentration" in {f["code"] for f in a["flags"]}
    print("ok: a self-employed customer's earnings are tracked by month and by payer")

    # ---- logging: PII scrubbed and uvicorn's access formatter still works
    from uvicorn.logging import AccessFormatter
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("127.0.0.1:1", "GET", "/api/x?phone=9876543210", "1.1", 200), None)
    assert PiiScrubber().filter(rec)
    out = AccessFormatter(fmt='%(client_addr)s "%(request_line)s" %(status_code)s', use_colors=False).format(rec)
    assert "9876543210" not in out and "200" in out, out
    print("ok: log scrubber keeps uvicorn's record shape and still masks")

print("ALL REGRESSION CHECKS PASSED")
