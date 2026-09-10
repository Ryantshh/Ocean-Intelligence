"""Development conversation assessment: inspect outputs, do not claim blind accuracy."""

import argparse
import json
import time
from datetime import date
from pathlib import Path

from freight_ai.config import ModelConfig, load_config
from freight_ai.inference.backend import HFBackend
from freight_ai.inference.chat import respond
from freight_ai.training.dataset import demonstrations

CASES = [
    ["Find ships open at Singapore."],
    ["Show orders discharging at Rotterdam."],
    ["List vessels with at least 175,000 tonnes DWT."],
    [
        "Screen the cargo from Tubarao to Qingdao.",
        "Would it fit at 90% of DWT?",
        "Now screen the Santos order.",
    ],
    ["Explain dry-bulk shipping in two sentences."],
    ["How does travelling in ballast differ from travelling laden?"],
    ["What does ETA tell me, and does it prove readiness at the loading port?"],
    ["Is there enough information to confirm a fixture?"],
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("manifest")
    p.add_argument("--output", required=True)
    a = p.parse_args()
    output = Path(a.output)
    if output.exists():
        raise ValueError("Use a fresh report path")
    manifest = json.loads(Path(a.manifest).read_text())
    from verify_model import check_file

    assert manifest["passed"]
    for name, entry in manifest["files"].items():
        assert check_file(
            Path(manifest["local_path"]) / name, entry["expected"], entry["algorithm"]
        )["passed"]
    cfg = load_config(Path(__file__).resolve().parents[1] / "configs/default.yaml")
    cfg["model"].update(
        model_id=manifest["local_path"],
        revision=manifest["revision"],
        device="cpu",
        local_files_only=True,
    )
    backend = HFBackend(ModelConfig(**cfg["model"]))
    results = []
    for questions in CASES:
        history, prior = [], None
        for question in questions:
            start = time.perf_counter()
            try:
                reply = respond(
                    backend,
                    question,
                    cfg,
                    date(2026, 9, 1),
                    history,
                    prior,
                    "few",
                    demonstrations(cfg["training_data"]),
                )
                results.append(
                    {
                        "question": question,
                        "response": reply,
                        "seconds": time.perf_counter() - start,
                    }
                )
                prior = reply.get("intent", prior)
                history.extend(
                    [
                        {"role": "user", "content": question},
                        {"role": "assistant", "content": reply["content"]},
                    ]
                )
            except (ValueError, RuntimeError, OSError, IndexError) as exc:
                results.append(
                    {
                        "question": question,
                        "error": repr(exc),
                        "seconds": time.perf_counter() - start,
                    }
                )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(
                    {
                        "scope": "Development cases, not blind or expert-reviewed; rules and model both contribute",
                        "model": manifest["model_id"],
                        "results": results,
                    },
                    indent=2,
                )
                + "\n"
            )
    print(f"{len(results)} turns recorded in {output}")


if __name__ == "__main__":
    main()
