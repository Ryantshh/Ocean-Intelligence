import json
from pathlib import Path

from freight_ai.data.models import Intent

STRATEGIES = {"zero": 0, "one": 1, "few": 3}
PROMPT_DIR = Path(__file__).resolve().parents[3] / "prompts"
if not PROMPT_DIR.is_dir():
    PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"


def system_prompt():
    return (
        (PROMPT_DIR / "intent.txt").read_text()
        + "\nJSON schema:\n"
        + json.dumps(Intent.model_json_schema(), sort_keys=True)
    )


def build_messages(question, strategy="zero", demonstrations=()):
    n = STRATEGIES[strategy]
    if len(demonstrations) < n:
        raise ValueError(f"{strategy} requires {n} training-only demonstrations")
    messages = [{"role": "system", "content": system_prompt()}]
    for example in demonstrations[:n]:
        if example.get("split") != "train":
            raise ValueError("Only training examples may be prompt demonstrations")
        messages += [
            {"role": "user", "content": example["question"]},
            {
                "role": "assistant",
                "content": json.dumps(example["expected"], sort_keys=True),
            },
        ]
    return messages + [{"role": "user", "content": question}]


def extract(backend, question, strategy="zero", demonstrations=()):
    raw = backend.generate(build_messages(question, strategy, demonstrations))
    # No eval(), SQL execution, permissive repair, or regex-based extraction.
    return Intent.model_validate_json(raw), raw


def explain(backend, question, result):
    from .explanation import EvidenceSelection, render_evidence

    raw = backend.generate(
        [
            {
                "role": "system",
                "content": (PROMPT_DIR / "explain.txt").read_text()
                + "\n"
                + json.dumps(EvidenceSelection.model_json_schema()),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"question": question, "tool_result": result}, default=str
                ),
            },
        ]
    )
    return render_evidence(result, EvidenceSelection.model_validate_json(raw))
