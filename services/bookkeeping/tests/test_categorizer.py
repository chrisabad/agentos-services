"""Tests for the bookkeeping categorization pipeline.

These tests are deterministic with zero external dependencies — they test
rule matching, confidence scoring, judge gate logic, and rule learning
against known fixtures, not against live LLMs.
"""

from __future__ import annotations

import os
import tempfile
from decimal import Decimal


from services.bookkeeping.config import (
    Entity,
    load_config,
    KAL_CHART,
)
from services.bookkeeping.categorizer import (
    CategorizationInput,
    CategorizationPipeline,
    CategorizationResult,
    ModelCategorizer,
    RuleBasedCategorizer,
    JudgeCategorizer,
    JudgeVerdict,
    _needs_judge_review,
)


# =========================================================================
# RuleBasedCategorizer tests
# =========================================================================


class TestRuleBasedCategorizer:
    def test_no_rules_file_returns_none(self):
        """No rules file configured → no categorization."""
        config = load_config(Entity.KAL)
        config.rules_path = None  # No rules file
        categorizer = RuleBasedCategorizer(config)
        cat_id, cat_name, confidence = categorizer.categorize(
            "Stripe", "Monthly subscription payment",
        )
        assert cat_id is None
        assert cat_name is None
        assert confidence == 0.0

    def test_empty_rules_file_returns_none(self):
        """Empty rules file → no categorization."""
        config = load_config(Entity.KAL)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".rules", delete=False) as f:
            config.rules_path = f.name
            f.write("# No rules yet\n")

        try:
            categorizer = RuleBasedCategorizer(config)
            cat_id, cat_name, confidence = categorizer.categorize(
                "GitHub", "Monthly plan",
            )
            assert cat_id is None
        finally:
            os.unlink(config.rules_path)

    def test_rule_match_by_merchant(self):
        """Rule matches on merchant name (case-insensitive)."""
        config = load_config(Entity.KAL)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".rules", delete=False) as f:
            config.rules_path = f.name
            f.write("Stripe||200\n")  # Sales

        try:
            categorizer = RuleBasedCategorizer(config)
            cat_id, cat_name, confidence = categorizer.categorize(
                "Stripe", "Payment received",
            )
            assert cat_id == "200"
            assert cat_name == "Sales"
            assert confidence == 0.95
        finally:
            os.unlink(config.rules_path)

    def test_rule_match_by_description(self):
        """Rule matches on description text when merchant is absent."""
        config = load_config(Entity.KAL)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".rules", delete=False) as f:
            config.rules_path = f.name
            f.write("digitalocean||461\n")  # Software

        try:
            categorizer = RuleBasedCategorizer(config)
            cat_id, cat_name, confidence = categorizer.categorize(
                None, "DigitalOcean hosting bill",
            )
            assert cat_id == "461"
            assert cat_name == "Software"
        finally:
            os.unlink(config.rules_path)

    def test_rule_match_case_insensitive(self):
        """Rule matching is case-insensitive."""
        config = load_config(Entity.KAL)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".rules", delete=False) as f:
            config.rules_path = f.name
            f.write("google cloud||461\n")  # Software

        try:
            categorizer = RuleBasedCategorizer(config)
            cat_id, _, _ = categorizer.categorize(
                "Google Cloud", "GCP compute engine",
            )
            assert cat_id == "461"
        finally:
            os.unlink(config.rules_path)

    def test_first_match_wins(self):
        """First matching rule wins (ordering matters)."""
        config = load_config(Entity.KAL)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".rules", delete=False) as f:
            config.rules_path = f.name
            f.write("stripe||200\n")   # Sales
            f.write("stripe||429\n")   # General Expenses

        try:
            categorizer = RuleBasedCategorizer(config)
            cat_id, cat_name, _ = categorizer.categorize(
                "Stripe", "Payment",
            )
            assert cat_id == "200"  # First match wins
            assert cat_name == "Sales"
        finally:
            os.unlink(config.rules_path)

    def test_learn_rule_appends_to_file(self):
        """Learning a rule appends it to the rules file."""
        config = load_config(Entity.KAL)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".rules", delete=False) as f:
            config.rules_path = f.name
            f.write("# Initial rules\n")

        try:
            categorizer = RuleBasedCategorizer(config)
            learned = categorizer.learn_rule("AWS", "AWS hosting bill", "461")
            assert learned is True

            # Should now match
            cat_id, _, _ = categorizer.categorize("AWS", "AWS hosting bill")
            assert cat_id == "461"

            # Should not learn duplicate
            learned_again = categorizer.learn_rule("AWS", "AWS hosting bill", "461")
            assert learned_again is False
        finally:
            os.unlink(config.rules_path)

    def test_derive_pattern_from_merchant(self):
        """Pattern derivation prefers merchant name."""
        pattern = RuleBasedCategorizer._derive_pattern("Google Cloud", "Payment for hosting")
        assert "Google Cloud" in pattern

    def test_derive_pattern_fallback_to_description(self):
        """Falls back to description words when merchant is empty."""
        pattern = RuleBasedCategorizer._derive_pattern(
            None, "DigitalOcean invoice December 2026",
        )
        assert pattern == "DigitalOcean"

    def test_derive_pattern_skips_noise_words(self):
        """Skips common noise words in description."""
        pattern = RuleBasedCategorizer._derive_pattern(
            None, "Payment for the hosting service",
        )
        # "hosting" should be picked over "the", "Payment", "for", "service"
        assert len(pattern) >= 4
        assert pattern.lower() in ("hosting", "service", "payment")


# =========================================================================
# CategorizationInput tests
# =========================================================================


class TestCategorizationInput:
    def test_basic_input(self):
        txn = CategorizationInput(
            transaction_id="txn-001",
            merchant="Stripe",
            description="Subscription payment",
            amount=Decimal("99.00"),
        )
        assert txn.transaction_id == "txn-001"
        assert txn.existing_category_id is None

    def test_with_existing_category(self):
        txn = CategorizationInput(
            transaction_id="txn-002",
            merchant="AWS",
            description="Cloud services",
            amount=Decimal("250.00"),
            existing_category_id="461",
        )
        assert txn.existing_category_id == "461"


# =========================================================================
# Judge gate logic tests
# =========================================================================


class TestJudgeGate:
    def test_small_amount_high_confidence_no_judge(self):
        """Small transaction with high confidence → no judge needed."""
        assert _needs_judge_review(
            amount=Decimal("12.50"),
            confidence=0.95,
            materiality_threshold=500.0,
        ) is False

    def test_large_amount_low_confidence_needs_judge(self):
        """Large transaction with model confidence → needs judge."""
        assert _needs_judge_review(
            amount=Decimal("999.00"),
            confidence=0.70,
            materiality_threshold=500.0,
        ) is True

    def test_very_low_confidence_always_needs_judge(self):
        """Confidence below 0.5 always triggers judge regardless of amount."""
        assert _needs_judge_review(
            amount=Decimal("5.00"),
            confidence=0.40,
            materiality_threshold=500.0,
        ) is True

    def test_material_but_high_confidence_no_judge(self):
        """Large amount with rule-level confidence → no judge needed (rule is trusted)."""
        assert _needs_judge_review(
            amount=Decimal("5000.00"),
            confidence=0.95,
            materiality_threshold=1000.0,
        ) is False


# =========================================================================
# CategorizationPipeline integration tests
# =========================================================================


class TestCategorizationPipeline:
    def test_existing_categories_pass_through(self):
        """Transactions with existing categories are left untouched."""
        config = load_config(Entity.KAL)
        pipeline = CategorizationPipeline(config)

        txns = [
            CategorizationInput(
                transaction_id="txn-001",
                merchant="Stripe",
                description="Revenue",
                amount=Decimal("1000.00"),
                existing_category_id="200",  # Already categorized as Sales
            ),
        ]
        report = pipeline.categorize_batch(txns)
        assert report.total == 1
        assert report.categorized == 0  # None newly categorized
        # The result still shows the existing category
        assert report.results[0].suggested_category_id == "200"
        assert report.results[0].source == "existing"

    def test_rule_based_categorization(self):
        """Transactions matching a rule get categorized without model fallback."""
        config = load_config(Entity.KAL)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".rules", delete=False) as f:
            config.rules_path = f.name
            f.write("stripe||200\n")
            f.write("aws||461\n")

        try:
            pipeline = CategorizationPipeline(config)
            txns = [
                CategorizationInput(
                    transaction_id="txn-001",
                    merchant="Stripe",
                    description="Monthly payment",
                    amount=Decimal("500.00"),
                ),
                CategorizationInput(
                    transaction_id="txn-002",
                    merchant="Unknown Merchant",
                    description="Random expense",
                    amount=Decimal("25.00"),
                ),
            ]
            report = pipeline.categorize_batch(txns)
            assert report.total == 2
            # Only the first (rule match) should be categorized
            # The second has no rule and no model (no API key in test) — stays uncategorized
            rule_result = report.results[0]
            assert rule_result.suggested_category_id == "200"
            assert rule_result.source == "rule"
            assert rule_result.confidence == 0.95
        finally:
            os.unlink(config.rules_path)

    def test_no_api_key_falls_through(self):
        """Without OLLAMA_API_KEY, model fallback is skipped."""
        config = load_config(Entity.KAL)
        pipeline = CategorizationPipeline(config)

        # No model configured — should still work gracefully
        assert pipeline.model_based.api_key == ""

        txn = CategorizationInput(
            transaction_id="txn-001",
            merchant="Fancy Boutique",
            description="Office supplies",
            amount=Decimal("85.00"),
        )
        report = pipeline.categorize_batch([txn])
        # No rule match, no model — stays uncategorized
        assert report.results[0].source == "uncategorized"
        assert report.results[0].suggested_category_id is None

    def test_approve_and_learn_model_result(self):
        """Approving a model categorization learns a rule."""
        config = load_config(Entity.KAL)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".rules", delete=False) as f:
            config.rules_path = f.name
            f.write("# Fresh rules file\n")

        try:
            pipeline = CategorizationPipeline(config)

            result = CategorizationResult(
                transaction_id="txn-001",
                merchant="DigitalOcean",
                description="Cloud hosting",
                amount=Decimal("50.00"),
                suggested_category_id="461",  # Software
                suggested_category_name="Software",
                confidence=0.70,
                source="model",
                needs_judge=False,
            )

            learned = pipeline.approve_and_learn(result)
            assert learned is True

            # Second time: rule now exists, should match directly
            txn = CategorizationInput(
                transaction_id="txn-002",
                merchant="DigitalOcean",
                description="Another bill",
                amount=Decimal("25.00"),
            )
            report = pipeline.categorize_batch([txn])
            assert report.results[0].suggested_category_id == "461"
            assert report.results[0].source == "rule"
        finally:
            os.unlink(config.rules_path)

    def test_approve_and_learn_rule_not_duplicated(self):
        """Approving an already-rule-categorized result does not create a duplicate rule."""
        config = load_config(Entity.KAL)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".rules", delete=False) as f:
            config.rules_path = f.name
            f.write("stripe||200\n")

        try:
            pipeline = CategorizationPipeline(config)

            result = CategorizationResult(
                transaction_id="txn-001",
                merchant="Stripe",
                description="Payment",
                amount=Decimal("100.00"),
                suggested_category_id="200",
                suggested_category_name="Sales",
                confidence=0.95,
                source="rule",  # Already from rule — should not learn
                needs_judge=False,
            )

            learned = pipeline.approve_and_learn(result)
            assert learned is False  # Nothing new to learn
        finally:
            os.unlink(config.rules_path)

    def test_empty_batch_returns_empty_report(self):
        """Empty transaction list produces an empty report."""
        config = load_config(Entity.KAL)
        pipeline = CategorizationPipeline(config)
        report = pipeline.categorize_batch([])
        assert report.total == 0
        assert report.categorized == 0
        assert report.results == []


# =========================================================================
# ModelCategorizer tests (without live API)
# =========================================================================


class TestModelCategorizer:
    def test_no_api_key_returns_none(self):
        """Without an API key, the model categorizer returns None."""
        config = load_config(Entity.KAL)
        # Remove the key from env for this test
        categorizer = ModelCategorizer(config)
        # When api_key is empty, categorize returns early
        cat_id, cat_name, confidence = categorizer.categorize(
            "Stripe", "Payment", Decimal("100.00"), KAL_CHART,
        )
        assert cat_id is None
        assert confidence == 0.0

    def test_available_categories_mapping(self):
        """Model categorizer has access to the entity's chart of accounts."""
        assert "200" in KAL_CHART
        assert KAL_CHART["200"] == "Sales"
        assert "461" in KAL_CHART
        assert KAL_CHART["461"] == "Software"
        assert "999" not in KAL_CHART  # Unknown category


# =========================================================================
# JudgeCategorizer tests (no live API calls)
# =========================================================================


class TestJudgeCategorizer:
    def test_no_api_key_returns_agrees(self):
        """Without OLLAMA_API_KEY, judge returns agrees=True."""
        config = load_config(Entity.KAL)
        judge = JudgeCategorizer(config)
        verdict = judge.verify(
            transaction_id="txn-001",
            merchant="Stripe",
            description="Monthly payment",
            amount=Decimal("500.00"),
            model_category_id="200",
            model_confidence=0.95,
            available_categories=KAL_CHART,
        )
        assert verdict.agrees is True
        assert verdict.transaction_id == "txn-001"
        assert verdict.original_category_id == "200"
        assert "no api key" in verdict.rationale.lower()

    def test_no_api_key_no_category_returns_agrees(self):
        """When model_category_id is None, judge returns agrees."""
        config = load_config(Entity.KAL)
        judge = JudgeCategorizer(config)
        verdict = judge.verify(
            transaction_id="txn-002",
            merchant="Unknown",
            description="Random expense",
            amount=Decimal("25.00"),
            model_category_id=None,
            model_confidence=0.0,
            available_categories=KAL_CHART,
        )
        assert verdict.agrees is True
        assert "uncategorized" in verdict.rationale.lower()

    def test_needs_judge_review_small_low_confidence(self):
        """Small amount + low confidence still triggers judge (below 0.5)."""
        assert _needs_judge_review(
            amount=Decimal("10.00"),
            confidence=0.40,
            materiality_threshold=500.0,
        ) is True

    def test_needs_judge_review_large_high_confidence(self):
        """Large amount but high confidence (rule-level) → no judge."""
        assert _needs_judge_review(
            amount=Decimal("5000.00"),
            confidence=0.95,
            materiality_threshold=500.0,
        ) is False

    def test_needs_judge_review_large_medium_confidence(self):
        """Large amount + medium confidence → needs judge."""
        assert _needs_judge_review(
            amount=Decimal("1000.00"),
            confidence=0.75,
            materiality_threshold=500.0,
        ) is True

    def test_needs_judge_review_at_threshold(self):
        """Amount exactly at materiality threshold with sub-0.9 confidence → needs judge."""
        assert _needs_judge_review(
            amount=Decimal("500.00"),
            confidence=0.80,
            materiality_threshold=500.0,
        ) is True

    def test_needs_judge_review_below_threshold_low_confidence(self):
        """Below materiality threshold but very low confidence → still needs judge."""
        assert _needs_judge_review(
            amount=Decimal("50.00"),
            confidence=0.30,
            materiality_threshold=500.0,
        ) is True

    def test_judge_verdict_dataclass(self):
        """JudgeVerdict dataclass fields work correctly."""
        verdict = JudgeVerdict(
            transaction_id="txn-001",
            original_category_id="200",
            original_confidence=0.95,
            judge_category_id="461",
            judge_confidence=0.85,
            agrees=False,
            rationale="This appears to be a software subscription, not revenue.",
        )
        assert not verdict.agrees
        assert verdict.original_category_id == "200"
        assert verdict.judge_category_id == "461"
