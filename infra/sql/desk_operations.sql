-- Desk accounts, distributed request admission and reviewer labels.
BEGIN;
CREATE TABLE IF NOT EXISTS public.oi_trader_accounts (
 identifier TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
 active BOOLEAN NOT NULL DEFAULT true, session_version INTEGER NOT NULL DEFAULT 1,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.oi_login_attempts (
 actor_hash TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS oi_login_attempts_window ON public.oi_login_attempts(created_at,actor_hash);
CREATE TABLE IF NOT EXISTS public.oi_workflow_requests (
 id UUID PRIMARY KEY, actor TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 lease_until TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ,
 outcome TEXT, latency_ms INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER,
 recommendation_id UUID REFERENCES public.oi_recommendations(id)
);
CREATE INDEX IF NOT EXISTS oi_workflow_requests_window ON public.oi_workflow_requests(started_at,actor);
CREATE TABLE IF NOT EXISTS public.oi_evaluation_labels (
 id UUID PRIMARY KEY, recommendation_id UUID NOT NULL REFERENCES public.oi_recommendations(id),
 reviewer TEXT NOT NULL, relevant TEXT[] NOT NULL, forbidden TEXT[] NOT NULL,
 rationale TEXT NOT NULL CHECK(length(trim(rationale)) > 0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS oi_evaluation_labels_latest ON public.oi_evaluation_labels(recommendation_id,created_at DESC);
DROP TRIGGER IF EXISTS oi_labels_immutable ON public.oi_evaluation_labels;
CREATE TRIGGER oi_labels_immutable BEFORE UPDATE OR DELETE ON public.oi_evaluation_labels
FOR EACH ROW EXECUTE FUNCTION public.oi_reject_ledger_mutation();
ALTER TABLE public.oi_trader_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.oi_login_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.oi_workflow_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.oi_evaluation_labels ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.oi_trader_accounts, public.oi_login_attempts, public.oi_workflow_requests, public.oi_evaluation_labels FROM anon, authenticated;
COMMIT;
