-- AGE-13737 / CFC Phase 2 — Topic class registry
-- Apply order: 001 before 002, 003, 004 (notifications and reports FK reference topic_classes)

CREATE TABLE IF NOT EXISTS topic_classes (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    object_type TEXT NOT NULL CHECK (object_type IN ('notification', 'report')),
    default_priority TEXT CHECK (default_priority IN ('immediate', 'daily_brief', 'weekly_brief', 'muted')),
    default_channel TEXT,                       -- optional hint; router decides final destination
    requires_juno_review BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at TIMESTAMPTZ
);

COMMENT ON TABLE topic_classes IS
    'Registered topic_class values that producers may emit. Prevents the divergence problem at the registry layer (AGE-13735). Producers must select from this list; FK on notifications/reports enforces this at write time.';

-- Seed initial topic classes — minimum set to support Phase 1 migration target (email-triage).
-- Add new rows here as topics emerge; do not invent ad-hoc names at the producer.
INSERT INTO topic_classes (name, description, object_type, default_priority, requires_juno_review) VALUES
    ('legalzoom-deadline', 'LegalZoom filing deadlines across all entities', 'notification', 'daily_brief', FALSE),
    ('state-filing-deadline', 'State annual filings outside LegalZoom', 'notification', 'daily_brief', FALSE),
    ('irs-notice', 'IRS correspondence requiring action', 'notification', 'immediate', FALSE),
    ('legal-counsel-request', 'Counsel-initiated request (signature, review)', 'notification', 'immediate', FALSE),
    ('slack-token-expired', 'Slack bot token invalid/expired for any company app', 'notification', 'immediate', FALSE),
    ('oauth-token-expired', 'OAuth tokens for non-Slack services', 'notification', 'immediate', FALSE),
    ('gateway-down', 'Hermes gateway not responding', 'notification', 'immediate', FALSE),
    ('agent-stuck-in-progress', 'Agent stuck >24h on an in_progress issue', 'notification', 'daily_brief', FALSE),
    ('paperclip-issue-stale', 'Issue >7d in todo without movement', 'notification', 'weekly_brief', FALSE),
    ('cron-failure', 'Scheduled job failed', 'notification', 'daily_brief', FALSE),
    ('deploy-failure', 'CI/CD deploy failed for any AgentOS repo', 'notification', 'immediate', FALSE),
    ('service-restart', 'A managed service restarted unexpectedly', 'notification', 'daily_brief', FALSE),
    ('llm-cost-spike', 'Anomalous LLM cost vs baseline', 'notification', 'daily_brief', FALSE),
    ('accounting-reply-overdue', 'Outstanding response to accountant >5d', 'notification', 'daily_brief', FALSE),
    ('vendor-invoice-due', 'Vendor invoice payment due', 'notification', 'daily_brief', FALSE),
    ('subscription-renewal', 'SaaS renewal in next 7d', 'notification', 'daily_brief', FALSE),
    ('mrr-anomaly', 'Font Replacer MRR change >5% week-over-week', 'notification', 'daily_brief', FALSE),
    ('email-actionable', 'Inbound email requires Chris decision', 'notification', 'immediate', FALSE),
    ('email-thread-stale', 'Outstanding email thread >7d without reply', 'notification', 'daily_brief', FALSE),
    ('external-meeting-prep', 'Meeting in <24h needs prep', 'notification', 'immediate', FALSE),
    ('hire-request', 'Agent or contractor hire needs Chris approval', 'notification', 'immediate', FALSE),
    ('budget-approval', 'Spend above threshold needs approval', 'notification', 'immediate', FALSE),
    ('architecture-decision', 'Architectural change needs sign-off', 'notification', 'daily_brief', FALSE),
    ('merge-elevation', 'PR merge requires admin bypass', 'notification', 'immediate', FALSE),
    ('queue-health-sweep', 'Paperclip queue health sweep results', 'notification', 'weekly_brief', FALSE),
    ('report-draft-ready', 'Report draft ready for Chris (when Juno needs input)', 'notification', 'immediate', FALSE),
    ('report-correction', 'Correction to a published report (Q8)', 'notification', 'immediate', FALSE),
    ('notification-quality-weekly', 'Weekly aggregate of Juno-to-Chris message feedback', 'report', 'daily_brief', TRUE),
    ('business-weekly-summary', 'Per-business weekly status rollup', 'report', 'daily_brief', TRUE),
    ('font-replacer-mrr-monthly', 'Font Replacer MRR + churn monthly', 'report', 'daily_brief', TRUE),
    ('agent-fleet-monthly', 'Agent fleet activity & cost rollup', 'report', 'daily_brief', TRUE),
    ('xero-monthly-close', 'Monthly accounting close summary', 'report', 'daily_brief', TRUE),
    ('ad-hoc-summary', 'One-off summary on request', 'report', 'immediate', TRUE)
ON CONFLICT (name) DO NOTHING;
