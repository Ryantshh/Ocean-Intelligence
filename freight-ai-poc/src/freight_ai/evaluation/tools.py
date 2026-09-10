"""Manually specified actual-source acceptance cases; never SFT inputs."""

from datetime import date

from freight_ai.data.models import Intent
from freight_ai.matching.engine import MatchingConfig
from freight_ai.service import execute


def result_signature(result, expected):
    signature = {}
    if "total_count" in expected:
        signature["total_count"] = result.get("total_count")
    if "record_ids" in expected:
        signature["record_ids"] = sorted(
            r["record_id"] for r in result.get("records", [])
        )
    if "vessel_names" in expected:
        signature["vessel_names"] = sorted(
            r["vessel_name"] for r in result.get("records", [])
        )
    if "candidates" in expected:
        signature["candidates"] = [
            [r["vessel_name"], r["status"]] for r in result.get("candidates", [])
        ]
        signature["excluded_names"] = sorted(
            r["vessel_name"] for r in result.get("excluded", [])
        )
    return signature


def add_tool_metrics(run, rows, records, matching):
    for prediction, row in zip(run["predictions"], rows, strict=True):
        try:
            intent = Intent.model_validate_json(prediction["raw"])
            result = execute(
                intent, records, MatchingConfig(**matching), date(2026, 9, 1)
            )
            signature = result_signature(result, row["expected_result"])
            prediction.update(
                tool_result=result,
                tool_signature=signature,
                tool_answer_correct=signature == row["expected_result"]
                and prediction["exact_match"],
            )
        except ValueError as exc:
            prediction.update(tool_error=str(exc), tool_answer_correct=False)
    run["metrics"]["tool_answer_correct"] = sum(
        p["tool_answer_correct"] for p in run["predictions"]
    ) / len(rows)
    return run
