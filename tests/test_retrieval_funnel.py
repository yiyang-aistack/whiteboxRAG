# -*- coding: utf-8 -*-
"""
Retrieval funnel attribution tests.

The funnel is the data behind the white-box answer to "which chunk hit the knowledge base,
which one did not reach the prompt, and why". These tests pin down the stage attribution
(threshold / compression / top_k) so the UI can never show a candidate as kept while the
retriever silently dropped it.

Pure logic only: no vector store, no LLM, no running service.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.retriever import HybridRetriever  # noqa: E402


def _retriever() -> HybridRetriever:
    """Build a retriever without running __init__ (no vector store / BM25 index needed)."""
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever.rerank_top_k = 10
    return retriever


def _candidate(cid: str, score: float, bm25: float = 0.0, vector: float = 0.0) -> dict:
    return {
        'id': cid,
        'score': score,
        'bm25_score': bm25,
        'vector_score': vector,
        'text': f'text of {cid}',
        'metadata': {'file_name': f'{cid}.txt', 'chunk_index': 0, 'total_chunks': 3},
    }


def test_threshold_drop_is_attributed_with_gap():
    """A candidate below the similarity threshold is reported as a threshold drop."""
    retriever = _retriever()
    merged = [_candidate('a', 0.80), _candidate('b', 0.30), _candidate('c', 0.10)]
    after_threshold = [merged[0], merged[1]]

    funnel = retriever._build_retrieval_funnel(
        merged=merged, after_threshold=after_threshold, after_compression=list(after_threshold),
        kept=list(after_threshold), similarity_threshold=0.20, top_k=5,
        mode='hybrid', bm25_weight=0.4, vector_weight=0.6,
    )

    assert funnel['counts'] == {
        'merged': 3, 'after_threshold': 2, 'after_compression': 2, 'kept': 2, 'dropped': 1,
    }
    dropped = funnel['dropped']
    assert len(dropped) == 1
    assert dropped[0]['id'] == 'c'
    assert dropped[0]['stage'] == 'threshold'
    assert dropped[0]['threshold'] == 0.20
    assert dropped[0]['gap'] == 0.1  # 0.20 - 0.10


def test_top_k_truncation_is_attributed_to_top_k():
    """A candidate that survived the threshold but lost the top_k race says so explicitly."""
    retriever = _retriever()
    candidates = [_candidate('a', 0.9), _candidate('b', 0.8), _candidate('c', 0.7)]
    kept = candidates[:2]

    funnel = retriever._build_retrieval_funnel(
        merged=candidates, after_threshold=candidates, after_compression=candidates,
        kept=kept, similarity_threshold=0.2, top_k=2,
        mode='vector', bm25_weight=0.0, vector_weight=1.0,
    )

    assert funnel['counts']['kept'] == 2
    assert funnel['counts']['dropped'] == 1
    dropped = funnel['dropped'][0]
    assert dropped['id'] == 'c'
    assert dropped['stage'] == 'top_k'
    assert dropped['top_k'] == 2


def test_compression_drop_is_attributed_to_compression():
    """Context compression (token budget) is a distinct root cause from top_k truncation."""
    retriever = _retriever()
    candidates = [_candidate('a', 0.9), _candidate('b', 0.8)]
    kept = candidates[:1]

    funnel = retriever._build_retrieval_funnel(
        merged=candidates, after_threshold=candidates, after_compression=kept,
        kept=kept, similarity_threshold=0.2, top_k=5,
        mode='hybrid', bm25_weight=0.4, vector_weight=0.6,
    )

    dropped = funnel['dropped']
    assert len(dropped) == 1
    assert dropped[0]['id'] == 'b'
    assert dropped[0]['stage'] == 'compression'


def test_each_candidate_is_attributed_to_exactly_one_stage():
    """No duplicate reporting: a candidate dropped early must not reappear later."""
    retriever = _retriever()
    merged = [_candidate('keep', 0.9), _candidate('low', 0.05), _candidate('compressed', 0.7),
              _candidate('over_top_k', 0.6)]

    funnel = retriever._build_retrieval_funnel(
        merged=merged,
        after_threshold=[merged[0], merged[2], merged[3]],
        after_compression=[merged[0], merged[3]],
        kept=[merged[0]],
        similarity_threshold=0.2, top_k=1,
        mode='hybrid', bm25_weight=0.4, vector_weight=0.6,
    )

    by_stage = {item['id']: item['stage'] for item in funnel['dropped']}
    assert by_stage == {'low': 'threshold', 'compressed': 'compression', 'over_top_k': 'top_k'}
    assert funnel['counts'] == {
        'merged': 4, 'after_threshold': 3, 'after_compression': 2, 'kept': 1, 'dropped': 3,
    }


def test_kept_candidates_carry_route_attribution():
    """Kept evidence records which retrieval routes actually found it (BM25 / vector / both)."""
    retriever = _retriever()
    kept = [_candidate('both', 0.9), _candidate('vec_only', 0.8)]
    funnel = retriever._build_retrieval_funnel(
        merged=kept, after_threshold=kept, after_compression=kept, kept=kept,
        similarity_threshold=0.2, top_k=5,
        mode='hybrid', bm25_weight=0.4, vector_weight=0.6,
        bm25_results=[{'id': 'both'}], vector_results=[{'id': 'both'}, {'id': 'vec_only'}],
    )

    route_map = {item['id']: (item['in_bm25'], item['in_vector']) for item in funnel['kept']}
    assert route_map['both'] == (True, True)
    assert route_map['vec_only'] == (False, True)
    assert funnel['params']['top_k'] == 5
    assert funnel['params']['rerank_top_k'] == 10


def test_missing_optional_fields_do_not_break_funnel():
    """Candidates from BM25/vector-only routes may lack fusion scores: they must not raise."""
    retriever = _retriever()
    funnel = retriever._build_retrieval_funnel(
        merged=[{'id': 'raw', 'text': 'plain', 'metadata': {}}],
        after_threshold=[], after_compression=[], kept=[],
        similarity_threshold=0.3, top_k=5, mode='bm25',
        bm25_weight=1.0, vector_weight=0.0,
    )

    assert funnel['counts']['dropped'] == 1
    entry = funnel['dropped'][0]
    assert entry['stage'] == 'threshold'
    assert entry['score'] == 0.0
    assert entry['bm25_score'] == 0.0
    assert entry['chunk_index'] is None


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])

