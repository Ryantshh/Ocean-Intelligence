-- c03  "What orders are loading in ECSA?"  (answered: East Coast South America)
-- Every cargo loading in East Coast South America as of today. Not a list
-- question, so no laycan or recency window.
SELECT o.order_id::text AS id
FROM order_test o
WHERE o.date_received < $1::date + 1
  AND ('East Coast South America' = ANY (regexp_split_to_array(o.load_zone, '\s*,\s*'))
       OR o.load_port ILIKE '%East Coast South America%');
