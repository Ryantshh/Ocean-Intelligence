"""Contract tests use doubles; these are not model-quality evaluations."""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from freight_ai.config import load_config

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("size", ["1b", "3b"])
def test_llama_uses_same_dataset_and_training_recipe(size):
    qwen = load_config(ROOT / "configs/default.yaml")
    llama = load_config(ROOT / f"configs/llama-{size}.yaml")
    assert llama["training_data"] == qwen["training_data"]
    assert len(llama["model"]["revision"]) == 40
    for key, value in qwen["training"].items():
        if key not in {"output_dir", "checkpoint_dir"}:
            assert llama["training"][key] == value
        else:
            assert llama["training"][key] != value
            assert size.upper() in llama["training"][key]


@pytest.fixture
def worker(monkeypatch, tmp_path):
    import shutil

    worker = load_module("worker_test", ROOT / "scripts/chat_worker.py")
    shutil.copytree(ROOT / "configs", tmp_path / "configs")
    (tmp_path / "artifacts/verification").mkdir(parents=True)
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setattr(
        worker, "current_records", lambda *_: pytest.fail("Raw generation queried DB")
    )
    return worker


def install_manifest(worker, size):
    config = load_config(worker.ROOT / f"configs/llama-{size}.yaml")
    local = worker.ROOT / f"weights-{size}"
    local.mkdir()
    manifest = {**config["model"], "passed": True, "local_path": str(local)}
    (worker.ROOT / f"artifacts/verification/llama-{size}.json").write_text(
        json.dumps(manifest)
    )
    return config


@pytest.mark.parametrize("size", ["1b", "3b"])
@pytest.mark.parametrize("tuned", [False, True])
def test_worker_preserves_identity_adapter_schema_and_usage(
    worker, monkeypatch, size, tuned
):
    config = install_manifest(worker, size)
    adapter = Path(config["training"]["output_dir"])
    if tuned:
        adapter.mkdir(parents=True)
        (adapter / "freight_manifest.json").write_text("{}")
    seen = []
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    messages = [{"role": "user", "content": "hello"}]

    class Backend:
        def __init__(self, model_config):
            seen.append(model_config)
            self.last_usage = {"prompt_tokens": 17, "completion_tokens": 5}

        def generate_json(self, actual_messages, actual_schema):
            assert actual_messages == messages and actual_schema == schema
            return '{"ok":true}'

    monkeypatch.setattr(worker, "HFBackend", Backend)
    profile = f"llama-{size}" + ("-qlora" if tuned else "")
    result = worker.answer(
        {"op": "generate", "model": profile, "messages": messages, "schema": schema}
    )
    assert result == {
        "content": '{"ok":true}',
        "usage": {"prompt_tokens": 17, "completion_tokens": 5},
    }
    assert seen[0].model_id == config["model"]["model_id"]
    assert seen[0].revision == config["model"]["revision"]
    assert seen[0].local_model_path.endswith(f"weights-{size}")
    assert seen[0].adapter_path == (str(adapter) if tuned else None)


def test_missing_adapter_never_falls_back_to_base(worker, monkeypatch):
    install_manifest(worker, "1b")
    monkeypatch.setattr(
        worker, "HFBackend", lambda *_: pytest.fail("Loaded base model")
    )
    with pytest.raises(ValueError, match="no trained adapter"):
        worker.answer({"op": "generate", "model": "llama-1b-qlora"})


def test_mismatched_verified_revision_rejected(worker):
    install_manifest(worker, "3b")
    path = worker.ROOT / "artifacts/verification/llama-3b.json"
    manifest = json.loads(path.read_text())
    manifest["revision"] = "wrong"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="verification differs"):
        worker.answer({"op": "generate", "model": "llama-3b"})


@pytest.mark.parametrize("failure", [asyncio.CancelledError, TimeoutError])
def test_worker_is_discarded_after_interrupted_response(monkeypatch, failure):
    bridge = load_module(
        "bridge_interrupt", ROOT.parent / "ai_platform/backend/local_qwen.py"
    )

    class Process:
        returncode = None
        killed = False

        async def drain(self):
            pass

        async def readline(self):
            raise failure()

        def write(self, _):
            pass

        def kill(self):
            self.killed = True

        async def wait(self):
            self.returncode = -9

    process = Process()
    process.stdin = process.stdout = process
    bridge._process = process
    with pytest.raises(failure):
        asyncio.run(bridge.generate_completion("llama-1b", []))
    assert process.killed and bridge._process is None


def test_checked_in_dataset_passes_training_audit():
    from freight_ai.training.dataset import verify_dataset

    manifest = verify_dataset(ROOT / "data/training")
    assert {key: value["count"] for key, value in manifest["splits"].items()} == {
        "train": 66,
        "validation": 18,
        "test": 18,
    }
