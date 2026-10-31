export type Status = "auto" | "review" | "unclassified" | "confirmed";
export type Severity = "high" | "medium" | "low" | "info";

export interface CategoryMeta {
  label: string;
  dir: "C" | "D" | "B";
  treatment: "income" | "obligation" | "expense" | "excluded" | "other_credit" | "review_credit";
}

export interface Meta {
  categories: Record<string, CategoryMeta>;
  roles: string[];
  versions: Record<string, string>;
  policy: { auto_min_p: number; auto_min_margin: number; review_min_p: number };
  jev_enabled: boolean;
  external_models_allowed: boolean;
}

export interface CaseSummary {
  case_id: string;
  name: string;
  application_date: string;
  profile: string;
  is_demo: boolean;
  n_transactions?: number;
  income?: number | null;
  profile_detected?: string;
  flags_high?: number;
  n_accounts?: number;
  n_files: number;
  months?: string[];
}

export interface Excluded { txn_id: string; reason: string }

export interface Metric {
  key: string;
  label: string;
  unit: "paise" | "ratio";
  value: number | null;
  method: string;
  profile?: string;
  monthly?: { month: string; value: number; gross?: number; costs?: number; regular?: number; variable?: number }[];
  unusual_months?: { month: string; kind: "spike" | "drop"; value: number; median: number }[];
  consistency?: string;
  assessment?: string;
  highlight_months?: string[];
  rules?: { max_foir: number; min_surplus_after_obligations_paise: number };
  rules_source?: string;
  checks?: { rule: string; value: number | null; limit: number; result: string; shown: string }[];
  stats?: Record<string, number>;
  items?: { category: string; label: string; counterparty: string; monthly_amount: number; months: string[]; pending?: boolean; why?: string; active: boolean; txn_ids: string[] }[];
  trend?: number | null;
  outflows?: number;
  included: string[];
  excluded: Excluded[];
  months_used: string[];
  months_missing: string[];
  notes: string[];
}

export interface MonthRow {
  month: string;
  coverage: "full" | "partial" | "missing";
  coverage_by_account: Record<string, string>;
  credits: number;
  debits: number;
  credits_adjusted: number;
  debits_adjusted: number;
  income: number;
  obligations: number;
  net: number;
  n_txns: number;
  avg_balance: number | null;
  active: boolean;
  credit_ids: string[];
  debit_ids: string[];
  balances: Record<string, { avg: number; min: number; low_days: number; days: number }>;
}

export interface Flag {
  code: string;
  severity: Severity;
  title: string;
  detail: string;
  txn_ids: string[];
  months: string[];
  category: "risk" | "data" | "review";
  rule?: string;
}

export interface FxView {
  reporting: string;
  rates: { currency: string; month: string; rate: string; source: string; actor: string }[];
  missing: [string, string][];
  currencies: string[];
  no_rate_rows: number;
  no_rate_txn_ids: string[];
  assumed_accounts: string[];
}

export interface AccountMonth {
  month: string; coverage: "full" | "partial" | "missing"; credits: number; debits: number; credits_adjusted: number; debits_adjusted: number;
  net: number; avg_balance: number | null; credit_ids: string[]; debit_ids: string[];
}

export interface HistoryEvent { date: string; kind: string; title: string; detail: string; txn_ids: string[] }
export interface Party { name: string; amount: number; count: number; txn_ids: string[]; share?: number; months?: number }

export interface AccountView {
  account_id: string; bank_name: string | null; acct_masked: string | null; currency: string; months: AccountMonth[]; missing_months: string[]; partial_months: string[];
  summary: { first_entry: string | null; last_entry: string | null; n_txns: number; n_statements: number; total_credits: number; total_debits: number; opening: number | null; latest_balance: number | null;
    lowest_balance: number | null; highest_balance: number | null; returned_payments: number; transfers_in: number; transfers_out: number };
  by_category: { category: string; credits: number; debits: number; count: number }[];
  top_in: Party[];
  top_out: Party[];
  events: HistoryEvent[];
}

export interface ScoreParameter { key: string; label: string; unit: string; source: string; weight: number; full_at: number; zero_at: number; value: number | null; txn_ids: string[]; points: number | null; assessed: boolean; reason?: string }

export interface CreditScoreData {
  engine: string; engine_version: number; notice: string; parameters: ScoreParameter[]; assessed_weight: number; min_assessed_weight: number;
  score: number | null; band: string | null; reason: string | null; bands: { min: number; label: string }[]; months_used: string[];
}

export interface ReconciliationData {
  rules: { recon_tolerance_paise: number; recon_require_closing: boolean; recon_max_row_breaks: number };
  statements: { statement_id: string; bank_name: string; acct_masked: string; currency: string; period_start: string; period_end: string; opening: number | null; opening_source: string; credits: number; debits: number;
    computed_closing: number | null; stated_closing: number | null; difference: number | null; n_rows: number; n_breaks: number; result: "within" | "outside"; reasons: string[];
    breaks: { txn_id: string; date: string; note: string; file: string; page: number; row: number }[] }[];
  continuity: { kind: string; detail: string; account_id: string }[];
  transfers: { matched_pairs: number; matched_amount: number; held_rows: number };
  summary: { statements: number; within: number; outside: number; row_breaks: number; gaps: number; overlaps: number; balance_mismatches: number };
}

export interface EarningsData {
  monthly: { month: string; receipts: number; costs: number; earned: number; cumulative: number }[]; total_receipts: number; total_costs: number; total_earned: number; months: number;
  average_monthly: number | null; estimate: number | null; annualised: number | null; best: { month: string } | null; worst: { month: string } | null; last_3_average: number | null;
  payers: Party[]; n_payers: number; pending_review: number; concentrated: boolean; concentration_limit: number;
}

export interface StatementRequestsData {
  prompt: string | null;
  items: { key: string; label: string; reason: string; count: number; amount: number; txn_ids: string[]; kind: string }[];
  matched_pairs: number; matched_amount: number; accounts_uploaded: number;
}

export interface EngineConfig {
  params: Record<string, number | string | boolean>;
  custom_rules: { keyword: string; direction: string; category: string; weight: number }[];
  score: { parameters: Record<string, { enabled: boolean; weight: number; full_at: number; zero_at: number }>; bands: { min: number; label: string }[]; min_assessed_weight: number };
}

export interface EngineSummary { engine_id: string; name: string; description: string; version: number; config: EngineConfig; is_sample: boolean; editable: boolean; cases: number }

export interface RuleEnginesData {
  engines: EngineSummary[];
  schema: { key: string; group: string; label: string; kind: string; default: unknown; source: string; help: string }[];
  score_parameters: Record<string, { label: string; unit: string; source: string; weight: number }>;
  rule_categories: string[];
}

export interface EngineComparison { engine_id: string; name: string; version: number; current: boolean; score: number | null; band: string | null; income: number | null; foir: number | null; reason: string | null }

export interface ReportData {
  case: { name: string; application_date: string; case_id: string };
  notice: string;
  snapshot_id: string;
  versions: Record<string, string>;
  reporting_currency: string;
  profile: string;
  metrics: Record<string, Metric>;
  monthly: MonthRow[];
  accounts: AccountView[];
  statements: Statement[];
  cross_checks: { account_id: string; kind: string; detail: string }[];
  flags: Flag[];
  fx: FxView;
  credit_score: CreditScoreData | null;
  reconciliation: ReconciliationData;
  earnings: EarningsData | null;
  statement_requests: StatementRequestsData;
  engine: { id: string; name: string; version: number };
  corrections: { txn_id: string; from_category: string; to_category: string; reason: string; actor: string; created_at: string }[];
  evidence: Record<string, { date: string; description: string; direction: "C" | "D"; amount: number; currency: string; original: number; fx_rate: string | null;
    category: string; status: string; why: string; account: string; sources: { file: string; page: number; row: number }[] }>;
}

export interface Account { account_id: string; acct_masked: string; bank_name: string; holder_masked: string; account_type: string | null }

export interface Statement {
  statement_id: string;
  file_id: string;
  account_id: string;
  acct_masked: string;
  bank_name: string;
  layout: string;
  period_start: string;
  period_end: string;
  opening_paise: number | null;
  opening_source: string;
  closing_stated: number | null;
  computed_closing: number | null;
  total_debits: number;
  total_credits: number;
  recon_status: string;
  recon_diff: number | null;
  currency?: string;
  n_rows: number;
  n_breaks: number;
}

export interface Pattern {
  pattern_id: string;
  account_id: string;
  direction: "C" | "D";
  label: string;
  counterparties: string[];
  median_amount: number;
  amount_cv: number;
  typical_day: number;
  months: string[];
  missed_months: string[];
  expected_next: string;
  txn_ids: string[];
}

export interface Analysis {
  snapshot_id: string;
  seq: number;
  trigger: string;
  created_at: string;
  versions: Record<string, string>;
  months: string[];
  coverage: Record<string, string>;
  monthly: MonthRow[];
  metrics: Record<string, Metric>;
  fx: FxView;
  account_view: AccountView[];
  credit_score: CreditScoreData | null;
  reconciliation: ReconciliationData;
  earnings: EarningsData | null;
  statement_requests: StatementRequestsData;
  engine: { id: string; name: string; version: number };
  money_flows: {
    edges: { from: string; to: string; possible: boolean; amount: number; count: number; txn_ids: string[] }[];
    cycles: { path: string[]; amount: number; count: number; txn_ids: string[] }[];
    parties: { counterparty: string; out: number; back: number; count: number; txn_ids: string[] }[];
    pass_through: { account_id: string; amount: number; count: number; txn_ids: string[] }[];
  };
  regular_outflows: { category: string; label: string; monthly_median: number; months: number; txn_ids: string[] }[];
  balance_series: { date: string; total: number }[];
  account_balance_series: Record<string, { date: string; balance: number }[]>;
  profile: string;
  flags: Flag[];
  accounts: Account[];
  statements: Statement[];
  cross_checks: { account_id: string; kind: string; detail: string }[];
  patterns: Pattern[];
  status_counts: Record<string, number>;
  n_transactions: number;
}

export interface Txn {
  txn_id: string;
  account_id: string;
  txn_date: string;
  description: string;
  debit: number;
  credit: number;
  balance: number | null;
  channel: string;
  counterparty: string;
  recon_status: string;
  recon_note: string;
  acct_masked: string;
  bank_name: string;
  category: string;
  status: Status;
  model_category: string;
  model_status: string;
  tier: string;
  source: string;
  probability: number | null;
  margin: number | null;
  explanation: string;
  attributes: string[];
  override_category: string | null;
  override_reason: string | null;
  override_actor: string | null;
  override_at: string | null;
  direction: "C" | "D";
  amount: number;
  flags: string[];
  currency: string;
  orig_debit: number;
  orig_credit: number;
  orig_balance: number | null;
  fx_rate: string | null;
  fx_missing: boolean;
}

export interface Contribution { signal: string; category: string; value: number; detail: string; weight_share?: number }

export interface TxnDetail {
  txn: Txn;
  classification: {
    category: string;
    status: string;
    probability: number;
    margin: number;
    explanation: string;
    contributions: Contribution[];
    candidates: { category: string; p: number }[];
    attributes: string[];
    source: string;
    tier: string;
  };
  history: { run_id: string; seq: number; created_at: string; category: string; status: string; probability: number; source: string }[];
  sources: { statement_id: string; file_id: string; page_no: number; row_no: number; filename: string; period_start: string; period_end: string }[];
  links: { kind: string; reason: string; txn: Txn }[];
  overrides: { event_id: string; from_category: string; to_category: string; reason: string; actor: string; role: string; created_at: string }[];
  patterns: Pattern[];
}

export interface FileReport {
  file_id: string;
  filename: string;
  kind: string;
  size_bytes: number;
  status: string;
  reason: string | null;
  uploaded_at: string;
  sha256: string;
  pages: { page_no: number; status: string; reason: string; n_rows: number; method: string }[];
  quarantine: { page_no: number | null; row_no: number | null; kind: string; reason: string; detail: string | null }[];
  statements: Statement[];
  duplicate_upload?: boolean;
  edit_check: EditCheck | null;
}

export interface EditSignal { code: string; weight: number; detail: string; evidence: string[] }
export interface EditCheck { score: number; structure_score: number; level: "none" | "low" | "medium" | "high"; signals: EditSignal[] }

export interface Diff {
  metrics: { key: string; label: string; unit: string; before: number | null; after: number | null; method_before?: string; method_after?: string }[];
  transactions: { txn_id: string; before: [string, string]; after: [string, string] }[];
  flags_added: Flag[];
  flags_removed: Flag[];
}

export interface Evaluation {
  available: boolean;
  reason?: string;
  matched: number;
  accuracy: number;
  auto_share: number;
  auto_accuracy: number | null;
  by_status: Record<string, number>;
  per_category: { category: string; label: string; support: number; precision: number | null; recall: number | null }[];
  errors: { txn_id: string; description: string; expected: string; predicted: string; status: string; amount: number; date: string }[];
  reconciliation_pass_rate: number | null;
}

export interface AuditEntry { seq: number; ts: string; actor: string; role: string; action: string; case_id: string | null; target: string | null; details: string; hash: string }
