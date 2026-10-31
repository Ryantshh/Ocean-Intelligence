-- Change Feed "Field changes".
-- Each vessel report updated in [since, until), listed when its open area,
-- DWT, destination, ETA, region or ballast/laden differ from the vessel's
-- previous batch. Fixture status is not compared; it is supply moves.
--   A batch is every report of the vessel stamped with the same update time:
--   a broker list can carry one vessel in two positions at once, and those
--   reports arrive together. A report is unchanged when any report in the
--   previous batch has the same six values, so two positions repeated from
--   one list to the next are not changes, and reports in the same batch are
--   never compared with each other. Identical reports in one batch are one
--   position sent twice, and listed once.
--   A vessel's first batch is new, not changed. Both batches must be before
--   until.
-- $1 since, $2 until.
WITH positions AS (
    SELECT DISTINCT t.vessel_id, t.update_date,
           t.open_area, t.dwt, t.destination, t.eta, t.parent_zone, t.ballast_laden
    FROM tonnage_test t
    WHERE t.update_date < $2
),
batches AS (
    SELECT DISTINCT vessel_id, update_date
    FROM positions
),
previous AS (
    SELECT vessel_id, update_date,
           LAG(update_date) OVER (PARTITION BY vessel_id ORDER BY update_date) AS previous_update
    FROM batches
)
SELECT t.vessel_id, t.update_date
FROM positions t
JOIN previous p
  ON p.vessel_id = t.vessel_id
 AND p.update_date = t.update_date
WHERE t.update_date >= $1
  AND t.update_date <  $2
  AND p.previous_update IS NOT NULL
  AND NOT EXISTS (
        SELECT 1
        FROM positions before
        WHERE before.vessel_id = t.vessel_id
          AND before.update_date = p.previous_update
          AND before.open_area     IS NOT DISTINCT FROM t.open_area
          AND before.dwt           IS NOT DISTINCT FROM t.dwt
          AND before.destination   IS NOT DISTINCT FROM t.destination
          AND before.eta           IS NOT DISTINCT FROM t.eta
          AND before.parent_zone   IS NOT DISTINCT FROM t.parent_zone
          AND before.ballast_laden IS NOT DISTINCT FROM t.ballast_laden
  );
