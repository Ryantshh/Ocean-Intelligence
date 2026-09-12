# Historical 0.5B model evaluation and technical specification

Historical experiment, not the current chatbot configuration. For current manual
acceptance tests see [current-chatbot-tests.md](current-chatbot-tests.md).

## Model tested in this experiment

The tested base model is **Qwen2.5-0.5B-Instruct**, loaded from the pinned Hub
revision `7ae557604adf67be50417f59c2c2f167def9a775`. It is an approximately 0.5
billion-parameter causal language model with an instruction/chat template. The
measured parameter count is **494,032,768 parameters (0.494 billion)**, recorded in
`artifacts/evaluation/model-specs.json`. The repository does not guess from the model name.

The local test used greedy decoding (`do_sample=false`), seed `483`, up to 512 new
tokens, a 4,096-token input budget, and no remote API. The model-spec run used CPU;
the earlier cached smoke artifact used the host's MPS path. The configured 4-bit inference path is CUDA/bitsandbytes
only; the current host cannot use it.

## Training design

The current repository contains no trained adapter. The intended adapted model is
QLoRA supervised fine-tuning:

- NF4 4-bit base quantization with double quantization on CUDA.
- PEFT LoRA adapters on `all-linear` layers, rank `r=16`, alpha `32`, dropout `0.05`.
- TRL `SFTTrainer`, one epoch, learning rate `2e-4`, batch size `1`, gradient
  accumulation `8`, maximum sequence length `2,048`.
- Completion-only loss so the model learns the structured assistant output rather
  than copying the prompt.
- 66 synthetic training examples, 18 validation examples and 18 held-out test
  examples. The spreadsheet rows are never used as training examples.

The training corpus teaches freight intent routing and structured JSON behavior. It
does not teach the model to memorize the supplied records. The workbook remains an
external source queried at inference time.

## Evaluation results

The frozen held-out test set contains 18 synthetic examples shared across all prompt
strategies. Results from `artifacts/evaluation/base-test.json`:

| Base model strategy | Valid JSON | Valid schema | Exact intent match | Field accuracy | Mean latency |
|---|---:|---:|---:|---:|---:|
| Zero-shot | 11.1% | 5.6% | 0.0% | 3.0% | 3.7 s |
| One-shot | 100.0% | 50.0% | 0.0% | 35.9% | 2.9 s |
| Few-shot | 100.0% | 22.2% | 0.0% | 18.2% | 2.9 s |

These are poor results. The model should not be treated as reliable for unattended
intent extraction. Deterministic validation and the data engine remain authoritative.
The result is also not evidence that fine-tuning cannot help; no adapter was trained
on this Apple Silicon host.

The separate four-case source acceptance set in `artifacts/evaluation/base-tools.json`
gave 0/4 correct end-to-end answers for zero-shot and 2/4 for both one-shot and
few-shot. It is a small acceptance fixture, not a domain benchmark.

## How to reproduce evaluation

```bash
cd freight-ai-poc
source .venv/bin/activate
freight-ai dataset
freight-ai evaluate --base-only --output artifacts/evaluation/base-test-new.json
freight-ai evaluate --base-only --tool-cases configs/tool_cases.json --output artifacts/evaluation/base-tools-new.json
python scripts/model_specs.py
```

After a CUDA QLoRA run, evaluate the six required cells with:

```bash
freight-ai evaluate --adapter artifacts/adapters/freight-qlora --output artifacts/evaluation/six-cell.json
```

The six cells are base/QLoRA crossed with zero-, one- and few-shot prompting. They
must use the same held-out IDs, prompt files, decoding settings and evaluation code.

## Interpretation limits

The evaluation corpus is synthetic and small. It measures structured intent behavior,
not the quality of commercial freight decisions. There are no human relevance labels,
no live market feed, no route/ETA labels and no trained adapter result. Before relying
on the chatbot for operations, add expert-authored questions, adversarial follow-ups,
human ratings for answer quality and a larger held-out freight benchmark.
