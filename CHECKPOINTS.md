# Checkpoint 1B

Requested by Louis on 13 September 2026, before implementation of machine
learning features (including unusual-record detection and similar historical
enquiries). The working tree was clean at baseline commit
`4ba5d0a6c4909387d348ae3f691f7cfed84c728b` on branch `lilykong`.

Restore target: Git tag `checkpoint-1b`. The tag includes this checkpoint
instruction and the complete tracked project state before ML implementation.

When the user says **Checkpoint 1B**, they authorize abandoning subsequent
project implementation changes and returning to this stage. Inspect the working
tree, preserve a recovery snapshot, restore the tag's tracked files, and remove
only identified post-checkpoint implementation additions. Preserve unrelated
user files, credentials, ignored model caches, and installed environments; do
not use a broad `git clean`.

This is a code checkpoint, not a Supabase database or ignored-runtime snapshot.
No database changes were made to create it. Before future ML implementation
changes external data or schema, record reversible migrations and any necessary
data backups so a requested rollback can address those changes explicitly.

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
