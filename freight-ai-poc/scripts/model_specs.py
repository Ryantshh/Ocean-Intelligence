"""Print exact model parameter counts and configuration for the pinned base model."""

import json
from pathlib import Path

from freight_ai.config import ModelConfig, load_config
from freight_ai.inference.backend import HFBackend

ROOT = Path(__file__).resolve().parents[1]
config = load_config(ROOT / "configs/default.yaml")
backend = HFBackend(ModelConfig(**config["model"]))
parameters = list(backend.model.parameters())
trainable = sum(p.numel() for p in parameters if p.requires_grad)
total = sum(p.numel() for p in parameters)
report = {
    "model": backend.metadata,
    "total_parameters": total,
    "total_parameters_billions": total / 1_000_000_000,
    "trainable_parameters": trainable,
    "trainable_parameters_billions": trainable / 1_000_000_000,
    "dtype_by_parameter": sorted({str(p.dtype) for p in parameters}),
    "architecture": backend.model.config.to_dict(),
}
(ROOT / "artifacts/evaluation/model-specs.json").write_text(
    json.dumps(report, indent=2, default=str) + "\n"
)
print(json.dumps({k: report[k] for k in report if k != "architecture"}, indent=2))
