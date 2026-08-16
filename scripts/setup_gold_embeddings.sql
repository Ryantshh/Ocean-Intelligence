-- One-time, idempotent setup for the gold layer: public.orders / public.tonnage
-- with every silver JSON column plus an embedding column per non-filterable
-- text/set column.
--
-- public.orders and public.tonnage live in the fixed "public" schema, outside
-- the CHAINLIT_DB_SCHEMA-driven dev/prod switching db/migrations/ manages, so
-- this runs standalone rather than as an Alembic revision:
--
--   psql "$SUPABASE_DB_URL" -f scripts/setup_gold_embeddings.sql
--
-- Safe to re-run: every statement is IF NOT EXISTS. Existing tables are only
-- ever added to, never altered destructively.

CREATE EXTENSION IF NOT EXISTS vector;

-- orders: order_id is already a stable per-enquiry key (glue_transform.py
-- hashes one when bronze doesn't supply it), so it's the primary key as-is.
CREATE TABLE IF NOT EXISTS public.orders (
    order_id BIGINT PRIMARY KEY
);

ALTER TABLE public.orders
    ADD COLUMN IF NOT EXISTS date_received DATE,
    ADD COLUMN IF NOT EXISTS update_date TIMESTAMP,
    ADD COLUMN IF NOT EXISTS laycan_start DATE,
    ADD COLUMN IF NOT EXISTS laycan_end DATE,
    ADD COLUMN IF NOT EXISTS load_port TEXT,
    ADD COLUMN IF NOT EXISTS discharge_port TEXT,
    ADD COLUMN IF NOT EXISTS cargo_type TEXT,
    ADD COLUMN IF NOT EXISTS cargo_description TEXT,
    ADD COLUMN IF NOT EXISTS load_zone TEXT,
    ADD COLUMN IF NOT EXISTS discharge_parent_zone TEXT,
    ADD COLUMN IF NOT EXISTS cargo_weight_min DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS cargo_weight_max DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS assigned TEXT,
    ADD COLUMN IF NOT EXISTS assigned_vessel_name TEXT,
    ADD COLUMN IF NOT EXISTS load_zone_embedding vector(1024),
    ADD COLUMN IF NOT EXISTS discharge_parent_zone_embedding vector(1024),
    ADD COLUMN IF NOT EXISTS load_port_embedding vector(1024),
    ADD COLUMN IF NOT EXISTS discharge_port_embedding vector(1024),
    ADD COLUMN IF NOT EXISTS cargo_type_embedding vector(1024),
    ADD COLUMN IF NOT EXISTS cargo_description_embedding vector(1024),
    ADD COLUMN IF NOT EXISTS embedding_hashes JSONB NOT NULL DEFAULT '{}'::jsonb;

-- tonnage: a row is a reported position, not a vessel (11,105 rows / 1,037
-- vessels), so vessel_id alone can't be the primary key. This is exactly
-- glue_transform.py's own dedup key for the tonnage silver file -- confirmed
-- against the real source workbook: none of these 4 columns are ever null
-- (0/11,246), and there are exactly 11,105 distinct combinations, matching
-- the published row count.
CREATE TABLE IF NOT EXISTS public.tonnage (
    vessel_id TEXT NOT NULL,
    open_date_start DATE NOT NULL,
    open_date_end DATE NOT NULL,
    first_date_received DATE NOT NULL,
    PRIMARY KEY (vessel_id, open_date_start, open_date_end, first_date_received)
);

ALTER TABLE public.tonnage
    ADD COLUMN IF NOT EXISTS update_date TIMESTAMP,
    ADD COLUMN IF NOT EXISTS parent_zone TEXT,
    ADD COLUMN IF NOT EXISTS vessel_status TEXT,
    ADD COLUMN IF NOT EXISTS dwt DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS commercial_status TEXT,
    ADD COLUMN IF NOT EXISTS ship_type TEXT,
    ADD COLUMN IF NOT EXISTS ship_size TEXT,
    ADD COLUMN IF NOT EXISTS ballast_laden TEXT,
    ADD COLUMN IF NOT EXISTS destination TEXT,
    ADD COLUMN IF NOT EXISTS open_area TEXT,
    ADD COLUMN IF NOT EXISTS eta DATE,
    ADD COLUMN IF NOT EXISTS order_id TEXT,
    ADD COLUMN IF NOT EXISTS parent_zone_embedding vector(1024),
    ADD COLUMN IF NOT EXISTS open_area_embedding vector(1024),
    ADD COLUMN IF NOT EXISTS destination_embedding vector(1024),
    ADD COLUMN IF NOT EXISTS ship_size_embedding vector(1024),
    ADD COLUMN IF NOT EXISTS ship_type_embedding vector(1024),
    ADD COLUMN IF NOT EXISTS vessel_status_embedding vector(1024),
    ADD COLUMN IF NOT EXISTS embedding_hashes JSONB NOT NULL DEFAULT '{}'::jsonb;

-- vessel_id is the leading column of the composite primary key above, so its
-- own btree index already covers the chat agent's vessel_id = ANY(...) filter
-- (tables/tonnage.py) without a separate index.

-- No vector index yet. rank/fuse (README) don't exist to use one, and indexing
-- a column that fills in gradually as embed_lambda.py backfills it is
-- premature -- add one (ivfflat or hnsw, cosine ops) when that stage is built.
