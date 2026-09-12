import asyncio
import importlib.util
from pathlib import Path


def test_bridge_profiles_are_closed_and_no_hosted_client():
    path = Path(__file__).resolve().parents[2] / 'ai_platform/backend/local_qwen.py'
    spec = importlib.util.spec_from_file_location('bridge_test', path)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    assert set(bridge.PROFILES.values()) == {'0.5b','1.5b','3b'}
    try:
        asyncio.run(bridge.answer('untrusted','question',[],None,None))
    except ValueError:
        pass
    else:
        raise AssertionError('Unknown profile accepted')
