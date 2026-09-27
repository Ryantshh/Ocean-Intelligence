-- Shared desk ledger. Additive and safe to re-run. No source tables are changed.
BEGIN;
CREATE TABLE IF NOT EXISTS public.oi_recommendations (
    id UUID PRIMARY KEY,
    payload JSONB NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.oi_recommendation_decisions (
    id BIGSERIAL PRIMARY KEY,
    recommendation_id UUID NOT NULL REFERENCES public.oi_recommendations(id),
    version INTEGER NOT NULL CHECK (version > 0),
    request_id UUID NOT NULL UNIQUE,
    action TEXT NOT NULL CHECK (action IN ('accept','reject','override')),
    vessel_id TEXT,
    entered_by TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(recommendation_id, version),
    CHECK ((action = 'reject' AND vessel_id IS NULL) OR
           (action <> 'reject' AND vessel_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS oi_recommendations_recent ON public.oi_recommendations(created_at DESC);
CREATE OR REPLACE FUNCTION public.oi_reject_ledger_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
    RAISE EXCEPTION 'Recommendation ledger is append-only';
END $$;
DROP TRIGGER IF EXISTS oi_recommendations_immutable ON public.oi_recommendations;
CREATE TRIGGER oi_recommendations_immutable BEFORE UPDATE OR DELETE ON public.oi_recommendations
FOR EACH ROW EXECUTE FUNCTION public.oi_reject_ledger_mutation();
DROP TRIGGER IF EXISTS oi_decisions_immutable ON public.oi_recommendation_decisions;
CREATE TRIGGER oi_decisions_immutable BEFORE UPDATE OR DELETE ON public.oi_recommendation_decisions
FOR EACH ROW EXECUTE FUNCTION public.oi_reject_ledger_mutation();
-- Supabase browser roles cannot read/write the desk ledger. The server uses
-- its configured database role; do not expose that role's credentials to clients.
ALTER TABLE public.oi_recommendations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.oi_recommendation_decisions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.oi_recommendations, public.oi_recommendation_decisions FROM anon, authenticated;
COMMIT;
