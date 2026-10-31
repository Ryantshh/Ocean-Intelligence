-- c08  "Iron ore cargoes loading in West Australia in March"  (answered: 2025)
-- A month means the laycan overlaps it: it has not cancelled before 1 March
-- and opens by 31 March.
SELECT o.order_id::text AS id
FROM order_test o
WHERE o.date_received < $1::date + 1
  AND ('West Australia' = ANY (regexp_split_to_array(o.load_zone, '\s*,\s*'))
       OR o.load_port ILIKE '%West Australia%')
  AND o.cargo_type ILIKE '%IRON ORE%'
  AND o.laycan_end   >= DATE '2025-03-01'
  AND o.laycan_start <  DATE '2025-04-01';
