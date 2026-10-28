-- Change Feed "New vessels".
-- A vessel is newly open on the day it becomes OPEN: OPEN that day and not
-- OPEN the day before (off the market, or no report covering it), using the
-- same day-by-day status as supply moves. Its first-ever OPEN day counts.
-- It must also have been heard from recently as of that day: some report
-- updated in the 5 days up to and including it.
--   Listed when the day falls in [since, until). The day-by-day status uses
--   every report filed before "now", as supply moves does.
-- $1 since, $2 until, $3 now. Week-on-week ends at midnight; "now" is later.
WITH reports AS (
    SELECT t.vessel_id, t.update_date, t.first_date_received,
           CASE WHEN t.commercial_status IN ('FIXED', 'ON SUBS') THEN 'OFF' ELSE 'OPEN' END AS market,
           t.open_date_start::date AS first_day,
           t.open_date_end::date   AS last_day
    FROM tonnage_test t
    WHERE t.update_date < $3
      AND t.open_date_start IS NOT NULL
      AND t.open_date_end >= t.open_date_start
),
daily AS (
    SELECT DISTINCT ON (r.vessel_id, d) r.vessel_id, d::date AS day, r.market
    FROM reports r
    CROSS JOIN LATERAL generate_series(r.first_day, r.last_day, interval '1 day') AS d
    ORDER BY r.vessel_id, d, r.update_date DESC, r.first_date_received DESC NULLS LAST
)
SELECT today.vessel_id, today.day
FROM daily today
LEFT JOIN daily yesterday
       ON yesterday.vessel_id = today.vessel_id
      AND yesterday.day = today.day - 1
WHERE today.market = 'OPEN'
  AND (yesterday.market IS NULL OR yesterday.market <> 'OPEN')
  AND today.day::timestamp >= $1
  AND today.day::timestamp <  $2
  AND EXISTS (
        SELECT 1 FROM tonnage_test heard
        WHERE heard.vessel_id = today.vessel_id
          AND heard.update_date <  $3
          AND heard.update_date >= today.day - 5
          AND heard.update_date <  today.day + 1
  );
