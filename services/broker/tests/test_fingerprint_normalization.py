"""Tests for sender-based triple normalization and slugification (AGE-13644).

Verifies that:
1. Known sender domains produce stable triples via SENDER_TRIPLE_MAP
2. Unknown domains fall back to deterministic slugification
3. Subject-line variants collapse to the same fingerprint
4. extract_sender_domain handles various email address formats
5. slugify_for_fingerprint produces stable, predictable slugs
"""

from __future__ import annotations

from services.broker.fingerprint import (
    compute_fingerprint,
    extract_sender_domain,
    normalize_triple_for_agent_topic,
    normalize_triple_for_email,
    slugify_for_fingerprint,
    SENDER_TRIPLE_MAP,
)


# ── extract_sender_domain ──────────────────────────────────────


def test_extract_sender_domain_angle_brackets():
    assert extract_sender_domain("One Medical <noreply@onemedical.com>") == "onemedical.com"


def test_extract_sender_domain_bare_address():
    assert extract_sender_domain("noreply@onemedical.com") == "onemedical.com"


def test_extract_sender_domain_quoted_name():
    assert extract_sender_domain('"One Medical" <support@onemedical.com>') == "onemedical.com"


def test_extract_sender_domain_subdomain():
    assert extract_sender_domain("alerts@mail.onemedical.com") == "mail.onemedical.com"


def test_extract_sender_domain_empty():
    assert extract_sender_domain("") == ""


def test_extract_sender_domain_no_at():
    assert extract_sender_domain("noreply-onemedical-com") == "noreply-onemedical-com"


# ── slugify_for_fingerprint ────────────────────────────────────


def test_slugify_basic():
    assert slugify_for_fingerprint("One Medical Activation") == "one-medical-activation"


def test_slugify_strips_articles():
    result = slugify_for_fingerprint("Activate Your One Medical Health Benefit")
    assert result == "activate-one-medical-health-benefit"


def test_slugify_strips_possessives():
    result = slugify_for_fingerprint("Your One Medical's Activation Code")
    # "Your" removed, "One Medical's" → "one-medicals" then 's stripped → "one-medical"
    assert "one-medical" in result


def test_slugify_removes_notification_words():
    result = slugify_for_fingerprint("Reminder: Your One Medical Benefit")
    assert "reminder" not in result


def test_slugify_empty_string():
    assert slugify_for_fingerprint("") == "unknown"


def test_slugify_truncates_long():
    long_text = "a " * 50  # 100 chars
    result = slugify_for_fingerprint(long_text.strip())
    assert len(result) <= 60


def test_slugify_collapses_multiple_hyphens():
    result = slugify_for_fingerprint("One --- Medical -- Activation")
    assert "---" not in result
    assert "--" not in result


# ── normalize_triple_for_email: sender table lookup ────────────


def test_normalize_triple_known_sender_onemedical():
    """Known sender domain returns the stable triple from SENDER_TRIPLE_MAP."""
    svc, pt, res = normalize_triple_for_email(
        "email-triage",
        "some-paraphrased-type",
        "paraphrased-resource",
        sender_address="noreply@onemedical.com",
        subject="Your One Medical activation code is ready",
    )
    assert (svc, pt, res) == SENDER_TRIPLE_MAP["onemedical.com"]


def test_normalize_triple_known_sender_typo_variant():
    """Typo variant domain also matches."""
    svc, pt, res = normalize_triple_for_email(
        "email-triage",
        "activation",
        "one-medical-activation-code-VOLLXOM",
        sender_address="support@1lifehealthcare.com",
        subject="Activate your membership",
    )
    assert (svc, pt, res) == SENDER_TRIPLE_MAP["1lifehealthcare.com"]


def test_normalize_triple_sender_overrides_divergent_subjects():
    """Even with wildly different subjects, the sender table produces the same triple."""
    results = []
    for subject in [
        "One Medical benefit activation code VOLLXOM",
        "onemedical/activation-reminder/health-benefit",
        "One Medical health benefit activation reminder",
        "One Medical membership activation reminder",
        "Activate Weekend One Medical health benefit",
        "onemedical_activate_health_benefit",
        "One Medical activation code ready",
        "One Medical health benefit activation needed",
    ]:
        svc, pt, res = normalize_triple_for_email(
            "email-triage",
            "activation-reminder",
            "one-medical",
            sender_address="noreply@onemedical.com",
            subject=subject,
        )
        results.append((svc, pt, res))
    # All 8 must produce the same triple
    assert all(r == results[0] for r in results), f"Divergent results: {results}"


# ── normalize_triple_for_email: slugification fallback ──────────


def test_normalize_triple_unknown_sender_slugifies():
    """Unknown sender falls back to deterministic slugification.

    Note: 'reminder' is a stopword in slugify_for_fingerprint, so
    'activation-reminder' becomes 'activation' after slugification.
    """
    svc, pt, res = normalize_triple_for_email(
        "email-triage",
        "activation-reminder",
        "one-medical",
        sender_address="noreply@unknown-sender.com",
        subject="Your benefit activation",
    )
    # Should slugify the original components, not look up in table
    assert svc == "email-triage"
    assert pt == "activation"  # "reminder" is a stopword, stripped by slugify
    assert res == "one-medical"


def test_normalize_triple_empty_sender_uses_slugification():
    """No sender address → slugification path.

    Note: 'reminder' is a stopword, so 'Activation Reminder' slugifies to 'activation'.
    """
    svc, pt, res = normalize_triple_for_email(
        "Email Triage",
        "Activation Reminder",
        "One Medical",
        sender_address="",
        subject="One Medical activation",
    )
    # Components are slugified deterministically; stopwords removed
    assert svc == "email-triage"
    assert pt == "activation"  # "reminder" is a stopword
    assert res == "one-medical"


def test_normalize_triple_all_unknown_uses_subject():
    """When service and resource are both unknown, slugify the subject as resource."""
    svc, pt, res = normalize_triple_for_email(
        "unknown",
        "unknown",
        "unknown",
        sender_address="noreply@unknown.com",
        subject="One Medical benefit activation",
    )
    assert res != "unknown"  # subject should have been used as fallback
    assert "one" in res
    assert "medical" in res


# ── Fingerprint stability ──────────────────────────────────────


def test_fingerprint_stability_with_sender_table():
    """All 8 One Medical subjects produce the same fingerprint via sender lookup."""
    fingerprints = []
    for subject in [
        "One Medical benefit activation code VOLLXOM",
        "onemedical/activation-reminder/health-benefit",
        "One Medical health benefit activation reminder",
        "One Medical membership activation reminder",
        "Activate Weekend One Medical health benefit",
        "onemedical_activate_health_benefit",
        "One Medical activation code ready",
        "One Medical health benefit activation needed",
    ]:
        svc, pt, res = normalize_triple_for_email(
            "email-triage",
            "activation-reminder",
            "one-medical",
            sender_address="noreply@onemedical.com",
            subject=subject,
        )
        fingerprints.append(compute_fingerprint(svc, pt, res))

    # All fingerprints must be identical
    assert len(set(fingerprints)) == 1, (
        f"Expected 1 unique fingerprint, got {len(set(fingerprints))}: {set(fingerprints)}"
    )


def test_fingerprint_stability_with_slugification():
    """Similar subjects via slugification should produce more-stable fingerprints
    than raw paraphrased canonical names."""
    # Compare raw vs normalized fingerprints for similar subjects
    raw_fps = set()
    slug_fps = set()

    paraphrased_types = [
        "activation-reminder",
        "health-benefit-activation",
        "benefit-activation-code",
        "membership-activation-reminder",
        "health-benefit-reminder",
    ]

    for pt in paraphrased_types:
        # Raw (divergent)
        raw_fps.add(compute_fingerprint("email-triage", pt, "one-medical"))
        # Normalized (should collapse to fewer unique fingerprints)
        norm_svc, norm_pt, norm_res = normalize_triple_for_email(
            "email-triage", pt, "one-medical",
            sender_address="",  # no sender → slugification path
            subject=pt,
        )
        slug_fps.add(compute_fingerprint(norm_svc, norm_pt, norm_res))

    # Slugification should produce fewer unique fingerprints than raw
    assert len(slug_fps) <= len(raw_fps)


def test_normalize_triple_subdomain_sender_matches_base_domain():
    """Subdomain senders like notifications.onemedical.com should match
    the onemedical.com entry in SENDER_TRIPLE_MAP (AGE-13672)."""
    # notifications.X.com — the case that originally triggered the bug
    svc, pt, res = normalize_triple_for_email(
        "email-triage",
        "unknown",
        "unknown",
        sender_address="onemedical@notifications.onemedical.com",
        subject="One Medical activation code",
    )
    assert (svc, pt, res) == SENDER_TRIPLE_MAP["onemedical.com"]


def test_normalize_triple_deep_subdomain_walk():
    """Deeply nested subdomains like mail.e.onemedical.com should walk
    down to onemedical.com."""
    svc, pt, res = normalize_triple_for_email(
        "email-triage",
        "activation-reminder",
        "one-medical",
        sender_address="noreply@mail.e.onemedical.com",
        subject="Activate your membership",
    )
    assert (svc, pt, res) == SENDER_TRIPLE_MAP["onemedical.com"]


def test_normalize_triple_exact_domain_match_preferred():
    """When an exact domain key exists (e.g. onemedical.com), the walk
    should find it directly without needing to strip subdomains."""
    svc, pt, res = normalize_triple_for_email(
        "email-triage",
        "activation",
        "one-medical",
        sender_address="noreply@onemedical.com",
        subject="Activation",
    )
    assert (svc, pt, res) == SENDER_TRIPLE_MAP["onemedical.com"]


def test_fingerprint_subdomain_sender_collapses_to_same_fingerprint():
    """All 8 One Medical emails from the original smoke test, including
    the one from notifications.onemedical.com, should produce the same
    fingerprint (AGE-13672 acceptance criterion)."""
    fingerprints = []
    senders_and_subjects = [
        ("onemedical@notifications.onemedical.com", "One Medical benefit activation code VOLLXOM"),
        ("reminders@onemedical.com", "One Medical health benefit activation reminder"),
        ("support@onemedical.com", "One Medical membership activation reminder"),
        ("noreply@onemedical.com", "Activate Weekend One Medical health benefit"),
        ("alerts@mail.onemedical.com", "One Medical activation code ready"),
        ("no-reply@e.onemedical.com", "One Medical health benefit activation needed"),
        ("info@1lifehealthcare.com", "One Medical benefit activation code"),
        ("news@onetmedical.com", "onemedical_activate_health_benefit"),
    ]
    for sender, subject in senders_and_subjects:
        svc, pt, res = normalize_triple_for_email(
            "email-triage",
            "activation-reminder",
            "one-medical",
            sender_address=sender,
            subject=subject,
        )
        fingerprints.append(compute_fingerprint(svc, pt, res))

    # All 8 must produce the same fingerprint (was 7/8 before fix)
    assert len(set(fingerprints)) == 1, (
        f"Expected 1 unique fingerprint, got {len(set(fingerprints))}: {set(fingerprints)}"
    )


def test_different_senders_different_fingerprints():
    """Different sender domains should produce different fingerprints (unless
    they map to the same triple)."""
    fp1 = compute_fingerprint(*normalize_triple_for_email(
        "email-triage", "activation", "benefit",
        sender_address="noreply@onemedical.com", subject="activation",
    ))
    # A completely different sender (not in table) with different components
    fp2 = compute_fingerprint(*normalize_triple_for_email(
        "email-triage", "billing", "subscription",
        sender_address="noreply@spotify.com", subject="billing",
    ))
    assert fp1 != fp2

# ── normalize_triple_for_agent_topic (AGE-13745) ──────────────


def test_legalzoom_variants_collapse_to_single_fingerprint():
    """AGE-13745 reference case: three observed LegalZoom canonical names
    from 2026-05-11 ledger.json must produce a SINGLE fingerprint after
    normalize_triple_for_agent_topic."""
    variants = [
        # (service, problem_type, resource) — from actual ledger entries
        ("font-replacer", "deadline-reminder", "FON-185-legalzoom"),
        ("fon", "legalzoom-deadline", "de-annual-tax-statement"),
        ("fon", "compliance", "LegalZoom DE Annual Tax Statement"),
        ("font-replacer-llc", "delaware-annual-tax", "legalzoom-filing"),
    ]
    fingerprints = {
        compute_fingerprint(*normalize_triple_for_agent_topic(*v))
        for v in variants
    }
    assert len(fingerprints) == 1, (
        f"Expected 1 unique fingerprint for LegalZoom variants, got {len(fingerprints)}: {fingerprints}"
    )


def test_slack_token_variants_collapse():
    """Slack bot token expiry across companies should collapse."""
    variants = [
        ("slack", "token-expired", "FON-1313-bot"),
        ("slack", "token-invalid", "font-replacer-slack-app"),
        ("font-replacer", "slack-bot-token", "expired"),
    ]
    fingerprints = {
        compute_fingerprint(*normalize_triple_for_agent_topic(*v))
        for v in variants
    }
    assert len(fingerprints) == 1


def test_queue_health_variants_collapse():
    """Queue-health sweep variants from 'queue healthy' status checks."""
    variants = [
        ("juno", "queue-healthy", "paperclip-sweep"),
        ("juno", "sweep-complete", "queue-health-check"),
    ]
    fingerprints = {
        compute_fingerprint(*normalize_triple_for_agent_topic(*v))
        for v in variants
    }
    assert len(fingerprints) == 1


def test_unmapped_topic_falls_back_to_slugify():
    """Topics not in TOPIC_TRIPLE_MAP should still normalize via slugify."""
    # Same underlying topic phrased differently — should collapse via slugify alone
    fp1 = compute_fingerprint(*normalize_triple_for_agent_topic(
        "some-service", "your random problem", "unique-resource",
    ))
    fp2 = compute_fingerprint(*normalize_triple_for_agent_topic(
        "some-service", "the random problem", "unique-resource",
    ))
    # slugify_for_fingerprint strips "your" and "the" — so they should match
    assert fp1 == fp2


def test_unrelated_topics_do_not_collapse():
    """TOPIC_TRIPLE_MAP must not over-collapse — unrelated topics keep separate fingerprints."""
    fp_legalzoom = compute_fingerprint(*normalize_triple_for_agent_topic(
        "fon", "legalzoom-deadline", "delaware",
    ))
    fp_slack = compute_fingerprint(*normalize_triple_for_agent_topic(
        "slack", "token-expired", "font-replacer-bot",
    ))
    fp_queue = compute_fingerprint(*normalize_triple_for_agent_topic(
        "juno", "queue-healthy", "paperclip",
    ))
    fp_random = compute_fingerprint(*normalize_triple_for_agent_topic(
        "some-other-service", "some-other-problem", "some-other-resource",
    ))
    fingerprints = {fp_legalzoom, fp_slack, fp_queue, fp_random}
    assert len(fingerprints) == 4, "TOPIC_TRIPLE_MAP over-collapsed unrelated topics"


def test_topic_triple_map_returns_canonical_for_legalzoom():
    """Verify TOPIC_TRIPLE_MAP entry shape: matching tokens → stable canonical triple."""
    result = normalize_triple_for_agent_topic("fon", "legalzoom-deadline", "delaware-annual-tax")
    # Should return the canonical triple from the map
    assert result == ("compliance", "legalzoom-deadline", "delaware-annual-tax")
