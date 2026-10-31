import { useEffect, useState } from "react";
import { Landmark } from "lucide-react";
import { api } from "../api";
import type { AccountView, Analysis } from "../types";
import { dateLabel, money, monthLabel } from "../fmt";
import { Empty, useCats, useToast } from "../components/ui";

interface Props { caseId: string; a: Analysis; canEdit: boolean; onTxns: (ids: string[], label: string) => void; onChanged: () => void }

const COVERAGE: Record<string, [string, string]> = { full: ["ok", "Full"], partial: ["warn", "Partial"], missing: ["crit", "Missing"] };

function Rates({ caseId, a, canEdit, onChanged }: Pick<Props, "caseId" | "a" | "canEdit" | "onChanged">) {
  const toast = useToast();
  const foreign = a.fx.currencies.filter((c) => c !== a.fx.reporting);
  const [form, setForm] = useState({ currency: foreign[0] ?? "", month: "", rate: "", source: "" });
  useEffect(() => { setForm((f) => ({ ...f, currency: f.currency || foreign[0] || "" })); }, [foreign.join()]);
  if (!foreign.length) return null;
  async function save(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.setRate(caseId, form);
      setForm({ ...form, rate: "", month: "" });
      onChanged();
      toast({ title: "Rate saved and figures recalculated" });
    } catch (err) { toast({ kind: "err", title: "Rate not saved", body: String((err as Error).message) }); }
  }
  return (
    <div className="card">
      <div className="card-h"><div><h3>Exchange rates</h3><div className="hint">Figures are reported in {a.fx.reporting}. Rates are supplied by you and recorded; none is fetched or assumed. A month's own rate wins over a general one.</div></div></div>
      {a.fx.missing.length > 0 && <div className="card-b"><div className="note warn">No rate for: {a.fx.missing.map(([c, m]) => `${c} ${monthLabel(m)}`).join(", ")}. {a.fx.no_rate_rows} row(s) are left out of every figure until a rate is supplied.</div></div>}
      {a.fx.rates.length > 0 && (
        <table className="tbl">
          <thead><tr><th>Currency</th><th>Applies to</th><th className="r">Rate to {a.fx.reporting}</th><th>Source</th><th>Added by</th></tr></thead>
          <tbody>{a.fx.rates.map((r) => <tr key={r.currency + r.month}><td>{r.currency}</td><td>{r.month ? monthLabel(r.month) : "All months"}</td><td className="r num">{r.rate}</td><td className="small">{r.source}</td><td className="small muted">{r.actor}</td></tr>)}</tbody>
        </table>
      )}
      {canEdit && (
        <form className="card-b row wrap" onSubmit={save}>
          <select id="fx-currency" className="select" aria-label="Currency" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })}>{foreign.map((c) => <option key={c}>{c}</option>)}</select>
          <input id="fx-month" className="input" style={{ width: 130 }} aria-label="Month" placeholder="2026-07 or blank" value={form.month} onChange={(e) => setForm({ ...form, month: e.target.value })} />
          <input id="fx-rate" className="input" style={{ width: 110 }} aria-label="Rate" placeholder="83.50" value={form.rate} onChange={(e) => setForm({ ...form, rate: e.target.value })} />
          <input id="fx-source" className="input" style={{ flex: 1, minWidth: 200 }} aria-label="Source" placeholder="Where this rate came from" value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })} />
          <button className="btn primary sm" type="submit">Save rate</button>
        </form>
      )}
    </div>
  );
}

const KIND: Record<string, string> = { statement: "", activity: "", income: "ok", loan: "warn", obligation: "info", risk: "crit", correction: "info" };

function History({ v, onTxns }: { v: AccountView; onTxns: (ids: string[], label: string) => void }) {
  const cats = useCats();
  const s = v.summary;
  const Parties = ({ title, rows }: { title: string; rows: AccountView["top_in"] }) => (
    <div className="card">
      <div className="card-h"><h3>{title}</h3></div>
      <table className="tbl"><tbody>
        {rows.map((p) => <tr key={p.name} className="click" onClick={() => onTxns(p.txn_ids, p.name)}><td>{p.name}</td><td className="r num small">{p.count}×</td><td className="r num">{money(p.amount)}</td></tr>)}
        {rows.length === 0 && <tr><td className="small muted">None</td></tr>}
      </tbody></table>
    </div>
  );
  return (
    <>
      <div className="card" data-testid="account-history">
        <div className="card-h"><div><h3>Account history: {v.bank_name} {(v.acct_masked ?? "").slice(-4)}</h3><div className="hint">{s.first_entry ? `${dateLabel(s.first_entry)} to ${s.last_entry ? dateLabel(s.last_entry) : ""}` : "No entries"} · {s.n_statements} statement{s.n_statements === 1 ? "" : "s"} · {s.n_txns} entries · shown in INR</div></div></div>
        <div className="card-b">
          <div className="recon">
            <div><div className="label">Opening balance</div><div className="v">{money(s.opening)}</div></div>
            <div><div className="label">Latest balance</div><div className="v">{money(s.latest_balance)}</div></div>
            <div><div className="label">Lowest balance</div><div className="v">{money(s.lowest_balance)}</div></div>
            <div><div className="label">Highest balance</div><div className="v">{money(s.highest_balance)}</div></div>
            <div><div className="label">Total credits</div><div className="v cr">{money(s.total_credits)}</div></div>
            <div><div className="label">Total debits</div><div className="v">{money(s.total_debits)}</div></div>
            <div><div className="label">Own transfers in / out</div><div className="v">{money(s.transfers_in)} / {money(s.transfers_out)}</div></div>
            <div><div className="label">Returned payments</div><div className={`v ${s.returned_payments ? "neg" : ""}`}>{s.returned_payments}</div></div>
          </div>
        </div>
        <div style={{ overflowX: "auto" }}>
          <table className="tbl">
            <thead><tr><th>Date</th><th>Event</th><th>Detail</th></tr></thead>
            <tbody>{v.events.map((e, i) => (
              <tr key={i} className={e.txn_ids.length ? "click" : ""} onClick={() => e.txn_ids.length && onTxns(e.txn_ids, e.title)}>
                <td className="num small" style={{ whiteSpace: "nowrap" }}>{e.date ? dateLabel(e.date) : "—"}</td>
                <td style={{ whiteSpace: "nowrap" }}><span className={`pill ${KIND[e.kind] ?? ""}`}>{e.title}</span></td>
                <td className="small muted">{e.detail}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </div>
      <div className="grid-2">
        <Parties title="Largest sources of money in" rows={v.top_in} />
        <Parties title="Largest destinations of money out" rows={v.top_out} />
      </div>
      <div className="card">
        <div className="card-h"><h3>What this account was used for</h3></div>
        <table className="tbl">
          <thead><tr><th>Category</th><th className="r">Entries</th><th className="r">In</th><th className="r">Out</th></tr></thead>
          <tbody>{v.by_category.map((c) => <tr key={c.category}><td>{cats[c.category]?.label ?? c.category}</td><td className="r num">{c.count}</td><td className="r num">{c.credits ? money(c.credits) : "—"}</td><td className="r num">{c.debits ? money(c.debits) : "—"}</td></tr>)}</tbody>
        </table>
      </div>
    </>
  );
}

export default function Accounts({ caseId, a, canEdit, onTxns, onChanged }: Props) {
  const [view, setView] = useState("all");
  useEffect(() => setView("all"), [caseId]);
  if (!a.account_view?.length) return <div className="content-inner"><Empty icon={<Landmark size={32} />} title="No accounts yet">Upload bank statements in the Statements tab.</Empty><Rates caseId={caseId} a={a} canEdit={canEdit} onChanged={onChanged} /></div>;
  const acct = a.account_view.find((v) => v.account_id === view);
  const label = (v: { bank_name: string | null; acct_masked: string | null }) => `${v.bank_name} ${(v.acct_masked ?? "").slice(-4)}`;
  const rows = acct ? acct.months : a.monthly;
  return (
    <div className="content-inner">
      <div className="card">
        <div className="card-h"><div><h3>Statement coverage by account</h3><div className="hint">A missing month is never treated as zero activity.</div></div></div>
        <div style={{ overflowX: "auto" }}>
          <table className="tbl">
            <thead><tr><th>Account</th><th>Currency</th>{a.months.map((m) => <th key={m} className="r">{monthLabel(m)}</th>)}</tr></thead>
            <tbody>
              {a.account_view.map((v) => (
                <tr key={v.account_id}>
                  <td style={{ whiteSpace: "nowrap" }}>{label(v)}</td><td className="small">{v.currency}</td>
                  {v.months.map((m) => <td className="r" key={m.month}><span className={`pill ${COVERAGE[m.coverage]?.[0] ?? ""}`}>{COVERAGE[m.coverage]?.[1] ?? m.coverage}</span></td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <div className="card-h">
          <div><h3>{acct ? label(acct) : "All accounts, consolidated"}</h3><div className="hint">{acct ? "This account alone." : "Transfers between the customer's own accounts cancel out of adjusted inflows and outflows."} Shown in {a.fx.reporting}. Choose an amount to see its transactions.</div></div>
          <div className="seg" role="group" aria-label="Account view">
            <button className={view === "all" ? "on" : ""} onClick={() => setView("all")}>Consolidated</button>
            {a.account_view.map((v) => <button key={v.account_id} className={view === v.account_id ? "on" : ""} onClick={() => setView(v.account_id)}>{label(v)}</button>)}
          </div>
        </div>
        <div style={{ overflowX: "auto" }}>
          <table className="tbl">
            <thead><tr><th>Month</th><th>Coverage</th><th className="r">All credits</th><th className="r">All debits</th><th className="r">Adjusted inflows</th><th className="r">Adjusted outflows</th><th className="r">Net</th><th className="r">Average balance</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.month}>
                  <td>{monthLabel(r.month)}</td>
                  <td><span className={`pill ${COVERAGE[r.coverage]?.[0] ?? ""}`}>{COVERAGE[r.coverage]?.[1] ?? r.coverage}</span></td>
                  <td className="r num"><button className="btn ghost sm" disabled={!r.credit_ids.length} onClick={() => onTxns(r.credit_ids, `Credits, ${monthLabel(r.month)}`)}>{money(r.credits)}</button></td>
                  <td className="r num"><button className="btn ghost sm" disabled={!r.debit_ids.length} onClick={() => onTxns(r.debit_ids, `Debits, ${monthLabel(r.month)}`)}>{money(r.debits)}</button></td>
                  <td className="r num">{money(r.credits_adjusted)}</td><td className="r num">{money(r.debits_adjusted)}</td>
                  <td className={`r num ${r.net < 0 ? "neg" : ""}`}>{money(r.net)}</td><td className="r num">{money(r.avg_balance)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {acct ? <History v={acct} onTxns={onTxns} /> : (
        <div className="card">
          <div className="card-h"><div><h3>Account history</h3><div className="hint">Choose an account to see everything it shows over time.</div></div></div>
          <div style={{ overflowX: "auto" }}>
            <table className="tbl">
              <thead><tr><th>Account</th><th>First entry</th><th>Latest entry</th><th className="r">Entries</th><th className="r">Credits</th><th className="r">Debits</th><th className="r">Latest balance</th><th /></tr></thead>
              <tbody>{a.account_view.map((v) => (
                <tr key={v.account_id} className="click" onClick={() => setView(v.account_id)}>
                  <td style={{ whiteSpace: "nowrap" }}>{label(v)}</td><td className="small">{v.summary?.first_entry ? dateLabel(v.summary.first_entry) : "—"}</td><td className="small">{v.summary?.last_entry ? dateLabel(v.summary.last_entry) : "—"}</td>
                  <td className="r num">{v.summary?.n_txns}</td><td className="r num">{money(v.summary?.total_credits)}</td><td className="r num">{money(v.summary?.total_debits)}</td><td className="r num">{money(v.summary?.latest_balance)}</td><td className="r small muted">Open</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </div>
      )}
      <Rates caseId={caseId} a={a} canEdit={canEdit} onChanged={onChanged} />
    </div>
  );
}
