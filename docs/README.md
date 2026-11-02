# FinSight documentation

FinSight reads bank statements, reconciles every row, classifies each transaction with a visible reason, and produces income, obligations, cash-flow trends, risk indicators, a credit score and a report that links back to source rows. Rules and arithmetic decide. An analyst can correct anything, and the original is kept.

## Where to look

| You want to | Read |
|---|---|
| Understand how the system fits together | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Know why something was built the way it was | [DECISIONS.md](DECISIONS.md) |
| Understand why a row got its category | [CLASSIFICATION.md](CLASSIFICATION.md) |
| Check how a figure, score or flag is worked out | [CALCULATIONS.md](CALCULATIONS.md) |
| Query the database or trace a figure to its source | [DATA_MODEL.md](DATA_MODEL.md) |
| Call the API | [API.md](API.md) |
| Review how personal data is protected, and what is not done yet | [SECURITY.md](SECURITY.md) |
| Set up, run, test or change the code | [DEVELOPMENT.md](DEVELOPMENT.md) |

Other documents, in the repository root:

| File | What it holds |
|---|---|
| [`README.md`](../README.md) | Install, a five-minute tour, honest limits |
| `EVALUATION.md` | Each requirement in the brief, with its code, its test and how to see it. Measured accuracy |
| [`HANDOFF.md`](../HANDOFF.md) | The original build notes: results, bugs found, backlog |
| `AGENTS.md` | Short working rules for coding agents |
| `sample_data/riya_sharma/README.md` | What each sample file should show |

## What these documents describe

They describe the working copy on 2 November 2026: pipeline version `p4`, rules `r9`, weights `w0`, question bank `q1`.

When a document and the code disagree, trust the code, then fix the document. Differences already found are listed in [Known gaps](ARCHITECTURE.md#10-known-gaps).

Reference tables for rules, categories, flags, settings and score parameters were generated from the code, not typed by hand. Measured figures say what they were measured on.

## Words used throughout

| Term | Meaning |
|---|---|
| Case | One applicant's review. It holds their files, accounts and decisions |
| Paise | Hundredths of a rupee. For a foreign account, hundredths of that currency |
| Narration | The description text a bank prints on a row |
| Own account | An account the customer holds, seen because its statement is in the case |
| Gate | A hard fact that decides a row outright, such as a matched transfer pair |
| Signal | One piece of evidence for a category, with a weight |
| Review | A suggested category that waits for an analyst and stays out of income |
| Override | An analyst's correction, stored as an event |
| Treatment | An analyst's decision to exclude or count a row, stored as an event |
| Rule engine | One lender's settings, versioned |
| Snapshot | One complete, stored set of results for a case |
| FOIR | Fixed obligations to income ratio: recurring obligations divided by estimated monthly income |
| EMI | Equated monthly instalment: a fixed loan repayment |
| Rotation | The same money moving around so that it looks like more income |
| Bronze, silver, gold | Storage layers: as read, cleaned and decided, and computed results |
