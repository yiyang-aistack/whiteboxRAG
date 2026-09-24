# -*- coding: utf-8 -*-
"""
Tests for the two on-demand white-box debug endpoints in api/routes/chat.py:

* POST /api/chat/simulate  - hypothesis analysis ("what if this chunk were not retrieved?")
* POST /api/chat/miss-scan - full-scan for relevant chunks that never reached the prompt

Both are driven with a stub pipeline/retriever, so no LLM, embedding service or vector
store is touched. The simulate test exists because the endpoint used to call a
`_get_ollama_client()` helper that no longer exists on the pipeline, which made every
simulation return "model unavailable" instead of a regenerated answer.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.routes import chat  # noqa: E402


class FakeRequest:
    """Minimal stand-in for a FastAPI Request (only what i18n reads)."""

    def __init__(self, accept_language: str = 'zh-CN'):
        self.headers = {'accept-language': accept_language}
        self.query_params = {}


class FakeAdapter:
    def __init__(self, content: str = 'SIMULATED ANSWER', raises: Exception = None):
        self.content = content
        self.raises = raises
        self.calls = []

    def chat(self, model, messages, stream=False, **kwargs):
        self.calls.append({'model': model, 'messages': messages, 'stream': stream})
        if self.raises:
            raise self.raises
        return {'message': {'content': self.content}}


def _pipeline(adapter=None, retriever=None, original_query_result=None):
    """Build a stub LLMPipeline exposing only what the endpoints use."""
    pipeline = SimpleNamespace(
        llm_model='fake-model',
        retriever=retriever,
        _get_llm_adapter=lambda: adapter,
        _build_prompt=lambda query, chunks, scenario_id=None, **kw: f'PROMPT::{query}::{len(chunks)}',
    )
    if original_query_result is not None:
        pipeline.query = lambda **kwargs: original_query_result
    return pipeline


@pytest.fixture
def restore_pipeline(monkeypatch):
    """Ensure the module-level pipeline is restored even if a test blows up."""
    original = chat._llm_pipeline
    yield monkeypatch
    chat._llm_pipeline = original


def test_simulate_regenerates_through_the_llm_adapter(monkeypatch, restore_pipeline):
    """The simulated answer must come from the adapter, not from the 'model unavailable' fallback."""
    adapter = FakeAdapter(content='删掉该片段后仍可回答')
    monkeypatch.setattr(chat, '_llm_pipeline', _pipeline(adapter=adapter))

    request = chat.SimulateRequest(
        kb_id='kb-1',
        query='退货流程是什么',
        selected_chunks=[{'id': 'c1', 'text': '退货需在签收后7天内申请', 'metadata': {}}],
    )
    result = asyncio.run(chat.simulate_chat(request, FakeRequest()))

    assert result['success'] is True
    assert result['answer'] == '删掉该片段后仍可回答'
    assert result['error'] is None
    assert result['chunk_count'] == 1
    assert adapter.calls and adapter.calls[0]['model'] == 'fake-model'
    assert adapter.calls[0]['stream'] is False
    assert 'PROMPT::退货流程是什么::1' in adapter.calls[0]['messages'][0]['content']
    assert 'duration' in result


def test_simulate_excludes_requested_chunks_before_prompting(monkeypatch, restore_pipeline):
    """Excluded chunk ids must be removed from the chunks handed to the prompt."""
    adapter = FakeAdapter()
    monkeypatch.setattr(chat, '_llm_pipeline', _pipeline(adapter=adapter))

    request = chat.SimulateRequest(
        kb_id='kb-1',
        query='q',
        selected_chunks=[
            {'id': 'c1', 'text': 'a', 'metadata': {}},
            {'id': 'c2', 'text': 'b', 'metadata': {}},
        ],
        excluded_chunk_ids=['c2'],
    )
    result = asyncio.run(chat.simulate_chat(request, FakeRequest()))

    assert result['chunk_count'] == 1
    assert result['excluded_count'] == 1
    assert 'PROMPT::q::1' in adapter.calls[0]['messages'][0]['content']


def test_simulate_reports_adapter_failure_without_crashing(monkeypatch, restore_pipeline):
    """A dead model backend yields the localized fallback plus the raw error, not a 500."""
    adapter = FakeAdapter(raises=RuntimeError('connection refused'))
    monkeypatch.setattr(chat, '_llm_pipeline', _pipeline(adapter=adapter))

    request = chat.SimulateRequest(kb_id='kb-1', query='q', selected_chunks=[])
    result = asyncio.run(chat.simulate_chat(request, FakeRequest()))

    assert result['success'] is True
    assert result['error'] == 'connection refused'
    assert result['answer']  # localized "model unavailable" message


def _retriever(collection_exists=True, results=None, params=None):
    params = params or {'similarity_threshold': 0.35, 'top_k': 3}
    store = SimpleNamespace(collection_exists=lambda kb_id: collection_exists)
    return SimpleNamespace(
        vector_store=store,
        retrieve=lambda kb_id, query, scenario_id=None: {
            'results': results if results is not None else [{'id': 'hit-1'}],
            'count': len(results if results is not None else [{'id': 'hit-1'}]),
        },
        get_effective_params=lambda scenario_id=None: params,
    )


def test_miss_scan_forces_the_full_scan_and_reuses_retrieval_params(monkeypatch, restore_pipeline):
    """The scan must run with debug=True and the scenario threshold of a real query."""
    captured = {}

    def fake_diagnose(**kwargs):
        captured.update(kwargs)
        return {
            'enabled': True,
            'potential_misses': [
                {
                    'id': 'miss-1',
                    'similarity': 0.33,
                    'keyword_overlap': 2,
                    'metadata': {'file_name': 'refund.txt'},
                    'text_preview': 'near threshold text',
                    'root_cause': 'threshold_edge',
                }
            ],
            'potential_misses_skipped': False,
            'summary': {'total_issues': 1},
        }

    monkeypatch.setattr('core.recall_diagnostic.recall_diagnostic.diagnose', fake_diagnose)
    monkeypatch.setattr(chat, '_llm_pipeline', _pipeline(retriever=_retriever()))

    result = asyncio.run(chat.miss_scan(
        chat.MissScanRequest(kb_id='kb-1', query='退货'),
        FakeRequest(),
    ))

    assert result['success'] is True
    assert result['potential_misses'][0]['id'] == 'miss-1'
    assert result['retrieved_count'] == 1
    assert result['threshold'] == 0.35
    assert isinstance(result['scan_limit'], int)
    assert result['duration'] >= 0
    # The whole point of the endpoint: the expensive scan is actually forced on.
    assert captured['debug'] is True
    assert captured['params']['similarity_threshold'] == 0.35
    assert captured['retrieved_results'] == [{'id': 'hit-1'}]
    assert captured['vector_store'] is chat._llm_pipeline.retriever.vector_store


def test_miss_scan_matches_retrieved_ids_from_a_real_retrieval(monkeypatch, restore_pipeline):
    """Retrieved chunks are excluded from the scan because retrieval is re-run server-side."""
    seen = {}

    def fake_diagnose(**kwargs):
        seen['retrieved'] = kwargs['retrieved_results']
        return {'enabled': True, 'potential_misses': [], 'summary': {}}

    monkeypatch.setattr('core.recall_diagnostic.recall_diagnostic.diagnose', fake_diagnose)
    monkeypatch.setattr(
        chat, '_llm_pipeline',
        _pipeline(retriever=_retriever(results=[{'id': 'k1'}, {'id': 'k2'}])),
    )

    result = asyncio.run(chat.miss_scan(
        chat.MissScanRequest(kb_id='kb-1', query='q'),
        FakeRequest(),
    ))

    assert seen['retrieved'] == [{'id': 'k1'}, {'id': 'k2'}]
    assert result['retrieved_count'] == 2
    assert result['potential_misses'] == []


def test_miss_scan_rejects_unknown_knowledge_base(monkeypatch, restore_pipeline):
    """An unknown KB is a 404, not a scan over an empty collection."""
    monkeypatch.setattr(chat, '_llm_pipeline', _pipeline(retriever=_retriever(collection_exists=False)))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(chat.miss_scan(
            chat.MissScanRequest(kb_id='missing', query='q'),
            FakeRequest(),
        ))
    assert exc.value.status_code == 404


def test_miss_scan_requires_an_initialized_pipeline(monkeypatch, restore_pipeline):
    """Without a pipeline the endpoint must fail fast instead of raising AttributeError."""
    monkeypatch.setattr(chat, '_llm_pipeline', None)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(chat.miss_scan(
            chat.MissScanRequest(kb_id='kb-1', query='q'),
            FakeRequest(),
        ))
    assert exc.value.status_code == 503


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
