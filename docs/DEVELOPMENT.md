# Development

How to set up, run, test and change FinSight.

## Set up

Needs Python 3.11 or newer. Node is only needed to change the UI.

```bash
python3 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
pip install -r backend/requirements-dev.txt   # pytest and playwright, for tests only
```

Or use the Makefile:

| Command | Does |
|---|---|
| `make setup` | Creates `.venv` and installs the requirements |
| `make data` | Regenerates the sample data and multilingual dataset |
| `make run` | Starts the server on 127.0.0.1:8000 |
| `make ui` | Installs and builds the UI |
| `make verify` | Runs every backend check |
| `make verify-ui` | Also clicks through the UI in Chrome |

On Windows, set `PYTHONIOENCODING=utf-8` for scripts that print ₹ or Indic text.

## Run

```bash
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Open http://localhost:8000 and choose **Load sample cases**, or run `curl -X POST http://127.0.0.1:8000/api/demo/load`.

For UI work, run `cd frontend && npm install && npm run dev`, then open http://localhost:5173. It proxies `/api`. The dev server hung once during heavy background evaluations, while the built UI on port 8000 was reliable.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `FINSIGHT_DATA_DIR` | `data/` | Where the database, files and dev keys live |
| `FINSIGHT_HMAC_KEY`, `FINSIGHT_FERNET_KEY` | Generated into `data/dev_keys.json` | Keys. Both must be set to take effect |
| `FINSIGHT_EXTERNAL_MODELS` | `0` | `1` allows the egress gateway to send |
| `JEV_API_URL`, `JEV_API_KEY`, `JEV_MODEL` | Unset | Jev. Off unless the URL and key are set and external models are allowed |

## Working rules

These protect the design. The reasons are in [DECISIONS.md](DECISIONS.md).

1. No LLM in classification scoring or arithmetic.
2. Amounts are integer paise. Parse through `money.py`, and use `Decimal` for rates.
3. Bump `RULES_VERSION` in `backend/app/config.py` whenever rules, weights or categories change. Otherwise old results are silently reused.
4. Never invent data. A missing or unconfirmed month is unknown, never zero, and what cannot be read is reported with a reason.
5. Flags say "review recommended", never "fraud".
6. Every new figure returns `included` and `excluded`, with reasons.
7. Do not delete `data/`. It holds every uploaded case, and there is no backup. Check `GET /api/cases` first and ask whoever uses the server.
8. Schema changes are additive and apply on start.
9. Tune only on the `dev` split of `datasets/multilingual_v1`, and report on `test`.
10. After changing the UI, run `npm run build` in `frontend/` and commit `frontend/dist`.

## Test

Each test makes its own temporary database, so your data is never touched.

```bash
python scripts/verify.py           # every backend check, one results table
python scripts/verify.py --quick   # skips the two slowest
python scripts/verify.py --ui      # also clicks through the UI in Chrome
pytest                             # the same checks, through test_suite.py
```

**Run `python scripts/verify.py` before committing.**

Latest full run, 2 November 2026, on the working copy:

| Check | Result | Time | Covers |
|---|---|---|---|
| `test_brief_examples` | Pass | 2 s | The brief's worked examples |
| `test_review_flow` | Pass | 7 s | Corrections, idempotency, roles, reveal logging, audit chain, re-upload |
| `test_tokens` | Pass | 0 s | The tokeniser keeps every script distinct |
| `test_regressions` | Pass | 14 s | Failures found on real statements, reproduced on generated files |
| `test_invariants` | Pass | 7 s | Every figure recomputed from its rows: nothing unaccounted for, nothing counted twice |
| `test_sample_package` | Pass | 6 s | Uploads the Riya Sharma package and checks each feature |
| `test_robustness` | Pass | 56 s | Broken files, odd formats, wrong API calls, simultaneous uploads |
| `smoke` | Pass | 7 s | Demo cases end to end, against ground truth |

The browser test `e2e_ui.py` was not part of this run. It needs playwright, `playwright install chromium` and a built UI.

A determinism check was also run for this documentation: the two Riya Sharma upload sets, each processed twice in separate databases with different Python hash seeds and the same keys. Every figure, score, flag and count of category and status matched. It is not part of `verify.py`.

### Which test to extend

| You changed | Add to |
|---|---|
| A failure found on a real statement | `test_regressions.py`, reproduced on a generated file with no personal data |
| A figure or calculation | `test_invariants.py`, recomputing it independently |
| Corrections, roles or audit | `test_review_flow.py` |
| Anything a hostile input could reach | `test_robustness.py` |
| Tokens or masking | `test_tokens.py` |
| Something the sample package should show | `test_sample_package.py` and `datagen/riya_sharma.py` |
| A screen or user flow | `e2e_ui.py` |

To check a private statement without printing its contents:

```bash
python backend/tests/test_invariants.py file1.pdf,file2.pdf file3.csv
```

Each argument is one case, with its files separated by commas. Only counts are printed. Never add real statements to the repository.

### Evaluations

These are slower and report numbers rather than pass or fail. Results are written as JSON beside the scripts.

| Command | Measures |
|---|---|
| `python backend/tests/eval_multilingual.py` | Accuracy on 16 multilingual statements. Use dev to tune and test to report |
| `python backend/tests/eval_tamper.py` | Edit-risk detection |
| `python backend/tests/eval_hf_statements.py <train dir>` | Reading the AgamiAI PDFs against their labels. The dataset is downloaded separately |
| `python backend/tests/eval_vlm_dataset.py <zip>` | The VLM dataset through the pipeline. Local only |

Latest numbers are in `EVALUATION.md`.

## Changing rules or categories

1. Edit `classify/rules.py`, or `classify/categories.py`. A category without a treatment cannot be used.
2. **Bump `RULES_VERSION`** in `config.py`, with a short comment saying what changed.
3. Run `eval_multilingual.py` and look at the **dev** split. Do not tune on test.
4. Add a case to `test_regressions.py`.
5. Run `python scripts/verify.py`.

No reset is needed: `GET /analysis` recomputes old snapshots.

A new category also needs a label and direction, handling in `metrics/engine.py` if its treatment needs anything special, and the type in `frontend/src/types.ts` if the UI names it. If a lender's keyword rules may point at it, add it to `RULE_CATEGORIES`.

## Adding a lender setting

1. Add it to `_P` in `rule_engine.py`, with its group, label, kind, default, source and help.
2. Add it to `SETTINGS`, naming the line of the problem statement it comes from. If the problem statement does not name it, it should stay a fixed default.
3. Read it from `p["..."]`, not from `config`.
4. Add any cross-field check to `validate`.

Older engines pick up new settings at their defaults. A built-in engine nobody has edited moves to a new version on start, and a lender's edits are kept.

## Adding a score parameter

Add it to `SCORE_DEFAULTS` with its weight, limits, unit and source, and return `(value, txn_ids, why_missing)` for it in `metrics/score.py`. Return `None` as the value when it cannot be measured, rather than a guess. Keep the default weights totalling 100.

## Adding a flag

Add a rule text to `rule_texts` and a `_flag(...)` call in `evaluate` in `flags/rules.py`, with the rows it rests on. Word it as a reason to review.

## Adding a figure

In `metrics/engine.py`, add an entry to `metrics` with `key`, `label`, `unit`, `value`, `method`, `included`, `excluded`, `months_used`, `months_missing` and `notes`. Use `None` for unknown. Add an independent recomputation to `test_invariants.py`, and the type to `frontend/src/types.ts`.

## Adding a file format or layout

Return an `Extracted` from a new function in `ingest/extract.py` and register it in `EXTRACTORS` and `detect_kind`. For a new PDF layout, prefer improving `ingest/layout.py` to writing bank-specific code. Add the failure to `test_regressions.py` on a generated file.

## Adding a database column

Add it to `CREATE TABLE` in `db.py` for new installs, and an `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `connect()` for existing ones. Remember that `INSERT ... VALUES` statements list every column by position.

## Versions

| Version | Bump when | Effect |
|---|---|---|
| `RULES_VERSION` | Rules, weights or categories change | Reclassifies, and recomputes on next read |
| `WEIGHTS_VERSION` | Fusion weights get their own scheme | Same |
| `PIPELINE_VERSION` | Reading or normalising changes | Recomputes on next read. **It does not re-read existing files.** Only a new upload uses the new reading code |
| `QUESTION_BANK_VERSION` | Jev's questions change | Changes the classification hash |

An engine's version and the similarity memory's version are tracked automatically.

## Regenerating sample data

| Script | Produces |
|---|---|
| `datagen/generate.py` | The demo cases, in `sample_data/` |
| `datagen/riya_sharma.py` | The Riya Sharma package |
| `datagen/multilingual.py` | `datasets/multilingual_v1/` |
| `backend/tests/eval_tamper.py --demo-out sample_data/tampered` | Edited PDFs for demos |

## Gotchas

- Changing rules without bumping `RULES_VERSION` silently reuses old classifications.
- A reset (deleting `data/`) is only needed after changing tokenisation or transaction IDs. Everything else applies in place.
- DuckDB allows one writer process. Stop the server before opening the file elsewhere, or work on a copy.
- The edit-risk flag on AgamiAI files comes from their broken arithmetic, not from edits. Look at `structure_score`.
- Recharts bars animate in for about a second, so a screenshot taken immediately looks empty.
- `test_regressions.py` and `eval_tamper.py` need `pypdf`, which is in `requirements.txt`.
- Most test files are scripts that run on import, so `pytest.ini` collects only `test_suite.py`.
