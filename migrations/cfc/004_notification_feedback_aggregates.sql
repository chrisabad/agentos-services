-- AGE-13737 / CFC Phase 2 — Notification feedback aggregate view
-- Requires: 002_notifications.sql
-- Feeds the weekly Notification Quality Report (Phase 7)

CREATE MATERIALIZED VIEW IF NOT EXISTS notification_feedback_aggregates AS
SELECT
    topic_class,
    date_trunc('week', created_at)::date AS week_start,
    count(*) AS total_surfaced,
    count(*) FILTER (WHERE feedback->>'sentiment' = 'positive') AS thumbs_up_count,
    count(*) FILTER (WHERE feedback->>'sentiment' = 'negative') AS thumbs_down_count,
    mode() WITHIN GROUP (ORDER BY feedback->>'reason')
        FILTER (WHERE feedback->>'reason' IS NOT NULL) AS top_reason,
    count(*) FILTER (WHERE feedback->>'sentiment' = 'positive')
        - count(*) FILTER (WHERE feedback->>'sentiment' = 'negative') AS net_score
FROM notifications
WHERE state IN ('escalated', 'acted', 'suppressed')
GROUP BY topic_class, week_start;

CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_feedback_aggregates_pk
    ON notification_feedback_aggregates (topic_class, week_start);

COMMENT ON MATERIALIZED VIEW notification_feedback_aggregates IS
    'Weekly per-topic_class feedback aggregate. Refreshed via REFRESH MATERIALIZED VIEW CONCURRENTLY notification_feedback_aggregates from a scheduled job (weekly cron at 8am local Mondays). Feeds the Weekly Notification Quality Report (Phase 7).';
