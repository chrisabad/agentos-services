"""
Entity configuration for the bookkeeping pipeline.

Defines per-entity parameters: fiscal year, chart of accounts mapping,
materiality thresholds, and data-source connections.
"""

from __future__ import annotations

import os
import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Optional

# ---------------------------------------------------------------------------
# Entity enum
# ---------------------------------------------------------------------------


class Entity:
    KAL = "KAL"  # Kaleidoscope — Xero
    FON = "FON"  # Font Replacer  — Xero
    PER = "PER"  # Personal       — Monarch Money


ALL_ENTITIES = [Entity.KAL, Entity.FON, Entity.PER]

# ---------------------------------------------------------------------------
# Chart of accounts (canonical codes per entity)
# ---------------------------------------------------------------------------

# Kaleidoscope — Xero Kaleidoscope org
KAL_CHART: Dict[str, str] = {
    "200": "Sales",
    "260": "Other Revenue",
    "400": "Advertising & Marketing",
    "404": "Bank Fees",
    "412": "Consulting & Accounting",
    "429": "General Expenses",
    "433": "Insurance",
    "437": "Internet",
    "445": "Office Expenses",
    "461": "Software",
    "473": "Travel - International",
    "477": "Travel - National",
    "480": "Wages & Salaries",
}

# Font Replacer — Xero Font Replacer org
FON_CHART: Dict[str, str] = {
    "200": "Sales",
    "260": "Other Revenue",
    "404": "Bank Fees",
    "461": "Software",
    "429": "General Expenses",
}

# Personal — Monarch Money categories (by ID)
PER_CATEGORIES: Dict[str, str] = {
    "212422101593280401": "Salary & Wages",
    "212422177785957476": "Dining & Coffee",
    "212422174063512675": "Groceries",
    "212422197776010407": "General Shopping",
    "226283123445911108": "Subscriptions",
    "212422182103993505": "Medical & Dental",
    "212422250697641446": "Fees & Charges",
    "212422210196562022": "Travel & Vacation",
    "212422158072728607": "Fuel / EV Charging",
    "212422170082069541": "Transit & Ride-Share",
    "212422114883981264": "Transfers & Internal",
}

# ---------------------------------------------------------------------------
# Tenants / Connection IDs
# ---------------------------------------------------------------------------

# Xero tenant IDs (GUIDs from xero-accounting skill v1.3.0)
XERO_TENANTS = {
    Entity.KAL: "9407f6f4-eb25-4740-b9c0-e47bef745954",
    Entity.FON: "81156553-6158-48ae-9481-ac7b52ff766c",
}

# Zapier connection ID for Xero (verified 2026-07-15)
ZAPIER_XERO_CONNECTION = "021c2a3a-2b75-891a-afae-56aa6df849ab"

# ---------------------------------------------------------------------------
# Per-entity dataclass
# ---------------------------------------------------------------------------


@dataclass
class EntityConfig:
    """Complete configuration for one bookkeeping entity."""

    entity_id: str          # KAL | FON | PER
    name: str               # Human-readable
    source_type: str        # "xero" | "monarch"

    # Fiscal year (month 1–12)
    fiscal_year_start_month: int = 1

    # Materiality threshold — transactions above this get judge-tier spot-check
    materiality_threshold: float = 500.00

    # Flag thresholds
    net_loss_flag: float = 0.0           # monthly net loss above this → flag
    unreconciled_flag_count: int = 5     # more than this → flag
    single_txn_flag: float = 100.0       # single unrec txn ≥ this → flag

    # Xero-specific (only for KAL / FON)
    xero_tenant_id: Optional[str] = None
    xero_org_prefix: Optional[str] = None  # "kaleidoscope" | "font_replacer"

    # Monarch-specific (only for PER)
    monarch_session_path: str = "/home/hermes/.mm/mm_session.pickle"

    # Chart of accounts
    chart: Dict[str, str] = field(default_factory=dict)

    # Rules file — deterministic categorizations that accumulate over time
    rules_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Default configs
# ---------------------------------------------------------------------------

DEFAULT_CONFIGS = {
    Entity.KAL: EntityConfig(
        entity_id=Entity.KAL,
        name="Kaleidoscope",
        source_type="xero",
        xero_tenant_id=XERO_TENANTS[Entity.KAL],
        xero_org_prefix="kaleidoscope",
        net_loss_flag=-1000.0,     # flag if net loss > $1,000
        unreconciled_flag_count=5,
        single_txn_flag=100.0,
        materiality_threshold=500.0,
        chart=KAL_CHART,
        rules_path="/paperclip/repos/agentos-services/services/bookkeeping/rules/kal.rules",
    ),
    Entity.FON: EntityConfig(
        entity_id=Entity.FON,
        name="Font Replacer",
        source_type="xero",
        xero_tenant_id=XERO_TENANTS[Entity.FON],
        xero_org_prefix="font_replacer",
        net_loss_flag=-200.0,      # flag if net loss > $200
        unreconciled_flag_count=5,
        single_txn_flag=100.0,
        materiality_threshold=100.0,
        chart=FON_CHART,
        rules_path="/paperclip/repos/agentos-services/services/bookkeeping/rules/fon.rules",
    ),
    Entity.PER: EntityConfig(
        entity_id=Entity.PER,
        name="Personal (PER)",
        source_type="monarch",
        net_loss_flag=0.0,
        unreconciled_flag_count=0,  # Monarch doesn't have "reconciled" the same way
        single_txn_flag=100.0,
        materiality_threshold=100.0,
        chart=PER_CATEGORIES,
    ),
}


# ---------------------------------------------------------------------------
# Load config (supports file override)
# ---------------------------------------------------------------------------


def load_config(entity_id: str, config_path: Optional[str] = None) -> EntityConfig:
    """Load entity config, optionally from a JSON file override."""
    if entity_id not in DEFAULT_CONFIGS:
        raise ValueError(f"Unknown entity: {entity_id}. Valid: {list(DEFAULT_CONFIGS.keys())}")

    base = deepcopy(DEFAULT_CONFIGS[entity_id])

    if config_path and os.path.exists(config_path):
        with open(config_path) as f:
            overrides = json.load(f)
        entity_overrides = overrides.get(entity_id, {})
        for k, v in entity_overrides.items():
            if hasattr(base, k):
                setattr(base, k, v)

    return base


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def period_start_end(year: int, month: int) -> tuple[date, date]:
    """Return (first_day, last_day) of a calendar month."""
    start = date(year, month, 1)
    # Last day of month: first day of next month - 1 day
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def previous_month() -> tuple[int, int]:
    """Return (year, month) of the previous calendar month."""
    today = date.today()
    if today.month == 1:
        return today.year - 1, 12
    return today.year, today.month - 1


def last_completed_month() -> tuple[int, int]:
    """
    Return (year, month) of the most recently completed month.
    On July 21, returns (2026, 6).
    """
    return previous_month()
