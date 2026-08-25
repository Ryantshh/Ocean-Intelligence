"""Shared Langfuse callback handle.

Split out of ``graph.py`` so ``llm.py`` can read the current node's span off
it (see ``trace_kwargs`` there) without an import cycle: ``graph`` imports
``nodes``, which imports ``llm``, so ``llm`` cannot import back from
``graph``.
"""

from __future__ import annotations

from dotenv import load_dotenv
from langfuse.langchain import CallbackHandler

load_dotenv()

langfuse_handler = CallbackHandler()
