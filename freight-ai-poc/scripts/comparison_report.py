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
    "Recommendation: 1.5B few-shot is the next experimental intent baseline. Both 1.5B and 3B achieve 8/18 exact matches; 3B offers higher schema validity at greater observed CPU cost. Neither is production-ready. See conversation-review.md for factual errors and review limitations.",
]
(root / "docs/base-model-comparison.md").write_text("\n".join(lines) + "\n")

with (root / "docs/base-model-comparison.md").open("a") as stream:
    stream.write(
        "\n## Resource measurements\n\n| Model | Peak process RSS (GiB) | Total run seconds |\n|---|---:|---:|\n"
    )
    for size in ["0.5b", "1.5b", "3b"]:
        p = root / f"artifacts/evaluation/iteration2-{size}.json.resources.json"
        if p.exists():
            r = json.loads(p.read_text())
            stream.write(
                f"| {size} | {r['peak_process_rss_bytes'] / 1024**3:.2f} | {r['elapsed_seconds_including_loading']:.1f} |\n"
            )
    stream.write(
        "\nSingle runs on the local CPU, not a throughput benchmark. RSS includes loading and verification, and does not measure minimum required memory. Verification was changed from whole-file reads to streaming between early and later runs; memory figures are observed process peaks, not strictly isolated inference memory. Timing excludes file verification but includes model loading. No claim about GPU performance or hosted-model cost.\n"
    )
