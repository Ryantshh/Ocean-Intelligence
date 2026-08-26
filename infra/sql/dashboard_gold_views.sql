-- =====================================================================
-- Dashboard Status Overview -- Gold Layer Views (Supabase / Postgres)
-- =====================================================================
-- Built against public.tonnage_test / public.order_test -- the
-- vector-embedded gold tables the deployed gold-loader Lambda
-- (scripts/gold_loader) actually keeps up to date, NOT public.tonnage /
-- public."order", which this file used until this revision.
--
-- Why the switch: public.tonnage / public."order" have no loader anywhere
-- in this repository -- nothing here shows how they were populated, and
-- they turned out to be a stale, smaller snapshot with a real data defect:
-- tonnage.order_id (NUMERIC) is precision-corrupted for effectively every
-- row (confirmed live -- e.g. stored as 1.52324E+17 instead of the full
-- 18-digit value, and a random sample all show the same trailing-zeros
-- float-round-trip fingerprint). tonnage_test.order_id (BIGINT) is not
-- corrupted -- confirmed live at full precision -- and every one of
-- tonnage_test's 11,190 rows successfully joins to order_test on order_id
-- (100%, tested directly), where tonnage/order's join could never have
-- matched anything. tonnage_test/order_test are also a superset: every
-- vessel_id in tonnage and every order_id in "order" is present in the
-- _test tables too, plus ~85 more of each -- because the _test tables are
-- fed by the pipeline that's actually deployed and still running
-- (EventBridge -> GoldLoaderLambda on every Glue success, see README),
-- while tonnage/order's undocumented source has not been refreshed since.
--
-- Column names/types differ slightly from the old source and matter for
-- callers: tonnage_test.eta and .first_date_received are `timestamp`
-- (tonnage.eta was bare `date`); tonnage_test.update_date/open_date_start/
-- open_date_end are all naive `timestamp` (tonnage.update_date was
-- `timestamptz`) -- a naive UTC Python datetime binds cleanly against
-- both naive and tz-aware Postgres timestamp columns in this database
-- (empirically confirmed, not assumed -- see
-- ai_platform/backend/dashboard_queries.py's window_start()), so this
-- required no binding changes, only the table/column references below.
-- tonnage_test / order_test also carry vector(512) embedding columns
-- (for a future similarity-search feature) plus embedding_source_hash and
-- gold_loaded_at -- every SELECT below lists columns explicitly rather
-- than using `t.*` specifically to keep those out of this dashboard's
-- queries.
--
-- Not applied automatically by anything in this repo -- run once manually
-- (psql "$SUPABASE_DB_URL" -f infra/sql/dashboard_gold_views.sql, or paste
-- into the Supabase SQL editor). Idempotent: CREATE OR REPLACE VIEW is
-- safe to re-run.
--
-- Verified live: tonnage_test has 11,190 rows / 1,122 distinct vessels,
-- order_test has 1,949 rows -- both supersets of the previous source's
-- 11,105/1,037 and 1,864. commercial_status, parent_zone's comma-space
-- delimiter, and "East Coast South America" as the exact live ECSA zone
-- label all carried over unchanged (same underlying broker data). The two
-- remaining [CONFIRM WITH SPONSOR] items (staleness threshold, whether ON
-- SUBS should occupy the vessel the same way FIXED does) are policy calls,
-- not data questions, and still need sponsor sign-off.
--
-- "Now" is simulated, not real: every staleness/freshness computation below
-- is measured against tonnage_reference_now() / orders_reference_now()
-- (function 0, below) -- real wall-clock now() minus one year -- rather
-- than true now(). Any row dated on or after that simulated "now" is
-- treated as not having happened yet and excluded outright (not just
-- "fresh"), everywhere a view reads tonnage_test/order_test directly. See
-- that function's comment for why.
--
-- order_id precision: both tonnage_test.order_id and order_test.order_id
-- are BIGINT (int8), full precision confirmed live. scripts/glue_transform.py's
-- with_stable_order_id() deliberately caps generated order_ids at 18
-- digits so they fit int8 -- but 18 digits still exceeds JS's
-- Number.MAX_SAFE_INTEGER (2^53-1, ~16 digits), so a raw numeric order_id
-- would silently corrupt in any browser's JSON.parse. Every view below
-- casts order_id to text regardless; application code must keep it a
-- string end to end -- never `Number(order_id)` in JS.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 0. Reference-time functions
-- "Perceived now" for this simulation = real wall-clock now() minus one
-- year, e.g. if today is really 2026-08-17, every view below behaves as
-- though today is 2025-08-17: a vessel last updated 2025-07-16 reads as
-- stale (over a month before perceived-now), and any row dated on or
-- after perceived-now is treated as not having happened yet and excluded
-- outright, not merely "fresh". This is a deliberate simulation choice
-- (explicitly requested), not a bug fix or a data-driven anchor. Both
-- functions currently return the identical expression (kept as two
-- functions, not collapsed to one, so tonnage- and order-side behaviour
-- can diverge again later without every caller changing) and both float
-- forward with the real clock -- one year behind today, always -- so
-- nothing here needs a manual date bump as real time passes.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION tonnage_reference_now() RETURNS timestamptz AS $$
  SELECT now() - interval '1 year'
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION orders_reference_now() RETURNS timestamptz AS $$
  SELECT now() - interval '1 year'
$$ LANGUAGE sql STABLE;

-- ---------------------------------------------------------------------
-- 1. vessel_current_status
-- One row per vessel = the latest reported position/status.
-- public.tonnage_test is a position-report history, not one-row-per-vessel:
-- scripts/glue_transform.py dedupes on
-- (vessel_id, open_date_start, open_date_end, first_date_received), so a
-- vessel can carry multiple distinct reports over time. Current status is
-- always derived by taking the latest update_date per vessel_id, never
-- assumed from row count.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW vessel_current_status AS
WITH ranked AS (
  SELECT
    t.vessel_id,
    t.order_id,
    t.dwt,
    t.ship_type,
    t.ship_size,
    t.ballast_laden,
    t.open_area,
    t.parent_zone,
    t.open_date_start,
    t.open_date_end,
    t.eta,
    t.destination,
    t.update_date,
    t.vessel_status,
    t.commercial_status,
    t.first_date_received,
    ROW_NUMBER() OVER (
      PARTITION BY t.vessel_id
      ORDER BY t.update_date DESC NULLS LAST, t.first_date_received DESC NULLS LAST
    ) AS rn
  FROM public.tonnage_test t
  -- Simulated-future rows are excluded outright, not just deprioritised --
  -- a vessel whose only reports are dated on/after perceived-now (see
  -- function 0) has no "current status" to show at all under this
  -- simulation and correctly disappears from this view, the same way a
  -- vessel with zero real-world reports would. A null update_date is kept
  -- (it's unknown, not future) -- is_stale below already treats it as
  -- automatically stale.
  WHERE t.update_date IS NULL OR t.update_date < tonnage_reference_now()
),
-- Flag vessels whose latest update_date has more than one disagreeing
-- report (same vessel_id + update_date, different open_area/
-- commercial_status/open_date_start). Surfaced to the trader rather than
-- silently resolved -- a genuine reporting conflict, not duplicate noise,
-- should not be hidden behind whichever row ROW_NUMBER() happened to keep.
conflicts AS (
  SELECT vessel_id, update_date
  FROM public.tonnage_test
  WHERE update_date IS NOT NULL AND update_date < tonnage_reference_now()
  GROUP BY vessel_id, update_date
  HAVING COUNT(DISTINCT open_area) > 1
      OR COUNT(DISTINCT COALESCE(commercial_status, '~')) > 1
      OR COUNT(DISTINCT open_date_start) > 1
),
-- Whether a vessel is FIXED/ON SUBS *today* is a date-range containment
-- question, not "whatever the most-recently-touched row says": a FIXED (or
-- ON SUBS) row means the vessel is booked for that row's own
-- [open_date_start, open_date_end] window specifically, and stops meaning
-- that the moment "today" (tonnage_reference_now()) passes open_date_end --
-- even if that row happens to be the newest one on file. Conversely, a
-- vessel whose latest report predates today can still be FIXED today if
-- some *other* row's window covers today. So this searches every
-- (non-future) row per vessel, not just rn = 1, for one whose window
-- contains today. ON SUBS is treated the same way for containment (a
-- discussion in progress still occupies the vessel for that window) and is
-- reported under its own raw label, ON SUBS, rather than merged into
-- FIXED -- the two are kept visibly distinct so a trader can tell "still
-- being discussed" apart from "firmly booked". If a vessel somehow has
-- both a FIXED- and an ON-SUBS-covering row for today, FIXED wins
-- (DISTINCT ON's ORDER BY below). A vessel with no row covering today at
-- all is OPEN -- handled by the LEFT JOIN below defaulting to OPEN when no
-- match exists, not by matching an explicit "open" row.
active_bookings AS (
  SELECT DISTINCT ON (vessel_id)
    vessel_id,
    commercial_status
  FROM public.tonnage_test
  WHERE commercial_status IN ('FIXED', 'ON SUBS')
    AND open_date_start IS NOT NULL
    AND open_date_end IS NOT NULL
    AND (update_date IS NULL OR update_date < tonnage_reference_now())
    AND tonnage_reference_now() BETWEEN open_date_start AND open_date_end
  ORDER BY vessel_id, (commercial_status = 'FIXED') DESC
)
SELECT
  r.vessel_id,
  r.order_id::text                          AS order_id,       -- see precision note above -- clean full-precision BIGINT on this source, unlike the old public.tonnage
  r.dwt,
  r.ship_type,
  r.ship_size,
  r.ballast_laden,
  r.open_area,
  r.parent_zone,
  CASE WHEN NULLIF(trim(r.parent_zone), '') IS NULL
       THEN ARRAY[]::text[]
       ELSE regexp_split_to_array(trim(r.parent_zone), '\s*,\s*')
  END                                        AS parent_zones,   -- [VERIFY AGAINST LIVE DATA] assumes comma-separated; regex tolerates missing/extra whitespace around the comma
  r.open_date_start,
  r.open_date_end,
  r.eta,
  r.destination,
  r.update_date,
  r.vessel_status                            AS ais_status,     -- navigational (Under way/Anchored/Moored) -- NOT trading status, do not wire into the Fixed/Open/On Subs badge
  r.commercial_status                        AS raw_commercial_status,   -- what the LATEST report says, for reference/debugging only -- can legitimately disagree with dashboard_status below (e.g. latest report says FIXED but that fixture's window has since elapsed and no later window covers today, so dashboard_status reads OPEN)
  CASE
    WHEN ab.commercial_status = 'FIXED'   THEN 'FIXED'
    WHEN ab.commercial_status = 'ON SUBS' THEN 'ON SUBS'        -- [CONFIRM WITH SPONSOR] on-subs treated as occupying the vessel for that window the same way a firm fixture does, kept under its own raw label rather than merged into FIXED -- the occupancy question is still a discussion in progress, not yet confirmed
    ELSE 'OPEN'                                                 -- no row's window covers today -- see active_bookings above; this is NOT the latest row's raw status, see raw_commercial_status
  END                                        AS dashboard_status,
  tonnage_reference_now() - r.update_date     AS age_since_update,
  (r.open_date_end IS NOT NULL AND r.open_date_end < tonnage_reference_now())  AS open_window_lapsed,
  (
    r.update_date IS NULL
    OR (tonnage_reference_now() - r.update_date) > interval '48 hours'  -- [CONFIRM WITH SPONSOR] placeholder threshold, proposal doesn't fix a number; measured against tonnage_reference_now(), see function 0 above
    OR (r.open_date_end IS NOT NULL AND r.open_date_end < tonnage_reference_now())
  )                                          AS is_stale,
  (c.vessel_id IS NOT NULL)                  AS has_conflicting_reports,
  r.first_date_received                      -- appended last: CREATE OR REPLACE VIEW can only add columns at the end, not reorder existing ones
FROM ranked r
LEFT JOIN conflicts c
  ON c.vessel_id = r.vessel_id AND c.update_date = r.update_date
LEFT JOIN active_bookings ab
  ON ab.vessel_id = r.vessel_id
WHERE r.rn = 1;

-- ---------------------------------------------------------------------
-- 2. vessel_status_history
-- The ECSA tracker's "daily status history trail" is derived directly
-- from public.tonnage_test's own row history (it already IS an event
-- log) -- no new events table needed. Collapses consecutive
-- identical-status rows per vessel into transition points.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW vessel_status_history AS
WITH labeled AS (
  SELECT
    vessel_id,
    update_date,
    open_area,
    CASE
      WHEN commercial_status = 'FIXED'   THEN 'FIXED'
      WHEN commercial_status = 'ON SUBS' THEN 'ON SUBS'
      ELSE 'OPEN'
    END AS status,
    LAG(CASE
      WHEN commercial_status = 'FIXED'   THEN 'FIXED'
      WHEN commercial_status = 'ON SUBS' THEN 'ON SUBS'
      ELSE 'OPEN'
    END) OVER (PARTITION BY vessel_id ORDER BY update_date) AS prev_status
  FROM public.tonnage_test
  WHERE update_date IS NOT NULL AND update_date < tonnage_reference_now()
)
SELECT vessel_id, update_date, open_area, prev_status, status
FROM labeled
WHERE prev_status IS NULL OR prev_status != status
ORDER BY vessel_id, update_date;

-- ---------------------------------------------------------------------
-- 3. ecsa_ballasters
-- Strict single-region match on the raw parent_zone column: only vessels
-- whose region is East Coast South America alone. A vessel listed under a
-- compound zone that merely includes ECSA alongside others (e.g. "West
-- Africa, East Coast South America") is deliberately excluded -- this was
-- an earlier version's behaviour (membership test on the exploded zone
-- array) and was reported as wrong: this tracker should show ECSA
-- vessels, not every vessel that could conceivably end up there via a
-- multi-region listing.
--
-- Also requires ballast_laden = 'BALLAST': the story is specifically
-- "vessels ballasting toward ECSA" (repositioning empty), per the product
-- spec's own field list ("Ballast/Laden -- repositioning vs. still
-- carrying cargo"). A laden vessel already carrying cargo toward ECSA is
-- a different situation for a trader and was previously included here by
-- mistake -- this view had no ballast_laden filter at all before this
-- revision. A null ballast_laden is excluded (can't confirm it's
-- ballasting), not assumed either way.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW ecsa_ballasters AS
SELECT v.*
FROM vessel_current_status v
WHERE v.parent_zone = 'East Coast South America'   -- [VERIFY AGAINST LIVE DATA] confirmed live as the exact label, not an abbreviation like "ECSA"
  AND v.ballast_laden = 'BALLAST';

-- ---------------------------------------------------------------------
-- 4. regional_supply_demand
-- Supply = open vessels by parent_zone (tonnage_test). Demand = orders by
-- load_zone (order_test). Both zone fields are exploded before
-- aggregating -- neither is guaranteed single-valued (filterable_fields.md
-- documents both tonnage.parent_zone and orders.load_zone/
-- discharge_parent_zone as "comma-packed sets"; carried over unchanged on
-- this source). The two tables are not guaranteed to share an identical
-- region vocabulary either -- treat a region that only ever appears on
-- one side as a known gap, not a zero.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW regional_supply_demand AS
WITH supply AS (
  SELECT trim(zone) AS region, COUNT(*) AS supply_count
  FROM vessel_current_status,
       LATERAL unnest(parent_zones) AS zone
  WHERE dashboard_status = 'OPEN'
    AND trim(zone) <> ''
  GROUP BY 1
),
demand AS (
  SELECT trim(zone) AS region, COUNT(*) AS demand_count
  FROM public.order_test,
       LATERAL regexp_split_to_table(trim(COALESCE(load_zone, '')), '\s*,\s*') AS zone
  WHERE date_received > orders_reference_now() - interval '90 days'   -- confirmed by product spec ("Date Received -- windows demand to trailing 90 days"); no longer a placeholder
    AND date_received < orders_reference_now()   -- excludes simulated-future orders outright, not just outside the trailing window
    AND trim(zone) <> ''
  GROUP BY 1
)
SELECT
  COALESCE(s.region, d.region) AS region,
  COALESCE(s.supply_count, 0)  AS supply,
  COALESCE(d.demand_count, 0)  AS demand
FROM supply s
FULL OUTER JOIN demand d ON s.region = d.region
ORDER BY 1;

-- ---------------------------------------------------------------------
-- 5. vessel_status_counts
-- Fleet-wide status breakdown (Fixed/Open/On Subs counts), so a trader
-- can see the shape of the market without opening the vessel table at
-- all. A thin wrapper over vessel_current_status -- exists as its own
-- view rather than inline SQL in the query builder purely so it's
-- reusable and testable the same way the other views are.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW vessel_status_counts AS
SELECT dashboard_status, COUNT(*) AS vessel_count
FROM vessel_current_status
GROUP BY dashboard_status
ORDER BY dashboard_status;

-- ---------------------------------------------------------------------
-- 6. Vessel <-> order join example
-- Both sides cast to text (defensive, though both are already clean
-- BIGINT on this source). Unlike the old public.tonnage/public."order"
-- source, this join is not merely illustrative: verified live that all
-- 11,190 rows of tonnage_test successfully join to order_test on
-- order_id (100%), so this can actually be relied on if a future feature
-- needs the vessel<->order link.
-- ---------------------------------------------------------------------
-- SELECT v.vessel_id, v.dashboard_status, o.order_id, o.load_port
-- FROM vessel_current_status v
-- JOIN public.order_test o ON v.order_id = o.order_id::text
-- WHERE v.order_id IS NOT NULL;
