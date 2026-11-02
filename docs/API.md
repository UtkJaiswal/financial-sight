# API

The routes in `backend/app/main.py`. While the server runs, generated documentation is at http://localhost:8000/docs.

## Conventions

| Item | Detail |
|---|---|
| Base | `http://localhost:8000` |
| Format | JSON, apart from file upload (multipart) and the report PDF |
| Money | Integer paise. A `credit` of `6000000` is ₹60,000.00 |
| Dates | ISO 8601 |
| Identity | Headers `X-User` and `X-Role`. With neither, you are `analyst.demo`, an analyst. **This is not authentication** (see [SECURITY.md](SECURITY.md)) |
| Retries | `Idempotency-Key` on the routes marked below |

## Roles

| Permission | Analyst | Senior | Auditor | Admin |
|---|---|---|---|---|
| `create_case`, `upload`, `override`, `reveal` | Yes | Yes | | Yes |
| `view_audit` | | Yes | Yes | Yes |
| `edit_engine` | | Yes | | Yes |

Routes marked "none" below have no role check. An auditor's read-only access applies to the checked routes. Nothing restricts reads.

## Errors

| Status | Meaning |
|---|---|
| 400 | Unknown role |
| 403 | The role lacks this permission |
| 404 | Case, transaction, engine or snapshot not found |
| 409 | `Idempotency-Key` already used with a different request |
| 422 | Invalid input. `detail` says which and why |
| 500 | Unexpected. A plain message, with detail in the server log |

All errors have the form `{"detail": "..."}`.

## Routes

### Meta

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/api/health` | none | Liveness |
| GET | `/api/meta` | none | Categories, roles, versions, thresholds, Jev state |

### Cases and files

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/api/cases` | none | List, with summaries |
| POST | `/api/cases` | `create_case` | Create. `Idempotency-Key` accepted |
| GET | `/api/cases/{case_id}` | none | One case |
| POST | `/api/cases/{case_id}/files` | `upload` | Upload one file, with an optional `password`. Runs the whole pipeline |
| GET | `/api/cases/{case_id}/files` | none | Files, pages, quarantine, upload events, cross-checks |
| POST | `/api/demo/load` | `upload` | Load the sample cases. Safe to repeat |

### Results

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/api/cases/{case_id}/analysis` | none | The current snapshot. Recomputes first if versions changed |
| GET | `/api/cases/{case_id}/snapshots` | none | Snapshot list |
| GET | `/api/cases/{case_id}/diff?from_id=&to_id=` | none | What changed between two snapshots |
| GET | `/api/cases/{case_id}/report` | none | The report as data |
| GET | `/api/cases/{case_id}/report.pdf` | none | The report as a PDF |
| GET | `/api/cases/{case_id}/evaluation` | none | Accuracy against ground truth, for sample cases |

### Transactions and decisions

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/api/cases/{case_id}/transactions` | none | All rows in effect, masked, with flags |
| GET | `/api/cases/{case_id}/transactions/{txn_id}` | none | Reason, signals, history, sources, links, overrides, treatments, notes, patterns |
| POST | `/api/cases/{case_id}/transactions/{txn_id}/override` | `override` | Correct a category. `Idempotency-Key` accepted |
| POST | `/api/cases/{case_id}/transactions/{txn_id}/treatment` | `override` | Exclude, include or clear |
| POST | `/api/cases/{case_id}/transactions/{txn_id}/reveal` | `reveal` | The clear narration. Logged |
| GET | `/api/cases/{case_id}/notes` | none | Notes |
| POST | `/api/cases/{case_id}/notes` | `override` | Add a note. Changes no figure |

### Exchange rates

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/api/cases/{case_id}/fx-rates` | none | Rates in force |
| POST | `/api/cases/{case_id}/fx-rates` | `override` | Set a rate. An empty `rate` removes it |

### Rule engines

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/api/rule-engines` | none | Engines, editable settings, score parameters, allowed categories |
| GET | `/api/rule-engines/{engine_id}` | none | One engine and its version history |
| POST | `/api/rule-engines` | `edit_engine` | Create, usually as a copy |
| PUT | `/api/rule-engines/{engine_id}` | `edit_engine` | Save a new version. Refused for Standard: copy it instead |
| DELETE | `/api/rule-engines/{engine_id}` | `edit_engine` | Delete. Refused for Standard, or if cases use it |
| POST | `/api/cases/{case_id}/engine` | `override` | Assign an engine. Returns the diff |
| GET | `/api/cases/{case_id}/engine-comparison` | none | The case under every engine. Saves nothing |

### Audit

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/api/audit?case_id=` | `view_audit` | Latest 300 entries, and the chain check |

Any other path serves the built UI when `frontend/dist` exists. Paths under `/api/` return 404 instead.

## Request bodies

| Route | Body | Rules |
|---|---|---|
| Create case | `{"name", "application_date", "profile"}` | All optional. The date looks like `2026-10-05` and defaults to today |
| Upload | Multipart `file`, optional `password` | `.pdf`, `.csv`, `.xlsx`, `.json`, up to 15 MB |
| Override | `{"category", "reason", "event_id"}` | `reason` at least 3 characters. The category must apply to the row's direction. `event_id` is optional |
| Treatment | `{"action", "reason"}` | `action` is `exclude`, `include` or `clear`. `reason` at least 3 characters |
| Note | `{"text", "txn_id"}` | `text` 3 to 2000 characters. Without `txn_id` it applies to the case |
| Reveal | `{"reason"}` | At least 5 characters |
| Rate | `{"currency", "month", "rate", "source"}` | A three-letter foreign code. `month` looks like `2026-07`, or is empty for every month. `source` at least 3 characters |
| Create engine | `{"name", "based_on", "description"}` | |
| Save engine | `{"config", "note", "name", "description"}` | Validated by `rule_engine.validate`. Unknown settings are refused |
| Assign engine | `{"engine_id"}` | |

## Examples

Create a case, upload a statement and read the income:

```bash
BASE=http://127.0.0.1:8000
CASE=$(curl -s -X POST $BASE/api/cases -H 'Content-Type: application/json' \
  -d '{"name":"Test applicant","application_date":"2026-10-05"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["case_id"])')

curl -s -X POST $BASE/api/cases/$CASE/files -F file=@statement.pdf

curl -s $BASE/api/cases/$CASE/analysis | python3 -c '
import json, sys
m = json.load(sys.stdin)["metrics"]["estimated_income"]
print(m["value"], m["method"])'
```

Correct a row safely, so that a retry does nothing:

```bash
curl -s -X POST $BASE/api/cases/$CASE/transactions/$TXN/override \
  -H 'Content-Type: application/json' -H 'X-User: asha' -H 'X-Role: analyst' \
  -H 'Idempotency-Key: 7f3c1e' \
  -d '{"category":"SALARY","reason":"Employer confirmed by payslip"}'
```

The response holds `noop`, `reason`, `snapshot_id` and `diff`. `diff` lists the figures, other rows and flags that changed.

## Response shape for a figure

Every figure in `metrics` has this shape, so any figure can be traced the same way:

```json
{
  "key": "estimated_income",
  "label": "Estimated monthly income",
  "unit": "paise",
  "value": 6000000,
  "method": "median of 6 monthly salary totals over fully covered months",
  "included": ["t_..."],
  "excluded": [{"txn_id": "t_...", "reason": "Internal transfer: not income"}],
  "months_used": ["2026-01"],
  "months_missing": [],
  "notes": []
}
```

Values are illustrative. `value` can be `null`, which means unknown, not zero.
