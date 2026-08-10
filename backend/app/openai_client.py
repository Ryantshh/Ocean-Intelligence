from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

try:
    from groq import Groq  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    Groq = None  # type: ignore

try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    OpenAI = None  # type: ignore


@lru_cache(maxsize=1)
def get_openai_client() -> Any:
    """Return a client-compatible object.

    Priority:
      - If `GROQ_API_KEY` is present, return the official Groq client
      - Otherwise fall back to the official OpenAI client (if installed).
    """
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key and Groq is not None:
        return Groq(api_key=groq_key)

    # Fallback to OpenAI
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key and OpenAI is not None:
        return OpenAI(api_key=api_key)

    raise RuntimeError("No Groq or OpenAI client available. Set GROQ_API_KEY or set OPENAI_API_KEY.")
