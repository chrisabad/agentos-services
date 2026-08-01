"""Offline tests for the metrics service (no network — adapter is stubbed)."""

from datetime import date

import pytest

from services.bookkeeping.lemonsqueezy_adapter import LemonSqueezyAdapter
from services.metrics.periods import parse_period
from services.metrics.registry import get_business

TODAY = date(2026, 8, 1)


# ---------------------------------------------------------------------------
# periods
# ---------------------------------------------------------------------------

def test_ytd():
    p = parse_period("ytd", today=TODAY)
    assert p.start == date(2026, 1, 1) and p.end == TODAY
    assert "YTD" in p.label


def test_year_clamps_to_today():
    p = parse_period("2026", today=TODAY)
    assert p.end == TODAY
    assert "YTD" in p.label  # partial year must not present as full year


def test_full_past_year():
    p = parse_period("2025", today=TODAY)
    assert p.start == date(2025, 1, 1) and p.end == date(2025, 12, 31)
    assert p.label == "2025 full year"


def test_month():
    p = parse_period("2026-07", today=TODAY)
    assert p.start == date(2026, 7, 1) and p.end == date(2026, 7, 31)


def test_month_range():
    p = parse_period("2026-01:2026-06", today=TODAY)
    assert p.start == date(2026, 1, 1) and p.end == date(2026, 6, 30)


def test_lifetime_requires_anchor():
    with pytest.raises(ValueError):
        parse_period("lifetime", today=TODAY)
    p = parse_period("lifetime", today=TODAY, lifetime_start=date(2024, 7, 12))
    assert p.start == date(2024, 7, 12)


def test_garbage_rejected():
    with pytest.raises(ValueError):
        parse_period("last quarter", today=TODAY)


# ---------------------------------------------------------------------------
# revenue aggregation: orders + renewals, initial invoices excluded
# ---------------------------------------------------------------------------

def _order(total_cents, created, status="paid", refunded=False, refunded_cents=0):
    return {"attributes": {
        "status": status, "total": total_cents, "total_usd": total_cents,
        "created_at": created + "T12:00:00.000000Z",
        "refunded": refunded, "refunded_amount": refunded_cents,
    }}


def _invoice(total_cents, created, reason, status="paid"):
    return {"attributes": {
        "status": status, "total": total_cents, "total_usd": total_cents,
        "billing_reason": reason, "created_at": created + "T12:00:00.000000Z",
    }}


@pytest.fixture
def adapter(monkeypatch):
    a = LemonSqueezyAdapter(store_id=98077, product_id=None, api_key="test-key")
    pages = {
        "/orders": [
            _order(960, "2026-01-10"),
            _order(400, "2026-02-05"),
            _order(400, "2025-12-31"),               # outside 2026
            _order(960, "2026-03-01", status="refunded"),  # not paid -> excluded
        ],
        "/subscription-invoices": [
            _invoice(960, "2026-01-15", "renewal"),
            _invoice(960, "2026-04-15", "renewal"),
            _invoice(400, "2026-02-01", "initial"),  # duplicates an order -> excluded
            _invoice(960, "2025-11-15", "renewal"),  # outside 2026
            _invoice(960, "2026-05-01", "renewal", status="pending"),  # unpaid
        ],
    }
    monkeypatch.setattr(a, "_paginate", lambda path, params: pages[path])
    return a


def test_compute_revenue_includes_renewals_excludes_initial(adapter):
    rev = adapter.compute_revenue(date(2026, 1, 1), date(2026, 12, 31))
    assert rev.order_count == 2
    assert rev.order_cents == 1360
    assert rev.renewal_count == 2          # only paid, in-period, reason=renewal
    assert rev.renewal_cents == 1920
    assert rev.gross_cents == 3280         # orders + renewals, nothing double-counted


def test_orders_only_method_flagged_by_comparison(adapter):
    """The legacy orders-only number must differ whenever renewals exist —
    guarding against anyone 'simplifying' compute_revenue back to orders."""
    rev = adapter.compute_revenue(date(2026, 1, 1), date(2026, 12, 31))
    assert rev.gross_cents > rev.order_cents


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

def test_fon_has_two_rails_and_anchor():
    b = get_business("fon")
    assert {r.key for r in b.rails} == {"lemonsqueezy", "figma_payouts"}
    assert b.first_revenue_date == "2024-07-12"
    assert b.ls_store_id == 98077 and b.ls_product_id == 304388


def test_unknown_business():
    with pytest.raises(KeyError):
        get_business("wee")
