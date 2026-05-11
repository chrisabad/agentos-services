-- AGE-13737 / CFC Phase 2 — Notification objects
-- Requires: 001_topic_classes.sql (FK on topic_class)

CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source TEXT NOT NULL,                       -- producer identifier ('email-triage', 'ops-sweep', 'agent:<name>')
    topic_class TEXT NOT NULL REFERENCES topic_classes(name) ON DELETE RESTRICT,
    priority TEXT NOT NULL CHECK (priority IN ('immediate', 'daily_brief', 'weekly_brief', 'muted')),
    payload JSONB NOT NULL,                     -- { message_text, references, attachments }
    state TEXT NOT NULL DEFAULT 'new' CHECK (state IN ('new', 'read', 'acted', 'escalated', 'suppressed')),
    feedback JSONB,                             -- { sentiment, reason, notes, reactions, responded_at }
    fingerprint TEXT NOT NULL,                  -- SHA-256 hex over normalized (topic_class, payload.key_fields)
    juno_seen_at TIMESTAMPTZ,
    juno_acted_at TIMESTAMPTZ,
    chris_seen_at TIMESTAMPTZ,
    escalated_slack_ts TEXT,                    -- Slack ts when escalated; NULL otherwise
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE notifications IS
    'First-class notification objects (AGE-13735, Phase 2). Replaces the broker s in-flight-message model. Lifecycle: new -> read -> acted -> [escalated|suppressed].';

CREATE INDEX IF NOT EXISTS idx_notifications_juno_queue
    ON notifications (state, priority, created_at)
    WHERE state IN ('new', 'read');

CREATE INDEX IF NOT EXISTS idx_notifications_chris_unread
    ON notifications (created_at)
    WHERE state = 'escalated' AND chris_seen_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_notifications_fingerprint_recent
    ON notifications (fingerprint, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_notifications_topic_class
    ON notifications (topic_class);

-- updated_at maintenance
-- Note: if Paperclip already defines set_updated_at(), this CREATE OR REPLACE is a no-op semantically.
CREATE OR REPLACE FUNCTION cfc_set_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_notifications_updated_at ON notifications;
CREATE TRIGGER trg_notifications_updated_at BEFORE UPDATE ON notifications
    FOR EACH ROW EXECUTE FUNCTION cfc_set_updated_at();
