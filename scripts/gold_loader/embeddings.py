"""Cohere embed-v4.0 calls for the gold-layer free-text fields.

Only orders.py/tonnage.py DISPLAY_COLUMNS' non-filterable free-text fields are
embedded -- the ones ai_platform's tables/orders.py and tables/tonnage.py
docstrings call out as needing ranking/fuzzy matching rather than exact
filtering. Ports, zones and cargo types repeat heavily across thousands of
rows, so identical values are embedded once and fanned out, keeping API
calls proportional to distinct values rather than row count.
"""

import hashlib
import json
import urllib.request
from typing import Any

from .logging_utils import get_logger

logger = get_logger("gold_loader")

ORDERS_EMBED_FIELDS = (
    "cargo_type",
    "discharge_port",
    "cargo_description",
    "load_port",
    "discharge_parent_zone",
    "load_zone",
)

TONNAGE_EMBED_FIELDS = (
    "vessel_status",
    "destination",
    "parent_zone",
    "open_area",
)

COHERE_EMBED_URL = "https://api.cohere.com/v2/embed"
COHERE_BATCH_SIZE = 96


def source_hash(row: dict[str, Any], fields: tuple[str, ...]) -> str:
    """SHA-256 over a row's embeddable field values, in field order.

    Compared against the previously stored hash so unchanged rows can reuse
    their existing embeddings instead of re-calling Cohere on every run.
    """
    joined = "||".join(str(row.get(field) or "") for field in fields)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _embed_batch(texts: list[str], api_key: str, model: str, dimension: int) -> list[list[float]]:
    payload = json.dumps(
        {
            "model": model,
            "texts": texts,
            "input_type": "search_document",
            "embedding_types": ["float"],
            "output_dimension": dimension,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        COHERE_EMBED_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        body = json.loads(response.read().decode("utf-8"))
    return body["embeddings"]["float"]


def embed_values(values: set[str], api_key: str, model: str, dimension: int) -> dict[str, list[float]]:
    """Embed each unique, non-empty value once. Returns {value: vector}."""
    unique_values = sorted(value for value in values if value)
    if not unique_values:
        return {}
    logger.info("embedding %d unique value(s) via Cohere %s", len(unique_values), model)
    cache: dict[str, list[float]] = {}
    for start in range(0, len(unique_values), COHERE_BATCH_SIZE):
        batch = unique_values[start : start + COHERE_BATCH_SIZE]
        vectors = _embed_batch(batch, api_key, model, dimension)
        cache.update(zip(batch, vectors))
    return cache


def attach_embeddings(
    rows: list[dict[str, Any]],
    fields: tuple[str, ...],
    api_key: str,
    model: str,
    dimension: int,
) -> None:
    """Fill in "{field}_embedding" on every row, in place.

    `rows` should already be filtered down to new-or-changed rows by the
    caller (via source_hash() vs. what's stored in Postgres) -- unchanged
    rows are expected to carry their embeddings forward without a call here.
    """
    for field in fields:
        values = {str(row.get(field) or "") for row in rows}
        cache = embed_values(values, api_key, model, dimension)
        embedding_column = f"{field}_embedding"
        for row in rows:
            value = str(row.get(field) or "")
            row[embedding_column] = cache.get(value)
