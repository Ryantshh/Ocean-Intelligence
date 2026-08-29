"""Cohere embeddings for the query side of vector retrieval.

Separate from ``llm.py`` because that module is the Groq chat client: different
vendor, different key, different API.

The constants here must match ``scripts/gold_loader/embeddings.py``, which wrote
the stored vectors. ``input_type`` is the one that differs on purpose — Cohere
embeds a query and a document into the same space but not with the same
transform, and a mismatch returns confident nonsense rather than an error.
"""

from __future__ import annotations

import os

import httpx
from dotenv import load_dotenv

load_dotenv()

COHERE_EMBED_URL = "https://api.cohere.com/v2/embed"
COHERE_MODEL = "embed-v4.0"
EMBEDDING_DIMENSION = 512


async def embed_search_terms(search_terms: list[str]) -> list[list[float]]:
    """Embed search terms as query-side vectors.

    Parameters
    ----------
    search_terms : list of str
        Terms to embed.

    Returns
    -------
    list of list of float
        One ``EMBEDDING_DIMENSION``-length vector per input, in the same order.

    Raises
    ------
    RuntimeError
        If ``COHERE_API_KEY`` is unset.
    httpx.HTTPStatusError
        If Cohere rejects the request.
    """
    # every term in one call: there are at most six
    api_key = os.environ.get("COHERE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("COHERE_API_KEY is not set. Check .env.")

    # input_type must stay the query-side mirror of the loader's search_document
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            COHERE_EMBED_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": COHERE_MODEL,
                "texts": search_terms,
                "input_type": "search_query",
                "embedding_types": ["float"],
                "output_dimension": EMBEDDING_DIMENSION,
            },
        )
    # a wrong model or dimension is only ever reported here, never in the vectors
    response.raise_for_status()
    return response.json()["embeddings"]["float"]
