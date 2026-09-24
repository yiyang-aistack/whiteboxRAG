# -*- coding: utf-8 -*-
"""
An empty retrieval route must not empty the answer.

The hybrid score is `bm25_weight * bm25 + vector_weight * vector`, so a route that returns
nothing still caps what any candidate can reach at the *other* route's weight. With the shipped
defaults (`bm25_weight` 0.6 -> vector weight 0.4) and `similarity_threshold` 0.5 the cap is 0.4,
i.e. below the threshold: every candidate is dropped and the user is told the knowledge base has
nothing, although the vector route had found the right chunk.

That is exactly what the first query after a restart did (the lazy BM25 build finished ~20 ms
after the request had already merged with `bm25_results = []`), while the second, identical query
answered correctly. These tests pin both halves of the fix:

* a bounded wait for an in-flight build (`bm25.build_wait_ms`), so the first query is not the one
  that pays for the index,
* weight renormalization onto the route that did answer (`retriever.reroute_on_empty_route`),
  reported in `debug_info` and in the retrieval funnel,
* a startup warning for weight/threshold combinations that make a route mandatory.

Pure logic + a stub vector store: no Ollama, no Chroma, no service.
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import config  # noqa: E402
from core import retriever as retriever_module  # noqa: E402
from core.retriever import (  # noqa: E402
    HybridRetriever,
    get_retrieval_defaults,
    validate_retrieval_weights,
)

KB_ID = 'kb-degrade'
QUERY = 'how does tongyi deepresearch work'

# Vector scores as they come back from the vector store (`1 - cosine distance`), i.e. two real
# matches and one candidate that deserves to be dropped by the threshold.
VECTOR_RESULTS = [
    {'id': 'c0', 'text': 'Tongyi DeepResearch is a fully open-source Web Agent.',
     'metadata': {'file_name': 'tongyi.txt', 'chunk_index': 0}, 'score': 0.667},
    {'id': 'c1', 'text': 'It was trained with Agentic CPT, SFT and on-policy RL.',
     'metadata': {'file_name': 'tongyi.txt', 'chunk_index': 1}, 'score': 0.597},
    {'id': 'c2', 'text': 'Unrelated boilerplate.',
     'metadata': {'file_name': 'tongyi.txt', 'chunk_index': 2}, 'score': 0.300},
]


class StubVectorStore:
    """Vector route only: `get_collection()` returns None, so no BM25 index can be built."""

    embedding_model = 'stub-embed'
    embedding_dim = 8

    def search(self, kb_id, query, top_k=None):
        return [dict(item) for item in VECTOR_RESULTS]

    def get_collection(self, kb_id):
        return None


class DocumentsOnlyVectorStore:
    """Exposes `get_documents()` (the BM25 path) and counts any `get_collection()` call."""

    embedding_model = 'stub-embed'
    embedding_dim = 8

    def __init__(self, documents):
        self.documents = documents
        self.collection_reads = 0

    def get_documents(self, kb_id):
        return [dict(item) for item in self.documents]

    def get_collection(self, kb_id):
        self.collection_reads += 1
        return None

    def search(self, kb_id, query, top_k=None):
        return []


def build_retriever(**overrides) -> HybridRetriever:
    """Retriever with the real __init__ (config defaults) and optional attribute overrides."""
    retriever = HybridRetriever(StubVectorStore())
    # Compression is not what these tests are about; keeping it out removes a second, unrelated
    # reason for a chunk to disappear from the final result list.
    retriever.compression_enabled = False
    for key, value in overrides.items():
        setattr(retriever, key, value)
    return retriever


def run_retrieval(retriever: HybridRetriever, **kwargs) -> dict:
    """Retrieve without touching a model: the query-rewrite stage is switched off."""
    return retriever.retrieve(KB_ID, QUERY, query_rewrite_enabled=False, **kwargs)


def test_shipped_weights_are_reported_as_route_starving():
    """settings.yaml declares 0.6 / 0.5: the vector route alone cannot reach the threshold."""
    defaults = get_retrieval_defaults()
    warnings = validate_retrieval_weights(defaults)

    assert defaults['mode'] == 'hybrid'
    assert defaults['vector_weight'] < defaults['similarity_threshold']
    assert warnings, 'the shipped weight/threshold combination must be reported'
    assert 'vector_weight' in warnings[0]


def test_self_consistent_weights_are_not_flagged():
    """A 50/50 split against a 0.5 threshold is fine, and other modes are not checked."""
    assert validate_retrieval_weights(
        {'mode': 'hybrid', 'bm25_weight': 0.5, 'vector_weight': 0.5, 'similarity_threshold': 0.5}
    ) == []
    assert validate_retrieval_weights(
        {'mode': 'vector', 'bm25_weight': 1.0, 'vector_weight': 0.0, 'similarity_threshold': 0.5}
    ) == []


def test_settings_declare_the_degradation_controls():
    """Both new switches are config-declared, so operators can tune them without a code change."""
    assert config.get('bm25.build_wait_ms') is not None
    assert config.get('retriever.reroute_on_empty_route') is not None


def test_empty_bm25_route_does_not_empty_the_answer():
    """The reported bug: BM25 empty + vector hits must still return the vector hits."""
    retriever = build_retriever()
    result = run_retrieval(retriever)

    # The BM25 route is empty (no index for this knowledge base), the vector route is not.
    assert result['debug_info']['bm25_results'] == []
    assert len(result['debug_info']['vector_results']) == len(VECTOR_RESULTS)

    # Its weight is redistributed instead of capping the live route at 0.4.
    assert result['debug_info']['route_degraded'] is True
    assert result['debug_info']['effective_weights'] == {'bm25_weight': 0.0, 'vector_weight': 1.0}
    # The funnel explains the scores with the same effective weights ...
    assert result['debug_info']['funnel']['params']['bm25_weight'] == 0.0
    assert result['debug_info']['funnel']['params']['vector_weight'] == 1.0
    # ... while the configured values stay visible as what the caller asked for.
    assert result['debug_info']['params']['bm25_weight'] == retriever.bm25_weight
    assert any(step['step'] == 'route_degraded' for step in result['debug_info']['steps'])

    # Counterfactual of the reported bug: keeping the configured weights would have made the best
    # candidate unreachable (0.4 * 0.667 = 0.267 < 0.5) and the answer empty.
    assert 0.4 * VECTOR_RESULTS[0]['score'] < retriever.similarity_threshold

    assert result['count'] == 2
    assert [item['id'] for item in result['results']] == ['c0', 'c1']
    assert result['has_results'] is True


def test_reroute_can_be_switched_off(monkeypatch):
    """The old behaviour stays reachable for A/B runs that must compare like with like."""
    original_get = retriever_module.config.get
    monkeypatch.setattr(
        retriever_module.config, 'get',
        lambda key, *args: False if key == 'retriever.reroute_on_empty_route' else original_get(key, *args)
    )

    retriever = build_retriever()
    result = run_retrieval(retriever)

    assert result['debug_info']['route_degraded'] is False
    assert result['debug_info']['effective_weights'] == {
        'bm25_weight': retriever.bm25_weight,
        'vector_weight': retriever.vector_weight,
    }
    assert result['count'] == 0, 'without the reroute the vectors are capped below the threshold'


def test_bm25_build_in_flight_is_awaited(monkeypatch):
    """A retrieval waits for the build it triggered instead of answering from one route."""
    retriever = build_retriever()
    release = threading.Event()

    def slow_build(kb_id, documents=None, expected_version=None):
        assert release.wait(5.0), 'the test released the build'
        retriever._set_bm25_cache(
            kb_id, object(), [{'id': 'c0', 'text': 'tongyi', 'metadata': {}}], expected_version
        )

    monkeypatch.setattr(retriever, '_build_bm25_index', slow_build)
    threading.Timer(0.2, release.set).start()

    started = time.time()
    assert retriever._ensure_bm25_index('kb-wait', wait_ms=3000) is True
    # It cannot have returned before the build was released.
    assert time.time() - started >= 0.15


def test_bm25_build_timeout_degrades_instead_of_blocking(monkeypatch):
    """A build that overruns `build_wait_ms` degrades, it does not hang the request."""
    retriever = build_retriever()
    release = threading.Event()

    def stuck_build(kb_id, documents=None, expected_version=None):
        release.wait(5.0)
        retriever._set_bm25_cache(kb_id, None, [], expected_version)

    monkeypatch.setattr(retriever, '_build_bm25_index', stuck_build)

    started = time.time()
    assert retriever._ensure_bm25_index('kb-slow', wait_ms=100) is False
    elapsed = time.time() - started
    release.set()
    assert elapsed < 2.0


def test_bm25_build_reads_documents_without_opening_a_collection():
    """The keyword index must not pay the embedding-model load that get_collection() triggers.

    `get_collection()` attaches the configured embedding function, and constructing it loads the
    whole model (measured: 5.1 s cold for a 33-chunk knowledge base versus 0.4 s through
    `get_documents()`), so a bounded wait could never cover the first query after a restart.
    """
    store = DocumentsOnlyVectorStore([
        {'id': 'c0', 'text': 'tongyi deepresearch plans, searches and cites sources.',
         'metadata': {'file_name': 'tongyi.txt', 'chunk_index': 0}},
        # Unrelated chunks on purpose: BM25Okapi only awards a positive IDF to a term that appears
        # in a minority of the documents, so a term present in every chunk scores below the
        # retriever's `score > 0` filter.
        {'id': 'c1', 'text': 'invoices and shipping documentation for the warehouse.',
         'metadata': {'file_name': 'other.txt', 'chunk_index': 0}},
        {'id': 'c2', 'text': 'engine oil filter replacement interval is 5000 km.',
         'metadata': {'file_name': 'other.txt', 'chunk_index': 1}},
        {'id': 'c3', 'text': 'assembly line torque specification for the gearbox.',
         'metadata': {'file_name': 'other.txt', 'chunk_index': 2}},
    ])
    retriever = HybridRetriever(store)
    retriever.compression_enabled = False

    assert retriever._ensure_bm25_index(KB_ID, wait_ms=2000) is True
    assert store.collection_reads == 0, 'BM25 must not build the embedding function'

    hits = retriever._bm25_search(KB_ID, 'tongyi deepresearch', 5)
    assert hits and hits[0]['id'] == 'c0'


def test_a_finished_build_does_not_block_the_next_one(monkeypatch):
    """A finished (empty) build must not be joined forever: the next retrieval rebuilds."""
    retriever = build_retriever()
    builds = []

    def build_empty_index(kb_id, documents=None, expected_version=None):
        builds.append(kb_id)
        retriever._set_bm25_cache(kb_id, None, [], expected_version)

    monkeypatch.setattr(retriever, '_build_bm25_index', build_empty_index)

    assert retriever._ensure_bm25_index('kb-empty', wait_ms=2000) is True
    assert retriever._bm25_search('kb-empty', 'tongyi', 5) == []

    retriever.invalidate_bm25_index('kb-empty')
    assert retriever._ensure_bm25_index('kb-empty', wait_ms=2000) is True
    assert builds == ['kb-empty', 'kb-empty']


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
