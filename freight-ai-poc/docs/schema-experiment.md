# 1.5B few-shot prompt and constrained-decoding experiment

Executed locally on CPU using verified Qwen2.5-1.5B-Instruct revision
989aa7980e4cf806f80c7fef2b1adb7bc71aa306. No training or adapter. Same historical
18 regression examples and three training-only demonstrations. Original prompt
retained; experimental instructions live in prompts/intent_experiment.txt.

| Condition | Valid JSON | Application schema valid | Exact intent | Mean generation seconds |
|---|---:|---:|---:|---:|
| Historical baseline | 94.4% | 66.7% | 44.4% | 8.05 |
| Clearer prompt | 94.4% | 88.9% | 66.7% | 7.89 |
| Clearer prompt + constrained decoding | 100% | 94.4% | 66.7% | 11.35 |

Constrained decoding uses LM Format Enforcer 0.11.3 with a token-prefix constraint
built from Intent.model_json_schema(). Pydantic's cross-field business validators
still run after decoding. They are not all expressible in the generated JSON schema.
A syntactically valid JSON object therefore need not pass application validation.

The constrained arm chose maximum instead of minimum DWT on four cases and added
an unrequested sum aggregation. On the unsupported crane query it produced a match
without an order ID and invented a crane filter. Its definition case also inserted
an incorrect definition into the clarification field. These are semantic failures,
not resolved by JSON syntax enforcement.

Recommendation: retain the clearer prompt as the candidate for the next development
validation; do not promote constrained decoding on correctness grounds yet. The
prompt-only arm matched its exact intent score with lower observed latency. Neither
result establishes production readiness. These reused regression cases informed
prior development, so 66.7% is not unseen generalisation accuracy. Domain expert
review and a fresh protected holdout remain outstanding.

Reproduce with `HF_HUB_OFFLINE=1 .venv/bin/python scripts/schema_experiment.py`
after installing the optional experiment extra. The script refuses to overwrite its
report. Raw predictions: artifacts/evaluation/schema-experiment-1.5b.json.
The UI baseline prompt remains unchanged; this is a separately recorded experiment.
