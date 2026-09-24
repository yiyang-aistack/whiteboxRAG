# -*- coding: utf-8 -*-
"""
Sentence-level tracing tests (core/sentence_tracing.py).

These tests exist because the tracer is the product's core promise ("which sentence is
supported by which chunk") and used to have none: the module carried three copies of
``trace_single``/``build_chunk_index``, the splitter cut numbers in half, the
per-sentence ``citations`` field could never be populated (it compared ``[文档1]``
markers against UUID chunk ids), and an embedding failure was reported as a
hallucination.

Everything here runs offline: the embedding backend is replaced by a dictionary of
vectors, so similarities - and therefore verdicts - are exact and deterministic.

Attribution contract under test:
  * a sentence whose ``[文档N]`` marker resolves to a retrieved chunk is attributed to
    that chunk (``attribution='citation'``, level ``citation_verified``) even when the
    similarity heuristic would have picked a different chunk;
  * otherwise the similarity heuristic decides, using absolute floors AND the
    sentence's rank inside this answer's own scores;
  * when the tracer cannot evaluate a sentence at all (embedding failure), the verdict
    is ``unverified`` and it is NOT counted as drift/hallucination.
"""
import ast
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.sentence_tracing import SentenceTracer  # noqa: E402

CHUNK_A = '退款政策：签收后 7 天内可申请退货退款。'
CHUNK_B = '配送说明：工作日 15:00 前下单当日发出。'

# Reference directions for the two chunks: A along x, B along y. An answer vector of
# _vector(s) therefore has similarity s against chunk A and exactly 0 against chunk B,
# which makes the fused score (max over chunks) equal to s.
CHUNK_VECTOR_A = [1.0, 0.0, 0.0]
CHUNK_VECTOR_B = [0.0, 1.0, 0.0]


def _vector(score: float) -> list:
    """Unit vector with cosine ``score`` against chunk A and 0 against chunk B."""
    return [score, 0.0, math.sqrt(max(0.0, 1.0 - score * score))]


def build_tracer(vectors: dict) -> SentenceTracer:
    """Tracer whose embeddings come from ``vectors`` (unknown text -> failure)."""
    tracer = SentenceTracer()

    def fake_embedding(text: str):
        return list(vectors[text]) if text in vectors else []

    tracer._get_embedding = fake_embedding
    return tracer


def context_two_chunks() -> list:
    return [
        {'id': 'chunk-A', 'text': CHUNK_A, 'metadata': {'file_name': 'refund.txt'}},
        {'id': 'chunk-B', 'text': CHUNK_B, 'metadata': {'file_name': 'delivery.txt'}},
    ]


def chunk_vectors() -> dict:
    """Chunk vectors plus each chunk's inner sentence (one sentence per chunk)."""
    return {
        CHUNK_A: list(CHUNK_VECTOR_A),
        '退款政策：签收后 7 天内可申请退货退款': list(CHUNK_VECTOR_A),
        CHUNK_B: list(CHUNK_VECTOR_B),
        '配送说明：工作日 15:00 前下单当日发出': list(CHUNK_VECTOR_B),
    }


# ---------------------------------------------------------------------------
# Structure: no dead duplicates, streaming must reuse the same primitive
# ---------------------------------------------------------------------------

def test_module_has_no_duplicate_method_definitions():
    """Three copies of trace_single/build_chunk_index once lived here.

    Python silently keeps the last definition, so an edit to an earlier copy had no
    effect - the streaming path could be "fixed" without anything changing.
    """
    source = (ROOT / 'core' / 'sentence_tracing.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    klass = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    names = [
        node.name for node in klass.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert not duplicates, f'duplicate method definitions: {duplicates}'


def test_streaming_wrapper_contains_no_local_scoring_logic():
    """_single_sentence_trace must delegate, not re-implement similarity/thresholds."""
    source = (ROOT / 'core' / 'llm_pipeline.py').read_text(encoding='utf-8')
    start = source.index('def _single_sentence_trace')
    end = source.index('def _ensure_hybrid_format')
    body = source[start:end]
    assert 'sentence_tracer.trace_single(' in body
    assert '_decide_confidence' not in body
    assert '_cosine_similarity' not in body


# ---------------------------------------------------------------------------
# Splitting (the numbers regression)
# ---------------------------------------------------------------------------

def test_answer_sentences_keep_decimals_intact():
    tracer = build_tracer({})
    answer = '混合检索默认启用，BM25 的权重为 0.4，向量权重为 0.6。第二句。'
    assert tracer._split_sentences(answer) == [
        '混合检索默认启用，BM25 的权重为 0.4，向量权重为 0.6。',
        '第二句。',
    ]


def test_items_carry_offsets_that_slice_back_to_the_sentence():
    answer = '第一句。第二句。'
    vectors = chunk_vectors()
    vectors.update({'第一句。': _vector(0.9), '第二句。': _vector(0.9)})
    tracer = build_tracer(vectors)

    items = tracer.trace(answer, context_two_chunks())
    assert len(items) == 2
    for index, item in enumerate(items):
        assert item['index'] == index
        assert answer[item['start']:item['end']] == item['sentence']


# ---------------------------------------------------------------------------
# Route 1: citation wins over similarity
# ---------------------------------------------------------------------------

def test_resolvable_citation_wins_over_a_better_similarity_match():
    """The marker is evidence: [文档2] must attribute the sentence to chunk-B.

    The sentence is deliberately far more similar to chunk-A, so this test fails if the
    similarity heuristic is allowed to override the citation.
    """
    sentence = '签收后 7 天内可以申请退货退款。[文档2]'
    vectors = chunk_vectors()
    vectors[sentence] = _vector(0.95)          # closest to chunk-A
    tracer = build_tracer(vectors)

    item = tracer.trace(sentence, context_two_chunks())[0]
    assert item['attribution'] == 'citation'
    assert item['confidence_level'] == 'citation_verified'
    assert item['citation_markers'] == ['2']
    assert item['citations'] == ['2']
    assert item['sources'][0]['chunk_id'] == 'chunk-B'
    assert item['sources'][0]['cited'] is True
    assert item['is_drift'] is False
    # The similarity-only verdict is still reported, so the UI can show both.
    assert item['similarity_level'] is not None


def test_cited_chunk_is_scored_even_when_similarity_would_drop_it():
    """A cited chunk with a ~0 similarity is still evidence, with a real score."""
    sentence = '与两段都不相似的引用句。[文档2]'
    vectors = chunk_vectors()
    vectors[sentence] = _vector(0.0)
    tracer = build_tracer(vectors)

    item = tracer.trace(sentence, context_two_chunks())[0]
    assert item['attribution'] == 'citation'
    assert item['sources'][0]['chunk_id'] == 'chunk-B'
    assert item['sources'][0]['score'] == pytest.approx(0.0, abs=0.01)
    assert item['sources'][0]['text_preview']


def test_out_of_range_citation_falls_back_to_similarity():
    sentence = '引用了一个不存在的文档。[文档9]'
    vectors = chunk_vectors()
    vectors[sentence] = _vector(0.95)
    tracer = build_tracer(vectors)

    item = tracer.trace(sentence, context_two_chunks())[0]
    assert item['attribution'] == 'similarity'
    assert item['invalid_citation_markers'] == ['9']
    assert item['citations'] == []
    assert item['confidence_level'] == 'direct_quote'


def test_citations_field_is_no_longer_always_empty():
    """Regression: [文档1] used to be compared against UUID chunk ids -> always []."""
    sentence = '第一句陈述。[文档1]'
    vectors = chunk_vectors()
    vectors[sentence] = _vector(0.9)
    tracer = build_tracer(vectors)

    item = tracer.trace(sentence, context_two_chunks())[0]
    assert item['citations'] == ['1']
    assert item['sources'][0]['chunk_id'] == 'chunk-A'


# ---------------------------------------------------------------------------
# Route 2: similarity heuristic = floors + relative rank
# ---------------------------------------------------------------------------

def test_direct_quote_requires_floor_and_rank():
    answer = '第一句。第二句。第三句。第四句。'
    scores = [0.95, 0.90, 0.88, 0.86]
    vectors = chunk_vectors()
    for sentence, score in zip(['第一句。', '第二句。', '第三句。', '第四句。'], scores):
        vectors[sentence] = _vector(score)
    tracer = build_tracer(vectors)

    levels = [item['confidence_level'] for item in tracer.trace(answer, context_two_chunks())]
    # Only the best sentence of the answer may claim a direct quote, even though all
    # four are above the 0.75 floor.
    assert levels[0] == 'direct_quote'
    assert 'direct_quote' not in levels[1:]
    assert levels[1] == 'summary'
    assert levels[2:] == ['low_confidence', 'low_confidence']


def test_tied_scores_do_not_promote_every_sentence():
    answer = '第一句。第二句。第三句。'
    vectors = chunk_vectors()
    for sentence in ['第一句。', '第二句。', '第三句。']:
        vectors[sentence] = _vector(0.9)
    tracer = build_tracer(vectors)

    levels = [item['confidence_level'] for item in tracer.trace(answer, context_two_chunks())]
    # An undiscriminating distribution must not be read as "all quoted".
    assert 'direct_quote' not in levels


def test_short_answers_fall_back_to_absolute_floors():
    answer = '只有一句话。'
    vectors = chunk_vectors()
    vectors[answer] = _vector(0.6)
    tracer = build_tracer(vectors)

    item = tracer.trace(answer, context_two_chunks())[0]
    assert item['confidence_level'] == 'summary'   # floors only: 0.55 <= 0.6 < 0.75


def test_sentence_below_the_drift_floor_is_drift():
    answer = '与知识库无关的一句话。'
    vectors = chunk_vectors()
    vectors[answer] = _vector(0.1)
    tracer = build_tracer(vectors)

    item = tracer.trace(answer, context_two_chunks())[0]
    assert item['confidence_level'] == 'drift'
    assert item['is_drift'] is True
    assert item['drift_reason']



# ---------------------------------------------------------------------------
# Failure state: unverified != hallucination
# ---------------------------------------------------------------------------

def test_embedding_failure_is_unverified_not_no_source():
    tracer = build_tracer({})            # nothing can be embedded
    answer = '第一句。第二句。'

    items = tracer.trace(answer, context_two_chunks())
    assert len(items) == 2
    for item in items:
        assert item['confidence_level'] == 'unverified'
        assert item['tracing_error'] is True
        assert item['is_drift'] is False
        assert item['attribution'] == 'none'
        assert answer[item['start']:item['end']] == item['sentence']


def test_unverified_sentences_are_excluded_from_the_drift_rate():
    tracer = build_tracer({})
    items = tracer.trace('只有一句。', context_two_chunks())
    analysis = tracer.analyze_drift(items)

    assert analysis['total_sentences'] == 1
    assert analysis['drift_count'] == 0
    assert analysis['drift_rate'] == 0
    assert analysis['unverified_count'] == 1
    assert analysis['unverified_rate'] == 1
    assert analysis['verified_count'] == 0
    assert analysis['verified_drift_rate'] == 0


def test_streaming_trace_single_matches_the_batch_trace():
    """The incremental verdict must equal the final verdict for the same sentence."""
    sentence = '签收后 7 天内可以申请退货退款。[文档1]'
    vectors = chunk_vectors()
    vectors[sentence] = _vector(0.95)
    tracer = build_tracer(vectors)
    chunk_index = tracer.build_chunk_index(context_two_chunks())
    citation_map = tracer.build_citation_map(context_two_chunks())

    batch_item = tracer.trace(sentence, context_two_chunks())[0]
    stream_item = tracer.trace_single(
        sentence, chunk_index, all_top1_for_adaptive=[],
        citation_map=citation_map, start=0, end=len(sentence), index=0,
    )

    assert stream_item['confidence_level'] == batch_item['confidence_level']
    assert stream_item['attribution'] == batch_item['attribution']
    assert stream_item['citations'] == batch_item['citations']
    assert stream_item['sources'][0]['chunk_id'] == batch_item['sources'][0]['chunk_id']
    assert (stream_item['start'], stream_item['end']) == (0, len(sentence))


def test_streaming_trace_single_reports_unverified_on_embedding_failure():
    tracer = build_tracer({})
    chunk_index = {'chunk-A': {'embedding': [1.0, 0.0, 0.0], 'text': CHUNK_A,
                               'metadata': {}, 'sentences': []}}

    item = tracer.trace_single('任意句子。', chunk_index)
    assert item['confidence_level'] == 'unverified'
    assert item['tracing_error'] is True
    assert item['is_drift'] is False


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def test_analyze_drift_counts_levels_and_keeps_legacy_alias():
    tracer = build_tracer({})
    items = [
        {'confidence_level': 'citation_verified', 'is_drift': False, 'sources': [{'score': 0.9}]},
        {'confidence_level': 'direct_quote', 'is_drift': False, 'sources': [{'score': 0.8}]},
        {'confidence_level': 'summary', 'is_drift': False, 'sources': [{'score': 0.6}]},
        {'confidence_level': 'low_confidence', 'is_drift': False, 'sources': [{'score': 0.5}]},
        {'confidence_level': 'drift', 'is_drift': True, 'sources': [{'score': 0.1}]},
        {'confidence_level': 'unverified', 'is_drift': False, 'tracing_error': True, 'sources': []},
    ]
    analysis = tracer.analyze_drift(items)

    assert analysis['total_sentences'] == 6
    assert analysis['citation_verified_count'] == 1
    assert analysis['direct_quote_count'] == 1
    assert analysis['summary_count'] == 1
    assert analysis['low_confidence_count'] == 1
    assert analysis['drift_count'] == 1
    # Alias kept for the log line / UI term map that read this name.
    assert analysis['drifted_count'] == 1
    assert analysis['unverified_count'] == 1
    assert analysis['verified_count'] == 5
    assert analysis['drift_rate'] == pytest.approx(1 / 6)
    assert analysis['verified_drift_rate'] == pytest.approx(1 / 5)
    assert analysis['high_confidence_rate'] == pytest.approx(3 / 6)


def test_confidence_labels_and_colors_cover_the_new_levels():
    tracer = build_tracer({})
    for level in ('citation_verified', 'unverified'):
        assert tracer.get_confidence_color(level) != tracer.get_confidence_color('unknown')
        assert tracer.get_confidence_label(level) != 'trace.citation_' + level


if __name__ == '__main__':
    import pytest as _pytest
    _pytest.main([__file__, '-v'])

