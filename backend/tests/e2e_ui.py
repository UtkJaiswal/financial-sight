"""End-to-end through the real UI in a real browser, on its own server and its own throwaway data. Port 8002.

Needs playwright and Google Chrome (pip install playwright). Build the UI first: cd frontend && npm run build

  python backend/tests/e2e_ui.py [screenshot_dir]
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request as u
from pathlib import Path

from playwright.sync_api import sync_playwright
from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parents[2]
PORT = 8002
BASE = f"http://127.0.0.1:{PORT}"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="finsight_e2e_shots_"))
OUT.mkdir(parents=True, exist_ok=True)
DATA = tempfile.mkdtemp(prefix="finsight_e2e_")
LOG = open(OUT / "server.log", "w")
results = []


def ok(label, cond, detail=""):
    results.append((bool(cond), label, str(detail)[:220]))
    print(("PASS " if cond else "FAIL ") + label + (f"  [{str(detail)[:220]}]" if detail and not cond else ""), flush=True)


def get(path):
    return json.load(u.urlopen(BASE + path, timeout=60))


def start():
    env = dict(os.environ, FINSIGHT_DATA_DIR=DATA, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", str(ROOT / "backend"), "--host", "127.0.0.1", "--port", str(PORT)],
                         env=env, cwd=DATA, stdout=LOG, stderr=subprocess.STDOUT)
    for _ in range(120):
        try:
            u.urlopen(BASE + "/api/health", timeout=2)
            return p
        except Exception:
            time.sleep(0.5)
    raise SystemExit("server did not start")


server = start()
S = ROOT / "sample_data"
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        pg = ctx.new_page()
        console, page_errors = [], []
        pg.on("console", lambda m: console.append(m.text[:160]) if m.type == "error" else None)
        pg.on("pageerror", lambda e: page_errors.append(str(e)[:200]))
        server_errors = []
        pg.on("response", lambda r: server_errors.append(f"{r.status} {r.request.method} {r.url.replace(BASE, '')}") if r.status >= 500 else None)

        def wait(ms=900):
            pg.wait_for_timeout(ms)

        def tab(name):
            pg.locator("nav.tabs .tab", has_text=name).first.click()
            wait(1100)

        def open_case(name):
            pg.locator(".case-item", has_text=name).first.click()
            wait(2000)

        def case_id(name):
            return next(c["case_id"] for c in get("/api/cases") if name in c["name"])

        def close_drawer():
            pg.keyboard.press("Escape")
            wait(400)

        def body():
            return pg.locator("body").inner_text()

        def toast_text(contains, timeout=30000):
            """Older pop-ups may still be showing, so wait for the one that says this."""
            try:
                t = pg.locator(".toast", has_text=contains).last
                t.wait_for(timeout=timeout)
                return t.inner_text()
            except Exception:
                return " / ".join(pg.locator(".toast").all_inner_texts())

        def upload(paths):
            pg.locator("input[type=file]").set_input_files(paths)
            wait(600)
            pg.wait_for_function("!document.body.innerText.includes('Processing ')", timeout=60000)
            wait(900)

        # ---------------------------------------------------------------- start, demo data
        pg.goto(BASE)
        wait(1500)
        ok("empty install opens without errors", not page_errors, page_errors)
        pg.locator(".side-btn", has_text="Load sample").click()
        pg.wait_for_function("document.querySelectorAll('.case-item').length >= 4", timeout=120000)
        wait(1500)
        ok("four demo cases load", pg.locator(".case-item").count() == 4, pg.locator(".case-item").count())

        # ---------------------------------------------------------------- overview and calculation breakdown
        open_case("Rohan")
        rid = case_id("Rohan")
        a = get(f"/api/cases/{rid}/analysis")
        ok("income on screen matches the API", "₹94.3k" in pg.locator(".kpi").first.inner_text() and a["metrics"]["estimated_income"]["value"] == 94350_00, pg.locator(".kpi").first.inner_text()[:60])
        pg.locator(".kpi").first.click()
        wait()
        d = pg.locator(".drawer").inner_text()
        ok("income breakdown shows method, included and excluded", all(x in d for x in ("Method", "Included", "Excluded, with reasons")), d[:120])
        close_drawer()
        ok("monthly breakdown table is shown", pg.locator("h3:has-text('Monthly breakdown')").count() == 1)
        card = pg.locator(".card", has=pg.locator("h3:has-text('Monthly breakdown')"))
        card.locator("tbody tr button").first.click()
        wait()
        ok("a monthly amount opens its transactions", "Transactions" in pg.locator("nav.tabs .tab.active").inner_text() and pg.locator(".tbl tbody tr").count() > 0)

        # ---------------------------------------------------------------- extracted transactions, source and reason
        tab("Transactions")
        pg.wait_for_timeout(300)
        heads = " ".join(pg.locator(".tbl thead th").all_inner_texts()).lower()
        ok("transactions show date, narration, debit, credit, balance", all(x in heads for x in ("date", "narration", "debit", "credit", "balance")), heads)
        txns = get(f"/api/cases/{rid}/transactions")
        last_sal = [t for t in txns if t["category"] == "SALARY"][-1]
        search = pg.locator("input[placeholder*='Search']")
        if search.count():
            search.fill("")
        clear = pg.locator("button:has-text('Clear')")
        if clear.count():
            clear.first.click()
        search.fill("RAZORPAYX")
        wait()
        pg.locator(".tbl tbody tr").last.click()
        wait(1100)
        det = get(f"/api/cases/{rid}/transactions/{last_sal['txn_id']}")
        d = pg.locator(".drawer").inner_text()
        ok("a row shows its source file and page", det["sources"][0]["filename"] in d, d[:200])
        ok("a row shows why it got its category", "evidence" in d.lower() or det["classification"]["explanation"] in d, d[:200])

        # ---------------------------------------------------------------- correct a classification, by clicking
        before_income = a["metrics"]["estimated_income"]["value"]
        pg.locator(".drawer select[aria-label='New category']").select_option("INTERNAL_TRANSFER")
        pg.locator(".drawer textarea").fill("Own account transfer per customer")
        pg.locator(".drawer button:has-text('Apply correction')").click()
        t = toast_text("recalculated")
        ok("correction shows recalculated figures", "recalculated" in t and "→" in t, t[:200])
        wait(1000)
        d = pg.locator(".drawer").inner_text()
        ok("the original classification stays visible", "Corrected by" in d and "Salary" in d, d[:200])
        det = get(f"/api/cases/{rid}/transactions/{last_sal['txn_id']}")
        ok("the original is preserved in the data", det["txn"]["category"] == "INTERNAL_TRANSFER" and det["txn"]["model_category"] == "SALARY" and det["overrides"])
        after = get(f"/api/cases/{rid}/analysis")["metrics"]["estimated_income"]
        ok("income recalculated, not zero", 0 < after["value"] < before_income, after["value"])
        ok("the corrected row is listed as excluded", any(e["txn_id"] == last_sal["txn_id"] for e in after["excluded"]))
        close_drawer()

        # ---------------------------------------------------------------- reveal, review queue
        search.fill("RAZORPAYX")
        wait()
        pg.locator(".tbl tbody tr").first.click()
        wait(1000)
        pg.locator(".drawer button:has-text('Reveal')").click()
        wait(300)
        pg.locator(".drawer input").first.fill("Verify employer name")
        pg.locator(".drawer button:has-text('Show')").click()
        ok("reveal requires a reason and says it is logged", "audit log" in toast_text("audit log"))
        close_drawer()
        open_case("Neha")
        tab("Review queue")
        before = pg.locator(".tbl tbody tr").count()
        row = pg.locator(".tbl tbody tr").first
        if not row.locator("select").input_value():
            row.locator("select").select_option(index=1)
        row.locator("button:has-text('Confirm')").click()
        toast_text("recalculated")
        wait(1500)
        ok("confirming from the queue removes the row", pg.locator(".tbl tbody tr").count() == before - 1, f"{before} -> {pg.locator('.tbl tbody tr').count()}")

        # ---------------------------------------------------------------- roles
        pg.locator("#role").select_option("auditor")
        wait(700)
        tab("Statements")
        ok("auditor cannot upload", pg.locator("button:has-text('Choose files')").count() == 0)
        tab("Transactions")
        pg.locator(".tbl tbody tr").first.click()
        wait(1000)
        ok("auditor cannot correct", "view but not correct" in pg.locator(".drawer").inner_text())
        close_drawer()
        tab("Audit log")
        wait(800)
        ok("auditor sees a verified audit chain", "Hash chain verified" in body(), body()[:100])
        pg.locator("#role").select_option("analyst")
        wait(1000)
        tab("Overview")
        tab("Audit log")
        wait(800)
        ok("analyst cannot read the audit log", "restricted" in body())

        # ---------------------------------------------------------------- new case, every format, unreadable files
        pg.locator(".side-btn.primary").click()
        wait(400)
        pg.locator("#nc-name").fill("E2E Applicant")
        pg.locator("button:has-text('Create and upload')").click()
        wait(1500)
        eid = case_id("E2E Applicant")
        files = sorted(str(p) for p in (S / "case_salaried").glob("*.pdf") if " (" not in p.name) + sorted(str(p) for p in (S / "case_sme").iterdir() if " (" not in p.name)
        upload(files)
        rep = get(f"/api/cases/{eid}/files")
        kinds = {f["kind"] for f in rep["files"]}
        ok("PDF, CSV, Excel and JSON all process", {"pdf", "csv", "xlsx", "json"} <= kinds and all(f["status"] in ("processed", "partial") for f in rep["files"]), [(f["filename"], f["status"]) for f in rep["files"]])
        ok("two PDF layouts recognised", len({s["layout"] for f in rep["files"] if f["kind"] == "pdf" for s in f["statements"]}) >= 2)
        st = rep["files"][0]["statements"][0]
        ok("account, bank and period extracted", st["acct_masked"] and st["bank_name"] and st["period_start"] and st["period_end"])
        ok("reconciliation shown on screen", any(x in body() for x in ("Reconciled", "No closing balance stated", "Does not reconcile")))
        upload([str(p) for p in sorted((S / "problem_files").iterdir())])
        rep = get(f"/api/cases/{eid}/files")
        bad = {f["filename"]: f for f in rep["files"] if f["status"] == "failed"}
        ok("corrupted and scanned files are reported, not skipped", {"corrupted_statement.pdf", "scanned_statement.pdf"} <= set(bad), list(bad))
        ok("the reason appears on screen", "corrupted_statement.pdf" in body() and "could not be" in body().lower(), "")
        codes = {f["code"] for f in get(f"/api/cases/{eid}/analysis")["flags"]}
        ok("unreadable content raises an indicator", {"file_not_processed", "page_not_processed"} <= codes, codes)
        n = get(f"/api/cases/{eid}/analysis")["n_transactions"]
        upload([files[0]])
        ev = get(f"/api/cases/{eid}/files")["upload_events"]
        ok("re-uploading a file adds nothing", get(f"/api/cases/{eid}/analysis")["n_transactions"] == n and any(e["duplicate"] for e in ev))
        junk = Path(DATA) / "notes.txt"
        junk.write_text("hello")
        pg.locator("input[type=file]").set_input_files(str(junk))
        ok("unsupported file gets a clear message", "not supported" in toast_text("not supported"), "")
        wait(800)

        # ---------------------------------------------------------------- locked PDF, by clicking
        w = PdfWriter(clone_from=PdfReader(files[0]))
        w.encrypt("secret123")
        buf = io.BytesIO()
        w.write(buf)
        locked = Path(DATA) / "locked_statement.pdf"
        locked.write_bytes(buf.getvalue())
        pg.locator(".side-btn.primary").click()
        wait(400)
        pg.locator("#nc-name").fill("E2E Locked")
        pg.locator("button:has-text('Create and upload')").click()
        wait(1500)
        lid = case_id("E2E Locked")
        upload([str(locked)])
        ok("a locked PDF asks for its password", "is password protected" in body())
        pg.locator(".modal input[type=password]").fill("wrong")
        pg.locator(".modal button:has-text('Open statement')").click()
        wait(1800)
        ok("a wrong password asks again", "is password protected" in body())
        pg.locator(".modal input[type=password]").fill("secret123")
        pg.locator(".modal button:has-text('Open statement')").click()
        wait(2500)
        f = get(f"/api/cases/{lid}/files")["files"][0]
        ok("the right password opens it", f["status"] in ("processed", "partial") and f["statements"], f["status"])

        # ---------------------------------------------------------------- edited PDF
        pg.locator(".side-btn.primary").click()
        wait(400)
        pg.locator("#nc-name").fill("E2E Edited")
        pg.locator("button:has-text('Create and upload')").click()
        wait(1500)
        upload([str(next((S / "tampered").glob("*C_overlay_amount.pdf")))])
        tab("Indicators")
        ok("an edited PDF is flagged, without accusing", "Possible editing" in body() and "fraud" not in body().lower().replace("none is a finding of fraud", ""), "")

        # ---------------------------------------------------------------- categories
        open_case("Rohan")
        tab("Categories")
        text = body()
        ok("categories are grouped", all(x in text for x in ("Income", "Obligations", "Spending", "Left out of the calculations")))
        ok("no filter controls on the category view", pg.locator(".content-inner select").count() == 0)
        pg.locator(".content-inner .card tbody tr.click").first.click()
        wait(500)
        ok("a category expands in place to its transactions", pg.locator(".content-inner .card table table tr").count() > 0)
        ok("finer spending categories appear", "Groceries" in text)

        # ---------------------------------------------------------------- rotation
        open_case("Neha")
        nid = case_id("Neha")
        an = get(f"/api/cases/{nid}/analysis")
        ok("money movement card is shown", "Money moving around" in body() and "Circle" in body())
        ok("if-real figure is far above income", an["metrics"]["estimated_income"]["stats"]["income_if_flagged_real"] > 3 * an["metrics"]["estimated_income"]["value"], an["metrics"]["estimated_income"]["stats"])
        pg.screenshot(path=str(OUT / "neha_overview.png"))
        pg.locator(".card", has=pg.locator("h3:has-text('Money moving around')")).locator("tbody tr").first.click()
        wait(900)
        ok("a movement opens its transactions", "Transactions" in pg.locator("nav.tabs .tab.active").inner_text() and pg.locator(".tbl tbody tr").count() > 0)
        ok("rotation is never called fraud", all("fraud" not in (f["title"] + f["detail"]).lower() for f in an["flags"]))

        # ---------------------------------------------------------------- currencies, by clicking
        open_case("Arjun")
        aid = case_id("Arjun")
        tab("Accounts")
        before = get(f"/api/cases/{aid}/analysis")
        ok("a currency with no rate is reported", "No rate for" in body() and before["fx"]["no_rate_rows"] == 6, before["fx"]["no_rate_rows"])
        ok("coverage by account is shown", "Statement coverage by account" in body() and pg.locator(".pill", has_text="Full").count() >= 18)
        cons = pg.locator(".card", has=pg.locator("h3:has-text('consolidated')")).locator("tbody tr").count()
        pg.locator(".seg button").nth(2).click()
        wait(600)
        ok("switching between consolidated and one account works", "consolidated" not in pg.locator(".card").nth(1).locator("h3").inner_text() and cons > 0)
        pg.locator(".seg button").first.click()
        wait(400)
        pg.locator("#fx-currency").select_option("GBP")
        pg.locator("#fx-rate").fill("105.25")
        pg.locator("#fx-source").fill("Bank's rate advice")
        pg.locator("button:has-text('Save rate')").click()
        ok("saving a rate recalculates", "recalculated" in toast_text("Rate saved"))
        wait(1800)
        after = get(f"/api/cases/{aid}/analysis")
        ok("the rate brings the rows in", after["fx"]["no_rate_rows"] == 0 and "No rate for" not in body(), after["fx"]["no_rate_rows"])
        ok("income rises by the converted amount", after["metrics"]["estimated_income"]["value"] == before["metrics"]["estimated_income"]["value"] + 400 * 10525, (before["metrics"]["estimated_income"]["value"], after["metrics"]["estimated_income"]["value"]))
        tab("Transactions")
        ok("the printed amount shows beside the rupee figure", "GBP 400.00 at 105.25" in body() and "USD " in body())

        # ---------------------------------------------------------------- report, rules, print, download
        tab("Report")
        wait(800)
        text = body()
        for s in ("Credit score", "Sustainable income", "What the customer earns", "Obligations and affordability", "Cash-flow trend", "Accounts and reconciliation", "Currencies", "Risk indicators", "Data quality"):
            ok(f"report has: {s}", s in text)
        ok("report says it is not a decision", "does not approve or decline" in text)
        ok("report shows regular and variable income", "regular" in text.lower() and "variable" in text.lower())
        pg.locator("button:has-text('supporting transaction')").first.click()
        wait(500)
        ok("report evidence reaches file, page and row", pg.locator(".evidence td").filter(has_text=" r").count() > 0 and ".csv p" in body())
        with pg.expect_download() as dl:
            pg.locator("button:has-text('JSON')").click()
        path = OUT / "report.json"
        dl.value.save_as(path)
        doc = json.loads(path.read_text())
        ok("JSON report downloads and is complete", doc["notice"] and doc["evidence"] and doc["metrics"]["estimated_income"]["key"])
        pg.emulate_media(media="print")
        pdf_path = OUT / "report_print.pdf"
        pg.pdf(path=str(pdf_path), format="A4", print_background=True)
        pg.emulate_media(media="screen")
        pdf = PdfReader(str(pdf_path))
        pdf_text = " ".join((p.extract_text() or "") for p in pdf.pages)
        ok("printed report contains every section", all(s in pdf_text for s in ("Credit score", "Sustainable income", "Obligations and affordability", "Cash-flow trend", "Accounts and reconciliation", "Risk indicators")), f"{len(pdf.pages)} pages")
        ok("printed report carries the evidence without opening anything", ".csv p" in pdf_text and "supporting transaction" in pdf_text, pdf_text[:80])
        first_page = pdf.pages[0].extract_text() or ""
        ok("printed report does not waste its first page", "Credit score" in first_page and "Sustainable income" in first_page, first_page[-120:])
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pp:
            cut = sum(1 for page in pp.pages for ch in page.chars if ch["x1"] > page.width - 1)
        ok("nothing on the printed report runs off the page", cut == 0, f"{cut} characters")
        ok("printed report leaves out the app chrome", "Load sample cases" not in pdf_text and "Acting as" not in pdf_text.replace("ACTING AS", "Acting as"), "")

        # ---------------------------------------------------------------- rule engines, by clicking
        pg.locator("[data-testid=open-engines]").click()
        wait(1200)
        text = body()
        ok("rule engines page lists the lenders", all(x in text for x in ("Standard", "Conservative bank (sample)", "SME growth lender (sample)")), text[:120])
        ok("engine settings are the Level 1 groups only", all(x in text for x in ("Reconciliation", "Classification", "Estimated income", "Recurring obligations")) and "Risk indicators" not in text and "Transfers and rotation" not in text)
        ok("an analyst cannot change engines", "Only a Senior reviewer" in text and pg.locator("#engine-copy-name").count() == 0)
        pg.locator("#role").select_option("senior")
        wait(900)
        ok("the Standard engine is protected", "cannot be changed" in body() and pg.locator("#param-income_method").is_disabled())
        pg.locator("#engine-copy-name").fill("Test Lender")
        pg.locator("button:has-text('Create engine')").click()
        ok("a senior reviewer creates an engine", "Test Lender created" in toast_text("Test Lender created"))
        wait(1000)
        pg.locator("#param-income_method").select_option("average")
        pg.locator("#param-recon_require_closing").check()
        pg.locator("#rule-keyword").fill("northstar")
        pg.locator("#rule-category").select_option("BUSINESS_RECEIPT")
        pg.locator("button:has-text('Add rule')").click()
        pg.locator("#score-estimated_income-full").fill("500000")
        pg.locator("#engine-note").fill("First setup")
        pg.locator("button:has-text('Save as a new version')").click()
        ok("saving creates a new version", "version 2" in toast_text("version 2"))
        wait(900)
        ok("the version history records the change", "First setup" in body() and "v2" in body())
        pg.screenshot(path=str(OUT / "rule_engine.png"))
        pg.locator("#param-review_min_p").fill("99")
        pg.locator("button:has-text('Save as a new version')").click()
        ok("an impossible setting is refused with a reason", "cannot be above" in toast_text("cannot be above"))
        wait(600)
        pg.locator("#role").select_option("analyst")
        wait(600)

        # ---------------------------------------------------------------- credit score, choosing the engine
        open_case("Arjun")
        tab("Credit score")
        wait(900)
        before = get(f"/api/cases/{aid}/analysis")["credit_score"]
        ok("the score shows on screen with its points", str(before["score"]) in pg.locator("[data-testid=credit-score]").inner_text() and "Estimated monthly income" in body() and " of " in body(), before["score"])
        ok("the score says what it is not", "Not a bureau score" in body() and "not a credit decision" in body())
        ok("the score only uses Level 1 parameters", {p["key"] for p in before["parameters"]} == {"estimated_income", "recurring_obligations", "net_cash_flow", "monthly_credits", "monthly_debits", "balance_trend", "reconciliation"})
        ok("the same case is compared across lenders", all(x in body() for x in ("Conservative bank (sample)", "SME growth lender (sample)", "Test Lender")))
        pg.locator("#case-engine").select_option(label=next(o for o in pg.locator("#case-engine option").all_inner_texts() if "Test Lender" in o))
        ok("choosing a lender's engine recalculates", "recalculated" in toast_text("Rule engine changed"))
        wait(2000)
        after = get(f"/api/cases/{aid}/analysis")
        ok("the score now follows that lender's rules", after["engine"]["name"] == "Test Lender" and after["credit_score"]["engine"] == "Test Lender" and after["credit_score"]["score"] != before["score"], (before["score"], after["credit_score"]["score"]))
        ok("the lender's keyword rule shows as the reason", any("this lender's rule" in str(c.get("detail")) for t in get(f"/api/cases/{aid}/transactions")[:40] if "NORTHSTAR" in t["description"] for c in get(f"/api/cases/{aid}/transactions/{t['txn_id']}")["classification"]["contributions"]))
        ok("income follows the lender's method", after["metrics"]["estimated_income"]["method"].startswith("average"), after["metrics"]["estimated_income"]["method"])
        pg.locator(".content-inner .card button:has-text('View ')").first.click()
        wait(800)
        ok("a score parameter opens its transactions", "Transactions" in pg.locator("nav.tabs .tab.active").inner_text())

        # ---------------------------------------------------------------- reconciliation
        tab("Reconciliation")
        text = body()
        ok("reconciliation shows the arithmetic", all(x in text.lower() for x in ("computed closing", "stated closing", "difference", "rules in force")))
        ok("reconciliation follows the lender's rules", after["reconciliation"]["rules"]["recon_require_closing"] is True and "required" in text)
        open_case("E2E Applicant")
        tab("Reconciliation")
        ok("overlaps between statements are shown", "Between statements" in body() and "overlap" in body().lower())
        open_case("Arjun")
        pg.locator("#role").select_option("analyst")

        # ---------------------------------------------------------------- earnings
        tab("Earnings")
        text = body()
        ok("earnings are tracked by month", all(x in text.lower() for x in ("what this customer earns", "running total", "at this rate, per year")), text[:200])
        ok("earnings show who pays", "Who pays" in text and "NORTHSTAR" in text)
        pg.locator(".card", has=pg.locator("h3:has-text('Who pays')")).locator("tbody tr").first.click()
        wait(800)
        ok("a payer opens their payments", "Transactions" in pg.locator("nav.tabs .tab.active").inner_text())

        # ---------------------------------------------------------------- account history
        tab("Accounts")
        ok("all accounts are listed with their history", "Account history" in body())
        pg.locator(".seg button").nth(1).click()
        wait(600)
        hist = pg.locator("[data-testid=account-history]").inner_text()
        ok("one account's history shows what is there", all(x in hist.lower() for x in ("opening balance", "lowest balance", "statement period", "first entry", "largest credit")), hist[:150])
        ok("sources, destinations and uses are shown", all(x in body() for x in ("Largest sources of money in", "Largest destinations of money out", "What this account was used for")))

        # ---------------------------------------------------------------- salaried: asked for the other statements
        salaried = Path(DATA) / "salaried_one_account.csv"
        lines = ["Bank X - Account Statement", "Account Name:,ASHA RAO", "Account Number:,11112222333", "Statement Period:,01-07-2026 to 31-12-2026", "Opening Balance:,1000.00", "", "Date,Description,Debit,Credit,Balance"]
        bal = 1000
        for m_ in range(7, 13):
            bal += 80000
            lines.append(f"01-{m_:02d}-2026,NEFT/ABC TECHNOLOGIES SALARY,,80000,{bal}.00")
            bal -= 30000
            lines.append(f"03-{m_:02d}-2026,NEFT/ASHA RAO/XXXXXXXX9981,30000,,{bal}.00")
        salaried.write_text("\n".join(lines))
        pg.locator(".side-btn.primary").click()
        wait(400)
        pg.locator("#nc-name").fill("E2E Salaried")
        pg.locator("button:has-text('Create and upload')").click()
        wait(1500)
        upload([str(salaried)])
        card = pg.locator("[data-testid=statement-requests]")
        ok("a salaried customer is asked for the other statements", card.count() == 1 and "Account ending 9981" in card.inner_text() and "other accounts" in card.inner_text(), card.inner_text()[:200] if card.count() else "")
        tab("Overview")
        ok("the request also shows on the overview", pg.locator("[data-testid=statement-requests]").count() == 1)
        pg.locator("[data-testid=statement-requests] tbody tr").first.click()
        wait(800)
        ok("a request opens the transfers behind it", "Transactions" in pg.locator("nav.tabs .tab.active").inner_text())

        # ---------------------------------------------------------------- indicators, accuracy, theme, layout
        open_case("Arjun")
        tab("Indicators")
        risk = [f for f in get(f"/api/cases/{aid}/analysis")["flags"] if f["category"] == "risk"]
        ok("each indicator shows its rule", pg.locator(".flag b:has-text('Rule:')").count() >= len(risk), len(risk))
        open_case("Rohan")
        tab("Indicators")
        pg.locator(".flag button").first.click()
        wait(900)
        ok("an indicator opens its supporting transactions", "Transactions" in pg.locator("nav.tabs .tab.active").inner_text())
        tab("Accuracy")
        ok("accuracy tab works with ground truth", "Per category" in body())
        open_case("Neha")
        tab("Accuracy")
        ok("accuracy tab explains when there is no ground truth", "No ground truth" in body())
        for name in ("Overview", "Credit score", "Report", "Earnings", "Categories", "Accounts", "Reconciliation", "Transactions", "Statements", "Indicators", "Review queue"):
            tab(name)
            over = pg.evaluate("document.documentElement.scrollWidth - innerWidth")
            ok(f"no sideways page scroll: {name}", over <= 1, over)
        pg.locator(".icon-btn[aria-label='Toggle theme']").click()
        wait(500)
        tab("Report")
        pg.screenshot(path=str(OUT / "dark_report.png"))
        ok("dark theme applies", pg.evaluate("document.documentElement.dataset.theme") == "dark")
        pg.locator(".icon-btn[aria-label='Toggle theme']").click()

        real_errors = [c for c in console if "403" not in c and "422" not in c]
        ok("no unexpected console errors", not real_errors, real_errors[:3])
        ok("no page errors at any point", not page_errors, page_errors[:3])
        ok("no server errors at any point", not server_errors, server_errors[:4])

        # ---------------------------------------------------------------- data survives a restart
        before_cases = {c["case_id"]: c.get("income") for c in get("/api/cases")}
        b.close()
    server.terminate()
    server.wait(timeout=20)
    server = start()
    after_cases = {c["case_id"]: c.get("income") for c in get("/api/cases")}
    ok("every case and figure survives a restart", before_cases == after_cases, (len(before_cases), len(after_cases)))
finally:
    server.terminate()
    shutil.rmtree(DATA, ignore_errors=True)

fails = [r for r in results if not r[0]]
print(f"\n{len(results) - len(fails)} of {len(results)} checks passed")
for _, label, detail in fails:
    print("FAILED:", label, "|", detail)
sys.exit(1 if fails else 0)
