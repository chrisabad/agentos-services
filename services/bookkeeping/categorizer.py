"""Categorization pipeline — rules-first, model-fallback, judge-verified.

Flow:
  1. RuleBasedCategorizer — deterministic pattern matching against saved rules
  2. ModelCategorizer — LLM fallback for novel merchants/descriptions
  3. Judge verification — high-value or low-confidence results sent for review
  4. Rule learning — approved categorizations become deterministic rules

Rule file format (rules_path on EntityConfig):
  Each line: merchant_pattern||category_id
  Lines starting with # are comments.
  The pattern is a simple case-insensitive substring match (first match wins).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

from .config import EntityConfig

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class CategorizationResult:
    """Result of categorizing one transaction."""
    transaction_id: str
    merchant: Optional[str]
    description: str
    amount: Decimal
    suggested_category_id: Optional[str]
    suggested_category_name: Optional[str]
    confidence: float  # 0.0–1.0
    source: str         # "rule" | "model" | "judge" | "uncategorized"
    needs_judge: bool   # True when confidence is below threshold for value
    reviewed: bool = False
    approved: bool = False
    review_note: str = ""


@dataclass
class CategorizationReport:
    """Aggregate report for a batch of transaction categorizations."""
    entity: str
    total: int
    categorized: int
    needs_judge: int
    learned_rules: int
    results: List[CategorizationResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Rule-based categorizer (deterministic, first pass)
# ---------------------------------------------------------------------------


class RuleBasedCategorizer:
    """First-pass deterministic categorization using saved merchant rules.

    Rules are stored per-entity as a simple text file:
      merchant_pattern||category_id

    Patterns are matched as case-insensitive substrings against both the
    merchant name and the transaction description.
    """

    def __init__(self, config: EntityConfig):
        self.config = config
        self._rules: Optional[List[Tuple[str, str]]] = None  # (pattern, category_id)

    def load_rules(self) -> List[Tuple[str, str]]:
        """Load rules from the entity's rules file. Returns [(pattern, category_id)]."""
        if self._rules is not None:
            return self._rules

        rules_path = self.config.rules_path
        self._rules = []

        if not rules_path or not os.path.exists(rules_path):
            return self._rules

        try:
            with open(rules_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "||" in line:
                        pattern, cat_id = line.split("||", 1)
                        self._rules.append((pattern.strip(), cat_id.strip()))
        except (OSError, IOError) as e:
            # Rules file unreadable — fall through with empty rules
            pass

        return self._rules

    def categorize(
        self,
        merchant: Optional[str],
        description: str,
    ) -> Tuple[Optional[str], Optional[str], float]:
        """Try to categorize using saved rules.

        Returns (category_id, category_name, confidence).
        Returns (None, None, 0.0) if no rule matches.
        """
        rules = self.load_rules()
        if not rules:
            return None, None, 0.0

        search_text = (merchant or "").lower() + " " + description.lower()

        for pattern, cat_id in rules:
            if pattern.lower() in search_text:
                cat_name = self._category_name(cat_id)
                return cat_id, cat_name, 0.95

        return None, None, 0.0

    def _category_name(self, category_id: str) -> Optional[str]:
        """Look up the human-readable category name from chart of accounts."""
        return self.config.chart.get(category_id)

    def learn_rule(self, merchant: Optional[str], description: str, category_id: str) -> bool:
        """Save a new rule learned from an approved categorization.

        Returns True if a new rule was written, False if it already existed.
        """
        rules_path = self.config.rules_path
        if not rules_path:
            return False

        # Derive a good pattern from the merchant or first significant word
        pattern = self._derive_pattern(merchant, description)
        rule_line = f"{pattern}||{category_id}"

        existing = self.load_rules()
        if any(line[1] == category_id and pattern in line[0] for line in existing):
            return False  # Already have this rule

        # Append to file
        try:
            os.makedirs(os.path.dirname(rules_path) or ".", exist_ok=True)
            with open(rules_path, "a") as f:
                f.write(f"{rule_line}\n")
            # Invalidate cache so next load picks it up
            self._rules = None
            return True
        except (OSError, IOError):
            return False

    @staticmethod
    def _derive_pattern(merchant: Optional[str], description: str) -> str:
        """Derive a match pattern from merchant name or description.

        Prefers merchant name. Falls back to the first non-trivial word
        in the description.
        """
        if merchant:
            text = merchant.strip()
            if len(text) >= 3:
                return text

        words = description.split()
        for word in words:
            # Skip short words and common noise
            if len(word) >= 4 and word.lower() not in {
                "the", "this", "that", "with", "from", "your", "order",
                "pymt", "payment", "transfer", "deposit", "chq",
            }:
                return word.strip(".,;:!?")

        # Last resort: first 3+ char word
        for word in words:
            if len(word) >= 3:
                return word.strip(".,;:!?")

        # Absolute last resort
        return description[:20].strip()


# ---------------------------------------------------------------------------
# Model-based categorizer (LLM fallback)
# ---------------------------------------------------------------------------


class ModelCategorizer:
    """LLM-based fallback for transactions that don't match any rule.

    Calls the LiteLLM proxy with the OLLAMA_API_KEY for the 'judge' model.
    The prompt asks for a single category ID from the entity's chart of accounts.
    """

    # Categories to use when the model can't determine one
    FALLBACK_CATEGORY = "__uncategorized__"

    def __init__(self, config: EntityConfig):
        self.config = config
        self.base_url = os.environ.get(
            "LITELLM_BASE_URL",
            "https://litellm-nnhx.srv1724463.hstgr.cloud",
        )
        self.api_key = os.environ.get("OLLAMA_API_KEY", "")
        self._client: Optional[httpx.Client] = None

    def _client_get(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=httpx.Timeout(30.0),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    def categorize(
        self,
        merchant: Optional[str],
        description: str,
        amount: Decimal,
        available_categories: Dict[str, str],
    ) -> Tuple[Optional[str], Optional[str], float]:
        """Ask the model to categorize a transaction.

        Returns (category_id, category_name, confidence).
        Returns (None, None, 0.0) on failure.
        """
        if not self.api_key:
            return None, None, 0.0

        categories_text = "; ".join(
            f"{k}: {v}" for k, v in sorted(available_categories.items())
        )

        prompt = (
            "You are a bookkeeping categorizer. Given a transaction merchant name "
            "and description, pick the single best category from the list provided. "
            "Respond with ONLY the category ID (e.g. '461'), nothing else.\n\n"
            f"Merchant: {merchant or '(unknown)'}\n"
            f"Description: {description}\n"
            f"Amount: ${float(abs(amount)):.2f}\n\n"
            f"Available categories:\n{categories_text}\n\n"
            "Category ID:"
        )

        try:
            client = self._client_get()
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "judge",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a precise bookkeeping categorizer. "
                                "Reply with exactly one category ID from the list provided. "
                                "No explanations. No formatting. Just the ID."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 10,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            text = (data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip())

            if not text:
                return None, None, 0.0

            # Clean the response — strip quotes/whitespace
            text = text.strip(' "\'')

            if text in available_categories:
                return text, available_categories[text], 0.70

            # Try to find the closest match
            for cat_id, cat_name in available_categories.items():
                if text.lower() == cat_name.lower():
                    return cat_id, cat_name, 0.70

            return None, None, 0.0

        except (httpx.HTTPError, httpx.TimeoutException, KeyError, json.JSONDecodeError):
            return None, None, 0.0


# ---------------------------------------------------------------------------
# Confidence / judge-tier helpers
# ---------------------------------------------------------------------------


def _needs_judge_review(
    amount: Decimal,
    confidence: float,
    materiality_threshold: float,
) -> bool:
    """Determine if a categorization needs judge-tier review.

    Judge review is triggered when:
    - The amount is above the materiality threshold AND confidence is below 0.9
    - OR confidence is below 0.5 regardless of amount
    """
    abs_amount = float(abs(amount))
    low_confidence = confidence < 0.5
    material_and_uncertain = (
        abs_amount >= materiality_threshold and confidence < 0.9
    )
    return low_confidence or material_and_uncertain


def _confidence_for_result(
    category_id: Optional[str],
    source: str,
) -> float:
    """Assign a confidence score based on the source of categorization."""
    if category_id is None:
        return 0.0
    return {
        "rule": 0.95,
        "model": 0.70,
        "judge": 0.99,
        "uncategorized": 0.0,
    }.get(source, 0.0)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@dataclass
class CategorizationInput:
    """A transaction needing categorization, in normalized form."""
    transaction_id: str
    merchant: Optional[str]
    description: str
    amount: Decimal
    existing_category_id: Optional[str] = None


class CategorizationPipeline:
    """Orchestrates rules → model → judge categorization flow.

    Usage:
        pipeline = CategorizationPipeline(config)
        report = pipeline.categorize_batch(transactions)
    """

    def __init__(self, config: EntityConfig):
        self.config = config
        self.rule_based = RuleBasedCategorizer(config)
        self.model_based = ModelCategorizer(config)
        self.materiality = config.materiality_threshold
        self._learned_count = 0

    def categorize_batch(
        self,
        transactions: Sequence[CategorizationInput],
    ) -> CategorizationReport:
        """Categorize a batch of transactions.

        Only categorizes transactions that are already uncategorized
        (no existing_category_id). Already-categorized ones pass through.
        """
        results: List[CategorizationResult] = []
        categorized_count = 0
        needs_judge_count = 0
        available_cats = self.config.chart

        for txn in transactions:
            # Skip already-categorized transactions
            if txn.existing_category_id:
                results.append(CategorizationResult(
                    transaction_id=txn.transaction_id,
                    merchant=txn.merchant,
                    description=txn.description,
                    amount=txn.amount,
                    suggested_category_id=txn.existing_category_id,
                    suggested_category_name=available_cats.get(txn.existing_category_id),
                    confidence=1.0,
                    source="existing",
                    needs_judge=False,
                ))
                continue

            # Step 1: Try rule-based
            cat_id, cat_name, confidence = self.rule_based.categorize(
                txn.merchant, txn.description,
            )

            if cat_id is not None:
                # Rule matched
                confidence = 0.95
                source = "rule"
            else:
                # Step 2: Try model fallback
                cat_id, cat_name, confidence = self.model_based.categorize(
                    txn.merchant,
                    txn.description,
                    txn.amount,
                    available_cats,
                )
                source = "model" if cat_id is not None else "uncategorized"

            needs_judge = _needs_judge_review(
                txn.amount, confidence, self.materiality,
            )

            if needs_judge:
                needs_judge_count += 1

            if cat_id is not None:
                categorized_count += 1

            results.append(CategorizationResult(
                transaction_id=txn.transaction_id,
                merchant=txn.merchant,
                description=txn.description,
                amount=txn.amount,
                suggested_category_id=cat_id,
                suggested_category_name=cat_name,
                confidence=confidence,
                source=source,
                needs_judge=needs_judge,
            ))

        return CategorizationReport(
            entity=self.config.entity_id,
            total=len(transactions),
            categorized=categorized_count,
            needs_judge=needs_judge_count,
            learned_rules=self._learned_count,
            results=results,
        )

    def approve_and_learn(
        self,
        result: CategorizationResult,
        note: str = "",
    ) -> bool:
        """Approve a categorization and optionally learn a rule from it.

        If the categorization came from the model (not an existing rule),
        and it was approved, save a deterministic rule so we never ask
        the model about this merchant again.

        Returns True if a new rule was learned.
        """
        result.approved = True
        result.reviewed = True
        result.review_note = note

        if result.suggested_category_id is None:
            return False

        # Only learn from model-suggested categorizations
        if result.source != "model":
            return False

        learned = self.rule_based.learn_rule(
            result.merchant,
            result.description,
            result.suggested_category_id,
        )
        if learned:
            self._learned_count += 1
        return learned
