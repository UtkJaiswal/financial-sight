import { useEffect, useState, type ReactNode } from "react";
import { Download, Printer } from "lucide-react";
import { api } from "../api";
import type { ReportData, Metric } from "../types";
import { dateLabel, money, moneyIn, monthLabel, pct } from "../fmt";
import { CategoryChip, SevPill, useToast } from "../components/ui";
import StatementRequests from "../components/StatementRequests";
import { shownValue } from "./CreditScore";

interface Props { caseId: string; version: string; onOpen: (id: string) => void; onTxns: (ids: string[], label: string) => void }

function Evidence({ ids, data, onOpen, reasons }: { ids: string[]; data: ReportData; onOpen: (id: string) => void; reasons?: Record<string, string> }) {
  const [open, setOpen] = useState(false);
  const rows = ids.map((i) => ({ id: i, e: data.evidence[i] })).filter((x) => x.e).sort((a, b) => a.e.date.localeCompare(b.e.date));
  if (!rows.length) return null;
  return (
    <div className="evidence">
      <button className="btn ghost sm no-print" onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"} {rows.length} supporting transaction{rows.length === 1 ? "" : "s"}</button>
      <span className="print-only small muted">{rows.length} supporting transaction{rows.length === 1 ? "" : "s"}</span>
      {/* always rendered, so the printed report carries its evidence whether or not a section was opened on screen */}
      <div className={open ? "evidence-rows" : "evidence-rows closed"} style={{ overflowX: "auto" }}>
          <table className="tbl">
            <thead><tr><th>Date</th><th>Narration</th><th>Account</th><th>Category</th><th className="r">Amount</th><th>Source</th></tr></thead>
            <tbody>{rows.map(({ id, e }) => (
              <tr key={id} className="click" onClick={() => onOpen(id)}>
                <td className="num small" style={{ whiteSpace: "nowrap" }}>{dateLabel(e.date)}</td>
                <td><span className="desc mono small">{e.description}</span>{reasons?.[id] && <div className="small muted">{reasons[id]}</div>}</td>
                <td className="small" style={{ whiteSpace: "nowrap" }}>{e.account}</td>
                <td><CategoryChip cat={e.category} /></td>
                <td className={`r num ${e.direction === "C" ? "cr" : ""}`}>{money(e.amount)}{e.currency !== "INR" && <div className="small muted">{moneyIn(e.currency, e.original)} at {e.fx_rate ?? "no rate"}</div>}</td>
                <td className="small muted" style={{ whiteSpace: "nowrap" }}>{e.sources.map((s) => `${s.file} p${s.page} r${s.row}`).join("; ")}</td>
              </tr>
            ))}</tbody>
          </table>
      </div>
    </div>
  );
}

const Section = ({ title, children }: { title: string; children: ReactNode }) => (
  <section className="card report-section"><div className="card-h"><h3>{title}</h3></div><div className="card-b stack">{children}</div></section>
);
const Facts = ({ items }: { items: [string, ReactNode][] }) => (
  <div className="recon">{items.map(([k, v]) => <div key={k}><div className="label">{k}</div><div className="v">{v}</div></div>)}</div>
);

export default function Report({ caseId, version, onOpen, onTxns }: Props) {
  const toast = useToast();
  const [d, setD] = useState<ReportData | null>(null);
  useEffect(() => {
    setD(null);
    api.report(caseId).then(setD).catch((e) => toast({ kind: "err", title: "Report not available", body: String(e.message) }));
  }, [caseId, version, toast]);
  if (!d) return <div className="content-inner"><div className="skeleton" style={{ height: 300 }} /></div>;

  const m = d.metrics;
  const inc = m.estimated_income as Metric | undefined;
  if (!inc?.key) return <div className="content-inner"><div className="note warn">No transactions yet, so there is nothing to report. Check the Statements tab.</div></div>;
  const aff = m.affordability, trend = m.cash_flow_trend, obl = m.recurring_obligations;
  const reasons = (metric?: Metric) => Object.fromEntries((metric?.excluded ?? []).map((e) => [e.txn_id, e.reason]));
  const risk = d.flags.filter((f) => f.category === "risk");
  const dataFlags = d.flags.filter((f) => f.category === "data");

  function download() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(d, null, 2)], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url; a.download = `finsight-report-${d!.case.case_id}.json`; a.click();
    URL.revokeObjectURL(url);
  }
  let n = 0;
  const T = (title: string) => `${++n}. ${title}`;
  const sc = d.credit_score, er = d.earnings, rec = d.reconciliation;

  return (
    <div className="content-inner report">
      <div className="card">
        <div className="card-h">
          <div><h3>Financial assessment: {d.case.name}</h3><div className="hint">Application date {dateLabel(d.case.application_date)} · figures in {d.reporting_currency} · snapshot {d.snapshot_id} · rule engine {d.engine?.name} v{d.engine?.version}</div></div>
          <div className="row no-print"><button className="btn sm" onClick={() => window.print()}><Printer size={14} /> Print</button><button className="btn sm" onClick={download}><Download size={14} /> JSON</button></div>
        </div>
        <div className="card-b"><div className="note info">{d.notice}</div></div>
      </div>

      {sc && (
        <Section title={T("Credit score")}>
          <Facts items={[["Score", sc.score ?? "No score"], ["Band", sc.band ?? "—"], ["Rule engine", `${sc.engine} v${sc.engine_version}`], ["Assessed", `${sc.assessed_weight}% of the weight`]]} />
          <div className="note warn">{sc.notice}{sc.reason ? ` ${sc.reason}` : ""}</div>
          <div style={{ overflowX: "auto" }}>
            <table className="tbl"><thead><tr><th>Level 1 parameter</th><th className="r">Measured</th><th className="r">Points</th></tr></thead>
              <tbody>{sc.parameters.map((p) => <tr key={p.key}><td>{p.label}{!p.assessed && <div className="small muted">Not assessed: {p.reason}</div>}</td><td className="r num">{shownValue(p.unit, p.value)}</td><td className="r num">{p.assessed ? `${p.points} of ${p.weight}` : `— of ${p.weight}`}</td></tr>)}</tbody></table>
          </div>
        </Section>
      )}

      <div className="no-print"><StatementRequests requests={d.statement_requests} onTxns={onTxns} /></div>

      <Section title={T("Sustainable income")}>
        <Facts items={[["Estimated monthly income", money(inc.value)], ["Regular (salary, monthly series)", money(inc.stats?.regular_monthly)], ["Variable", money(inc.stats?.variable_monthly)], ...(inc.stats?.gross_receipts_monthly !== undefined ? [["Receipts before costs", money(inc.stats.gross_receipts_monthly)], ["Business costs", money(inc.stats.business_costs_monthly)]] as [string, ReactNode][] : []), ["Conservative", money(inc.stats?.conservative)], ["Consistency", inc.consistency ?? "—"]]} />
        <div className="note"><b>Method.</b> {inc.method}. Months used: {inc.months_used.map(monthLabel).join(", ") || "none"}.</div>
        {inc.notes.map((n) => <div className="note warn" key={n}>{n}</div>)}
        <div style={{ overflowX: "auto" }}>
          <table className="tbl"><thead><tr><th>Month</th><th className="r">Regular</th><th className="r">Variable</th><th className="r">Business costs</th><th className="r">Income used</th><th>Note</th></tr></thead>
            <tbody>{(inc.monthly ?? []).map((x) => { const u = inc.unusual_months?.find((y) => y.month === x.month); return (
              <tr key={x.month}><td>{monthLabel(x.month)}</td><td className="r num">{money(x.regular)}</td><td className="r num">{money(x.variable)}</td><td className="r num">{x.costs ? `−${money(x.costs)}` : "—"}</td><td className="r num">{money(x.value)}</td><td>{u && <span className="pill warn">{u.kind === "spike" ? "Unusual spike" : "Unusual drop"}</span>}</td></tr>
            ); })}</tbody></table>
        </div>
        <Evidence ids={inc.included} data={d} onOpen={onOpen} />
        <div className="small muted">Credits left out, with reasons:</div>
        <Evidence ids={inc.excluded.map((e) => e.txn_id)} data={d} onOpen={onOpen} reasons={reasons(inc)} />
      </Section>

      {er && er.months > 0 && (
        <Section title={T("What the customer earns")}>
          <Facts items={[["Average per month", money(er.average_monthly)], ["Earned over the period", money(er.total_earned)], ["At this rate, per year", money(er.annualised)], ["Payers", String(er.n_payers)]]} />
          <div style={{ overflowX: "auto" }}>
            <table className="tbl"><thead><tr><th>Payer</th><th className="r">Months</th><th className="r">Amount</th><th className="r">Share</th></tr></thead>
              <tbody>{er.payers.map((p) => <tr key={p.name}><td>{p.name}</td><td className="r num">{p.months}</td><td className="r num">{money(p.amount)}</td><td className="r num">{pct(p.share)}</td></tr>)}</tbody></table>
          </div>
        </Section>
      )}

      <Section title={T("Obligations and affordability")}>
        <Facts items={[["Recurring obligations", money(obl?.value)], ["Obligations to income", pct(m.foir?.value, 1)], ["Surplus after obligations", money(aff?.value)], ["Average monthly surplus achieved", money(aff?.stats?.average_monthly_surplus)]]} />
        <table className="tbl"><thead><tr><th>Rule</th><th>Value</th><th>Result</th></tr></thead>
          <tbody>{(aff?.checks ?? []).map((c) => <tr key={c.rule}><td>{c.rule}</td><td className="num">{c.shown || "—"}</td><td><span className={`pill ${c.result === "within" ? "ok" : c.result === "outside" ? "warn" : ""}`}>{c.result === "within" ? "Within" : c.result === "outside" ? "Outside" : "Not assessed"}</span></td></tr>)}</tbody></table>
        {aff?.notes.map((n) => <div className="note warn" key={n}>{n}</div>)}
        <h4>Treated as obligations, and why</h4>
        {(obl?.items ?? []).length === 0 && <div className="small muted">No EMI or rent payments identified.</div>}
        {(obl?.items ?? []).map((i) => (
          <div className="linked" key={i.category + i.counterparty}>
            <div className="row"><CategoryChip cat={i.category} /><span className="small">{i.counterparty}</span><span className="spacer" /><span className="num">{money(i.monthly_amount)}/mo</span><span className={`pill ${i.active ? "ok" : "warn"}`}>{i.active ? "Counted" : i.pending ? "Awaiting review" : "Not counted"}</span></div>
            <div className="small muted">{i.why} · {i.months.length} month{i.months.length === 1 ? "" : "s"}</div>
            <Evidence ids={i.txn_ids} data={{ ...d, evidence: { ...d.evidence } }} onOpen={onOpen} />
          </div>
        ))}
      </Section>

      <Section title={T("Cash-flow trend")}>
        <Facts items={[["Assessment", <span className={`pill ${trend?.assessment === "improving" ? "ok" : trend?.assessment === "deteriorating" ? "crit" : "info"}`}>{trend?.assessment ?? "—"}</span>], ["Earlier months' average net", money(trend?.stats?.earlier_months_average)], ["Later months' average net", money(trend?.stats?.later_months_average)]]} />
        <div className="note"><b>Method.</b> {trend?.method}.{trend?.highlight_months?.length ? ` Months driving this: ${trend.highlight_months.map(monthLabel).join(", ")}.` : ""}</div>
        <div style={{ overflowX: "auto" }}>
          <table className="tbl"><thead><tr><th>Month</th><th>Coverage</th><th className="r">Inflows</th><th className="r">Outflows</th><th className="r">Net</th><th className="r">Average balance</th></tr></thead>
            <tbody>{d.monthly.filter((r) => r.active).map((r) => <tr key={r.month} className={trend?.highlight_months?.includes(r.month) ? "sel" : ""}><td>{monthLabel(r.month)}</td><td className="small">{r.coverage}</td><td className="r num">{money(r.credits_adjusted)}</td><td className="r num">{money(r.debits_adjusted)}</td><td className={`r num ${r.net < 0 ? "neg" : ""}`}>{money(r.net)}</td><td className="r num">{money(r.avg_balance)}</td></tr>)}</tbody></table>
        </div>
        <Evidence ids={trend?.included ?? []} data={d} onOpen={onOpen} />
      </Section>

      <Section title={T("Accounts and reconciliation")}>
        <div style={{ overflowX: "auto" }}>
          <table className="tbl"><thead><tr><th>Account</th><th>Currency</th><th>Period</th><th>Reconciliation</th><th className="r">Rows</th></tr></thead>
            <tbody>{d.statements.map((s) => <tr key={s.statement_id}><td style={{ whiteSpace: "nowrap" }}>{s.bank_name} {s.acct_masked.slice(-4)}</td><td className="small">{s.currency ?? "INR"}</td><td className="small" style={{ whiteSpace: "nowrap" }}>{dateLabel(s.period_start)} to {dateLabel(s.period_end)}</td><td className="small">{s.recon_status.replace(/_/g, " ")}</td><td className="r num">{s.n_rows}</td></tr>)}</tbody></table>
        </div>
        {rec && <Facts items={[["Statements within reconciliation rules", `${rec.summary.within} of ${rec.summary.statements}`], ["Running-balance breaks", String(rec.summary.row_breaks)], ["Gaps", String(rec.summary.gaps)], ["Overlaps", String(rec.summary.overlaps)]]} />}
        {d.statement_requests?.prompt && <div className="note warn">{d.statement_requests.prompt}{d.statement_requests.items.length ? ` Asked for: ${d.statement_requests.items.map((g) => g.label).join(", ")}.` : ""}</div>}
        <Facts items={[["Money moved around", money(m.money_movement?.value)], ["Own-account transfers", money(m.money_movement?.stats?.own_account_transfers)], ["Possible rotation", money(m.money_movement?.stats?.possible_rotation)], ["Share of all credits", pct(m.money_movement?.stats?.share_of_all_credits)]]} />
        {d.cross_checks.filter((c) => c.kind !== "continuous").map((c, i) => <div className="note" key={i}>{c.detail}</div>)}
        {d.accounts.filter((v) => v.missing_months.length).map((v) => <div className="note warn" key={v.account_id}>{v.bank_name} {v.acct_masked?.slice(-4)}: no statement for {v.missing_months.map(monthLabel).join(", ")}.</div>)}
        <Evidence ids={m.money_movement?.included ?? []} data={d} onOpen={onOpen} />
      </Section>

      {d.fx.currencies.some((c) => c !== "INR") && (
        <Section title={T("Currencies")}>
          {d.fx.rates.map((r) => <div className="small" key={r.currency + r.month}>{r.currency} {r.month ? `in ${monthLabel(r.month)}` : "in all months"}: {r.rate} to {d.fx.reporting}. Source: {r.source}.</div>)}
          {d.fx.no_rate_rows > 0 && <div className="note warn">{d.fx.no_rate_rows} row(s) have no rate and are left out of every figure.</div>}
        </Section>
      )}

      <Section title={T("Risk indicators for credit review")}>
        {risk.length === 0 && <div className="small muted">No indicator was triggered.</div>}
        {risk.map((f, i) => (
          <div className="linked" key={i}>
            <div className="row wrap"><b>{f.title}</b><SevPill sev={f.severity} /></div>
            <div className="small">{f.detail}</div>
            {f.rule && <div className="small muted"><b>Rule:</b> {f.rule}</div>}
            <Evidence ids={f.txn_ids} data={d} onOpen={onOpen} />
          </div>
        ))}
      </Section>

      <Section title={T("Data quality and corrections")}>
        {dataFlags.map((f, i) => <div className="note warn" key={i}><b>{f.title}.</b> {f.detail}</div>)}
        {dataFlags.length === 0 && <div className="small muted">No data-quality issue was raised.</div>}
        {d.corrections.length === 0 ? <div className="small muted">No analyst corrections so far.</div> : (
          <table className="tbl"><thead><tr><th>When</th><th>By</th><th>Change</th><th>Reason</th></tr></thead>
            <tbody>{d.corrections.map((c) => <tr key={c.txn_id + c.created_at}><td className="small" style={{ whiteSpace: "nowrap" }}>{dateLabel(c.created_at)}</td><td className="small">{c.actor}</td><td><CategoryChip cat={c.from_category} /> → <CategoryChip cat={c.to_category} /></td><td className="small">{c.reason}</td></tr>)}</tbody></table>
        )}
      </Section>
    </div>
  );
}
