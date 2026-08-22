# Ocean-Intelligence-

Data pipeline for the SMU IS483 capstone with Cargill Ocean Transportation. An Excel file placed in the bronze S3 bucket is automatically transformed into cleaned JSON in the silver bucket, then embedded and loaded into Supabase gold tables, where it becomes available to the dashboard and chatbot.

This repository covers the bronze-to-silver-to-gold data layers and the chat agent that queries the gold tables. The RAG components are maintained elsewhere.

Status: deployed and verified end to end.

## What it does

```
Excel upload → EventBridge → SQS → Lambda → Glue → JSON in silver
                (filters)  (queues)  (selects)  (transforms)

Glue SUCCEEDED → EventBridge → Lambda → Cohere embeddings → Supabase gold
                                (reads)   (embeds new/changed) (upserts)
```

### Bronze → Silver

An `.xlsx` file placed in `bronze-ocean-layer` triggers an S3 notification. EventBridge filters for the `.xlsx` suffix and forwards the event to an SQS queue. A Lambda function consumes the queue, waiting up to 60 seconds so that a multi-file upload produces a single invocation.

The Lambda determines what work is outstanding by listing every `.xlsx` object in the bucket and checking each against a DynamoDB ledger of processed files. It passes the remainder to Glue, which transforms them and writes `orders/orders.json` and `tonnage/tonnage.json` to `silver-ocean-layer`, then records the processed files in DynamoDB.

The Lambda discards the queue message contents and re-derives its file list from the bucket on every invocation, making the bucket the single source of truth. Lost, duplicated, or out-of-order messages cannot affect correctness. A scheduled rule invokes the same function daily at 06:00 SGT as a fallback.

Files are tracked by S3 path combined with object ETag, so replacing a file at an existing path is correctly treated as new work.

### Silver → Gold

When the Glue job's state changes to `SUCCEEDED`, AWS emits this automatically to the account's default EventBridge bus — no manual notification setup is needed here, unlike the bronze bucket. A rule matching that job name and state invokes the gold loader Lambda.

The event payload is ignored. Since every Glue run republishes the full silver dataset rather than a delta (a read-modify-write on a single JSON object), the gold loader always re-reads `orders/orders.json` and `tonnage/tonnage.json` in full from `silver-ocean-layer`.

Before doing any real work, it checks a DynamoDB record of each file's content hash from the last successful run. If both `orders.json` and `tonnage.json` are byte-identical to what was already loaded, the run exits immediately — no Supabase connection, no embedding calls.

If the files have changed, it estimates the resulting Supabase payload size (row count × columns, including embedding vectors at the configured dimension) and skips the run if the combined estimate exceeds 495 MB, again without touching Supabase or Cohere.

Otherwise, it fetches each row's previously stored `embedding_source_hash` from `order_test` / `tonnage_test` and only calls the Cohere embeddings API for rows whose hash has changed — unchanged rows reuse their existing stored embedding. All rows (not just the re-embedded ones) are then upserted, since non-embedded columns can change independently of embedded ones. On success, the new content hashes are recorded for the next run's short-circuit check.

`order_test` embeds six free-text fields (`cargo_type`, `discharge_port`, `cargo_description`, `load_port`, `discharge_parent_zone`, `load_zone`); `tonnage_test` embeds four (`vessel_status`, `destination`, `parent_zone`, `open_area`). Each is stored as a separate `vector(512)` column (Cohere `embed-v4.0`, truncated via `output_dimension`) — plain `vector`, not `halfvec`, since 512 is well under pgvector's 2,000-dimension index ceiling. No ANN index (HNSW/IVFFlat) is created on either table yet; nothing in the repo does similarity search over the gold tables today, so an index across ten vector columns would only add to on-disk size for no current benefit.

## Current capabilities and limits

**Any number of files per run.** Every unprocessed file is passed to a single Glue run. No cap exists at any point in the bronze-to-silver pipeline.

**One Glue run at a time.** Files uploaded during a run are not processed immediately. The Lambda detects the running job, raises, and the SQS message is retried after 420 seconds until Glue is available. After 10 retries, approximately 70 minutes, the message moves to the dead-letter queue. Glue's job timeout is 60 minutes, leaving a narrow margin under sustained load.

**Near-real-time for single uploads.** Worst-case latency from upload to Glue start is roughly 60 seconds, plus Glue's cluster provisioning time of about a further 60 seconds.

**Output volume.** `orders.json` holds 1864 records across 15 fields; `tonnage.json` holds 11105 records across 16 fields, covering 1037 unique vessels.

**Gold loader upload cap.** A single silver-to-gold run is skipped if the estimated combined upload (rows plus embedding vectors, at the configured Cohere dimension) exceeds 495 MB. This is a pre-flight check based on row count and dimension, not the size of the silver JSON files themselves.

**Gold loader embedding cost.** Only rows whose embeddable fields changed since the last successful load are sent to Cohere. A Glue run that only touches one of the two datasets, or republishes without content changes, results in zero embedding calls on the next gold load, caught by the file-level content-hash check before any row-level comparison happens.

## Data caveats

**Vessel-to-order links are synthetic.** The tonnage source contains no Order ID column, and no relationship between vessels and orders exists in the source data. `assign_order_ids` generates this relationship artificially, assigning each vessel an `order_id` from the orders pool solely to permit the two tables to be joined. These are not real Cargill fixtures, and any feature presenting them as genuine vessel matches is displaying fabricated data.

**Two required columns contain no data.** `assigned` and `assigned_vessel_name` are 100% null; `commercial_status` is 79.2% null. These are deficiencies in the source workbooks rather than pipeline defects, and both affect core deliverables — vessel-order matching and US-2.2 respectively.

**Date coverage differs.** Tonnage extends to 2026-04-21, orders only to 2026-01-06. Time-based joins will produce a three-month period containing vessels but no orders.

## The chat agent

A LangGraph agent over the gold tables `public.order_test` and `public.tonnage_test`, served by Chainlit at `/chat`. Metadata filtering and dense vector retrieval over the same tables.

```mermaid
flowchart TB
    start(["__start__"]):::terminal
    start -. "history over 80%" .-> compact
    start -. "history small" .-> extract_filters

    compact["<b>compact</b><br/><small>old history → one summary</small>"]:::llm
    compact --> extract_filters

    extract_filters{{"<b>extract_filters</b><br/><small>question → filters + semantic terms</small>"}}:::llm
    extract_filters -. "nothing usable" .-> answer
    extract_filters -. "filters only" .-> build_query
    extract_filters -. "has semantic terms" .-> embed

    embed["<b>embed</b><br/><small>semantic terms → query vectors</small>"]:::llm
    embed --> build_query

    build_query["<b>build_query</b><br/><small>filters + vectors → one SQL statement</small>"]:::guard
    build_query --> narrow

    narrow["<b>narrow</b><br/><small>run it → matching rows</small>"]:::tool
    narrow --> answer

    answer["<b>answer</b><br/><small>rows → reply, streamed</small>"]:::llm
    answer --> finish(["__end__"]):::terminal

    classDef llm fill:#0f766e,stroke:#0b5d56,color:#fff
    classDef tool fill:#1e40af,stroke:#1a3a94,color:#fff
    classDef guard fill:#b45309,stroke:#92400e,color:#fff
    classDef terminal fill:#334155,stroke:#1e293b,color:#fff
```

Teal is a model call, blue a database read, amber deterministic code. Dotted edges are conditional.

**The only branch is whether `embed` runs.** `build_query` and `narrow` always do — a vector search is still SQL, just without a `WHERE`. What changes is what gets emitted: `WHERE …` for filters alone, `ORDER BY … <=> $1 LIMIT k` for semantic terms alone, or the filter narrowing the set before the distance ordering when both are present.

**Filter before ranking.** An ANN index only accelerates a single-column `ORDER BY`, so two semantic fields fall back to a scan — free over a few hundred survivors, slow over 11,105.

**`compact`** summarises everything but the last six messages so history stops growing. Conditional, because it costs a model call.

**`extract_filters`** turns the question into a typed `Filters` object using strict structured outputs. The only place untrusted output enters the pipeline, so every failure mode is handled here and nothing downstream needs to.

**`embed`** turns semantic terms into query vectors, one per field, using the same Cohere `embed-v4.0` model that produced the stored columns — with `input_type=search_query` against the `search_document` used at load time. Not built yet.

**`build_query`** compiles that object into parameterised SQL. Our code, never the model — a bad extraction returns wrong rows, it cannot execute anything.

**`narrow`** runs the statement. There is no `LIMIT` on the filter, so the row count is the true number of matches. Columns are listed explicitly rather than selected with `*`, which would drag every `vector(512)` column back for the caller to discard — 7.2M characters against 110k for a 200-row query.

**`answer`** has three paths: summarise the rows, report an error, or — when nothing filterable was named — answer from the conversation instead of the database.

Two routers, both plain functions that call no model:

| Router | Sits on | Decides |
|---|---|---|
| `route_entry` | `START` | `compact` when history passes 80% of usable context, else `extract_filters` |
| `route_after_extract` | `extract_filters` | `answer` on a clarification or an error, else `build_query` |

### Limits

| Limit | Detail |
|---|---|
| Filterable fields | Tonnage: vessel ids, open/ETA/updated/received windows, dwt, ballast or laden, commercial status. Orders: order ids, laycan, received, updated, cargo weight |
| Not filterable | Region, port, zone, open area, destination, cargo type, cargo description, ship type, vessel status. All are shown in the results panel, which filters per column. Ten of them are embedded and become reachable semantically once `embed` exists |
| Combining conditions | AND only. No OR and no negation, so "fixed or on subs" and "everything except fixed" cannot be asked |
| Enum arity | `ballast_laden` and `commercial_status` take one value; ids take a list |
| Result size | Beyond roughly 950 rows the model's context is exceeded and the query fails with a message asking you to narrow. One month of tonnage is about 1,000 rows |
| Aggregation | None. No `GROUP BY`, no averages, no top-N |
| Joins | One table per question. `tonnage_test.order_id` does now join to `order_test` for all 11,105 rows, but those links are **synthetic** — see Data caveats — so they must not be presented as real fixtures |
| Row meaning | A tonnage row is a reported position, not a vessel — 11,105 rows cover 1,037 vessels |
| Memory | Retrieved rows do not survive the turn. Follow-ups re-query rather than recall, so "show me the third one" has no list to index |
| Relative dates | The extraction prompt carries no current date, so "next month" is guessed rather than computed |

## Full pipeline: bronze to gold

```mermaid
flowchart TB
    upload(["Excel upload<br/><small>s3://bronze-ocean-layer/...</small>"]):::terminal
    schedule(["Daily schedule<br/><small>cron 22:00 UTC / 06:00 SGT</small>"]):::terminal

    upload --> s3event["S3 Object Created<br/><small>.xlsx suffix filter</small>"]:::guard
    s3event --> ebUpload["EventBridge<br/><small>BronzeUploadRule</small>"]:::guard
    ebUpload --> sqs["SQS UploadQueue<br/><small>batches up to 60s</small>"]:::tool
    sqs -. "retry, up to 10x<br/>then DLQ" .-> sqs
    schedule --> ebSchedule["EventBridge<br/><small>DailyScheduleRule</small>"]:::guard

    sqs --> trigger
    ebSchedule --> trigger

    trigger["TriggerLambda<br/><small>list bronze .xlsx, diff vs.</small><br/><small>ProcessedFilesTable (DynamoDB)</small>"]:::llm
    trigger -. "nothing new" .-> doneA(["done<br/><small>message deleted</small>"]):::terminal
    trigger -. "Glue busy" .-> raiseErr["raise → SQS retry"]:::guard
    trigger -- "start_job_run(--input_files=...)" --> glue

    glue["Glue job<br/><small>smu-glue-transform</small><br/><small>read existing silver → merge new → dedupe → rewrite full file</small>"]:::tool
    glue --> silverOrders[("s3://silver.../orders/orders.json")]:::tool
    glue --> silverTonnage[("s3://silver.../tonnage/tonnage.json")]:::tool
    glue --> markProcessed["mark_processed_files<br/><small>→ ProcessedFilesTable</small>"]:::guard

    glue -. "state = SUCCEEDED" .-> ebGlue["EventBridge<br/><small>GlueSucceededRule</small><br/><small>automatic, no manual setup</small>"]:::guard
    ebGlue --> goldLoader

    goldLoader["GoldLoaderLambda<br/><small>always re-reads full silver files</small>"]:::llm
    goldLoader --> hashCheck{{"content hash vs.<br/>GoldProcessedHashTable"}}:::guard
    hashCheck -. "unchanged" .-> doneB(["skipped<br/><small>no DB conn, no embed calls</small>"]):::terminal
    hashCheck -. "changed" .-> sizeCheck{{"estimated payload<br/>≤ 495 MB?"}}:::guard
    sizeCheck -. "over limit" .-> doneC(["skipped<br/><small>logged with size breakdown</small>"]):::terminal
    sizeCheck -. "within limit" .-> rowDedup["fetch existing rows from Supabase<br/><small>compare embedding_source_hash</small>"]:::tool

    rowDedup --> stale["stale rows only"]:::guard
    stale --> cohere["Cohere embed-v4.0<br/><small>embeddings.attach_embeddings</small>"]:::llm
    cohere --> upsert["upsert ALL rows<br/><small>order_test / tonnage_test</small>"]:::tool
    upsert --> recordHash["record new content hashes<br/><small>→ GoldProcessedHashTable</small>"]:::guard
    recordHash --> doneD(["done<br/><small>summary logged</small>"]):::terminal

    classDef llm fill:#0f766e,stroke:#0b5d56,color:#fff
    classDef tool fill:#1e40af,stroke:#1a3a94,color:#fff
    classDef guard fill:#b45309,stroke:#92400e,color:#fff
    classDef terminal fill:#334155,stroke:#1e293b,color:#fff
```

Teal is a Lambda doing real work, blue is a data read/write, amber is a routing or dedup decision, grey is a terminal outcome. Both dashed "skipped" exits on the gold loader side (content-hash match, oversized payload) return before a Supabase connection is opened.

| Layer | What gets skipped/reused | Mechanism |
|---|---|---|
| Bronze → Silver | Already-processed `.xlsx` files | `ProcessedFilesTable`, keyed on S3 key + ETag |
| Silver → Gold, file level | Entire Lambda run if silver files are byte-identical to the last successful load | `GoldProcessedHashTable`, keyed on content hash |
| Silver → Gold, row level | Cohere calls for rows whose embedded fields didn't change | `embedding_source_hash` comparison against Supabase |
| Silver → Gold, size guard | Runs that would produce an oversized Supabase payload | `MAX_UPLOAD_BYTES` (495 MB) pre-flight estimate |

## Deployment

```
scripts/glue_transform.py            ← transformation logic (bronze → silver)
scripts/gold_loader/                 ← handler, db, embeddings, silver_reader (silver → gold)
infra/smu_daily_pipeline.yaml        ← Lambda, queues, Glue job, DynamoDB table (bronze → silver)
infra/smu_gold_loader.yaml           ← Lambda, EventBridge rule, IAM role, hash table (silver → gold)
infra/sql/gold_layer_test_setup.sql  ← order_test / tonnage_test schema (run once manually)
```

Changes to the bronze-to-silver transformation logic require only re-uploading `glue_transform.py` to `s3://bronze-ocean-layer/scripts/`. Infrastructure changes require deploying the relevant CloudFormation stack with `CAPABILITY_NAMED_IAM`.

`smu_daily_pipeline.yaml` needs `BronzeBucketName`, `SilverBucketName`, and `GlueScriptS3Uri`. `smu_gold_loader.yaml` needs `SupabaseDbUrl` and `CohereApiKey` (both `NoEcho`), plus a packaged Lambda zip built per the template's build-step comment and uploaded to `CodeS3Bucket`/`CodeS3Key`. Deploy `smu_gold_loader.yaml` after `smu_daily_pipeline.yaml` — its `GlueJobName` parameter must match the Glue job created by the daily pipeline stack.

Before the first invocation, `infra/sql/gold_layer_test_setup.sql` must be run once manually against the target database (`psql "$SUPABASE_DB_URL" -f infra/sql/gold_layer_test_setup.sql`, or pasted into the Supabase SQL editor) — it is idempotent and safe to re-run, but is not applied automatically by the Lambda or the CloudFormation stack. It creates the `vector` extension plus `order_test` and `tonnage_test`, with `vector(512)` columns matching the default `EmbeddingDimension` parameter; changing that parameter away from 512 requires updating this SQL file's column definitions to match.

EventBridge notifications must be enabled manually on the bronze bucket via S3 → Properties → Event notifications → Amazon EventBridge → Edit → On. CloudFormation cannot apply this because the stack does not own the bucket. If the bucket is recreated the setting must be reapplied, or uploads will generate no events and the pipeline will run only on its daily schedule. The Glue-success trigger for the gold loader needs no equivalent manual step — Glue job state changes are published to the default EventBridge bus automatically.

If files cease to be processed at the bronze-to-silver stage, examine the dead-letter queue `smu-daily-trigger-uploads-dlq` first. If silver data stops reaching Supabase, check the `GoldLoaderLambda`'s CloudWatch logs for a `"skipped"` summary — it may be a legitimate no-op (unchanged content, or an oversized estimated payload) rather than a failure.

Account `FYP_oceanAI` (334298574595), region `us-east-1`, stack `smu-daily-pipeline`.

| Resource | Name |
|---|---|
| DynamoDB (bronze→silver) | `smu-processed-files` |
| DynamoDB (silver→gold) | `smu-gold-loader-processed-hashes` |
| Glue job | `smu-glue-transform` |
| Lambda (bronze→silver) | `smu-daily-trigger` |
| Lambda (silver→gold) | `smu-gold-loader` |
| SQS / DLQ | `smu-daily-trigger-uploads`, `-dlq` |
| EventBridge (bronze→silver) | `smu-daily-trigger-on-upload`, `-schedule` |
| EventBridge (silver→gold) | `smu-gold-loader-on-glue-success` |
| Supabase tables | `order_test`, `tonnage_test` |

The principal scaling constraint is that each silver table is a single JSON object, requiring read-modify-write on every update, which is what necessitates one concurrent Glue run. Migrating silver to RDS PostgreSQL, already in the project proposal, removes this constraint.