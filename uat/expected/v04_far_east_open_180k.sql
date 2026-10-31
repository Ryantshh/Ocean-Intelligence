-- v04  "Show me open vessels in the Far East of at least 180,000 dwt."
-- Each vessel at its latest report as of today, open in the Far East, at
-- least 180,000 dwt, and free today. A vessel is FIXED or ON SUBS only while
-- that fixture's open window covers today; otherwise it is open.
WITH latest AS (
    SELECT DISTINCT ON (t.vessel_id) t.*
    FROM tonnage_test t
    WHERE t.update_date < $1::date + 1
    ORDER BY t.vessel_id, t.update_date DESC, t.first_date_received DESC NULLS LAST
)
SELECT v.vessel_id AS id
FROM latest v
WHERE ('Far East' = ANY (regexp_split_to_array(v.parent_zone, '\s*,\s*'))
       OR v.open_area ILIKE '%Far East%')
  AND v.dwt >= 180000
  AND NOT (COALESCE(v.commercial_status, '') IN ('FIXED', 'ON SUBS')
           AND v.open_date_start < $1::date + 1
           AND v.open_date_end  >= $1::date);
-- COALESCE: without it a vessel with no status whose window covers today
-- makes the condition NULL, NOT NULL is NULL, and the vessel is dropped.
