"""
Bookkeeping — deterministic pipeline for KAL + FON (Xero) and PER (Monarch).

Invariant-gated, judge-verified, zero-human close when all invariants pass.
"""

from .config import Entity, EntityConfig, load_config
from .xero_adapter import XeroAdapter, XeroData, BankTransaction
from .monarch_adapter import MonarchAdapter
from .invariants import (
    InvariantResult,
    check_balance_sheet_balances,
    check_bank_vs_ledger,
    check_unreconciled_count,
    check_mom_delta,
    check_no_out_of_period,
    check_category_totals,
    run_all_invariants,
)
from .pipeline import (
    PipelineResult,
    RunReport,
    run_bookkeeping_pipeline,
)

__all__ = [
    "Entity", "EntityConfig", "load_config",
    "XeroAdapter", "XeroData", "BankTransaction",
    "MonarchAdapter",
    "InvariantResult",
    "check_balance_sheet_balances", "check_bank_vs_ledger",
    "check_unreconciled_count", "check_mom_delta",
    "check_no_out_of_period", "check_category_totals",
    "run_all_invariants",
    "PipelineResult", "RunReport", "run_bookkeeping_pipeline",
]
