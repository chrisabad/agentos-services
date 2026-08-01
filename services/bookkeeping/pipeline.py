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
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .config import (
    EntityConfig,
    load_config,
    period_start_end,
    last_completed_month,
)
from .invariants import (
    InvariantReport,
    run_all_invariants,
)
from .xero_adapter import XeroAdapter, XeroData, BankTransaction
from .monarch_adapter import MonarchAdapter, MonarchData
from .categorizer import (
    CategorizationPipeline,
    CategorizationInput,
    JudgeCategorizer,
    _needs_judge_review,
)
from .config import KAL_CHART

# ---------------------------------------------------------------------------
# Run log directory
# ---------------------------------------------------------------------------

RUN_LOG_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "runs",
)


def write_run_log(run_report: RunReport) -> str:
    """Write an immutable JSON run log to runs/YYYY-MM/run-{timestamp}.json.

    Each run produces one file. The file is never modified after creation
    (append-only / write-once semantics). Returns the absolute path written.
    """
    period_dir = os.path.join(RUN_LOG_DIR, run_report.period)
    os.makedirs(period_dir, exist_ok=True)

    # Sanitise timestamp for filename
    ts = run_report.timestamp.replace(":", "-").replace(".", "-")
    filename = f"run-{ts}.json"
    filepath = os.path.join(period_dir, filename)

    # Write-once: refuse to overwrite an existing log
    if os.path.exists(filepath):
        raise FileExistsError(
            f"Run log already exists (write-once): {filepath}"
        )

    with open(filepath, "w") as f:
        json.dump(run_report.to_dict(), f, indent=2, default=str)
        f.write("\n")

    return filepath


def get_run_logs(period: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all run logs, optionally filtered by period (YYYY-MM).

    Returns metadata (period, timestamp, all_passed, entity_count) for each log.
    """
    logs: List[Dict[str, Any]] = []

    if period:
        period_dirs = [os.path.join(RUN_LOG_DIR, period)]
    else:
        try:
            period_dirs = sorted(
                os.path.join(RUN_LOG_DIR, d)
                for d in os.listdir(RUN_LOG_DIR)
                if os.path.isdir(os.path.join(RUN_LOG_DIR, d))
            )
        except FileNotFoundError:
            return logs

    for pdir in period_dirs:
        if not os.path.isdir(pdir):
            continue
        period_name = os.path.basename(pdir)
        for fname in sorted(os.listdir(pdir)):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(pdir, fname)
            try:
                with open(fpath) as f:
                    data = json.load(f)
                logs.append({
                    "period": period_name,
                    "timestamp": data.get("timestamp", ""),
                    "all_passed": data.get("all_passed", False),
                    "entity_count": len(data.get("entities", {})),
                    "file": fpath,
                })
            except (json.JSONDecodeError, OSError):
                continue

    return logs


def get_run_log(period: str, timestamp: str) -> Optional[Dict[str, Any]]:
    """Retrieve a specific run log by period and timestamp.

    Timestamp is matched as a prefix (the filename uses a sanitised version).
    Returns the full run report dict, or None if not found.
    """
    period_dir = os.path.join(RUN_LOG_DIR, period)
    if not os.path.isdir(period_dir):
        return None

    ts_sanitised = timestamp.replace(":", "-").replace(".", "-")
    for fname in os.listdir(period_dir):
        if ts_sanitised in fname and fname.endswith(".json"):
            fpath = os.path.join(period_dir, fname)
            try:
                with open(fpath) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return None
    return None


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
    categorization_results: List[Dict[str, Any]] = field(default_factory=list)
    judge_disagreements: List[Dict[str, Any]] = field(default_factory=list)
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
                    "categorization_results": e.categorization_results,
                    "judge_disagreements": e.judge_disagreements,
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
    log_path: str = ""


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
        if entity_run.judge_disagreements:
            for d in entity_run.judge_disagreements:
                flags.append(
                    f"[{entity_id}] Judge disagrees on {d.get('transaction_id', '?')}: "
                    f"model={d.get('model_category_id')} vs judge={d.get('judge_category_id')} "
                    f"({d.get('rationale', '')})"
                )
                all_flags.append(flags[-1])

    run_report.all_passed = all(
        e.invariant_report.all_passed for e in run_report.entities.values()
    )

    # Persist immutable run log
    log_path = write_run_log(run_report)

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
                "categorization_results": e.categorization_results,
                "judge_disagreements": e.judge_disagreements,
            }
            for k, e in run_report.entities.items()
        },
        raw=run_report,
        log_path=log_path,
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

    # Mercury feed-gap data (Font Replacer only)
    mercury_txns: Optional[List[Dict[str, Any]]] = None
    xero_raw_txns: Optional[List[Dict[str, Any]]] = None
    if config.entity_id == "FON":
        try:
            from .mercury_adapter import MercuryAdapter
            mercury = MercuryAdapter()
            mercury_data = mercury.pull_all(start, end)
            mercury_txns = mercury_data.transactions

            # Get raw Xero BankTransactions (the BankTransaction objects have
            # the fields INV10 needs: Total, Date, Reference, BankAccount)
            xero_raw_txns = [
                {
                    "BankTransactionID": t.id,
                    "Total": float(t.total),
                    "Date": t.date.isoformat(),
                    "Type": t.type,
                    "Reference": t.reference,
                    "IsReconciled": t.is_reconciled,
                    "Contact": {"Name": t.contact_name} if t.contact_name else None,
                    "BankAccount": {"Name": t.account_name} if t.account_name else None,
                }
                for t in data.transactions
            ]
        except Exception as e:
            import logging
            logging.warning(f"Mercury feed-gap check setup failed: {e}")
    # END Mercury feed-gap block

    # ------------------------------------------------------------------
    # Categorization pipeline + judge-tier spot verification
    # ------------------------------------------------------------------
    cat_pipeline = CategorizationPipeline(config)
    judge = JudgeCategorizer(config)
    materiality = config.single_txn_flag or 500.0
    chart = config.chart or KAL_CHART

    # Build CategorizationInputs from BankTransactions
    cat_inputs = [
        CategorizationInput(
            transaction_id=t.id,
            merchant=t.contact_name or "",
            description=t.description,
            amount=t.total,
            existing_category_id=t.account_code,
        )
        for t in data.transactions
    ]

    # Run categorization
    cat_report = cat_pipeline.categorize_batch(cat_inputs)

    # Run judge-tier spot verification on model/rule results that meet criteria
    judge_disagreements: List[Dict[str, Any]] = []
    for result in cat_report.results:
        if result.source == "existing":
            continue  # Already approved — skip judge

        is_novel_merchant = result.merchant and result.merchant not in (
            r.split("||")[0] for r in _load_rules(config.rules_path) if config.rules_path
        ) if config.rules_path else True

        if _needs_judge_review(result.amount, result.confidence, materiality) or is_novel_merchant:
            verdict = judge.verify(
                transaction_id=result.transaction_id,
                merchant=result.merchant,
                description=result.description,
                amount=result.amount,
                model_category_id=result.suggested_category_id,
                model_confidence=result.confidence,
                available_categories=chart,
            )
            if not verdict.agrees:
                judge_disagreements.append({
                    "transaction_id": result.transaction_id,
                    "merchant": result.merchant,
                    "description": result.description,
                    "amount": float(result.amount),
                    "model_category_id": result.suggested_category_id,
                    "model_category_name": result.suggested_category_name,
                    "model_confidence": result.confidence,
                    "judge_category_id": verdict.judge_category_id,
                    "judge_confidence": verdict.judge_confidence,
                    "rationale": verdict.rationale,
                    "source": result.source,
                })

    # Build categorization results for summary
    categorization_results = [
        {
            "transaction_id": r.transaction_id,
            "merchant": r.merchant or "",
            "description": r.description,
            "amount": float(r.amount),
            "suggested_category_id": r.suggested_category_id,
            "suggested_category_name": r.suggested_category_name,
            "confidence": r.confidence,
            "source": r.source,
            "needs_judge": _needs_judge_review(r.amount, r.confidence, materiality),
        }
        for r in cat_report.results
    ]

    # Compute category totals (now includes categorization results)
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

    # LS↔Xero revenue cross-check (Font Replacer only)
    ls_revenue_cents = None
    xero_sales_total = None
    if config.entity_id == "FON":
        try:
            from .lemonsqueezy_adapter import LemonSqueezyAdapter
            ls_adapter = LemonSqueezyAdapter()
            ls_revenue = ls_adapter.compute_monthly_revenue(start, end)
            ls_revenue_cents = ls_revenue.subtotal_cents
            xero_sales_total = category_totals.get("200", Decimal("0"))
        except Exception as e:
            # If LS API fails, note it but don't crash the pipeline
            import logging
            logging.warning(f"LS↔Xero cross-check failed for FON: {e}")

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
        statement_totals=None if data.bank_statement_totals is None else data.bank_statement_totals,
        balance_sheet_cash=balance_sheet_cash,
        bank_statement_balance=None,  # Not available via API — manual step
        prior_month_summary=prior_month_summary,
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        ls_revenue_cents=ls_revenue_cents,
        xero_sales_total=xero_sales_total,
        mercury_txns=mercury_txns,
        xero_bank_txns=xero_raw_txns,
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
        categorization_results=categorization_results,
        judge_disagreements=judge_disagreements,
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

    # ------------------------------------------------------------------
    # Categorization pipeline + judge-tier spot verification
    # ------------------------------------------------------------------
    cat_pipeline = CategorizationPipeline(config)
    judge = JudgeCategorizer(config)
    materiality = config.single_txn_flag or 500.0
    chart = config.chart or KAL_CHART

    # Build CategorizationInputs from MonarchTransactions
    cat_inputs = [
        CategorizationInput(
            transaction_id=t.id,
            merchant=t.merchant or "",
            description=t.description,
            amount=t.amount,
            existing_category_id=t.category_id,
        )
        for t in data.transactions
    ]

    # Run categorization
    cat_report = cat_pipeline.categorize_batch(cat_inputs)

    # Judge-tier spot verification
    judge_disagreements: List[Dict[str, Any]] = []
    for result in cat_report.results:
        if result.source == "existing":
            continue

        is_novel_merchant = result.merchant and result.merchant not in (
            r.split("||")[0] for r in _load_rules(config.rules_path) if config.rules_path
        ) if config.rules_path else True

        if _needs_judge_review(result.amount, result.confidence, materiality) or is_novel_merchant:
            verdict = judge.verify(
                transaction_id=result.transaction_id,
                merchant=result.merchant,
                description=result.description,
                amount=result.amount,
                model_category_id=result.suggested_category_id,
                model_confidence=result.confidence,
                available_categories=chart,
            )
            if not verdict.agrees:
                judge_disagreements.append({
                    "transaction_id": result.transaction_id,
                    "merchant": result.merchant,
                    "description": result.description,
                    "amount": float(result.amount),
                    "model_category_id": result.suggested_category_id,
                    "model_category_name": result.suggested_category_name,
                    "model_confidence": result.confidence,
                    "judge_category_id": verdict.judge_category_id,
                    "judge_confidence": verdict.judge_confidence,
                    "rationale": verdict.rationale,
                    "source": result.source,
                })

    # Build categorization results for summary
    categorization_results = [
        {
            "transaction_id": r.transaction_id,
            "merchant": r.merchant or "",
            "description": r.description,
            "amount": float(r.amount),
            "suggested_category_id": r.suggested_category_id,
            "suggested_category_name": r.suggested_category_name,
            "confidence": r.confidence,
            "source": r.source,
            "needs_judge": _needs_judge_review(r.amount, r.confidence, materiality),
        }
        for r in cat_report.results
    ]

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
        statement_totals=None,           # Monarch has no statement totals
        prior_month_summary=prior_month_summary,
        period_start=start.isoformat(),
        period_end=end.isoformat(),
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
        categorization_results=categorization_results,
        judge_disagreements=judge_disagreements,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_rules(rules_path: Optional[str]) -> List[str]:
    """Load raw rules lines from the rules file. Returns empty list if none."""
    if not rules_path or not os.path.isfile(rules_path):
        return []
    with open(rules_path, "r") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


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
