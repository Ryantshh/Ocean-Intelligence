"""Evaluate saved model/workflow outputs against independently labelled cases.

Usage: python -m ai_platform.recommendations.evaluate cases.json predictions.json
Cases: [{"id":"case-1","relevant":["A"],"forbidden":["B"]}]
Predictions: [{"id":"case-1","vessels":["A"],"latency_ms":120,"cost_usd":0}]
Keep case identifiers fixed when comparing model or workflow versions.
"""

import argparse
import json


def evaluate(cases, predictions):
    expected = {case["id"] for case in cases}
    by_id = {prediction["id"]: prediction for prediction in predictions}
    if (
        not cases
        or len(expected) != len(cases)
        or len(by_id) != len(predictions)
        or set(by_id) != expected
    ):
        raise ValueError("Require nonempty, unique, matching case and prediction IDs")
    rows = []
    for case in cases:
        prediction = by_id[case["id"]]
        ranked = prediction["vessels"]
        if len(ranked) != len(set(ranked)):
            raise ValueError("Duplicate predicted vessels")
        relevant, forbidden, selected = (
            set(case["relevant"]),
            set(case["forbidden"]),
            set(ranked),
        )
        if relevant & forbidden:
            raise ValueError("Relevant and forbidden labels overlap")
        hits = relevant & selected
        rows.append(
            {
                "id": case["id"],
                "precision": len(hits) / len(selected)
                if selected
                else float(not relevant),
                "recall": len(hits) / len(relevant)
                if relevant
                else float(not selected),
                "reciprocal_rank": next(
                    (1 / (i + 1) for i, v in enumerate(ranked) if v in relevant), 0
                ),
                "unsafe": bool(selected & forbidden),
                "correct_abstention": not selected if not relevant else None,
                "latency_ms": prediction.get("latency_ms"),
                "cost_usd": prediction.get("cost_usd"),
            }
        )
    positive = [row for row in rows if row["correct_abstention"] is None]
    empty = [row for row in rows if row["correct_abstention"] is not None]
    return {
        "positive_case_count": len(positive),
        "empty_case_count": len(empty),
        "mean_recall_on_positive_cases": sum(row["recall"] for row in positive)
        / len(positive)
        if positive
        else None,
        "mean_reciprocal_rank_on_positive_cases": sum(
            row["reciprocal_rank"] for row in positive
        )
        / len(positive)
        if positive
        else None,
        "abstention_accuracy": sum(row["correct_abstention"] for row in empty)
        / len(empty)
        if empty
        else None,
        "cases": rows,
        "mean_precision": sum(r["precision"] for r in rows) / len(rows),
        "mean_recall": sum(r["recall"] for r in rows) / len(rows),
        "mean_reciprocal_rank": sum(r["reciprocal_rank"] for r in rows) / len(rows),
        "unsafe_case_rate": sum(r["unsafe"] for r in rows) / len(rows),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases")
    parser.add_argument("predictions")
    args = parser.parse_args()
    with open(args.cases) as source, open(args.predictions) as output:
        print(json.dumps(evaluate(json.load(source), json.load(output)), indent=2))
