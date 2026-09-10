"""Run under an OS outbound-network deny policy for meaningful isolation evidence."""

import json
import os
from pathlib import Path

os.environ.update(
    HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1"
)
from freight_ai.config import ModelConfig
from freight_ai.inference.backend import HFBackend

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "artifacts/verification/qwen-0.5b.json").read_text())
backend = HFBackend(
    ModelConfig(
        model_id=manifest["local_path"],
        revision=manifest["revision"],
        device="cpu",
        max_new_tokens=32,
    )
)
answer = backend.generate([{"role": "user", "content": "Explain DWT in one sentence."}])
assert answer.strip()
print(
    json.dumps(
        {
            "local_model_load_and_generation": "passed",
            "answer": answer,
            "scope": "Inference process only; browser and Streamlit network isolation not covered",
        }
    )
)
