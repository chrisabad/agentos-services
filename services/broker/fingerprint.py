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


def compute_fingerprint(service: str, problem_type: str, resource: str) -> str:
    """Compute a deterministic fingerprint from (service, problem_type, resource).

    All components are normalized before hashing:
    1. Lowercase + strip whitespace
    2. Underscores and hyphens collapsed to a canonical space separator
    3. Known aliases resolved (e.g., 'auth expired' → 'oauth expired')

    The fingerprint is a hex-encoded SHA-256 of the canonical pipe-delimited form:
        "<service>|<problem_type>|<resource>"

    Args:
        service: The service or business context (e.g., "granola", "slack", "fon")
        problem_type: The category of problem (e.g., "oauth_expired", "build_failure", "compliance")
        resource: The specific resource affected (e.g., "granola-api", "dependabot-pr-115", "legalzoom-font-replacer")

    Returns:
        64-char hex string (SHA-256)
    """
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