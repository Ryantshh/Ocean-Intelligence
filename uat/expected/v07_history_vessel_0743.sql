-- v07  "Show me the position history of VESSEL 0743."
-- Every report of VESSEL 0743 up to today, one row per report. Compared by
-- count, since the same vessel appears once per report.
SELECT t.vessel_id AS id
FROM tonnage_test t
WHERE t.vessel_id = 'VESSEL 0743'
  AND t.update_date < $1::date + 1;
