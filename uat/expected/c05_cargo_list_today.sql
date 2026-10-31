-- c05  "Show me today's cargo list."
-- Every cargo on today's book: laycan not cancelled yet, opening within 30
-- days, circulated in the last 5 days.
SELECT o.order_id::text AS id
FROM order_test o
WHERE o.date_received < $1::date + 1
  AND o.laycan_end   >= $1::date
  AND o.laycan_start <  $1::date + 31
  AND o.update_date  >= $1::date - 5;
