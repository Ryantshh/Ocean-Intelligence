# Ocean-Intelligence-

## Daily AWS pipeline

The ETL logic lives in `scripts/glue_transform.py`. A CloudFormation template is available at `infra/smu_daily_pipeline.yaml` to wire up:

- EventBridge daily schedule
- Lambda trigger
- Glue job execution
- DynamoDB checkpoint table for processed XLSX files

### How it works

1. EventBridge triggers Lambda on a daily cron.
2. Lambda lists `.xlsx` files in the bronze bucket and skips keys already recorded in DynamoDB.
3. Lambda starts the Glue job only when there are new files.
4. The Glue job processes the input files, writes JSON to the silver bucket, and records processed keys.
5. `orders.json` uses a deterministic Spark-generated `order_id` based on order details, so reruns can update in place.

### Deploy inputs

The CloudFormation stack needs:

- `BronzeBucketName`
- `SilverBucketName`
- `GlueScriptS3Uri` pointing to `scripts/glue_transform.py` uploaded in S3
- Optional bronze/silver prefixes if your files are not at the bucket root
