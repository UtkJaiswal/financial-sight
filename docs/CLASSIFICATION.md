# Classification

How a transaction gets its category, in `backend/app/classify/`. Figures that use these categories are in [CALCULATIONS.md](CALCULATIONS.md).

## Inputs

The classifier works on `description_norm`, the upper-case narration with personal values replaced by placeholders such as `<HOLDER>`, `<OWN_ACCT>`, `<UPI>`, `<PHONE>` and `<NUM>`. It also uses the amount, date, direction, channel and counterparty.

Direction matters throughout: a category only applies to credits (C), debits (D) or both (B).

## Tier 1: hard gates

Gates decide a row outright with probability 1.0, and they are never averaged with anything.

| Gate | Condition | Result |
|---|---|---|
| Bounce charge | Debit naming a return and a charge | `BANK_CHARGES` |
| Returned payment | Credit naming a return, with context such as `ACH`, `CHQ` or `INSUFF` | `BOUNCE_RETURN` |
| Bounce pair | A debit of the same amount, same account, up to 3 days before that return | `BOUNCE_RETURN`, linked |
| Own-account transfer | Equal debit and credit in two of the customer's own accounts, within `transfer_max_days` (default 1). Nearest day wins. Across currencies, within 2% at the supplied rate | `INTERNAL_TRANSFER`, linked |
| Reversal pair | A row with reversal wording, plus an opposite row of the same amount in the same account up to `reversal_max_days` (default 30) earlier. A shared reference number is preferred, then the nearest date | `REFUND_REVERSAL`, linked |

Reversals work both ways: a refunded debit and a reversed credit.

An own-account transfer can only be matched when both statements are in the case. That is why the app asks for missing statements (see `statement_requests` in [CALCULATIONS.md](CALCULATIONS.md#statements-to-ask-for)).

## Tier 2: weighted evidence

Each signal adds a value to one category. Scores are summed per category, and the category baseline for that direction (Other credit, or General spending) is always a candidate at 0 unless evidence adds to it.

```
score(category) = sum of its signal values
P(category)     = exp(score) / sum of exp(score) over all candidates
margin          = P(top) − P(second)
```

### Signals

| Signal | Value | Fires when |
|---|---|---|
| `rule:<id>` | The rule's weight (table below) | A keyword rule matches, in the right direction |
| `rule:custom` | 0.5 to 6, set by the lender | A lender's keyword rule matches |
| `rule:R_MIXED_SPEND` | 2.7 for General spending | The narration names more than one kind of spending. It replaces those signals |
| `entity:lender` | 1.5 | A known lender is named: EMI on a debit, disbursal on a credit |
| `entity:utility` | 1.8 | A known utility is named |
| `entity:own_bank` | 1.0 for transfer | It names a bank where the customer holds another account |
| `account:trading_pattern` | 1.3 for business receipt | The account averages 6 or more different payers a month |
| `periodic:several_payers` | 1.6 for business receipt | One of several regular payers on the account, without payroll wording |
| `periodic:monthly_credit` | 1.4 for salary | Part of a monthly credit series. On a trading account, or with business wording on the row, this needs payroll wording in the series |
| `periodic:stable_amount` | 0.8 for salary | That series varies less than 10% |
| `periodic:series_payroll` | 2.0 for salary | This row has no payroll word, but at least 34% of its series' rows do |
| `periodic:fixed_monthly_debit` | 0.9 | Varies less than 2%, and only if EMI or rent already has evidence |
| `memory:global`, `memory:case` | 1.5 × similarity | A similar approved example at similarity 0.50 or above |
| `jev:*` | 0.6 × log-odds | Only if Jev is configured. Off by default |

### How much evidence decides alone

With one signal against the baseline, `P = exp(w) / (exp(w) + 1)`:

| Weight | P | Result on its own |
|---|---|---|
| 0.4 or less | Under 0.60 | Unclassified |
| 0.41 to 2.19 | 0.60 to 0.90 | Review |
| 2.2 or more | Over 0.90 | Automatic |

So `R_SAL_KW` (2.4) can decide alone, while `R_MANDATE` (1.3) cannot: `NACH` alone suggests an EMI, and with `EMI` or `LOAN` as well (1.2) it reaches 2.5 and decides.

When the only signal is for the baseline itself, there is no competitor and P is 1.0. This is why a plain UPI debit becomes General spending automatically.

### Adjustments

- **Payroll words name an employer.** With a payroll keyword, `R_COMPANY_PAY` is skipped: the company named is the employer, not a customer.
- **School and hospital fees are not bank charges.** `R_CHARGES` is skipped when education or medical wording matches.
- **A named merchant beats the rail.** `R_UPI_SPEND` is dropped when a specific spending rule fires.
- **Settled rows ignore case memory.** A row whose own evidence already passes the automatic thresholds ignores case-level examples, so a correction never moves it.

### Keyword rules

From `classify/rules.py`. Direction C is credit, D is debit, B is both.

| Rule | Direction | Category | Weight |
|---|---|---|---|
| `R_SAL_KW` | C | `SALARY` | 2.4 |
| `R_STAFF_SAL` | D | `PAYROLL_PAYMENT` | 2.8 |
| `R_MANDATE` | D | `LOAN_EMI` | 1.3 |
| `R_EMI_KW` | D | `LOAN_EMI` | 1.2 |
| `R_DISB` | C | `LOAN_DISBURSAL` | 3.2 |
| `R_RENT` | D | `RENT` | 2.9 |
| `R_UTIL` | D | `UTILITIES` | 1.8 |
| `R_CARD` | D | `CARD_PAYMENT` | 2.9 |
| `R_INS` | D | `INSURANCE` | 2.6 |
| `R_INV` | D | `INVESTMENT` | 2.8 |
| `R_TAX` | D | `TAX` | 2.9 |
| `R_CASH_DEP` | C | `CASH_DEPOSIT` | 3.4 |
| `R_CHQ_CLG_CR` | C | `BUSINESS_RECEIPT` | 1.8 |
| `R_COMPANY_PAY` | C | `BUSINESS_RECEIPT` | 1.2 |
| `R_ATM` | D | `CASH_WITHDRAWAL` | 3.4 |
| `R_INTEREST` | C | `INTEREST` | 3.2 |
| `R_CHARGES` | D | `BANK_CHARGES` | 2.6 |
| `R_SUPPLIER` | D | `SUPPLIER_PAYMENT` | 2.2 |
| `R_BIZ_IN` | C | `BUSINESS_RECEIPT` | 2.2 |
| `R_BIZ` | C | `BUSINESS_RECEIPT` | 2.0 |
| `R_CLIENT` | C | `BUSINESS_RECEIPT` | 2.6 |
| `R_FOOD` | D | `FOOD_DINING` | 2.9 |
| `R_GROCERY` | D | `GROCERIES` | 2.7 |
| `R_SHOPPING` | D | `SHOPPING` | 2.7 |
| `R_TRAVEL` | D | `TRAVEL` | 2.7 |
| `R_FUEL` | D | `FUEL` | 2.7 |
| `R_MEDICAL` | D | `MEDICAL` | 2.7 |
| `R_EDUCATION` | D | `EDUCATION` | 2.9 |
| `R_INVEST_IN` | C | `OTHER_CREDIT` | 2.6 |
| `R_SELF` | B | `INTERNAL_TRANSFER` | 4.2 |
| `R_UPI_SPEND` | D | `GENERAL_EXPENSE` | 0.8 |
| `R_REVERSAL` | B | `REFUND_REVERSAL` | 3.6 |

Patterns include Hindi, Hinglish, Gujarati and Tamil words where the rule has them.

**Lenders:** BAJAJ FINANCE, BAJAJ FINSERV, TATA CAPITAL, HDFC LTD, HOME CREDIT, FULLERTON, MUTHOOT, L&T FINANCE, ADITYA BIRLA FIN, IDFC FIRST, HERO FINCORP, MAHINDRA FINANCE, SHRIRAM, PIRAMAL, LIC HOUSING.

**Utilities:** BESCOM, TNEB, TANGEDCO, MSEDCL, TATA POWER, ADANI ELECTRICITY, AIRTEL, JIO, VI POSTPAID, ACT FIBERNET, BSNL, MAHANAGAR GAS, INDANE, BWSSB.

### Monthly series

`classify/periodic.py` finds repeated payments per account, among rows of ₹500 or more that no gate decided:

1. Group by counterparty, with name similarity of 88 or more after removing digits, legal suffixes and month words.
2. Merge groups that share no months, with amounts within 8% and the same day of the month ±4. This keeps a salary recognisable when an employer renames itself.
3. Keep a series with at least 3 months and at most 1.5 entries a month.

The account's fully covered months give each series its missed months, which feed the missed salary and missed obligation flags.

### Similar examples

`classify/semantic.py` holds approved narrations: seeded generic examples at global scope, and analyst corrections at case scope.

- Text is cleaned first: rail words, digits, placeholders and IFSC codes are removed.
- Matching uses TF-IDF on 3 to 5 character n-grams, with cosine similarity to the top 5 examples.
- A match below 0.50 is ignored.

## Tier 3: policy

| Condition | Status | Category |
|---|---|---|
| No evidence, credit of ₹10,000 or more | `unclassified` | Unclassified |
| No evidence, debit in a script the rules cannot read | `review` | General spending, as a placeholder |
| No evidence, debit of ₹25,000 or more | `review` | General spending, as a placeholder |
| No other evidence | `auto` | Baseline for the direction |
| P ≥ 0.90, margin ≥ 0.30, and similar examples do not disagree | `auto` | Top category |
| P ≥ 0.60 | `review` | Top category |
| Otherwise | `unclassified` | Unclassified |

"Disagree" means an example at similarity 0.60 or more points to a different category. A case-level example does not count against a settled row.

Afterwards, if more than two different payers made salary credits in one account in one month, those rows move to review.

The limits for P, the amount thresholds and the payer count come from the case's rule engine. Only the P limits for automatic labels and suggestions can be edited by a lender.

## Rotation check

`classify/rotation.py` runs last, on rows no gate decided, comparing in rupees. Every finding moves the row to `review` with the attribute `possible_rotation`, and income leaves it out until someone decides.

| Order | Check | Default limits |
|---|---|---|
| 1 | One debit and one credit across own accounts, late or short by a fee | ₹25,000 or more, −1 to 7 days apart, fee up to ₹1,000 and 0.5%. Across currencies, within 2% |
| 2 | Out to a party and back from the same party, in either order | ₹25,000 or more, within 2%, within 15 days, name similarity 88 or more |
| 3 | A habit of money leaving as it arrives | ₹50,000 or more, within 1%, within 3 days, at least 3 cases on the account. Payroll-worded salary is exempt |
| 4 | One entry split into 2 or 3 across one other account | ₹25,000 or more, each part ₹5,000 or more, within 7 days. Parts must be General spending, Unclassified, Other credit, Business receipt or Internal transfer, not a clear supplier or rent payment |

Rows in the own-account findings are suggested as transfers. The others keep their label.

## Categories

| Category | Label | Direction | Treatment in figures |
|---|---|---|---|
| `SALARY` | Salary | C | income |
| `BUSINESS_RECEIPT` | Business receipt | C | income |
| `INTERNAL_TRANSFER` | Internal transfer | B | excluded |
| `REFUND_REVERSAL` | Refund / reversal | B | excluded |
| `LOAN_DISBURSAL` | Loan disbursal | C | excluded |
| `BOUNCE_RETURN` | Bounced / returned | B | excluded |
| `LOAN_EMI` | Loan EMI | D | obligation |
| `RENT` | Rent | D | obligation |
| `UTILITIES` | Utilities | D | expense |
| `CARD_PAYMENT` | Credit card payment | D | expense |
| `INSURANCE` | Insurance | D | expense |
| `INVESTMENT` | Investment / SIP | D | expense |
| `TAX` | Tax / GST | D | expense |
| `SUPPLIER_PAYMENT` | Supplier payment | D | expense |
| `PAYROLL_PAYMENT` | Staff salary paid | D | expense |
| `CASH_WITHDRAWAL` | Cash withdrawal | D | expense |
| `BANK_CHARGES` | Bank charges | D | expense |
| `FOOD_DINING` | Food & dining | D | expense |
| `GROCERIES` | Groceries | D | expense |
| `SHOPPING` | Shopping | D | expense |
| `TRAVEL` | Travel | D | expense |
| `FUEL` | Fuel | D | expense |
| `MEDICAL` | Medical | D | expense |
| `EDUCATION` | Education | D | expense |
| `GENERAL_EXPENSE` | General spending | D | expense |
| `CASH_DEPOSIT` | Cash deposit | C | review_credit |
| `INTEREST` | Interest | C | other_credit |
| `OTHER_CREDIT` | Other credit | C | other_credit |
| `ANALYST_EXCLUDED` | Excluded by analyst | B | excluded |
| `UNCLASSIFIED` | Unclassified | B | other_credit |

| Treatment | Effect |
|---|---|
| `income` | Counted in income when trusted |
| `obligation` | Counted in recurring obligations, subject to the checks |
| `expense` | Counted in Monthly surplus outflows |
| `excluded` | Left out of adjusted cash flow and income |
| `other_credit` | A credit in cash flow, not income |
| `review_credit` | Cash deposit: a credit in cash flow, not income until verified |

## A worked example

From the Riya Sharma sample, measured by running the code:

**`NEFT/ACME SOFTWARE PVT LTD/SALARY JAN26`, a credit**

| Signal | Value | Why |
|---|---|---|
| `rule:R_SAL_KW` | 2.4 | The word `SALARY` |
| `periodic:monthly_credit` | 1.4 | A monthly series across 9 months, around day 1 |
| `periodic:stable_amount` | 0.8 | The amount varies 1.9% |
| `memory:global` | 0.929 | Similarity 0.62 to a seeded example |

Salary scores 5.529 and the baseline scores 0. `P = exp(5.529) / (exp(5.529) + 1) = 0.996`, and the margin is 0.992, so the row is `auto`. `R_COMPANY_PAY` did not fire despite `PVT LTD`, because the row has a payroll word.

**`NEFT/[Holder]/XXXXXXXX9981`, a debit with no matching credit**

`rule:R_SELF` fires at 4.2 on `<HOLDER>`. `P = 0.985`, so it is `auto` as an internal transfer. With no paired credit in the case, it also joins the list of statements to ask for.

**`IMPS/MOHAN VERMA`, a small debit**

No signal fires, so it is `auto` as General spending, by default. Had it been ₹25,000 or more, it would have gone to review.

## Changing the rules

See [DEVELOPMENT.md](DEVELOPMENT.md#changing-rules-or-categories). Bump `RULES_VERSION`, tune only on the dev split, and add a regression test.
