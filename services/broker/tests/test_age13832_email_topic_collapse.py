"""AGE-13832: Broker fingerprint instability — LegalZoom email-path collapse.

When emails arrive from LegalZoom with varying LLM-paraphrased triples and
subjects, the broker must produce a single fingerprint (not 20 per day).

Root cause: normalize_triple_for_email() did NOT consult TOPIC_TRIPLE_MAP.
Emails from unknown senders (not in SENDER_TRIPLE_MAP) fell through to
pure slugification, which couldn't collapse phrasing variants.

Fix: After SENDER_TRIPLE_MAP check, inject the sender domain into the
slugified join string before consulting TOPIC_TRIPLE_MAP. This allows
topic entries like ("legalzoom", "delaware") to match even when the
email triple is empty — the domain provides the "legalzoom" token.
"""
import pytest
from services.broker.fingerprint import (
    compute_fingerprint,
    normalize_triple_for_email,
    normalize_triple_for_agent_topic,
)


class TestLegalZoomEmailCollapse:
    """All LegalZoom email variants must collapse to one fingerprint."""

    @pytest.fixture
    def legalzoom_cases(self):
        """Observed/realistic LegalZoom email inputs that produced divergent
        fingerprints before the AGE-13832 fix."""
        return [
            # Empty triple + sender + subject (Juno triage with no LLM classification)
            {"svc": "", "pt": "", "res": "", "sender": "legalzoom.com", "subj": "Delaware Annual Tax Statement - Action Required"},
            # Same but different subject phrasing
            {"svc": "", "pt": "", "res": "", "sender": "legalzoom.com", "subj": "Your Delaware Annual Tax Statement is due"},
            # LLM-paraphrased triples with subject
            {"svc": "compliance", "pt": "deadline", "res": "LegalZoom DE Annual Tax", "sender": "legalzoom.com", "subj": "Delaware Annual Tax Statement - Action Required"},
            {"svc": "compliance", "pt": "reminder", "res": "LegalZoom Delaware Filing", "sender": "legalzoom.com", "subj": "Your Delaware Annual Tax Statement is due"},
            # Subdomain variant
            {"svc": "", "pt": "", "res": "", "sender": "notifications.legalzoom.com", "subj": "Delaware Annual Tax - Final Notice"},
            # Another paraphrase
            {"svc": "font-replacer", "pt": "deadline-reminder", "res": "FON-185-legalzoom", "sender": "legalzoom.com", "subj": "Delaware Annual Tax Statement"},
            # Yet another
            {"svc": "fon", "pt": "compliance", "res": "LegalZoom DE Annual Tax Statement", "sender": "legalzoom.com", "subj": "Delaware Annual Tax"},
        ]

    def test_legalzoom_email_variants_collapse(self, legalzoom_cases):
        """All LegalZoom email variants produce a single fingerprint."""
        fingerprints = set()
        for tc in legalzoom_cases:
            norm = normalize_triple_for_email(
                service=tc["svc"], problem_type=tc["pt"], resource=tc["res"],
                sender_address=tc["sender"], subject=tc["subj"],
            )
            fp = compute_fingerprint(*norm)
            fingerprints.add(fp)

        assert len(fingerprints) == 1, (
            f"Expected 1 unique fingerprint for LegalZoom email variants, "
            f"got {len(fingerprints)}: {fingerprints}"
        )

    def test_legalzoom_email_collapses_to_same_as_agent_topic(self, legalzoom_cases):
        """Email-path and agent-topic-path produce the same canonical triple for
        LegalZoom — ensuring consistency across producer types."""
        # Get the canonical triple from the email path (first case)
        email_norm = normalize_triple_for_email(
            service="", problem_type="", resource="",
            sender_address="legalzoom.com",
            subject="Delaware Annual Tax Statement",
        )
        # Get the canonical triple from the agent-topic path (known LLM variant)
        agent_norm = normalize_triple_for_agent_topic(
            "font-replacer", "deadline-reminder", "FON-185-legalzoom",
        )
        assert email_norm == agent_norm, (
            f"Email and agent-topic paths should produce the same canonical triple, "
            f"got email={email_norm} vs agent={agent_norm}"
        )


class TestEmailTopicCollapseNonLegalZoom:
    """Verify the domain-injection fix doesn't over-collapse non-LegalZoom emails."""

    def test_slack_token_expired_emails_collapse(self):
        """Slack bot token expiry emails from various senders collapse."""
        fps = set()
        for sender in ["slack.com", "notifications@slack.com", "feedback@slack.com"]:
            norm = normalize_triple_for_email(
                service="", problem_type="", resource="",
                sender_address=sender,
                subject="Your Slack bot token has expired",
            )
            fps.add(compute_fingerprint(*norm))
        assert len(fps) == 1, f"Slack token emails should collapse, got {len(fps)} fingerprints"

    def test_unrelated_emails_stay_separate(self):
        """Emails about completely different topics produce different fingerprints."""
        fp_legalzoom = compute_fingerprint(*normalize_triple_for_email(
            service="compliance", problem_type="deadline", resource="LegalZoom",
            sender_address="legalzoom.com",
            subject="Delaware Annual Tax Statement",
        ))
        fp_slack = compute_fingerprint(*normalize_triple_for_email(
            service="ops", problem_type="token-expiry", resource="Slack bot token",
            sender_address="slack.com",
            subject="Your Slack bot token has expired",
        ))
        assert fp_legalzoom != fp_slack, "Different topics should produce different fingerprints"

    def test_domain_injection_does_not_match_wrong_topic(self):
        """An email from legalzoom.com about a NON-tax topic should NOT
        match the LegalZoom tax deadline topic entries."""
        # If someone forwards a LegalZoom incorporation email, the subject
        # won't contain "delaware" or "tax" tokens, so it shouldn't match
        # the LegalZoom tax topic entries
        norm = normalize_triple_for_email(
            service="", problem_type="", resource="",
            sender_address="legalzoom.com",
            subject="Your LLC formation documents are ready",
        )
        # This should fall through to slugification, not match TOPIC_TRIPLE_MAP
        # (which only has "legalzoom + delaware/tax/filing" patterns)
        # The slugified result will be based on the subject
        assert norm != ("compliance", "legalzoom-deadline", "delaware-annual-tax"), (
            f"Incorporation email should not match tax deadline topic, got {norm}"
        )