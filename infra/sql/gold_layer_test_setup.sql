-- Gold-layer test tables for scripts/gold_loader/.
--
-- Idempotent: safe to re-run. Not applied by the Lambda itself -- run once
-- manually (psql "$SUPABASE_DB_URL" -f infra/sql/gold_layer_test_setup.sql,
-- or paste into the Supabase SQL editor) before the first invocation.
--
-- Embeddings are plain vector(512) (Cohere embed-v4.0, truncated via
-- output_dimension), not halfvec -- see the sizing note in the project's
-- gold-loader plan for the storage/growth tradeoff this implies against the
-- 500MB database cap.
--
-- No ANN index (HNSW/IVFFlat) is created here on purpose: nothing in this
-- repo does similarity search yet, and an index across 10 vector columns
-- would add meaningfully to on-disk size for no current benefit. Add one
-- later, scoped to whichever column(s) a search feature actually needs.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS public.order_test (
    order_id BIGINT PRIMARY KEY,
    date_received TIMESTAMP,
    update_date TIMESTAMP,
    laycan_start TIMESTAMP,
    laycan_end TIMESTAMP,
    load_port TEXT,
    discharge_port TEXT,
    cargo_type TEXT,
    cargo_description TEXT,
    load_zone TEXT,
    discharge_parent_zone TEXT,
    cargo_weight_min NUMERIC,
    cargo_weight_max NUMERIC,
    assigned TEXT,
    assigned_vessel_name TEXT,
    cargo_type_embedding vector(512),
    discharge_port_embedding vector(512),
    cargo_description_embedding vector(512),
    load_port_embedding vector(512),
    discharge_parent_zone_embedding vector(512),
    load_zone_embedding vector(512),
    embedding_source_hash TEXT,
    gold_loaded_at TIMESTAMP NOT NULL DEFAULT now()
);

-- tonnage_row_key stands in for the composite key
-- (vessel_id, open_date_start, open_date_end, first_date_received) that
-- scripts/glue_transform.py's write_dataset() dedups tonnage rows on --
-- those date columns can be null, so a hash avoids NOT NULL issues a raw
-- composite primary key would run into. Computed in scripts/gold_loader.
CREATE TABLE IF NOT EXISTS public.tonnage_test (
    tonnage_row_key TEXT PRIMARY KEY,
    vessel_id TEXT NOT NULL,
    update_date TIMESTAMP,
    parent_zone TEXT,
    vessel_status TEXT,
    dwt NUMERIC,
    commercial_status TEXT,
    ship_type TEXT,
    ship_size TEXT,
    ballast_laden TEXT,
    destination TEXT,
    open_area TEXT,
    eta TIMESTAMP,
    open_date_start TIMESTAMP,
    open_date_end TIMESTAMP,
    first_date_received TIMESTAMP,
    order_id BIGINT,
    vessel_status_embedding vector(512),
    destination_embedding vector(512),
    parent_zone_embedding vector(512),
    open_area_embedding vector(512),
    embedding_source_hash TEXT,
    gold_loaded_at TIMESTAMP NOT NULL DEFAULT now()
);
