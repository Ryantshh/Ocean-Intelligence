-- v03  "Show me the WAF openers."  (answered: West Africa)
-- Each vessel at its latest report as of today that comes open in West
-- Africa. "Openers" is read as where the vessel opens, with no fixture-status
-- filter; confirm that reading with the sponsor.
WITH latest AS (
    SELECT DISTINCT ON (t.vessel_id) t.*
    FROM tonnage_test t
    WHERE t.update_date < $1::date + 1
    ORDER BY t.vessel_id, t.update_date DESC, t.first_date_received DESC NULLS LAST
)
SELECT v.vessel_id AS id
FROM latest v
WHERE 'West Africa' = ANY (regexp_split_to_array(v.parent_zone, '\s*,\s*'))
   OR v.open_area ILIKE '%West Africa%';
