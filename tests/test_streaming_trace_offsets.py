# -*- coding: utf-8 -*-
"""
End-to-end check of the streaming provenance contract in LLMPipeline.query_stream.

The chat UI attributes a per-sentence badge by the character offsets the backend sends,
so the streamed offsets and the final full-answer trace must describe the *same*
sentences. Nothing else in the suite exercises the async generator, and the code it used
to run (a boolean "skip the first sentence" plus a local `.`-splitting regex) produced
exactly the kind of off-by-one and number-splitting drift this test now pins down.

Fakes only: a stub LLM adapter, a stub retriever and a dictionary-backed embedding
function. No service, no Ollama, no vector store.
"""
import asyncio
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import llm_pipeline as pipeline_module  # noqa: E402
from core.llm_pipeline import LLMPipeline  # noqa: E402
from core.sentence_tracing import SentenceTracer  # noqa: E402

CHUNK_TEXT = '退款政策：签收后 7 天内可以申请退货退款，退款按原路径返回。'
CHUNK_TEXT_FINE = '退款政策：签收后 7 天内可以申请退货退款，退款按原路径返回'
ANSWER_SENTENCES = [
    '签收后 7 天内可以申请退货退款。[文档1]',
    '退款会按原支付路径返回。',
]
ANSWER = ''.join(ANSWER_SENTENCES)
# Deliberately awkward pieces so sentence boundaries, the number 7 and the [文档1] marker
# all land across chunk splits.
PIECES = [ANSWER[i:i + 5] for i in range(0, len(ANSWER), 5)]


def _vector(score: float) -> list:
    """Unit vector with cosine ``score`` against the chunk vector and 0 elsewhere."""
    return [score, 0.0, math.sqrt(max(0.0, 1.0 - score * score))]


def _fake_tracer(extra: dict = None) -> SentenceTracer:
    """Tracer whose similarities are fixed, so verdicts are deterministic."""
    vectors = {
        CHUNK_TEXT: [1.0, 0.0, 0.0],
        CHUNK_TEXT_FINE: [1.0, 0.0, 0.0],
        ANSWER_SENTENCES[0]: _vector(0.95),
        ANSWER_SENTENCES[1]: _vector(0.90),
    }
    vectors.update(extra or {})
    tracer = SentenceTracer()

    def fake_embedding(text: str):
        return list(vectors[text]) if text in vectors else []

    tracer._get_embedding = fake_embedding
    return tracer


class FakeAdapter:
    """Streams the answer in pieces, like a real chat model would."""

    def __init__(self, pieces):
        self.pieces = pieces
        self.calls = []

    def chat(self, model=None, messages=None, stream=False, **kwargs):
        self.calls.append({'model': model, 'messages': messages, 'stream': stream})
        for piece in self.pieces:
            yield {'message': {'content': piece}}
        yield {'done': True}


class FakeRetriever:
    """Returns one chunk with a high fused score (retrieval quality stays 'high')."""

    def __init__(self):
        self.vector_store = None

    def retrieve(self, kb_id, query, scenario_id=None, **kwargs):
        results = [{
            'id': 'chunk-A',
            'text': CHUNK_TEXT,
            'metadata': {'file_name': 'refund.txt', 'chunk_index': 0},
            'score': 0.9,
        }]
        return {
            'results': results,
            'count': len(results),
            'has_results': True,
            'mode': 'hybrid',
            'empty_response': '',
            'scenario_id': scenario_id,
            'debug_info': {},
            'recall_diagnosis': {},
            'business_diagnosis': {},
        }


class StubIntent:
    @staticmethod
    def classify(query, lang=None):
        return {'intent_type': 'test', 'confidence': 1.0}


class StubBoundary:
    @staticmethod
    def detect(query, lang=None):
        return {'in_domain': True, 'confidence': 1.0}


def _build_pipeline(monkeypatch, tmp_path, tracer: SentenceTracer = None) -> LLMPipeline:
    pipeline = LLMPipeline.__new__(LLMPipeline)
    pipeline.retriever = FakeRetriever()
    pipeline.llm_model = 'fake-model'
    pipeline.max_retries = 1
    pipeline.trace_enabled = True
    pipeline.trace_dir = tmp_path
    pipeline.evaluation_enabled = False
    pipeline._evaluator = None
    pipeline._llm_adapter = FakeAdapter(PIECES)
    pipeline._get_llm_adapter = lambda: pipeline._llm_adapter

    # Keep the trace in memory (no files written into the repo) and cut out the two
    # stages that would otherwise call a model.
    monkeypatch.setattr(pipeline_module.trace_manager, 'save_trace', lambda trace: None)
    monkeypatch.setattr(pipeline_module, 'sentence_tracer', tracer or _fake_tracer())
    monkeypatch.setattr('core.intent_classifier.intent_classifier', StubIntent)
    monkeypatch.setattr('core.boundary_detector.boundary_detector', StubBoundary)
    return pipeline


def _collect_frames(pipeline) -> list:
    async def _drain():
        frames = []
        async for frame in pipeline.query_stream(kb_id='kb-1', query='退货流程是什么'):
            frames.append(frame)
        return frames

    return asyncio.run(_drain())


def _parse(frames: list) -> list:
    """Turn SSE frames into (event, payload) pairs."""
    parsed = []
    for frame in frames:
        event, data = None, None
        for line in frame.strip().splitlines():
            if line.startswith('event: '):
                event = line[len('event: '):]
            elif line.startswith('data: '):
                data = json.loads(line[len('data: '):])
        parsed.append((event, data))
    return parsed


def _answer_from(parsed: list) -> str:
    return ''.join(
        payload['content'] for event, payload in parsed
        if event == 'answer' and payload and 'content' in payload
    )



def test_streamed_offsets_match_the_final_trace(monkeypatch, tmp_path):
    """Every incremental event must slice the answer back to its own sentence."""
    pipeline = _build_pipeline(monkeypatch, tmp_path)
    parsed = _parse(_collect_frames(pipeline))
    answer = _answer_from(parsed)
    assert answer == ANSWER

    incremental = [payload for event, payload in parsed if event == 'sentence_provenance']
    assert incremental, 'no incremental provenance was streamed'

    # 1. Each event's offsets point at exactly the sentence it reports.
    for payload in incremental:
        start, end = payload['sentence_start'], payload['sentence_end']
        assert answer[start:end] == payload['sentence']
        assert payload['sentence_provenance']['start'] == start
        assert payload['sentence_provenance']['end'] == end

    # 2. They describe the same sentences as the final full-answer trace.
    final_payload = next(payload for event, payload in parsed if event == 'sentence_tracing')
    final = final_payload['sentence_tracing']
    assert [item['sentence'] for item in final] == ANSWER_SENTENCES
    assert [(item['start'], item['end']) for item in final] == [
        (item['sentence_start'], item['sentence_end']) for item in incremental
    ]
    for item in final:
        assert answer[item['start']:item['end']] == item['sentence']


def test_incremental_verdicts_match_the_final_verdicts(monkeypatch, tmp_path):
    """Streaming and batch must agree on both attribution and confidence level."""
    pipeline = _build_pipeline(monkeypatch, tmp_path)
    parsed = _parse(_collect_frames(pipeline))

    incremental = [payload['sentence_provenance'] for event, payload in parsed
                   if event == 'sentence_provenance']
    final = next(payload for event, payload in parsed
                 if event == 'sentence_tracing')['sentence_tracing']

    assert len(incremental) == len(final) == 2
    for streamed, batch in zip(incremental, final):
        assert streamed['sentence'] == batch['sentence']
        assert streamed['confidence_level'] == batch['confidence_level']
        assert streamed['attribution'] == batch['attribution']

    # The first sentence cites [文档1], so it is attributed to chunk-A by citation.
    assert final[0]['confidence_level'] == 'citation_verified'
    assert final[0]['attribution'] == 'citation'
    assert final[0]['sources'][0]['chunk_id'] == 'chunk-A'
    assert final[1]['attribution'] == 'similarity'




def test_citation_validation_and_counts_are_streamed(monkeypatch, tmp_path):
    """The citation audit and the drift aggregates still arrive after the answer."""
    pipeline = _build_pipeline(monkeypatch, tmp_path)
    parsed = _parse(_collect_frames(pipeline))

    citation_payload = next(payload for event, payload in parsed if event == 'citation_validation')
    assert citation_payload['citation_validation']['valid_citations'] == 1

    drift = next(payload for event, payload in parsed
                 if event == 'sentence_tracing')['drift_analysis']
    assert drift['total_sentences'] == 2
    assert drift['citation_verified_count'] == 1
    assert drift['unverified_count'] == 0
    assert drift['drift_count'] == 0


def test_no_disclaimer_is_duplicated_in_hybrid_mode(monkeypatch, tmp_path):
    """A low-quality retrieval injects the disclaimer once, and it is not traced live."""
    pipeline = _build_pipeline(monkeypatch, tmp_path)

    def low_score_retrieve(kb_id, query, scenario_id=None, **kwargs):
        payload = FakeRetriever().retrieve(kb_id, query, scenario_id)
        payload['results'][0]['score'] = 0.05       # forces retrieval_quality='low'
        return payload

    pipeline.retriever.retrieve = low_score_retrieve
    parsed = _parse(_collect_frames(pipeline))
    answer = _answer_from(parsed)

    from service.i18n import _

    disclaimer = _('pipeline.hybrid_disclaimer', 'en-US')
    assert answer.startswith(disclaimer)
    assert answer.count(disclaimer.strip()) == 1

    # The disclaimer is rendered as its own card, so no provenance event targets it.
    incremental = [payload for event, payload in parsed if event == 'sentence_provenance']
    assert incremental
    assert all(payload['sentence_start'] >= len(disclaimer) for payload in incremental)


def test_a_repeated_disclaimer_is_not_streamed_twice(monkeypatch, tmp_path):
    """The model echoes the injected disclaimer; the client must still read it once.

    Real models follow `pipeline.system_prompt_hybrid` and repeat the two opening lines (the stored
    traces hold the prefix twice) while the pipeline injects them before streaming. The duplicate is
    dropped in flight, so the visible answer and the reported offsets stay consistent.
    """
    from service.i18n import _

    pipeline = _build_pipeline(monkeypatch, tmp_path)

    def low_score_retrieve(kb_id, query, scenario_id=None, **kwargs):
        payload = FakeRetriever().retrieve(kb_id, query, scenario_id)
        payload['results'][0]['score'] = 0.05       # forces retrieval_quality='low'
        return payload

    pipeline.retriever.retrieve = low_score_retrieve

    disclaimer = _('pipeline.hybrid_disclaimer', 'en-US')
    # The echo differs from the injected text in whitespace only, which is what real models produce.
    echo = disclaimer.replace('\n\n', '\n').rstrip() + ' \n'
    assert echo != disclaimer

    stream = echo + ANSWER
    pipeline._llm_adapter = FakeAdapter([stream[i:i + 5] for i in range(0, len(stream), 5)])

    parsed = _parse(_collect_frames(pipeline))
    answer = _answer_from(parsed)

    assert answer == disclaimer + ANSWER
    assert answer.count(disclaimer.strip()) == 1

    incremental = [payload for event, payload in parsed if event == 'sentence_provenance']
    assert incremental
    for payload in incremental:
        start, end = payload['sentence_start'], payload['sentence_end']
        assert answer[start:end] == payload['sentence']
        assert payload['sentence_provenance']['start'] == start
        assert payload['sentence_provenance']['end'] == end


def test_contradictions_are_streamed_and_attached_to_the_sentence(monkeypatch, tmp_path):
    """The sentence can be both well supported and wrong on a number.

    The answer states 0.9 where the only source says 0.4: the tracer finds the sentence
    closest to that chunk (a direct quote) *and* the contradiction checker must flag it, which
    is the reading the old design could not express at all.
    """
    answer = '混合检索默认启用，BM25 的默认权重为 0.9。'
    contradicting_chunk = '混合检索默认启用，BM25 权重为 0.4，向量权重为 0.6。'
    tracer = _fake_tracer({
        answer: _vector(0.95),
        contradicting_chunk: [1.0, 0.0, 0.0],
        contradicting_chunk.rstrip('。'): [1.0, 0.0, 0.0],
    })
    pipeline = _build_pipeline(monkeypatch, tmp_path, tracer=tracer)
    pipeline._llm_adapter = FakeAdapter([answer])

    def retrieve(kb_id, query, scenario_id=None, **kwargs):
        payload = FakeRetriever().retrieve(kb_id, query, scenario_id)
        payload['results'][0]['text'] = contradicting_chunk
        return payload

    pipeline.retriever.retrieve = retrieve
    parsed = _parse(_collect_frames(pipeline))

    # The discrepancy itself. The reference value is the CLOSEST in-scope source claim
    # (0.9 is nearer to 0.6 than to 0.4), which is what the explanation quotes.
    event_names = [event for event, _payload in parsed]
    contradiction_payload = next(payload for event, payload in parsed if event == 'contradictions')
    assert contradiction_payload['contradiction_count'] == 1
    finding = contradiction_payload['contradictions'][0]
    assert finding['type'] == 'number_quantity'
    assert finding['answer_text'] == '0.9'
    assert finding['source_text'] == '0.6'
    assert finding['sentence'] == answer

    # The trace item is flagged, and keeps its (independent) confidence verdict
    trace_items = next(payload for event, payload in parsed
                       if event == 'sentence_tracing')['sentence_tracing']
    assert len(trace_items) == 1
    assert trace_items[0]['has_contradiction'] is True
    assert trace_items[0]['contradictions'][0]['source_text'] == '0.6'
    assert trace_items[0]['confidence_level'] == 'direct_quote'

    # Ordering: the flagged sentence must reach the UI before the findings summary, and the
    # completion event carries the count for the meta bar.
    assert event_names.index('sentence_tracing') < event_names.index('contradictions')
    done_payload = next(payload for event, payload in parsed if event == 'done')
    assert done_payload['contradiction_count'] == 1


if __name__ == '__main__':
    import pytest as _pytest
    _pytest.main([__file__, '-v'])
