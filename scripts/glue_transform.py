"""AWS Glue ETL job to transform Excel exports into Silver JSON datasets.

This script is designed to run in an AWS Glue 4.x/5.x Spark job and can also be
run locally with the same CLI arguments.
"""

import argparse
import hashlib
import io
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, unquote

import pandas as pd
from pyspark.sql import DataFrame, functions as F

try:
    from awsglue.utils import getResolvedOptions
except Exception:  # pragma: no cover - Glue runtime only
    getResolvedOptions = None

try:
    from awsglue.context import GlueContext
    from awsglue.dynamicframe import DynamicFrame
except Exception:  # pragma: no cover - local fallback
    GlueContext = None
    DynamicFrame = None


def load_dotenv_if_present() -> None:
    """Load basic environment variables from a .env file if present."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_dotenv_if_present()

RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"


def setup_logging() -> logging.Logger:
    """Log to the console always, and to a timestamped file under runs/ when
    that's writable. Local runs get a file under the repo; Glue's container
    filesystem doesn't have a repo checked out, so the file handler is
    skipped there rather than failing the job.
    """
    logger = logging.getLogger("glue_transform")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    try:
        RUNS_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        file_handler = logging.FileHandler(RUNS_DIR / f"glue_transform_{timestamp}.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        logger.warning("could not create log file under %s; logging to console only", RUNS_DIR)

    return logger


logger = setup_logging()


def get_env_var(*names: str, default: Optional[str] = None) -> Optional[str]:
    """Fetch the first non-empty environment variable from the provided names."""
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def resolve_default_s3_uri(bucket_env_names: tuple[str, ...], object_key: str) -> Optional[str]:
    """Build an s3:// URI from a bucket env name and object key."""
    bucket = get_env_var(*bucket_env_names)
    if not bucket:
        return None
    return f"s3://{bucket.rstrip('/')}/{object_key.lstrip('/')}"


def resolve_default_s3_prefix(bucket_env_names: tuple[str, ...]) -> str:
    """Build an s3:// prefix from a bucket env name."""
    bucket = get_env_var(*bucket_env_names)
    if not bucket:
        return "s3://silver-ocean-layer/"
    return f"s3://{bucket.rstrip('/')}/"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transform SMU Excel files into Silver JSON data")
    parser.add_argument("--JOB_NAME", default=None)
    parser.add_argument("--input_files", default=None)
    parser.add_argument("--source_tonnage_s3", default=None)
    parser.add_argument("--source_orders_s3", default=None)
    parser.add_argument("--silver_s3_prefix", default=None)
    parser.add_argument("--glue_database", default=None)
    parser.add_argument("--glue_tonnage_table", default=None)
    parser.add_argument("--glue_orders_table", default=None)
    parser.add_argument("--processed_table_name", default=None)
    parser.add_argument("--tonnage_sheet_name", default=None)
    parser.add_argument("--orders_sheet_name", default=None)
    parser.add_argument("--region", default=None)
    parser.add_argument("--job_bookmark_option", default=None)

    if getResolvedOptions is not None:
        try:
            args, _ = parser.parse_known_args()
            glue_args = getResolvedOptions(sys.argv, [
                "JOB_NAME",
                "source_tonnage_s3",
                "source_orders_s3",
                "silver_s3_prefix",
                "glue_database",
                "glue_tonnage_table",
                "glue_orders_table",
                "processed_table_name",
                "tonnage_sheet_name",
                "orders_sheet_name",
                "region",
                "job_bookmark_option",
            ])
            for key, value in glue_args.items():
                setattr(args, key, value)
            return args
        except Exception:
            pass

    return parser.parse_args()


def get_spark_session():
    from pyspark.sql import SparkSession

    return SparkSession.builder.appName("smu-glue-transform").getOrCreate()


def get_glue_context(spark_session):
    if GlueContext is None:
        return None
    return GlueContext(spark_session.sparkContext)


def read_excel(spark, input_path: str, sheet_name: Optional[str] = None) -> DataFrame:
    import boto3

    bucket, key = parse_s3_uri(input_path)

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        s3_client = boto3.client("s3", region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
        logger.debug("downloading s3://%s/%s to %s", bucket, key, tmp_path)
        s3_client.download_file(bucket, key, tmp_path)
        pdf = pd.read_excel(tmp_path, sheet_name=sheet_name, engine="openpyxl")
        if isinstance(pdf, dict):
            pdf = next(iter(pdf.values()))
        pdf = pdf.where(pd.notna(pdf), None)
        logger.info("read %d row(s) from s3://%s/%s", len(pdf), bucket, key)
        return spark.createDataFrame(pdf)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Invalid S3 URI: {s3_uri}")
    return parsed.netloc, unquote(parsed.path.lstrip("/"))


def split_input_files(input_files: Optional[str]) -> list[str]:
    if not input_files:
        return []
    return [item.strip() for item in input_files.split(",") if item.strip()]


def categorize_input_file(s3_uri: str) -> Optional[str]:
    file_name = os.path.basename(urlparse(s3_uri).path).lower()
    if "tonnage" in file_name:
        return "tonnage"
    if "order" in file_name:
        return "orders"
    return None


def combine_dataframes(dataframes: list[DataFrame]) -> Optional[DataFrame]:
    if not dataframes:
        return None

    combined = dataframes[0]
    for dataframe in dataframes[1:]:
        combined = combined.unionByName(dataframe, allowMissingColumns=True)
    return combined


def mark_processed_files(input_files: list[str], processed_table_name: Optional[str], region: str) -> None:
    if not input_files or not processed_table_name:
        return

    import boto3

    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(processed_table_name)
    with table.batch_writer() as batch:
        for input_file in input_files:
            batch.put_item(
                Item={
                    "source_key": input_file,
                    "processed_at": pd.Timestamp.utcnow().isoformat(),
                }
            )


def resolve_column(df: DataFrame, candidates: list[str]) -> Optional[str]:
    for candidate in candidates:
        if candidate in df.columns:
            return candidate
    return None


def select_or_null(df: DataFrame, alias: str, candidates: list[str]):
    source_column = resolve_column(df, candidates)
    if source_column:
        return F.col(f"`{source_column}`").alias(alias)
    # No bronze header matched, so this silver column will be null for every
    # row. That is usually a header-name mismatch rather than genuinely absent
    # data, so name it -- a silently all-null column is easy to miss.
    logger.warning("'%s' matched no bronze column (tried: %s) -- will be null for all rows", alias, candidates)
    return F.lit(None).cast("string").alias(alias)


def normalize_id_series(series: pd.Series) -> pd.Series:
    """Coerce an ID column to clean strings.

    IDs are 19-digit numeric strings, which pandas/JSON round-trips love to
    turn back into int64 or float64 (e.g. 1.2345678901234568e+18). Everything
    that compares or joins on an ID goes through here first so that
    "1234567890123456789", 1234567890123456789 and 1.23456789e18 don't end up
    as three different keys.
    """

    def _normalize(value) -> str:
        if value is None:
            return ""
        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass
        if isinstance(value, float) and float(value).is_integer():
            return str(int(value))
        text = str(value).strip()
        if text.lower() in {"nan", "none", "nat", "<na>"}:
            return ""
        if text.endswith(".0") and text[:-2].isdigit():
            return text[:-2]
        return text

    return series.map(_normalize).astype("object")


def stable_bucket(value: str, modulus: int) -> int:
    """Map a value to a bucket in [0, modulus) deterministically across runs.

    Uses hashlib rather than Python's built-in hash(), which is salted per
    process and would reshuffle every assignment on every run.
    """
    if modulus <= 0:
        raise ValueError("modulus must be positive")
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return int(digest, 16) % modulus


def assign_order_ids(tonnage_pdf: pd.DataFrame, order_id_pool: list[str]) -> pd.DataFrame:
    """Link every tonnage vessel to exactly one order_id from the orders table.

    THIS IS NOT REAL VESSEL-TO-CARGO MATCHING. None of the actual matching
    conditions (size class, weight lift, laycan/position fit, physical limits,
    gear fit, liveness) are evaluated here. It exists purely so the silver
    tonnage and orders tables can be joined on order_id for simulation and
    downstream testing until the real matching engine lands.

    Properties:
      - Every vessel that doesn't already carry an order_id gets one, as long
        as the pool is non-empty.
      - One order_id per vessel, and every row of the same vessel shares it.
      - Assignment is unique while orders last: an order is claimed by at most
        one vessel, so with more orders than vessels some orders are simply
        left unassigned (expected, and fine).
      - Deterministic: the starting pick is a stable hash of vessel_id, so the
        same vessel lands on the same order across runs given the same pool.
      - If vessels outnumber orders, uniqueness is dropped rather than leaving
        vessels unlinked: the surplus vessels reuse their hashed pick.
    """
    if tonnage_pdf is None or tonnage_pdf.empty or "vessel_id" not in tonnage_pdf.columns:
        return tonnage_pdf

    pool = sorted({value for value in normalize_id_series(pd.Series(order_id_pool, dtype="object")) if value})
    if not pool:
        logger.warning("orders pool is empty - tonnage rows will be written without an order_id")
        return tonnage_pdf

    vessels = normalize_id_series(tonnage_pdf["vessel_id"])
    if "order_id" in tonnage_pdf.columns:
        existing = normalize_id_series(tonnage_pdf["order_id"])
    else:
        existing = pd.Series([""] * len(tonnage_pdf), index=tonnage_pdf.index, dtype="object")

    # Anything already linked keeps its link, and its order counts as claimed.
    claimed = {value for value in existing if value}
    mapping: dict[str, str] = {}
    for vessel, order_id in zip(vessels, existing):
        if vessel and order_id and vessel not in mapping:
            mapping[vessel] = order_id

    pending = sorted({vessel for vessel in vessels if vessel and vessel not in mapping})
    pool_size = len(pool)

    for vessel in pending:
        start = stable_bucket(vessel, pool_size)
        chosen = pool[start]
        if len(claimed) < pool_size:
            # Probe forward from the hashed slot for the first free order.
            for offset in range(pool_size):
                candidate = pool[(start + offset) % pool_size]
                if candidate not in claimed:
                    chosen = candidate
                    break
        claimed.add(chosen)
        mapping[vessel] = chosen

    assigned = vessels.map(lambda vessel: mapping.get(vessel, ""))
    tonnage_pdf = tonnage_pdf.copy()
    tonnage_pdf["order_id"] = [
        current if current else fallback
        for current, fallback in zip(existing, assigned)
    ]

    linked = sum(1 for value in tonnage_pdf["order_id"] if value)
    logger.debug(
        "linked %d/%d tonnage rows (%d distinct vessels) to %d/%d orders",
        linked,
        len(tonnage_pdf),
        len(mapping),
        len(claimed),
        pool_size,
    )
    return tonnage_pdf


def transform_tonnage(df: DataFrame) -> DataFrame:
    selected = df.select(
        select_or_null(df, "update_date", ["Update Date", "update_date"]),
        select_or_null(df, "parent_zone", ["Parent Zone", "parent_zone"]),
        select_or_null(df, "vessel_id", ["Vessel ID", "Vessel Name", "VesselID", "vessel_id", "vessel_name"]),
        select_or_null(df, "vessel_status", ["Vessel Status", "Status", "vessel_status"]),
        select_or_null(df, "DWT", ["DWT", "DWT Summer", "dwt_summer"]),
        select_or_null(df, "commercial_status", ["Commercial Status", "commercial_status"]),
        select_or_null(df, "ship_type", ["Ship Type", "Ship Types", "ship_type"]),
        select_or_null(df, "ship_size", ["Ship Size", "Ship Sizes", "ship_size"]),
        select_or_null(df, "ballast_laden", ["Ballast/Laden", "ballast_laden"]),
        select_or_null(df, "destination", ["Destination", "destination"]),
        select_or_null(df, "open_area", ["Open Areas", "Open Area", "open_area"]),
        select_or_null(df, "eta", ["ETA", "eta"]),
        select_or_null(df, "open_date_start", ["Open Dates Start", "Open Date Start", "open_date_start"]),
        select_or_null(df, "open_date_end", ["Open Dates End", "Open Date End", "open_date_end"]),
        select_or_null(df, "first_date_received", ["First Date Received", "first_date_received"]),
        # order_id is a LINK to the orders table, never generated from tonnage
        # content. Bronze almost certainly doesn't carry one, in which case
        # this stays null here and is filled by assign_order_ids() inside
        # write_dataset(), once the orders table has been published.
        select_or_null(df, "order_id", ["Order ID", "Order Id", "OrderID", "order_id"]),
    )
    # Tonnage rows are identified by vessel_id, which is already a natural
    # per-vessel key in the source data - no generated ID column.
    return selected.withColumn("order_id", F.col("order_id").cast("string"))


def transform_orders(df: DataFrame) -> DataFrame:
    selected = df.select(
        select_or_null(df, "order_id", ["Order ID", "Order Id", "OrderID", "order_id"]),
        select_or_null(df, "date_received", ["Date Received", "date_received"]),
        select_or_null(df, "update_date", ["Update Date", "update_date"]),
        select_or_null(df, "laycan_start", ["Lay-Can Start", "Laycan Start", "laycan_start"]),
        select_or_null(df, "laycan_end", ["Lay-Can End", "Laycan End", "laycan_end"]),
        select_or_null(df, "load_port", ["Load / Deli", "Load", "Load Port", "load_port"]),
        select_or_null(df, "discharge_port", ["Disc / Redel", "Discharge", "Discharge Port", "discharge_port"]),
        select_or_null(df, "cargo_type", ["Cargo Types", "Cargo Type", "cargo_type"]),
        select_or_null(df, "cargo_description", ["Cargo Desc.", "Cargo Description", "cargo_description"]),
        select_or_null(df, "load_zone", ["Load Parent Zone", "Load Zone", "load_zone"]),
        select_or_null(df, "discharge_parent_zone", ["Disc Parent Zone", "Discharge Parent Zone", "discharge_parent_zone"]),
        select_or_null(df, "cargo_weight_min", ["Cargo Weight Min", "Cargo Weight Min.1", "cargo_weight_min"]),
        select_or_null(df, "cargo_weight_max", ["Cargo Weight Max", "Cargo Weight Max.1", "cargo_weight_max"]),
        select_or_null(df, "assigned", ["Assigned", "Assigned (T/F)", "assigned"]),
        select_or_null(df, "assigned_vessel_name", ["Assigned Vessel Name", "Assigned Vessel", "assigned_vessel_name"]),
    )
    return with_stable_order_id(selected)


def with_stable_order_id(df: DataFrame) -> DataFrame:
    # Deliberately excludes "assigned" / "assigned_vessel_name": those fields
    # change *after* an order is created (e.g. a cargo gets fixed to a vessel).
    # If they were part of the hash, that update would mint a brand-new
    # order_id instead of updating the existing record on merge.
    key_columns = [
        "laycan_start",
        "laycan_end",
        "load_port",
        "discharge_port",
        "cargo_type",
        "cargo_description",
        "load_zone",
        "discharge_parent_zone",
        "cargo_weight_min",
        "cargo_weight_max",
    ]

    # Take the first 18 digits, not 19. Postgres/Supabase int8 (bigint) tops out
    # at 9223372036854775807, and a 19-digit value can exceed that -- with the
    # previous 19-digit form, 153 of 1864 real order_ids overflowed. 18 digits
    # caps the value at 999999999999999999, comfortably inside int8.
    #
    # The cast to long and back to string produces the canonical integer form,
    # dropping any leading zeros so the value here matches what Supabase stores
    # once the column is typed int8. The coalesce is a guard for the
    # (astronomically unlikely) digest containing no digits at all.
    #
    # cargo_weight_min/max arrive from pandas as NaN when the Excel cell is
    # empty, and NaN is NOT NULL -- coalesce() lets it through, so the payload
    # picks up the literal string "NaN". Whether an empty cell reaches Spark as
    # NaN or as NULL differs between local PySpark and Glue, which made the same
    # 50 orders hash differently in the two environments. Normalise NaN to NULL
    # first so both render as "".
    numeric_key_columns = {"cargo_weight_min", "cargo_weight_max"}

    def key_part(column: str):
        col = F.col(column)
        if column in numeric_key_columns:
            col = F.when(F.isnan(col.cast("double")), F.lit(None)).otherwise(col)
        return F.coalesce(col.cast("string"), F.lit(""))

    hash_expr = F.coalesce(
        F.substring(
            F.regexp_replace(
                F.sha2(
                    F.concat_ws("||", *[key_part(column) for column in key_columns]),
                    256,
                ),
                "[^0-9]",
                "",
            ),
            1,
            18,
        ).cast("long").cast("string"),
        F.lit("0"),
    )

    return df.withColumn(
        "order_id",
        F.when(F.coalesce(F.col("order_id").cast("string"), F.lit("")) != "", F.col("order_id").cast("string")).otherwise(hash_expr),
    )


def spark_type_to_glue_type(data_type) -> str:
    type_name = str(data_type)
    if "String" in type_name:
        return "string"
    if "Timestamp" in type_name:
        return "timestamp"
    if "Date" in type_name:
        return "date"
    if "Integer" in type_name or "Long" in type_name:
        return "bigint"
    if "Double" in type_name or "Float" in type_name or "Decimal" in type_name:
        return "double"
    if "Boolean" in type_name:
        return "boolean"
    return "string"


def create_or_update_glue_database(glue_client, database_name: str):
    try:
        glue_client.get_database(Name=database_name)
    except Exception:
        glue_client.create_database(DatabaseInput={"Name": database_name})


def create_or_update_glue_table(glue_client, database_name: str, table_name: str, output_path: str, df: DataFrame):
    create_or_update_glue_database(glue_client, database_name)

    columns = [
        {"Name": field.name, "Type": spark_type_to_glue_type(field.dataType)}
        for field in df.schema.fields
    ]
    table_input = {
        "Name": table_name,
        "StorageDescriptor": {
            "Columns": columns,
            "Location": output_path,
            "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
            "OutputFormat": "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
            "SerdeInfo": {
                "SerializationLibrary": "org.openx.data.jsonserde.JsonSerDe",
                "Parameters": {"serialization.format": "1"},
            },
            "Compressed": False,
            "NumberOfBuckets": -1,
            "BucketColumns": [],
            "SortColumns": [],
            "StoredAsSubDirectories": False,
        },
        "TableType": "EXTERNAL_TABLE",
        "Parameters": {
            "classification": "json",
            "typeOfData": "file",
        },
        "PartitionKeys": [],
    }

    try:
        glue_client.get_table(DatabaseName=database_name, Name=table_name)
        glue_client.update_table(DatabaseName=database_name, TableInput=table_input)
    except Exception:
        glue_client.create_table(DatabaseName=database_name, TableInput=table_input)


def read_existing_json(s3_client, bucket: str, key: str) -> Optional[pd.DataFrame]:
    """Read an existing newline-delimited JSON object from S3, if it exists."""
    try:
        obj = s3_client.get_object(Bucket=bucket, Key=key)
    except s3_client.exceptions.NoSuchKey:
        return None
    except Exception as exc:
        error_code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        if error_code in {"NoSuchKey", "404"}:
            return None
        raise

    body = obj["Body"].read().decode("utf-8")
    if not body.strip():
        return None

    # Force the ID columns to stay strings: pandas will happily re-parse a
    # 19-digit ID as int64 or (past int64 range) as a lossy float.
    try:
        return pd.read_json(
            io.StringIO(body),
            lines=True,
            dtype={"order_id": "string", "vessel_id": "string"},
        )
    except (TypeError, ValueError):
        return pd.read_json(io.StringIO(body), lines=True)


def write_dataset(
    glue_context,
    df: DataFrame,
    output_path: str,
    database_name: str,
    table_name: str,
    region: str,
    order_id_pool: Optional[list[str]] = None,
) -> pd.DataFrame:
    """Merge this batch into the published dataset and return the merged frame.

    Returns the final merged pandas DataFrame so main() can feed the published
    orders table's order_id values back in as the pool for tonnage.
    """
    import boto3

    logger.info("writing dataset to %s (table=%s)", output_path, table_name)

    parsed = urlparse(output_path)
    bucket = parsed.netloc
    prefix = parsed.path.lstrip("/")

    is_tonnage = table_name.endswith("tonnage_silver")

    if is_tonnage:
        object_key = f"{prefix.rstrip('/')}/tonnage.json" if prefix else "tonnage.json"
        # vessel_id is the natural per-vessel key in the source data. Note this
        # collapses the table to one current row per vessel; to keep the daily
        # snapshots instead, use ["vessel_id", "update_date"] - assign_order_ids()
        # works either way, since it assigns per vessel, not per row.
        dedup_key_columns = ["vessel_id", "open_date_start", "open_date_end", "first_date_received"]
    elif table_name.endswith("orders_silver"):
        object_key = f"{prefix.rstrip('/')}/orders.json" if prefix else "orders.json"
        dedup_key_columns = ["order_id"]
    else:
        object_key = f"{prefix.rstrip('/')}/data.json" if prefix else "data.json"
        dedup_key_columns = None

    s3_client = boto3.client("s3", region_name=region)
    new_pdf = df.toPandas()

    existing_pdf = read_existing_json(s3_client, bucket, object_key)
    if existing_pdf is not None and not existing_pdf.empty:
        combined_pdf = pd.concat([existing_pdf, new_pdf], ignore_index=True, sort=False)
    else:
        combined_pdf = new_pdf

    # The published file may still carry columns from an older version of the
    # silver schema (e.g. record_id). Concatenating resurrects them as an
    # all-null column, so prune the merged frame back to the current schema.
    # This also fixes column ordering in the output JSON.
    expected_columns = [field.name for field in df.schema.fields]
    stale_columns = [column for column in combined_pdf.columns if column not in expected_columns]
    if stale_columns:
        logger.debug("dropping stale columns inherited from the published dataset: %s", stale_columns)
    combined_pdf = combined_pdf[[column for column in expected_columns if column in combined_pdf.columns]]

    for id_column in ("vessel_id", "order_id"):
        if id_column in combined_pdf.columns:
            combined_pdf[id_column] = normalize_id_series(combined_pdf[id_column])

    if dedup_key_columns and all(col in combined_pdf.columns for col in dedup_key_columns):
        # keep="last" -> when the same record appears in both the existing
        # file and the new batch, the new batch's version wins (an update).
        combined_pdf = combined_pdf.drop_duplicates(subset=dedup_key_columns, keep="last")

    if is_tonnage:
        # Done here, after the merge, in plain pandas: the whole published set
        # of vessels is assigned in one pass (so uniqueness holds globally),
        # and there's no pandas -> Spark -> pandas round-trip to trip over
        # Arrow's schema inference on a freshly added string column.
        combined_pdf = assign_order_ids(combined_pdf, order_id_pool or [])

    with tempfile.TemporaryDirectory() as tmp_dir:
        local_json_path = os.path.join(tmp_dir, "data.json")
        json_payload = combined_pdf.to_json(orient="records", lines=True, date_format="iso")
        with open(local_json_path, "w", encoding="utf-8") as handle:
            handle.write(json_payload)
        s3_client.upload_file(local_json_path, bucket, object_key)

    # Use the original DataFrame's schema to maintain type consistency
    glue_client = boto3.client("glue", region_name=region)
    create_or_update_glue_table(glue_client, database_name, table_name, output_path, df)

    logger.info("wrote %d row(s) to s3://%s/%s (table=%s)", len(combined_pdf), bucket, object_key, table_name)
    return combined_pdf


def main() -> None:
    start_time = time.monotonic()
    args = parse_args()

    args.JOB_NAME = args.JOB_NAME or get_env_var("JOB_NAME", default="smu-glue-transform")
    logger.info("glue_transform run starting (JOB_NAME=%s)", args.JOB_NAME)
    args.input_files = args.input_files or get_env_var("INPUT_FILES")
    args.source_tonnage_s3 = args.source_tonnage_s3 or resolve_default_s3_uri(("S3_bucket_bronze", "S3_BUCKET_BRONZE"), "tonnage/SMU Tonnage data 2025.xlsx") or "s3://bronze-ocean-layer/tonnage/SMU Tonnage data 2025.xlsx"
    args.source_orders_s3 = args.source_orders_s3 or resolve_default_s3_uri(("S3_bucket_bronze", "S3_BUCKET_BRONZE"), "orders/SMU Order data 2025.xlsx") or "s3://bronze-ocean-layer/orders/SMU Order data 2025.xlsx"
    args.silver_s3_prefix = args.silver_s3_prefix or resolve_default_s3_prefix(("S3_bucket_silver", "S3_BUCKET_SILVER"))
    args.glue_database = args.glue_database or get_env_var("GLUE_DATABASE", default="silver_db")
    args.glue_tonnage_table = args.glue_tonnage_table or get_env_var("GLUE_TONNAGE_TABLE", default="smu_tonnage_silver")
    args.glue_orders_table = args.glue_orders_table or get_env_var("GLUE_ORDERS_TABLE", default="smu_orders_silver")
    args.processed_table_name = args.processed_table_name or get_env_var("PROCESSED_TABLE_NAME")
    args.region = args.region or get_env_var("AWS_DEFAULT_REGION", "AWS_REGION", default="us-east-1")
    args.job_bookmark_option = args.job_bookmark_option or get_env_var("JOB_BOOKMARK_OPTION", default="job-bookmark-enable")

    spark = get_spark_session()
    glue_context = get_glue_context(spark)

    input_files = split_input_files(args.input_files)
    if input_files:
        tonnage_frames: list[DataFrame] = []
        orders_frames: list[DataFrame] = []

        for input_file in input_files:
            category = categorize_input_file(input_file)
            if category == "tonnage":
                tonnage_frames.append(transform_tonnage(read_excel(spark, input_file, sheet_name=args.tonnage_sheet_name)))
            elif category == "orders":
                orders_frames.append(transform_orders(read_excel(spark, input_file, sheet_name=args.orders_sheet_name)))

        tonnage_silver = combine_dataframes(tonnage_frames)
        orders_silver = combine_dataframes(orders_frames)
    else:
        tonnage_df = read_excel(spark, args.source_tonnage_s3, sheet_name=args.tonnage_sheet_name)
        orders_df = read_excel(spark, args.source_orders_s3, sheet_name=args.orders_sheet_name)

        tonnage_silver = transform_tonnage(tonnage_df)
        orders_silver = transform_orders(orders_df)

    silver_prefix = args.silver_s3_prefix.rstrip("/") + "/"
    tonnage_output_path = f"{silver_prefix}tonnage/"
    orders_output_path = f"{silver_prefix}orders/"

    # Orders go first: the published orders table is the pool of valid
    # order_id values that tonnage rows are then linked to.
    order_id_pool: list[str] = []
    if orders_silver is not None:
        orders_merged = write_dataset(
            glue_context,
            orders_silver,
            orders_output_path,
            args.glue_database,
            args.glue_orders_table,
            args.region,
        )
        if orders_merged is not None and "order_id" in orders_merged.columns:
            order_id_pool = [value for value in normalize_id_series(orders_merged["order_id"]) if value]
        logger.info("%d order_id values available for tonnage linking", len(order_id_pool))
        if orders_merged is not None and not orders_merged.empty:
            preview_columns = [c for c in ("order_id", "load_port", "discharge_port", "update_date") if c in orders_merged.columns]
            logger.debug("orders preview:\n%s", orders_merged[preview_columns].head(5).to_string(index=False))

    if tonnage_silver is not None:
        tonnage_merged = write_dataset(
            glue_context,
            tonnage_silver,
            tonnage_output_path,
            args.glue_database,
            args.glue_tonnage_table,
            args.region,
            order_id_pool=order_id_pool,
        )
        # Printed from the merged pandas frame, not tonnage_silver.show(): the
        # order_id link is filled in inside write_dataset, so the Spark
        # DataFrame still shows it as null at this point.
        if tonnage_merged is not None and not tonnage_merged.empty:
            preview_columns = [c for c in ("vessel_id", "order_id", "update_date") if c in tonnage_merged.columns]
            logger.debug("tonnage preview:\n%s", tonnage_merged[preview_columns].head(5).to_string(index=False))

    if input_files:
        mark_processed_files(input_files, args.processed_table_name, args.region)

    elapsed = time.monotonic() - start_time
    logger.info("glue_transform run complete in %.1fs", elapsed)


if __name__ == "__main__":
    main()