-- Seed data for the Order Status Override "Assigned" field.
--
-- order_test.assigned is 100% null in the source workbooks, so without this
-- the Current Record panel has nothing to show and the Audit Trail's
-- Assigned column has nothing to diff. Run AFTER
-- ai_platform/order_override/order_override_audit_setup.sql (which adds the
-- old_assigned / assigned audit columns). Not applied automatically.
--
-- Idempotent: orders only get a seed value while still null, and the audit
-- rows are skipped if already present (matched on their reason text).

-- Sample the 10 most recently updated orders: alternate true / false.
WITH picked AS (
    SELECT order_id, order_seq,
           row_number() OVER (ORDER BY update_date DESC NULLS LAST, order_id, order_seq) AS rn
    FROM public.order_test
    WHERE assigned IS NULL
    LIMIT 10
)
UPDATE public.order_test o
SET assigned = (p.rn % 2 = 1)
FROM picked p
WHERE o.order_id = p.order_id AND o.order_seq = p.order_seq;

-- Audit trail examples showing the flag flipping in both directions.
INSERT INTO public.order_override_audit
    (order_id, order_seq, old_assigned, assigned, entered_by, reason, action)
SELECT order_id::text, order_seq, false, true, 'seed', '[seed] Fixture confirmed with vessel', 'UPDATE'
FROM public.order_test WHERE assigned IS TRUE
  AND NOT EXISTS (SELECT 1 FROM public.order_override_audit WHERE reason = '[seed] Fixture confirmed with vessel')
ORDER BY update_date DESC NULLS LAST LIMIT 1;

INSERT INTO public.order_override_audit
    (order_id, order_seq, old_assigned, assigned, entered_by, reason, action)
SELECT order_id::text, order_seq, true, false, 'seed', '[seed] Vessel fell through, order back on market', 'UPDATE'
FROM public.order_test WHERE assigned IS FALSE
  AND NOT EXISTS (SELECT 1 FROM public.order_override_audit WHERE reason = '[seed] Vessel fell through, order back on market')
ORDER BY update_date DESC NULLS LAST LIMIT 1;
