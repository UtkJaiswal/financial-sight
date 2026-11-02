# Calculations

How each figure, indicator and score is worked out, in `backend/app/metrics/` and `backend/app/flags/`. The same rule applies to all of them: each lists the rows it included and excluded, with reasons.

Parameter names such as `income_max_months` are rule engine settings or fixed defaults (see [Settings](#settings)).

## Which rows count

| Term | Meaning |
|---|---|
| Trusted | Status `auto` or `confirmed` |
| Held | Possible rotation, not yet decided by an analyst |
| Fully covered month | Every day of the month falls inside a statement |
| Active month | On or after the first entry in the case |
| Adjusted | Leaves out categories treated as `excluded`: transfers, reversals, loan proceeds, returned payments and analyst exclusions |

A row with no exchange rate is left out of every figure. A foreign row with a rate is converted first.

## Estimated monthly income

**Profile.** Trusted salary in 3 or more months means `SALARIED`, otherwise `SELF_EMPLOYED`. A salaried estimate counts only salary. A self-employed estimate counts salary and business receipts.

**Which months.** Fully covered months on every account holding income, from the first entry on those accounts, up to the latest `income_max_months`. A month whose only income is still in review is left out as unknown, unless too little is confirmed to say anything.

**How each credit is treated:**

| Credit | Treatment |
|---|---|
| Analyst said include | Counted |
| Analyst said exclude | Left out, with the analyst's reason |
| Held as possible rotation | Left out, not even provisional |
| Income category, trusted, in a usable month | Counted |
| Income category, in review | Left out, and added to the provisional figure |
| Income category, trusted, in an incomplete month | Left out, with that reason |
| Cash deposit | Left out until verified, unless the engine counts cash |
| Excluded category | Left out as "not income" |
| Other income category, such as a salaried customer's receipts | Left out, reported as variable income |
| Anything else | Left out as "not counted as income" |

**Business costs.** For self-employed customers, each month's suppliers, staff and tax are taken off receipts. Rent and EMIs are not: they are obligations.

**The figure:**

1. `series` = counted receipts minus costs, for each month.
2. Take the median of `series`, or the average if the engine says so.
3. With 6 or more months, if receipts in the last 3 months fell at least `income_fall_pct` (15%) against the 3 before, and the recent average is lower, use the recent 3-month average. The method text says so.
4. Below zero shows as zero, with a note.
5. With fewer than `income_min_months`, no figure is given.

**Shown beside it:**

| Field | Meaning |
|---|---|
| `conservative` | 25th percentile: reached in at least three of every four months |
| `provisional` | If everything in review were confirmed |
| `income_if_flagged_real` | If everything held as rotation were real as well |
| `income_if_cash_real` | If all cash deposits were real sales |
| `gross_receipts_monthly`, `business_costs_monthly` | The two sides of a business's figure |
| `regular_monthly`, `variable_monthly` | Salary and monthly series, against the rest |
| `cv` | Coefficient of variation of receipts |
| `consistency` | `consistent` under 0.15, `moderately variable`, or `highly variable` at `income_volatile_cv` (0.50) and above |
| `unusual_months` | A month at least 1.5 times the median, or at most the median divided by 1.5 |

## Recurring obligations

For each pair of EMI or rent category and counterparty, among debits not held:

| Test | Rule |
|---|---|
| Recurring | At least `obligation_min_months` (2), and seen in the latest two months |
| Active | Recurring, and either trusted or stable (highest month within 1.10 times the lowest) |
| Pending | Recurring but neither. Reported separately, not counted |

The figure is the sum of each active commitment's median monthly amount. A returned EMI is left out as not paid.

## FOIR and affordability

| Figure | Formula |
|---|---|
| FOIR | Recurring obligations ÷ estimated income. No figure when income is zero or unknown |
| Surplus after obligations | Estimated income − recurring obligations |
| Checks | FOIR ≤ `max_foir` (50%) and surplus ≥ `min_surplus_after_obligations_paise` (₹10,000) |
| Average monthly surplus | Mean of each covered month's income minus its obligations |

## Monthly surplus

Estimated income minus typical monthly outflows.

Outflows are, per category, total debits over fully covered months divided by the number of those months, so a one-off bill is spread rather than inflated. They cover expense and obligation categories, and unclassified debits. Held rows are left out, and so are business costs already taken off income.

## Net cash flow

For each fully covered active month: adjusted credits − adjusted debits. The figure is the mean of those months.

## Balances

`daily_balances` builds an end-of-day balance per account from the running balances.

| Figure | Formula |
|---|---|
| Average daily balance | Mean of the combined balance, on days when every account is covered |
| Balance trend | (last month's average − first month's) ÷ first month's |

## Cash-flow trend

Needs at least 3 fully covered active months. The months split into earlier and later groups, with the later group holding half.

`change = later average − earlier average`. It is `improving` above `cash_flow_trend_share` (10%) of average monthly inflows, `deteriorating` below minus that, and otherwise `stable`. Up to two later months that moved it most are highlighted.

## Money movement

Trusted transfers between own accounts plus held credits, also shown as a share of all credits. `money_flows` lists transfers, circles, parties and pass-through for the Overview card.

## Earnings

`metrics/history.py:earnings` gives, by month: receipts, costs, earned and a running total. It also gives the average, the last 3 months' average, a yearly rate (average × 12), the best and lowest months, who pays and each payer's share. It marks income as concentrated when one payer reaches `customer_concentration_share` (60%) over at least 3 months.

## Statements to ask for

`metrics/history.py:statement_requests` lists transfers that point at an account not in the case, because they cannot be matched or excluded with certainty until its statement arrives:

1. Unpaired rows naming the holder or an own account, grouped by the account number or bank code they mention.
2. For salaried customers, a steady monthly debit of at least 10% of income, to a party with no clearer label.

The list clears once the statement arrives and the transfers pair up.

## Credit score

`metrics/score.py`. Not a bureau score, not calibrated against repayment, and not a credit decision. The screen and report say so.

Each parameter is measured from the rows and earns points in a straight line:

```
share  = (value − zero_at) / (full_at − zero_at), held between 0 and 1
points = weight × share
score  = 100 × points earned / weight that could be assessed
```

A parameter that cannot be measured is left out and named, never assumed.

**The score is withheld** when no income is confirmed, or when the assessed weight is below `min_assessed_weight` (60%). The reason is shown.

**Bands by default:** Strong from 75, Moderate from 55, Weak from 35, High concern below that.

**Standard defaults** (weights total 100):

| Parameter | Weight | Full points at | Zero points at | Key |
|---|---|---|---|---|
| Estimated monthly income | 15 | ₹1,00,000 | ₹0 | `estimated_income` |
| Recurring obligations, as a share of income | 15 | 30% | 70% | `recurring_obligations` |
| Net cash flow, as a share of monthly credits | 15 | 20% | -10% | `net_cash_flow` |
| Returned payments | 10 | 0 | 3 | `returned_payments` |
| Possible rotation, as a share of credits | 10 | 0% | 30% | `possible_rotation` |
| How much monthly credits vary | 5 | 0.15 | 0.75 | `monthly_credits` |
| Monthly debits, as a share of monthly credits | 5 | 70% | 110% | `monthly_debits` |
| Change in average balance, first month to last | 5 | 0% | -50% | `balance_trend` |
| Share of days with a low balance | 5 | 2% | 25% | `low_balance` |
| Large unexplained deposits, as a share of credits | 5 | 0% | 30% | `unexplained_deposits` |
| Months of statement history | 5 | 6 | 2 | `history_months` |
| Share of statements within reconciliation rules | 5 | 100% | 50% | `reconciliation` |

Where FOIR cannot be worked out because income is zero while costs and obligations take everything, it scores as 100%.

The limits are starting values set by hand. Each lender can change them.

## Edit risk

`validate/tamper.py`. The score is the sum of the weights below, capped at 100. Levels: `high` from 60, `medium` from 30, `low` above 0, otherwise `none`. A CSV, Excel or JSON file carries no signal to verify, and the report notes that.

| Signal | Weight | Meaning |
|---|---|---|
| `editor_producer` | 35 | Produced or re-saved by PDF editing software |
| `incremental_update` | 35 | Saved again after first being produced |
| `overlapping_text` | 35 | Different text printed at the same position |
| `font_mismatch` | 30 | An amount's font or size differs from the rest of its table |
| `unreconciled` | 25 | Opening + credits − debits differs from the stated closing |
| `office_producer` | 20 | Produced by an office application |
| `modified_after_creation` | 15 | Modified more than 2 minutes after creation |
| `balance_break` | 10 per row, up to 20 | The running balance does not follow |

`structure_score` leaves out the two arithmetic signals, so broken arithmetic is not mistaken for an edit.

## Risk indicators

`flags/rules.py`. Each says "review recommended", with its rule and rows. Limits shown are Standard defaults. "Varies" means the severity depends on the finding.

| Code | Severity | Rule |
|---|---|---|
| `account_missing_months` | medium | An account has no statement for a month that other statements in this case cover. |
| `behaviour_change` | medium | The latest covered month has inflows, outflows or transaction count at least double or at most half the typical earlier month. |
| `circular_flow` | medium | Matched movements of about the same amount, each within 7 days of the last, returning to the first account. |
| `coverage_gap` | medium | Dates between two statements of the same account that no statement covers. |
| `customer_concentration` | medium | One payer accounts for at least 60% of counted income, over at least 3 months. |
| `duplicate_upload` | info | The same file was uploaded again. It is processed once. |
| `file_not_processed` | high | A file yielded no transactions or could not be opened. |
| `foreign_currency_income` | info | Part of estimated income is in a foreign currency. |
| `fx_rate_missing` | high | A foreign-currency row has no supplied exchange rate for its month. |
| `income_decline` | varies | Income in the latest covered months (up to 3) averages at least 20% below the same number of months before. 35% or more is high. |
| `income_unusual_month` | low | A month's income is at least 1.5 times the median, or at most the median divided by 1.5. |
| `income_volatility` | varies | Monthly income's coefficient of variation is at least 0.50, over at least 3 covered months. |
| `large_cash_deposit` | medium | Cash deposit of at least ₹2,00,000.00, or at least ₹1,00,000 and three times the median credit. |
| `large_unexplained_deposit` | medium | Credit of at least ₹1,00,000.00 with no confirmed explanation. |
| `low_balance` | medium | End-of-day balance below ₹5,000.00 on at least 8% of covered days. |
| `missed_obligation` | medium | A monthly EMI or rent series has no payment in a fully covered month. |
| `missed_salary` | high | A monthly salary series has no salary credit in a fully covered month. |
| `multiple_large_credits` | medium | Two or more non-routine credits, each at least ₹50,000 and at least 1.5 times estimated monthly income. |
| `negative_net_cash_flow` | medium | Adjusted outflows exceed inflows in at least 2 fully covered months. |
| `overlapping_statements` | info | Two statements of the same account cover some of the same dates. Shared rows count once. |
| `page_not_processed` | high | A page has no text layer, or some of its rows could not be read. |
| `possible_document_edit` | varies | Edit-risk score of 30 or more from the PDF's structure and arithmetic: editor or office software as producer, incremental saves, modified after creation, mismatched fonts in a table, text printed over other text, unreconciled totals or broken balance chains. |
| `possible_rotation` | varies | A similar amount across own accounts within 7 days, split into 2 or 3 entries, out and back with one party within 15 days, or repeatedly leaving within 3 days of arriving. |
| `reconciliation` | medium, sev | Opening balance plus credits minus debits differs from the stated closing balance, or a row breaks the running balance. |
| `request_statement` | medium | Transfers name the customer's own name or another account of theirs with no matching entry, or a salaried customer sends a steady sum out each month. |
| `returned_payment` | high | Any payment returned unpaid, or a charge for one. |
| `review_queue` | low | Evidence below 90% confidence or conflicting, so an analyst decides. |
| `rising_expenses` | medium | Average monthly outflows in the later half of covered months are at least 25% above the earlier half. |
| `round_figure_pre_application` | medium | Round-figure credit of at least ₹50,000.00 within 30 days before the application. |
| `several_salary_payers` | medium | More than 2 different payers made salary credits in one month. |
| `statement_balance_mismatch` | medium | A statement's opening balance differs from the balance carried from the statement before it. |

## Settings

A lender can edit these. Each shows its source in the problem statement on the Rule engines page.

| Group | Setting | Key | Standard |
|---|---|---|---|
| Reconciliation | Allowed difference at closing | `recon_tolerance_paise` | ₹0 |
| Reconciliation | Closing balance must be stated | `recon_require_closing` | off |
| Reconciliation | Running-balance breaks allowed | `recon_max_row_breaks` | 0 |
| Estimated income | Monthly figure | `income_method` | median |
| Estimated income | Months of history used | `income_max_months` | 12 |
| Estimated income | Months needed for a figure | `income_min_months` | 1 |
| Obligations and affordability | Months before a payment counts as recurring | `obligation_min_months` | 2 |
| Obligations and affordability | Most obligations can be of income | `max_foir` | 50% |
| Obligations and affordability | Least surplus after obligations | `min_surplus_after_obligations_paise` | ₹10,000 |
| Classification | Days apart for a certain own-account transfer | `transfer_max_days` | 1 |
| Classification | Days apart for a refund or reversal | `reversal_max_days` | 30 |
| Risk indicators | Low balance below | `low_balance_paise` | ₹5,000 |
| Risk indicators | Share of days that counts as frequent | `low_balance_days_share` | 8% |
| Risk indicators | Negative months that count as sustained | `negative_cash_flow_months` | 2 |
| Risk indicators | Income variation that counts as high | `income_volatile_cv` | 0.5 |
| Risk indicators | Large unexplained deposit from | `unexplained_deposit_paise` | ₹1,00,000 |
| Classification | Confidence for an automatic label | `auto_min_p` | 90% |
| Classification | Confidence for a suggestion | `review_min_p` | 60% |

Other thresholds, including the rotation limits, the margin and the similarity floor, are fixed in `rule_engine.py` and `config.py`. The API refuses any attempt to set them.

Two sample engines show the range:

| Engine | How it differs |
|---|---|
| Conservative bank (sample) | Closing balance required, 6 months needed, automatic labels at 95%, FOIR limit 40%, surplus ₹25,000, low balance ₹10,000, tighter score limits |
| SME growth lender (sample) | Averages instead of medians, ₹100 reconciliation allowance, one balance break allowed, FOIR limit 65%, surplus ₹5,000, more weight on cash flow |
