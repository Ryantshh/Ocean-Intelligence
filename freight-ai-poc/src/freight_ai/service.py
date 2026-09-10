import json
from datetime import date
from pathlib import Path

from freight_ai.data.ingest import FILENAMES, digest, load_records
from freight_ai.inference.prompts import explain, extract
from freight_ai.matching.engine import MatchingConfig, match
from freight_ai.matching.query import as_of_records, latest_vessels, query

GLOSSARY = {
    "DWT": "Deadweight is the vessel's maximum cargo plus fuel and stores carrying capacity, in metric tonnes. It is not usable cargo capacity.",
    "laycan": "Laycan is the readiness/cancelling window for loading; overlap alone does not prove arrival feasibility.",
    "provenance": "Definitions reviewed against workspace SMU_2025_Data_Glossary.md; limited POC glossary.",
}


def current_records(config):
    manifest = json.loads((Path(config["processed"]) / "inspection.json").read_text())
    for kind, name in FILENAMES.items():
        if digest(Path(config["sources"]) / name) != manifest[kind]["sha256"]:
            raise ValueError("Source workbook changed; run preprocess before querying")
        if manifest[kind]["rejected"]:
            raise ValueError(
                "Preprocessing rejected records; review the inspection report before inference"
            )
        if (
            digest(Path(config["processed"]) / f"{kind}.jsonl")
            != manifest[kind]["processed_sha256"]
        ):
            raise ValueError("Processed record checksum mismatch; run preprocess")
    return {k: load_records(config["processed"], k) for k in FILENAMES}


def execute(intent, records, matching_config, as_of: date):
    if intent.action in {"qa", "clarify"} and (
        intent.record_id
        or intent.text_filters
        or intent.min_tonnes is not None
        or intent.max_tonnes is not None
        or intent.window_start
        or intent.window_end
        or intent.aggregation != "none"
    ):
        raise ValueError("QA/clarification cannot silently ignore query constraints")
    if intent.action == "clarify":
        return {"clarification": intent.clarification}
    if intent.action == "qa":
        return {
            "glossary": GLOSSARY,
            "limitation": "Questions beyond these facts require a domain expert.",
        }
    if intent.action == "match":
        orders = [r for r in records["orders"] if r.record_id == intent.record_id]
        if len(orders) != 1:
            raise ValueError(
                "Order snapshot ID missing or ambiguous; choose an existing order"
            )
        return match(
            orders[0], records["tonnage"], matching_config, as_of, intent.limit
        )
    selected = as_of_records(records[intent.dataset], as_of)
    conflicts = set()
    if intent.dataset == "tonnage":
        selected, conflicts = latest_vessels(selected, as_of)
    result = query(selected, intent)
    result.update(as_of=as_of.isoformat(), conflicting_vessel_reports=sorted(conflicts))
    return result


def ask(
    backend, question, config, as_of, strategy="zero", demonstrations=(), narrative=True
):
    intent, raw = extract(backend, question, strategy, demonstrations)
    result = execute(
        intent, current_records(config), MatchingConfig(**config["matching"]), as_of
    )
    response = {
        "intent": intent.model_dump(mode="json"),
        "raw_intent": raw,
        "tool_result": result,
        "explanation": None,
        "explanation_error": None,
    }
    if narrative and intent.action != "clarify":
        try:
            response["explanation"] = explain(backend, question, result)
        except (ValueError, RuntimeError) as exc:
            from freight_ai.inference.explanation import render_evidence

            response["explanation"] = render_evidence(result)
            response["explanation_error"] = str(exc)
    return response
