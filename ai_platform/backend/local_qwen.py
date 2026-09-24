"""Local POC bridge. No Groq, Cohere or hosted inference calls."""

import asyncio
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Legacy Qwen identifiers remain stable for persisted chats.
PROFILES = {
    "qwen-0.5b": "0.5b",
    "qwen-1.5b": "1.5b",
    "qwen-3b": "3b",
    "llama-1b": "llama-1b",
    "llama-3b": "llama-3b",
    "llama-1b-qlora": "llama-1b-qlora",
    "llama-3b-qlora": "llama-3b-qlora",
}
SELECTABLE_PROFILES = tuple(name for name in PROFILES if not name.endswith("-qlora"))
PROFILE_LABELS = {
    **{f"qwen-{size}": f"Qwen {size.upper()}" for size in ("0.5b", "1.5b", "3b")},
    **{
        f"llama-{size}{suffix}": f"Llama 3.2 {size.upper()}" + label
        for size in ("1b", "3b")
        for suffix, label in (("", ""), ("-qlora", " (QLoRA)"))
    },
}
_process = None
_lock = asyncio.Lock()


async def answer(profile, question, history, state, as_of):
    global _process
    if profile not in PROFILES:
        raise ValueError("Unknown local model profile")
    async with _lock:
        try:
            if _process is None or _process.returncode is not None:
                python = Path(
                    os.environ.get(
                        "FREIGHT_AI_PYTHON",
                        str(ROOT / "freight-ai-poc/.venv/bin/python"),
                    )
                )
                worker = ROOT / "freight-ai-poc/scripts/chat_worker.py"
                if not python.exists():
                    raise RuntimeError(
                        "Install the local POC environment before selecting a local model."
                    )
                _process = await asyncio.create_subprocess_exec(
                    str(python),
                    str(worker),
                    cwd=ROOT,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    limit=2**22,
                )
            payload = {
                "model": PROFILES[profile],
                "question": question,
                "history": history,
                "state": state,
                "as_of": as_of.isoformat(),
            }
            _process.stdin.write((json.dumps(payload) + "\n").encode())
            await _process.stdin.drain()
            response = json.loads(
                await asyncio.wait_for(_process.stdout.readline(), timeout=300)
            )
            if not response["ok"]:
                raise RuntimeError(response["error"])
            return response["result"]
        except (OSError, ValueError):
            if _process is not None and _process.returncode is None:
                _process.kill()
                await _process.wait()
            _process = None
            raise RuntimeError(
                "The local worker stopped unexpectedly. Please retry."
            ) from None
        except (TimeoutError, asyncio.CancelledError):
            if _process is not None and _process.returncode is None:
                _process.kill()
                await _process.wait()
            _process = None
            raise


async def generate(profile, messages, schema=None):
    """Compatibility helper returning completion text."""
    return (await generate_completion(profile, messages, schema))["content"]


async def generate_completion(profile, messages, schema=None):
    """Generate through the same serialized worker, including measured usage."""
    global _process
    if profile not in PROFILES:
        raise ValueError("Unknown local model profile")
    async with _lock:
        try:
            if _process is None or _process.returncode is not None:
                python = Path(
                    os.environ.get(
                        "FREIGHT_AI_PYTHON",
                        str(ROOT / "freight-ai-poc/.venv/bin/python"),
                    )
                )
                worker = ROOT / "freight-ai-poc/scripts/chat_worker.py"
                if not python.exists():
                    raise RuntimeError(
                        "Install the local POC environment before selecting a local model."
                    )
                _process = await asyncio.create_subprocess_exec(
                    str(python),
                    str(worker),
                    cwd=ROOT,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    limit=2**22,
                )
            payload = {
                "op": "generate",
                "model": PROFILES[profile],
                "messages": messages,
            }
            if schema is not None:
                payload["schema"] = schema.get("json_schema", {}).get("schema", schema)
            _process.stdin.write((json.dumps(payload) + "\n").encode())
            await _process.stdin.drain()
            response = json.loads(
                await asyncio.wait_for(_process.stdout.readline(), timeout=300)
            )
            if not response["ok"]:
                raise RuntimeError(response["error"])
            return response["result"]
        except (OSError, ValueError, TimeoutError, asyncio.CancelledError):
            if _process is not None and _process.returncode is None:
                _process.kill()
                await _process.wait()
            _process = None
            raise
