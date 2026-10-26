-- Each trader's saved voyage calculator defaults, one row per login.
--
-- Read by match_orders to prefill every voyage draft, and written by the
-- Voyage defaults tab through /api/voyage-defaults. input_values maps
-- calculator input names (ai_platform/backend/voyage.py) to numbers.
--
-- Idempotent: safe to re-run. Run once manually in the Supabase SQL editor
-- or with psql against the data database.

CREATE TABLE IF NOT EXISTS public.voyage_defaults (
    user_identifier TEXT PRIMARY KEY,
    input_values JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
