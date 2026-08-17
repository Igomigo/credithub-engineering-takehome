import React from "react";
import { REASONS } from "../reasons";

// The audit trail — what was decided, in order.
//
// Secondary to the issues queue by design: this answers "why does this loan
// look like this?" after the fact, which is a question you go looking for
// rather than one that should compete for attention on arrival.

const time = new Intl.DateTimeFormat("en-NG", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

function formatTime(iso) {
  if (!iso) return "—";
  // Timestamps are recorded in UTC; the browser renders them locally.
  const parsed = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  return Number.isNaN(parsed.getTime()) ? "—" : time.format(parsed);
}

// Audit details are stored with machine codes — the record is written for
// durability, not for reading. Swap them for the operator's wording at render
// time, leaving what was persisted untouched.
const PHRASES = {
  ...Object.fromEntries(
    Object.entries(REASONS).map(([code, reason]) => [code, reason.label.toLowerCase()]),
  ),
  paid_off: "paid off",
  written_off: "written off",
};

function readable(detail) {
  if (!detail) return detail;
  return Object.entries(PHRASES).reduce(
    (text, [code, phrase]) => text.replace(code, phrase),
    detail,
  );
}

export default function ActivityTrail({ entries }) {
  return (
    <div className="card">
      <div className="card-h">
        <span>Activity trail</span>
        <span className="muted issue-subtitle">Newest first</span>
      </div>

      {entries.length === 0 ? (
        <div className="empty-state">No activity recorded yet.</div>
      ) : (
        <table>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.id}>
                <td className="trail-time">{formatTime(entry.created_at)}</td>
                <td>
                  <span className={`pbadge ${entry.action === "payment.applied" ? "applied" : "rejected"}`}>
                    {entry.action === "payment.applied" ? "Applied" : "Rejected"}
                  </span>
                </td>
                <td className="trail-detail">{readable(entry.detail)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
