import { useCallback, useEffect, useMemo, useState } from "react";
import { Copy, Plus, Save, Trash2 } from "lucide-react";
import { api } from "../api";
import type { EngineConfig, EngineSummary, RuleEnginesData } from "../types";
import { dateLabel } from "../fmt";
import { useCats, useToast } from "../components/ui";

interface Props { canEditEngines: boolean; onChanged: () => void }

const rupees = (paise: number) => String(Number(paise) / 100);
const fromRupees = (v: string) => Math.round(Number(v) * 100);

function asShown(unit: string, v: number) { return unit === "paise" ? rupees(v) : unit === "share" ? String(Math.round(Number(v) * 10000) / 100) : String(v); }
function asStored(unit: string, v: string) { return unit === "paise" ? fromRupees(v) : unit === "share" ? Number(v) / 100 : Number(v); }
const unitLabel: Record<string, string> = { paise: "₹", share: "%", ratio: "ratio", count: "count" };

export default function RuleEngines({ canEditEngines, onChanged }: Props) {
  const cats = useCats();
  const toast = useToast();
  const [data, setData] = useState<RuleEnginesData | null>(null);
  const [selected, setSelected] = useState("standard");
  const [draft, setDraft] = useState<EngineConfig | null>(null);
  const [note, setNote] = useState("");
  const [history, setHistory] = useState<{ version: number; actor: string; note: string; created_at: string }[]>([]);
  const [copyName, setCopyName] = useState("");
  const [rule, setRule] = useState({ keyword: "", direction: "C", category: "SALARY", weight: "3" });

  const load = useCallback(async (id?: string) => {
    const d = await api.ruleEngines();
    setData(d);
    const pick = id ?? selected;
    const e = d.engines.find((x) => x.engine_id === pick) ?? d.engines[0];
    setSelected(e.engine_id);
    setDraft(JSON.parse(JSON.stringify(e.config)));
    setNote("");
    api.ruleEngine(e.engine_id).then((x) => setHistory(x.history));
  }, [selected]);
  useEffect(() => { load().catch((e) => toast({ kind: "err", title: "Could not load rule engines", body: String(e.message) })); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const engine: EngineSummary | undefined = data?.engines.find((e) => e.engine_id === selected);
  const groups = useMemo(() => {
    const g: Record<string, RuleEnginesData["schema"]> = {};
    data?.schema.forEach((p) => { (g[p.group] = g[p.group] ?? []).push(p); });
    return Object.entries(g);
  }, [data]);
  if (!data || !draft || !engine) return <div className="content-inner"><div className="skeleton" style={{ height: 300 }} /></div>;
  const editable = canEditEngines && engine.editable;

  const setParam = (k: string, v: number | string | boolean) => setDraft({ ...draft, params: { ...draft.params, [k]: v } });
  const setScore = (k: string, f: string, v: unknown) => setDraft({ ...draft, score: { ...draft.score, parameters: { ...draft.score.parameters, [k]: { ...draft.score.parameters[k], [f]: v } } } });
  const totalWeight = Object.values(draft.score.parameters).filter((p) => p.enabled).reduce((s, p) => s + Number(p.weight), 0);

  async function save() {
    try {
      const e = await api.saveRuleEngine(selected, draft!, note);
      await load(e.engine_id);
      onChanged();
      toast({ title: `Saved as version ${e.version}`, body: "Cases using this engine recalculate the next time they are opened." });
    } catch (err) { toast({ kind: "err", title: "Not saved", body: String((err as Error).message) }); }
  }
  async function copy() {
    try {
      const e = await api.createRuleEngine({ name: copyName, based_on: selected });
      setCopyName("");
      await load(e.engine_id);
      toast({ title: `${e.name} created`, body: `Copied from ${engine!.name}. Change its settings and save.` });
    } catch (err) { toast({ kind: "err", title: "Not created", body: String((err as Error).message) }); }
  }
  async function remove() {
    try {
      await api.deleteRuleEngine(selected);
      await load("standard");
      toast({ title: "Engine deleted" });
    } catch (err) { toast({ kind: "err", title: "Not deleted", body: String((err as Error).message) }); }
  }
  function addRule() {
    setDraft({ ...draft!, custom_rules: [...draft!.custom_rules, { keyword: rule.keyword.toUpperCase(), direction: rule.direction, category: rule.category, weight: Number(rule.weight) }] });
    setRule({ ...rule, keyword: "" });
  }

  return (
    <div className="content-inner">
      <div className="card">
        <div className="card-h"><div><h3>Rule engines</h3><div className="hint">Each lender sets its own rules, limited to the parameters in Level 1 of the problem statement. A rule engine decides what is counted and how the score is built. It does not approve or decline credit.</div></div></div>
        <table className="tbl">
          <thead><tr><th>Engine</th><th>What it is</th><th className="r">Version</th><th className="r">Cases</th></tr></thead>
          <tbody>{data.engines.map((e) => (
            <tr key={e.engine_id} className={`click ${e.engine_id === selected ? "sel" : ""}`} onClick={() => load(e.engine_id)}>
              <td style={{ whiteSpace: "nowrap" }}><b>{e.name}</b></td><td className="small muted">{e.description}</td><td className="r num">v{e.version}</td><td className="r num">{e.cases}</td>
            </tr>
          ))}</tbody>
        </table>
        {canEditEngines ? (
          <div className="card-b row wrap">
            <input id="engine-copy-name" className="input" style={{ flex: 1, minWidth: 220 }} aria-label="New engine name" placeholder={`Name for a copy of ${engine.name}`} value={copyName} onChange={(e) => setCopyName(e.target.value)} />
            <button className="btn primary sm" disabled={copyName.trim().length < 3} onClick={copy}><Copy size={13} /> Create engine</button>
            {editable && <button className="btn sm" onClick={remove}><Trash2 size={13} /> Delete this engine</button>}
          </div>
        ) : <div className="card-b"><div className="note">Only a Senior reviewer or Administrator can change rule engines. Switch role in the sidebar.</div></div>}
      </div>

      {!engine.editable && canEditEngines && <div className="note info">{engine.name} is the reference and cannot be changed. Create a copy above to set up your own.</div>}

      {groups.map(([group, params]) => (
        <div className="card" key={group}>
          <div className="card-h"><h3>{group}</h3></div>
          <div className="card-b engine-grid">
            {params.map((p) => {
              const v = draft.params[p.key];
              const id = `param-${p.key}`;
              return (
                <div className="engine-field" key={p.key}>
                  <label className="label" htmlFor={id}>{p.label}</label>
                  {p.kind === "bool" ? <input id={id} type="checkbox" disabled={!editable} checked={Boolean(v)} onChange={(e) => setParam(p.key, e.target.checked)} />
                    : p.kind.startsWith("choice:") ? <select id={id} className="select" disabled={!editable} value={String(v)} onChange={(e) => setParam(p.key, e.target.value)}>{p.kind.slice(7).split(",").map((o) => <option key={o}>{o}</option>)}</select>
                    : p.kind === "paise" ? <div className="row"><span className="small muted">₹</span><input id={id} className="input" disabled={!editable} value={rupees(Number(v))} onChange={(e) => setParam(p.key, fromRupees(e.target.value))} /></div>
                    : p.kind === "share" ? <div className="row"><input id={id} className="input" disabled={!editable} value={String(Math.round(Number(v) * 10000) / 100)} onChange={(e) => setParam(p.key, Number(e.target.value) / 100)} /><span className="small muted">%</span></div>
                    : <input id={id} className="input" disabled={!editable} value={String(v)} onChange={(e) => setParam(p.key, Number(e.target.value))} />}
                  <div className="small muted">{p.help ? `${p.help} ` : ""}From the brief: {p.source}.</div>
                </div>
              );
            })}
          </div>
        </div>
      ))}

      <div className="card">
        <div className="card-h"><div><h3>Keyword rules</h3><div className="hint">Teach this lender's wording. A keyword adds points toward one of the Level 1 categories, shown as the reason on each transaction.</div></div></div>
        {draft.custom_rules.length > 0 && (
          <table className="tbl">
            <thead><tr><th>Keyword</th><th>Applies to</th><th>Category</th><th className="r">Points</th><th /></tr></thead>
            <tbody>{draft.custom_rules.map((r, i) => (
              <tr key={i}><td className="mono small">{r.keyword}</td><td className="small">{r.direction === "C" ? "Credits" : r.direction === "D" ? "Debits" : "Both"}</td><td className="small">{cats[r.category]?.label ?? r.category}</td><td className="r num">{r.weight}</td>
                <td className="r">{editable && <button className="btn ghost sm" onClick={() => setDraft({ ...draft, custom_rules: draft.custom_rules.filter((_, j) => j !== i) })}>Remove</button>}</td></tr>
            ))}</tbody>
          </table>
        )}
        {editable ? (
          <div className="card-b row wrap">
            <input id="rule-keyword" className="input" style={{ flex: 1, minWidth: 180 }} aria-label="Keyword" placeholder="Keyword, such as PAGAR" value={rule.keyword} onChange={(e) => setRule({ ...rule, keyword: e.target.value })} />
            <select id="rule-direction" className="select" aria-label="Applies to" value={rule.direction} onChange={(e) => setRule({ ...rule, direction: e.target.value })}><option value="C">Credits</option><option value="D">Debits</option><option value="B">Both</option></select>
            <select id="rule-category" className="select" aria-label="Category" value={rule.category} onChange={(e) => setRule({ ...rule, category: e.target.value })}>{data.rule_categories.map((c) => <option key={c} value={c}>{cats[c]?.label ?? c}</option>)}</select>
            <input id="rule-weight" className="input" style={{ width: 70 }} aria-label="Points" value={rule.weight} onChange={(e) => setRule({ ...rule, weight: e.target.value })} />
            <button className="btn sm" disabled={rule.keyword.trim().length < 3} onClick={addRule}><Plus size={13} /> Add rule</button>
          </div>
        ) : draft.custom_rules.length === 0 && <div className="card-b small muted">No keyword rules in this engine.</div>}
      </div>

      <div className="card">
        <div className="card-h"><div><h3>Credit score</h3><div className="hint">Points run in a straight line from the zero-points value to the full-points value. Weights in use total {totalWeight}. The score is scaled to 0 to 100 either way.</div></div></div>
        <div style={{ overflowX: "auto" }}>
          <table className="tbl">
            <thead><tr><th>Use</th><th>Level 1 parameter</th><th className="r">Weight</th><th className="r">Full points at</th><th className="r">Zero points at</th><th>Unit</th></tr></thead>
            <tbody>{Object.entries(data.score_parameters).map(([k, d]) => {
              const p = draft.score.parameters[k];
              return (
                <tr key={k}>
                  <td><input id={`score-${k}-enabled`} aria-label={`Use ${d.label}`} type="checkbox" disabled={!editable} checked={p.enabled} onChange={(e) => setScore(k, "enabled", e.target.checked)} /></td>
                  <td>{d.label}<div className="small muted">{d.source}</div></td>
                  <td className="r"><input id={`score-${k}-weight`} aria-label={`Weight for ${d.label}`} className="input num-input" disabled={!editable} value={String(p.weight)} onChange={(e) => setScore(k, "weight", Number(e.target.value))} /></td>
                  <td className="r"><input id={`score-${k}-full`} aria-label={`Full points for ${d.label}`} className="input num-input" disabled={!editable} value={asShown(d.unit, p.full_at)} onChange={(e) => setScore(k, "full_at", asStored(d.unit, e.target.value))} /></td>
                  <td className="r"><input id={`score-${k}-zero`} aria-label={`Zero points for ${d.label}`} className="input num-input" disabled={!editable} value={asShown(d.unit, p.zero_at)} onChange={(e) => setScore(k, "zero_at", asStored(d.unit, e.target.value))} /></td>
                  <td className="small muted">{unitLabel[d.unit] ?? d.unit}</td>
                </tr>
              );
            })}</tbody>
          </table>
        </div>
        <div className="card-b row wrap">
          <span className="label">Score bands, from</span>
          {draft.score.bands.map((b, i) => (
            <span className="row" key={i} style={{ gap: 4 }}>
              <input id={`band-${i}-min`} aria-label={`Lower limit of band ${i + 1}`} className="input num-input" disabled={!editable} value={String(b.min)} onChange={(e) => setDraft({ ...draft, score: { ...draft.score, bands: draft.score.bands.map((x, j) => (j === i ? { ...x, min: Number(e.target.value) } : x)) } })} />
              <input id={`band-${i}-label`} aria-label={`Name of band ${i + 1}`} className="input" style={{ width: 120 }} disabled={!editable} value={b.label} onChange={(e) => setDraft({ ...draft, score: { ...draft.score, bands: draft.score.bands.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)) } })} />
            </span>
          ))}
          <label className="label" htmlFor="score-min-assessed">No score below assessed weight of (%)</label>
          <input id="score-min-assessed" className="input num-input" disabled={!editable} value={String(draft.score.min_assessed_weight)} onChange={(e) => setDraft({ ...draft, score: { ...draft.score, min_assessed_weight: Number(e.target.value) } })} />
        </div>
      </div>

      {editable && (
        <div className="card"><div className="card-b row wrap">
          <input id="engine-note" className="input" style={{ flex: 1, minWidth: 240 }} aria-label="What changed" placeholder="What changed, for the version history" value={note} onChange={(e) => setNote(e.target.value)} />
          <button className="btn primary" onClick={save}><Save size={14} /> Save as a new version</button>
        </div></div>
      )}

      <div className="card">
        <div className="card-h"><h3>Version history: {engine.name}</h3></div>
        <table className="tbl">
          <thead><tr><th>Version</th><th>When</th><th>By</th><th>What changed</th></tr></thead>
          <tbody>{history.map((h) => <tr key={h.version}><td className="num">v{h.version}</td><td className="small">{dateLabel(h.created_at)}</td><td className="small">{h.actor}</td><td className="small">{h.note}</td></tr>)}</tbody>
        </table>
      </div>
    </div>
  );
}
