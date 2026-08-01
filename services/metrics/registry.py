"""Business registry: which revenue rails each business has.

A "rail" is an independent stream of customer payments. The cardinal rule
(learned on Font Replacer, AGE-2787): a single rail is never "total
revenue" unless the registry says the business has exactly one rail —
outputs must name which rails are included and which were unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class Rail:
    key: str            # "lemonsqueezy" | "figma_payouts" | "xero_pnl"
    label: str
    basis: str          # "gross" (customer payments) | "net_payout" (deposits, post-fees)
    notes: str = ""


@dataclass(frozen=True)
class Business:
    key: str            # registry key, e.g. "fon"
    name: str
    entity_id: str      # bookkeeping EntityConfig id ("FON" | "KAL" | "PER")
    rails: List[Rail] = field(default_factory=list)
    first_revenue_date: Optional[str] = None  # ISO date; "lifetime" starts here
    ls_store_id: Optional[int] = None         # Lemon Squeezy store (rail: lemonsqueezy)
    ls_product_id: Optional[int] = None       # restrict to one product (None = whole store)


REGISTRY = {
    "fon": Business(
        key="fon",
        name="Font Replacer",
        entity_id="FON",
        first_revenue_date="2024-07-12",
        ls_store_id=98077,
        ls_product_id=304388,
        rails=[
            Rail(
                key="lemonsqueezy",
                label="Lemon Squeezy (subscriptions checkout)",
                basis="gross",
                notes="orders + renewal invoices; API amounts are cents",
            ),
            Rail(
                key="figma_payouts",
                label="Figma-native payments (legacy rail, ~$400/mo)",
                basis="net_payout",
                notes=(
                    "Observed as Mercury deposits from contact FIGMA in Xero; "
                    "gross-side data is not exposed to us. Bank feed currently "
                    "starts 2025-01-07 and lags ~2 months (FON-84)."
                ),
            ),
        ],
    ),
    "kal": Business(
        key="kal",
        name="Kaleidoscope Venture Studio",
        entity_id="KAL",
        rails=[
            Rail(
                key="xero_pnl",
                label="Xero P&L revenue (all Kaleidoscope income)",
                basis="net_payout",
                notes="Cash-basis ledger revenue as recorded in Xero",
            ),
        ],
    ),
    # Diacritic Mining has a Xero tenant (xero-accounting skill) but no
    # bookkeeping EntityConfig yet — add it there first, then register here.
}


def get_business(key: str) -> Business:
    b = REGISTRY.get(key.lower())
    if not b:
        raise KeyError(
            f"Unknown business '{key}'. Known: {', '.join(sorted(REGISTRY))}"
        )
    return b
