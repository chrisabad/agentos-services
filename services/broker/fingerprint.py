#!/usr/bin/env python3
"""
Fingerprinting — Deterministic topic fingerprinting for the Attention Broker.

A fingerprint uniquely identifies a topic based on (service, problem_type, resource).
All signals about the same underlying situation map to the same fingerprint,
replacing the old model that keyed on (issueId, channel, alertType).

Fingerprint format: hex-encoded SHA-256 of the canonical form:
  "<service>|<problem_type>|<resource>"

This provides:
- Cross-issue dedup (same underlying problem, different tracking artifacts)
- Cross-channel dedup (same topic, different delivery channel)
- Cross-source dedup (same situation detected from email + Slack + Linear)
"""

import hashlib
import re


# ── Normalization constants ────────────────────────────────────

# Known aliases: maps variant spellings to their canonical form.
# Keys and values are all lowercase, separator-normalized (spaces, no hyphens/underscores).
# Applied after separator normalization, so only the normalized form needs to be listed.
PROBLEM_TYPE_ALIASES: dict[str, str] = {
    # OAuth / authentication
    "auth expired": "oauth expired",
    "auth": "oauth expired",          # bare "auth" usually means expired token
    "authentication": "oauth expired",
    "oauth": "oauth expired",
    # Build / CI
    "build error": "build failure",
    "ci fail": "build failure",
    "ci cd": "build failure",
    # Cron
    "cron error": "cron failure",
    "cron timeout": "cron failure",
    "schedule fail": "cron failure",
}

RESOURCE_ALIASES: dict[str, str] = {
    # OAuth token variants
    "oauth token": "oauth token",
    "oauthtoken": "oauth token",
    # Normalized aliases that already collapse — listed for explicitness
}

# ── Sender normalization table ─────────────────────────────────
# Maps (sender_domain_pattern, subject_keyword_pattern) to a stable
# (service, problem_type, resource) triple. This is the primary mechanism
# for ensuring repeated emails about the same situation produce the same
# broker fingerprint, even when the LLM paraphrases subjects differently.
#
# Each entry has:
#   - domains: list of sender domain substrings to match (lowercase)
#   - keywords: list of subject/body keywords to match (lowercase)
#   - triple: (service, problem_type, resource) — the stable canonical triple
#
# The lookup checks: does ANY domain substring match the sender AND
# does ANY keyword appear in the text? If so, the stable triple is used.
# Entries are checked in order; first match wins.

SENDER_NORMALIZATION_TABLE: list[dict] = [
    # ── One Medical / health benefits ──
    {
        "domains": ["onemedical", "1life healthcare", "1life", "one medical"],
        "keywords": ["activation", "activate", "membership", "benefit", "health benefit",
                      "welcome", "enrollment", "enrol", "code", "voucher"],
        "triple": ("email_triage", "activation_reminder", "one_medical"),
    },
    {
        "domains": ["onemedical", "1life healthcare", "1life", "one medical"],
        "keywords": ["appointment", "booking", "schedule", "visit", "telehealth",
                      "cancel", "reminder", "checkup"],
        "triple": ("email_triage", "appointment_notification", "one_medical"),
    },
    {
        "domains": ["onemedical", "1life healthcare", "1life", "one medical"],
        "keywords": [],  # catch-all for any other One Medical emails
        "triple": ("email_triage", "notification", "one_medical"),
    },
    # ── Granola ──
    {
        "domains": ["granola"],
        "keywords": ["invoice", "billing", "payment", "subscription", "charge"],
        "triple": ("email_triage", "billing_notification", "granola"),
    },
    {
        "domains": ["granola"],
        "keywords": [],
        "triple": ("email_triage", "notification", "granola"),
    },
    # ── Slack ──
    {
        "domains": ["slack.com"],
        "keywords": ["security", "password", "2fa", "sign-in", "suspicious"],
        "triple": ("email_triage", "security_alert", "slack"),
    },
    {
        "domains": ["slack.com"],
        "keywords": ["billing", "plan", "subscription", "invoice"],
        "triple": ("email_triage", "billing_notification", "slack"),
    },
    {
        "domains": ["slack.com"],
        "keywords": [],
        "triple": ("email_triage", "notification", "slack"),
    },
    # ── GitHub ──
    {
        "domains": ["github.com", "github", "noreply@github"],
        "keywords": ["security", "vulnerability", "advisory", "token", "credential"],
        "triple": ("email_triage", "security_alert", "github"),
    },
    {
        "domains": ["github.com", "github", "noreply@github"],
        "keywords": ["dependabot", "dependency", "automated", "pull request"],
        "triple": ("email_triage", "dependency_update", "github"),
    },
    {
        "domains": ["github.com", "github", "noreply@github"],
        "keywords": [],
        "triple": ("email_triage", "notification", "github"),
    },
    # ── Google / Google Workspace ──
    {
        "domains": ["google.com", "googlemail.com", "google"],
        "keywords": ["security", "alert", "suspicious", "new sign-in", "2-step"],
        "triple": ("email_triage", "security_alert", "google"),
    },
    {
        "domains": ["google.com", "googlemail.com", "google"],
        "keywords": ["billing", "subscription", "payment", "invoice"],
        "triple": ("email_triage", "billing_notification", "google"),
    },
    {
        "domains": ["google.com", "googlemail.com", "google"],
        "keywords": [],
        "triple": ("email_triage", "notification", "google"),
    },
    # ── Stripe ──
    {
        "domains": ["stripe.com", "stripe"],
        "keywords": ["payment", "payout", "dispute", "chargeback", "refund"],
        "triple": ("email_triage", "payment_notification", "stripe"),
    },
    {
        "domains": ["stripe.com", "stripe"],
        "keywords": [],
        "triple": ("email_triage", "notification", "stripe"),
    },
    # ── AWS ──
    {
        "domains": ["amazonaws.com", "aws.amazon.com", "aws"],
        "keywords": ["billing", "invoice", "cost", "budget"],
        "triple": ("email_triage", "billing_notification", "aws"),
    },
    {
        "domains": ["amazonaws.com", "aws.amazon.com", "aws"],
        "keywords": ["security", "alert", "vulnerability", "certificate"],
        "triple": ("email_triage", "security_alert", "aws"),
    },
    {
        "domains": ["amazonaws.com", "aws.amazon.com", "aws"],
        "keywords": [],
        "triple": ("email_triage", "notification", "aws"),
    },
    # ── Lemon Squeezy ──
    {
        "domains": ["lemonsqueezy", "lemon squeezy"],
        "keywords": ["order", "subscription", "payment", "refund"],
        "triple": ("email_triage", "financial_notification", "lemon_squeezy"),
    },
    {
        "domains": ["lemonsqueezy", "lemon squeezy"],
        "keywords": [],
        "triple": ("email_triage", "notification", "lemon_squeezy"),
    },
    # ── LegalZoom ──
    {
        "domains": ["legalzoom"],
        "keywords": ["compliance", "filing", "annual report", "registered agent"],
        "triple": ("email_triage", "compliance_reminder", "legalzoom"),
    },
    {
        "domains": ["legalzoom"],
        "keywords": [],
        "triple": ("email_triage", "notification", "legalzoom"),
    },
    # ── BetterStack ──
    {
        "domains": ["betterstack", "better stack"],
        "keywords": ["incident", "alert", "monitor", "down", "recovery"],
        "triple": ("email_triage", "monitoring_alert", "betterstack"),
    },
    {
        "domains": ["betterstack", "better stack"],
        "keywords": [],
        "triple": ("email_triage", "notification", "betterstack"),
    },
    # ── Linear ──
    {
        "domains": ["linear.app", "linear", "notify@linear"],
        "keywords": ["issue", "comment", "assigned", "mention", "notification"],
        "triple": ("email_triage", "notification", "linear"),
    },
    {
        "domains": ["linear.app", "linear", "notify@linear"],
        "keywords": [],
        "triple": ("email_triage", "notification", "linear"),
    },
    # ── Xero ──
    {
        "domains": ["xero.com", "xero"],
        "keywords": ["invoice", "payment", "bank feed", "reconciliation"],
        "triple": ("email_triage", "financial_notification", "xero"),
    },
    {
        "domains": ["xero.com", "xero"],
        "keywords": [],
        "triple": ("email_triage", "notification", "xero"),
    },
]

# ── Deterministic slug normalization ────────────────────────────
# When no sender normalization table entry matches, we apply a deterministic
# slug normalization that collapses common variations so the same subject
# (even with minor rephrasing) converges to the same fingerprint.
#
# This is a fallback — the sender table should be the primary path.

def _slug_normalize(text: str) -> str:
    """Deterministic slug normalization for fallback fingerprint stability.

    Applies:
    1. Lowercase
    2. Strip leading/trailing whitespace
    3. Remove all punctuation except spaces and hyphens
    4. Collapse whitespace to single spaces
    5. Strip common filler words (a, the, your, my, you, please, etc.)
    6. Sort remaining words alphabetically (so word order doesn't matter)
    7. Join with hyphens

    This ensures "One Medical benefit activation code VOLLXOM",
    "One Medical health benefit activation reminder", and
    "Activate Weekend One Medical health benefit" all converge toward
    similar (though not identical) slugs. The sender normalization table
    is the primary dedup mechanism; this is a safety net.
    """
    FILLER_WORDS = frozenset({
        "a", "an", "the", "your", "my", "you", "your", "our", "their",
        "please", "reminder", "notification", "new", "update", "alert",
        "needed", "ready", "important", "action", "required", "just",
    })
    # Lowercase and strip
    text = text.strip().lower()
    # Remove punctuation (keep spaces and hyphens)
    import re as _re
    text = _re.sub(r"[^\w\s\-]", "", text)
    # Collapse whitespace
    text = _re.sub(r"\s+", " ", text).strip()
    # Tokenize, remove filler, deduplicate, sort
    tokens = [t for t in text.split() if t and t not in FILLER_WORDS]
    unique_sorted = sorted(set(tokens))
    return "-".join(unique_sorted)


def normalize_triple(
    service: str,
    problem_type: str,
    resource: str,
    sender: str = "",
    subject: str = "",
    body: str = "",
) -> tuple[str, str, str]:
    """Normalize a (service, problem_type, resource) triple for stable fingerprinting.

    Priority:
    1. If sender + subject/body match an entry in SENDER_NORMALIZATION_TABLE,
       return that entry's stable triple.
    2. Otherwise, apply _slug_normalize to resource and classify_problem_type
       to problem_type for deterministic normalization.
    3. Service is passed through _normalize_component (existing behavior).

    Args:
        service: Raw service string (e.g., "email-triage", "age")
        problem_type: Raw problem type string (e.g., "activation reminder")
        resource: Raw resource string (e.g., "One Medical benefit activation code VOLLXOM")
        sender: Email sender address (e.g., "no-reply@onemedical.com")
        subject: Email subject line
        body: Email body text (first few hundred chars is fine)

    Returns:
        Normalized (service, problem_type, resource) triple for compute_fingerprint.
    """
    sender_lower = (sender or "").lower()
    text_lower = ((subject or "") + " " + (body or "")).lower()

    # Priority 1: Sender normalization table lookup
    for entry in SENDER_NORMALIZATION_TABLE:
        domain_match = any(d in sender_lower for d in entry["domains"])
        keywords = entry.get("keywords", [])
        keyword_match = not keywords or any(k in text_lower for k in keywords)
        if domain_match and keyword_match:
            return entry["triple"]

    # Priority 2: Deterministic fallback normalization
    svc = _normalize_component(service)
    ptype_norm = _normalize_component(problem_type)
    ptype = _apply_aliases(ptype_norm, PROBLEM_TYPE_ALIASES)
    # Don't just use _normalize_component on resource — that preserves too much
    # variation. Instead slug-normalize to collapse rephrasings.
    res = _slug_normalize(resource) if resource else "unknown"

    # If problem_type still looks like free text (multi-word and not a known
    # canonical value), try classify_problem_type for a stable category.
    # Known canonical values include alias target values and single-word types.
    alias_canonical_values = set(PROBLEM_TYPE_ALIASES.values())
    is_canonical = (" " not in ptype) or (ptype in alias_canonical_values)
    if not is_canonical:
        classified = classify_problem_type(subject or resource or problem_type)
        ptype = classified

    return (svc, ptype, res)


def _normalize_component(value: str) -> str:
    """Normalize a single fingerprint component.

    Steps:
    1. Lowercase
    2. Strip leading/trailing whitespace
    3. Replace underscores and hyphens with spaces
    4. Collapse multiple whitespace to single space
    5. Strip again

    This ensures 'oauth_token', 'oauth-token', and 'oauth token' all produce
    the same intermediate form before alias resolution.
    """
    v = value.strip().lower()
    v = re.sub(r"[_\-]+", " ", v)
    v = re.sub(r"\s+", " ", v)
    return v.strip()


def _apply_aliases(normalized: str, alias_map: dict[str, str]) -> str:
    """Apply alias mapping to a normalized component.

    If the normalized value matches an alias key, return the canonical value.
    Otherwise return the normalized value unchanged.
    """
    return alias_map.get(normalized, normalized)


def compute_fingerprint(
    service: str,
    problem_type: str,
    resource: str,
    sender: str = "",
    subject: str = "",
    body: str = "",
) -> str:
    """Compute a deterministic fingerprint from (service, problem_type, resource).

    If sender and/or subject are provided, the SENDER_NORMALIZATION_TABLE is
    consulted first to produce a stable triple. Otherwise, deterministic slug
    normalization is applied as a fallback. This prevents fingerprint divergence
    when the caller paraphrases topics differently across repeated signals.

    All components are normalized before hashing:
    1. (If sender/subject provided) SENDER_NORMALIZATION_TABLE lookup → stable triple
    2. (Fallback) Lowercase + strip whitespace + collapse separators
    3. Known aliases resolved (e.g., 'auth expired' → 'oauth expired')

    The fingerprint is a hex-encoded SHA-256 of the canonical pipe-delimited form:
        "<service>|<problem_type>|<resource>"

    Args:
        service: The service or business context (e.g., "granola", "slack", "fon")
        problem_type: The category of problem (e.g., "oauth_expired", "build_failure", "compliance")
        resource: The specific resource affected (e.g., "granola-api", "dependabot-pr-115", "legalzoom-font-replacer")
        sender: Optional email sender address for normalization table lookup
        subject: Optional email subject line for normalization table lookup
        body: Optional email body text for normalization table lookup

    Returns:
        64-char hex string (SHA-256)
    """
    if sender or subject or body:
        svc, ptype, res = normalize_triple(
            service, problem_type, resource,
            sender=sender, subject=subject, body=body,
        )
    else:
        svc = _normalize_component(service)
        ptype = _apply_aliases(_normalize_component(problem_type), PROBLEM_TYPE_ALIASES)
        res = _apply_aliases(_normalize_component(resource), RESOURCE_ALIASES)
    canonical = f"{svc}|{ptype}|{res}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def extract_service_from_issue_id(issue_id: str) -> str:
    """Extract the business/service prefix from a Paperclip issue identifier.

    Maps AGE → age, WEE → wee, FON → fon, KAL → kal, PIX → pix, etc.

    Args:
        issue_id: Issue identifier like "AGE-7533" or "WEE-325"

    Returns:
        Lowercase service string, or "unknown" if not parseable
    """
    if not issue_id:
        return "unknown"
    prefix = issue_id.split("-")[0].upper() if "-" in str(issue_id) else str(issue_id).upper()
    service_map = {
        "AGE": "age",
        "WEE": "wee",
        "FON": "fon",
        "KAL": "kal",
        "PIX": "pix",
        "STU": "stu",
        "DIA": "dia",
    }
    return service_map.get(prefix, prefix.lower())


def classify_problem_type(text: str) -> str:
    """Best-effort classification of a problem type from free text.

    Uses keyword matching to classify into standard problem type categories.
    Falls back to "general" if no match.

    Args:
        text: Free-text description or title to classify

    Returns:
        Problem type string (e.g., "oauth_expired", "build_failure", "compliance")
    """
    text_lower = text.lower() if text else ""

    type_patterns = {
        "oauth_expired": ["oauth", "re-auth", "reauth", "token expired", "refresh token", "access token"],
        "authentication": ["auth", "login", "sign in", "credential", "2fa", "mfa"],
        "build_failure": ["build fail", "ci fail", "compile error", "build error", "ci/cd"],
        "compliance": ["compliance", "legal", "regulatory", "gdpr", "consent", "privacy"],
        "configuration_drift": ["config drift", "configuration", "misconfig", "settings"],
        "cron_failure": ["cron fail", "cron error", "cron timeout", "schedule fail"],
        "data_loss": ["data loss", "data corruption", "missing data", "deleted"],
        "dependency": ["dependabot", "dependency", "vulnerability", "cve", "outdated"],
        "escalation": ["escalat", "blocked", "approval", "needs decision"],
        "financial": ["invoice", "payment", "billing", "revenue", "expense", "pnl"],
        "infrastructure": ["infrastructure", "infra", "server", "docker", "container", "deploy"],
        "integration": ["integration", "webhook", "api connect", "connector", "sync"],
        "monitoring": ["alert", "monitor", "health check", "uptime", "down", "incident"],
        "performance": ["performance", "slow", "latency", "timeout", "bottleneck"],
        "phantom_close": ["phantom close", "misassigned", "wrongly closed", "reopened"],
        "security": ["security", "vulnerability", "cve", "breach", "exploit"],
    }

    for ptype, keywords in type_patterns.items():
        for kw in keywords:
            if kw in text_lower:
                return ptype

    return "general"


def infer_resource(issue_id: str = "", title: str = "") -> str:
    """Infer the affected resource from issue ID and/or title.

    Falls back to the issue ID itself if no better resource can be determined.

    Args:
        issue_id: Paperclip issue identifier
        title: Issue title or description

    Returns:
        Resource identifier string
    """
    # If we have a specific system/service name in the title, use it
    title_lower = title.lower() if title else ""

    system_patterns = [
        "granola", "slack", "linear", "notion", "monarch", "xero",
        "betterstack", "litellm", "openclaw", "paperclip", "graphiti",
        "neo4j", "weekend", "font replacer", "agent"
    ]

    for system in system_patterns:
        if system in title_lower:
            return system.replace(" ", "-")

    # Fall back to issue ID
    return issue_id if issue_id else "unknown"


def fingerprint_from_issue(issue_id: str, title: str = "",
                           problem_type: str = "") -> str:
    """Convenience: compute fingerprint from a Paperclip issue.

    Args:
        issue_id: Issue identifier like "AGE-7533"
        title: Issue title for problem type and resource inference
        problem_type: Optional explicit problem type (overrides inference)

    Returns:
        64-char hex fingerprint
    """
    service = extract_service_from_issue_id(issue_id)
    ptype = problem_type or classify_problem_type(title or issue_id)
    resource = infer_resource(issue_id, title)
    return compute_fingerprint(service, ptype, resource)


def canonical_form(service: str, problem_type: str, resource: str) -> str:
    """Return the canonical pipe-delimited form after normalization and alias resolution.

    Useful for display and logging — shows what the fingerprint was actually
    computed from.
    """
    svc = _normalize_component(service)
    ptype = _apply_aliases(_normalize_component(problem_type), PROBLEM_TYPE_ALIASES)
    res = _apply_aliases(_normalize_component(resource), RESOURCE_ALIASES)
    return f"{svc}|{ptype}|{res}"


def name_similarity(name_a: str, name_b: str) -> float:
    """Compute a simple Jaccard-like similarity between two canonical names.

    Splits each name on '/' and counts matching segments (normalized).
    Returns a float in [0.0, 1.0].

    This is intentionally simple — it's used for dedup heuristics,
    not for general string similarity.
    """
    parts_a = set(_normalize_component(p) for p in name_a.split("/") if p.strip())
    parts_b = set(_normalize_component(p) for p in name_b.split("/") if p.strip())
    if not parts_a and not parts_b:
        return 1.0
    if not parts_a or not parts_b:
        return 0.0
    intersection = parts_a & parts_b
    union = parts_a | parts_b
    return len(intersection) / len(union)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Topic Fingerprinting CLI")
    parser.add_argument("--service", required=True, help="Service context (e.g., granola, fon, age)")
    parser.add_argument("--problem-type", required=True, help="Problem type (e.g., oauth_expired, build_failure)")
    parser.add_argument("--resource", required=True, help="Affected resource (e.g., granola-api)")
    args = parser.parse_args()
    fp = compute_fingerprint(args.service, args.problem_type, args.resource)
    print(f"fingerprint={fp}")
    print(f"canonical={args.service.lower()}|{args.problem_type.lower()}|{args.resource.lower()}")