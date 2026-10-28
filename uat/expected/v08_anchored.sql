-- v08  "Which vessels are anchored?"
-- Each vessel whose latest report as of today has the AIS status Anchored.
WITH latest AS (
    SELECT DISTINCT ON (t.vessel_id) t.*
    FROM tonnage_test t
    WHERE t.update_date < $1::date + 1
    ORDER BY t.vessel_id, t.update_date DESC, t.first_date_received DESC NULLS LAST
)
SELECT v.vessel_id AS id
FROM latest v
WHERE v.vessel_status = 'Anchored';
