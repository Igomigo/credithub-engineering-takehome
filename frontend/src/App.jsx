import React, { useCallback, useEffect, useState } from "react";
import AdminPanel from "./components/AdminPanel";
import { describeReason } from "./reasons";
import "./styles.css";

// Servicing console. The feed and loan balances below are the provided screen,
// left as they were — the raw log is the right tool for tracing one reference.
// The admin panel above it is the operator's view: what needs attention now.
// (The token below mirrors app/auth.py.)

const WEBHOOK_TOKEN = "dev-webhook-secret";

const ngn = new Intl.NumberFormat("en-NG", {
  style: "currency",
  currency: "NGN",
  maximumFractionDigits: 2,
});

const LOAN_LABEL = { active: "Active", paid_off: "Paid off", cancelled: "Cancelled", written_off: "Written off" };
const PAY_LABEL = { pending: "Pending", applied: "Applied", rejected: "Rejected" };

export default function App() {
  const [loans, setLoans] = useState(null);
  const [events, setEvents] = useState(null);
  const [auditEntries, setAuditEntries] = useState([]);
  const [error, setError] = useState(null);
  const [note, setNote] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setError(null);
    Promise.all([
      fetch("/loans").then((r) => { if (!r.ok) throw new Error(`loans HTTP ${r.status}`); return r.json(); }),
      fetch("/payment-events").then((r) => { if (!r.ok) throw new Error(`events HTTP ${r.status}`); return r.json(); }),
    ])
      .then(([l, e]) => { setLoans(l); setEvents(e); })
      .catch((err) => setError(String(err.message || err)));

    // Fetched separately: the trail is supplementary, and losing it should not
    // blank out the balances and the issues queue, which are what the screen
    // is for.
    fetch("/audit-log")
      .then((r) => (r.ok ? r.json() : []))
      .then(setAuditEntries)
      .catch(() => setAuditEntries([]));
  }, []);

  useEffect(load, [load]);

  // POST a synthetic payment to the webhook — the "a payment just arrived" event.
  const send = async (payload) => {
    setBusy(true);
    setNote(null);
    try {
      const r = await fetch("/webhooks/payments", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Webhook-Token": WEBHOOK_TOKEN },
        body: JSON.stringify(payload),
      });
      if (r.status === 501) {
        setNote("The payments webhook isn’t built yet — that’s your task (POST /webhooks/payments).");
      } else if (!r.ok) {
        setNote(`Webhook returned ${r.status}.`);
      }
    } catch (e) {
      setNote(String(e));
    } finally {
      setBusy(false);
      load();
    }
  };

  const simulate = () => {
    const list = loans ?? [];
    if (!list.length) return;
    const loan = list[Math.floor(Math.random() * list.length)];
    const amount = [5000, 10000, 20000, loan.outstanding][Math.floor(Math.random() * 4)];
    send({
      external_ref: "SIM-" + crypto.randomUUID().slice(0, 8).toUpperCase(),
      loan_id: loan.id,
      amount,
      channel: "paystack",
    });
  };

  // Re-fire an existing payment with its original reference — a rail redelivery.
  const resend = (e) =>
    send({ external_ref: e.external_ref, loan_id: e.loan_id, amount: e.amount, channel: e.channel });

  const loanList = loans ?? [];
  const eventList = events ?? [];
  const active = loanList.filter((l) => l.status === "active");
  const outstanding = active.reduce((s, l) => s + (l.outstanding || 0), 0);

  return (
    <div className="app">
      <div className="brand">
        <h1>CreditHub</h1>
        <span className="tag">· Loan Servicing</span>
      </div>
      <p className="sub">Payments arrive from the rails and reconcile against loans on receipt.</p>

      {error && (
        <div className="banner">
          Couldn’t reach the API on <b>:8137</b> — is it running? ({error})
        </div>
      )}
      {note && <div className="banner note">{note}</div>}

      <div className="stats">
        <div className="stat"><div className="k">Active loans</div><div className="v">{loans ? active.length : "—"}</div></div>
        <div className="stat"><div className="k">Outstanding · active</div><div className="v">{loans ? ngn.format(outstanding) : "—"}</div></div>
        <div className="stat"><div className="k">Payments received</div><div className="v">{events ? eventList.length : "—"}</div></div>
      </div>

      {events && <AdminPanel events={eventList} auditEntries={auditEntries} />}

      {/* Payments feed — provided screen, unchanged */}
      <div className="section-title">Payments feed</div>
      <div className="card">
        <div className="card-h">
          <span className="muted issue-subtitle">Every payment received, newest first</span>
          <button className="btn btn-primary" onClick={simulate} disabled={busy || !loans}>
            {busy ? "Sending…" : "Simulate incoming payment"}
          </button>
        </div>
        <table className="feed">
          <thead>
            <tr>
              <th>Reference</th>
              <th>Loan</th>
              <th className="num">Amount</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {events === null && !error &&
              [0, 1, 2].map((i) => (
                <tr key={i}><td colSpan="5"><div className="skeleton" /></td></tr>
              ))}

            {events &&
              eventList.map((e) => (
                <tr key={e.id}>
                  <td className="ref">
                    {e.external_ref}
                    <div className="chan">{e.channel}</div>
                  </td>
                  <td>Loan #{e.loan_id}</td>
                  <td className="num">{ngn.format(e.amount)}</td>
                  <td>
                    <span className={`pbadge ${e.status}`}>{PAY_LABEL[e.status] || e.status}</span>
                    {e.reason ? <div className="chan">{describeReason(e.reason).label}</div> : null}
                  </td>
                  <td className="num">
                    <button className="btn" onClick={() => resend(e)} disabled={busy} title="Redeliver this payment">
                      Resend ↻
                    </button>
                  </td>
                </tr>
              ))}

            {events && eventList.length === 0 && (
              <tr><td colSpan="5" className="muted">No payments yet — hit “Simulate incoming payment”.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Loans */}
      <div className="section-title">Loans</div>
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Borrower</th>
              <th className="num">Principal</th>
              <th className="num">Outstanding</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {loans === null && !error &&
              [0, 1, 2].map((i) => (
                <tr key={i}><td colSpan="4"><div className="skeleton" /></td></tr>
              ))}

            {loans &&
              loanList.map((l) => (
                <tr key={l.id}>
                  <td>
                    <div className="name">{l.borrower_name}</div>
                    <div className="sub-id">Loan #{l.id}</div>
                  </td>
                  <td className="num">{ngn.format(l.principal)}</td>
                  <td className="num">{ngn.format(l.outstanding)}</td>
                  <td><span className={`badge ${l.status}`}>{LOAN_LABEL[l.status] || l.status}</span></td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

    </div>
  );
}
