-- Trader status overrides (Epic 3 / US-3.1).
--
-- Lets a trader manually record a vessel's commercial status heard privately
-- (via broker/messaging) before it reaches Shipfix/the pipeline. Append-only,
-- same convention as public.tonnage_test itself being a position-report log
-- rather than one-row-per-vessel (see dashboard_gold_views.sql's
-- vessel_current_status view): every submission is a new row, and a
-- vessel's "current" override is simply its latest row by created_at. This
-- also gives the audit trail for free -- it's just this table, newest first.
--
-- Idempotent: safe to re-run. Not applied automatically by anything in this
-- repo -- run once manually (psql "$SUPABASE_DB_URL" -f
-- ai_platform/trader_override/trader_override_setup.sql, or paste into the Supabase SQL
-- editor) before the trader override tab's endpoints will resolve.
--
-- Deliberately out of scope here: deriving an "effective" open/closed status
-- from these overrides for the dashboard or chatbot. That's tracked
-- separately on the Supabase side. This table only captures what a trader
-- entered, validated and as-is.
--
-- Overridable fields beyond commercial_status: chosen to mirror what a
-- broker/trader actually relays in an "open" report -- open_area,
-- destination, eta, open_date_start/end, ballast_laden, parent_zone --
-- mapping directly onto the tonnage_test columns those describe. Deliberately
-- excludes vessel_status (AIS-derived, not broker-reported), dwt/ship_type/
-- ship_size (static vessel attributes), and update_date/first_date_received/
-- order_id (pipeline-owned). All optional except override_status -- a trader
-- may only have new word on the status, not a full re-report.

CREATE TABLE IF NOT EXISTS public.vessel_status_overrides (
    id BIGSERIAL PRIMARY KEY,
    vessel_id TEXT NOT NULL,
    override_status TEXT NOT NULL CHECK (override_status IN (
        'AVAILABLE', 'ON SUBS', 'FIXED', 'FAILED', 'CANCELLED', 'POSS FIXED',
        'PROGRAM', 'CONTRACT', 'RELET', 'BALLAST FIXED', 'DO NOT COUNT', 'WATCHLIST'
    )),
    open_area TEXT,
    destination TEXT,
    eta TIMESTAMP,
    open_date_start TIMESTAMP,
    open_date_end TIMESTAMP,
    ballast_laden TEXT CHECK (ballast_laden IN ('LADEN', 'BALLAST')),
    parent_zone TEXT,
    note TEXT,
    entered_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ALTER ... ADD COLUMN IF NOT EXISTS so re-running this file brings an
-- already-created table (from before these columns existed) up to date too.
ALTER TABLE public.vessel_status_overrides ADD COLUMN IF NOT EXISTS open_area TEXT;
ALTER TABLE public.vessel_status_overrides ADD COLUMN IF NOT EXISTS destination TEXT;
ALTER TABLE public.vessel_status_overrides ADD COLUMN IF NOT EXISTS eta TIMESTAMP;
ALTER TABLE public.vessel_status_overrides ADD COLUMN IF NOT EXISTS open_date_start TIMESTAMP;
ALTER TABLE public.vessel_status_overrides ADD COLUMN IF NOT EXISTS open_date_end TIMESTAMP;
ALTER TABLE public.vessel_status_overrides ADD COLUMN IF NOT EXISTS ballast_laden TEXT;
ALTER TABLE public.vessel_status_overrides ADD COLUMN IF NOT EXISTS parent_zone TEXT;
DO $$ BEGIN
    ALTER TABLE public.vessel_status_overrides
        ADD CONSTRAINT vessel_status_overrides_ballast_laden_check
        CHECK (ballast_laden IN ('LADEN', 'BALLAST'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Backs both "latest override per vessel" lookups and the audit trail's
-- optional per-vessel filter, in the row order it's always read in.
CREATE INDEX IF NOT EXISTS vessel_status_overrides_vessel_id_idx
    ON public.vessel_status_overrides (vessel_id, created_at DESC);
