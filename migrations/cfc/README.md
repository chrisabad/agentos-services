# CFC Phase 2 — Schema Migrations

Migration files for the Chris-facing Communications redesign (AGE-13735, Phase 2 = AGE-13737).

These create the persistent storage for first-class `Notification` and `Report` objects that replace the broker's in-flight-message model.

## Files (apply in order)

| File | Purpose |
|---|---|
| `001_topic_classes.sql` | Topic class registry + initial seed data |
| `002_notifications.sql` | Notification table + indexes + updated_at trigger |
| `003_reports.sql` | Report table + indexes + updated_at trigger |
| `004_notification_feedback_aggregates.sql` | Materialized view for the weekly quality report |

## How to apply

```bash
# Each file is idempotent (CREATE TABLE IF NOT EXISTS, ON CONFLICT DO NOTHING).
# Safe to re-run.

PGUSER=paperclip PGDATABASE=paperclip psql -f 001_topic_classes.sql
PGUSER=paperclip PGDATABASE=paperclip psql -f 002_notifications.sql
PGUSER=paperclip PGDATABASE=paperclip psql -f 003_reports.sql
PGUSER=paperclip PGDATABASE=paperclip psql -f 004_notification_feedback_aggregates.sql

# Verify
PGUSER=paperclip PGDATABASE=paperclip psql -c "\\dt notifications reports topic_classes"
PGUSER=paperclip PGDATABASE=paperclip psql -c "SELECT count(*) FROM topic_classes;"
# expected: 33 rows from the initial seed
```

## What this does NOT do

- ❌ Does not register itself with any migration tooling (no alembic, no Paperclip-managed migration record). These are raw SQL migrations applied manually.
- ❌ Does not provide down migrations. To roll back: `DROP MATERIALIZED VIEW notification_feedback_aggregates; DROP TABLE reports; DROP TABLE notifications; DROP TABLE topic_classes;`
- ❌ Does not create the services that read/write these tables. The Notification Service (AGE-13738) and Report Service (AGE-13739) will do that.

## Why no migration framework?

The agentos-services repo currently has no migration framework. Rather than introduce one for the first set of tables (which would be a bigger architectural decision than the schema itself), this PR ships raw SQL that operators apply directly. When more migrations are needed, we'll adopt a framework — but not as part of this bleeding-stopper-phase PR.

## Schema overview

```
topic_classes (name PK)
├── notifications (id PK, topic_class FK)
│   └── feedback JSONB (sentiment, reason, notes, reactions)
└── reports (id PK, topic_class FK)
    ├── juno_review JSONB (reviewed_by, edits_summary, kicked_back_to)
    └── feedback JSONB (sentiment, reason, notes, reactions)

notification_feedback_aggregates (materialized view over notifications)
```

Full schema rationale lives in the [Phase 2 spec comment on AGE-13737](https://volley.paperclipai.com/issues/AGE-13737).
