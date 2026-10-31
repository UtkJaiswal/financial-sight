import { FileSearch } from "lucide-react";
import type { StatementRequestsData } from "../types";
import { money } from "../fmt";

interface Props { requests?: StatementRequestsData; onTxns: (ids: string[], label: string) => void; onUpload?: () => void }

/** Asks for the statements needed to track internal transfers. */
export default function StatementRequests({ requests, onTxns, onUpload }: Props) {
  if (!requests?.prompt) return null;
  return (
    <div className="card" data-testid="statement-requests">
      <div className="card-h">
        <div className="row"><FileSearch size={16} /><div><h3>Statements to ask for</h3><div className="hint">{requests.prompt}</div></div></div>
        {onUpload && <button className="btn sm" onClick={onUpload}>Upload a statement</button>}
      </div>
      {requests.items.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table className="tbl">
            <thead><tr><th>Ask for</th><th>Why</th><th className="r">Entries</th><th className="r">Amount</th></tr></thead>
            <tbody>{requests.items.map((g) => (
              <tr key={g.key} className="click" onClick={() => onTxns(g.txn_ids, g.label)}><td style={{ whiteSpace: "nowrap" }}><b>{g.label}</b></td><td className="small muted">{g.reason}</td><td className="r num">{g.count}</td><td className="r num">{money(g.amount)}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {requests.matched_pairs > 0 && <div className="card-b small muted">Already tracked: {requests.matched_pairs} transfer pair(s), {money(requests.matched_amount)}, across the {requests.accounts_uploaded} uploaded accounts.</div>}
    </div>
  );
}
