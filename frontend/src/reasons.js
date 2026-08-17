// How a rejection is described to the person who has to deal with it.
//
// The codes come from RejectionReason in app/payments.py. An operator is not
// an engineer: "duplicate_external_ref" tells them nothing about whether to
// act, so each reason carries a plain-language explanation and — the part that
// matters — what to do about it.
//
// Grouping by reason is deliberate. A duplicate is routine rail noise that
// needs nobody. An overpayment is money sitting in the wrong place. A payment
// to a closed loan may be a borrower paying against the wrong reference. The
// same red badge covers three completely different mornings, so the panel
// separates them.

export const UNCLASSIFIED = "unclassified";

export const REASONS = {
  duplicate_external_ref: {
    label: "Duplicate payment",
    severity: "info",
    // Not a problem: the guard worked. Shown so the count is explainable.
    explanation:
      "The rail delivered this payment more than once. It was counted once — the repeats were not applied.",
    action: "No action needed unless the borrower says they paid twice.",
  },
  overpayment: {
    label: "Overpayment",
    severity: "critical",
    explanation:
      "The amount is more than the loan still owes, so none of it was applied.",
    action: "Refund the borrower or post the excess to suspense, then re-send the correct amount.",
  },
  loan_not_active: {
    label: "Loan already closed",
    severity: "critical",
    explanation:
      "The loan is settled, cancelled or written off, so it cannot take a payment.",
    action: "Check whether the borrower paid against the wrong reference — their money is elsewhere.",
  },
  unknown_loan: {
    label: "Unknown loan",
    severity: "critical",
    explanation:
      "No loan matches the reference on this payment. The money arrived with nowhere to go.",
    action: "Trace the payment with the rail and identify the borrower before it ages.",
  },
  [UNCLASSIFIED]: {
    label: "Unclassified",
    severity: "critical",
    // Defensive: a reason the backend added and the UI has not learnt yet must
    // still surface as needing attention, never disappear from the queue.
    explanation: "This payment was rejected for a reason this screen does not recognise.",
    action: "Check the audit trail for the raw reason.",
  },
};

export function describeReason(code) {
  return REASONS[code] ?? REASONS[UNCLASSIFIED];
}

// Duplicates are expected noise; everything else is a genuine exception.
export function needsAttention(code) {
  return describeReason(code).severity === "critical";
}
