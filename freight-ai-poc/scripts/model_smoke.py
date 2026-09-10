"""Real model smoke on validation prompts; saves raw generations, never fake scores."""

from pathlib import Path

from freight_ai.config import ModelConfig, load_config
from freight_ai.data.ingest import write_json
from freight_ai.evaluation.runner import evaluate_backend
from freight_ai.inference.backend import HFBackend
from freight_ai.training.dataset import demonstrations, read_split

ROOT = Path(__file__).resolve().parents[1]
config = load_config(ROOT / "configs/default.yaml")
backend = HFBackend(ModelConfig(**config["model"]))
rows = read_split(config["training_data"], "validation")[:1]
report = {
    "purpose": "Real base-model smoke; one validation example, not a quality benchmark",
    "model": backend.metadata,
    "runs": {
        s: evaluate_backend(backend, rows, s, demonstrations(config["training_data"]))
        for s in ("zero", "one", "few")
    },
}
write_json(ROOT / "artifacts/evaluation/model-smoke.json", report)
print({s: r["metrics"] for s, r in report["runs"].items()})
