# FinSight handoff

Context for teammates (and their Claude Code) to continue this project. Written 31 Oct 2026 at the end of the first
build session. Read this first, then `README.md` for setup.

**Status in one line:** a working bank statement analyser for the hackathon's Problem 9, Level 1 complete and tested on
four datasets and on real statement layouts (ICICI-style exports, a label-less layout). Weak spots: Gujarati, Tamil and Hindi
narrations, and scanned PDFs (reported, not read).

---

## 1. The problem

Hackathon brief "9. Bank Statement & Financial Analysis": ingest bank statements (PDF in several layouts, CSV, Excel,
JSON), extract and reconcile every transaction with a reference to its file and page, classify transactions (salary,
business receipts, internal transfers, refunds/reversals, EMIs, rent, utilities, card payments…) with a visible reason,
calculate income, obligations, cash flow and balance trends with a breakdown of included and excluded transactions,
and let analysts correct a classification with dependent figures recalculated and the original preserved. It must
handle irregular income, duplicates, overlapping and incomplete statements and suspicious items **without inventing
missing information**. The organiser judges the running application, not slides.

Six scenarios: (1) salary despite description changes, (2) irregular/self-employed income, (3) transfers, refunds and
reversals excluded, (4) recurring obligations and cash-flow stress, (5) duplicate/overlapping/missing statements,
(6) unusual transactions flagged for review, not called fraud.

---

## 2. Design principles (do not break these)

1. **Rules and arithmetic decide; models only add evidence.** No LLM in the scoring path. Money is integer paise; floats never touch amounts.
2. **Reproducible.** Same inputs plus same versions give the same output. `PIPELINE_VERSION`, `RULES_VERSION`, `WEIGHTS_VERSION`, `QUESTION_BANK_VERSION` live in `backend/app/config.py` and are recorded in every analysis. **Bump `RULES_VERSION` whenever you change rules, weights or categories**, otherwise cached classification runs are reused (an analysis is skipped when its input hash, which includes versions, is unchanged).
3. **Traceable.** Every metric returns `included` and `excluded` (with reasons) transaction ids. Every transaction keeps file, page and row (`silver.txn_sources`).
4. **Never invent.** Missing months are `missing`, never zero. Unreadable rows and pages are quarantined with a reason. Uncertain items go to review; income excludes them until confirmed (a provisional figure is shown separately).
5. **Idempotent.** File SHA-256 per case, deterministic transaction ids (hash of account, date, amounts, balance and ordinal; without a balance, the narration with spacing and punctuation removed), stage outputs replaced in one DB transaction, `Idempotency-Key` on POSTs, unique override `event_id`, content-addressed gold snapshots.
6. **Flags say "review recommended", never "fraud".**
7. **PII masked by default.** Account numbers, UPI IDs, phones, cards, PAN, Aadhaar and holder names are masked in display and replaced with placeholders for classification. Clear values live only in the encrypted vault; reveal requires a reason and is audit-logged.

---

## 3. Architecture

A shareable architecture page (private Claude artifact, owner must share it): https://claude.ai/artifact/2MnC49jnbaqtznjNfxxVjg

**Medallion layers in one DuckDB file** (`data/finsight.duckdb`, schemas `bronze`, `silver`, `gold`, `knowledge`, `review`, `security`):

```
upload ─► bronze: files (sha256), pages (status per page), raw_rows (encrypted), quarantine (reason per item)
       ─► silver: accounts, statements (reconciliation), transactions, txn_sources, txn_links (pairs),
                  classification_runs + classifications (history kept), overrides (events), coverage, cross_checks,
                  document_checks (edit risk)
       ─► gold:   snapshots (content-addressed; metrics with lineage, flags, monthly table)
knowledge: semantic_examples (global seed + case corrections), periodic_patterns
review:    audit_log (hash chain), idempotency_keys, stage_runs
security:  pii_vault (Fernet-encrypted), egress_log
```

**Classification** (`backend/app/classify/engine.py`), three tiers:

1. **Hard gates** (never averaged): transfer pairs across the customer's own accounts (equal amount, opposite direction, ±1 day), bounce/return pairs, bounce charges, reversal/refund pairs (either direction, shared reference preferred).
2. **Weighted log-odds fusion** of signals: keyword rules (`rules.py`), entity lists (lenders, utilities), own-bank mentions, account trading pattern, periodic memory (monthly series, merged across employer renames), semantic memory (char n-gram TF-IDF kNN over approved examples), optional Jev answers. `P = softmax(scores)` with a baseline category per direction.
3. **Policy:** auto if P ≥ 0.90 and margin ≥ 0.30; review if P ≥ 0.60 or a near tie or a kNN disagreement; otherwise unclassified. Debits with no evidence default to General spending, and go to review when they are ₹25,000 or more or in a script the rules cannot read. Semantic matches below similarity 0.50 are ignored.

**Currencies** (`fx.py`): amounts are stored as printed, in hundredths of the account's currency, and reconcile in that currency. Figures are reported in INR at rates a person supplies with a source (`silver.fx_rates`, one per currency or per month). No rate is fetched or assumed: a row without one is left out of every figure and raises `fx_rate_missing`. A file with a currency column becomes one account per currency. Own-account matching across currencies allows a 2% rate difference. Currencies with three decimals (KWD, OMR, BHD) are rounded to two, so their balance chains may show breaks.

**Salary and business income:** a salary has one payer at a time, so a regular credit running alongside another with no payroll wording counts as a client, and more than two salary payers in a month goes to review. For self-employed customers, income is receipts less identified suppliers, staff and tax (`BUSINESS_COST_CATEGORIES`), never below zero, with gross receipts shown beside it. Rent and EMIs are not deducted: they are obligations, counted once against this income. Those costs also leave Monthly surplus, so nothing is charged twice.

**Assessment** (Level 2): income splits into regular and variable with unusual months and a consistency label; `affordability` checks obligations against rules in `silver.case_rules` (illustrative defaults otherwise, and the report says which); `cash_flow_trend` compares later months with earlier ones; `account_view` gives each account's months beside the consolidated table; every risk indicator carries its rule and supporting transactions; `report.py` builds the evidence-linked report.

**Rule engines** (`rule_engine.py`, Rule engines page): each lender copies Standard and sets its own reconciliation rules, classification thresholds, keyword rules, income method and months, recurring-obligation months, and score weights and bands. Only Level 1 parameters can be set; other thresholds keep fixed defaults. Senior reviewers and admins edit; every save is a new version, and every analysis records the version it used. A case picks its engine on the Credit score tab.

**Credit score** (`metrics/score.py`): 0 to 100 from the Level 1 calculations only: estimated income, recurring obligations, net cash flow, monthly credits, monthly debits, balance trend and reconciliation. Points run in a straight line between each parameter's limits, and every point shows its evidence. Withheld with a reason when income is unknown or too little can be assessed. It is hand-set: not a bureau score, not calibrated against repayment, and not a decision.

**Reconciliation, history, earnings, statement requests:** `validate/reconcile.py:case_view` judges each statement against the engine's rules; `metrics/history.py` builds each account's timeline, the earnings tracker, and the prompts for statements needed to track internal transfers.

**Possible money rotation** (`classify/rotation.py`), after the three tiers: a similar amount across own accounts up to 7 days apart or short by a fee, one entry split into two or three, out to a party and back within 15 days, and a repeated habit of money leaving within 3 days of arriving. These are never settled automatically: rows go to review, income leaves them out (not even provisional), and `income_if_flagged_real` shows what income would read if all were real. Exact own-account pairs stay a Tier 1 gate. `money_flows` in gold lists transfers, circles, parties and pass-through for the Overview card.

**Corrections** (`review/overrides.py`): append an override event, write the narration to case-level semantic memory, re-run classification and gold, return a before/after diff. The model's own result and history are kept. A correction only influences rows that cannot decide on their own evidence (`settled` in `engine.py`): decided rows never move.

**Edit-risk score** (`validate/tamper.py`): structure signals (PDF editor or office producer, incremental saves, modified after creation, font/size mismatch inside a table, text printed over different text) plus arithmetic signals (unreconciled totals, balance-chain breaks). 0–100, levels none/low/medium/high. PDF libraries as producer are neutral.

---

## 4. Repository map

```
backend/app/
  main.py            FastAPI routes (also serves frontend/dist on :8000 if built)
  config.py          paths, versions, thresholds
  db.py              DuckDB schema and helpers
  money.py           paise parsing (Rs./₹/INR, Indian and Western grouping), date parsing incl. date+time cells
  analysis.py        silver -> gold orchestration, snapshots, diff
  fx.py              exchange rates and conversion
  rule_engine.py     lender rule engines: Level 1 settings, versions, assignment
  report.py          the evidence-linked assessment report
  evaluation.py      accuracy vs sample ground truth (Accuracy tab)
  ingest/            extract.py (PDF tables, watermark removal, CSV/XLSX/JSON), layout.py (column roles, header fields),
                     normalize.py (channel, counterparty, refs), service.py (upload pipeline)
  validate/          reconcile.py (row chain with misprint detection), tamper.py (edit risk)
  dedup/coverage.py  month coverage, gaps, overlaps, balance continuity between statements
  classify/          categories.py (categories + metric treatment), rules.py, periodic.py, semantic.py, engine.py, rotation.py, jev_client.py
  metrics/           effective.py (category in effect = latest override else latest run), engine.py (all metrics), score.py (credit score), history.py (account history, earnings, statement requests)
  flags/rules.py     all indicators
  review/overrides.py
  security/          crypto.py (HMAC tokens, vault), pii.py, egress.py, audit.py, rbac.py, log_scrub.py
backend/tests/       tests and evaluation harnesses (section 6)
datagen/             generate.py (2 English demo cases), multilingual.py (16 multilingual statements)
datasets/            multilingual_v1/ + README.md
sample_data/         generated demo cases, problem_files/ (corrupted + scanned PDF), tampered/ (edited PDFs for demos)
frontend/            React + TypeScript + Vite + Recharts UI
```

---

## 5. Running it

Windows notes: set `PYTHONIOENCODING=utf-8` for scripts that print ₹ or Indic text. Packages were installed into the
user site-packages during development; use a venv for a clean setup (see `README.md`).

```bash
pip install -r backend/requirements.txt
python datagen/generate.py
python datagen/multilingual.py
python -m uvicorn app.main:app --app-dir backend --port 8000
```

UI: either `cd frontend && npm install && npm run dev` (http://localhost:5173, proxies /api) or `npm run build` and use
http://localhost:8000. **The Vite dev server hung once during heavy background evaluations; the built UI on :8000 was reliable.**

Load demo data with the "Load sample cases" button or `curl -X POST http://127.0.0.1:8000/api/demo/load`.
Demo roles via the sidebar (headers `X-User`/`X-Role`): analyst, senior, auditor (read only), admin.

**Changing rules or adding columns needs no reset.** Bump the version and `GET /analysis` recomputes old snapshots; new columns are added in place in `db.py`. Deleting `data/` destroys every uploaded case, and there is no backup, so check `GET /api/cases` first and ask whoever is using the server. A reset is only needed after changing tokenisation or transaction ids.

---

## 6. Tests and evaluations

All tests create a throwaway DB in the temp folder.

| Command | What it checks | Latest result |
|---|---|---|
| `python backend/tests/test_brief_examples.py` | The brief's worked examples with assertions | Pass |
| `python backend/tests/test_review_flow.py` | Corrections, idempotency, RBAC, reveal logging, audit chain, re-upload | Pass |
| `python backend/tests/test_tokens.py` | Tokeniser keeps all scripts distinct | Pass |
| `python backend/tests/test_regressions.py` | Failures found on real statements, reproduced on generated files | Pass |
| `python backend/tests/test_invariants.py [files]` | Recomputes every figure from the rows: nothing unaccounted for, nothing counted twice | Pass |
| `python backend/tests/e2e_ui.py` | Clicks through every feature in Chrome on its own server and data (needs playwright and a built UI) | Pass |
| `python backend/tests/smoke.py` | Demo cases end to end | 99.2% / 99.5% category accuracy |
| `python backend/tests/eval_multilingual.py` | 16 multilingual statements, accuracy + efficiency | 63% overall (see below) |
| `python backend/tests/eval_hf_statements.py <train dir>` | AgamiAI PDFs vs JSON labels | see below |
| `python backend/tests/eval_tamper.py [--n 25] [--demo-out sample_data/tampered]` | Edit-risk detection | 162/162 edits caught |
| `python backend/tests/eval_vlm_dataset.py <zip>` | VLM dataset labels through the pipeline | see below |

Result JSON files are written next to the scripts (`*.json` in `backend/tests/`).

### Datasets used

| Dataset | Where | Notes |
|---|---|---|
| Demo cases | `sample_data/` (generated) | 3 customers, 2 PDF layouts + CSV/XLSX/JSON, all six scenarios planted, ground truth in `ground_truth.json` for the first two. The third (`case_rotation`) passes ₹5 lakh between three accounts on about ₹1.15 lakh of real receipts. The fourth (`case_multicurrency`) has INR, USD and GBP accounts, with no rate supplied for GBP |
| Multilingual v1 | `datasets/multilingual_v1/` (generated) | 16 statements, 2,551 labelled rows: English, Hindi, Hinglish, Gujarati, Tamil. 8 dev / 8 held-out test with different wording. **Tune on dev only.** |
| AgamiAI Indian-Bank-Statements | `hf download AgamiAI/Indian-Bank-Statements --repo-type dataset --include "train/*"` (was saved to `Downloads/hf_indian_bank_statements`) | 400 business statements: 200 digital PDFs (2 layouts, diagonal watermark) + 200 scanned, JSON labels without categories. **Labels' own arithmetic is broken in 184/200 statements.** |
| VLM training dataset | `Downloads/vlm_training_dataset_1000_real.zip` (local only) | 2,913 page images + extraction JSON, Hindi/Gujarati narrations, no category labels |

### Latest numbers

- **AgamiAI digital PDFs (200):** row recall 99.2% (all misses are zero-amount `FAILED` lines, quarantined), row precision 100%, header fields 100%, balances 100%, narration ≥ 90% similar 100%, reversal recall 100%, reconciliation verdict correct for all 200, auto-decided 35% (type 1) / 56% (type 2) before the r2 rules. A re-run with r2 rules was in progress (`--out hf_eval_v2`); check `backend/tests/hf_eval_v2.json`. Scanned: 1,194 pages all reported `needs_ocr`.
- **Multilingual v1 (2,548 rows, rules r4, `multilingual_eval_v5.json`):** English 96%, Hinglish 87%, Hindi 66%, Gujarati 39%, Tamil 29% category accuracy across all rows. Finer spending labels roll up to General spending for this comparison. On the held-out test split, accuracy is 62.5% and automatic decisions are 88.4% right (77.6% before r3) on 62% of rows. No rotation flags on these 2,548 rows, which contain none. Tamil automatic decisions are still only 42% right on test. Income exact on salaried statements with Latin-script employers; badly underestimated for regional-language business statements.
- **VLM test split (120 statements, 7,527 rows):** ingestion 100%, reconciliation 120/120, 63% auto-decided, about 86% of a hand-checked sample of automatic decisions correct. Run before the tokeniser fix, so redo it.
- **Edit risk:** all 162 applicable edits caught, 0 false alarms from structure checks on 54 clean files. AgamiAI clean files score "medium" only because their arithmetic is broken.
- **Efficiency (multilingual set):** upload 67 rows/s, classification 430 rows/s, about 2.3 s per statement end to end, correction with full recompute 0.78 s on a 323-row case, peak Python heap 3.4 MB.

---

## 7. Decisions made, and why

| Decision | Why |
|---|---|
| Deterministic core, LLM only optional and outside scoring | Credit decisions need reproducible, defensible numbers; LLM output is nondeterministic and can expose data |
| Medallion layers in DuckDB, not Spark | Thousands of rows per case; layers give lineage and immutability cheaply |
| Weighted log-odds fusion instead of an OR cascade | Agreeing signals raise confidence, conflicts become visible, per-signal contributions explain the result |
| Hard facts as gates, not averaged | A confirmed own-account pair must never be outvoted by a keyword |
| Jev (TypeSafe's calibrated classification model) behind an interface, off by default | Early access, unknown API; calibrated probabilities fit our thresholds; fusion works without it |
| Approval tiers (HITL): analyst for labels affecting income, senior for new categories, rules, weights and global memory | A wrong global approval spreads; customer memory first |
| Keep LLMs out of the multilingual path; prefer word lists, transliteration and a small local multilingual model (MuRIL / LaBSE / multilingual-e5); optionally a local open-weight LLM for leftovers, or an LLM as an offline teacher on public data | Mentor raised data exposure; most errors are missing vocabulary, not reasoning |
| Interpretation of mentor's "editing confidence score" = edit-risk score for the PDF | Best fit to the brief's "suspicious inconsistencies" and to competitor CART's tamper detection; mentor could not be asked |
| Median for income; recent 3-month average if income fell 15%+; conservative = 25th percentile; provisional shown separately | Brief asks for sustainable, not peak, income; never mix unconfirmed credits into the main figure |
| Competitor CART (novelpatterns.ai) reviewed from public pages only | Closed SaaS; "reverse-engineer-anything" tools decompile binaries and do not apply |

---

## 8. Bugs found by testing (all fixed)

- **Tokeniser erased Indic scripts** (kept only A–Z0–9), so different Tamil/Gujarati narrations shared a vault token and were shown and classified as each other. Now Unicode-aware; narrations hashed exactly. Guarded by `test_tokens.py`. **Earlier Hindi/Gujarati results were affected.**
- PDF reading was 0% on AgamiAI: diagonal watermark letters leaked into cells (fixed by dropping rotated characters), `Rs.` prefixes, date+time cells, "Posted Date" headers, header labels without colons.
- Wrapped cells split long numbers with spaces (`APR2 6`); now joined without a space between digits or next to `/`.
- Reversals only paired refunds of debits; now both directions (reversed credits too), plus a reversal keyword rule.
- Recurrence wrongly added EMI/rent evidence to every fixed monthly debit (SIPs, insurance); now only strengthens existing obligation evidence.
- False "salary not received" when one month's salary was worded differently; single months now merge into a series, and any salary credit in a month prevents the flag.
- Client payments left unclassified; business-income keywords added; recurrence no longer pushes business receipts toward salary.
- Income showed ₹0 when likely receipts were in review; provisional income now shown.
- A scanned PDF created a phantom "unidentified" account; accounts are created only from files that yield rows.
- Similarity memory matched on rail words (`IMPS/P2A`), letting one correction pull unrelated payments; rail words stripped.
- `datagen/generate.py` deleted `sample_data/tampered`; it now only replaces its own folders.
- **Real statements read nothing:** headers wrapped mid-word (`Withdra\nwal`), `01/Apr/2024` dates, column labels missing from the text layer (columns now inferred from data, debit and credit settled by the balance chain), no ruling lines (word-position fallback), newest-first statements. Bank name came from the first line ("Detailed"); holder missed on `Name:` headers.
- **Locked PDFs were a dead end:** pdfplumber's wrapped error was not recognised, and retrying with the password counted as a duplicate.
- **One correction could take income to ₹0:** case memory demoted neighbouring salary rows to review.
- **Same statement in two formats counted twice** because transaction ids hashed the exact narration. A row reworded between statements is now counted once and recorded as `narration_differs`.
- **Income ₹0 on an active account:** months before the first entry entered the median. They are now left out with a note, and do not raise low-balance flags.
- **Unconfirmed EMI guesses added obligations:** irregular unconfirmed items are held back and reported; stable ones still count.
- **IFSC codes drove similarity matches** (an HDFC branch code looked like an HDFC loan). Stripped from memory text.
- **Analyser workbooks:** summary sheets raised false "page not read" flags and could add rows. Account details are now read from any sheet.
- **Log scrubber broke uvicorn's access log** by clearing record args.
- **Rotation inflated income:** late, split or fee-reduced transfers and money going out to a party and back read as income, up to six times the real figure.
- **Unclassified debits were left out of Monthly surplus**, overstating what is left over.

---

## 9. Backlog, in priority order

1. **Multilingual layer** (biggest accuracy gap): word lists for Hindi/Gujarati/Tamil/Hinglish (salary वेतन/પગાર/சம்பளம்/pagar, rent किराया/ભાડું/வாடகை, sales बिक्री/વેચાણ/விற்பனை, cash नकद/રોકડ/ரொக்க, interest ब्याज/વ્યાજ/வட்டி…), transliteration (e.g. `indic-transliteration`) and a local multilingual embedding model in the semantic-memory slot. Tune on `multilingual_v1` dev, report on test.
2. **PII masking for Indic names:** other people's names in Devanagari/Gujarati/Tamil are not masked. Must be fixed before any external model is enabled.
3. Re-run the VLM evaluation after the tokeniser fix; check the AgamiAI `hf_eval_v2.json` results.
4. Remaining narration types: `UPI/CR/…/SENT-TRANSFER FROM` person-to-person credits; dividends in the holder's own name are labelled internal transfers; `FEES` in "school fees" triggers bank charges.
5. Costs inside General spending are not taken off business income, and cash deposits stay out of receipts until verified; `income_if_cash_real` shows the difference.
6. Real HDFC/SBI/Axis/Kotak PDF layouts (need sample statements with personal details removed). ICICI-style exports are verified; the borderless reader is tested only on generated files. Holder names printed without a label are not captured.
7. OCR for scanned PDFs (Tesseract with hin+guj+tam, or a vision model); the VLM dataset is a ready benchmark.
8. Speed: batch the per-row vault writes during upload (upload is the slowest stage).
9. Learned fusion weights (logistic regression on dev labels + corrections) and a calibration check.
10. Improvement loop from the architecture: cluster unclassified items, propose categories, impact preview, senior approval, promotion to global memory.
11. Fetch or import official reference rates if a lender wants that; today rates are entered by hand.

---

## 10. Gotchas

- Changing rules without bumping `RULES_VERSION` silently reuses old classifications.
- `GET /api/cases/{id}/analysis` returns the stored snapshot unless it was made by older rules, in which case it recomputes.
- The edit-risk flag on AgamiAI files comes from their broken arithmetic, not file edits; look at `structure_score`.
- Recharts bars animate in for about a second; screenshots taken immediately look empty.
- `pypdf` is needed for `eval_tamper.py` and `test_regressions.py`. `huggingface_hub[cli]` (`hf`) only for downloading AgamiAI.
- During setup, pip upgraded `attrs` and changed `httpx` in the user site-packages, which conflicts with unrelated packages on the original dev machine (`fireworks-ai`, `langgraph-sdk`). Use a venv.
- Sample data, datasets and keys are synthetic or dev-only. `data/dev_keys.json` is generated locally and git-ignored; production keys belong in a KMS.
