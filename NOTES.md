# Notes

`POST /webhooks/payments` reconciles a payment on receipt: it records every
delivery, matches it to a loan, applies or rejects it, closes the loan when the
debt is settled, and audits the outcome, all in one transaction.

Both extensions are included: an admin reconciliation panel built around what an
operator needs to act on, and concurrency handling for payments racing on the
same reference or the same loan. The optional provider signature check is also
in, alongside the shared token.

**71 tests pass** (11 provided, 60 added). The provided spec is untouched.

```bash
./run-local.sh      # API on :8137, UI on :5137
pytest              # 71 passed
```

---

## The two correctness traps

### 1. Money stored as `Float`

`models.py` says the schema is inherited and that the `Float` columns should not
be assumed correct. They are not.

Most decimal amounts have no exact binary representation, so sums drift. One
payment is fine; the drift only appears once amounts accumulate, which is how
real loans are repaid.

```
total_repayable = 48,702.38
partials        = 3,622.74 + 26,794.56 + 18,285.08   # sums to the total on paper
outstanding     = -7.27e-12                          # not 0.0
```

A residue above zero means a fully repaid loan never reaches `paid_off` and the
borrower still shows as owing. A residue below zero means the loan is silently
overpaid, and a naive `amount > outstanding` check then rejects every later
payment. Both render as ₦0.00, so nobody can see the disagreement.

**Fix:** `app/money.py` converts to integer kobo at the boundary. Every
comparison and every sum happens in integers; the float columns are written once
at the end, as storage. No decision is made on a float.

The conversion rounds rather than truncates: `1.15 * 100` is `114.99999999999999`,
so `int()` would drop a kobo on that whole class of value, always in the
lender's favour.

**Caught by:** `test_loan_paid_in_instalments_closes_exactly`. Reverting to float
accumulation fails it while every test in the provided spec still passes, which
is why the trap survives a green suite.

### 2. Rails redeliver, and the obvious guard has a race

`external_ref` is deliberately not unique at the DB level. Looking the reference
up and then crediting is check-then-act: two redeliveries arriving together both
read before either commits, both see nothing, and both credit money that came in
once.

Application code cannot close that gap, because two requests cannot see each
other. Only the database sees every writer.

**Fix, in two layers:**

- `uq_applied_external_ref`, a **partial** unique index covering only `applied`
  rows. The contract requires recording every delivery including the rejected
  duplicates, and a full unique index would block writing those rows. On a
  collision the database raises `IntegrityError`, which *is* the duplicate
  signal.
- `_already_applied`, a lookup that catches the ordinary sequential redelivery.
  It exists for the **reason**, not the protection: without it, a redelivery of
  the payment that closed a loan falls through to the next rule and reports
  "loan not active" — true, but it puts benign rail noise in an operator's queue
  as a loan problem.

Neither is redundant. The lookup cannot be made safe under concurrency; the index
cannot produce a useful reason.

---

## Key decisions

**Rejections return `200` with a reason.** The status code answers "did we
receive your message", not "did we like it". A 4xx or 5xx tells the rail delivery
failed and it redelivers, so a correctly refused payment would trigger a retry
storm. `401` is the exception: an unauthenticated request is not recorded at all.

**One transaction.** The event row, the ledger row, the balance, the loan status
and the audit record commit together. Committing the event first "so we have a
record no matter what" is the tempting shape and it is wrong: a crash between the
two commits leaves a payment recorded but never applied.

**Rejection order: unknown loan, duplicate, loan not active, overpayment.** A
payment can fail several checks at once and only one reason is stored. Duplicates
are classified before loan state for the reason above. Unknown loan outranks
everything because there is no loan to reason about.

**Overpayment is rejected in full, not part-applied.** That is what the spec
requires, and part-applying would leave the remainder in limbo with no record of
where it went. It is not what I would ship to a real lender: the money has
already left the borrower's account, so bouncing it into the void is not neutral.
In production the excess should post to a **suspense account** for an operator to
refund or reallocate, with the payment marked as partially settled. That needs a
ledger account this schema does not have.

**The `Float` columns were left alone.** The right fix is a migration to integer
minor units or `NUMERIC`, but this is described as an inherited production
schema, and changing a money column type there is a planned migration with a
backfill and a rollback plan, not a take-home diff.

**Signature auth alongside the token.** A shared token only proves the caller
once saw the secret. It says nothing about the message: a captured request
replays forever, and anything relaying it could raise the amount with the token
still matching. `X-Webhook-Signature` carries an HMAC-SHA256 over
`{timestamp}.{raw body}`. Three details matter: the raw body is read from the
request rather than re-serialised from the parsed model, since different key
order or whitespace would never match; the timestamp is inside the signed
material, so a captured body cannot be replayed with a fresh timestamp; and all
comparisons use `hmac.compare_digest`, including on the token path, because a
plain `!=` returns early on the first wrong character and leaks a secret one byte
at a time. The token still works, which is also the real migration shape.

---

## Bugs found in my own work

These were found by review and by probing the running system, not by the suite
going green. Each now has a test.

**A lost update when two payments settle one loan.** The first concurrency
implementation used a `SELECT ... FOR UPDATE` row lock, with a comment asserting
that SQLite serialises writers so the lock was belt-and-braces locally. I did not
trust the comment and checked the emitted SQL: `with_for_update()` compiles to a
plain `SELECT` on SQLite, because the dialect has no row-lock syntax. A
two-thread test then reproduced the bug — ₦20,000 and ₦15,000 arrived together,
both returned `200 applied`, both wrote ledger rows, and the balance showed
₦15,000. ₦20,000 vanished.

The balance is no longer written from a value read earlier. The `UPDATE` carries
the previously read `total_paid` in its `WHERE` clause, making the read and write
one atomic step; if the balance moved, the statement matches no rows and the
payment is decided again against the new one. That works on both dialects. The
row lock stays for Postgres, where it does hold and keeps retries rare.

I tested two simpler shapes and rejected both. Letting SQL do the addition
(`total_paid = total_paid + amount`) reintroduces the float drift from trap 1 and
fails the instalment test. Dropping the retry and returning `503` bounces a
legitimate payment that could simply be re-decided.

**Sub-kobo amounts reported success while crediting nothing.** An amount of
`0.004` returned `applied` and wrote a ledger row, having moved the balance by
zero, because it rounds to no kobo. `100.005` credited `100.00` while storing
`100.005` on the event and the ledger, leaving the books half a kobo adrift.
Both are now refused at the schema. One kobo is still valid.

**Rejections were not audited.** The first version audited applied payments only.
Rejections are the ones that need explaining, since they leave no ledger row and
no balance change. "We received money and did not credit it" is exactly what
someone needs to reconstruct in a dispute.

**A redelivery read as "loan not active".** The first rule ordering checked loan
state before duplicates, so a redelivery of the payment that closed a loan was
reported as a closed-loan problem. The money was always correct; the reason was
misleading, and the panel groups on reasons.

---

## How the tests were written

The provided spec pins the contract. It does not catch either trap: with float
accumulation restored, every test in it still passes.

So the added tests were verified by **mutation** — each defect reintroduced, and
the suite checked to fail on it:

| Defect reintroduced | Tests failing |
| --- | --- |
| `int()` instead of `round()` in kobo conversion | 2 |
| Unique index removed | 1 |
| Duplicate lookup removed | 1 |
| Rejection order swapped | 1 |
| Compare-and-set weakened | 2 |
| Rejection audit removed | 2 |
| Signature replay window removed | 2 |
| Timestamp excluded from the signature | 1 |

Two escaped the first draft. Removing the unique index left the whole suite
green, because `_already_applied` covered for it; and narrowing the lookup to
`(ref, loan_id)` also stayed green, because the index covered for that. The two
defences mask each other through the API, so no endpoint test can tell which is
working. Both now have unit tests that isolate them —
`test_database_rejects_a_second_applied_row_for_one_reference` writes rows
directly, and `test_already_applied_lookup_ignores_the_loan` calls the lookup.

---

## The admin panel

The provided feed is a log: everything that happened, newest first, applied and
rejected together. That answers "what happened". An operator opens the console
asking "what needs me", so the panel answers that first and leaves the feed below
for tracing a single reference.

**One page, no routing.** The brief asks for issues front and centre. Behind a
tab they are one click away from never being seen, and the real workflow — read
"2 payments to closed loans", then trace `GSI-4410` in the feed — is a scroll
rather than a round trip.

**Grouped by cause, not listed by time,** because the response differs by cause.
A duplicate is rail noise that needs nobody. An overpayment is money in the wrong
place. A payment to a closed loan may mean a borrower paid against the wrong
reference. The same red badge covers three different mornings.

**Written for the reader.** Reason codes never appear raw. `loan_not_active`
becomes "Loan already closed", with a plain explanation and what to do about it.
`reasons.js` is the single vocabulary, used by the panel, the feed and the trail;
the audit records keep their machine codes, since a record written for durability
should not be rewritten to read nicely.

**"Needs attention" excludes duplicates.** Counting them would inflate the number
every time a rail redelivered, and a number that cries wolf gets ignored, which is
how the real exceptions get missed.

**An unrecognised reason code falls back to "unclassified"** rather than
disappearing. The failure mode of a silent drop is an exception nobody sees.

No new dependencies, and the provided screen and design tokens are reused rather
than replaced.

---

## What I would flag before this ships to a real lender

**The money columns.** Integer kobo makes the arithmetic correct, but the columns
are still `Float`. Migrate to integer minor units or `NUMERIC` before this
handles real balances.

**Overpayments need somewhere to go.** Rejecting is correct here and wrong in
production. The money has left the borrower's account; it needs a suspense
account and an operator workflow, not a rejection.

**Concurrency is only proven on the paths I tested.** The compare-and-set is
correct on both dialects and verified under load locally, but SQLite cannot
exercise the Postgres row lock. This needs a run against Postgres before it is
trusted with real traffic.

**No rate limiting or payload size limit on the webhook.** A misbehaving rail, or
anyone who obtains the token, can flood the events table and the operator's
screen.

**Secrets.** Both are read from the environment but fall back to the values in
this repo. A real deployment should refuse to start without them.

**The audit endpoint has no filtering.** It returns the recent tail, which is
enough for triage but not for investigating one account. It needs filters by
loan, actor and date range, and pagination.

**No observability.** There are no metrics on reconciliation rate, rejection
reasons or retry frequency. A rising failure rate should page someone rather than
wait to be noticed on a dashboard.

**Loan status transitions are not modelled.** Nothing prevents a `paid_off` loan
being moved back to `active` by another code path, which would let a settled loan
take payments again.

---

## How I used AI

I used Claude for most of the implementation, which the brief encourages, and
worked as the reviewer and director rather than accepting what came out. I set
the structure and the standards up front: separate deciding from doing, keep
functions to one job, no new dependencies, comments that explain why rather than
what, and incremental commits rather than one drop.

Where I pushed back:

**Concurrency.** The first answer was the `SELECT ... FOR UPDATE` lock described
above, with a confident comment that SQLite serialises writers anyway. I did not
accept that and asked for the emitted SQL to be checked; it turned out to be a
plain `SELECT` with no locking, and a two-thread test reproduced a real lost
update. I had it replaced with compare-and-set, and had two simpler alternatives
implemented and tested before settling, to confirm the retry was necessary rather
than defensive.

**Testing method.** Passing tests prove nothing about whether they would catch a
regression, so I asked for every defence to be deliberately broken and the suite
re-run. That is what exposed the two tests that were not catching their bug.

**The panel.** The layout, the decision to keep it on one page rather than behind
tabs, and the call to surface issues above the feed were mine, for the reasons in
that section. I also sent back the first version because it printed raw reason
codes on screen, and again because the critical groups carried a red left border
on top of an already red badge, which is emphasis competing with itself.

**Scope.** I declined suggestions that would have expanded the footprint without
serving the brief: migrating the schema, adding frontend tooling the repo does
not ship with, and adding routing for the panel.

I also verified the behaviour myself rather than trusting the suite: running the
webhook against every rejection path, watching the instalment payoff close at
exactly zero, checking both auth paths against a live server, and clicking
through the UI to confirm the panel matched what the API returned. Probing the
running endpoint is how the sub-kobo bug surfaced, and it is not something the
tests would have found on their own.
