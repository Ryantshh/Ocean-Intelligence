"""Run the chatbot UAT and score every reply against an independent answer.

Run from the repository root::

    .venv/Scripts/python.exe -m uat.run_uat              # every case, 3 runs each
    .venv/Scripts/python.exe -m uat.run_uat --runs 1 --case c01 v02

Order matters and is the point of the design. The expected answers are computed
first, by ``uat.expected``, which shares no code with the agent, and written to
disk before the agent is even imported. Only then is the agent run, the same
compiled graph the chat page uses, with its forms answered from ``cases.json``.

What is recorded per run: every tool call and its arguments, the SQL the agent's
builder produced and the rows it returned (by wrapping ``fetch_rows`` in the
tools module, which changes nothing it does), every form the agent showed, the
reply, and the time taken. The scoring is in :func:`score`.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import statistics
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from uat import expected as expected_answers

UAT_DIR = Path(__file__).resolve().parent
RESULTS_DIR = UAT_DIR / "results"

MAX_FORMS = 3
"""Forms answered per run before giving up; the agent's own limit is lower."""

RATE_LIMIT_RETRIES = 3
RATE_LIMIT_WAIT_SECONDS = 30

COLUMN_NAMES = (
    "order_id", "vessel_id", "laycan_start", "laycan_end", "load_port", "discharge_port",
    "cargo_type", "cargo_description", "load_zone", "discharge_parent_zone",
    "cargo_weight_min", "cargo_weight_max", "update_date", "date_received",
    "first_date_received", "parent_zone", "vessel_status", "commercial_status",
    "ballast_laden", "open_area", "open_date_start", "open_date_end",
    "include_future", "include_history", "updated_from", "open_end_from",
    "laycan_end_from",
)
"""Raw names the reply must never show a trader."""

NUMBER_WORDS = {
    "no": 0, "none": 0, "nothing": 0, "zero": 0, "one": 1, "a single": 1, "two": 2,
    "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20,
}

recorded_searches: list[dict[str, Any]] = []
"""Every query the agent ran in the current run, in order."""


def _install_recorder() -> None:
    """Wrap the agent's ``fetch_rows`` so each query and its rows are kept."""
    from ai_platform.backend import tools as agent_tools

    original = agent_tools.fetch_rows

    async def recording_fetch_rows(sql: str, params: list[Any]) -> list[dict[str, Any]]:
        rows = await original(sql, params)
        table = "vessels" if "tonnage_test" in sql else "cargoes"
        recorded_searches.append(
            {"table": table, "sql": sql, "params": [str(p) for p in params], "rows": rows}
        )
        return rows

    agent_tools.fetch_rows = recording_fetch_rows


_ODD_CHARACTERS = str.maketrans({
    " ": " ", " ": " ", " ": " ", " ": " ",
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "​": None, "‌": None, "‍": None, "﻿": None,
})
"""Spaces and hyphens the model puts inside names, made plain, as the chat page
does before display, so "West Australia" reads as "West Australia"."""


def _plain(text: str) -> str:
    return text.translate(_ODD_CHARACTERS)


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content or "")


def _answer_form(case: dict[str, Any], form: dict[str, Any]) -> tuple[dict[str, str], bool]:
    """Fill the agent's form the way the case says a trader would.

    Returns
    -------
    tuple
        Question text to answer, and whether any question fell back to the
        agent's own first option because no rule matched.
    """
    answers: dict[str, str] = {}
    fell_back = False
    for question in form.get("questions", []):
        haystack = " ".join(
            [question.get("question", ""), question.get("header", "")]
            + [f"{o.get('label', '')} {o.get('description', '')}" for o in question.get("options", [])]
        )
        rule = next(
            (r for r in case.get("answers", []) if re.search(r["when"], haystack, re.I)),
            None,
        )
        if rule is None:
            options = question.get("options") or [{"label": ""}]
            answers[question["question"]] = options[0]["label"]
            fell_back = True
        else:
            answers[question["question"]] = rule["say"]
    return answers, fell_back


async def run_once(agent: Any, case: dict[str, Any], run_no: int, callbacks: list[Any]) -> dict[str, Any]:
    """Put one case to the agent, answering its forms, and record everything."""
    from langchain_core.messages import AIMessage
    from langgraph.types import Command

    recorded_searches.clear()
    thread_id = f"uat-{case['id']}-{run_no}-{uuid.uuid4().hex[:8]}"
    config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": callbacks,
        "metadata": {"langfuse_tags": ["uat", case["id"]], "langfuse_session_id": thread_id},
    }
    payload: Any = {"messages": [{"role": "user", "content": case["prompt"]}]}
    forms: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    reply = ""
    error = ""
    started = time.perf_counter()

    try:
        for _ in range(MAX_FORMS + 1):
            form = None
            async for update in agent.astream(payload, config, stream_mode="updates"):
                if "__interrupt__" in update:
                    form = update["__interrupt__"][0].value
                    break
                for node_update in update.values():
                    if not isinstance(node_update, dict):
                        continue
                    for message in node_update.get("messages", []) or []:
                        if not isinstance(message, AIMessage):
                            continue
                        tool_calls += [
                            {"name": call["name"], "args": call["args"]}
                            for call in message.tool_calls
                        ]
                        if not message.tool_calls and _text(message.content).strip():
                            reply = _text(message.content).strip()
            if form is None:
                break
            answers, fell_back = _answer_form(case, form)
            forms.append({"questions": form.get("questions", []), "answers": answers, "fell_back": fell_back})
            if case.get("stop_at_ask"):
                break
            payload = Command(resume=answers)
    except Exception as exc:  # recorded and scored as a failed run
        error = f"{type(exc).__name__}: {exc}"

    return {
        "case": case["id"],
        "story": case["story"],
        "run": run_no,
        "prompt": case["prompt"],
        "thread_id": thread_id,
        "seconds": round(time.perf_counter() - started, 1),
        "forms": forms,
        "tool_calls": tool_calls,
        "searches": [{k: v for k, v in s.items() if k != "rows"} | {"row_count": len(s["rows"])} for s in recorded_searches],
        "final_rows": _final_rows(),
        "all_ids": sorted(_all_ids()),
        "reply": reply,
        "error": error,
    }


def _row_id(table: str, row: dict[str, Any]) -> str:
    return str(row.get("order_id" if table == "cargoes" else "vessel_id", ""))


def _final_rows() -> dict[str, list[str]]:
    """Ids from the last search of each table: what the results panel shows."""
    final: dict[str, list[str]] = {}
    for search in recorded_searches:
        final[search["table"]] = [_row_id(search["table"], row) for row in search["rows"]]
    return final


def _all_ids() -> set[str]:
    return {_row_id(s["table"], row) for s in recorded_searches for row in s["rows"]}


COUNTED_NOUN = r"(?:\S+\s+){0,3}?(?:cargo|order|vessel|enquir|ship|stem|position|report)"
"""A count is a number followed within a few words by what was counted, so the
30 in "opening within the next 30 days" is not read as the count."""


def reported_count(reply: str) -> int | None:
    """Read the count from the reply's opening sentence."""
    opening = re.split(r"(?<=[.!?])\s|\n", reply.strip(), maxsplit=1)[0] if reply else ""
    opening = re.sub(r"\*", "", opening)
    counted = re.search(rf"\b(\d[\d,]*)\s+{COUNTED_NOUN}", opening, re.I)
    if counted:
        return int(counted.group(1).replace(",", ""))
    digits = re.search(r"\b\d[\d,]*\b", opening)
    words = re.search(r"\b(" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)) + r")\b", opening, re.I)
    if digits and (not words or digits.start() < words.start()):
        return int(digits.group().replace(",", ""))
    if words:
        return NUMBER_WORDS[words.group(1).lower()]
    return None


NAME_FIELDS = {
    "load_zone": "zones", "discharge_parent_zone": "zones", "parent_zone": "zones",
    "load_port": "port_labels", "discharge_port": "port_labels", "open_area": "port_labels",
    "cargo_type": "cargo_types", "vessel_status": "statuses",
}
"""Search fields holding a name, and which stored names it must be one of."""

REQUIRED_CHECKS = ("asked_ok", "option_ok", "names_ok", "rows_ok", "count_ok", "invented_ok", "reply_ok")


def unknown_names(run: dict[str, Any], names: dict[str, list[str]]) -> list[str]:
    """Names in the agent's last search that are stored nowhere in the data.

    A zone must equal a stored zone; a port, area, cargo type or status must
    appear inside a stored label, the way the search matches them.
    """
    searches = [c["args"] for c in run["tool_calls"] if c["name"] == "search_orders_and_tonnage"]
    if not searches:
        return []
    unknown = []
    for table_args in searches[-1].values():
        for field, value in (table_args or {}).items():
            kind = NAME_FIELDS.get(field)
            if kind is None or not value:
                continue
            for name in value if isinstance(value, list) else [value]:
                wanted = str(name).lower()
                stored = names[kind]
                found = wanted in stored if kind == "zones" else any(wanted in label for label in stored)
                if not found:
                    unknown.append(f"{field}={name}")
    return unknown


def score(
    case: dict[str, Any],
    run: dict[str, Any],
    expected: dict[str, Any] | None,
    names: dict[str, list[str]],
) -> dict[str, Any]:
    """Score one run. Each check is True, False or None (not applicable).

    asked_ok      the agent asked a question exactly when the case says it should
    option_ok     when it asked, the stored name the case expects was among the options
    names_ok      every place, cargo type and status it searched for is stored in the data
    rows_ok       the rows shown equal the expected rows (or, for 'count', their number)
    count_ok      the number in the reply's opening sentence equals the expected count
    invented_ok   every vessel or order id named in the reply was in the rows it got
    reply_ok      the reply states the assumption the case requires (reply_must_match)
    format_ok     ends with an "Assumed:" line, at most 5 bullets, no raw column names
    passed        no error, a reply (or a stop at the form), and every check in
                  REQUIRED_CHECKS that applies holds; format_ok is reported only
    """
    reply = _plain(run["reply"])
    asked = bool(run["forms"])
    expect_ask = case.get("expect_ask", False)
    checks: dict[str, Any] = {}

    checks["asked_ok"] = True if expect_ask == "either" else asked == expect_ask

    if case.get("expect_option") and asked:
        offered = [o.get("label", "") for f in run["forms"] for q in f["questions"] for o in q.get("options", [])]
        checks["option_ok"] = any(case["expect_option"].lower() == label.lower() for label in offered)
    else:
        checks["option_ok"] = None

    stopped = case.get("stop_at_ask") and asked
    if expected is None or stopped:
        checks["rows_ok"] = None
        checks["count_ok"] = None
    else:
        table = "vessels" if case["id"].startswith("v") else "cargoes"
        got = run["final_rows"].get(table, [])
        if case.get("compare") == "count":
            checks["rows_ok"] = len(got) == expected["count"]
        else:
            checks["rows_ok"] = sorted(set(got)) == sorted(set(expected["ids"])) and len(got) == len(set(got))
        run["missing"] = sorted(set(expected["ids"]) - set(got))
        run["extra"] = sorted(set(got) - set(expected["ids"]))
        run["reported_count"] = reported_count(reply)
        checks["count_ok"] = run["reported_count"] == expected["count"]

    run["unknown_names"] = unknown_names(run, names)
    checks["names_ok"] = not run["unknown_names"] if any(
        c["name"] == "search_orders_and_tonnage" for c in run["tool_calls"]
    ) else None

    # an id the user typed is not invented when the reply repeats it
    id_pattern = r"VESSEL \d{3,5}|\b\d{12,19}\b"
    known = set(run["all_ids"]) | set(re.findall(id_pattern, _plain(case["prompt"])))
    named = set(re.findall(id_pattern, reply))
    run["invented"] = sorted(named - known)
    checks["invented_ok"] = not run["invented"]

    if case.get("reply_must_match") and not stopped:
        checks["reply_ok"] = bool(re.search(case["reply_must_match"], reply, re.I))
    else:
        checks["reply_ok"] = None

    if stopped or not reply:
        checks["format_ok"] = None
    else:
        bullets = [line for line in reply.splitlines() if re.match(r"\s*([-*•]|\d+\.)\s", line)]
        raw = [name for name in COLUMN_NAMES if re.search(rf"\b{name}\b", reply)]
        run["raw_names"] = raw
        checks["format_ok"] = "Assumed:" in reply and len(bullets) <= 5 and not raw

    checks["passed"] = not run["error"] and all(
        checks[k] is not False for k in REQUIRED_CHECKS
    ) and (bool(reply) or bool(stopped))
    return checks


def _rate(values: list[Any]) -> str:
    applicable = [v for v in values if v is not None]
    if not applicable:
        return "n/a"
    return f"{sum(applicable)}/{len(applicable)} ({100 * sum(applicable) / len(applicable):.0f}%)"


def write_report(out: Path, cases: list[dict[str, Any]], runs: list[dict[str, Any]], expected: dict[str, Any], today: Any) -> None:
    by_case: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        by_case.setdefault(run["case"], []).append(run)

    case_passed = {case_id: all(r["checks"]["passed"] for r in rs) for case_id, rs in by_case.items()}
    lines = [
        f"# Chatbot UAT results — {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"Working date: {today}. Runs per case: {max(r['run'] for r in runs)}.",
        "",
        f"**Cases passed: {sum(case_passed.values())}/{len(case_passed)}** (a case passes only when every one of its runs passes). "
        f"Runs passed: {sum(r['checks']['passed'] for r in runs)}/{len(runs)}.",
        "",
        "## By user story",
        "",
        "| Story | Runs passed | Asked correctly | Right options | Real names | Rows exact | Count exact | No invention | Format |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for story in sorted({c["story"] for c in cases}):
        rs = [r for r in runs if r["story"] == story]
        if not rs:
            continue
        lines.append(
            f"| {story} | {_rate([r['checks']['passed'] for r in rs])} "
            f"| {_rate([r['checks']['asked_ok'] for r in rs])} "
            f"| {_rate([r['checks']['option_ok'] for r in rs])} "
            f"| {_rate([r['checks']['names_ok'] for r in rs])} "
            f"| {_rate([r['checks']['rows_ok'] for r in rs])} "
            f"| {_rate([r['checks']['count_ok'] for r in rs])} "
            f"| {_rate([r['checks']['invented_ok'] for r in rs])} "
            f"| {_rate([r['checks']['format_ok'] for r in rs])} |"
        )
    seconds = [r["seconds"] for r in runs if not r["error"]]
    if seconds:
        lines += ["", f"Median response time {statistics.median(seconds):.1f} s, slowest {max(seconds):.1f} s."]

    lines += ["", "## By case", "", "| Case | Prompt | Expected rows | Passed | Notes |", "|---|---|---|---|---|"]
    for case in cases:
        rs = by_case.get(case["id"], [])
        if not rs:
            continue
        exp = expected.get(case["id"])
        notes = []
        for r in rs:
            failed = [k for k, v in r["checks"].items() if v is False and k != "passed"]
            if r["error"]:
                failed.append("error")
            if failed:
                notes.append(f"run {r['run']}: " + ", ".join(failed))
        lines.append(
            f"| {case['id']} | {case['prompt']} | {exp['count'] if exp else '—'} "
            f"| {sum(r['checks']['passed'] for r in rs)}/{len(rs)} | {'; '.join(notes) or case.get('review', '')} |"
        )

    lines += ["", "## Replies", ""]
    for case in cases:
        for r in by_case.get(case["id"], []):
            lines += [f"### {case['id']} run {r['run']} — {'PASS' if r['checks']['passed'] else 'FAIL'}", "", f"> {case['prompt']}", ""]
            for form in r["forms"]:
                for q in form["questions"]:
                    offered = ", ".join(o.get("label", "") for o in q.get("options", []))
                    lines.append(f"- Asked: *{q.get('question', '')}* — offered: {offered} — answered: {form['answers'].get(q.get('question', ''), '')}")
            for call in r["tool_calls"]:
                lines.append(f"- Tool `{call['name']}`: `{json.dumps(call['args'], default=str)}`")
            if r.get("missing") or r.get("extra"):
                lines.append(f"- Missing vs expected: {r.get('missing', [])[:20]}; extra: {r.get('extra', [])[:20]}")
            if r.get("invented"):
                lines.append(f"- Named but not in its rows: {r['invented']}")
            if r.get("unknown_names"):
                lines.append(f"- Searched for names stored nowhere: {r['unknown_names']}")
            if r["error"]:
                lines.append(f"- Error: {r['error']}")
            lines += ["", r["reply"] or "_(no reply — stopped at the form)_", ""]

    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")

    with (out / "runs.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        checks = ["passed", "asked_ok", "option_ok", "names_ok", "rows_ok", "count_ok", "invented_ok", "reply_ok", "format_ok"]
        writer.writerow(["case", "story", "run", "prompt", "expected_count", "rows_returned", "reported_count", *checks, "seconds", "error"])
        for r in runs:
            exp = expected.get(r["case"])
            table = "vessels" if r["case"].startswith("v") else "cargoes"
            writer.writerow([
                r["case"], r["story"], r["run"], r["prompt"],
                exp["count"] if exp else "", len(r["final_rows"].get(table, [])) if r["final_rows"] else "",
                r.get("reported_count", ""), *[r["checks"][c] for c in checks], r["seconds"], r["error"],
            ])


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=3, help="runs per case (default 3)")
    parser.add_argument("--case", nargs="*", help="only these case ids")
    parser.add_argument("--story", nargs="*", help="only these user stories, e.g. US-1.1")
    parser.add_argument("--no-langfuse", action="store_true", help="do not send traces to Langfuse")
    args = parser.parse_args()

    cases = json.loads((UAT_DIR / "cases.json").read_text(encoding="utf-8"))["cases"]
    if args.case:
        cases = [c for c in cases if c["id"] in args.case]
    if args.story:
        cases = [c for c in cases if c["story"] in args.story]

    out = RESULTS_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)

    # 1. Expected answers, before the agent exists in this process.
    today = expected_answers.working_date()
    expected = await expected_answers.compute(cases, today)
    names = await expected_answers.stored_names()
    (out / "expected.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
    print(f"Expected answers for {len(expected)} cases written to {out / 'expected.json'}")

    # 2. The agent, exactly as the chat page compiles it.
    from ai_platform.backend.agent import agent

    _install_recorder()
    callbacks: list[Any] = []
    if not args.no_langfuse:
        from ai_platform.backend.tracing import langfuse_handler

        callbacks = [langfuse_handler]

    runs: list[dict[str, Any]] = []
    for case in cases:
        for run_no in range(1, args.runs + 1):
            for attempt in range(RATE_LIMIT_RETRIES + 1):
                run = await run_once(agent, case, run_no, callbacks)
                if "RateLimit" not in run["error"] or attempt == RATE_LIMIT_RETRIES:
                    break
                print(f"  rate limited, waiting {RATE_LIMIT_WAIT_SECONDS}s")
                await asyncio.sleep(RATE_LIMIT_WAIT_SECONDS)
            run["checks"] = score(case, run, expected.get(case["id"]), names)
            runs.append(run)
            print(f"{case['id']} run {run_no}: {'PASS' if run['checks']['passed'] else 'FAIL'} ({run['seconds']}s)")
            (out / "runs.json").write_text(json.dumps(runs, indent=2, default=str), encoding="utf-8")

    write_report(out, cases, runs, expected, today)
    print(f"Report: {out / 'summary.md'}")


if __name__ == "__main__":
    asyncio.run(main())
