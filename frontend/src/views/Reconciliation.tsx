import { Scale } from "lucide-react";
import type { Analysis } from "../types";
import { dateLabel, money, moneyIn } from "../fmt";
import { Empty } from "../components/ui";

interface Props { a: Analysis; onOpen: (id: string) => void; onEngines: () => void }

export default function Reconciliation({ a, onOpen, onEngines }: Props) {
  const r = a.reconciliation;
  if (!r || !r.statements.length) return <div className="content-inner"><Empty icon={<Scale size={32} />} title="Nothing to reconcile yet">Upload statements in the Statements tab.</Empty></div>;
  const s = r.summary;
  return (
    <div className="content-inner">
      <div className="card">
        <div className="card-h">
          <div><h3>Reconciliation</h3><div className="hint">Opening balance + credits − debits should equal the closing balance, and each row's balance should follow from the one before.</div></div>
          <button className="btn sm" onClick={onEngines}>Set up in rule engines</button>
        </div>
        <div className="card-b">
          <div className="recon">
            <div><div className="label">Statements within the rules</div><div className={`v ${s.outside ? "neg" : "pos"}`}>{s.within} of {s.statements}</div></div>
            <div><div className="label">Running-balance breaks</div><div className="v">{s.row_breaks}</div></div>
            <div><div className="label">Gaps between statements</div><div className="v">{s.gaps}</div></div>
            <div><div className="label">Overlaps</div><div className="v">{s.overlaps}</div></div>
            <div><div className="label">Balances that do not carry over</div><div className="v">{s.balance_mismatches}</div></div>
            <div><div className="label">Own-account pairs matched</div><div className="v">{r.transfers.matched_pairs} · {money(r.transfers.matched_amount)}</div></div>
          </div>
          <div className="small muted" style={{ marginTop: 10 }}>
            Rules in force ({a.engine?.name}): allowed difference {money(r.rules.recon_tolerance_paise, true)}, {r.rules.recon_max_row_breaks} running-balance break(s) allowed, closing balance {r.rules.recon_require_closing ? "required" : "not required"}.
          </div>
        </div>
      </div>
      {r.statements.map((st) => (
        <div className="card" key={st.statement_id}>
          <div className="card-h">
            <div><h3>{st.bank_name} {st.acct_masked.slice(-4)}</h3><div className="hint">{dateLabel(st.period_start)} to {dateLabel(st.period_end)} · {st.n_rows} rows · {st.currency}</div></div>
            <span className={`pill ${st.result === "within" ? "ok" : "crit"}`}>{st.result === "within" ? "Within the rules" : "Outside the rules"}</span>
          </div>
          <div className="card-b">
            <div className="recon">
              <div><div className="label">Opening{st.opening_source === "derived" ? " (derived)" : ""}</div><div className="v">{moneyIn(st.currency, st.opening)}</div></div>
              <div><div className="label">+ Credits</div><div className="v cr">{moneyIn(st.currency, st.credits)}</div></div>
              <div><div className="label">− Debits</div><div className="v">{moneyIn(st.currency, st.debits)}</div></div>
              <div><div className="label">= Computed closing</div><div className="v">{moneyIn(st.currency, st.computed_closing)}</div></div>
              <div><div className="label">Stated closing</div><div className="v">{st.stated_closing === null ? "Not stated" : moneyIn(st.currency, st.stated_closing)}</div></div>
              <div><div className="label">Difference</div><div className={`v ${st.difference ? "neg" : ""}`}>{st.difference === null ? "—" : moneyIn(st.currency, st.difference)}</div></div>
            </div>
            {st.reasons.map((x) => <div className="note warn" key={x} style={{ marginTop: 8 }}>{x[0].toUpperCase() + x.slice(1)}.</div>)}
          </div>
          {st.breaks.length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table className="tbl">
                <thead><tr><th>Date</th><th>What is wrong</th><th>Source</th></tr></thead>
                <tbody>{st.breaks.map((b) => <tr key={b.txn_id} className="click" onClick={() => onOpen(b.txn_id)}><td className="num small" style={{ whiteSpace: "nowrap" }}>{dateLabel(b.date)}</td><td className="small">{b.note}</td><td className="small muted" style={{ whiteSpace: "nowrap" }}>{b.file} p{b.page} r{b.row}</td></tr>)}</tbody>
              </table>
            </div>
          )}
        </div>
      ))}
      {r.continuity.length > 0 && (
        <div className="card">
          <div className="card-h"><h3>Between statements</h3></div>
          <div className="card-b stack">{r.continuity.map((c, i) => <div key={i} className={`note ${c.kind === "gap" || c.kind === "balance_mismatch" ? "warn" : ""}`}>{c.detail[0].toUpperCase() + c.detail.slice(1)}.</div>)}</div>
        </div>
      )}
    </div>
  );
}
