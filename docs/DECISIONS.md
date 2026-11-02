# Decision log

Every significant decision in FinSight: what was chosen, why, what it costs, and how we know the reason.

## How to read this

Each entry has a **Basis**, because not every reason was written down at the time:

| Basis | Meaning |
|---|---|
| **Recorded** | The reason is stated in `HANDOFF.md` section 2 or 7, or in the brief it summarises |
| **From a bug** | The decision fixed a failure listed in `HANDOFF.md` section 8 |
| **Code** | The reason is stated in a comment or docstring in the file named |
| **Inferred** | No reason was written down. The reason given is a reading of the code and may not be what the authors had in mind |

"Instead of" lists only alternatives that were recorded or that the design plainly rules out.

| Area | Decisions |
|---|---|
| [Foundations](#foundations) | D-01 to D-06 |
| [Storage](#storage) | D-07 to D-11 |
| [Reading statements](#reading-statements) | D-12 to D-22 |
| [Reconciliation and edit risk](#reconciliation-and-edit-risk) | D-23 to D-26 |
| [Classification](#classification) | D-27 to D-42 |
| [Income and obligations](#income-and-obligations) | D-43 to D-51 |
| [Currencies, lenders and score](#currencies-lenders-and-score) | D-52 to D-56 |
| [Corrections](#corrections) | D-57 to D-61 |
| [Security](#security) | D-62 to D-68 |
| [Delivery and evaluation](#delivery-and-evaluation) | D-69 to D-74 |

---

## Foundations

### D-01 Rules and arithmetic decide; models only add evidence

- **Decision.** No LLM in scoring or arithmetic. Categories come from rules, fixed-weight evidence and a threshold policy.
- **Why.** Credit decisions need reproducible, defensible numbers. LLM output is nondeterministic and can expose data.
- **Instead of.** An LLM classifier, or an LLM reviewing leftovers.
- **Cost.** Wording the rules have not seen goes to review instead of being guessed. This is why Tamil and regional business statements read low.
- **Basis.** Recorded.

### D-02 Money is integer paise

- **Decision.** Amounts are parsed through `Decimal` and stored as integers. Sums are integer. Exchange conversion uses `Decimal` with half-even rounding.
- **Why.** Reconciliation compares for exact equality. Binary floats would create false breaks.
- **Cost.** Ratios, probabilities and aggregates are not integers. Medians and means pass through Python's `statistics` and are truncated to whole paise, so the rule is stricter in the docs than in those few lines.
- **Basis.** Recorded for the rule. The aggregate detail is from reading `metrics/engine.py`.

### D-03 Never invent missing information

- **Decision.** A missing month is `missing`, not zero. An unreadable page is reported. A row with no rate is left out. An uncertain row goes to review.
- **Why.** The brief requires it, and a guess in a credit file looks like a fact to the next reader.
- **Cost.** Figures can be low or absent until someone acts. The UI shows provisional figures beside them to make that visible.
- **Basis.** Recorded.

### D-04 Flags say "review recommended"; nothing approves or declines

- **Decision.** Indicators recommend review and never say "fraud". Neither a rule engine nor the score makes a credit decision.
- **Why.** Statements alone cannot establish intent. A person decides.
- **Basis.** Recorded.

### D-05 Version everything, and recompute old results on read

- **Decision.** Pipeline, rules, weights and question bank versions, the engine version and the memory version are recorded with every run. `GET /analysis` recomputes a snapshot made by older versions.
- **Why.** A figure must trace to the rules behind it, and new rules should reach existing cases without a reset.
- **Cost.** A read can write and can be slow once. Changing rules without bumping the version silently reuses old results.
- **Basis.** Recorded, and code (`analysis.py`).

### D-06 One process, not services

- **Decision.** A single FastAPI process with one database file.
- **Why.** Thousands of rows per case need nothing more, and it can be run by one person in two minutes. The organiser judged the running application.
- **Cost.** It does not scale out, and one analysis runs at a time. Moving to several workers would mean changing the database first.
- **Basis.** Code (`db.py`) for the row counts. Inferred for the rest.

---

## Storage

### D-07 Medallion layers in DuckDB, not Spark

- **Decision.** Bronze, silver and gold schemas in one DuckDB file, plus knowledge, review and security.
- **Why.** Thousands of rows per case. The layers give lineage and immutability cheaply.
- **Instead of.** Spark.
- **Cost.** One writer process.
- **Basis.** Recorded.

### D-08 Additive schema changes, applied on start

- **Decision.** `CREATE TABLE IF NOT EXISTS`, plus `ADD COLUMN IF NOT EXISTS`. There is no migration tool.
- **Why.** Restarting on newer code must never cost existing cases. `data/` holds every uploaded case and has no backup.
- **Cost.** Renames and type changes have no path. Only additions are safe.
- **Basis.** Code (`db.py`), and recorded in `HANDOFF.md` section 5.

### D-09 Content-addressed snapshots

- **Decision.** A snapshot's ID is a hash of everything it was computed from. Identical inputs return the existing one.
- **Why.** A new snapshot then means something really changed, and any two can be compared.
- **Basis.** Recorded, and code (`analysis.py`).

### D-10 Returning to an earlier state makes that snapshot current

- **Decision.** When a recompute matches an old snapshot, that snapshot gets a new sequence number.
- **Why.** Undoing a decision found the old snapshot, but "latest" still returned the newer one, so the undone figures stayed on screen.
- **Cost.** Snapshot rows are not strictly immutable: `seq` and the `seq` inside the payload change.
- **Basis.** From a bug.

### D-11 Snapshot results stored as one JSON document

- **Decision.** Gold is a JSON payload per snapshot, not tables of metrics.
- **Why.** The screens and report read a whole assessment at once, and old snapshots keep their exact shape as the code changes.
- **Cost.** Gold cannot be queried across cases with plain SQL columns. Comparing cases would need JSON extraction.
- **Basis.** Inferred.

---

## Reading statements

### D-12 Read the PDF's text layer with pdfplumber

- **Decision.** Tables and word positions from pdfplumber, with no vision model.
- **Why.** Deterministic, local, and each cell traces to a page and row. It also follows from D-01 and the privacy goals.
- **Cost.** Scanned PDFs cannot be read (D-17).
- **Basis.** Inferred.

### D-13 Infer the layout rather than write bank templates

- **Decision.** Find column roles from header text, then from the data, then from word positions. The balance chain settles which column is debit and which is credit.
- **Why.** Real statements broke template-like assumptions: wrapped headers, no column labels in the text layer, no ruling lines, newest-first order.
- **Cost.** Holder names printed without a label are not captured. The word-position reader is tested only on generated files.
- **Basis.** From a bug for the failures. Inferred for not using templates.

### D-14 Drop rotated characters before reading

- **Decision.** Remove rotated text from each page first.
- **Why.** Diagonal watermark letters leaked into cells, and reading was 0% on the AgamiAI files.
- **Basis.** From a bug.

### D-15 All formats produce one shape

- **Decision.** PDF, CSV, Excel and JSON all return header fields, rows and page results.
- **Why.** Everything after extraction stays format-blind, and overlaps across formats de-duplicate naturally.
- **Basis.** Code (`ingest/extract.py`).

### D-16 Every page has a status, and every unread item a reason

- **Decision.** `bronze.pages` and `bronze.quarantine`, with reasons, surfaced as flags.
- **Why.** The brief forbids skipping silently, and an analyst must know what was not read.
- **Basis.** Recorded, and code (`ingest/extract.py`).

### D-17 No OCR yet; scanned pages are reported

- **Decision.** A page with no text layer is `needs_ocr` and raises a high-severity flag.
- **Why.** Reporting a gap honestly is better than a misread amount in a credit file. OCR was not reached in the build.
- **Cost.** Scanned statements give no figures. All 1,194 scanned AgamiAI pages were reported, not read.
- **Basis.** Recorded in the README's limits and the backlog.

### D-18 Transaction IDs come from the row's content

- **Decision.** Hash of account, date, debit, credit, balance and an ordinal. Without a balance, the narration with spacing and punctuation removed.
- **Why.** Hashing the exact narration counted the same statement twice when it arrived in two formats. The ordinal keeps real repeats separate.
- **Cost.** Changing the ID recipe needs a reset. IDs depend on the HMAC key through the account token, so they differ between installations.
- **Basis.** From a bug, and recorded.

### D-19 A reworded copy is counted once and recorded

- **Decision.** Same date, amounts and balance with different wording is one transaction, with a `narration_differs` entry.
- **Why.** The money is the same, and the difference may matter to a reviewer.
- **Basis.** From a bug.

### D-20 Quarantine a row years away from its statement

- **Decision.** Years outside 1990 to 2099 are not dates. A row more than about five years from its statement's middle is quarantined.
- **Why.** One date in 1900 and one in 2099 hung analysis for over ten minutes, because months were scanned daily across the range.
- **Basis.** From a bug.

### D-21 Accounts exist only for files that yield rows

- **Decision.** No rows, no account.
- **Why.** A scanned PDF created a phantom "unidentified" account.
- **Basis.** From a bug.

### D-22 A PDF password is used once and never stored

- **Decision.** It is passed through processing and dropped. A file marked `needs_password` can be retried without counting as a duplicate.
- **Why.** Locked PDFs were a dead end, because the retry counted as a duplicate. Storing the password would add a secret to protect.
- **Basis.** From a bug for the retry. Code (`ingest/service.py`) for not storing it.

---

## Reconciliation and edit risk

### D-23 Tell a misprint from a gap

- **Decision.** If the next row continues from the computed balance, the break is one misprint. If not, the jump may be a missing row.
- **Why.** Otherwise one bad figure reports as two breaks and hides what happened.
- **Basis.** Code (`validate/reconcile.py`).

### D-24 Store arithmetic as fact; judge it per lender

- **Decision.** Statement results are stored once. `case_view` compares them to the engine's tolerance each time.
- **Why.** Lenders differ on what is acceptable, and the arithmetic does not change with the lender.
- **Basis.** Inferred, from the review feedback on reconciliation setup.

### D-25 An "edit-risk" score for PDFs

- **Decision.** Structure signals (editor software, incremental saves, modified after creation, font mismatch, overprinted text) plus arithmetic signals, weighted and capped at 100.
- **Why.** The mentor mentioned an "editing confidence score" and could not be asked what it meant. This was the best fit to the brief's "suspicious inconsistencies", and a competitor offered tamper detection.
- **Cost.** It rests on an interpretation of a remark. Weights are hand-set. Files with broken arithmetic score "medium" with no edit, so `structure_score` is kept separately.
- **Basis.** Recorded.

### D-26 A PDF library as producer is neutral

- **Decision.** Only editors and office software count against a file.
- **Why.** Banks generate statements with PDF libraries. Measured: 0 false alarms from structure checks on 54 clean files.
- **Basis.** Code (`validate/tamper.py`), and recorded.

---

## Classification

### D-27 Three tiers

- **Decision.** Hard gates, then weighted evidence, then a policy.
- **Why.** Each kind of knowledge gets the right treatment: certain facts, uncertain clues, and a separate choice about how much certainty is enough.
- **Basis.** Recorded.

### D-28 Weighted log-odds fusion, not an OR cascade

- **Decision.** Add signal weights per category and take a softmax.
- **Why.** Agreeing signals raise confidence, conflicts become visible, and each signal's contribution explains the result.
- **Instead of.** The first matching rule wins.
- **Cost.** Weights are hand-set (`w0`) and the probabilities are not calibrated. They rank evidence and should not be read as true likelihoods. Learned weights and a calibration check are in the backlog.
- **Basis.** Recorded.

### D-29 Hard facts are gates, never averaged

- **Decision.** Transfer pairs, returned payments and reversal pairs decide a row outright.
- **Why.** A confirmed own-account pair must never be outvoted by a keyword.
- **Basis.** Recorded.

### D-30 Automatic at P ≥ 0.90 and margin ≥ 0.30; review at P ≥ 0.60

- **Decision.** These thresholds, editable per lender except the margin.
- **Why.** Automatic decisions must be rarely wrong, and everything else should reach a person with a suggestion. Measured on held-out data: automatic decisions 97.7% right on 77% of rows.
- **Cost.** A stricter policy sends more work to analysts and leaves more income unconfirmed.
- **Basis.** Inferred. No record says how these exact values were chosen. The measurement is from `EVALUATION.md`, on generated data.

### D-31 Similarity by character n-grams over approved examples

- **Decision.** TF-IDF on 3 to 5 character n-grams, with k-nearest examples at a fixed weight. Below similarity 0.50 a match is ignored.
- **Why.** Fast, local and explainable, with no model download. Bank abbreviations share fragments rather than whole words.
- **Instead of.** Sentence embeddings, which remain in the backlog as a local multilingual model for this slot.
- **Cost.** Spelling-level only. It cannot match across scripts, and it knows nothing of meaning.
- **Basis.** Code for the 0.50 floor. Inferred for choosing n-grams.

### D-32 Strip rail words and branch codes before matching

- **Decision.** Remove `IMPS`, `NEFT`, `UPI` and similar words, digits, placeholders and IFSC codes.
- **Why.** One remembered IMPS narration pulled every IMPS payment towards its category, and an HDFC branch code looked like an HDFC loan.
- **Basis.** From a bug, and code (`classify/semantic.py`).

### D-33 Classify on masked text

- **Decision.** Rules and memory see placeholders such as `<HOLDER>` and `<OWN_ACCT>`.
- **Why.** Personal values stay out of classification, and a placeholder is itself a useful clue: `<HOLDER>` suggests a transfer.
- **Basis.** Recorded.

### D-34 Word lists for regional languages, not an LLM

- **Decision.** Hindi, Hinglish, Gujarati and Tamil vocabulary in the keyword rules. Next come transliteration and a small local model.
- **Why.** A mentor raised data exposure, and most errors were missing vocabulary, not reasoning. Measured: held-out accuracy rose from 62.5% to 90.8%.
- **Cost.** Vocabulary has to be maintained. Tamil is at 81.3%, with half its rows going to review.
- **Basis.** Recorded.

### D-35 A salary has one payer at a time

- **Decision.** A regular credit running alongside another, with no payroll wording, counts as a client. More than two salary payers in a month goes to review.
- **Why.** Regular payment looks like salary. A retainer client or a tenant is steady too, and salary is treated more favourably than receipts.
- **Basis.** Code (`classify/engine.py`), and recorded.

### D-36 Merge a series across employer renames

- **Decision.** Clusters with no shared months, amounts within 8% and the same day of the month (±4) merge, and a single differently worded month can join.
- **Why.** Scenario 1 asks for salary despite description changes. Separately, one month's different wording caused a false "salary not received".
- **Basis.** Code (`classify/periodic.py`), and from a bug.

### D-37 Recurrence only strengthens obligation evidence

- **Decision.** A fixed monthly debit adds to EMI or rent only if that category already has evidence.
- **Why.** Recurrence says a payment is regular, not what it is. SIPs and insurance were being called EMIs.
- **Basis.** From a bug.

### D-38 What a row with no evidence becomes

- **Decision.** A small debit becomes General spending automatically. A debit of ₹25,000 or more, or one in a script the rules cannot read, goes to review. A credit of ₹10,000 or more stays unclassified. A smaller credit becomes Other credit.
- **Why.** Small ordinary spending should not fill the queue, but no evidence is not evidence of ordinary spending for large or unreadable rows, and an unknown credit must not become income.
- **Basis.** Code (`classify/engine.py`). The amounts are hand-set.

### D-39 A named merchant beats the payment rail; mixed spending stays spending

- **Decision.** The generic `UPI` clue is dropped when a specific spending rule fires. Two kinds of spending in one narration become General spending.
- **Why.** Otherwise a rail word argues against the merchant, and `KIRANA/PETROL` cancels itself out into unclassified.
- **Basis.** Code (`classify/engine.py`).

### D-40 Jev behind an interface, off by default

- **Decision.** A calibrated classification model can add signals, only through the egress gateway, and only if configured.
- **Why.** Early access with an unknown API. Calibrated probabilities fit the thresholds, and fusion works without it.
- **Cost.** It is a placeholder that has never run. Enabling it weakens reproducibility ([Known gaps](ARCHITECTURE.md#10-known-gaps)).
- **Basis.** Recorded.

### D-41 Possible rotation is never settled automatically

- **Decision.** Late, split or fee-reduced transfers, money out and back, and a habit of money passing through all go to review. Income leaves them out, not even provisionally, and `income_if_flagged_real` shows the other reading.
- **Why.** These inflated income up to six times the real figure, but each has innocent explanations, so only a person can say.
- **Cost.** Genuine income can be held until reviewed.
- **Basis.** From a bug, and recorded.

### D-42 Check rotation from most to least certain

- **Decision.** Own-account pairs, then same party, then pass-through, then splits last. Split parts must come from one other account. A pass-through needs at least three cases. Payroll-worded credits are exempt.
- **Why.** Specific findings should explain a row before a coincidental sum can. A salary swept out on payday says nothing about the salary, while a monthly pattern alone earns no protection because money passing through can look just as regular.
- **Basis.** Code (`classify/rotation.py`).

---

## Income and obligations

### D-43 Median income, with fallbacks for decline and caution

- **Decision.** Median of monthly totals. If income fell 15% or more, the recent 3-month average. A conservative figure at the 25th percentile.
- **Why.** The brief asks for sustainable income, not peak income.
- **Basis.** Recorded.

### D-44 Unconfirmed credits stay out; provisional is shown beside

- **Decision.** Income counts `auto` and `confirmed` rows only. Review rows form a separate provisional figure.
- **Why.** Never mix unconfirmed credits into the main figure. But showing ₹0 with likely receipts waiting was misleading, so the provisional figure was added.
- **Basis.** Recorded, and from a bug.

### D-45 A month awaiting review is unknown, not zero

- **Decision.** Such months leave the level, the trend and the flags.
- **Why.** A steady salary half in review read as income ₹0 and a 100% fall.
- **Basis.** From a bug.

### D-46 Months before the first entry are left out

- **Decision.** Months before the first entry do not enter the median, with a note. They raise no low-balance flags.
- **Why.** An active account showed income ₹0 because a statement period started before its first entry.
- **Basis.** From a bug.

### D-47 Business income is receipts less identified costs

- **Decision.** Receipts less suppliers, staff and tax, never below zero, with gross shown beside it. Rent and EMIs are not deducted. Those costs also leave Monthly surplus.
- **Why.** A business's receipts are not its income. Rent and EMIs are obligations counted once against this income, and nothing should be charged twice.
- **Cost.** Costs inside General spending are not taken off, so the true margin may be lower. The notes say so.
- **Basis.** Recorded, and code (`metrics/engine.py`, `config.py`).

### D-48 Cash deposits stay out until verified

- **Decision.** Excluded by default, shown as `income_if_cash_real`, with a lender setting in the code that is not exposed for editing.
- **Why.** Often real sales for a shop, but unverifiable, and the brief names large unexplained cash as a concern.
- **Cost.** Cash-heavy businesses read low.
- **Basis.** Recorded, and code.

### D-49 The profile is inferred from the rows

- **Decision.** Trusted salary in 3 or more months means salaried, otherwise self-employed. A salaried estimate counts only salary.
- **Why.** The evidence should decide, not what an applicant declared. Side income shows separately.
- **Cost.** A salaried customer with most salary still in review reads as self-employed until it is confirmed.
- **Basis.** Inferred.

### D-50 Unconfirmed EMIs count only if stable

- **Decision.** Recurring, unconfirmed obligations count when the highest month is within 1.10 times the lowest. Otherwise they are reported as pending.
- **Why.** A missed real EMI understates risk, but guessed labels on irregular payments were adding commitments.
- **Basis.** From a bug, and code (`metrics/engine.py`).

### D-51 Unclassified debits still reduce surplus

- **Decision.** Monthly surplus includes them.
- **Why.** They are money out, and leaving them out overstated what is left over.
- **Basis.** From a bug.

---

## Currencies, lenders and score

### D-52 Keep amounts as printed; convert only at supplied rates

- **Decision.** Reconcile in the account's currency. Report in INR at a rate a person enters with its source. No rate is fetched or assumed, and a row with no rate is left out and flagged.
- **Why.** Never invent. A rate in a credit file should have an accountable source.
- **Cost.** Manual entry. Official reference rates are in the backlog.
- **Basis.** Recorded, and code (`fx.py`).

### D-53 One account per currency, and a 2% matching tolerance

- **Decision.** A file with a currency column splits into one account per currency. Own-account matching across currencies allows 2%.
- **Why.** Each currency has its own balance chain, and the supplied rate is rarely the rate a bank applied.
- **Cost.** Three-decimal currencies (KWD, OMR, BHD) are rounded to two, so their chains may show breaks.
- **Basis.** Recorded, and code (`config.py`, `fx.py`).

### D-54 Lender rule engines, limited to what the problem statement names

- **Decision.** Lenders copy Standard. Only named parameters can be set, each showing its source. Every save is a new version. Senior reviewers and admins edit.
- **Why.** Review feedback asked for each bank to have its own engine, built from parameters in the problem statement. Versioning keeps every figure traceable.
- **Cost.** Lenders cannot change some thresholds they may want, such as the margin.
- **Basis.** Recorded, and code (`rule_engine.py`).

### D-55 A hand-set, explainable score, withheld when unsafe

- **Decision.** 0 to 100 from twelve named parameters, with points in a straight line between limits. It is withheld with no confirmed income, or when too little weight can be assessed.
- **Why.** Review feedback asked for a score from history, transactions and the bank's engine. A straight line makes each point easy to explain. There is no repayment data to learn from.
- **Cost.** Not a bureau score, not calibrated, not a decision. The screen and report say so.
- **Basis.** Recorded, and code (`metrics/score.py`).

### D-56 Compare engines without saving

- **Decision.** The comparison computes figures under each engine and stores nothing. Labels stay as they are.
- **Why.** A lender should see the effect before committing. An engine's labelling rules apply only once it is assigned.
- **Basis.** Code (`analysis.py`).

---

## Corrections

### D-57 Corrections are events; the original is never changed

- **Decision.** Append to `silver.overrides`, with the latest winning. Classification runs stay as they were.
- **Why.** The brief requires the original preserved, and an auditor must see who changed what, when and why.
- **Basis.** Recorded, and code (`review/overrides.py`).

### D-58 Customer memory first; wider approval for wider effects

- **Decision.** A correction writes to case-level memory only. Global memory, new categories, rules and weights are meant for senior approval.
- **Why.** A wrong global approval spreads.
- **Cost.** Lessons do not carry between cases. Promotion to global memory is not built: only rule engine edits are enforced by role today.
- **Basis.** Recorded for the design. Partial build confirmed by reading the code.

### D-59 Excluding a row is separate from relabelling it

- **Decision.** Treatments are their own events, and the category stays alongside.
- **Why.** "Leave this out" and "this is a different kind of thing" are different judgements. Clearing a treatment returns the row to where it was.
- **Basis.** Code (`metrics/effective.py`), and the Level 2 requirement.

### D-60 Retries are safe

- **Decision.** `Idempotency-Key` on case creation and overrides, a unique override `event_id`, and a file hash on upload. The same key with a different request is a 409.
- **Why.** A double click or a network retry must not apply twice.
- **Cost.** Treatments, notes and rates have no key, so a retry adds a second event.
- **Basis.** Recorded.

### D-61 A correction moves only rows that cannot decide for themselves

- **Decision.** Case memory is ignored for rows whose own evidence already passes the automatic thresholds.
- **Why.** One correction took income to ₹0 by demoting neighbouring salary rows.
- **Basis.** From a bug, and code (`classify/engine.py`).

---

## Security

### D-62 Two masked forms of each narration

- **Decision.** One for display, masked to the last four digits. One for classification, with placeholders.
- **Why.** Analysts need recognisable text. Classifiers and external models do not need values.
- **Cost.** Other people's names are not masked (see [SECURITY.md](SECURITY.md)).
- **Basis.** Recorded, and code (`security/pii.py`).

### D-63 Keyed tokens, Unicode-aware

- **Decision.** HMAC-SHA256 tokens. Identifiers are normalised keeping letters in any script. Narrations are hashed exactly.
- **Why.** A plain hash of an account number can be brute-forced. An ASCII-only filter once erased Indic scripts, so different narrations shared a token and were shown and classified as each other.
- **Basis.** Code (`security/crypto.py`), and from a bug.

### D-64 Clear values only in a vault; reveal needs a reason

- **Decision.** Fernet-encrypted values in `security.pii_vault`. Revealing a narration needs a stated reason and is logged.
- **Why.** Least exposure by default, and accountability when more is needed.
- **Basis.** Recorded.

### D-65 One gated route to any external model

- **Decision.** `egress.send` re-scans for identifiers, blocks rather than warns, logs every attempt, and is off unless enabled.
- **Why.** A single choke point can be audited, and a leak is worse than a missing signal.
- **Basis.** Code (`security/egress.py`).

### D-66 A hash-chained audit log

- **Decision.** Each entry carries the previous entry's hash.
- **Why.** Editing a past entry becomes detectable, and it costs almost nothing to build.
- **Cost.** No outside anchor. Someone able to rewrite the whole table could rebuild the chain.
- **Basis.** Recorded, and code (`security/audit.py`).

### D-67 Demo roles from headers

- **Decision.** `X-User` and `X-Role`, with a sidebar picker.
- **Why.** Lets a judge try each role with no sign-in setup. Production would use SSO claims.
- **Cost.** This is not authentication, and it must not be exposed beyond a trusted machine.
- **Basis.** Code (`security/rbac.py`).

### D-68 Scrub logs at the logging filter

- **Decision.** A filter masks personal data before any log sink, keeping the shape of its arguments.
- **Why.** Logs leak easily. Keeping the shape matters because clearing arguments broke uvicorn's access log.
- **Basis.** Code (`security/log_scrub.py`), and from a bug.

---

## Delivery and evaluation

### D-69 The built UI is committed and served by the API

- **Decision.** `frontend/dist` is meant to be committed, and FastAPI serves it.
- **Why.** Node is then needed only to change the UI, and the dev server once hung during heavy background evaluations.
- **Cost.** Build output in version control, and it must be rebuilt after any UI change.
- **Basis.** Recorded.

### D-70 Process inside the request

- **Decision.** No queue or workers.
- **Why.** About 0.65 s per statement, and a simpler system to run.
- **Cost.** Large files block other requests ([Known gaps](ARCHITECTURE.md#10-known-gaps)).
- **Basis.** Inferred.

### D-71 Pin exact dependency versions

- **Decision.** Exact versions, changed only with `scripts/verify.py` passing.
- **Why.** So an analysis can be reproduced. A library change can move a figure.
- **Basis.** Code (`backend/requirements.txt`).

### D-72 Tests as standalone scripts, including independent recomputation

- **Decision.** Each test is a script with its own temporary database. `test_invariants.py` recomputes figures from the rows, and `test_regressions.py` reproduces real failures on generated files.
- **Why.** Tests must never touch real cases. Private statements can be checked locally with only counts printed. Recomputation catches errors that a same-logic unit test would repeat.
- **Basis.** Code (`test_invariants.py`, `test_suite.py`, `scripts/verify.py`).

### D-73 Generated data with a held-out split

- **Decision.** Tune on the `multilingual_v1` dev split and report on test, whose wording differs.
- **Why.** Honest numbers. Real labelled statements were not available, and real ones are personal data.
- **Cost.** Accuracy comes from generated data written by the same people as the rules. The test split has now been read, so further gains on it should be treated with suspicion.
- **Basis.** Recorded.

### D-74 Competitor reviewed from public pages only

- **Decision.** CART (novelpatterns.ai) was looked at from public pages.
- **Why.** It is a closed SaaS, and reverse-engineering tools decompile binaries and do not apply.
- **Basis.** Recorded.
