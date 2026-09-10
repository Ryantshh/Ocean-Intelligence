from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field

from .data.models import StrictModel


class ModelConfig(StrictModel):
    model_id: str = "Qwen/Qwen2.5-0.5B-Instruct"
    revision: str = "main"
    local_files_only: bool = True
    cache_dir: str | None = None
    device: Literal["auto", "cpu", "mps", "cuda"] = "auto"
    max_new_tokens: int = Field(default=512, gt=0)
    max_input_tokens: int = Field(default=4096, gt=0)
    seed: int = 483
    quantize_4bit: bool = False
    adapter_path: str | None = None


def load_config(path):
    path = Path(path).resolve()
    value = yaml.safe_load(path.read_text())
    root = path.parent.parent
    for key in ("sources", "processed", "training_data"):
        value[key] = str((root / value[key]).resolve())
    for key in ("output_dir", "checkpoint_dir"):
        value["training"][key] = str((root / value["training"][key]).resolve())
    if value["model"].get("adapter_path"):
        value["model"]["adapter_path"] = str(
            (root / value["model"]["adapter_path"]).resolve()
        )
    value["model"]["cache_dir"] = str(
        (root / value["model"].get("cache_dir", "artifacts/hf-cache/hub")).resolve()
    )
    ModelConfig(**value["model"])
    return value
