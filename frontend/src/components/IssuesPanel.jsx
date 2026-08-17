import React from "react";
import { describeReason, needsAttention } from "../reasons";

// The queue an operator works from.
//
// Grouped by cause rather than listed by time, because the response differs by
// cause and a flat list makes someone re-derive that for every row. Genuine
// exceptions sort above duplicates: the guard working as designed is worth
// showing, but never above money that is in the wrong place.

const ngn = new Intl.NumberFormat("en-NG", {
  style: "currency",
  currency: "NGN",
  maximumFractionDigits: 2,
});

export function groupByReason(events) {
  const groups = new Map();

  for (const event of events) {
    if (event.status !== "rejected") continue;
    const reason = describeReason(event.reason);
    const key = reason.label;
    if (!groups.has(key)) {
      groups.set(key, { ...reason, code: event.reason, events: [] });
    }
    groups.get(key).events.push(event);
  }

  return [...groups.values()].sort((a, b) => {
    const bySeverity = Number(needsAttention(b.code)) - Number(needsAttention(a.code));
    return bySeverity !== 0 ? bySeverity : b.events.length - a.events.length;
  });
}

function IssueGroup({ group }) {
  const critical = needsAttention(group.code);

  return (
    <div className={`issue-group ${critical ? "" : "info"}`}>
      <div className="issue-head">
        <span className={`pbadge ${critical ? "rejected" : "pending"}`}>{group.label}</span>
        <span className="issue-count">
          {group.events.length} payment{group.events.length === 1 ? "" : "s"}
        </span>
      </div>

      <p className="issue-explain">{group.explanation}</p>
      <p className="issue-action">
        <b>What to do:</b> {group.action}
      </p>

      <table className="issue-table">
        <tbody>
          {group.events.map((e) => (
            <tr key={e.id}>
              <td className="ref">
                {e.external_ref}
                <div className="chan">{e.channel}</div>
              </td>
              <td>Loan #{e.loan_id}</td>
              <td className="num">{ngn.format(e.amount)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function IssuesPanel({ events }) {
  const groups = groupByReason(events);

  return (
    <div className="card">
      <div className="card-h">
        <span>Issues</span>
        <span className="muted issue-subtitle">Grouped by cause</span>
      </div>

      {groups.length === 0 ? (
        <div className="empty-state">
          Every payment received has been reconciled. Nothing needs attention.
        </div>
      ) : (
        <div className="issue-list">
          {groups.map((group) => (
            <IssueGroup key={group.label} group={group} />
          ))}
        </div>
      )}
    </div>
  );
}
