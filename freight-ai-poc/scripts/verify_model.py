"""Download only public model assets; verify against pinned Hub Git/LFS metadata."""

import argparse
import hashlib
import json
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download


def check_file(path, expected, algorithm):
    path = Path(path)
    size = path.stat().st_size
    if algorithm not in {"sha256", "git-sha1"}:
        raise ValueError("Unsupported checksum algorithm")
    digest = hashlib.sha256() if algorithm == "sha256" else hashlib.sha1()
    if algorithm == "git-sha1":
        digest.update(f"blob {size}\0".encode())
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    return {
        "algorithm": algorithm,
        "expected": expected,
        "actual": actual,
        "passed": actual == expected,
        "bytes": size,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("model")
    p.add_argument(
        "--revision", default="main", help="Resolved once to immutable commit"
    )
    p.add_argument("--output", required=True)
    p.add_argument("--cache-dir")
    a = p.parse_args()
    output = Path(a.output)
    if output.exists():
        raise ValueError("Use a fresh output path")
    info = HfApi().model_info(a.model, revision=a.revision, files_metadata=True)
    names = [
        f.rfilename
        for f in info.siblings
        if f.rfilename.endswith((".json", ".safetensors", ".txt", ".model", ".jinja"))
    ]
    root = Path(
        snapshot_download(
            a.model, revision=info.sha, allow_patterns=names, cache_dir=a.cache_dir
        )
    )
    checks = {}
    for f in info.siblings:
        if f.rfilename not in names:
            continue
        expected = f.lfs.sha256 if f.lfs else f.blob_id
        algorithm = "sha256" if f.lfs else "git-sha1"
        checks[f.rfilename] = check_file(root / f.rfilename, expected, algorithm)
    report = {
        "model_id": a.model,
        "revision": info.sha,
        "local_path": str(root.resolve()),
        "authority": "Hugging Face repository Git/LFS metadata via HTTPS; not a publisher signature",
        "files": checks,
        "passed": bool(checks) and all(x["passed"] for x in checks.values()),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {"passed": report["passed"], "revision": info.sha, "report": str(output)}
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
