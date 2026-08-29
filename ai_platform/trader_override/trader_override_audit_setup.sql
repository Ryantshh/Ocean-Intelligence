-- Trader override audit trail (Epic 3 / US-3.1).
--
-- Purely a log of what a trader submitted through the override form and
-- who submitted it -- never the source of truth for a vessel's status.
-- The actual override lands in public.tonnage_test as a brand-new
-- position-report row (see trader_override_queries.insert_tonnage_row_sql);
-- this table is written to right after that insert succeeds, solely so the
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
