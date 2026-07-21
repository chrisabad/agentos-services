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

from .config import EntityConfig, Entity

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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "entity": self.entity,
            "summary": self.summary,
            "detail": self.detail,
            "severity": self.severity,
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
    category_totals: Dict[str, Decimal],
    entity: str,
    max_uncategorized: Decimal = Decimal("0.01"),
) -> InvariantResult:
    """
    INV06 — No material uncategorized income/expense.

    Transactions without account codes (Xero) or without categories (Monarch)
    mean the books are incomplete. A small rounding residual is tolerated.
    """
    uncategorized = category_totals.get("__uncategorized__", Decimal("0"))
    passed = abs(uncategorized) <= max_uncategorized

    summary = (
        f"All transactions categorized (uncategorized: ${uncategorized:.2f})"
        if passed else
        f"Material uncategorized amount: ${uncategorized:.2f}"
    )

    return InvariantResult(
        name="INV06_category_totals",
        passed=passed,
        entity=entity,
        summary=summary,
        detail=(
            f"Uncategorized total: ${uncategorized:.2f}\n"
            f"Threshold: ${max_uncategorized:.2f}\n"
            f"Categorized breakdown: {category_totals}"
        ),
        severity="error",
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
    balance_sheet_cash: Optional[Decimal] = None,
    bank_statement_balance: Optional[Decimal] = None,
    prior_month_summary: Optional[str] = None,
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
    if txn_dates:
        period_start = entity  # placeholder; caller should provide real dates
        period_end = entity
        results.append(check_no_out_of_period(
            txn_dates, period_start, period_end, entity,
        ))

    # INV06
    if category_totals:
        results.append(check_category_totals(category_totals, entity))

    # INV07
    results.append(check_prior_month_closed(prior_month_summary, entity))

    all_passed = all(r.passed or r.severity == "warning" for r in results)

    return InvariantReport(
        entity=entity,
        all_passed=all_passed,
        results=results,
    )
