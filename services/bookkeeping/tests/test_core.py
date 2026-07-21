"""
Tests for the bookkeeping pipeline core.

These tests run deterministically with zero external dependencies — they
test config logic, invariants, and data normalization against known
test fixtures, not against live Xero/Monarch APIs.
"""

from __future__ import annotations

from decimal import Decimal
from datetime import date


from services.bookkeeping.config import (
    EntityConfig,
    Entity,
    load_config,
    period_start_end,
    last_completed_month,
)
from services.bookkeeping.invariants import (
    check_balance_sheet_balances,
    check_unreconciled_count,
    check_mom_delta,
    check_no_out_of_period,
    check_category_totals,
    run_all_invariants,
    InvariantReport,
)
from services.bookkeeping.xero_adapter import (
    BankTransaction,
)
from services.bookkeeping.monarch_adapter import (
    MonarchTransaction,
)
from services.bookkeeping.pipeline import (
    _flag_transactions,
    _build_summary,
    _load_rules,
    EntityRun,
)


# =========================================================================
# Config tests
# =========================================================================


class TestConfig:
    def test_load_kal_config(self):
        cfg = load_config(Entity.KAL)
        assert cfg.entity_id == "KAL"
        assert cfg.source_type == "xero"
        assert cfg.xero_tenant_id == "9407f6f4-eb25-4740-b9c0-e47bef745954"
        assert cfg.net_loss_flag == -1000.0
        assert cfg.chart["200"] == "Sales"
        assert cfg.chart["461"] == "Software"
        assert cfg.rules_path is not None
        assert "kal.rules" in cfg.rules_path

    def test_load_fon_config(self):
        cfg = load_config(Entity.FON)
        assert cfg.entity_id == "FON"
        assert cfg.source_type == "xero"
        assert cfg.xero_org_prefix == "font_replacer"
        assert cfg.net_loss_flag == -200.0
        assert cfg.rules_path is not None
        assert "fon.rules" in cfg.rules_path

    def test_load_per_config(self):
        cfg = load_config(Entity.PER)
        assert cfg.entity_id == "PER"
        assert cfg.source_type == "monarch"
        assert cfg.single_txn_flag == 100.0

    def test_period_start_end(self):
        start, end = period_start_end(2026, 6)
        assert start == date(2026, 6, 1)
        assert end == date(2026, 6, 30)

        start, end = period_start_end(2026, 12)
        assert start == date(2026, 12, 1)
        assert end == date(2026, 12, 31)

    def test_period_start_end_january(self):
        start, end = period_start_end(2026, 1)
        assert start == date(2026, 1, 1)
        assert end == date(2026, 1, 31)

    def test_last_completed_month_returns_tuple(self):
        ym = last_completed_month()
        assert len(ym) == 2
        assert isinstance(ym[0], int)  # year
        assert isinstance(ym[1], int)  # month
        assert 1 <= ym[1] <= 12


# =========================================================================
# Invariant tests
# =========================================================================


class TestInvariants:
    def test_inv01_balance_sheet_balances_passes(self):
        result = check_balance_sheet_balances(
            total_assets=Decimal("10000"),
            total_liabilities=Decimal("4000"),
            total_equity=Decimal("6000"),
            entity="KAL",
        )
        assert result.passed is True
        assert "balances" in result.summary.lower()

    def test_inv01_balance_sheet_balances_fails(self):
        result = check_balance_sheet_balances(
            total_assets=Decimal("10000"),
            total_liabilities=Decimal("4000"),
            total_equity=Decimal("5000"),  # Should be 6000
            entity="KAL",
        )
        assert result.passed is False
        assert "out of balance" in result.summary.lower()
        assert result.severity == "error"

    def test_inv01_within_rounding_threshold(self):
        """$0.005 difference should pass as rounding."""
        result = check_balance_sheet_balances(
            total_assets=Decimal("10000.01"),
            total_liabilities=Decimal("4000"),
            total_equity=Decimal("6000"),
            entity="KAL",
            threshold=Decimal("0.01"),
        )
        assert result.passed is True

    def test_inv03_unreconciled_ok(self):
        result = check_unreconciled_count(
            unreconciled_count=3,
            threshold=5,
            entity="KAL",
        )
        assert result.passed is True

    def test_inv03_unreconciled_exceeds(self):
        result = check_unreconciled_count(
            unreconciled_count=7,
            threshold=5,
            entity="KAL",
        )
        assert result.passed is False
        assert "too many" in result.summary.lower()

    def test_inv04_mom_delta_within_threshold(self):
        result = check_mom_delta(
            current_net=Decimal("500"),
            previous_net=Decimal("400"),
            entity="KAL",
        )
        assert result.passed is True

    def test_inv04_mom_delta_exceeds_pct(self):
        result = check_mom_delta(
            current_net=Decimal("1000"),
            previous_net=Decimal("100"),
            entity="KAL",
            max_delta_pct=Decimal("50"),
        )
        assert result.passed is False

    def test_inv04_mom_delta_no_prior(self):
        result = check_mom_delta(
            current_net=Decimal("500"),
            previous_net=None,
            entity="KAL",
        )
        assert result.passed is True
        assert "skipped" in result.summary.lower()

    def test_inv05_no_out_of_period(self):
        txn_dates = ["2026-06-01", "2026-06-15", "2026-06-30"]
        result = check_no_out_of_period(
            txn_dates=txn_dates,
            period_start="2026-06-01",
            period_end="2026-06-30",
            entity="KAL",
        )
        assert result.passed is True

    def test_inv05_out_of_period_detected(self):
        txn_dates = ["2026-06-01", "2026-07-15", "2026-06-30", "2026-05-01"]
        result = check_no_out_of_period(
            txn_dates=txn_dates,
            period_start="2026-06-01",
            period_end="2026-06-30",
            entity="KAL",
        )
        assert result.passed is False
        assert "outside" in result.summary.lower()

    def test_inv06_all_categorized(self):
        result = check_category_totals(
            categorized_totals={"200": Decimal("5000"), "461": Decimal("200")},
            statement_totals={"200": Decimal("5000"), "461": Decimal("200")},
            entity="KAL",
        )
        assert result.passed is True

    def test_inv06_uncategorized_found(self):
        result = check_category_totals(
            categorized_totals={"__uncategorized__": Decimal("1500"), "200": Decimal("5000")},
            statement_totals={"200": Decimal("5000")},
            entity="KAL",
        )
        assert result.passed is False
        assert "uncategorized" in result.summary.lower()

    def test_inv06_statement_mismatch(self):
        result = check_category_totals(
            categorized_totals={"200": Decimal("5000"), "461": Decimal("200")},
            statement_totals={"200": Decimal("4800"), "461": Decimal("200")},
            entity="KAL",
        )
        assert result.passed is False
        assert "mismatch" in result.summary.lower()
        assert "200" in result.metrics["mismatches"]

    def test_run_all_invariants_error_blocking(self):
        report = run_all_invariants(
            entity="KAL",
            total_assets=Decimal("10000"),
            total_liabilities=Decimal("4000"),
            total_equity=Decimal("4000"),  # Wrong — should be 6000
            unreconciled_count=3,
            unreconciled_threshold=5,
            current_net=Decimal("500"),
            previous_net=None,
        )
        assert isinstance(report, InvariantReport)
        assert report.all_passed is False
        assert len(report.errors()) > 0

    def test_run_all_invariants_warning_not_blocking(self):
        """Warnings alone should not set all_passed=False."""
        report = run_all_invariants(
            entity="KAL",
            total_assets=Decimal("10000"),
            total_liabilities=Decimal("4000"),
            total_equity=Decimal("6000"),
            unreconciled_count=3,
            unreconciled_threshold=5,
            current_net=Decimal("500"),
            previous_net=None,
        )
        # Prior month check returns warning "skipped" — should not fail
        assert report.all_passed is True

    def test_run_all_invariants_with_period_dates(self):
        """INV05 and INV06 run correctly when period dates and category totals provided."""
        report = run_all_invariants(
            entity="KAL",
            total_assets=Decimal("10000"),
            total_liabilities=Decimal("4000"),
            total_equity=Decimal("6000"),
            unreconciled_count=2,
            unreconciled_threshold=5,
            current_net=Decimal("500"),
            previous_net=None,
            txn_dates=["2026-06-01", "2026-06-15", "2026-06-30"],
            category_totals={"200": Decimal("5000"), "461": Decimal("200")},
            statement_totals={"200": Decimal("5000"), "461": Decimal("200")},
            period_start="2026-06-01",
            period_end="2026-06-30",
        )
        assert report.all_passed is True
        name_inv05 = "INV05_out_of_period"
        name_inv06 = "INV06_category_totals"
        inv05_results = [r for r in report.results if r.name == name_inv05]
        inv06_results = [r for r in report.results if r.name == name_inv06]
        assert len(inv05_results) == 1, "INV05 should run when period dates provided"
        assert inv05_results[0].passed is True
        assert len(inv06_results) == 1, "INV06 should run when category_totals provided"
        assert inv06_results[0].passed is True
        assert inv06_results[0].metrics["categorized_count"] == 2
        assert inv06_results[0].metrics["mismatch_count"] == 0

    def test_run_all_invariants_with_out_of_period_txns(self):
        """INV05 catches out-of-period transactions."""
        report = run_all_invariants(
            entity="KAL",
            total_assets=Decimal("10000"),
            total_liabilities=Decimal("4000"),
            total_equity=Decimal("6000"),
            unreconciled_count=2,
            unreconciled_threshold=5,
            current_net=Decimal("500"),
            previous_net=None,
            txn_dates=["2026-06-01", "2026-07-15", "2026-06-30"],
            period_start="2026-06-01",
            period_end="2026-06-30",
        )
        inv05 = [r for r in report.results if r.name == "INV05_out_of_period"]
        assert len(inv05) == 1
        assert inv05[0].passed is False
        assert "outside" in inv05[0].summary.lower()

    def test_invariant_result_metrics(self):
        """InvariantResult carries a metrics dict."""
        result = check_balance_sheet_balances(
            total_assets=Decimal("10000"),
            total_liabilities=Decimal("4000"),
            total_equity=Decimal("6000"),
            entity="KAL",
        )
        assert hasattr(result, "metrics")
        assert isinstance(result.metrics, dict)
        # to_dict should include metrics
        d = result.to_dict()
        assert "metrics" in d


# =========================================================================
# Data model tests
# =========================================================================


class TestBankTransaction:
    def test_basic_fields(self):
        t = BankTransaction(
            id="abc-123",
            date=date(2026, 6, 15),
            type="SPEND",
            total=Decimal("250.00"),
            description="Software subscription",
            reference="REF001",
            is_reconciled=False,
            account_code="461",
            account_name="Business Checking",
            contact_name="Figma Inc.",
        )
        assert t.id == "abc-123"
        assert str(t.total) == "250.00"
        assert not t.is_reconciled

    def test_reconciled_txn(self):
        t = BankTransaction(
            id="rec-001",
            date=date(2026, 6, 1),
            type="RECEIVE",
            total=Decimal("5000.00"),
            description="Client payment",
            reference="INV-2026-001",
            is_reconciled=True,
            account_code="200",
            account_name="Business Checking",
            contact_name="ACME Corp",
        )
        assert t.is_reconciled


class TestMonarchTransaction:
    def test_basic_fields(self):
        t = MonarchTransaction(
            id="mon-001",
            date=date(2026, 6, 10),
            amount=Decimal("-45.50"),
            description="Grocery run",
            merchant="Walmart",
            category_id="212422174063512675",
            category_name="Groceries",
            account_name="Chase Checking",
            needs_review=False,
            is_recurring=False,
            notes=None,
        )
        assert t.amount == Decimal("-45.50")
        assert t.category_name == "Groceries"
        assert not t.needs_review

    def test_needs_review(self):
        t = MonarchTransaction(
            id="mon-002",
            date=date(2026, 6, 11),
            amount=Decimal("200.00"),
            description="Zelle transfer",
            merchant=None,
            category_id=None,
            category_name=None,
            account_name="Chase Checking",
            needs_review=True,
            is_recurring=False,
            notes="Check this transfer",
        )
        assert t.needs_review
        assert t.category_name is None


# =========================================================================
# Pipeline helpers
# =========================================================================


class TestFlagTransactions:
    def test_flags_unreconciled_high_value(self):
        txns = [
            BankTransaction(
                id="t1",
                date=date(2026, 6, 1),
                type="SPEND",
                total=Decimal("150.00"),
                description="Large expense",
                reference="",
                is_reconciled=False,
                account_code="461",
                account_name="Checking",
                contact_name="",
            ),
            BankTransaction(
                id="t2",
                date=date(2026, 6, 2),
                type="RECEIVE",
                total=Decimal("50.00"),
                description="Small",
                reference="",
                is_reconciled=True,
                account_code="200",
                account_name="Checking",
                contact_name="",
            ),
        ]
        cfg = EntityConfig(
            entity_id="KAL",
            name="Test",
            source_type="xero",
            single_txn_flag=100.0,
        )
        flagged = _flag_transactions(txns, cfg)
        assert len(flagged) == 1
        assert flagged[0]["id"] == "t1"
        assert "Unreconciled" in flagged[0]["reasons"][0]

    def test_no_flags_for_clean_txns(self):
        txns = [
            BankTransaction(
                id="t1",
                date=date(2026, 6, 1),
                type="SPEND",
                total=Decimal("50.00"),
                description="Small expense",
                reference="",
                is_reconciled=True,
                account_code="461",
                account_name="Checking",
                contact_name="",
            ),
        ]
        cfg = EntityConfig(
            entity_id="KAL",
            name="Test",
            source_type="xero",
            single_txn_flag=100.0,
        )
        flagged = _flag_transactions(txns, cfg)
        assert len(flagged) == 0


class TestBuildSummary:
    def test_summary_includes_entities(self):
        from services.bookkeeping.pipeline import RunReport, EntityRun, InvariantReport
        report = RunReport(
            period="2026-06",
            timestamp="2026-07-21T00:00:00",
            entities={
                "KAL": EntityRun(
                    entity="KAL",
                    entity_name="Kaleidoscope",
                    period="2026-06",
                    data=[],
                    invariant_report=InvariantReport(
                        entity="KAL",
                        all_passed=True,
                        results=[],
                    ),
                    summary="Kaleidoscope: PASS",
                ),
            },
            all_passed=True,
        )
        summary = _build_summary(report)
        assert "2026-06" in summary
        assert "1/1" in summary
        assert "Kaleidoscope" in summary


class TestLoadRules:
    def test_load_rules_none_path(self):
        """None path returns empty list."""
        assert _load_rules(None) == []

    def test_load_rules_nonexistent(self):
        """Nonexistent path returns empty list."""
        assert _load_rules("/tmp/nonexistent.rules") == []

    def test_load_rules_with_file(self):
        """Loads rules from a file, skipping comments."""
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".rules", delete=False) as f:
            f.write("# Comment line\n")
            f.write("stripe||200\n")
            f.write("aws||461\n")
            f.write("\n")  # blank line
            path = f.name
        try:
            rules = _load_rules(path)
            assert len(rules) == 2
            assert "stripe||200" in rules
            assert "aws||461" in rules
        finally:
            os.unlink(path)

    def test_load_rules_only_comments(self):
        """File with only comments returns empty list."""
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".rules", delete=False) as f:
            f.write("# Only comments\n")
            f.write("# Another comment\n")
            path = f.name
        try:
            rules = _load_rules(path)
            assert rules == []
        finally:
            os.unlink(path)


class TestEntityRun:
    def test_entity_run_new_fields(self):
        """EntityRun supports categorization_results and judge_disagreements."""
        from services.bookkeeping.pipeline import InvariantReport
        run = EntityRun(
            entity="KAL",
            entity_name="Kaleidoscope",
            period="2026-06",
            data=[],
            invariant_report=InvariantReport(
                entity="KAL", all_passed=True, results=[],
            ),
            categorization_results=[
                {"transaction_id": "txn-001", "merchant": "Stripe",
                 "suggested_category_id": "200", "confidence": 0.95, "source": "rule"},
            ],
            judge_disagreements=[
                {"transaction_id": "txn-002",
                 "model_category_id": "200", "judge_category_id": "461",
                 "rationale": "Software subscription"},
            ],
        )
        assert len(run.categorization_results) == 1
        assert run.categorization_results[0]["suggested_category_id"] == "200"
        assert len(run.judge_disagreements) == 1
        assert run.judge_disagreements[0]["judge_category_id"] == "461"
