import argparse
import json
from datetime import date
from pathlib import Path

from freight_ai.config import ModelConfig, load_config
from freight_ai.data.ingest import inspect_sources, preprocess
from freight_ai.data.models import Intent
from freight_ai.matching.engine import MatchingConfig
from freight_ai.service import ask, current_records, execute
from freight_ai.training.dataset import build_dataset, demonstrations

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "configs/default.yaml"


def main():
    parser = argparse.ArgumentParser(description="Freight AI POC")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inspect")
    sub.add_parser("preprocess")
    sub.add_parser("dataset")
    sub.add_parser("train")
    smoke = sub.add_parser("smoke")
    smoke.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 9, 1))
    query = sub.add_parser("query", help="Execute validated JSON without an LLM")
    query.add_argument("intent", help="JSON object")
    query.add_argument("--as-of", type=date.fromisoformat, required=True)
    infer = sub.add_parser("infer")
    infer.add_argument("question")
    infer.add_argument("--strategy", choices=["zero", "one", "few"], default="zero")
    infer.add_argument("--as-of", type=date.fromisoformat, required=True)
    infer.add_argument("--no-explanation", action="store_true")
    ev = sub.add_parser("evaluate")
    ev.add_argument("--output", required=True)
    ev.add_argument("--adapter")
    ev.add_argument("--base-only", action="store_true")
    ev.add_argument(
        "--tool-cases", help="Optional manually labelled source acceptance cases JSON"
    )
    ev.add_argument("--split", choices=["validation", "test"], default="test")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.command == "inspect":
        result = inspect_sources(config["sources"])
    elif args.command == "preprocess":
        result = preprocess(config["sources"], config["processed"])
    elif args.command == "dataset":
        result = build_dataset(config["training_data"])
    elif args.command == "train":
        from freight_ai.training.train import train

        result = train(config)
    elif args.command == "query":
        result = execute(
            Intent.model_validate_json(args.intent),
            current_records(config),
            MatchingConfig(**config["matching"]),
            args.as_of,
        )
    elif args.command == "infer":
        from freight_ai.inference.backend import HFBackend

        result = ask(
            HFBackend(ModelConfig(**config["model"])),
            args.question,
            config,
            args.as_of,
            args.strategy,
            demonstrations(config["training_data"]),
            narrative=not args.no_explanation,
        )
    elif args.command == "evaluate":
        from freight_ai.evaluation.runner import matrix

        result = matrix(
            config,
            args.output,
            args.adapter,
            args.base_only,
            args.split,
            tool_cases=args.tool_cases,
        )
        result = {k: v["metrics"] for k, v in result["runs"].items()}
    else:
        report = preprocess(config["sources"], config["processed"])
        records = current_records(config)
        results = [
            execute(
                Intent(action="match", record_id=r.record_id),
                records,
                MatchingConfig(**config["matching"]),
                args.as_of,
            )
            for r in records["orders"]
        ]
        result = {
            "mode": "deterministic_smoke_no_model",
            "ingestion_counts": {k: v["accepted"] for k, v in report.items()},
            "matches": results,
        }
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
