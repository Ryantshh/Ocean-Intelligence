-- v01  "Show me today's tonnage list open in the Pacific."
-- Each vessel at its latest report as of today, open in the Pacific, on
-- today's book: open window not closed yet, opening within 30 days, updated
-- in the last 5 days.
WITH latest AS (
    SELECT DISTINCT ON (t.vessel_id) t.*
    FROM tonnage_test t
    WHERE t.update_date < $1::date + 1
    ORDER BY t.vessel_id, t.update_date DESC, t.first_date_received DESC NULLS LAST
)
SELECT v.vessel_id AS id
FROM latest v
WHERE ('Pacific' = ANY (regexp_split_to_array(v.parent_zone, '\s*,\s*'))
       OR v.open_area ILIKE '%Pacific%')
  AND v.open_date_end   >= $1::date
  AND v.open_date_start <  $1::date + 31
  AND v.update_date     >= $1::date - 5;
