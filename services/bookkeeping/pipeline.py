"""
Bookkeeping pipeline — orchestrates data pulls, invariant checks, and reporting.

Flow:
  1. Pull data from source (Xero or Monarch)
  2. Run invariant checks
  3. Generate report
  4. Route results to Paperclip (for human/judge review if invariants fail)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .config import (
    EntityConfig,
    Entity,
    load_config,
    period_start_end,
    last_completed_month,
)
from .invariants import (
    InvariantReport,
    InvariantResult,
    run_all_invariants,
)
from .xero_adapter import XeroAdapter, XeroData, BankTransaction
from .monarch_adapter import MonarchAdapter, MonarchData, MonarchTransaction

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class EntityRun:
    """Result of running the pipeline for one entity."""
    entity: str
    entity_name: str
    period: str  # "YYYY-MM"
    data: Any
    invariant_report: InvariantReport
    flagged_transactions: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""


@dataclass
class RunReport:
    """Aggregate pipeline run report."""
    period: str
    timestamp: str
    entities: Dict[str, EntityRun] = field(default_factory=dict)
    all_passed: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "period": self.period,
            "timestamp": self.timestamp,
            "entities": {
                k: {
                    "entity": e.entity,
                    "entity_name": e.entity_name,
                    "period": e.period,
                    "invariant_report": {
                        "all_passed": e.invariant_report.all_passed,
                        "errors": [r.to_dict() for r in e.invariant_report.errors()],
                        "warnings": [r.to_dict() for r in e.invariant_report.warnings()],
                    },
                    "flagged_count": len(e.flagged_transactions),
                    "summary": e.summary,
                }
                for k, e in self.entities.items()
            },
            "all_passed": self.all_passed,
        }


@dataclass
class PipelineResult:
    """High-level pipeline result for agent consumption."""
    period: str
    all_passed: bool
    summary_text: str
    flags: List[str]
    entity_reports: Dict[str, Dict[str, Any]]
    raw: RunReport


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------


def run_bookkeeping_pipeline(
    year: Optional[int] = None,
    month: Optional[int] = None,
    entities: Optional[List[str]] = None,
    prior_month_summaries: Optional[Dict[str, str]] = None,
) -> PipelineResult:
    """
    Run the full bookkeeping close pipeline.

    Args:
        year: Calendar year (default: previous completed month's year)
        month: Calendar month (default: previous completed month)
        entities: List of entity IDs to process (default: ALL_ENTITIES)
        prior_month_summaries: Dict of entity_id -> prior month close summary

    Returns:
        PipelineResult with full details.
    """
    if year is None or month is None:
        year, month = last_completed_month()

    if entities is None:
        from .config import ALL_ENTITIES
        entities = list(ALL_ENTITIES)

    start, end = period_start_end(year, month)
    period_str = f"{year}-{month:02d}"
    prior_month_summaries = prior_month_summaries or {}

    timestamp = datetime.utcnow().isoformat()
    run_report = RunReport(
        period=period_str,
        timestamp=timestamp,
    )

    flags: List[str] = []
    all_flags: List[str] = []

    for entity_id in entities:
        config = load_config(entity_id)
        entity_run = _run_for_entity(
            config, start, end, period_str,
            prior_month_summary=prior_month_summaries.get(entity_id),
        )
        run_report.entities[entity_id] = entity_run
        if not entity_run.invariant_report.all_passed:
            for err in entity_run.invariant_report.errors():
                flags.append(f"[{entity_id}] {err.summary}")
            all_flags.extend(flags)
        if entity_run.flagged_transactions:
            flags.append(f"[{entity_id}] {len(entity_run.flagged_transactions)} transactions flagged")

    run_report.all_passed = all(
        e.invariant_report.all_passed for e in run_report.entities.values()
    )

    summary_text = _build_summary(run_report)

    return PipelineResult(
        period=period_str,
        all_passed=run_report.all_passed,
        summary_text=summary_text,
        flags=all_flags,
        entity_reports={
            k: {
                "summary": e.summary,
                "flagged": e.flagged_transactions,
            }
            for k, e in run_report.entities.items()
        },
        raw=run_report,
    )


# ---------------------------------------------------------------------------
# Per-entity runner
# ---------------------------------------------------------------------------


def _run_for_entity(
    config: EntityConfig,
    start: date,
    end: date,
    period_str: str,
    prior_month_summary: Optional[str] = None,
) -> EntityRun:
    """
    Run the pipeline for a single entity.
    """
    if config.source_type == "xero":
        return _run_xero_entity(config, start, end, period_str, prior_month_summary)
    elif config.source_type == "monarch":
        return _run_monarch_entity(config, start, end, period_str, prior_month_summary)
    else:
        raise ValueError(f"Unknown source type: {config.source_type}")


def _run_xero_entity(
    config: EntityConfig,
    start: date,
    end: date,
    period_str: str,
    prior_month_summary: Optional[str],
) -> EntityRun:
    """Run pipeline for a Xero entity."""
    adapter = XeroAdapter(config)
    data = adapter.pull_all(start, end)

    # Compute category totals
    category_totals: Dict[str, Decimal] = {}
    for t in data.transactions:
        cat = t.account_code or "__uncategorized__"
        category_totals.setdefault(cat, Decimal("0"))
        category_totals[cat] += t.total

    # Extract txn date strings for INV05
    txn_date_strs = [t.date.isoformat() for t in data.transactions]

    # Cash balance
    balance_sheet_cash = _extract_cash_balance(data.balance_sheet)

    # Prior period net for MoM
    previous_net = _estimate_prior_net(data, config) if prior_month_summary else None

    # Run invariants
    report = run_all_invariants(
        entity=config.entity_id,
        total_assets=data.balance_sheet.total_assets if data.balance_sheet else Decimal("0"),
        total_liabilities=data.balance_sheet.total_liabilities if data.balance_sheet else Decimal("0"),
        total_equity=data.balance_sheet.total_equity if data.balance_sheet else Decimal("0"),
        unreconciled_count=data.unreconciled_count,
        unreconciled_threshold=config.unreconciled_flag_count,
        current_net=data.p_and_l.net_profit if data.p_and_l else Decimal("0"),
        previous_net=previous_net,
        txn_dates=txn_date_strs,
        category_totals=category_totals,
        balance_sheet_cash=balance_sheet_cash,
        bank_statement_balance=None,  # Not available via API — manual step
        prior_month_summary=prior_month_summary,
    )

    # Flag transactions above materiality threshold
    flagged = _flag_transactions(data.transactions, config)

    summary = _entity_summary(config.entity_id, config.name, data, report, flagged)

    return EntityRun(
        entity=config.entity_id,
        entity_name=config.name,
        period=period_str,
        data=data,
        invariant_report=report,
        flagged_transactions=flagged,
        summary=summary,
    )


def _run_monarch_entity(
    config: EntityConfig,
    start: date,
    end: date,
    period_str: str,
    prior_month_summary: Optional[str],
) -> EntityRun:
    """Run pipeline for a Monarch entity."""
    adapter = MonarchAdapter(config)
    data = adapter.pull_all_sync(start, end)

    # Compute category totals
    category_totals: Dict[str, Decimal] = {}
    for t in data.transactions:
        cat = t.category_id or "__uncategorized__"
        category_totals.setdefault(cat, Decimal("0"))
        category_totals[cat] += t.amount

    txn_date_strs = [t.date.isoformat() for t in data.transactions]

    report = run_all_invariants(
        entity=config.entity_id,
        total_assets=Decimal("0"),      # Monarch doesn't give BS
        total_liabilities=Decimal("0"),
        total_equity=Decimal("0"),
        unreconciled_count=sum(1 for t in data.transactions if t.needs_review),
        unreconciled_threshold=10,       # PER-specific
        current_net=data.total_income - data.total_expenses,
        previous_net=None,
        txn_dates=txn_date_strs,
        category_totals=category_totals,
        prior_month_summary=prior_month_summary,
    )

    # Flag large expenses
    flagged = [
        {
            "id": t.id,
            "date": t.date.isoformat(),
            "amount": float(t.amount),
            "description": t.description,
            "merchant": t.merchant,
            "category": t.category_name,
            "reason": f"Amount ${float(abs(t.amount)):.2f} ≥ threshold ${config.single_txn_flag:.2f}",
        }
        for t in data.transactions
        if abs(t.amount) >= config.single_txn_flag and not t.is_recurring
    ]

    summary = _entity_summary_monarch(config.entity_id, config.name, data, report, flagged)

    return EntityRun(
        entity=config.entity_id,
        entity_name=config.name,
        period=period_str,
        data=data,
        invariant_report=report,
        flagged_transactions=flagged,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _flag_transactions(
    txns: List[Any],
    config: EntityConfig,
) -> List[Dict[str, Any]]:
    """Flag transactions ≥ materiality threshold that are unreconciled or uncategorized."""
    flagged = []
    for t in txns:
        reasons = []
        if isinstance(t, BankTransaction) and not t.is_reconciled and t.type in ("SPEND", "RECEIVE"):
            if abs(t.total) >= config.single_txn_flag:
                reasons.append(f"Unreconciled ≥ ${config.single_txn_flag:.2f}")
            if not t.account_code:
                reasons.append("No account code assigned")
        if reasons:
            flagged.append({
                "id": t.id,
                "date": t.date.isoformat(),
                "type": t.type if isinstance(t, BankTransaction) else "unknown",
                "amount": float(t.total) if isinstance(t, BankTransaction) else 0,
                "description": t.description if isinstance(t, BankTransaction) else "",
                "reasons": reasons,
            })
    return flagged


def _extract_cash_balance(bs: Any) -> Optional[Decimal]:
    """Try to extract cash/bank balance from balance sheet."""
    if bs is None or bs.raw_report is None:
        return None
    try:
        rows = bs.raw_report.get("Rows", [])
        for section in rows:
            title = (section.get("Title") or "").lower()
            if "bank" in title or "cash" in title or "current asset" in title:
                for row in section.get("Rows", []):
                    cells = row.get("Cells", [])
                    if len(cells) >= 2:
                        val_str = str(cells[-1].get("Value", "0"))
                        if val_str.replace("-", "").replace(".", "").isdigit():
                            return Decimal(val_str)
        return None
    except Exception:
        return None


def _estimate_prior_net(data: XeroData, config: EntityConfig) -> Optional[Decimal]:
    """Estimate prior month net from data (simplified)."""
    return None  # Placeholder — depends on having prior data cached


def _entity_summary(
    entity_id: str,
    name: str,
    data: XeroData,
    report: InvariantReport,
    flagged: List[Dict[str, Any]],
) -> str:
    """Build a one-paragraph summary string for a Xero entity."""
    pnl = data.p_and_l
    bs = data.balance_sheet
    inv_status = "PASS" if report.all_passed else "INVARIANT FAILURE"

    lines = [
        f"{name} ({entity_id}): {inv_status}",
        f"  Period: {data.period_start} to {data.period_end}",
        f"  Transactions: {data.total_txns} total, {data.unreconciled_count} unreconciled",
    ]
    if pnl:
        lines.append(
            f"  P&L: Revenue=${float(pnl.total_revenue):.2f} | "
            f"Expenses=${float(pnl.total_expenses):.2f} | "
            f"Net=${float(pnl.net_profit):.2f}"
        )
    if bs:
        lines.append(
            f"  Balance Sheet: Assets=${float(bs.total_assets):.2f} | "
            f"Liabilities=${float(bs.total_liabilities):.2f} | "
            f"Equity=${float(bs.total_equity):.2f}"
        )
    if flagged:
        lines.append(f"  Flagged: {len(flagged)} transaction(s)")
    if report.errors():
        lines.append(f"  Errors: {len(report.errors())}")
    if report.warnings():
        lines.append(f"  Warnings: {len(report.warnings())}")

    return "\n".join(lines)


def _entity_summary_monarch(
    entity_id: str,
    name: str,
    data: MonarchData,
    report: InvariantReport,
    flagged: List[Dict[str, Any]],
) -> str:
    """Build a one-paragraph summary string for a Monarch entity."""
    inv_status = "PASS" if report.all_passed else "INVARIANT FAILURE"

    lines = [
        f"{name} ({entity_id}): {inv_status}",
        f"  Period: {data.period_start} to {data.period_end}",
        f"  Transactions: {len(data.transactions)} total",
        f"  Income: ${float(data.total_income):.2f} | "
        f"Expenses: ${float(data.total_expenses):.2f} | "
        f"Net: ${float(data.total_income - data.total_expenses):.2f}",
    ]
    if data.accounts:
        lines.append(f"  Accounts: {len(data.accounts)} linked")
    if flagged:
        lines.append(f"  Flagged: {len(flagged)} transaction(s)")
    if report.errors():
        lines.append(f"  Errors: {len(report.errors())}")

    return "\n".join(lines)


def _build_summary(report: RunReport) -> str:
    """Build a final summary string across all entities."""
    passed_count = sum(
        1 for e in report.entities.values() if e.invariant_report.all_passed
    )
    total = len(report.entities)
    total_flags = sum(len(e.flagged_transactions) for e in report.entities.values())
    total_errors = sum(
        len(e.invariant_report.errors()) for e in report.entities.values()
    )
    total_warnings = sum(
        len(e.invariant_report.warnings()) for e in report.entities.values()
    )

    lines = [
        f"📊 Bookkeeping Close — {report.period}",
        f"  Entities: {passed_count}/{total} passed invariants",
        f"  Invariant Errors: {total_errors}",
        f"  Warnings: {total_warnings}",
        f"  Flagged Transactions: {total_flags}",
        "",
        *[e.summary for e in report.entities.values()],
    ]
    return "\n".join(lines)
