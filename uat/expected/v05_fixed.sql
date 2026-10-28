-- v05  "Which vessels are fixed?"
-- Each vessel at its latest report as of today that is FIXED today: the
-- fixture's open window covers today. A fixture for a later or earlier window
-- leaves the vessel open today.
WITH latest AS (
    SELECT DISTINCT ON (t.vessel_id) t.*
    FROM tonnage_test t
    WHERE t.update_date < $1::date + 1
    ORDER BY t.vessel_id, t.update_date DESC, t.first_date_received DESC NULLS LAST
)
SELECT v.vessel_id AS id
FROM latest v
WHERE v.commercial_status = 'FIXED'
  AND v.open_date_start < $1::date + 1
  AND v.open_date_end  >= $1::date;
