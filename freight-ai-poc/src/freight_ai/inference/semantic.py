"""Optional local embeddings. Similarity is retrieval evidence, not a business fact."""
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def encoder(path):
    from sentence_transformers import SentenceTransformer
    if not Path(path).is_dir():
        raise ValueError('A downloaded local embedding model directory is required.')
    return SentenceTransformer(path, local_files_only=True, trust_remote_code=False, device='cpu')


def rank(records, filters, model_path, limit=20):
    import numpy as np
    if not records:
        return [], {}
    model=encoder(model_path)
    scores=[]
    for field,term in filters.items():
        values=[getattr(r,field) or '' for r in records]
        unique=list(dict.fromkeys(values))
        vectors=model.encode(unique, normalize_embeddings=True, show_progress_bar=False)
        target=model.encode([term], normalize_embeddings=True, show_progress_bar=False)[0]
        mapping=dict(zip(unique, np.asarray(vectors) @ target))
        scores.append([float(mapping[v]) if v else -1.0 for v in values])
    combined=np.mean(scores,axis=0)
    ranked=sorted(zip(records,combined),key=lambda item:(-item[1],item[0].record_id))[:limit]
    return [r for r,_ in ranked], {r.record_id:float(score) for r,score in ranked}
