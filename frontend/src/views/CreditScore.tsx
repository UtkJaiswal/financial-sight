import { useEffect, useState } from "react";
import { api } from "../api";
import type { Analysis, EngineComparison, EngineSummary, ScoreParameter } from "../types";
import { money, monthLabel, pct } from "../fmt";
import { Empty, useToast } from "../components/ui";
import { Gauge } from "lucide-react";

interface Props { caseId: string; a: Analysis; canEdit: boolean; onTxns: (ids: string[], label: string) => void; onChanged: () => void }

export const shownValue = (unit: string, v: number | null) => (v === null || v === undefined ? "—" : unit === "paise" ? money(v) : unit === "share" ? pct(v, 1) : unit === "count" ? String(v) : v.toFixed(2));

export default function CreditScore({ caseId, a, canEdit, onTxns, onChanged }: Props) {
  const toast = useToast();
  const s = a.credit_score;
  const [engines, setEngines] = useState<EngineSummary[]>([]);
  const [compare, setCompare] = useState<EngineComparison[] | null>(null);
  useEffect(() => {
    api.ruleEngines().then((d) => setEngines(d.engines));
    api.engineComparison(caseId).then(setCompare).catch(() => setCompare([]));
  }, [caseId, a.snapshot_id]);
  async function assign(id: string) {
    try {
      await api.assignEngine(caseId, id);
      onChanged();
      toast({ title: "Rule engine changed and figures recalculated" });
    } catch (err) { toast({ kind: "err", title: "Not changed", body: String((err as Error).message) }); }
  }
  const picker = (
    <div className="row">
      <label className="label" htmlFor="case-engine">Rule engine</label>
      <select id="case-engine" className="select" disabled={!canEdit} value={a.engine?.id ?? "standard"} onChange={(e) => assign(e.target.value)}>
        {engines.map((e) => <option key={e.engine_id} value={e.engine_id}>{e.name} (v{e.version})</option>)}
      </select>
    </div>
  );
  if (!s) return <div className="content-inner"><div className="row">{picker}</div><Empty icon={<Gauge size={32} />} title="No score yet">Upload statements. A rate is needed for any statement in a foreign currency.</Empty></div>;
  return (
    <div className="content-inner">
      <div className="card">
        <div className="card-h"><div><h3>Credit score</h3><div className="hint">{s.notice}</div></div>{picker}</div>
        <div className="card-b row wrap" style={{ gap: 28 }}>
          <div>
            <div className="big-amt" data-testid="credit-score">{s.score ?? "No score"}</div>
            <div className="small muted">{s.score !== null ? `out of 100 · ${s.band}` : s.reason}</div>
          </div>
          <div className="small" style={{ flex: 1, minWidth: 240 }}>
            <div><b>Rule engine:</b> {s.engine}, version {s.engine_version}</div>
            <div><b>History used:</b> {s.months_used.length ? `${monthLabel(s.months_used[0])} to ${monthLabel(s.months_used[s.months_used.length - 1])} (${s.months_used.length} covered months)` : "no fully covered months"}</div>
            <div><b>Could be assessed:</b> {s.assessed_weight}% of this engine's weight, and it asks for at least {s.min_assessed_weight}%</div>
            <div><b>Bands:</b> {s.bands.map((b) => `${b.label} from ${b.min}`).join(", ")}</div>
          </div>
        </div>
        {s.score !== null && s.reason && <div className="card-b"><div className="note warn">{s.reason}</div></div>}
        <div style={{ overflowX: "auto" }}>
          <table className="tbl">
            <thead><tr><th>Level 1 parameter</th><th className="r">Measured</th><th className="r">Full points at</th><th className="r">Zero points at</th><th className="r">Points</th><th /></tr></thead>
            <tbody>{s.parameters.map((p: ScoreParameter) => (
              <tr key={p.key}>
                <td>{p.label}<div className="small muted">{p.source}</div>{!p.assessed && <div className="small warn-text">Not assessed: {p.reason}</div>}</td>
                <td className="r num">{shownValue(p.unit, p.value)}</td>
                <td className="r num muted">{shownValue(p.unit, p.full_at)}</td>
                <td className="r num muted">{shownValue(p.unit, p.zero_at)}</td>
                <td className="r num">{p.assessed ? `${p.points} of ${p.weight}` : `— of ${p.weight}`}</td>
                <td className="r">{p.txn_ids.length > 0 && <button className="btn ghost sm" onClick={() => onTxns(p.txn_ids, p.label)}>View {p.txn_ids.length}</button>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </div>
      {compare && compare.length > 0 && (
        <div className="card">
          <div className="card-h"><div><h3>The same statements under each lender's engine</h3><div className="hint">Nothing is saved. Labels stay as they are until an engine is chosen above, because an engine's classification rules apply only once it is in use.</div></div></div>
          <table className="tbl">
            <thead><tr><th>Engine</th><th className="r">Score</th><th>Band</th><th className="r">Estimated income</th><th className="r">Obligations to income</th></tr></thead>
            <tbody>{compare.map((c) => (
              <tr key={c.engine_id} className={c.current ? "sel" : ""}><td>{c.name}{c.current && <span className="small muted"> · in use</span>}</td><td className="r num">{c.score ?? "—"}</td><td className="small">{c.band ?? c.reason ?? "—"}</td><td className="r num">{money(c.income)}</td><td className="r num">{pct(c.foir, 1)}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}
