"""Golden-set broker requests for Phase 1.2 validation.

Each entry is a (description, request_dict, expected_decision, expected_rule) tuple.
The eval passes if the decision matches `expected_decision` AND the rule_id matches
`expected_rule` (or expected_rule is None — wildcard).

The set is intentionally small (10-15) and biased toward the 8 implemented rules in
Phase 1.1. Phase 1.1b will expand once the remaining 31 rules ship.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GoldenCase:
    description: str
    request: dict
    expected_decision: str
    expected_rule: str | None = None
    notes: str = ""


GOLDEN_SET: list[GoldenCase] = [
    GoldenCase(
        description="all-clear nightly status (R18)",
        request={
            "service": "age",
            "problem_type": "nightly_status",
            "resource": "scheduler",
            "canonical_name": "all clear: nightly run completed",
            "flow": "agent_to_juno",
            "context": {"message_text": "all clear, no anomalies detected"},
        },
        expected_decision="suppress",
        expected_rule="R18",
    ),
    GoldenCase(
        description="fresh ops alert with no prior context (default surface)",
        request={
            "service": "age",
            "problem_type": "build_failure",
            "resource": "openclaw",
            "canonical_name": "openclaw build failed on main",
            "flow": "agent_to_juno",
            "context": {"source": "ci"},
        },
        expected_decision="surface",
        expected_rule="DEFAULT",
    ),
    GoldenCase(
        description="financial alert routes to financial channel",
        request={
            "service": "kaleidoscope",
            "problem_type": "low_balance",
            "resource": "stripe_balance",
            "canonical_name": "kaleidoscope stripe balance below threshold",
            "flow": "juno_to_chris",
            "category": "financial",
        },
        expected_decision="surface",
        expected_rule="DEFAULT",
    ),
    GoldenCase(
        description="BetterStack low-severity alert → daily brief (R28)",
        request={
            "service": "age",
            "problem_type": "service_degraded",
            "resource": "openclaw_gateway",
            "canonical_name": "openclaw_gateway low cpu spike",
            "flow": "agent_to_juno",
            "context": {"source": "betterstack", "severity": "warning"},
        },
        expected_decision="batch",
        expected_rule="R28",
    ),
    GoldenCase(
        description="BetterStack critical alert NOT batched (passes through)",
        request={
            "service": "age",
            "problem_type": "outage",
            "resource": "openclaw_gateway",
            "canonical_name": "gateway down",
            "flow": "agent_to_juno",
            "context": {"source": "betterstack", "severity": "critical"},
        },
        expected_decision="surface",
        expected_rule="DEFAULT",
    ),
    GoldenCase(
        description="approval-style topic routes to default channel",
        request={
            "service": "age",
            "problem_type": "approval_request",
            "resource": "structural_change",
            "canonical_name": "approve broker rollout",
            "flow": "juno_to_chris",
            "category": "approval",
        },
        expected_decision="surface",
        expected_rule="DEFAULT",
    ),
]
