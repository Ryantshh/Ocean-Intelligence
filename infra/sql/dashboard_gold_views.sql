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
-- previously-open policy questions are now confirmed by the sponsor:
-- staleness is a 5-day-past-open_date_end rule (not a 48h/update_date
-- rule), and ON SUBS does occupy the vessel for its window the same way a
-- firm fixture does -- see vessel_current_status below for both.
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

-- vessel_current_status and vessel_status_history both get rebuilt with a
-- DROP ... CASCADE below rather than CREATE OR REPLACE, every time this
-- file runs. CREATE OR REPLACE VIEW can only append new columns at the
-- end of an existing view's output -- it cannot remove, reorder, or
-- retype one -- which this file has hit more than once already as these
-- two views evolved. Dropping and recreating unconditionally sidesteps
-- that restriction permanently, for every future edit, not just this
-- one; it's cheap (these are plain views, no stored data to rebuild) and
-- CASCADE takes ecsa_ballasters / regional_supply_demand /
-- vessel_status_counts down with vessel_current_status, but all three are
-- recreated later in this same file, so nothing is lost.
DROP VIEW IF EXISTS vessel_current_status CASCADE;
DROP VIEW IF EXISTS vessel_status_history CASCADE;

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
  -- (it's unknown, not future) -- dashboard_status below already treats a
  -- null update_date as failing the "heard from recently" check, so a
  -- vessel with no covering row and no known update_date lands on
  -- LIKELY FIXED automatically, same as one that's gone stale.
  WHERE t.update_date IS NULL OR t.update_date < tonnage_reference_now()
),
-- A vessel is "new" the day its very first row was ever added, not the day
-- its most-recently-touched row happens to have been received -- confirmed
-- by the sponsor: a re-report of an already-known vessel doesn't make it
-- "new" again. `ranked.first_date_received` (the latest row's own value)
-- is the wrong source for this: confirmed live that first_date_received
-- drifts on ~99% of a re-reported vessel's rows, so the latest row's value
-- reflects that row's own receipt, not the vessel's debut. This takes the
-- true per-vessel minimum across every non-future row instead.
first_seen AS (
  SELECT vessel_id, MIN(first_date_received) AS first_seen_date
  FROM public.tonnage_test
  WHERE first_date_received IS NOT NULL
    AND (update_date IS NULL OR update_date < tonnage_reference_now())
  GROUP BY vessel_id
),
-- Whether a vessel is FIXED/ON SUBS/OPEN *today* is a date-range
-- containment question, and where two rows' windows overlap and disagree,
-- the more recently updated row overrides the older one -- but only for
-- the dates that newer row itself covers, not the older row's whole
-- window. Confirmed by the sponsor with a worked example: an older row
-- reporting OPEN 17/8-19/8 and a newer row reporting FIXED 18/8-20/8
-- should read OPEN on the 17th (only the older row covers that date) and
-- FIXED from the 18th onward (the newer row now covers it, and it's the
-- more recent report). Evaluated at a single date -- today -- that rule is
-- exactly "among every non-future row whose window contains today, take
-- the status of whichever one has the latest update_date", which is what
-- this CTE computes; the sponsor's multi-day walkthrough is that same
-- per-date rule applied to several different "todays" in a row, not a
-- different algorithm needed for the single-date case below. Every row
-- competes on recency, not just FIXED/ON-SUBS ones -- a plain "open" row
-- (no fixture, i.e. commercial_status NULL) with the latest update_date
-- must be able to override an older FIXED/ON-SUBS row the same way a
-- newer fixture overrides an older open report, per the same worked
-- example -- this used to be restricted to commercial_status IN ('FIXED',
-- 'ON SUBS') and is not anymore. Ties on update_date (an identical
-- timestamp) fall back to preferring FIXED, then ON SUBS, purely for a
-- deterministic result -- not expected to matter in practice.
--
-- Revised again after further sponsor consultation: OPEN used to be the
-- unconditional default whenever nothing said FIXED/ON SUBS -- including
-- a vessel with NO row covering today at all. That's no longer assumed
-- to hold forever. A vessel with no covering row is now only kept as
-- OPEN if it's still been heard from recently (updated within the last 5
-- days); past that silence it's reclassified LIKELY FIXED -- a distinct
-- status, not folded into FIXED, so a trader can tell a confirmed
-- fixture from an inferred one. This is also what replaces the old
-- separate is_stale flag: a vessel whose latest row's open_date_end
-- lapsed 5+ days ago reads as LIKELY FIXED here rather than carrying a
-- second, overlapping "stale" signal alongside its status.
--
-- Bug fix, found live: the containment check used to be plain
-- `tonnage_reference_now() BETWEEN open_date_start AND open_date_end`.
-- open_date_start/open_date_end are `timestamp` columns stored at exact
-- midnight, so for a single-day window (open_date_start = open_date_end,
-- confirmed live as 79% of all rows -- 8,754 of 11,102) that BETWEEN only
-- covers the *instant* of midnight, not the calendar day: the moment
-- tonnage_reference_now() ticks past 00:00:00, the row stops "covering
-- today" even though today hasn't ended. This made active_bookings fail
-- to find a covering row for the overwhelming majority of vessels on
-- most days (confirmed live: e.g. VESSEL 0043's and VESSEL 0809's
-- identically-shaped today-covering rows both independently evaluated
-- covers_today = false), silently inflating LIKELY FIXED far beyond what
-- 5-days-of-silence should produce -- most "LIKELY FIXED" vessels were
-- really just falling through a broken containment check, not genuinely
-- unheard-from. Fixed the same way every other date-range comparison in
-- this file already treats a window's end date -- as covering through
-- the end of that calendar day, i.e. up to (but excluding) midnight the
-- day after -- consistent with vessel_status_history's own breakpoint
-- sweep below, which was never affected by this bug since it compares
-- date ranges to date ranges, never a timestamp-with-time-of-day "now"
-- to a bare date.
active_bookings AS (
  SELECT DISTINCT ON (vessel_id)
    vessel_id,
    commercial_status
  FROM public.tonnage_test
  WHERE open_date_start IS NOT NULL
    AND open_date_end IS NOT NULL
    AND (update_date IS NULL OR update_date < tonnage_reference_now())
    AND tonnage_reference_now() >= open_date_start
    AND tonnage_reference_now() < open_date_end + interval '1 day'
  ORDER BY vessel_id, update_date DESC NULLS LAST, (commercial_status = 'FIXED') DESC, (commercial_status = 'ON SUBS') DESC
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
  r.commercial_status                        AS raw_commercial_status,   -- what the LATEST report says, for reference/debugging only -- can legitimately disagree with dashboard_status below (e.g. latest report says FIXED but a still-more-recent row's window has since overridden it for today, so dashboard_status reads something else)
  CASE
    WHEN ab.commercial_status = 'FIXED'   THEN 'FIXED'
    WHEN ab.commercial_status = 'ON SUBS' THEN 'ON SUBS'        -- confirmed by sponsor: on-subs occupies the vessel for that window the same way a firm fixture does, kept under its own raw label rather than merged into FIXED
    WHEN ab.vessel_id IS NOT NULL         THEN 'OPEN'           -- a row's window covers today with no fixture -- an explicit, current "open" declaration, regardless of recency
    WHEN r.update_date IS NOT NULL AND r.update_date >= (tonnage_reference_now() - interval '5 days')
                                           THEN 'OPEN'           -- no row covers today, but this vessel's still been heard from recently -- not yet silent long enough to assume otherwise
    ELSE 'LIKELY FIXED'                                          -- no row covers today, and nothing's been heard from this vessel in 5+ days -- sponsor-confirmed: this long a silence is treated as probably fixed off-market, not still open indefinitely
  END                                        AS dashboard_status,
  tonnage_reference_now() - r.update_date     AS age_since_update,
  (r.open_date_end IS NOT NULL AND r.open_date_end < tonnage_reference_now())  AS open_window_lapsed,
  fs.first_seen_date                         AS first_date_received  -- the vessel's true earliest-ever report, see first_seen above, not the latest row's own value
FROM ranked r
LEFT JOIN first_seen fs
  ON fs.vessel_id = r.vessel_id
LEFT JOIN active_bookings ab
  ON ab.vessel_id = r.vessel_id
WHERE r.rn = 1;

-- ---------------------------------------------------------------------
-- 2. vessel_status_history
-- The vessel's resolved status TIMELINE -- not a log of raw reports, but
-- the sequence of (date range, status) segments that results once
-- overlapping reports are reconciled the same way vessel_current_status's
-- active_bookings CTE resolves "what is this vessel right now": where two
-- rows' open-date windows overlap, the more recently updated row
-- overrides the older one, but only for the dates it itself covers -- the
-- older row's status still stands for any of its own dates the newer row
-- doesn't reach. Confirmed by the sponsor with a worked example: an older
-- row reporting FIXED 17/8-20/8 and a newer row reporting OPEN 19/8-21/8
-- resolves to FIXED 17/8-18/8, then OPEN 19/8-21/8 -- not a flat
-- "whichever row is newest wins outright" and not a "conflict" to flag;
-- there is no more unresolved ambiguity once this rule is applied, so
-- there is nothing left to surface as a conflict (has_conflicting_reports
-- on vessel_current_status was removed for the same reason).
--
-- Computed via a breakpoint sweep, not a day-by-day grid, for cost: every
-- row's open_date_start and (open_date_end + 1 day) is a candidate
-- boundary, since the winning row for a date can only ever change at one
-- of those points. Between two adjacent boundaries lies one atomic
-- interval that every row's coverage is constant across (a row's window
-- can't start or end strictly inside it, by construction); the winner for
-- that interval is whichever covering row has the latest update_date --
-- identical tie-break to active_bookings, including the FIXED-then-ON
-- SUBS fallback for the near-impossible case of two rows sharing the
-- exact same update_date. Adjacent atomic intervals that resolve to the
-- same status are merged back into one output segment, same spirit as the
-- old row-by-row version collapsing consecutive identical statuses. An
-- atomic interval no row covers at all is left out of the output
-- entirely (not shown as an inferred "OPEN" segment) -- this is a record
-- of what was actually reported, not a synthetic calendar; "no covering
-- row" is already the documented default elsewhere (dashboard_status).
--
-- is_first_segment marks each vessel's earliest segment (by
-- open_date_start) -- the baseline nothing "changed" from, since there's
-- no earlier segment to compare against. Callers building a change feed
-- (vessel_status_changes_sql, daily_status_changes_sql) exclude it the
-- same way the old view's `prev_status IS NULL` used to.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW vessel_status_history AS
WITH eligible AS (
  SELECT
    vessel_id,
    update_date,
    open_date_start,
    open_date_end,
    parent_zone,
    CASE
      WHEN commercial_status = 'FIXED'   THEN 'FIXED'
      WHEN commercial_status = 'ON SUBS' THEN 'ON SUBS'
      ELSE 'OPEN'
    END AS status
  FROM public.tonnage_test
  WHERE open_date_start IS NOT NULL
    AND open_date_end IS NOT NULL
    AND (update_date IS NULL OR update_date < tonnage_reference_now())
),
breakpoints AS (
  SELECT vessel_id, open_date_start AS bp FROM eligible
  UNION
  SELECT vessel_id, open_date_end + interval '1 day' AS bp FROM eligible
),
ordered_bp AS (
  SELECT vessel_id, bp,
    LEAD(bp) OVER (PARTITION BY vessel_id ORDER BY bp) AS next_bp
  FROM breakpoints
),
atomic AS (
  SELECT vessel_id, bp AS seg_start, next_bp - interval '1 day' AS seg_end
  FROM ordered_bp
  WHERE next_bp IS NOT NULL AND next_bp > bp
),
resolved AS (
  -- A plain JOIN + DISTINCT ON, not a LATERAL subquery correlated per
  -- atomic row: the LATERAL version re-scanned the entire (materialised)
  -- `eligible` CTE for every atomic interval -- ~15,000 atomic intervals
  -- across all vessels times ~8,000 eligible rows, confirmed live to take
  -- ~18 seconds per query. A join lets Postgres group `eligible` by
  -- vessel_id once (a hash join) instead of re-scanning it per row; an
  -- atomic interval with no covering row is simply absent from an INNER
  -- join's result, same effect the old LEFT JOIN + `WHERE status IS NOT
  -- NULL` had.
  SELECT DISTINCT ON (a.vessel_id, a.seg_start)
    a.vessel_id, a.seg_start, a.seg_end, e.status, e.update_date, e.parent_zone
  FROM atomic a
  JOIN eligible e
    ON e.vessel_id = a.vessel_id
    AND e.open_date_start <= a.seg_start
    AND e.open_date_end >= a.seg_end
  ORDER BY a.vessel_id, a.seg_start, e.update_date DESC NULLS LAST, (e.status = 'FIXED') DESC, (e.status = 'ON SUBS') DESC
),
with_lag AS (
  -- Postgres won't let a window function's argument contain another
  -- window function call directly (LAG inside SUM below), so the LAG
  -- lookups are materialised here first and the running total in
  -- `grouped` reads them as plain columns instead.
  SELECT *,
    LAG(status) OVER w AS prev_status,
    LAG(seg_end) OVER w AS prev_seg_end
  FROM resolved
  WINDOW w AS (PARTITION BY vessel_id ORDER BY seg_start)
),
grouped AS (
  SELECT *,
    SUM(
      CASE WHEN status IS DISTINCT FROM prev_status
             OR seg_start IS DISTINCT FROM (prev_seg_end + interval '1 day')
           THEN 1 ELSE 0 END
    ) OVER (PARTITION BY vessel_id ORDER BY seg_start) AS grp
  FROM with_lag
)
SELECT
  vessel_id,
  MIN(seg_start)                                  AS open_date_start,
  MAX(seg_end)                                    AS open_date_end,
  (ARRAY_AGG(status ORDER BY seg_start))[1]        AS status,
  (ARRAY_AGG(parent_zone ORDER BY seg_start))[1]   AS parent_zone,
  MAX(update_date)                                AS update_date,
  ROW_NUMBER() OVER (PARTITION BY vessel_id ORDER BY MIN(seg_start)) = 1  AS is_first_segment
FROM grouped
GROUP BY vessel_id, grp
ORDER BY vessel_id, open_date_start;

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
