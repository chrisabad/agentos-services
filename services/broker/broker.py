#!/usr/bin/env python3
"""
Attention Broker — Core decision engine.

7-step decision flow per the PRD amendment (2026-04-23):

  1. FINGERPRINT the signal
  2. CHECK PRODUCER'S OWN ACTIONS
  3. CHECK DISPOSITION STATE
  4. CHECK KNOWLEDGE GRAPH (requires Layer 2; skipped if Graphiti unavailable)
  5. APPLY DETERMINISTIC RULES
  6. RESOLVE CHANNEL
  7. ROUTE (suppress | batch | surface)

This module provides the AttentionBroker class and a check() function
suitable for calling from Juno's message path.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from services.broker.fingerprint import (
    compute_fingerprint,
    name_similarity,
    normalize_triple_for_agent_topic,
    normalize_triple_for_email,
)
from services.broker.ledger import (
    load_ledger, save_ledger, upsert_topic, transition_topic,
    record_surface, add_producer_action, get_topic, prune_resolved,
    record_recent_message, record_chris_dedup,
)
from services.broker.rules import (
    RuleEngine, resolve_channel, decay_tier, simple_hash,
    CHRIS_CHANNELS, normalize_for_dedup,
    DECISION_SUPPRESS, DECISION_SURFACE, DECISION_BATCH, DECISION_DECAY,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BrokerResult:
    """Result from AttentionBroker.check()."""
    def __init__(self, decision: str, reason: str, rule_id: str,
                 fingerprint: str, resolved_channel: Optional[str],
                 topic_state: str, topic_tier: str):
        self.decision = decision            # suppress | surface | batch | decay
        self.reason = reason
        self.rule_id = rule_id
        self.fingerprint = fingerprint
        self.resolved_channel = resolved_channel
        self.topic_state = topic_state
        self.topic_tier = topic_tier
        self.suppressed = decision == DECISION_SUPPRESS
        self.should_surface = decision == DECISION_SURFACE
        self.should_batch = decision == DECISION_BATCH

    def __repr__(self):
        return (f"BrokerResult(decision={self.decision!r}, rule={self.rule_id}, "
                f"reason={self.reason!r}, channel={self.resolved_channel!r})")

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "rule_id": self.rule_id,
            "fingerprint": self.fingerprint,
            "resolved_channel": self.resolved_channel,
            "topic_state": self.topic_state,
            "topic_tier": self.topic_tier,
        }


class AttentionBroker:
    """The Attention Broker: 7-step decision engine for output lifecycle management.

    Usage:
        broker = AttentionBroker()
        result = broker.check(
            service="age",
            problem_type="build_failure",
            resource="openclaw",
            canonical_name="OpenClaw build failure on main",
            flow="juno_to_chris",
            consumer="chris",
            business="age",
            category="ops",
            context={"source": "cron"},
        )
        if result.should_surface:
            # deliver to result.resolved_channel
        elif result.should_batch:
            # add to next brief
        else:
            print(f"Suppressed: {result.reason}")
    """

    def __init__(self, ledger_path: Optional[Path] = None):
        self._ledger_path = ledger_path  # override for testing
        self._engine = RuleEngine()

    def _load(self) -> dict:
        return load_ledger()

    def _save(self, ledger: dict) -> None:
        if self._ledger_path:
            # Override for testing
            import json
            with open(self._ledger_path, "w") as f:
                json.dump(ledger, f, indent=2)
        else:
            save_ledger(ledger)

    def _normalized_fingerprint(
        self,
        service: str,
        problem_type: str,
        resource: str,
        ctx: Optional[dict] = None,
    ) -> str:
        """Compute a fingerprint with appropriate pre-normalization.

        Email-derived signals (sender_address in context) route through
        `normalize_triple_for_email` (sender domain + slugify). All other
        producer paths route through `normalize_triple_for_agent_topic`
        (TOPIC_TRIPLE_MAP + slugify). Both collapse LLM-paraphrased variants
        of the same underlying topic to a single canonical fingerprint
        (AGE-13745).
        """
        ctx = ctx or {}
        sender_address = ctx.get("sender_address") or ""
        if sender_address:
            norm = normalize_triple_for_email(
                service, problem_type, resource,
                sender_address=sender_address,
                subject=ctx.get("subject") or "",
            )
        else:
            norm = normalize_triple_for_agent_topic(service, problem_type, resource)
        return compute_fingerprint(*norm)

    def check(
        self,
        service: str,
        problem_type: str,
        resource: str,
        canonical_name: str = "",
        flow: str = "juno_to_chris",
        consumer: str = "chris",
        business: str = "",
        category: str = "ops",
        related_issue_ids: Optional[list] = None,
        context: Optional[dict] = None,
        surface_tier: str = "immediate",
        dry_run: bool = False,
    ) -> BrokerResult:
        """Run the 7-step broker decision flow.

        Args:
            service: Service/business context ("age", "fon", "wee", etc.)
            problem_type: Category of problem ("oauth_expired", "build_failure", etc.)
            resource: Specific resource affected
            canonical_name: Human-readable topic name
            flow: "juno_to_chris" or "agent_to_juno"
            consumer: "chris" or "juno"
            business: Business context (same as service usually)
            category: Topic category ("ops", "financial", "approval", etc.)
            related_issue_ids: List of related issue identifiers
            context: Additional context dict (source, delivery_type, email_type, etc.)
            surface_tier: "immediate" | "daily_brief" | "weekly_brief"
            dry_run: If True, evaluate rules but don't persist ledger changes

        Returns:
            BrokerResult with decision, reason, channel, and topic state
        """
        ctx = context or {}
        related = related_issue_ids or []
        biz = business or service

        # ── STEP 1: FINGERPRINT ──────────────────────────────────────
        fp = self._normalized_fingerprint(service, problem_type, resource, ctx)

        ledger = self._load()
        topic = get_topic(ledger, fp)

        if topic is None:
            # ── DEDUP CHECK: look for existing topics with similar canonical names ──
            new_canonical = canonical_name or f"{service}/{problem_type}/{resource}"
            merged_into = None
            for existing_fp, existing_topic in ledger.get("topics", {}).items():
                existing_state = existing_topic.get("state", "triggered")
                if existing_state in ("resolved", "muted"):
                    continue
                existing_name = existing_topic.get("canonical_name", "")
                if not existing_name:
                    continue
                sim = name_similarity(new_canonical, existing_name)
                if sim >= 0.9:
                    # Same underlying problem — reuse this topic instead of creating a new one
                    merged_into = existing_fp
                    topic = existing_topic
                    # Merge related issue IDs
                    existing_ids = set(topic.get("related_issue_ids", []))
                    new_ids = set(related) - existing_ids
                    if new_ids:
                        topic["related_issue_ids"] = list(existing_ids | new_ids)
                    # Update surface tier if escalated
                    if surface_tier != topic.get("surface_tier"):
                        topic["surface_tier"] = surface_tier
                    ledger["topics"][existing_fp] = topic
                    break

            if merged_into is None:
                # No similar existing topic — create new
                topic = {
                    "fingerprint": fp,
                    "canonical_name": new_canonical,
                    "flow": flow,
                    "consumer": consumer,
                    "business": biz,
                    "category": category,
                    "resolved_channel": resolve_channel(biz, category, surface_tier),
                    "related_issue_ids": related,
                    "related_thread_ids": [],
                    "state": "triggered",
                    "first_seen": _now_iso(),
                    "last_surfaced": None,
                    "surface_count": 0,
                    "surface_tier": surface_tier,
                    "disposition": None,
                    "disposition_source": None,
                    "disposition_evidence": None,
                    "producer_actions": [],
                    "muted_until": None,
                    "last_state_change": _now_iso(),
                }
                ledger = upsert_topic(ledger, topic)
                topic = get_topic(ledger, fp)
            else:
                # Reuse the merged topic for the rest of the pipeline
                fp = merged_into
        else:
            # Update surface tier if escalated
            if surface_tier != topic.get("surface_tier"):
                topic["surface_tier"] = surface_tier
            # Add any new related issue IDs
            existing_ids = set(topic.get("related_issue_ids", []))
            new_ids = set(related) - existing_ids
            if new_ids:
                topic["related_issue_ids"] = list(existing_ids | new_ids)
            ledger["topics"][fp] = topic

        # ── STEP 2: CHECK PRODUCER'S OWN ACTIONS ─────────────────────
        # (handled by R20 in the rule engine)

        # ── STEP 3: CHECK DISPOSITION STATE ──────────────────────────
        # (handled by R24, R25 in the rule engine)

        # ── STEP 4: CHECK KNOWLEDGE GRAPH ────────────────────────────
        # Requires Phase 5.5 (Graphiti integration). Skipped until Layer 2 is operational.
        # Placeholder: ctx.get("graphiti_resolved") could be set by a pre-check.
        if ctx.get("graphiti_resolved", False):
            if not dry_run:
                try:
                    ledger = transition_topic(ledger, fp, "resolved",
                                              disposition="resolved",
                                              disposition_source="graphiti",
                                              disposition_evidence=ctx.get("graphiti_evidence", ""))
                    self._save(ledger)
                except (ValueError, RuntimeError):
                    pass
            return BrokerResult(
                decision=DECISION_SUPPRESS,
                reason="Graphiti: resolution signal detected in knowledge graph",
                rule_id="GRAPHITI",
                fingerprint=fp,
                resolved_channel=topic.get("resolved_channel"),
                topic_state="resolved",
                topic_tier=topic.get("surface_tier", surface_tier),
            )

        # ── STEP 5: APPLY DETERMINISTIC RULES ────────────────────────
        # Build enriched context for rule evaluation
        eval_ctx = {
            **ctx,
            "flow": flow,
            "consumer": consumer,
            # R32 (recent-duplicate) reads this; pass the dedup store from the ledger
            "recent_messages": ledger.get("recent_messages") or {},
            # R33 (Chris 24h dedup) reads this; load_ledger rotates entries daily
            "chris_dedup_today": (ledger.get("chris_dedup") or {}).get("entries") or {},
        }
        # Mark already_surfaced_in_any_channel if surface_count > 0 and last_surfaced today
        if topic.get("surface_count", 0) > 0 and topic.get("last_surfaced"):
            from datetime import timezone as tz
            last_str = topic["last_surfaced"]
            if last_str.endswith("Z"):
                last_str = last_str[:-1] + "+00:00"
            last = datetime.fromisoformat(last_str)
            today = datetime.now(tz.utc).date()
            if last.date() == today:
                eval_ctx.setdefault("already_surfaced_in_any_channel", True)

        decision, reason, rule_id = self._engine.evaluate(topic, eval_ctx)

        # ── STEP 6: RESOLVE CHANNEL ───────────────────────────────────
        resolved_channel = topic.get("resolved_channel") or resolve_channel(biz, category, surface_tier)
        # Override for WEEKEND_BOT_DOWN
        if rule_id == "R14":
            resolved_channel = "C0AKKLWGNG4"  # #agent-ops

        # ── STEP 7: ROUTE ─────────────────────────────────────────────
        if not dry_run:
            try:
                if decision == DECISION_SUPPRESS:
                    # Log suppression — no state change needed
                    pass
                elif decision == DECISION_DECAY:
                    # Decay the tier
                    new_tier = decay_tier(topic.get("surface_tier", surface_tier))
                    topic["surface_tier"] = new_tier
                    ledger["topics"][fp] = topic
                    if new_tier == "muted":
                        ledger = transition_topic(ledger, fp, "muted",
                                                  disposition="silence",
                                                  disposition_source="non_response")
                elif decision in (DECISION_SURFACE, DECISION_BATCH):
                    ledger = record_surface(ledger, fp,
                                            channel=resolved_channel,
                                            tier=topic.get("surface_tier", surface_tier))
                    # R32 dedup: stamp the message hash so the next identical
                    # message within the window suppresses. Only on actual
                    # surfacing — suppressed messages don't poison the store.
                    text = ctx.get("message_text") or ""
                    if text:
                        window_ms = int(ctx.get("dupe_window_ms") or 300_000)
                        ledger = record_recent_message(ledger, simple_hash(text), window_ms=window_ms)
                        # R33 dedup: stamp normalized hash if delivering to a Chris channel
                        if resolved_channel in CHRIS_CHANNELS:
                            ledger = record_chris_dedup(ledger, simple_hash(normalize_for_dedup(text)))
                self._save(ledger)
            except (ValueError, RuntimeError) as e:
                # Non-fatal — log but don't block delivery
                import sys
                print(f"[attention-broker] ledger update error: {e}", file=sys.stderr)

        # Refresh topic state for result
        topic = get_topic(ledger, fp) or topic
        return BrokerResult(
            decision=decision,
            reason=reason,
            rule_id=rule_id,
            fingerprint=fp,
            resolved_channel=resolved_channel,
            topic_state=topic.get("state", "triggered"),
            topic_tier=topic.get("surface_tier", surface_tier),
        )

    def record_action(self, service: str, problem_type: str, resource: str,
                      action: str, evidence_ref: str = "") -> bool:
        """Record that the producer took an action on a topic.

        Use this when Juno sends an email, closes an issue, posts a comment, etc.
        This prevents re-escalation of topics Juno has already acted on (R20).

        Returns True if the topic exists and the action was recorded.
        """
        fp = self._normalized_fingerprint(service, problem_type, resource)
        ledger = self._load()
        topic = get_topic(ledger, fp)
        if topic is None:
            return False
        try:
            ledger = add_producer_action(ledger, fp, action, evidence_ref)
            save_ledger(ledger)
            return True
        except (ValueError, RuntimeError):
            return False

    def acknowledge(self, service: str, problem_type: str, resource: str,
                    source: str = "explicit", evidence: str = "") -> bool:
        """Mark a topic as acknowledged by its consumer.

        Call this when Chris responds to a Juno message, or when Juno handles
        an agent escalation (posts a comment, changes issue status, approves plan).
        """
        fp = self._normalized_fingerprint(service, problem_type, resource)
        ledger = self._load()
        topic = get_topic(ledger, fp)
        if topic is None:
            return False
        try:
            ledger = transition_topic(ledger, fp, "acknowledged",
                                      disposition="acknowledged",
                                      disposition_source=source,
                                      disposition_evidence=evidence)
            save_ledger(ledger)
            return True
        except (ValueError, RuntimeError):
            return False

    def resolve(self, service: str, problem_type: str, resource: str,
                source: str = "explicit", evidence: str = "") -> bool:
        """Mark a topic as fully resolved."""
        fp = self._normalized_fingerprint(service, problem_type, resource)
        ledger = self._load()
        topic = get_topic(ledger, fp)
        if topic is None:
            return False
        try:
            ledger = transition_topic(ledger, fp, "resolved",
                                      disposition="resolved",
                                      disposition_source=source,
                                      disposition_evidence=evidence)
            save_ledger(ledger)
            return True
        except (ValueError, RuntimeError):
            return False

    def mute(self, service: str, problem_type: str, resource: str,
             until_iso: Optional[str] = None) -> bool:
        """Mute a topic, optionally with an expiry."""
        fp = self._normalized_fingerprint(service, problem_type, resource)
        ledger = self._load()
        topic = get_topic(ledger, fp)
        if topic is None:
            return False
        try:
            if until_iso:
                topic["muted_until"] = until_iso
            ledger = transition_topic(ledger, fp, "muted",
                                      disposition="muted",
                                      disposition_source="explicit")
            save_ledger(ledger)
            return True
        except (ValueError, RuntimeError):
            return False

    def stats(self) -> dict:
        """Return ledger statistics."""
        from services.broker.ledger import get_stats
        return get_stats(self._load())

    def prune(self, max_age_hours: int = 168) -> int:
        """Prune old resolved topics. Returns count pruned."""
        ledger = self._load()
        before = len(ledger["topics"])
        ledger = prune_resolved(ledger, max_age_hours)
        self._save(ledger)
        return before - len(ledger["topics"])


# ──────────────────────────────────────────────
# Convenience function for Juno's message path
# ──────────────────────────────────────────────

_default_broker = None

def get_broker() -> AttentionBroker:
    """Return (or create) the singleton broker instance."""
    global _default_broker
    if _default_broker is None:
        _default_broker = AttentionBroker()
    return _default_broker


def check(service: str, problem_type: str, resource: str, **kwargs) -> BrokerResult:
    """Convenience wrapper — check a topic against the broker.

    Returns a BrokerResult. If result.suppressed is True, do not send.
    If result.should_surface, deliver to result.resolved_channel.
    """
    return get_broker().check(service, problem_type, resource, **kwargs)


# ──────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Attention Broker CLI")
    sub = parser.add_subparsers(dest="command")

    # check
    chk = sub.add_parser("check", help="Run broker check on a topic")
    chk.add_argument("--service", required=True)
    chk.add_argument("--problem-type", required=True)
    chk.add_argument("--resource", required=True)
    chk.add_argument("--canonical-name", default="")
    chk.add_argument("--flow", default="juno_to_chris")
    chk.add_argument("--consumer", default="chris")
    chk.add_argument("--business", default="")
    chk.add_argument("--category", default="ops")
    chk.add_argument("--tier", default="immediate")
    chk.add_argument("--dry-run", action="store_true")
    chk.add_argument("--json", action="store_true", dest="as_json")

    # stats
    sub.add_parser("stats", help="Show ledger statistics")

    # prune
    prune_p = sub.add_parser("prune", help="Prune old resolved topics")
    prune_p.add_argument("--max-age", type=int, default=168)

    args = parser.parse_args()
    broker = AttentionBroker()

    if args.command == "check":
        result = broker.check(
            service=args.service,
            problem_type=args.problem_type,
            resource=args.resource,
            canonical_name=args.canonical_name,
            flow=args.flow,
            consumer=args.consumer,
            business=args.business or args.service,
            category=args.category,
            surface_tier=args.tier,
            dry_run=args.dry_run,
        )
        if args.as_json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            print(f"decision:  {result.decision}")
            print(f"rule:      {result.rule_id}")
            print(f"reason:    {result.reason}")
            print(f"channel:   {result.resolved_channel}")
            print(f"fp:        {result.fingerprint[:16]}…")
            print(f"state:     {result.topic_state}")
            print(f"tier:      {result.topic_tier}")

    elif args.command == "stats":
        import pprint
        pprint.pprint(broker.stats())

    elif args.command == "prune":
        n = broker.prune(args.max_age)
        print(f"Pruned {n} resolved topics")
    else:
        parser.print_help()
