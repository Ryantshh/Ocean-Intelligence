-- Change Feed "Supply moves" (API key vessel_status_changes).
-- A vessel moves when its status switches between OPEN and off the market
-- (FIXED or ON SUBS), dated by the day the new status takes effect.
--   Status on a day: from the most recently updated report, known before
--   "now", whose open window covers that day. FIXED and ON SUBS are off the
--   market; anything else, including no status, is OPEN.
--   up   = off the market -> OPEN;  down = OPEN -> off the market.
--   FIXED <-> ON SUBS is not a move. A vessel's first covered day is its
--   baseline. Days no report covers are skipped, so a gap that returns to
--   the same status is not a move.
--   Listed when the day falls in [since, until). Every report filed before
--   "now" counts, including one filed after the window closed that
--   back-dates a change into it.
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
),
timeline AS (
    SELECT vessel_id, day, market,
           LAG(market) OVER (PARTITION BY vessel_id ORDER BY day) AS previous
    FROM daily
)
SELECT vessel_id,
       day,
       CASE WHEN market = 'OPEN' THEN 'up' ELSE 'down' END AS direction
FROM timeline
WHERE previous IS NOT NULL
  AND market <> previous
  AND day::timestamp >= $1
  AND day::timestamp <  $2;
