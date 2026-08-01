"""
LemonSqueezy adapter — pulls revenue data for cross-reference with Xero.

Fetches order revenue from the LemonSqueezy API for a given period,
so invariants can compare LS-reported revenue against Xero Sales accounts.

API docs: https://docs.lemonsqueezy.com/api/orders
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class LSMonthlyRevenue:
    """LS revenue aggregated for one month."""
    month: str             # "YYYY-MM"
    order_count: int = 0
    subtotal_cents: int = 0   # Revenue before tax (USD)
    total_cents: int = 0      # Revenue after tax (USD)
    tax_cents: int = 0
    refunded_count: int = 0


@dataclass
class LSData:
    """All LS data pulled for one store in one period."""
    store_id: int
    product_id: Optional[int]
    period_start: date
    period_end: date
    orders: List[Dict[str, Any]] = field(default_factory=list)
    revenue: Optional[LSMonthlyRevenue] = None


@dataclass
class LSRevenue:
    """LS gross revenue for a period, renewals INCLUDED.

    ``/v1/orders`` contains only initial purchases; subscription renewals
    bill through ``/v1/subscription-invoices`` (``billing_reason="renewal"``)
    and are ~33% of Font Replacer lifetime revenue as of 2026-07 (and
    growing as annual cohorts renew). Any "revenue" number derived from
    orders alone silently undercounts — the AGE-2787 report bug class.
    Verified reconciliation 2026-07-30: orders $13,908.33 + renewals
    $6,875.73 ≈ store-reported lifetime $20,760.21.
    """
    period_start: date
    period_end: date
    order_count: int = 0
    order_cents: int = 0        # paid initial purchases (orders endpoint)
    renewal_count: int = 0
    renewal_cents: int = 0      # paid renewal invoices
    refunded_order_count: int = 0
    refunded_order_cents: int = 0

    @property
    def gross_cents(self) -> int:
        return self.order_cents + self.renewal_cents


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


LEMONSQUEEZY_API_URL = "https://api.lemonsqueezy.com/v1"


class LemonSqueezyAdapter:
    """
    Pulls revenue data from the LemonSqueezy API.

    Requires LEMONSQUEEZY_API_KEY to be set in the environment
    (read from /opt/hermes-profiles/piper/.env by the pipeline runner).
    """

    def __init__(
        self,
        store_id: int = 98077,         # Font Replacer store
        product_id: Optional[int] = 304388,  # Font Replacer product (None = all products)
        api_key: Optional[str] = None,
    ):
        self.store_id = store_id
        self.product_id = product_id
        self.api_key = api_key or os.environ.get("LEMONSQUEEZY_API_KEY", "")

        if not self.api_key:
            raise RuntimeError(
                "LEMONSQUEEZY_API_KEY not set in environment. "
                "Source /opt/hermes-profiles/piper/.env or set the env var."
            )

    # ------------------------------------------------------------------
    # API helpers
    # ------------------------------------------------------------------

    def _request(self, path: str, params: Dict[str, str]) -> Dict[str, Any]:
        """Make a GET request to the LS API and return parsed JSON."""
        query_parts = [f"{k}={v}" for k, v in params.items()]
        url = f"{LEMONSQUEEZY_API_URL}{path}?{'&'.join(query_parts)}"

        # -g (--globoff) is REQUIRED: LS query params use literal brackets
        # (page[size], filter[store_id]); without it curl treats them as a
        # glob range and dies with exit 3 ("bad range in URL") before any
        # request is sent — the exact silent-failure class from AGE-2787.
        result = subprocess.run(
            ["curl", "-sg", url,
             "-H", f"Authorization: Bearer {self.api_key}",
             "-H", "Accept: application/vnd.api+json"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            raise RuntimeError(f"LS API curl failed (exit {result.returncode}): {result.stderr[:500]}")

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f"LS API returned non-JSON: {result.stdout[:500]}")

    def _paginate(self, path: str, params: Dict[str, str]) -> List[Dict[str, Any]]:
        """Fetch all pages of a list endpoint."""
        all_items: List[Dict[str, Any]] = []
        page = 1
        page_size = 100  # LS max
        total = None

        while True:
            query_params = dict(params)
            query_params["page[size]"] = str(page_size)
            query_params["page[number]"] = str(page)

            data = self._request(path, query_params)

            if total is None:
                total = data.get("meta", {}).get("page", {}).get("total", 0)

            items = data.get("data", [])
            all_items.extend(items)

            if page * page_size >= total:
                break
            page += 1

        return all_items

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def pull_orders(
        self,
        start: date,
        end: date,
    ) -> List[Dict[str, Any]]:
        """
        Fetch all paid orders in the given date range.

        Filters by store_id and optionally product_id.
        """
        params: Dict[str, str] = {
            "filter[store_id]": str(self.store_id),
        }

        # LS API doesn't reliably filter by created_at date range directly,
        # so we fetch all orders and filter client-side
        orders = self._paginate("/orders", params)

        # Filter by date range
        start_str = start.isoformat()
        end_str = end.isoformat()

        paid_orders: List[Dict[str, Any]] = []
        for o in orders:
            attrs = o.get("attributes", {})
            if attrs.get("status") != "paid":
                continue
            created = attrs.get("created_at", "")[:10]
            if start_str <= created <= end_str:
                # Filter by product_id if specified
                if self.product_id is not None:
                    first_item = attrs.get("first_order_item", {})
                    if first_item.get("product_id") != self.product_id:
                        continue
                paid_orders.append(o)

        return paid_orders

    def pull_subscriptions(
        self,
        status_filter: Optional[str] = "active",
    ) -> List[Dict[str, Any]]:
        """
        Fetch subscriptions for the store.
        Used for MRR / active-subscriber analysis.
        """
        params: Dict[str, str] = {
            "filter[store_id]": str(self.store_id),
        }
        all_subs = self._paginate("/subscriptions", params)

        if status_filter:
            return [s for s in all_subs if s.get("attributes", {}).get("status") == status_filter]
        return all_subs

    def pull_subscription_invoices(
        self,
        start: date,
        end: date,
        billing_reason: Optional[str] = "renewal",
    ) -> List[Dict[str, Any]]:
        """
        Fetch paid subscription invoices in the date range.

        ``billing_reason="renewal"`` (the default) returns only renewal
        billings — the correct set to ADD to orders when computing revenue,
        because ``billing_reason="initial"`` invoices duplicate the order
        rows. Pass ``billing_reason=None`` for all paid invoices.
        """
        params: Dict[str, str] = {
            "filter[store_id]": str(self.store_id),
        }
        invoices = self._paginate("/subscription-invoices", params)

        start_str = start.isoformat()
        end_str = end.isoformat()
        out: List[Dict[str, Any]] = []
        for inv in invoices:
            attrs = inv.get("attributes", {})
            if attrs.get("status") != "paid":
                continue
            if billing_reason and attrs.get("billing_reason") != billing_reason:
                continue
            created = attrs.get("created_at", "")[:10]
            if start_str <= created <= end_str:
                out.append(inv)
        return out

    def compute_revenue(self, start: date, end: date) -> LSRevenue:
        """
        Gross customer-payment revenue for a period: paid orders PLUS paid
        renewal invoices. This is the method to use for any "revenue for
        period X" question; ``compute_monthly_revenue`` below is orders-only
        and kept for backward compatibility.
        """
        rev = LSRevenue(period_start=start, period_end=end)

        for o in self.pull_orders(start, end):
            attrs = o.get("attributes", {})
            rev.order_count += 1
            rev.order_cents += int(attrs.get("total_usd", attrs.get("total", 0)))
            if attrs.get("refunded", False):
                rev.refunded_order_count += 1
                rev.refunded_order_cents += int(
                    attrs.get("refunded_amount_usd", attrs.get("refunded_amount", 0)) or 0
                )

        for inv in self.pull_subscription_invoices(start, end, billing_reason="renewal"):
            attrs = inv.get("attributes", {})
            rev.renewal_count += 1
            rev.renewal_cents += int(attrs.get("total_usd", attrs.get("total", 0)))

        return rev

    def compute_monthly_revenue(
        self,
        period_start: date,
        period_end: date,
        compare_field: str = "subtotal_usd",  # "subtotal_usd" or "total_usd"
    ) -> LSMonthlyRevenue:
        """
        Compute total LS revenue for a specific month.

        WARNING (AGE-2787): orders-only — EXCLUDES subscription renewals,
        which bill via /v1/subscription-invoices. Use ``compute_revenue``
        for any true revenue figure; this method remains for the legacy
        monthly-close comparison path only.

        Args:
            period_start: First day of period
            period_end: Last day of period
            compare_field: Which LS field to use for comparison amounts.
                           "subtotal_usd" = revenue before tax (default)
                           "total_usd" = revenue including tax

        Returns:
            LSMonthlyRevenue with aggregated data.
        """
        month_str = period_start.strftime("%Y-%m")
        orders = self.pull_orders(period_start, period_end)

        total_cents = 0
        subtotal_cents = 0
        tax_cents = 0
        refunded_count = 0

        for o in orders:
            attrs = o.get("attributes", {})
            total_cents += int(attrs.get("total_usd", attrs.get("total", 0)))
            subtotal_cents += int(attrs.get("subtotal_usd", attrs.get("subtotal", 0)))
            tax_cents += int(attrs.get("tax_usd", attrs.get("tax", 0)))
            if attrs.get("refunded", False):
                refunded_count += 1

        return LSMonthlyRevenue(
            month=month_str,
            order_count=len(orders),
            subtotal_cents=subtotal_cents,
            total_cents=total_cents,
            tax_cents=tax_cents,
            refunded_count=refunded_count,
        )

    def pull_all(self, start: date, end: date) -> LSData:
        """Pull all LS data for a period."""
        orders = self.pull_orders(start, end)
        revenue = self.compute_monthly_revenue(start, end)

        return LSData(
            store_id=self.store_id,
            product_id=self.product_id,
            period_start=start,
            period_end=end,
            orders=orders,
            revenue=revenue,
        )
