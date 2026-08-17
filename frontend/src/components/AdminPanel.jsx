import React from "react";
import ActivityTrail from "./ActivityTrail";
import HealthStrip from "./HealthStrip";
import IssuesPanel from "./IssuesPanel";

// The operator's view, above the raw feed.
//
// Ordering answers the question someone actually opens this with — "what needs
// me?" — before the question "what happened?". The provided feed is still
// below, unchanged: it is the right tool for tracing one specific reference,
// just the wrong one for triage.

export default function AdminPanel({ events, auditEntries }) {
  return (
    <>
      <div className="section-title">Reconciliation</div>
      <HealthStrip events={events} />
      <IssuesPanel events={events} />
      <div className="spacer" />
      <ActivityTrail entries={auditEntries} />
    </>
  );
}
