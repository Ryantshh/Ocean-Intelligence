from datetime import date
from pathlib import Path
from freight_ai.config import load_config
from freight_ai.inference.chat import respond


def test_summary_is_optional_and_cannot_replace_capacity_state():
    class Backend:
        calls=0
        def generate(self,messages):
            self.calls+=1
            return 'An older conversation discussed a different capacity allowance.'
    config=load_config(Path(__file__).resolve().parents[1]/'configs/default.yaml')
    config.update(data_source='supabase',_snapshot={'orders':[],'tonnage':[]},summarize_history=True)
    backend=Backend()
    result=respond(backend,'List vessels with at least 180,000 tonnes DWT',config,date(2026,9,1),
        history=[{'role':'user','content':'Earlier question'}]*12,
        conversation_state={'cargo_fraction':0.95,'fraction_overridden':True})
    assert backend.calls==1
    assert result['conversation_state']['cargo_fraction']==0.95
    assert result['tool_result']['total_count']==0
