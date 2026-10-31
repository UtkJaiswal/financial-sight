"""Paths, versions and tunable thresholds. Every analysis records the versions below."""
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("FINSIGHT_DATA_DIR", ROOT / "data"))
FILES_DIR = DATA_DIR / "files"
DB_PATH = DATA_DIR / "finsight.duckdb"
KEYS_PATH = DATA_DIR / "dev_keys.json"  # dev only; production keys come from a KMS
SAMPLE_DIR = ROOT / "sample_data"

PIPELINE_VERSION = "p4"  # p4: foreign payment rails are not counterparties
RULES_VERSION = "r7"  # r7: thresholds and rules come from the lender's rule engine; custom keyword rules
WEIGHTS_VERSION = "w0"
QUESTION_BANK_VERSION = "q1"

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".csv", ".xlsx", ".json"}

# Tier 3 decision policy
AUTO_MIN_P = 0.90
AUTO_MIN_MARGIN = 0.30
REVIEW_MIN_P = 0.60
KNN_DISAGREE_SIM = 0.60
KNN_MIN_SIM = 0.50  # below this a similar-looking example is coincidence, not evidence

# Detectors and flags (amounts in paise)
TRANSFER_MAX_DAYS = 1
REVERSAL_MAX_DAYS = 30
LOW_BALANCE_PAISE = 5_000_00
LARGE_CASH_PAISE = 2_00_000_00
ROUND_FIGURE_MIN_PAISE = 50_000_00
PRE_APPLICATION_DAYS = 30
UNEXPLAINED_CREDIT_PAISE = 10_000_00
UNEXPLAINED_DEBIT_PAISE = 25_000_00
ROTATION_MIN_PAISE = 25_000_00  # smaller round trips are ordinary life
ROTATION_PART_MIN_PAISE = 5_000_00
ROTATION_FEE_MAX_PAISE = 1_000_00
ROTATION_WINDOW_DAYS = 7
ROTATION_PARTY_DAYS = 15
ROTATION_SPLIT_CANDIDATES = 10
ROTATION_PASS_MIN_PAISE = 50_000_00
ROTATION_PASS_DAYS = 3
ROTATION_PASS_MIN_COUNT = 3
FX_MATCH_TOLERANCE_PCT = 2  # the supplied rate is rarely the rate a bank applied
UNEXPLAINED_DEPOSIT_PAISE = 1_00_000_00
INCOME_VOLATILE_CV = 0.50
INCOME_SPIKE_RATIO = 1.5  # a month this far above the median, or below median / this, is unusual
NEGATIVE_CASH_FLOW_MONTHS = 2
CASH_FLOW_TREND_SHARE = 0.10  # net cash flow must move by this share of average inflows to count as a change
SALARY_MAX_PAYERS_IN_MONTH = 2  # a job change or a second job; more than this is not a salary pattern
# Taken off a self-employed customer's receipts. Rent and EMIs stay out: they are obligations, counted once against this income.
BUSINESS_COST_CATEGORIES = ("SUPPLIER_PAYMENT", "PAYROLL_PAYMENT", "TAX")
STABLE_OBLIGATION_RATIO = 1.10  # an unconfirmed EMI or rent counts only if its monthly amount stays within this ratio
