# Checkpoint 1A

Requested by Louis on 12 September 2026, before integration of the local Qwen
chatbot into the main application's UI.

Restore target: Git tag `checkpoint-1a`. This is the complete tracked codebase,
including the current Streamlit POC, separate main chatbot, read-only Supabase
connector, local Qwen selector, conversation history, deterministic screening,
semantic suggestions, and record tables. Last verification: 49 POC tests passed;
live historical DWT query returned 4,708 reports.

When the user says **Checkpoint 1A**, they intend to abandon subsequent integration
changes and restore this stage. Inspect the working tree first, preserve a recovery
snapshot of subsequent work, then restore this tag's tracked code and remove only
identified post-checkpoint integration additions. Do not erase unrelated user work,
credentials, model caches or Supabase data. Do not use a broad `git clean`.

Local runtime snapshot: `freight-ai-poc/artifacts/checkpoints/checkpoint-1a/`.
It preserves a consistent copy of local conversation history, the embedding model
and a dependency listing. Credentials and installed environments are not in Git.
Existing Qwen model caches and source workbooks remain in their original locations.

Next planned work (not part of this checkpoint): replace Streamlit as the primary
interface with a main-application selector for the existing hosted model and local
Qwen 0.5B, 1.5B and 3B. Preserve the POC strengths and read-only freight access.
