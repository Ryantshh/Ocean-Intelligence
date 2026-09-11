"""Paired development ablation: existing baseline, clearer prompt, constrained decode."""

import hashlib
import json
import time
from pathlib import Path

from freight_ai.config import ModelConfig, load_config
from freight_ai.data.models import Intent
from freight_ai.evaluation.runner import score_intent
from freight_ai.inference.backend import HFBackend
from freight_ai.inference.prompts import build_messages
from freight_ai.training.dataset import demonstrations, read_split, verify_dataset
from verify_model import check_file

root = Path(__file__).resolve().parents[1]
output = root / "artifacts/evaluation/schema-experiment-1.5b.json"
if output.exists():
    raise ValueError("Refusing to overwrite experiment")
manifest = json.loads((root / "artifacts/verification/qwen-1.5b.json").read_text())
for name, entry in manifest["files"].items():
    if not check_file(
        Path(manifest["local_path"]) / name, entry["expected"], entry["algorithm"]
    )["passed"]:
        raise ValueError("Model checksum mismatch")
cfg = load_config(root / "configs/qwen-1.5b.yaml")
backend = HFBackend(ModelConfig(**cfg["model"]))
rows = read_split(cfg["training_data"], "test")
demos = demonstrations(cfg["training_data"])
extra = (root / "prompts/intent_experiment.txt").read_text()
report = {
    "scope": "Historical regression development ablation, not blind evaluation",
    "model": backend.metadata,
    "dataset_manifest": verify_dataset(cfg["training_data"]),
    "extra_prompt": extra,
    "extra_prompt_sha256": hashlib.sha256(extra.encode()).hexdigest(),
    "baseline_report": "iteration2-1.5b.json",
    "runs": {},
}
for arm in ["clearer_prompt", "clearer_prompt_constrained"]:
    results = []
    for row in rows:
        messages = build_messages(row["question"], "few", demos)
        messages[0]["content"] += "\n" + extra
        start = time.perf_counter()
        raw = (
            backend.generate_json(messages, Intent.model_json_schema())
            if arm.endswith("constrained")
            else backend.generate(messages)
        )
        results.append(
            {
                "id": row["id"],
                "question": row["question"],
                "expected": row["expected"],
                "raw": raw,
                "seconds": time.perf_counter() - start,
                **score_intent(raw, row["expected"]),
            }
        )
    report["runs"][arm] = {
        "predictions": results,
        "metrics": {
            k: sum(float(r[k]) for r in results) / len(results)
            for k in ["json_valid", "schema_valid", "exact_match", "field_accuracy"]
        },
    }
    report["runs"][arm]["metrics"]["mean_seconds"] = sum(
        r["seconds"] for r in results
    ) / len(results)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(arm, report["runs"][arm]["metrics"], flush=True)
