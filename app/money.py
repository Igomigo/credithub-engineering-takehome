"""Money arithmetic in integer minor units (kobo).

The inherited schema stores NGN as ``Float`` (see ``models.py``), which is not
safe for money: most decimal amounts have no exact binary representation, so
sums drift. The drift is tiny per operation but it accumulates, and in a
servicing system it lands on the two decisions that matter most — "is this loan
fully repaid?" and "does this payment overpay?".

Concretely, with floats::

    total_repayable = 48702.38
    partials        = 3622.74 + 26794.56 + 18285.08   # sums to the total on paper
    outstanding     = -7.275957614183426e-12          # not 0.0

A residue above zero means a fully-repaid loan never reaches ``paid_off`` and
the borrower is still shown as owing. A residue below zero means the loan has
been silently overpaid, and a naive ``amount > outstanding`` check then rejects
every subsequent payment. Neither is visible to an operator, because both
render as ``₦0.00``.

This module is the boundary. Amounts are converted to ``int`` kobo on the way
in, every comparison and subtraction happens in integers — where 1 + 2 == 3
always holds — and results are converted back only to write the columns we
inherited. The float columns still can't represent every value exactly, but
they are only ever *storage* here; no decision is made on a float.

The real fix is a schema migration to integer minor units (or ``NUMERIC``),
which is out of scope for this exercise — see NOTES.md.
"""

KOBO_PER_NAIRA = 100


def to_kobo(naira: float) -> int:
    """Convert naira to whole kobo, rounding to the nearest kobo.

    Rounding rather than truncating is deliberate. ``float`` can land a hair
    below the intended value — ``1.15 * 100`` is ``114.99999999999999``, so
    ``int()`` would truncate to ``114`` and quietly lose a kobo on the way in.
    ``round()`` recovers the intended amount.
    """
    return round(naira * KOBO_PER_NAIRA)


def to_naira(kobo: int) -> float:
    """Convert whole kobo back to naira, for writing to the ``Float`` columns.

    Only for persistence and display — never feed the result back into a
    comparison. Do the arithmetic in kobo and convert once, at the end.
    """
    return kobo / KOBO_PER_NAIRA
