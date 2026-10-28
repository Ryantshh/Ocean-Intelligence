-- c02  "Show me the latest C3 iron ore orders."  (answered: updated in the last 30 days)
-- C3 is Tubarao to Qingdao. Iron ore includes its grades (fines, pellets, ...).
-- "Latest" names no window, so the agent asks; the scripted answer is the last
-- 30 days of updates.
SELECT o.order_id::text AS id
FROM order_test o
WHERE o.date_received < $1::date + 1
  AND o.load_port      ILIKE '%Tubarao%'
  AND o.discharge_port ILIKE '%Qingdao%'
  AND o.cargo_type     ILIKE '%IRON ORE%'
  AND o.update_date   >= $1::date - 30;
