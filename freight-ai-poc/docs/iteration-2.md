# Verification and baseline comparison iteration

The POC source was recovered from GitHub Desktop stash commit cc70685. The stash
was not popped and the main application was not reverted. The original snapshot
is archived at artifacts/baselines/pre-verification.tar.gz.

## Verification

Run scripts/verify_model.py with the official Qwen repository ID and a fresh report
path. The script resolves the revision to a commit, downloads JSON/tokenizer and
safetensors assets, and compares every selected file with official Git/LFS metadata.
LFS assets use SHA-256; ordinary Git objects use Git's SHA-1 blob algorithm.
This establishes consistency with HTTPS-served Hub metadata, not a publisher
signature or a malware audit. Benchmark startup recomputes these hashes.

## Offline usage

Run `sh scripts/offline.sh` from any directory. The launcher disables Hub and
Transformers network loading and applicable telemetry; model loading defaults to
local_files_only. Initial acquisition is a separate online operation. The existing
main chatbot still uses cloud services. Local inference does not establish that
all browser traffic is isolated. scripts/offline_smoke.py is intended to run under
an OS network-deny policy; its report covers model loading and inference only.

## Repeatable experiments

From freight-ai-poc, for each size 0.5B, 1.5B and 3B:

```
.venv/bin/python scripts/verify_model.py Qwen/Qwen2.5-1.5B-Instruct --output artifacts/verification/qwen-1.5b.json --cache-dir artifacts/hf-cache/hub
HF_HUB_OFFLINE=1 .venv/bin/python scripts/benchmark_verified.py artifacts/verification/qwen-1.5b.json --output artifacts/evaluation/iteration2-1.5b.json --device cpu
```

Use a new process and fresh output path for each model. The verified manifest
pins the exact revision. All models share the 18 historical cases, train-only
prompt demonstrations, seed, greedy decoding and input/output budgets. Reports
include schema validity, exact intent correctness, generation latency, elapsed
runtime including loading, and process peak RSS (not total GPU memory). CPU is
the controlled default; any device/precision variation must be reported.

The 18 historical examples are regression cases, not an untouched test set.
Do not claim new generalisation results from them. A domain expert should supply
fresh held-out multi-turn cases, ambiguity cases and paraphrases; keep them out
of training and prompt demonstrations. No new blind evaluation result is claimed.
Raw model evaluation bypasses the chatbot rules. Deterministic unit tests are
separate from model evaluation and do not establish conversational quality.

## Conversation changes

Removed named-vessel and named-port routing exceptions and the fixed 80,000/95%
branches. Queries parse location and numeric constraints; matching resolves order
references from supplied records or prior structured intent. Unspecified order or
port references clarify rather than invent a filter. Compound lookup/screening
uses selected result IDs. Predefined educational answers remain explicitly labelled.
The parser is still bounded, and arbitrary natural language relies on the model.
New regression cases include unseen quantities and nonexistent locations.

## Decision gate

No adapter is trained in this iteration. Compare base-model results first and
classify errors as schema, semantic intent, application, or missing evidence.
Only proceed to QLoRA when a specific trainable deficit is identified. CUDA
training remains unavailable unless suitable hardware is provided.

## Executed checks on 10 September 2026

- Official metadata comparison: 0.5B selected model/tokenizer files passed.
- OS network-denied model loading and generation: passed; the unconditioned DWT
  smoke answer interpreted the acronym outside shipping, so this is connectivity
  evidence only, not a quality pass.
- 0.5B historical regression: exact intent match remains 0% for all strategies;
  schema validity 5.6%, 50%, 22.2%; mean generation latency 3.82s, 3.05s, 3.03s.
  Peak process RSS approximately 3.37 GB. See iteration2-0.5b.json.resources.json.
- Larger model acquisition was initiated; benchmark reports must exist before
  any results or completed verification are claimed for those models.

### Streamlit cache-path regression fixed

A chat-input reproduction exposed model loading against the user's default Hub
cache instead of the POC cache. Config loading now resolves an explicit cache_dir
and the backend passes it to both Hugging Face loaders. Chat uses lazy model loading,
so deterministic queries do not need weights. A Streamlit AppTest submits both a
DWT-filter question and an empty-port search with model loading forbidden. Runtime
errors no longer tell users to rephrase a valid question.
