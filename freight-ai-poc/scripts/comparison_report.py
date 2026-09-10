"""Summarise completed reports only; missing models stay explicitly pending."""

import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
lines = [
    "# Base-model comparison",
    "",
    "Historical 18-case intent regression. No fine-tuning. CPU; greedy decoding; shared prompts and cases.",
    "",
    "| Model | Strategy | Schema valid | Exact intent | Mean generation seconds |",
    "|---|---|---:|---:|---:|",
]
for size in ["0.5b", "1.5b", "3b"]:
    file = root / f"artifacts/evaluation/iteration2-{size}.json"
    if not file.exists():
        lines.append(f"| {size} | Pending | — | — | — |")
        continue
    report = json.loads(file.read_text())
    for strategy, result in report["runs"].items():
        m = result["metrics"]
        lines.append(
            f"| {size} | {strategy} | {m['schema_valid']:.1%} | {m['exact_match']:.1%} | {m['mean_latency_seconds']:.2f} |"
        )
lines += [
    "",
    "Conversation outputs are separate development assessments; scripted query success must not be attributed entirely to model capability. No expert-reviewed or blind quality score is claimed.",
    "",
    "No deployment recommendation is final until all requested comparisons and response reviews are complete.",
]
(root / "docs/base-model-comparison.md").write_text("\n".join(lines) + "\n")
