"""
Bookkeeping — deterministic pipeline for KAL + FON (Xero) and PER (Monarch).

Invariant-gated, judge-verified, zero-human close when all invariants pass.
"""

from .config import Entity, EntityConfig, load_config
from .xero_adapter import XeroAdapter, XeroData, BankTransaction
from .monarch_adapter import MonarchAdapter
from .mercury_adapter import MercuryAdapter
from .invariants import (
    InvariantResult,
    check_balance_sheet_balances,
    check_bank_vs_ledger,
    check_unreconciled_count,
    check_mom_delta,
    check_no_out_of_period,
    check_category_totals,
    check_xero_feed_gap,
    run_all_invariants,
)
# The pipeline/categorizer stack needs optional heavy deps (httpx for the
# model categorizer). Lightweight consumers — e.g. `services.metrics`, which
# agents run with bare python for revenue questions — only need config +
# adapters, so degrade gracefully instead of failing the whole package import.
try:
    from .pipeline import (
        PipelineResult,
        RunReport,
        run_bookkeeping_pipeline,
        write_run_log,
        get_run_logs,
        get_run_log,
    )
    from .categorizer import (
        CategorizationInput,
        CategorizationPipeline,
        CategorizationReport,
        CategorizationResult,
        RuleBasedCategorizer,
        ModelCategorizer,
    )
except ImportError:  # pragma: no cover — full pipeline unavailable without extras
    pass

__all__ = [
    "Entity", "EntityConfig", "load_config",
    "XeroAdapter", "XeroData", "BankTransaction",
    "MonarchAdapter", "MercuryAdapter",
    "InvariantResult",
    "check_balance_sheet_balances", "check_bank_vs_ledger",
    "check_unreconciled_count", "check_mom_delta",
    "check_no_out_of_period", "check_category_totals",
    "check_xero_feed_gap",
    "run_all_invariants",
    "PipelineResult", "RunReport", "run_bookkeeping_pipeline",
    "write_run_log", "get_run_logs", "get_run_log",
    "CategorizationInput", "CategorizationPipeline",
    "CategorizationReport", "CategorizationResult",
    "RuleBasedCategorizer", "ModelCategorizer",
]
