-- AGE-13737 / CFC Phase 2 — Report objects
-- Requires: 001_topic_classes.sql (FK on topic_class)

CREATE TABLE IF NOT EXISTS reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source TEXT NOT NULL,                       -- composer agent identifier
    topic_class TEXT NOT NULL REFERENCES topic_classes(name) ON DELETE RESTRICT,
    draft_version INT NOT NULL DEFAULT 1,
    published_version INT,                      -- NULL until published
    state TEXT NOT NULL DEFAULT 'drafted' CHECK (state IN ('drafted', 'reviewed', 'published', 'archived')),
    storage_type TEXT NOT NULL CHECK (storage_type IN ('notion', 'paperclip_doc')),
    storage_url TEXT,
    storage_doc_id TEXT,
    juno_review JSONB,                          -- { reviewed_by, reviewed_at, edits_summary, kicked_back_to }
    sources_cited JSONB NOT NULL DEFAULT '[]'::jsonb,
    feedback JSONB,                             -- different reason vocab than notifications
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at TIMESTAMPTZ
);

COMMENT ON TABLE reports IS
    'First-class report objects (AGE-13735, Phase 2). Versioned, immutable once published. Required Juno-review pass before publish per the PRD.';

CREATE INDEX IF NOT EXISTS idx_reports_juno_review_queue
    ON reports (state, created_at)
    WHERE state = 'drafted';

CREATE INDEX IF NOT EXISTS idx_reports_topic_class
    ON reports (topic_class);

DROP TRIGGER IF EXISTS trg_reports_updated_at ON reports;
CREATE TRIGGER trg_reports_updated_at BEFORE UPDATE ON reports
    FOR EACH ROW EXECUTE FUNCTION cfc_set_updated_at();
