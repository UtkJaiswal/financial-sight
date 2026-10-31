import { Fragment, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Layers } from "lucide-react";
import type { Analysis, Txn } from "../types";
import { compact, dateLabel, money, monthLabel } from "../fmt";
import { CategoryChip, Empty, StatusDot, useCats } from "../components/ui";

interface Props { a: Analysis; txns: Txn[]; onOpen: (id: string) => void }

const GROUPS = [
  { key: "income", title: "Income", hint: "Salary and business receipts. Only confirmed rows count toward estimated income.", treatments: ["income"] },
  { key: "obligations", title: "Obligations", hint: "Loan instalments and rent.", treatments: ["obligation"] },
  { key: "spending", title: "Spending", hint: "Everyday and business outflows by what they were for.", treatments: ["expense"] },
  { key: "other", title: "Other credits", hint: "Money received that is not counted as income, such as cash deposits and interest.", treatments: ["review_credit", "other_credit"] },
  { key: "excluded", title: "Left out of the calculations", hint: "Transfers, reversals, loan proceeds and returned payments. These neither add income nor count as spending.", treatments: ["excluded"] },
];
const UNDECIDED = { key: "undecided", title: "Not categorised yet", hint: "No clear evidence. These need an analyst's decision.", treatments: [] as string[] };

const inOut = (cr: number, dr: number) => (cr && dr ? `${compact(cr)} in · ${compact(dr)} out` : money(cr || dr));

export default function Categories({ a, txns, onOpen }: Props) {
  const cats = useCats();
  const [open, setOpen] = useState<string | null>(null);
  const months = useMemo(() => a.monthly.filter((r) => r.active).map((r) => r.month), [a]);
  const groups = useMemo(() => {
    const byCat: Record<string, Txn[]> = {};
    txns.forEach((t) => { (byCat[t.category] = byCat[t.category] ?? []).push(t); });
    const row = (cat: string, ts: Txn[]) => ({
      cat, ts: [...ts].sort((x, y) => y.amount - x.amount),
      total: ts.reduce((s, t) => s + t.amount, 0),
      credits: ts.reduce((s, t) => s + t.credit, 0), debits: ts.reduce((s, t) => s + t.debit, 0),
      pending: ts.filter((t) => t.status === "review" || t.status === "unclassified").length,
      perMonth: Object.fromEntries(months.map((m) => [m, ts.filter((t) => t.txn_date.startsWith(m)).reduce((s, t) => s + t.amount, 0)])),
    });
    return [...GROUPS, UNDECIDED].map((g) => {
      const rows = Object.entries(byCat)
        .filter(([c]) => (g === UNDECIDED ? c === "UNCLASSIFIED" : c !== "UNCLASSIFIED" && g.treatments.includes(cats[c]?.treatment ?? "expense")))
        .map(([c, ts]) => row(c, ts)).sort((x, y) => y.total - x.total);
      return { ...g, rows, count: rows.reduce((s, r) => s + r.ts.length, 0), credits: rows.reduce((s, r) => s + r.credits, 0), debits: rows.reduce((s, r) => s + r.debits, 0) };
    }).filter((g) => g.rows.length);
  }, [txns, cats, months]);

  if (!txns.length) return <div className="content-inner"><Empty icon={<Layers size={32} />} title="Nothing to categorise yet">Upload bank statements in the Statements tab.</Empty></div>;

  return (
    <div className="content-inner">
      {groups.map((g) => (
        <div className="card" key={g.key}>
          <div className="card-h">
            <div><h3>{g.title}</h3><div className="hint">{g.hint}</div></div>
            <div style={{ textAlign: "right" }}><div className="num" style={{ fontWeight: 600 }}>{inOut(g.credits, g.debits)}</div><div className="small muted">{g.count} transaction{g.count === 1 ? "" : "s"}</div></div>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table className="tbl">
              <thead><tr><th>Category</th><th className="r">Count</th><th className="r">Total</th>{months.map((m) => <th className="r" key={m}>{monthLabel(m)}</th>)}<th className="r">To review</th></tr></thead>
              <tbody>
                {g.rows.map((r) => {
                  const id = `${g.key}:${r.cat}`;
                  const isOpen = open === id;
                  return (
                    <Fragment key={r.cat}>
                      <tr className="click" onClick={() => setOpen(isOpen ? null : id)} aria-expanded={isOpen}>
                        <td><div className="row">{isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}<CategoryChip cat={r.cat} /></div></td>
                        <td className="r num">{r.ts.length}</td>
                        <td className="r num">{inOut(r.credits, r.debits)}</td>
                        {months.map((m) => <td className="r num small" key={m}>{r.perMonth[m] ? compact(r.perMonth[m]) : <span className="muted">-</span>}</td>)}
                        <td className="r">{r.pending ? <span className="pill warn">{r.pending}</span> : <span className="muted small">none</span>}</td>
                      </tr>
                      {isOpen && (
                        <tr><td colSpan={months.length + 4} style={{ padding: 0, background: "var(--surface-2)" }}>
                          <table className="tbl"><tbody>
                            {r.ts.map((t) => (
                              <tr key={t.txn_id} className="click" onClick={() => onOpen(t.txn_id)}>
                                <td className="num small" style={{ whiteSpace: "nowrap" }}>{dateLabel(t.txn_date)}</td>
                                <td><StatusDot status={t.status} /></td>
                                <td><span className="desc mono small">{t.description}</span><div className="small muted">{t.explanation}</div></td>
                                <td className={`r num ${t.direction === "C" ? "cr" : ""}`}>{t.direction === "C" ? "+" : "−"}{money(t.amount)}</td>
                              </tr>
                            ))}
                          </tbody></table>
                        </td></tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}
