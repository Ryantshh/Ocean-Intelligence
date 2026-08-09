"""AWS Glue ETL job to transform Excel exports into Silver JSON datasets.

This script is designed to run in an AWS Glue 4.x/5.x Spark job and can also be
run locally with the same CLI arguments.
"""

import argparse
import io
import os
import sys
import tempfile
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
        return "s3://silver-layer-ocean/"
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
        print(f"DEBUG: Downloading s3://{bucket}/{key} to {tmp_path}")
        s3_client.download_file(bucket, key, tmp_path)
        pdf = pd.read_excel(tmp_path, sheet_name=sheet_name, engine="openpyxl")
        if isinstance(pdf, dict):
            pdf = next(iter(pdf.values()))
        pdf = pdf.where(pd.notna(pdf), None)
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
    return F.lit(None).cast("string").alias(alias)


def add_stable_id(df: DataFrame, key_columns: list[str], id_column: str) -> DataFrame:
    """Derive a deterministic ID from a set of key columns.

    Using a content-derived hash (instead of a per-run sequential number) means
    the same real-world record gets the same ID on every run, which is what
    lets write_dataset() merge/deduplicate correctly across multiple runs.
    Converts SHA256 hash to numeric string (first 19 digits).
    """
    hash_expr = F.substring(
        F.regexp_replace(
            F.sha2(
                F.concat_ws(
                    "||",
                    *[F.coalesce(F.col(c).cast("string"), F.lit("")) for c in key_columns],
                ),
                256,
            ),
            "[^0-9]",
            "",
        ),
        1,
        19,
    )
    return df.withColumn(id_column, hash_expr)


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
    )
    # Identity = vessel + the snapshot date it was reported on. Re-running the
    # same day's file twice will dedupe to one row; a new day's file for the
    # same vessel is treated as a new historical snapshot, not an update.
    return add_stable_id(selected, ["vessel_id", "update_date"], "record_id")


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

    # Create a SHA256 hash and convert to numeric string by taking first 19 digits
    hash_expr = F.substring(
        F.regexp_replace(
            F.sha2(
                F.concat_ws(
                    "||",
                    *[F.coalesce(F.col(column).cast("string"), F.lit("")) for column in key_columns],
                ),
                256,
            ),
            "[^0-9]",
            "",
        ),
        1,
        19,
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
    return pd.read_json(io.StringIO(body), lines=True)


def write_dataset(glue_context, df: DataFrame, output_path: str, database_name: str, table_name: str, region: str):
    import boto3

    parsed = urlparse(output_path)
    bucket = parsed.netloc
    prefix = parsed.path.lstrip("/")

    if table_name.endswith("tonnage_silver"):
        object_key = f"{prefix.rstrip('/')}/tonnage.json" if prefix else "tonnage.json"
        dedup_key_columns = ["record_id"]
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

    if dedup_key_columns and all(col in combined_pdf.columns for col in dedup_key_columns):
        # keep="last" -> when the same record appears in both the existing
        # file and the new batch, the new batch's version wins (an update).
        combined_pdf = combined_pdf.drop_duplicates(subset=dedup_key_columns, keep="last")

    with tempfile.TemporaryDirectory() as tmp_dir:
        local_json_path = os.path.join(tmp_dir, "data.json")
        json_payload = combined_pdf.to_json(orient="records", lines=True, date_format="iso")
        with open(local_json_path, "w", encoding="utf-8") as handle:
            handle.write(json_payload)
        s3_client.upload_file(local_json_path, bucket, object_key)

    # Use the original DataFrame's schema to maintain type consistency
    glue_client = boto3.client("glue", region_name=region)
    create_or_update_glue_table(glue_client, database_name, table_name, output_path, df)


def main() -> None:
    args = parse_args()

    args.JOB_NAME = args.JOB_NAME or get_env_var("JOB_NAME", default="smu-glue-transform")
    args.input_files = args.input_files or get_env_var("INPUT_FILES")
    args.source_tonnage_s3 = args.source_tonnage_s3 or resolve_default_s3_uri(("S3_bucket_bronze", "S3_BUCKET_BRONZE"), "tonnage/SMU Tonnage data 2025.xlsx") or "s3://bronze-layer-ocean/tonnage/SMU Tonnage data 2025.xlsx"
    args.source_orders_s3 = args.source_orders_s3 or resolve_default_s3_uri(("S3_bucket_bronze", "S3_BUCKET_BRONZE"), "orders/SMU Order data 2025.xlsx") or "s3://bronze-layer-ocean/orders/SMU Order data 2025.xlsx"
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

    if tonnage_silver is not None:
        write_dataset(
            glue_context,
            tonnage_silver,
            tonnage_output_path,
            args.glue_database,
            args.glue_tonnage_table,
            args.region,
        )
        tonnage_silver.show(5, truncate=False)

    if orders_silver is not None:
        write_dataset(
            glue_context,
            orders_silver,
            orders_output_path,
            args.glue_database,
            args.glue_orders_table,
            args.region,
        )
        orders_silver.show(5, truncate=False)

    if input_files:
        mark_processed_files(input_files, args.processed_table_name, args.region)


if __name__ == "__main__":
    main()
