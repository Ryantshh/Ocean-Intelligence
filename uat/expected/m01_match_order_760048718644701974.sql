-- m01  "Which open vessels can carry order 760048718644701974?"
-- The matching rule, applied to that C3 iron ore order (laycan 1-7 Jul 2025):
-- each vessel at its latest report as of today that
--   is in one of the cargo's load zones,
--   has an open window sharing at least one day with the laycan,
--   is at least the cargo's minimum tonnes, and
--   is OPEN today: no FIXED or ON SUBS fixture whose window covers today.
-- Open port, ballast/laden, discharge and cargo type play no part.
WITH cargo AS (
    SELECT regexp_split_to_array(o.load_zone, '\s*,\s*') AS zones,
           o.laycan_start::date AS laycan_start,
           o.laycan_end::date   AS laycan_end,
           o.cargo_weight_min   AS tonnes
    FROM order_test o
    WHERE o.order_id::text = '760048718644701974'
),
latest AS (
    SELECT DISTINCT ON (t.vessel_id) t.*
    FROM tonnage_test t
    WHERE t.update_date < $1::date + 1
    ORDER BY t.vessel_id, t.update_date DESC, t.first_date_received DESC NULLS LAST
)
SELECT v.vessel_id AS id
FROM latest v, cargo c
WHERE regexp_split_to_array(v.parent_zone, '\s*,\s*') && c.zones
  AND v.open_date_end::date   >= c.laycan_start
  AND v.open_date_start::date <= c.laycan_end
  AND v.dwt >= c.tonnes
  AND NOT (COALESCE(v.commercial_status, '') IN ('FIXED', 'ON SUBS')
           AND v.open_date_start < $1::date + 1
           AND v.open_date_end  >= $1::date);
