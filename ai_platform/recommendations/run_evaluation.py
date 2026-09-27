"""Run frozen cases through rules or AI review and persist reproducible results.

python -m ai_platform.recommendations.run_evaluation --mode rules --output runs/evaluation.json
Use --mode ai to call the configured provider. Labels are never sent to models.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path
from time import perf_counter

from ai_platform.recommendations.evaluate import evaluate
from ai_platform.recommendations.orchestration import run_workflow

DEFAULT_CASES = Path(__file__).with_name("evaluation_cases.json")


async def run_cases(cases, *, mode, model=None, reviewer=None):
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Require nonempty cases with unique IDs")
    predictions, runs = [], []
    for case in cases:
        started = perf_counter()
        result = await run_workflow(
            case["order"],
            case["vessels"],
            date.fromisoformat(case["as_of"]),
            mode=mode,
            model=model,
            reviewer=reviewer,
        )
        usage = [review["usage"] for review in result["reviews"] if review.get("usage")]
        prediction = {
            "id": case["id"],
            "vessels": [item["vessel_id"] for item in result["candidates"]],
            "latency_ms": round((perf_counter() - started) * 1000),
            "cost_usd": None,
            "review_status": result["review_status"],
            "prompt_tokens": sum(item.get("prompt_tokens", 0) for item in usage),
            "completion_tokens": sum(
                item.get("completion_tokens", 0) for item in usage
            ),
            "usage_complete": all(
                review.get("usage") is not None for review in result["reviews"]
            ),
        }
        predictions.append(prediction)
        runs.append({"id": case["id"], "result": result})
    groups = {}
    for entry in runs:
        result = entry["result"]
        if result.get("evidence_sha256") and result["review_status"] == "completed":
            groups.setdefault(result["evidence_sha256"], []).append(
                {
                    "id": entry["id"],
                    "vessels": [item["vessel_id"] for item in result["candidates"]],
                }
            )
    inconsistent = [
        group
        for group in groups.values()
        if len({tuple(item["vessels"]) for item in group}) > 1
    ]
    report = evaluate(cases, predictions)
    report["inconsistent_evidence_groups"] = inconsistent
    report.update(
        mode=mode,
        label_source="See each case's label_source; bundled cases are synthetic regression fixtures.",
        degraded_cases=sum(item["review_status"] == "degraded" for item in predictions),
        predictions=predictions,
        runs=runs,
        mean_latency_ms=sum(item["latency_ms"] for item in predictions)
        / len(predictions),
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--mode", choices=["rules", "ai"], default="rules")
    parser.add_argument(
        "--model", help="Override configured provider model for this evaluation"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.cases.open() as source:
        cases = json.load(source)
    report = asyncio.run(run_cases(cases, mode=args.mode, model=args.model))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in ("runs", "predictions", "cases")
            },
            indent=2,
        )
    )
    if report["degraded_cases"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
