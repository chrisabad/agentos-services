"""Standalone pytest suite for all invariant checks.

Mirrors the 15-test spec from AGE-1934.
Tests the invariants module independently (no pipeline dependencies).
"""

from __future__ import annotations

from decimal import Decimal


from services.bookkeeping.invariants import (
    check_balance_sheet_balances,
    check_bank_vs_ledger,
    check_unreconciled_count,
    check_mom_delta,
    check_no_out_of_period,
    check_category_totals,
    check_prior_month_closed,
    check_ls_xero_revenue,
    run_all_invariants,
    InvariantReport,
)


# =====================================================================
# INV01 — Balance sheet balances
# =====================================================================


class TestBalanceSheet:
    def test_balance_sheet_balances_balanced(self):
        """Known-balanced BS data → pass."""
        result = check_balance_sheet_balances(
            total_assets=Decimal("10000"),
            total_liabilities=Decimal("4000"),
            total_equity=Decimal("6000"),
            entity="KAL",
        )
        assert result.passed is True
        assert "balances" in result.summary.lower()
        assert result.severity == "error"

    def test_balance_sheet_balances_unbalanced(self):
        """Deliberately unbalanced → fail."""
        result = check_balance_sheet_balances(
            total_assets=Decimal("10000"),
            total_liabilities=Decimal("4000"),
            total_equity=Decimal("5000"),  # Should be 6000
            entity="KAL",
        )
        assert result.passed is False
        assert "out of balance" in result.summary.lower()
        assert result.severity == "error"


# =====================================================================
# INV02 — Bank vs ledger
# =====================================================================


class TestBankVsLedger:
    def test_bank_vs_ledger_matches(self):
        """Bank txns match ledger → pass."""
        result = check_bank_vs_ledger(
            balance_sheet_cash=Decimal("15000.00"),
            bank_statement_balance=Decimal("15000.00"),
            entity="KAL",
        )
        assert result.passed is True
        assert "matches" in result.summary.lower()

    def test_bank_vs_ledger_mismatch(self):
        """Difference above threshold → fail."""
        result = check_bank_vs_ledger(
            balance_sheet_cash=Decimal("15000.00"),
            bank_statement_balance=Decimal("15200.00"),  # $200 diff > $10 threshold
            entity="KAL",
        )
        assert result.passed is False
        assert "mismatch" in result.summary.lower()
        assert result.severity == "error"

    def test_bank_vs_ledger_missing_data(self):
        """One side None → skips (pass with warning)."""
        result = check_bank_vs_ledger(
            balance_sheet_cash=None,
            bank_statement_balance=Decimal("15000.00"),
            entity="KAL",
        )
        assert result.passed is True
        assert "skipped" in result.summary.lower()
        assert result.severity == "warning"

    def test_bank_vs_ledger_within_threshold(self):
        """$5 diff with $10 threshold → pass."""
        result = check_bank_vs_ledger(
            balance_sheet_cash=Decimal("10005.00"),
            bank_statement_balance=Decimal("10000.00"),
            entity="KAL",
            threshold=Decimal("10.00"),
        )
        assert result.passed is True


# =====================================================================
# INV03 — Unreconciled count
# =====================================================================


class TestUnreconciled:
    def test_unreconciled_count_under_threshold(self):
        """Small count within limits → pass."""
        result = check_unreconciled_count(
            unreconciled_count=3,
            threshold=5,
            entity="KAL",
        )
        assert result.passed is True

    def test_unreconciled_count_over_threshold(self):
        """Large count → fail."""
        result = check_unreconciled_count(
            unreconciled_count=7,
            threshold=5,
            entity="KAL",
        )
        assert result.passed is False
        assert "too many" in result.summary.lower()

    def test_unreconciled_single_large_txn(self):
        """Single txn above threshold (PER: threshold=0, any unrec → fail)."""
        result = check_unreconciled_count(
            unreconciled_count=1,
            threshold=0,
            entity="PER",
        )
        assert result.passed is False
        assert "too many" in result.summary.lower()


# =====================================================================
# INV04 — Month-over-month delta
# =====================================================================


class TestMomDelta:
    def test_mom_delta_within_threshold(self):
        """MoM change within bounds → pass."""
        result = check_mom_delta(
            current_net=Decimal("500"),
            previous_net=Decimal("400"),
            entity="KAL",
        )
        assert result.passed is True

    def test_mom_delta_over_threshold(self):
        """MoM change > materiality → fail."""
        result = check_mom_delta(
            current_net=Decimal("1000"),
            previous_net=Decimal("100"),
            entity="KAL",
            max_delta_pct=Decimal("50"),
            max_delta_abs=Decimal("500"),
        )
        # 900% change > 50%, $900 delta > $500 → fail
        assert result.passed is False
        assert "swing" in result.summary.lower() or "threshold" in result.summary.lower()

    def test_mom_delta_no_prior(self):
        """No prior period → skip (pass with warning)."""
        result = check_mom_delta(
            current_net=Decimal("500"),
            previous_net=None,
            entity="KAL",
        )
        assert result.passed is True
        assert "skipped" in result.summary.lower()


# =====================================================================
# INV05 — Out-of-period transactions
# =====================================================================


class TestOutOfPeriod:
    def test_no_out_of_period_all_valid(self):
        """All dates in period → pass."""
        txn_dates = ["2026-06-01", "2026-06-15", "2026-06-30"]
        result = check_no_out_of_period(
            txn_dates=txn_dates,
            period_start="2026-06-01",
            period_end="2026-06-30",
            entity="KAL",
        )
        assert result.passed is True

    def test_no_out_of_period_invalid_dates(self):
        """Dates outside period → fail."""
        txn_dates = ["2026-06-01", "2026-07-15", "2026-06-30", "2026-05-01"]
        result = check_no_out_of_period(
            txn_dates=txn_dates,
            period_start="2026-06-01",
            period_end="2026-06-30",
            entity="KAL",
        )
        assert result.passed is False
        assert "outside" in result.summary.lower()
        assert result.severity == "error"

    def test_no_out_of_period_invalid_range(self):
        """Invalid period dates → skip (pass with warning)."""
        result = check_no_out_of_period(
            txn_dates=["2026-06-01"],
            period_start="not-a-date",
            period_end="2026-06-30",
            entity="KAL",
        )
        assert result.passed is True
        assert "skipped" in result.summary.lower()


# =====================================================================
# INV06 — Category totals
# =====================================================================


class TestCategoryTotals:
    def test_category_totals_match(self):
        """Sums match statement → pass."""
        result = check_category_totals(
            categorized_totals={"200": Decimal("5000"), "461": Decimal("200")},
            statement_totals={"200": Decimal("5000"), "461": Decimal("200")},
            entity="KAL",
        )
        assert result.passed is True
        assert "match" in result.summary.lower()

    def test_category_totals_mismatch(self):
        """Sums don't match → fail."""
        result = check_category_totals(
            categorized_totals={"200": Decimal("5000"), "461": Decimal("200")},
            statement_totals={"200": Decimal("4800"), "461": Decimal("200")},
            entity="KAL",
        )
        assert result.passed is False
        assert "mismatch" in result.summary.lower()
        assert "200" in result.metrics["mismatches"]

    def test_category_totals_uncategorized(self):
        """Uncategorized exceeds threshold → fail."""
        result = check_category_totals(
            categorized_totals={"__uncategorized__": Decimal("1500"), "200": Decimal("5000")},
            statement_totals={"200": Decimal("5000")},
            entity="KAL",
        )
        assert result.passed is False
        assert "uncategorized" in result.summary.lower()

    def test_category_totals_empty_both(self):
        """Empty dicts on both sides → pass."""
        result = check_category_totals(
            categorized_totals={},
            statement_totals={},
            entity="KAL",
        )
        assert result.passed is True


# =====================================================================
# INV07 — Prior month closed
# =====================================================================


class TestPriorMonthClosed:
    def test_prior_month_confirmed(self):
        """Prior month has summary → pass."""
        result = check_prior_month_closed(
            prior_month_summary="June 2026 close: all invariants passed",
            entity="KAL",
        )
        assert result.passed is True
        assert "confirmed" in result.summary.lower()

    def test_prior_month_missing(self):
        """No prior month summary → warn (passes but warns)."""
        result = check_prior_month_closed(
            prior_month_summary=None,
            entity="KAL",
        )
        assert result.passed is False
        assert "not found" in result.summary.lower()
        assert result.severity == "warning"

    def test_prior_month_empty(self):
        """Empty summary string → warn."""
        result = check_prior_month_closed(
            prior_month_summary="",
            entity="KAL",
        )
        assert result.passed is False
        assert "not found" in result.summary.lower()


# =====================================================================
# INV08 — LS↔Xero revenue cross-check
# =====================================================================


class TestLSXeroCrossCheck:
    def test_revenue_matches_within_threshold(self):
        """LS revenue and Xero sales match closely → pass."""
        result = check_ls_xero_revenue(
            ls_revenue_cents=500000,  # $5,000.00
            xero_sales_total=Decimal("4950.00"),
            entity="FON",
        )
        assert result.passed is True
        assert "OK" in result.summary

    def test_revenue_large_discrepancy(self):
        """LS and Xero differ by >$100 → warning."""
        result = check_ls_xero_revenue(
            ls_revenue_cents=500000,  # $5,000.00
            xero_sales_total=Decimal("3500.00"),  # $1,500 diff → > $100 threshold
            entity="FON",
        )
        assert result.passed is False
        assert "DISCREPANCY" in result.summary
        assert result.severity == "warning"

    def test_both_zero(self):
        """Both sides report $0 → info pass."""
        result = check_ls_xero_revenue(
            ls_revenue_cents=0,
            xero_sales_total=Decimal("0"),
            entity="FON",
        )
        assert result.passed is True
        assert result.severity == "info"

    def test_ls_zero_but_xero_has_revenue(self):
        """LS shows $0 but Xero has revenue → error (likely missing LS sync)."""
        result = check_ls_xero_revenue(
            ls_revenue_cents=0,
            xero_sales_total=Decimal("5000.00"),
            entity="FON",
        )
        assert result.passed is False
        assert result.severity == "error"
        assert "missing" in result.summary.lower()

    def test_inv08_integrated_in_run_all(self):
        """Verify INV08 is triggered when params provided to run_all."""
        report = run_all_invariants(
            entity="FON",
            total_assets=Decimal("10000"),
            total_liabilities=Decimal("4000"),
            total_equity=Decimal("6000"),
            unreconciled_count=2,
            unreconciled_threshold=5,
            current_net=Decimal("500"),
            previous_net=Decimal("450"),
            txn_dates=["2026-06-01"],
            category_totals={"200": Decimal("5000")},
            statement_totals={"200": Decimal("5000")},
            balance_sheet_cash=Decimal("5000"),
            bank_statement_balance=Decimal("5000"),
            prior_month_summary="May close ok",
            period_start="2026-06-01",
            period_end="2026-06-30",
            ls_revenue_cents=500000,
            xero_sales_total=Decimal("5000.00"),
        )
        assert report.all_passed is True
        # Find INV08 in results
        inv08_results = [r for r in report.results if r.name == "INV08_ls_xero_revenue"]
        assert len(inv08_results) == 1
        assert inv08_results[0].passed is True


# =====================================================================
# run_all_invariants — integration
# =====================================================================


class TestRunAll:
    def test_run_all_invariants_all_pass(self):
        """Happy path — all invariants pass."""
        report = run_all_invariants(
            entity="KAL",
            total_assets=Decimal("10000"),
            total_liabilities=Decimal("4000"),
            total_equity=Decimal("6000"),
            unreconciled_count=3,
            unreconciled_threshold=5,
            current_net=Decimal("500"),
            previous_net=Decimal("450"),
            txn_dates=["2026-06-01", "2026-06-15"],
            category_totals={"200": Decimal("5000")},
            statement_totals={"200": Decimal("5000")},
            balance_sheet_cash=Decimal("5000"),
            bank_statement_balance=Decimal("5000"),
            prior_month_summary="May close ok",
            period_start="2026-06-01",
            period_end="2026-06-30",
        )
        assert isinstance(report, InvariantReport)
        assert report.all_passed is True
        # All errors should pass; warnings are non-blocking
        assert len(report.errors()) == 0

    def test_run_all_invariants_with_failures(self):
        """Some checks fail — verify collected."""
        report = run_all_invariants(
            entity="KAL",
            total_assets=Decimal("10000"),
            total_liabilities=Decimal("4000"),
            total_equity=Decimal("4000"),  # Wrong — out of balance
            unreconciled_count=7,
            unreconciled_threshold=5,
            current_net=Decimal("500"),
            previous_net=None,
        )
        assert report.all_passed is False
        assert len(report.errors()) >= 1

    def test_invariant_result_metrics(self):
        """InvariantResult carries a metrics dict and serializes cleanly."""
        result = check_balance_sheet_balances(
            total_assets=Decimal("10000"),
            total_liabilities=Decimal("4000"),
            total_equity=Decimal("6000"),
            entity="KAL",
        )
        assert hasattr(result, "metrics")
        assert isinstance(result.metrics, dict)
        d = result.to_dict()
        assert "metrics" in d
        assert d["name"] == "INV01_balance_sheet_balances"
        assert d["passed"] is True
        assert d["entity"] == "KAL"
