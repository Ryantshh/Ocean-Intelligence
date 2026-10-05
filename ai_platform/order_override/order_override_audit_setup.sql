-- Order override audit trail -- the order-side counterpart to
-- ai_platform/trader_override/trader_override_audit_setup.sql.
--
-- Purely a log of what a trader submitted through the Order Status
-- Override form and who submitted it -- never the source of truth for an
-- order's laycan window or cargo weight range. The actual update lands in
-- public.order_test by updating the matching row in place (see
-- order_override_queries.update_order_sql); this table is written to
-- right after that update succeeds, solely so the Order Status Override
-- tab can show a trail scoped to trader submissions -- order_test itself
-- has no column marking a row as trader-entered, so a trail over
-- order_test directly can't distinguish those rows from ordinary Shipfix-
-- reported ones.
--
-- customer_name is logged here even though it's never written to
-- order_test (no matching column exists there yet -- see
-- order_override_queries.py's module docstring): this table is a fresh
-- log, not an order_test schema change, so recording what a trader typed
-- doesn't require deciding that open question first.
--
-- Deliberately never read by the Dashboard tab or by any of this
-- feature's own read endpoints (orders_sql, order_detail_sql) -- it
-- exists only to answer "what has been submitted here, and by whom."
--
-- Idempotent: safe to re-run. Not applied automatically by anything in
-- this repo -- run once manually (psql "$SUPABASE_DB_URL" -f
-- ai_platform/order_override/order_override_audit_setup.sql, or paste
-- into the Supabase SQL editor) before the Order tab's Audit Trail table
-- will resolve. Until then, the audit insert and the audit read both fail
-- quietly -- a submission still succeeds and lands in order_test, the
-- Audit Trail table just reports empty instead of erroring (same
-- convention as the tonnage side).

CREATE TABLE IF NOT EXISTS public.order_override_audit (
    id BIGSERIAL PRIMARY KEY,
    order_id TEXT NOT NULL,
    old_laycan_start TIMESTAMP,
    laycan_start TIMESTAMP,
    old_laycan_end TIMESTAMP,
    laycan_end TIMESTAMP,
    old_cargo_weight_min NUMERIC,
    cargo_weight_min NUMERIC,
    old_cargo_weight_max NUMERIC,
    cargo_weight_max NUMERIC,
    customer_name TEXT,
    entered_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Backs the audit trail's newest-first read and its optional per-order filter.
CREATE INDEX IF NOT EXISTS order_override_audit_order_id_idx
    ON public.order_override_audit (order_id, created_at DESC);
