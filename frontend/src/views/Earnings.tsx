import { TrendingUp } from "lucide-react";
import type { Analysis } from "../types";
import { money, monthLabel, pct } from "../fmt";
import { Empty } from "../components/ui";

interface Props { a: Analysis; onTxns: (ids: string[], label: string) => void }

export default function Earnings({ a, onTxns }: Props) {
  const e = a.earnings;
  if (!e || !e.months) return <div className="content-inner"><Empty icon={<TrendingUp size={32} />} title="No earnings to track yet">Upload statements covering at least one full month.</Empty></div>;
  const biggest = Math.max(1, ...e.monthly.map((r) => Math.abs(r.earned)));
  const selfEmployed = a.profile !== "SALARIED";
  return (
    <div className="content-inner">
      <div className="card">
        <div className="card-h"><div><h3>{selfEmployed ? "What this customer earns" : "Salary received"}</h3><div className="hint">{selfEmployed ? "Confirmed receipts less identified suppliers, staff and tax." : "Confirmed salary credits."} Credits awaiting review are not counted.</div></div></div>
        <div className="card-b">
          <div className="recon">
            <div><div className="label">Estimated monthly income</div><div className="v">{money(e.estimate)}</div></div>
            <div><div className="label">Average per month</div><div className="v">{money(e.average_monthly)}</div></div>
            <div><div className="label">Last 3 months' average</div><div className="v">{money(e.last_3_average)}</div></div>
            <div><div className="label">Earned over {e.months} months</div><div className="v">{money(e.total_earned)}</div></div>
            <div><div className="label">At this rate, per year</div><div className="v">{money(e.annualised)}</div></div>
            {selfEmployed && <div><div className="label">Receipts over the period</div><div className="v">{money(e.total_receipts)}</div></div>}
            {selfEmployed && <div><div className="label">Business costs over the period</div><div className="v">{money(e.total_costs)}</div></div>}
            {e.pending_review > 0 && <div><div className="label">Awaiting review, not counted</div><div className="v warn-text">{money(e.pending_review)}</div></div>}
          </div>
        </div>
        <div style={{ overflowX: "auto" }}>
          <table className="tbl">
            <thead><tr><th>Month</th>{selfEmployed && <th className="r">Receipts</th>}{selfEmployed && <th className="r">Costs</th>}<th className="r">Earned</th><th style={{ width: "28%" }} /><th className="r">Running total</th></tr></thead>
            <tbody>{e.monthly.map((r) => (
              <tr key={r.month}>
                <td>{monthLabel(r.month)}{e.best?.month === r.month && <span className="pill ok" style={{ marginLeft: 6 }}>Best</span>}{e.worst?.month === r.month && e.months > 1 && <span className="pill warn" style={{ marginLeft: 6 }}>Lowest</span>}</td>
                {selfEmployed && <td className="r num">{money(r.receipts)}</td>}
                {selfEmployed && <td className="r num">{r.costs ? `−${money(r.costs)}` : "—"}</td>}
                <td className={`r num ${r.earned < 0 ? "neg" : ""}`}>{money(r.earned)}</td>
                <td><div className="earn-bar"><span className={r.earned < 0 ? "neg-bar" : ""} style={{ width: `${(Math.abs(r.earned) / biggest) * 100}%` }} /></div></td>
                <td className="r num muted">{money(r.cumulative)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </div>
      <div className="card">
        <div className="card-h"><div><h3>Who pays</h3><div className="hint">{e.n_payers} payer{e.n_payers === 1 ? "" : "s"} in the counted income.{e.concentrated ? ` One payer is above the ${pct(e.concentration_limit)} limit.` : ""}</div></div>{e.concentrated && <span className="pill warn">Depends on one payer</span>}</div>
        <table className="tbl">
          <thead><tr><th>Payer</th><th className="r">Months</th><th className="r">Payments</th><th className="r">Amount</th><th className="r">Share</th></tr></thead>
          <tbody>{e.payers.map((p) => (
            <tr key={p.name} className="click" onClick={() => onTxns(p.txn_ids, `Payments from ${p.name}`)}><td>{p.name}</td><td className="r num">{p.months}</td><td className="r num">{p.count}</td><td className="r num">{money(p.amount)}</td><td className="r num">{pct(p.share)}</td></tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  );
}
