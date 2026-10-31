-- c01  "Show me today's cargo list in West Aussie."  (answered: West Australia)
-- Cargoes loading in West Australia that are on today's book.
--   Loading in West Australia: the load zone is West Australia, or the load
--   port label names it (some enquiries give only the region).
--   Today's book: laycan not cancelled yet, opening within 30 days, circulated
--   in the last 5 days.
--   As of today: received by the working date (see uat/README.md).
SELECT o.order_id::text AS id
FROM order_test o
WHERE o.date_received < $1::date + 1
  AND ('West Australia' = ANY (regexp_split_to_array(o.load_zone, '\s*,\s*'))
       OR o.load_port ILIKE '%West Australia%')
  AND o.laycan_end   >= $1::date
  AND o.laycan_start <  $1::date + 31
  AND o.update_date  >= $1::date - 5;
