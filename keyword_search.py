from __future__ import annotations

import logging
from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)


def tokenize(text: str) -> list[str]:
    """Tokenización simple por espacios y minúsculas."""
    return text.lower().split()


def bm25_search(
    query: str,
    chunks: list[dict],
    top_n: int = 5,
) -> list[dict]:
    """
    Búsqueda BM25 sobre una lista de chunks.
    Cada chunk debe tener 'text', 'chunk_id', 'score', 'metadata'.
    Retorna lista ordenada por score BM25 descendente.
    """
    if not chunks:
        return []

    corpus = [tokenize(chunk.get('text', '')) for chunk in chunks]
    bm25 = BM25Okapi(corpus)
    query_tokens = tokenize(query)
    scores = bm25.get_scores(query_tokens)

    results = []
    for i, chunk in enumerate(chunks):
        results.append({
            'chunk_id': chunk.get('chunk_id', ''),
            'text': chunk.get('text', ''),
            'score': float(scores[i]),
            'distance': 0.0,
            'metadata': chunk.get('metadata', {}),
        })

    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:top_n]


def reciprocal_rank_fusion(
    semantic_results: list[dict],
    keyword_results: list[dict],
    top_n: int = 5,
    k: int = 60,
) -> list[dict]:
    """
    Fusiona resultados semánticos y BM25 usando Reciprocal Rank Fusion (RRF).
    RRF score = 1/(k + rank)
    """
    rrf_scores: dict[str, float] = {}
    chunk_map: dict[str, dict] = {}

    for rank, result in enumerate(semantic_results):
        cid = result['chunk_id']
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
        chunk_map[cid] = result

    for rank, result in enumerate(keyword_results):
        cid = result['chunk_id']
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
        chunk_map[cid] = result

    sorted_ids = sorted(rrf_scores, key=lambda cid: rrf_scores[cid], reverse=True)

    fused = []
    for cid in sorted_ids[:top_n]:
        item = dict(chunk_map[cid])
        item['score'] = rrf_scores[cid]
        fused.append(item)

    return fused