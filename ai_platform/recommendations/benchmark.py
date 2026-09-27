"""Compare the rules baseline with repeated model runs on the same labelled cases.

python -m ai_platform.recommendations.benchmark --mode rules --output runs/benchmark.json
python -m ai_platform.recommendations.benchmark --mode ai --repeats 3 --output runs/benchmark.json
--mode ai always includes the rules baseline. Each --model adds a model to compare.
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from statistics import mean

from ai_platform.backend.llm import get_model_name
from ai_platform.recommendations.run_evaluation import DEFAULT_CASES, run_cases


async def benchmark(cases, *, models, repeats, runner=run_cases):
    if not 1 <= repeats <= 20:
        raise ValueError("Repeats must be between 1 and 20")
    reports = [
        {"name": "rules", "repeat": 1, "report": await runner(cases, mode="rules")}
    ]
    for model in models:
        for repetition in range(1, repeats + 1):
            reports.append(
                {
                    "name": model,
                    "repeat": repetition,
                    "report": await runner(cases, mode="ai", model=model),
                }
            )
    summary = []
    for name in ["rules", *models]:
        matching = [entry["report"] for entry in reports if entry["name"] == name]
        ranks = {}
        for report in matching:
            for prediction in report["predictions"]:
                if prediction["review_status"] != "degraded":
                    ranks.setdefault(prediction["id"], []).append(
                        tuple(prediction["vessels"])
                    )
        comparable = [values for values in ranks.values() if len(values) > 1]
        recall = [
            report["mean_recall_on_positive_cases"]
            for report in matching
            if report["mean_recall_on_positive_cases"] is not None
        ]
        summary.append(
            {
                "name": name,
                "runs": len(matching),
                "mean_positive_recall": mean(recall) if recall else None,
                "mean_unsafe_case_rate": mean(
                    report["unsafe_case_rate"] for report in matching
                ),
                "degraded_cases": sum(report["degraded_cases"] for report in matching),
                "mean_latency_ms": mean(
                    report["mean_latency_ms"] for report in matching
                ),
                "comparable_cases": len(comparable),
                "ranking_agreement": sum(len(set(values)) == 1 for values in comparable)
                / len(comparable)
                if comparable
                else None,
            }
        )
    return {
        "dataset_sha256": hashlib.sha256(
            json.dumps(cases, sort_keys=True).encode()
        ).hexdigest(),
        "case_count": len(cases),
        "summary": summary,
        "runs": reports,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--mode", choices=["rules", "ai"], default="rules")
    parser.add_argument("--model", action="append")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--min-positive-recall", type=float, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 0 <= args.min_positive_recall <= 1:
        parser.error("--min-positive-recall must be between 0 and 1")
    cases = json.loads(args.cases.read_text())
    models = (
        list(dict.fromkeys(args.model or [get_model_name()]))
        if args.mode == "ai"
        else []
    )
    report = asyncio.run(benchmark(cases, models=models, repeats=args.repeats))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))
    if any(
        item["degraded_cases"]
        or item["mean_unsafe_case_rate"] > 0
        or (
            args.min_positive_recall > 0
            and (
                item["mean_positive_recall"] is None
                or item["mean_positive_recall"] < args.min_positive_recall
            )
        )
        for item in report["summary"]
    ):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
