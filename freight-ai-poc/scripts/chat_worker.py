"""Local JSON-lines worker for the main UI; stdout is protocol output only."""
import contextlib
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
sys.path.insert(0, str(ROOT / 'src'))

from freight_ai.config import ModelConfig, load_config
from freight_ai.inference.backend import HFBackend
from freight_ai.inference.chat import respond
from freight_ai.service import current_records
from freight_ai.training.dataset import demonstrations

loaded = None
loaded_size = None


class LazyBackend:
    def __init__(self, size, config):
        self.size, self.config = size, config

    def generate(self, messages):
        global loaded, loaded_size
        if loaded_size != self.size:
            loaded = None
            import gc
            gc.collect()
            loaded = HFBackend(ModelConfig(**self.config))
            loaded_size = self.size
        return loaded.generate(messages)


def answer(request):
    size = request['model']
    if size not in {'0.5b', '1.5b', '3b'}:
        raise ValueError('Unknown local model')
    manifest = json.loads((ROOT / f'artifacts/verification/qwen-{size}.json').read_text())
    if not manifest.get('passed') or not Path(manifest['local_path']).is_dir():
        raise ValueError('Local model is unavailable or unverified')
    config = load_config(ROOT / 'configs/default.yaml')
    config.update(data_source='supabase', query_execution='snapshot',
                  text_match='normalized', summarize_history=True, ui_pagination=True)
    config['_snapshot'] = current_records(config)
    config['automatic_semantic_path'] = str(ROOT / 'artifacts/embedding-model')
    mc = dict(config['model'], model_id=manifest['local_path'], revision=manifest['revision'],
              local_files_only=True, device='cpu', adapter_path=None)
    result = respond(LazyBackend(size, mc), request['question'], config,
                     date.fromisoformat(request['as_of']), request.get('history', []),
                     strategy='few', demonstrations=demonstrations(config['training_data']),
                     conversation_state=request.get('state'))
    # Keep deterministic retrieval available to the host UI.  The prose remains
    # model-facing, while the main app renders these records with its own table.
    return {k: result.get(k) for k in ('content', 'conversation_state', 'kind', 'intent', 'tool_result', 'basis')}


if __name__ == '__main__':
    for line in sys.stdin:
        try:
            with contextlib.redirect_stdout(sys.stderr):
                result = answer(json.loads(line))
            output = {'ok': True, 'result': result}
        except Exception:
            # Do not return database exceptions, credentials or traceback payloads to UI.
            output = {'ok': False, 'error': 'Local Qwen could not complete the request. Check model availability and the read-only data connection.'}
        print(json.dumps(output), flush=True)
