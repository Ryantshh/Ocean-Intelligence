"""One isolated process per verified model. Historical 18 cases are regression only."""

import argparse
import json
import resource
import sys
import time
from pathlib import Path

from freight_ai.config import load_config
from freight_ai.evaluation.runner import matrix
from verify_model import check_file


def main():
    p = argparse.ArgumentParser()
    p.add_argument("manifest")
    p.add_argument("--output", required=True)
    p.add_argument("--device", default="cpu", choices=["cpu", "mps", "cuda"])
    a = p.parse_args()
    manifest = json.loads(Path(a.manifest).read_text())
    if not manifest["passed"]:
        raise ValueError("Verification did not pass")
    for name, entry in manifest["files"].items():
        if not check_file(
            Path(manifest["local_path"]) / name, entry["expected"], entry["algorithm"]
        )["passed"]:
            raise ValueError(f"Model file changed: {name}")
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "configs/default.yaml")
    cfg["model"].update(
        model_id=manifest["local_path"],
        revision=manifest["revision"],
        device=a.device,
        local_files_only=True,
    )
    start = time.perf_counter()
    report = matrix(cfg, a.output, base_only=True)
    measurements = {
        "model_id": manifest["model_id"],
        "revision": manifest["revision"],
        "elapsed_seconds_including_loading": time.perf_counter() - start,
        "peak_process_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * (1 if sys.platform == "darwin" else 1024),
        "memory_scope": "Process RSS; excludes separate GPU allocations",
        "evaluation_scope": "Historical intent regression; not unseen chatbot evaluation",
        "metrics": {k: v["metrics"] for k, v in report["runs"].items()},
    }
    Path(a.output + ".resources.json").write_text(
        json.dumps(measurements, indent=2) + "\n"
    )
    print(json.dumps(measurements, indent=2))


if __name__ == "__main__":
    main()
