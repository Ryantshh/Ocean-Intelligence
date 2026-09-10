"""Schema-informed synthetic behavior. No workbook rows enter this generator."""

import json
from pathlib import Path

from freight_ai.data.ingest import digest, write_json
from freight_ai.data.models import Intent
from freight_ai.inference.prompts import system_prompt

# Each split has independently authored phrasing families and fictitious slot values.
# No train item is generated from a validation/test item or a source record.
FAMILIES = {
    "train": [
        "List orders loading at {port}.",
        "Show tonnage with at least {weight} tonnes DWT.",
        "Summarize orders discharging at {port}.",
        "Screen vessels for order {id}.",
        "Explain DWT.",
        "Find ships with crane certificates.",
    ],
    "validation": [
        "Which cargo orders load in {port}?",
        "Retrieve vessels whose DWT is at least {weight} tonnes.",
        "Give a summary of cargo orders destined for {port}.",
        "Rank vessel candidates for order {id}.",
        "What does deadweight mean?",
        "Only show vessels with certified cranes.",
    ],
    "test": [
        "Return the cargo enquiries with load port {port}.",
        "I need the tonnage list restricted to DWT >= {weight} tonnes.",
        "Describe the orders whose discharge port is {port}.",
        "For cargo order {id}, produce a vessel screening.",
        "Define deadweight tonnage for me.",
        "Filter the fleet by crane certification.",
    ],
}


def generate_split(split):
    records = []
    offset = {"train": 100, "validation": 500, "test": 900}[split]
    for family, template in enumerate(FAMILIES[split]):
        for i in range(16 if split == "train" else 4):
            if family >= 4 and i:
                continue  # No duplicate examples to inflate metrics.
            port, weight, oid = (
                f"SYNTHETIC PORT {offset + i}",
                (offset + i) * 100,
                f"synthetic-order-{offset + i}",
            )
            expected = [
                {"action": "query", "text_filters": {"load_port": port}},
                {"action": "query", "dataset": "tonnage", "min_tonnes": weight},
                {"action": "summarize", "text_filters": {"discharge_port": port}},
                {"action": "match", "record_id": oid},
                {"action": "qa"},
                {
                    "action": "clarify",
                    "clarification": "Crane certification is not available in the current schema. Which supported constraints should I use?",
                },
            ][family]
            question = template.format(port=port, weight=weight, id=oid)
            canonical = Intent(**expected).model_dump(mode="json")
            records.append(
                {
                    "id": f"{split}-{family}-{i}",
                    "split": split,
                    "group_id": f"{split}-phrasing-{family}",
                    "origin": "synthetic_schema_behavior_v1",
                    "question": question,
                    "expected": canonical,
                    "prompt": [
                        {"role": "system", "content": system_prompt()},
                        {"role": "user", "content": question},
                    ],
                    "completion": [
                        {
                            "role": "assistant",
                            "content": json.dumps(canonical, sort_keys=True),
                        }
                    ],
                }
            )
    return records


def read_split(directory, split):
    return [
        json.loads(line)
        for line in (Path(directory) / f"{split}.jsonl").read_text().splitlines()
        if line
    ]


def audit_splits(splits):
    seen_ids, seen_questions, seen_groups = set(), set(), set()
    for split, rows in splits.items():
        groups = {r["group_id"] for r in rows}
        if not rows or seen_groups & groups:
            raise ValueError("Empty split or cross-split family leakage")
        seen_groups |= groups
        for row in rows:
            key = " ".join(row["question"].lower().split())
            if row["split"] != split or row["id"] in seen_ids or key in seen_questions:
                raise ValueError(
                    "Duplicate or incorrectly assigned training/evaluation example"
                )
            canonical = Intent.model_validate(row["expected"]).model_dump(mode="json")
            if row["prompt"] != [
                {"role": "system", "content": system_prompt()},
                {"role": "user", "content": row["question"]},
            ]:
                raise ValueError(
                    "Training prompt differs from audited question/system prompt"
                )
            if (
                len(row["completion"]) != 1
                or row["completion"][0]["role"] != "assistant"
                or json.loads(row["completion"][0]["content"]) != canonical
            ):
                raise ValueError("Training completion differs from expected intent")
            seen_ids.add(row["id"])
            seen_questions.add(key)


def build_dataset(directory):
    splits = {split: generate_split(split) for split in FAMILIES}
    audit_splits(splits)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for split, rows in splits.items():
        (directory / f"{split}.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
        )
    manifest = {
        "generator": "synthetic_schema_behavior_v1",
        "source_records_used": False,
        "limitations": "Small synthetic intent corpus; not evidence of freight expertise or generalization.",
        "splits": {
            s: {"count": len(r), "sha256": digest(directory / f"{s}.jsonl")}
            for s, r in splits.items()
        },
    }
    write_json(directory / "manifest.json", manifest)
    return manifest


def verify_dataset(directory):
    manifest = json.loads((Path(directory) / "manifest.json").read_text())
    for split, entry in manifest["splits"].items():
        if digest(Path(directory) / f"{split}.jsonl") != entry["sha256"]:
            raise ValueError(f"Dataset hash mismatch: {split}")
    audit_splits({s: read_split(directory, s) for s in FAMILIES})
    return manifest


def demonstrations(directory):
    rows = read_split(directory, "train")
    selected, groups = [], set()
    for row in rows:
        if row["group_id"] not in groups:
            selected.append(row)
            groups.add(row["group_id"])
    return selected[:3]
