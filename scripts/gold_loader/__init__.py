"""Silver -> gold loader: reads orders/tonnage silver JSON from S3, embeds the
free-text fields via Cohere, and upserts into Supabase's public.order_test /
public.tonnage_test tables.

Deployed as a standalone Lambda (see infra/smu_gold_loader.yaml), independent
of ai_platform -- it uses its own SUPABASE_DB_URL rather than
CHAINLIT_DATABASE_URL, since it writes gold tables ai_platform only reads.
"""
