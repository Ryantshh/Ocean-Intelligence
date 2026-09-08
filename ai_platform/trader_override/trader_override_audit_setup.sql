-- Trader override audit trail (Epic 3 / US-3.1).
--
-- Purely a log of what a trader submitted through the override form and
-- who submitted it -- never the source of truth for a vessel's status.
-- The actual override lands in public.tonnage_test by replacing the
-- trader's chosen baseline row with a copy carrying their edits (see
-- trader_override_queries.override_tonnage_row_sql);
-- this table is written to right after that swap succeeds, solely so the
-- Trader Overrides tab can show a trail scoped to trader submissions --
-- tonnage_test itself has no column marking a row as trader-entered, so a
-- trail over tonnage_test directly can't distinguish those rows from
-- ordinary Shipfix-reported ones.
--
-- Deliberately never read by the Dashboard tab or by any of this feature's
-- own read/diff endpoints (vessels_sql, vessel_history_sql) -- it exists
-- only to answer "what has been submitted here, and by whom."
--
-- Idempotent: safe to re-run. Not applied automatically by anything in
-- this repo -- run once manually (psql "$SUPABASE_DB_URL" -f
-- ai_platform/trader_override/trader_override_audit_setup.sql, or paste
-- into the Supabase SQL editor) before the Audit Trail table will resolve.

CREATE TABLE IF NOT EXISTS public.trader_override_audit (
    id BIGSERIAL PRIMARY KEY,
    vessel_id TEXT NOT NULL,
    override_status TEXT NOT NULL,
    open_area TEXT,
    open_date_start TIMESTAMP,
    open_date_end TIMESTAMP,
    order_assignment TEXT,
    entered_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Backs the audit trail's newest-first read and its optional per-vessel filter.
CREATE INDEX IF NOT EXISTS trader_override_audit_vessel_id_idx
    ON public.trader_override_audit (vessel_id, created_at DESC);

-- Added so the Audit Trail table can show what each field actually
-- changed *from*, not just what was submitted -- see
-- trader_override_queries.tonnage_row_snapshot_sql/insert_audit_sql.
-- Nullable and backfilled with nothing: a row logged before this column
-- set existed has old_* = NULL, meaning "unknown before-value", which the
-- dashboard renders as "?" rather than mistaking it for "unchanged".
ALTER TABLE public.trader_override_audit
    ADD COLUMN IF NOT EXISTS old_override_status TEXT,
    ADD COLUMN IF NOT EXISTS old_open_area TEXT,
    ADD COLUMN IF NOT EXISTS old_open_date_start TIMESTAMP,
    ADD COLUMN IF NOT EXISTS old_open_date_end TIMESTAMP,
    ADD COLUMN IF NOT EXISTS old_order_assignment TEXT;
