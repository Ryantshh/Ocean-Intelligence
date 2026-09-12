import numpy as np
from freight_ai.inference import semantic
from freight_ai.data.models import Order, Provenance


def test_ranking_does_not_treat_missing_text_as_match(monkeypatch):
    class Encoder:
        def encode(self,texts,**kwargs):
            return np.array([[1.,0.] for _ in texts])
    monkeypatch.setattr(semantic,'encoder',lambda path:Encoder())
    rows=[Order(record_id=str(i),cargo_description=text,source=Provenance(file='x',sheet='x',row=i+1,sha256='x')) for i,text in enumerate([None,'iron ore'])]
    ranked,scores=semantic.rank(rows,{'cargo_description':'steel'},'unused',1)
    assert ranked[0].record_id=='1' and list(scores)==['1']
