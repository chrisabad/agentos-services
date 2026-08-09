"""
Invariant checks — deterministic gate for the bookkeeping pipeline.

Each invariant is a function that takes pipeline data and returns
an InvariantResult. A failing invariant BLOCKS the close and requires
human (or judge-agent) intervention before proceeding.

The invariants encode real-world constraints learned from prior Xero failures
and close-calls. They should fail LOUDLY and with enough context for a judge
agent to diagnose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class InvariantResult:
    """Result of a single invariant check."""
    name: str
    passed: bool
    entity: str
    summary: str
    detail: str = ""
    severity: str = "error"  # "error" | "warning"
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "entity": self.entity,
            "summary": self.summary,
            "detail": self.detail,
            "severity": self.severity,
            "metrics": self.metrics,
        }


@dataclass
class InvariantReport:
    """Aggregate result of all invariant checks for one entity."""
    entity: str
    all_passed: bool
    results: List[InvariantResult] = field(default_factory=list)

    def errors(self) -> List[InvariantResult]:
        return [r for r in self.results if not r.passed and r.severity == "error"]

    def warnings(self) -> List[InvariantResult]:
        return [r for r in self.results if not r.passed and r.severity == "warning"]


# ---------------------------------------------------------------------------
# Invariant implementations
# ---------------------------------------------------------------------------


def check_balance_sheet_balances(
    total_assets: Decimal,
    total_liabilities: Decimal,
    total_equity: Decimal,
    entity: str,
    threshold: Decimal = Decimal("0.01"),
) -> InvariantResult:
    """
    INV01 — Assets MUST = Liabilities + Equity (within rounding threshold).

    A failure here means the books are out of balance — the close cannot proceed
    until this is resolved.
    """
    calculated = total_liabilities + total_equity
    diff = abs(total_assets - calculated)

    passed = diff <= threshold
    summary = (
        "Balance sheet balances"
        if passed else
        f"Balance sheet OUT OF BALANCE: Assets={total_assets} ≠ Liab+Equity={calculated} (diff={diff})"
    )

    return InvariantResult(
        name="INV01_balance_sheet_balances",
        passed=passed,
        entity=entity,
        summary=summary,
        detail=(
            f"Assets: ${total_assets:.2f}\n"
            f"Liabilities: ${total_liabilities:.2f}\n"
            f"Equity: ${total_equity:.2f}\n"
            f"Liabilities + Equity: ${calculated:.2f}\n"
            f"Difference: ${diff:.2f}"
        ),
        severity="error",
    )


def check_bank_vs_ledger(
    balance_sheet_cash: Optional[Decimal],
    bank_statement_balance: Optional[Decimal],
    entity: str,
    threshold: Decimal = Decimal("10.00"),
) -> InvariantResult:
    """
    INV02 — Cash per Balance Sheet ≈ Cash per bank statement (within threshold).

    Only runs when both values are available. A large gap means transactions
    were recorded in the bank feed but not classified, or vice versa.
    """
    if balance_sheet_cash is None or bank_statement_balance is None:
        return InvariantResult(
            name="INV02_bank_vs_ledger",
            passed=True,  # Cannot check — skip
            entity=entity,
            summary="Cash-to-bank reconciliation skipped — insufficient data",
            severity="warning",
        )

    diff = abs(balance_sheet_cash - bank_statement_balance)
    passed = diff <= threshold

    summary = (
        "Cash balance matches bank statement"
        if passed else
        f"Cash MISMATCH: Ledger=${balance_sheet_cash:.2f} vs Bank=${bank_statement_balance:.2f} (diff=${diff:.2f})"
    )

    return InvariantResult(
        name="INV02_bank_vs_ledger",
        passed=passed,
        entity=entity,
        summary=summary,
        detail=(
            f"Balance Sheet cash: ${balance_sheet_cash:.2f}\n"
            f"Bank statement: ${bank_statement_balance:.2f}\n"
            f"Difference: ${diff:.2f}\n"
            f"Threshold: ${threshold:.2f}"
        ),
        severity="error",
    )


def check_unreconciled_count(
    unreconciled_count: int,
    threshold: int,
    entity: str,
) -> InvariantResult:
    """
    INV03 — Unreconciled transactions must be below the per-entity threshold.

    A pileup of unreconciled transactions means accounting is falling behind.
    The threshold is entity-specific (KAL=5, FON=5).
    """
    passed = unreconciled_count <= threshold

    summary = (
        f"Unreconciled count OK ({unreconciled_count} ≤ {threshold})"
        if passed else
        f"Too many unreconciled transactions: {unreconciled_count} (limit: {threshold})"
    )

    return InvariantResult(
        name="INV03_unreconciled_count",
        passed=passed,
        entity=entity,
        summary=summary,
        detail=(
            f"Unreconciled: {unreconciled_count}\n"
            f"Threshold: {threshold}"
        ),
        severity="error",
    )


def check_mom_delta(
    current_net: Decimal,
    previous_net: Optional[Decimal],
    entity: str,
    max_delta_pct: Decimal = Decimal("50"),  # 50% change triggers warning
    max_delta_abs: Decimal = Decimal("500"),  # $500 absolute triggers warning
) -> InvariantResult:
    """
    INV04 — Month-over-month net income should not swing wildly without explanation.

    A >50% change or >$500 absolute swing is flagged (warning, not error).
    The agent must add a note explaining the variance before closing.
    """
    if previous_net is None:
        return InvariantResult(
            name="INV04_mom_delta",
            passed=True,
            entity=entity,
            summary="MoM delta check skipped — no prior period data",
            severity="warning",
        )

    if previous_net == Decimal("0"):
        return InvariantResult(
            name="INV04_mom_delta",
            passed=True,
            entity=entity,
            summary="MoM delta check skipped — prior period was zero",
            severity="warning",
        )

    abs_delta = abs(current_net - previous_net)
    pct_change = (abs_delta / abs(previous_net)) * Decimal("100")

    passed = pct_change <= max_delta_pct and abs_delta <= max_delta_abs

    summary = (
        f"MoM net income change OK ({pct_change:.1f}%, ${abs_delta:.2f})"
        if passed else
        f"MoM net income swing: {pct_change:.1f}% (${abs_delta:.2f}) — threshold: {max_delta_pct}% / ${max_delta_abs:.2f}"
    )

    return InvariantResult(
        name="INV04_mom_delta",
        passed=passed,
        entity=entity,
        summary=summary,
        detail=(
            f"Prior period net: ${previous_net:.2f}\n"
            f"Current period net: ${current_net:.2f}\n"
            f"Delta: ${abs_delta:.2f} ({pct_change:.1f}%)"
        ),
        severity="warning",
    )


def check_no_out_of_period(
    txn_dates: List[str],
    period_start: str,
    period_end: str,
    entity: str,
) -> InvariantResult:
    """
    INV05 — No transactions dated outside the reporting period.

    Prevents the "stray November transaction polluting December close" bug.
    """
    import datetime

    try:
        start = datetime.date.fromisoformat(period_start)
        end = datetime.date.fromisoformat(period_end)
    except ValueError:
        return InvariantResult(
            name="INV05_out_of_period",
            passed=True,
            entity=entity,
            summary="Out-of-period check skipped — invalid period dates",
            severity="warning",
        )

    out_of_period: List[str] = []
    for txn_date_str in txn_dates:
        try:
            txn_date = datetime.date.fromisoformat(txn_date_str[:10])
            if txn_date < start or txn_date > end:
                out_of_period.append(txn_date_str)
        except ValueError:
            pass

    passed = len(out_of_period) == 0

    summary = (
        f"All {len(txn_dates)} transactions within period"
        if passed else
        f"{len(out_of_period)} transactions outside period ({', '.join(out_of_period[:5])})"
    )

    return InvariantResult(
        name="INV05_out_of_period",
        passed=passed,
        entity=entity,
        summary=summary,
        detail=(
            f"Period: {period_start} to {period_end}\n"
            f"Total transactions: {len(txn_dates)}\n"
            f"Out of period: {out_of_period}"
        ),
        severity="error",
    )


def check_category_totals(
    categorized_totals: Dict[str, Decimal],
    statement_totals: Dict[str, Decimal],
    entity: str,
    max_uncategorized: Decimal = Decimal("0.01"),
) -> InvariantResult:
    """
    INV06 — Categorized totals match statement control totals per category.

    The two input dicts share the same key set (account codes or category IDs).
    For each key we compare categorized_totals[key] vs statement_totals[key].
    Any difference beyond rounding ($0.01) is flagged.

    `__uncategorized__` in categorized_totals is also checked — flagged if
    it exceeds the threshold.
    """
    mismatches: Dict[str, Decimal] = {}
    all_keys = set(categorized_totals.keys()) | set(statement_totals.keys())

    for key in sorted(all_keys):
        if key == "__uncategorized__":
            continue
        cat_val = categorized_totals.get(key, Decimal("0"))
        stmt_val = statement_totals.get(key, Decimal("0"))
        diff = abs(cat_val - stmt_val)
        if diff > Decimal("0.01"):
            mismatches[key] = diff

    # Check uncategorized
    uncategorized = abs(categorized_totals.get("__uncategorized__", Decimal("0")))
    uncategorized_exceeds = uncategorized > max_uncategorized

    passed = len(mismatches) == 0 and not uncategorized_exceeds

    detail_parts = []
    for k, diff in mismatches.items():
        detail_parts.append(
            f"  {k}: categorized=${categorized_totals.get(k, 0):.2f} vs "
            f"statement=${statement_totals.get(k, 0):.2f} (diff=${diff:.2f})"
        )
    if uncategorized_exceeds:
        detail_parts.append(
            f"  __uncategorized__: ${uncategorized:.2f} (threshold: ${max_uncategorized:.2f})"
        )

    if passed:
        summary = (
            f"All {len(categorized_totals)} categories match statement totals"
            if not mismatches
            else f"Minor uncategorized residual: ${uncategorized:.2f}"
        )
    else:
        parts = []
        if mismatches:
            parts.append(f"{len(mismatches)} category total(s) mismatch statement")
        if uncategorized_exceeds:
            parts.append(f"Uncategorized ${uncategorized:.2f} exceeds ${max_uncategorized:.2f}")
        summary = "; ".join(parts)

    return InvariantResult(
        name="INV06_category_totals",
        passed=passed,
        entity=entity,
        summary=summary,
        detail=(
            "Category vs statement totals:\n" + ("\n".join(detail_parts) if detail_parts else "  All match")
        ),
        severity="error",
        metrics={
            "categorized_count": len(categorized_totals),
            "mismatch_count": len(mismatches),
            "uncategorized_total": float(uncategorized),
            "mismatches": {k: float(v) for k, v in mismatches.items()},
        },
    )


def check_ls_xero_revenue(
    ls_revenue_cents: int,
    xero_sales_total: Decimal,
    entity: str,
    max_discrepancy_pct: Decimal = Decimal("10"),
    max_discrepancy_abs: Decimal = Decimal("100"),
) -> InvariantResult:
    """
    INV08 — LS-reported revenue ≈ Xero Sales (account 200).

    Compares LemonSqueezy's reported subtotal (before tax) for the period
    against the Sales category total in Xero. A discrepancy >10% or $100
    is flagged as a warning for the agent to explain.

    Args:
        ls_revenue_cents: LS subtotal for the period (in cents, before tax)
        xero_sales_total: Xero Sales (account 200) total for the period
        entity: Entity ID
        max_discrepancy_pct: Max acceptable % difference (default 10%)
        max_discrepancy_abs: Max acceptable $ difference (default $100)

    The Xero Sales figure includes ALL revenue, not just LS — for entities
    where LS is the only revenue source (Font Replacer), this cross-check
    catches missing/duplicate syncs.
    """
    ls_revenue = Decimal(str(ls_revenue_cents)) / Decimal("100")

    if ls_revenue == Decimal("0") and xero_sales_total == Decimal("0"):
        return InvariantResult(
            name="INV08_ls_xero_revenue",
            passed=True,
            entity=entity,
            summary="LS↔Xero revenue cross-check: both zero — no revenue this period",
            severity="info",
        )

    if ls_revenue == Decimal("0"):
        return InvariantResult(
            name="INV08_ls_xero_revenue",
            passed=False,
            entity=entity,
            summary="LS revenue is $0 but Xero Sales shows ${:.2f} — LS data may be missing".format(
                float(xero_sales_total)
            ),
            severity="error",
        )

    # Calculate discrepancy
    diff = abs(ls_revenue - xero_sales_total)
    avg = (ls_revenue + xero_sales_total) / Decimal("2")
    pct = (diff / avg) * Decimal("100") if avg > Decimal("0") else Decimal("0")

    passed = pct <= max_discrepancy_pct and diff <= max_discrepancy_abs

    summary = (
        f"LS↔Xero revenue cross-check OK: LS=${float(ls_revenue):.2f} "
        f"vs Xero Sales=${float(xero_sales_total):.2f} "
        f"(diff=${float(diff):.2f}/{float(pct):.1f}%)"
        if passed
        else (
            f"LS↔Xero revenue DISCREPANCY: "
            f"LS=${float(ls_revenue):.2f} vs Xero Sales=${float(xero_sales_total):.2f} "
            f"(diff=${float(diff):.2f}/{float(pct):.1f}%) — "
            f"threshold: {float(max_discrepancy_pct)}% / ${float(max_discrepancy_abs):.2f}"
        )
    )

    return InvariantResult(
        name="INV08_ls_xero_revenue",
        passed=passed,
        entity=entity,
        summary=summary,
        detail=(
            f"LemonSqueezy revenue (subtotal, before tax): ${float(ls_revenue):.2f}\n"
            f"Xero Sales (account 200): ${float(xero_sales_total):.2f}\n"
            f"Difference: ${float(diff):.2f} ({float(pct):.1f}%)\n"
            f"Threshold: {float(max_discrepancy_pct)}% / ${float(max_discrepancy_abs):.2f}\n"
            f"LS order count: {ls_revenue_cents > 0}"  # placeholder — actual count added in pipeline
        ),
        severity="warning",  # Warning, not error — allows close with explanation
        metrics={
            "ls_revenue_usd": float(ls_revenue),
            "xero_sales_usd": float(xero_sales_total),
            "diff_usd": float(diff),
            "diff_pct": float(pct),
        },
    )


def check_prior_month_closed(
    prior_month_summary: Optional[str],
    entity: str,
) -> InvariantResult:
    """
    INV07 — Prior month must be closed before current month is signed off.

    Prevents skipping months. If the prior month's close report is missing,
    this invariant warns but does not block (the agent can explain).
    """
    passed = prior_month_summary is not None and len(prior_month_summary) > 0

    summary = (
        "Prior month close confirmed"
        if passed else
        "Prior month close not found — ensure it was closed before continuing"
    )

    return InvariantResult(
        name="INV07_prior_month_closed",
        passed=passed,
        entity=entity,
        summary=summary,
        severity="warning",
    )


def check_xero_feed_gap(
    mercury_txns: List[Dict[str, Any]],
    xero_bank_txns: List[Dict[str, Any]],
    entity: str,
    date_window_days: int = 5,
) -> InvariantResult:
    """
    INV10 — Mercury feed-gap check: every Mercury-settled transaction must appear in Xero.

    Mercury is the tie-out authority over the Xero bank feed. A feed gap is any
    Mercury transaction with status='sent' that has NO corresponding transaction
    in Xero's BankTransactions for the same period.

    Matching strategy:
      - Approximate date match (±date_window_days around postedAt)
      - Amount match (within $0.01)
      - Counterparty name match (case-insensitive substring of bankDescription)

    Only runs when both lists are provided. For non-FON entities, skips with
    a pass (Mercury is Font Replacer only).
    """
    from datetime import datetime

    if not mercury_txns or not xero_bank_txns:
        return InvariantResult(
            name="INV10_xero_feed_gap",
            passed=True,
            entity=entity,
            summary="Feed-gap check skipped — missing Mercury or Xero data",
            severity="warning",
        )

    settled = [t for t in mercury_txns if t.get("status") == "sent"]

    feed_gaps: List[Dict[str, Any]] = []
    for mt in settled:
        mt_amount = Decimal(str(abs(mt.get("amount", 0))))  # Mercury amounts are in dollars
        try:
            mt_date = datetime.fromisoformat(mt.get("postedAt", "").replace("Z", "+00:00"))
            # Make offset-naive for comparison with Xero dates
            if mt_date.tzinfo is not None:
                mt_date = mt_date.replace(tzinfo=None)
        except (ValueError, TypeError):
            continue
        mt_desc = (mt.get("bankDescription") or mt.get("counterpartyName") or "").lower()

        matched = False
        for xt in xero_bank_txns:
            xt_amount = Decimal(str(abs(xt.get("Total", 0))))
            # Xero Date is string like "2026-07-07T00:00:00"
            try:
                xt_date = datetime.fromisoformat(xt.get("Date", "").replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            # Counterparty name lives in Contact.Name for bank-feed transactions
            # (Reference is typically empty, BankAccount.Name is just "Mercury Checking").
            xt_desc = (
                xt.get("Reference")
                or xt.get("Contact", {}).get("Name", "")
                or xt.get("BankAccount", {}).get("Name", "")
                or ""
            ).lower()

            # 3-way match: amount (within $0.01), date (within window), counterparty
            amount_match = abs(mt_amount - xt_amount) <= Decimal("0.01")
            date_diff = abs((mt_date - xt_date).days)
            date_match = date_diff <= date_window_days
            # Counterparty: check if Mercury description appears in Xero description
            # or vice versa (case-insensitive). Split on common delimiters.
            mt_keywords = set(w for w in mt_desc.replace(";", " ").replace(",", " ").split() if len(w) > 2)
            xt_keywords = set(w for w in xt_desc.replace(";", " ").replace(",", " ").split() if len(w) > 2)
            counterparty_match = len(mt_keywords & xt_keywords) > 0 if mt_keywords and xt_keywords else False

            if amount_match and date_match and counterparty_match:
                matched = True
                break

        if not matched:
            feed_gaps.append(mt)

    passed = len(feed_gaps) == 0

    if passed:
        summary = (
            f"Xero feed-gap check PASSED: all {len(settled)} Mercury transactions "
            f"have Xero matches"
        )
    else:
        gap_total = sum(t.get("amount", 0) for t in feed_gaps)  # Mercury amounts are in dollars
        summary = (
            f"Xero FEED GAP: {len(feed_gaps)} Mercury transaction(s) absent from Xero "
            f"(total ${abs(gap_total):.2f})"
        )

    detail_lines = [
        f"Mercury settled transactions scanned: {len(settled)}",
        f"Xero bank transactions compared: {len(xero_bank_txns)}",
        f"Feed gaps found: {len(feed_gaps)}",
    ]
    if feed_gaps:
        detail_lines.append("")
        detail_lines.append("Gaps:")
        for g in feed_gaps:
            detail_lines.append(
                f"  {str(g.get('postedAt','?'))[:10]}  "
                f"${abs(g.get('amount',0)):.2f}  "  # Mercury amounts are in dollars
                f"{g.get('counterpartyName','?')}  "
                f"[{g.get('bankDescription','')}]"
            )

    return InvariantResult(
        name="INV10_xero_feed_gap",
        passed=passed,
        entity=entity,
        summary=summary,
        detail="\n".join(detail_lines),
        severity="error",
        metrics={
            "mercury_settled_count": len(settled),
            "xero_txn_count": len(xero_bank_txns),
            "feed_gap_count": len(feed_gaps),
            "feed_gap_total_usd": float(abs(sum(t.get("amount", 0) for t in feed_gaps))),
        },
    )

def check_degenerate_result(
    total_assets: Decimal,
    total_liabilities: Decimal,
    total_equity: Decimal,
    current_net: Decimal,
    entity: str,
) -> InvariantResult:
    """
    INV09 — P&L and Balance Sheet must not be all-zero (degenerate result).

    A bookkeeping pass that produces 0 revenue, 0 expenses, 0 assets,
    0 liabilities, and 0 equity is degenerate — it means the data source
    (Xero/Monarch parser) returned nothing useful. The close MUST be
    blocked, not completed.

    This catches the exact failure mode from KAL-5 (2026-07-21): the
    Xero P&L/BalanceSheet parser returned all zeros on KAL's chart
    structure, and the agent marked the issue done anyway.
    """
    all_zero = (
        total_assets == Decimal("0")
        and total_liabilities == Decimal("0")
        and total_equity == Decimal("0")
        and current_net == Decimal("0")
    )

    passed = not all_zero

    summary = (
        "P&L and Balance Sheet contain non-zero data"
        if passed else
        "DEGENERATE RESULT: P&L and Balance Sheet are ALL ZERO — "
        "data source or parser returned nothing useful. "
        "Do NOT mark this issue done. Set blocked with cause."
    )

    return InvariantResult(
        name="INV09_degenerate_result",
        passed=passed,
        entity=entity,
        summary=summary,
        detail=(
            f"Total Assets: ${total_assets:.2f}\n"
            f"Total Liabilities: ${total_liabilities:.2f}\n"
            f"Total Equity: ${total_equity:.2f}\n"
            f"Net Profit (P&L): ${current_net:.2f}\n"
            f"All zero: {all_zero}\n"
            f"\n"
            f"A degenerate result means the data source or parser returned\n"
            f"nothing useful. The close MUST be blocked, not completed.\n"
            f"Suspect: upstream data missing, parser bug, or connection issue."
        ),
        severity="error",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_all_invariants(
    entity: str,
    *,
    total_assets: Decimal,
    total_liabilities: Decimal,
    total_equity: Decimal,
    unreconciled_count: int,
    unreconciled_threshold: int,
    current_net: Decimal,
    previous_net: Optional[Decimal] = None,
    txn_dates: Optional[List[str]] = None,
    category_totals: Optional[Dict[str, Decimal]] = None,
    statement_totals: Optional[Dict[str, Decimal]] = None,
    balance_sheet_cash: Optional[Decimal] = None,
    bank_statement_balance: Optional[Decimal] = None,
    prior_month_summary: Optional[str] = None,
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    ls_revenue_cents: Optional[int] = None,
    xero_sales_total: Optional[Decimal] = None,
    mercury_txns: Optional[List[Dict[str, Any]]] = None,
    xero_bank_txns: Optional[List[Dict[str, Any]]] = None,
) -> InvariantReport:
    """
    Run all applicable invariant checks for one entity.

    Returns a report summarising pass/fail for every invariant. The pipeline
    should only proceed if `all_passed` is True (all errors pass; warnings
    are advisory).
    """
    results: List[InvariantResult] = []

    # INV01
    results.append(check_balance_sheet_balances(
        total_assets, total_liabilities, total_equity, entity,
    ))

    # INV02
    results.append(check_bank_vs_ledger(
        balance_sheet_cash, bank_statement_balance, entity,
    ))

    # INV03
    results.append(check_unreconciled_count(
        unreconciled_count, unreconciled_threshold, entity,
    ))

    # INV04
    results.append(check_mom_delta(
        current_net, previous_net, entity,
    ))

    # INV05
    if txn_dates and period_start and period_end:
        results.append(check_no_out_of_period(
            txn_dates, period_start, period_end, entity,
        ))

    # INV06
    if category_totals is not None and statement_totals is not None:
        results.append(check_category_totals(category_totals, statement_totals, entity))

    # INV07
    results.append(check_prior_month_closed(prior_month_summary, entity))

    # INV08 — LS↔Xero revenue cross-check (only when both values provided)
    if ls_revenue_cents is not None and xero_sales_total is not None:
        results.append(check_ls_xero_revenue(
            ls_revenue_cents, xero_sales_total, entity,
        ))

    # INV09 — Degenerate all-zero result gate
    results.append(check_degenerate_result(
        total_assets, total_liabilities, total_equity, current_net, entity,
    ))

    # INV10 — Mercury feed-gap check (only when both lists provided)
    if mercury_txns is not None and xero_bank_txns is not None:
        results.append(check_xero_feed_gap(
            mercury_txns, xero_bank_txns, entity,
        ))

    all_passed = all(r.passed or r.severity == "warning" or r.severity == "info" for r in results)

    return InvariantReport(
        entity=entity,
        all_passed=all_passed,
        results=results,
    )
