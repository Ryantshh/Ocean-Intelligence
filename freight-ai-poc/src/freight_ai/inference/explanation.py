"""LLM selects evidence; deterministic rendering prevents invented factual prose."""

import json

from pydantic import Field

from freight_ai.data.models import StrictModel


class EvidenceSelection(StrictModel):
    record_ids: list[str] = Field(default_factory=list)
    vessel_record_ids: list[str] = Field(default_factory=list)
    glossary_keys: list[str] = Field(default_factory=list)


def render_evidence(result, selection=None):
    records = {r["record_id"]: r for r in result.get("records", [])}
    candidates = {r["vessel_record_id"]: r for r in result.get("candidates", [])}
    glossary = result.get("glossary", {})
    if selection is None:
        selection = EvidenceSelection(
            record_ids=list(records),
            vessel_record_ids=list(candidates),
            glossary_keys=[k for k in glossary if k != "provenance"],
        )
    if (
        set(selection.record_ids) - records.keys()
        or set(selection.vessel_record_ids) - candidates.keys()
        or set(selection.glossary_keys) - glossary.keys()
    ):
        raise ValueError(
            "Model explanation referenced evidence absent from tool results"
        )
    if (records or candidates or glossary) and not any(selection.model_dump().values()):
        raise ValueError("Model selected no evidence from nonempty tool results")
    lines = []
    if "total_count" in result:
        lines.append(
            f"The deterministic query found {result['total_count']} record(s)."
        )
    if "candidate_count" in result:
        lines.append(
            f"Screening found {result['candidate_count']} candidate(s); {len(result['excluded'])} vessel(s) were excluded. No confirmed matches."
        )
    for key in dict.fromkeys(selection.record_ids):
        row = records[key]
        if "cargo_weight_min" in row:
            lines.append(
                f"Order {key}: {row['load_port'] or 'unknown load port'} → {row['discharge_port'] or 'unknown discharge port'}; cargo type: {row['cargo_type'] or 'unknown'}; offered quantity: {row['cargo_weight_min'] if row['cargo_weight_min'] is not None else 'unknown'}–{row['cargo_weight_max'] if row['cargo_weight_max'] is not None else 'unknown'} metric tonnes; laycan: {row['laycan_start'] or 'unknown'} to {row['laycan_end'] or 'unknown'}."
            )
        else:
            lines.append(
                f"Vessel {row['vessel_name']} ({key}): DWT {row['dwt'] if row['dwt'] is not None else 'unknown'} metric tonnes (includes fuel and stores); open area: {row['open_area'] or 'unknown'}; commercial status: {row['commercial_status'] or 'unknown'}."
            )
    for key in dict.fromkeys(selection.vessel_record_ids):
        row = candidates[key]
        lines.append(
            f"{row['vessel_name']} ({key}): {row['status']}; score {row['score']}. Unresolved checks: {', '.join(row['unknowns']) or 'none in the configured screening checks'}."
        )
    for row in result.get("excluded", []):
        lines.append(f"Excluded {row['vessel_name']}: {', '.join(row['exclusions'])}.")
    for key in dict.fromkeys(selection.glossary_keys):
        lines.append(f"{key}: {glossary[key]}")
    if result.get("aggregation") is not None:
        lines.append(
            "Deterministic aggregation: "
            + json.dumps(result["aggregation"], sort_keys=True)
        )
    lines.extend(result.get("limitations", []))
    return "\n\n".join(lines) or "No records or candidates were returned."
