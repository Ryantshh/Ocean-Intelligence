-- c06  "Show me the C5 cargoes."
-- C5 is West Australia to Qingdao. No dates asked for.
SELECT o.order_id::text AS id
FROM order_test o
WHERE o.date_received < $1::date + 1
  AND ('West Australia' = ANY (regexp_split_to_array(o.load_zone, '\s*,\s*'))
       OR o.load_port ILIKE '%West Australia%')
  AND o.discharge_port ILIKE '%Qingdao%';
