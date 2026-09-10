import gc
import hashlib
import json
import platform
import time
from pathlib import Path

from freight_ai.config import ModelConfig
from freight_ai.data.ingest import write_json
from freight_ai.data.models import Intent
from freight_ai.inference.backend import HFBackend
from freight_ai.inference.prompts import STRATEGIES, build_messages, system_prompt
from freight_ai.training.dataset import demonstrations, read_split, verify_dataset


def score_intent(raw, expected):
    gold = Intent.model_validate(expected).model_dump(mode="json")
    try:
        json.loads(raw)
    except (ValueError, TypeError):
        return {
            "json_valid": False,
            "schema_valid": False,
            "exact_match": False,
            "field_accuracy": 0.0,
        }
    try:
        predicted = Intent.model_validate_json(raw).model_dump(mode="json")
    except ValueError:
        return {
            "json_valid": True,
            "schema_valid": False,
            "exact_match": False,
            "field_accuracy": 0.0,
        }
    # Free-form clarification wording is reviewed separately; exact includes wording.
    return {
        "json_valid": True,
        "schema_valid": True,
        "exact_match": predicted == gold,
        "field_accuracy": sum(predicted[k] == v for k, v in gold.items()) / len(gold),
        "action_correct": predicted["action"] == gold["action"],
    }


def evaluate_backend(backend, rows, strategy, demos):
    results = []
    for row in rows:
        start = time.perf_counter()
        raw, error = "", None
        try:
            raw = backend.generate(build_messages(row["question"], strategy, demos))
        except (ValueError, RuntimeError) as exc:
            error = str(exc)
        results.append(
            {
                "id": row["id"],
                "question": row["question"],
                "expected": row["expected"],
                "raw": raw,
                "error": error,
                "latency_seconds": time.perf_counter() - start,
                **score_intent(raw, row["expected"]),
            }
        )
    n = len(results)
    if not n:
        raise ValueError("Cannot evaluate an empty test set")
    metrics = {
        key: sum(float(r[key]) for r in results) / n
        for key in ("json_valid", "schema_valid", "exact_match", "field_accuracy")
    }
    metrics.update(
        examples=n,
        mean_latency_seconds=sum(r["latency_seconds"] for r in results) / n,
        generation_errors=sum(r["error"] is not None for r in results),
    )
    return {"metrics": metrics, "predictions": results}


def matrix(
    config,
    output,
    adapter=None,
    base_only=False,
    split="test",
    backend_factory=HFBackend,
    tool_cases=None,
):
    dataset_manifest = verify_dataset(config["training_data"])
    if not base_only and not adapter:
        raise ValueError(
            "Six-cell comparison requires a trained adapter; use --base-only for baseline"
        )
    if split not in ("validation", "test"):
        raise ValueError("Evaluation must use validation or test")
    output = Path(output)
    if output.exists():
        raise ValueError("Evaluation output exists; use a fresh experiment path")
    rows, demos = (
        read_split(config["training_data"], split),
        demonstrations(config["training_data"]),
    )
    records = None
    if tool_cases:
        from freight_ai.service import current_records

        rows = json.loads(Path(tool_cases).read_text())
        records = current_records(config)
    runs = {}
    for variant in ["base"] if base_only else ["base", "qlora"]:
        model_config = ModelConfig(
            **{
                **config["model"],
                "adapter_path": adapter if variant == "qlora" else None,
            }
        )
        backend = backend_factory(model_config)
        for strategy in STRATEGIES:
            runs[f"{variant}_{strategy}"] = {
                "model": getattr(backend, "metadata", model_config.model_dump()),
                **evaluate_backend(backend, rows, strategy, demos),
            }
            if records is not None:
                from freight_ai.evaluation.tools import add_tool_metrics

                add_tool_metrics(
                    runs[f"{variant}_{strategy}"], rows, records, config["matching"]
                )
        del backend
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
        except ImportError:
            pass
    report = {
        "split": split,
        "dataset_manifest": dataset_manifest,
        "held_out_ids": [r["id"] for r in rows],
        "demonstration_ids": [r["id"] for r in demos],
        "prompt_sha256": hashlib.sha256(system_prompt().encode()).hexdigest(),
        "platform": platform.platform(),
        "tool_cases_sha256": hashlib.sha256(Path(tool_cases).read_bytes()).hexdigest()
        if tool_cases
        else None,
        "tool_as_of": "2026-09-01" if tool_cases else None,
        "matching_config": config["matching"] if tool_cases else None,
        "source_hashes": {
            k: sorted({r.source.sha256 for r in values})
            for k, values in records.items()
        }
        if records is not None
        else None,
        "runs": runs,
        "limitations": [
            "Synthetic intent benchmark only; no freight ranking relevance labels.",
            "Field accuracy includes default fields; exact-match rate is the primary strict metric.",
            "Explanation grounding requires separate expert review.",
        ],
    }
    write_json(output, report)
    return report
