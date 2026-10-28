-- v06  "Which vessels open in West Australia were updated in the last 3 days?"
-- Each vessel at its latest report as of today, open in West Australia, with
-- that report updated on or after today minus 3 days.
WITH latest AS (
    SELECT DISTINCT ON (t.vessel_id) t.*
    FROM tonnage_test t
    WHERE t.update_date < $1::date + 1
    ORDER BY t.vessel_id, t.update_date DESC, t.first_date_received DESC NULLS LAST
)
SELECT v.vessel_id AS id
FROM latest v
WHERE ('West Australia' = ANY (regexp_split_to_array(v.parent_zone, '\s*,\s*'))
       OR v.open_area ILIKE '%West Australia%')
  AND v.update_date >= $1::date - 3;
