-- v02  "Show me the ECSA ballasters."  (answered: East Coast South America)
-- Each vessel at its latest report as of today, in ballast, open in East
-- Coast South America. Not a list question, so no window.
WITH latest AS (
    SELECT DISTINCT ON (t.vessel_id) t.*
    FROM tonnage_test t
    WHERE t.update_date < $1::date + 1
    ORDER BY t.vessel_id, t.update_date DESC, t.first_date_received DESC NULLS LAST
)
SELECT v.vessel_id AS id
FROM latest v
WHERE ('East Coast South America' = ANY (regexp_split_to_array(v.parent_zone, '\s*,\s*'))
       OR v.open_area ILIKE '%East Coast South America%')
  AND v.ballast_laden = 'BALLAST';
