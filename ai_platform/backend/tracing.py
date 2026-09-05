"""Shared Langfuse callback handle.

Attached to the agent's run config in ``cl_app``, which is what puts the agent's
model calls and tool calls into Langfuse. Reads its credentials from the
environment, and disables itself with a warning when they are absent.
"""

from __future__ import annotations

from dotenv import load_dotenv
from langfuse.langchain import CallbackHandler

load_dotenv()

langfuse_handler = CallbackHandler()
