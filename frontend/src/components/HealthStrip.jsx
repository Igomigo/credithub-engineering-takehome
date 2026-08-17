import React from "react";
import { needsAttention } from "../reasons";

// Reconciliation health at a glance.
//
// "Needs attention" is deliberately not the same as "rejected": duplicates are
// rejections that require nobody. Counting them as problems would inflate the
// number every time a rail redelivered, and a number that cries wolf gets
// ignored — which is how the real exceptions get missed.

const ngn = new Intl.NumberFormat("en-NG", {
  style: "currency",
  currency: "NGN",
  maximumFractionDigits: 0,
});

export function summarise(events) {
  const applied = events.filter((e) => e.status === "applied");
  const rejected = events.filter((e) => e.status === "rejected");
  const attention = rejected.filter((e) => needsAttention(e.reason));
  const settled = applied.length + rejected.length;

  return {
    appliedCount: applied.length,
    rejectedCount: rejected.length,
    attentionCount: attention.length,
    reconciledValue: applied.reduce((sum, e) => sum + e.amount, 0),
    // Of payments actually reconciled — pending ones haven't succeeded or
    // failed yet, so including them would understate the rate.
    failureRate: settled === 0 ? 0 : rejected.length / settled,
  };
}

export default function HealthStrip({ events }) {
  const s = summarise(events);

  return (
    <div className="stats health">
      <div className={`stat ${s.attentionCount > 0 ? "stat-alert" : ""}`}>
        <div className="k">Needs attention</div>
        <div className="v">{s.attentionCount}</div>
        <div className="stat-foot">
          {s.attentionCount === 0 ? "Nothing outstanding" : "Unresolved exceptions"}
        </div>
      </div>

      <div className="stat">
        <div className="k">Reconciled</div>
        <div className="v">{ngn.format(s.reconciledValue)}</div>
        <div className="stat-foot">
          {s.appliedCount} payment{s.appliedCount === 1 ? "" : "s"} applied
        </div>
      </div>

      <div className="stat">
        <div className="k">Failure rate</div>
        <div className="v">{(s.failureRate * 100).toFixed(1)}%</div>
        <div className="stat-foot">
          {s.rejectedCount} of {s.appliedCount + s.rejectedCount} rejected
        </div>
      </div>
    </div>
  );
}
