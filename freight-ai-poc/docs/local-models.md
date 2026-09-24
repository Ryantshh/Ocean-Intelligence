# Local Qwen and Llama models

The main Chainlit app offers the existing Qwen 0.5B, 1.5B and 3B profiles plus Llama 3.2 1B Instruct and 3B Instruct, each with separate **base** and **QLoRA** profiles. Built with Llama. Hosted Agent and Plain model profiles retain their existing providers.

All local profiles use the same `run_agent` → LangGraph → local JSON-lines worker → Transformers provider. Extraction uses the graph's JSON schema through lm-format-enforcer; retrieval, tables, working date and Chainlit persistence stay in the main application. Semantic retrieval still uses the graph's configured embedding service. A local language model does not make the entire retrieval stack offline. Existing Qwen profile IDs and saved chats remain valid.

## What is actually trained

The shared `freight_ai.training.train` implementation performs completion-only supervised QLoRA on synthetic freight **intent extraction**, not on workbook records or full conversations. It uses 66 training examples, 18 validation examples and 18 held-out test examples, with three training-only demonstrations for few-shot evaluation. The graph's extraction schema differs from the POC Intent schema: training does not by itself prove graph accuracy. Validate both after training.

Both Llama configs preserve Qwen's recipe: one epoch, learning rate 0.0002, batch size 1, accumulation 8, maximum length 2048, seed 483, LoRA rank 16/alpha 32/dropout 0.05, all linear target modules, NF4 double quantization, gradient checkpointing, completion-only loss, no packing, and epoch validation/checkpoints. BF16 is used when CUDA supports it, otherwise FP16. Test examples never enter the trainer. Each size has its own adapter/checkpoint directories. The evaluation command runs the same base/QLoRA × zero/one/few-shot matrix.

The checked-in dataset was stale against the current Intent schema and failed its own audit. It has been regenerated with the existing generator: all question IDs, questions, split memberships and counts are unchanged; prompts, canonical labels and manifest hashes now match the current schema. Use these same refreshed splits for Qwen comparisons.

The existing Qwen UI profiles select **base weights**, not trained adapters. No trained Qwen adapters were found during this implementation. Llama QLoRA profiles require both a verified base snapshot and a completed adapter with `freight_manifest.json`; they never silently substitute a base model. Adapter identity is checked against the canonical Hub model ID and revision, independently of the local snapshot path.

## Install and train

Run these commands from `freight-ai-poc`. Install the POC in its `.venv` with the model, train, experiment and dev extras (for example `uv pip install --python .venv/bin/python -e '.[model,train,experiment,dev]'`). The main app starts that interpreter; `FREIGHT_AI_PYTHON` can explicitly select another existing POC interpreter.

Request model access and authenticate with Hugging Face using an account approved for each official repository. Do not put tokens in source files. The official cards describe gated access and Transformers support:

- [Llama 3.2 1B Instruct](https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct)
- [Llama 3.2 3B Instruct](https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct)

The project's Transformers requirement (4.56 or newer, below 5) supports these models. The tokenizer's chat template and model generation configuration are reused, including model-specific end-of-turn behavior.

```sh
.venv/bin/python scripts/verify_model.py meta-llama/Llama-3.2-1B-Instruct --revision 9213176726f574b556790deb65791e0c5aa438b6 --output artifacts/verification/llama-1b.json --cache-dir artifacts/hf-cache/hub
.venv/bin/python scripts/verify_model.py meta-llama/Llama-3.2-3B-Instruct --revision 0cb88a4f764b7a12671c53f0838cd831a0843b95 --output artifacts/verification/llama-3b.json --cache-dir artifacts/hf-cache/hub
.venv/bin/freight-ai --config configs/llama-1b.yaml train
.venv/bin/freight-ai --config configs/llama-3b.yaml train
.venv/bin/freight-ai --config configs/llama-1b.yaml evaluate --adapter artifacts/adapters/Llama-3.2-1B-freight-qlora --output artifacts/evaluation/llama-1b.json
.venv/bin/freight-ai --config configs/llama-3b.yaml evaluate --adapter artifacts/adapters/Llama-3.2-3B-freight-qlora --output artifacts/evaluation/llama-3b.json
```

Training requires a CUDA GPU with sufficient available memory. Inference defaults to CPU, matching the larger Qwen configs; change `model.device` to `auto` or `cuda` on an appropriate machine. Verification downloads only the root Transformers assets, not duplicate `original/` weights, and verifies hashes against pinned Hub metadata. The worker runs offline. Run verification on the serving machine so the manifest contains a valid local path. Copy the completed adapter from a training machine to the configured output directory on the serving machine. Restart the application after replacing weights/adapters, then select the corresponding QLoRA profile in a new chat.

## Context and verification limits

Character-based prompt slicing has been removed. The backend checks the actual chat-template token count and rejects oversized prompts without silently deleting system instructions or record content. The shared graph retains its existing approximate compaction policy, now using the local 4096-input-token budget for local profiles. Very large questions, histories, or result payloads can still exceed that budget and produce a visible error. This change does not introduce automatic result sampling. Usage comes from actual input/output token counts; missing usage is not reported as zero. Cancellation/timeouts discard the worker to prevent a late response being mistaken for the next request. Context selection is request-scoped and hosted/local overhead measurements are separate.

On 2026-09-23 both official repository revisions were resolved, but file access returned `GatedRepoError`; no Hugging Face token was available. The available macOS arm64 Torch runtime reported CUDA unavailable. Both real training entrypoints passed dataset validation and stopped at the CUDA guard. **No Llama weights were run, no adapters were trained, and no model-quality scores are claimed.** Contract tests use explicitly identified doubles. Five pre-existing Streamlit tests also fail on an unchanged checkout because the recorded Qwen snapshot paths do not exist on this machine.
