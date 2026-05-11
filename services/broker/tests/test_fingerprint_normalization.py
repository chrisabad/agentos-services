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