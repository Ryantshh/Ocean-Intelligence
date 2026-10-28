"""Score a finished chatbot run again under the current expected answers and rules.

Run from the repository root::

    .venv/Scripts/python.exe -m uat.rescore uat/results/<timestamp>

Calls no model. It recomputes the expected answers and stored names, scores the
recorded replies and tool calls again with :func:`uat.run_uat.score`, and writes
the report to ``<timestamp>/rescored/``. Use it after changing a definition or a
rule, to see its effect on the same answers.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from uat import expected as expected_answers
from uat.run_uat import UAT_DIR, score, write_report


async def main(run_dir: Path) -> None:
    cases = json.loads((UAT_DIR / "cases.json").read_text(encoding="utf-8"))["cases"]
    by_id = {case["id"]: case for case in cases}
    runs = [r for r in json.loads((run_dir / "runs.json").read_text(encoding="utf-8")) if r["case"] in by_id]

    today = expected_answers.working_date()
    expected = await expected_answers.compute(cases, today)
    names = await expected_answers.stored_names()
    for run in runs:
        run["checks"] = score(by_id[run["case"]], run, expected.get(run["case"]), names)

    out = run_dir / "rescored"
    out.mkdir(exist_ok=True)
    (out / "expected.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
    (out / "runs.json").write_text(json.dumps(runs, indent=2, default=str), encoding="utf-8")
    write_report(out, [c for c in cases if any(r["case"] == c["id"] for r in runs)], runs, expected, today)
    print(f"Report: {out / 'summary.md'}")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
