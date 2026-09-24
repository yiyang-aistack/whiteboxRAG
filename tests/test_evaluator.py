# -*- coding: utf-8 -*-
"""
RAG evaluator / rubric tests (core/evaluator.py).

Why these tests exist: the evaluator produced the number shown next to every answer and
had no test at all. Rubric v1 averaged raw ``value / target`` ratios, so a metric that
merely reached its pass line earned a full 1.0 (three of ten metrics were constant over
storage/traces: ``answer_faithfulness`` 61/67, ``retrieval_recall`` 57/67,
``answer_relevance`` 42/67 had saturated), the negative metrics earned 0 at the same
line, ``rejection_accuracy`` was a byte-for-byte duplicate of ``answer_faithfulness``,
an embedding outage reported 0.0 hallucinations (i.e. perfect), and ``is_passing`` came
from a second rule that could print "85 分 · 不通过".

The invariants pinned down here:

* every metric earns exactly ``PASS_CREDIT`` at its pass line, so ``is_pass`` and
  "credit >= PASS_CREDIT" are the same statement and can never disagree again;
* a metric that cannot be measured scores 0 (fail closed) instead of being dropped from
  both the mean and the denominator;
* a not-applicable metric drops out and its weight is redistributed, the lost weight
  being reported as ``weight_coverage``;
* ``is_passing`` is the score plus named gates, and every failure has a name;
* a refusal is recognised in both languages, and a scenario's own fallback text too.

Everything runs offline: embeddings are replaced by a dictionary of vectors, so no vector
store, model or network is involved.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import evaluator as evaluator_module  # noqa: E402
from core.evaluator import PASS_CREDIT, RAGEvaluator  # noqa: E402
from service.i18n import _  # noqa: E402

CHUNK = 'Refund policy: returns are accepted within 7 days of delivery.'
GOOD_ANSWER = 'Returns are accepted within 7 days of delivery [Document 1].'
ZH_REFUSAL = '抱歉，未找到相关信息。'
GLOBAL_FALLBACK = ("Sorry, I couldn't find any relevant information in the knowledge base. "
                   "Please try your question description or upload related documents.")


def build_evaluator(vectors=None) -> RAGEvaluator:
    """Evaluator whose embeddings come from ``vectors`` (unknown text -> the default).

    Most tests only need *some* deterministic vector: the similarity of two identical
    default vectors is 1.0, which is what "the answer matches the context" should look
    like. Pass ``vectors`` to make a specific text dissimilar.
    """
    evaluator = RAGEvaluator()
    mapping = {key: list(value) for key, value in (vectors or {}).items()}
    default = mapping.get('*', [1.0, 0.0])

    def fake_embedding(text: str):
        return list(mapping.get((text or '').strip(), default))

    evaluator._get_embedding = fake_embedding
    return evaluator


def context_one_chunk() -> list:
    return [{'id': 'chunk-1', 'text': CHUNK, 'score': 0.8}]


def context_two_chunks() -> list:
    return context_one_chunk() + [{'id': 'chunk-2', 'text': 'Warranty: 12 months.', 'score': 0.6}]


def metrics_at_pass_line(evaluator: RAGEvaluator) -> dict:
    """One entry per metric, each sitting exactly on its pass line."""
    metrics = {}
    for name, target in evaluator._default_targets.items():
        value = (target['min'] + target['max']) / 2 if isinstance(target, dict) else target
        metrics[name] = evaluator._metric_entry(name, value, target)
    return metrics


def probe_values(target):
    """Values to probe around a pass line, including both sides of it."""
    if isinstance(target, dict):
        low, high = float(target['min']), float(target['max'])
        width = max(1.0, high - low)
        return [low - width, low, (low + high) / 2, high, high + width, high + 10 * width]
    if isinstance(target, bool):
        return [True, False]
    return [0.0, target * 0.5, target, target * 1.5, target * 2.0, 1.0, 1.5]


# ---------------------------------------------------------------------------
# Credit anchors: the pass line is worth PASS_CREDIT in both directions
# ---------------------------------------------------------------------------

def test_higher_is_better_credit_is_anchored_at_the_pass_line():
    evaluator = build_evaluator()
    assert evaluator._normalize_metric_score('answer_faithfulness', 0.0, 0.7) == 0.0
    assert evaluator._normalize_metric_score('answer_faithfulness', 0.7, 0.7) == pytest.approx(PASS_CREDIT)
    assert evaluator._normalize_metric_score('answer_faithfulness', 0.35, 0.7) == pytest.approx(0.35)
    assert evaluator._normalize_metric_score('answer_faithfulness', 1.0, 0.7) == pytest.approx(1.0)
    # A pass line at the metric ceiling must not push the credit above 1.0.
    assert evaluator._normalize_metric_score('answer_faithfulness', 1.0, 1.0) == pytest.approx(PASS_CREDIT)


def test_lower_is_better_credit_meets_the_same_anchor():
    evaluator = build_evaluator()
    assert evaluator._normalize_metric_score('hallucination_rate', 0.0, 0.15) == pytest.approx(1.0)
    assert evaluator._normalize_metric_score('hallucination_rate', 0.075, 0.15) == pytest.approx(0.85)
    assert evaluator._normalize_metric_score('hallucination_rate', 0.15, 0.15) == pytest.approx(PASS_CREDIT)
    assert evaluator._normalize_metric_score('hallucination_rate', 0.30, 0.15) == 0.0
    assert evaluator._normalize_metric_score('hallucination_rate', 0.60, 0.15) == 0.0


def test_bool_and_band_metrics():
    evaluator = build_evaluator()
    assert evaluator._normalize_metric_score('empty_response', False, False) == 1.0
    assert evaluator._normalize_metric_score('empty_response', True, False) == 0.0

    band = {'min': 50, 'max': 2000}
    assert evaluator._normalize_metric_score('response_length', 500, band) == 1.0
    just_over = evaluator._normalize_metric_score('response_length', 2050, band)
    assert 0.0 < just_over < PASS_CREDIT, 'just outside the band must fail, not half-pass'
    assert evaluator._normalize_metric_score('response_length', 50 + 10 * 1950, band) == 0.0


def test_normalization_needs_both_a_value_and_a_target():
    evaluator = build_evaluator()
    assert evaluator._normalize_metric_score('answer_relevance', None, 0.5) is None
    assert evaluator._normalize_metric_score('answer_relevance', 0.5, None) is None
    # A degenerate pass line carries no scale; whatever was measured is acceptable.
    assert evaluator._normalize_metric_score('answer_relevance', 0.2, 0.0) == 1.0


def test_pass_and_credit_can_never_disagree():
    """The invariant the whole rubric rests on, checked for every registered metric."""
    evaluator = build_evaluator()
    for name in evaluator._metrics:
        target = evaluator._default_targets[name]
        for value in probe_values(target):
            credit = evaluator._normalize_metric_score(name, value, target)
            passed = evaluator._check_pass(name, value, target)
            assert credit is not None, (name, value)
            assert passed == (credit >= PASS_CREDIT - 1e-9), (name, value, credit, passed)


def test_every_registered_metric_has_metadata_and_weight():
    """A new metric without a direction/weight would silently behave as "higher is better"."""
    evaluator = build_evaluator()
    for name in evaluator._metrics:
        assert name in evaluator_module._METRIC_DIRECTION, name
        assert name in evaluator_module._METRIC_GROUP, name
        assert name in evaluator._default_targets, name
        assert evaluator._weights.get(name, 0.0) > 0.0, name
    assert sum(evaluator._weights.values()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Weighted mean, weight coverage and the named gates
# ---------------------------------------------------------------------------

def test_a_metric_on_its_pass_line_earns_exactly_pass_credit():
    evaluator = build_evaluator()
    metrics = metrics_at_pass_line(evaluator)
    score, breakdown, coverage = evaluator._compute_overall_score(metrics)

    for name, part in breakdown.items():
        direction = evaluator_module._METRIC_DIRECTION[name]
        # bool/band metrics are not graded: "not empty" and "inside the band" are simply
        # right, and their pass line is the only passing value.
        expected = 1.0 if direction in ('bool', 'band') else PASS_CREDIT
        assert part['credit'] == pytest.approx(expected), name

    assert coverage == pytest.approx(1.0)
    assert len(breakdown) == len(metrics)
    assert score >= PASS_CREDIT
    # contributions are the weighted credits: they must add up to the score itself
    assert sum(part['contribution'] for part in breakdown.values()) == pytest.approx(score)


def test_not_applicable_metric_drops_out_and_its_weight_is_redistributed():
    """v1 fell back to another metric here; v2 reports "we did not measure this"."""
    evaluator = build_evaluator()
    metrics = metrics_at_pass_line(evaluator)
    baseline, _, baseline_coverage = evaluator._compute_overall_score(metrics)

    weight = metrics['rejection_accuracy']['weight']
    metrics['rejection_accuracy'] = evaluator._metric_entry(
        'rejection_accuracy', None, metrics['rejection_accuracy']['target'])

    score, breakdown, coverage = evaluator._compute_overall_score(metrics)
    assert 'rejection_accuracy' not in breakdown
    assert coverage == pytest.approx(baseline_coverage - weight)

    # The score stays the weighted mean of the remaining parts: its weight was
    # redistributed, not silently dropped from the denominator.
    available = sum(part['weight'] for part in breakdown.values())
    expected = sum(part['contribution'] for part in breakdown.values()) / available
    assert score == pytest.approx(expected)
    # Here the dropped metric sat *below* the rest (its pass line vs "not empty"/"in band"),
    # so redistributing its weight raises the mean -- a not-applicable metric is not a
    # punishment, it is an absence of information.
    assert score > baseline


def test_a_metric_that_could_not_be_measured_scores_zero():
    """Fail closed: "we could not check" must never beat "we checked and it is bad"."""
    evaluator = build_evaluator()
    metrics = metrics_at_pass_line(evaluator)
    metrics['answer_faithfulness'] = evaluator._metric_entry(
        'answer_faithfulness', None, metrics['answer_faithfulness']['target'],
        error='embedding backend unavailable')

    score, breakdown, coverage = evaluator._compute_overall_score(metrics)
    assert breakdown['answer_faithfulness']['status'] == 'error'
    assert breakdown['answer_faithfulness']['credit'] == 0.0
    assert coverage == pytest.approx(1.0), 'an erroring metric is configured *and* available'
    assert score < PASS_CREDIT


def test_gates_are_named_and_decode_the_verdict():
    evaluator = build_evaluator()
    metrics = metrics_at_pass_line(evaluator)
    score, _, coverage = evaluator._compute_overall_score(metrics)

    passed, gates = evaluator._check_overall_passing(score, metrics, coverage)
    assert passed and all(gates.values())

    with_empty = dict(metrics)
    with_empty['empty_response'] = evaluator._metric_entry('empty_response', True, False)
    passed, gates = evaluator._check_overall_passing(score, with_empty, coverage)
    assert not passed and gates['not_empty_response'] is False

    with_hallucination = dict(metrics)
    with_hallucination['hallucination_rate'] = evaluator._metric_entry(
        'hallucination_rate', 0.9, evaluator._default_targets['hallucination_rate'])
    passed, gates = evaluator._check_overall_passing(score, with_hallucination, coverage)
    assert not passed and gates['hallucination_within_limit'] is False

    passed, gates = evaluator._check_overall_passing(score, metrics, 0.5)
    assert not passed and gates['weight_coverage'] is False

    passed, gates = evaluator._check_overall_passing(PASS_CREDIT - 0.01, metrics, 1.0)
    assert not passed and gates['score'] is False


def test_verdict_and_score_are_the_same_rubric():
    """The v1 bug this replaces: 9 of 72 stored traces read "8x 分 · 不通过".

    With the pass line anchored at PASS_CREDIT, a score on the line always passes and
    anything below it never does -- the only reasons for a failure are the named gates.
    """
    evaluator = build_evaluator()
    metrics = metrics_at_pass_line(evaluator)
    for score, coverage in ((0.95, 1.0), (0.85, 0.9), (0.7, 1.0), (0.69, 1.0), (0.95, 0.5)):
        passed, gates = evaluator._check_overall_passing(score, metrics, coverage)
        assert passed == all(gates.values())
        assert passed == (score >= evaluator.pass_score
                          and coverage >= evaluator.min_weight_coverage), (score, coverage)


# ---------------------------------------------------------------------------
# End to end: evaluate() on a stubbed embedding backend
# ---------------------------------------------------------------------------

def test_a_grounded_answer_scores_high_and_passes():
    evaluator = build_evaluator()
    result = evaluator.evaluate('What is the refund window?', context_one_chunk(), GOOD_ANSWER)

    assert result['rubric_version']
    assert result['overall_score'] > 0.95, result['score_breakdown']
    assert result['is_passing'] is True
    assert result['gate_failures'] == []
    assert result['quality_flags'] == []

    assert result['metrics']['answer_faithfulness']['value'] == pytest.approx(1.0)
    assert result['metrics']['citation_coverage']['value'] == pytest.approx(1.0)
    assert result['metrics']['empty_response']['value'] is False
    # the retrieval average is folded in by weight, not saturating at 1.0 by accident
    assert result['score_breakdown']['retrieval_score_avg']['credit'] == pytest.approx(0.88)
    # not applicable: one chunk has no spread, and the question is in-domain
    assert result['metrics']['retrieval_score_std']['value'] is None
    assert result['metrics']['rejection_accuracy']['value'] is None
    assert result['weight_coverage'] == pytest.approx(0.9)


def test_no_context_is_a_zero_score_with_an_explicit_gate():
    evaluator = build_evaluator()
    result = evaluator.evaluate('Anything?', [], GLOBAL_FALLBACK)

    assert result['overall_score'] == 0.0
    assert result['is_passing'] is False
    assert result['gate_failures'] == ['retrieved_context']
    assert result['metrics']['empty_response']['value'] is True
    assert result['quality_flags'], 'a zero score must carry a human readable reason'


def test_refusals_are_recognised_in_both_languages():
    """v1 matched English substrings only, so a zh-CN refusal was scored as an answer."""
    evaluator = build_evaluator()
    for answer in (ZH_REFUSAL, GLOBAL_FALLBACK, 'Sorry, no relevant information found.'):
        result = evaluator.evaluate('Q', context_one_chunk(), answer)
        assert result['metrics']['empty_response']['value'] is True, answer
        assert result['is_passing'] is False, answer


def test_a_scenario_specific_fallback_counts_as_a_refusal():
    """The scenario's own `retriever.empty_response` text was not in v1's phrase list."""
    evaluator = build_evaluator()
    answer = ("Sorry, I couldn't find a relevant answer. "
              "Please contact customer service for help.")
    result = evaluator.evaluate('Q', context_one_chunk(), answer, scenario_id='customer_service')
    assert result['metrics']['empty_response']['value'] is True
    assert result['is_passing'] is False


def test_a_long_answer_that_merely_mentions_a_refusal_is_not_empty():
    evaluator = build_evaluator()
    long_answer = ('The refund window is seven days from delivery. ' * 12) \
        + 'Sorry, no relevant information found.'
    assert evaluator.evaluate('Q', context_one_chunk(),
                              long_answer)['metrics']['empty_response']['value'] is False

    # Mid-length answers matter too: the refusal has to dominate, not just appear.
    medium = ('The refund window is seven days from delivery and the request must be filed '
              'in the order page. We cannot answer questions about orders placed elsewhere.')
    result = evaluator.evaluate('Q', context_one_chunk(), medium)
    assert len(medium) > 120
    assert result['metrics']['empty_response']['value'] is False


def test_citation_coverage_needs_a_resolvable_marker():
    """`[Document 9]` with two chunks in the prompt is not a citation (same rule as the tracer)."""
    evaluator = build_evaluator()
    two_chunks = context_two_chunks()
    resolvable = evaluator.evaluate('Q', two_chunks, 'Returns within 7 days [Document 1].')
    assert resolvable['metrics']['citation_coverage']['value'] == pytest.approx(1.0)

    out_of_range = evaluator.evaluate('Q', two_chunks, 'Returns within 7 days [Document 9].')
    assert out_of_range['metrics']['citation_coverage']['value'] == pytest.approx(0.0)

    uncited = evaluator.evaluate('Q', two_chunks, 'Returns within 7 days.')
    assert uncited['metrics']['citation_coverage']['value'] == pytest.approx(0.0)
    assert uncited['metrics']['citation_coverage']['is_pass'] is False


def test_in_domain_rejection_accuracy_is_not_a_second_faithfulness():
    """v1 returned `_compute_semantic_faithfulness` here: identical in 67/67 sampled traces."""
    evaluator = build_evaluator()
    result = evaluator.evaluate('Q', context_one_chunk(), GOOD_ANSWER,
                                boundary_result={'in_domain': True, 'confidence': 0.9})
    assert result['metrics']['rejection_accuracy']['value'] is None
    assert result['metrics']['rejection_accuracy']['is_pass'] is None
    assert result['metrics']['answer_faithfulness']['value'] is not None


def test_out_of_domain_rejection_is_the_only_case_that_metric_scores():
    evaluator = build_evaluator()
    boundary = {'in_domain': False, 'confidence': 0.9}
    verbatim = _('pipeline.boundary_rejected_msg', 'zh-CN')
    shortened = '抱歉，您的问题不在我的业务范围内，请咨询相关业务部门。'

    # Out of domain and no retrieval at all: the refusal gate answers, not the score.
    refused = evaluator.evaluate('Q', [], verbatim, boundary_result=boundary)
    assert refused['metrics']['empty_response']['value'] is True

    # The refusal text is often echoed in a shorter form by the model: the keyword signal
    # plus the short-answer rule must still recognise it (v1 knew six English keywords).
    for answer in (verbatim, shortened):
        result = evaluator.evaluate('Q', context_one_chunk(), answer, boundary_result=boundary)
        assert result['metrics']['empty_response']['value'] is True, answer
        assert result['metrics']['rejection_accuracy']['value'] == pytest.approx(1.0), answer
        assert result['metrics']['rejection_accuracy']['is_pass'] is True, answer


def test_an_embedding_outage_fails_closed_on_the_safety_metric():
    """v1 reported 0.0 here, i.e. a *perfect* hallucination score while embeddings were down."""
    healthy = build_evaluator().evaluate('Q', context_one_chunk(), GOOD_ANSWER)
    broken = build_evaluator({CHUNK: []}).evaluate('Q', context_one_chunk(), GOOD_ANSWER)

    hallucination = broken['metrics']['hallucination_rate']
    assert hallucination['value'] is None and 'error' in hallucination
    assert broken['score_breakdown']['hallucination_rate']['status'] == 'error'
    assert broken['score_breakdown']['hallucination_rate']['credit'] == 0.0
    assert broken['overall_score'] < healthy['overall_score']


def test_adjustments_are_recorded_with_the_score_before_and_after():
    evaluator = build_evaluator()

    capped = evaluator.evaluate('Q', [{'id': 'c', 'text': CHUNK, 'score': 0.05}], GOOD_ANSWER)
    assert capped['adjustments'][0]['reason'] == 'retrieval_quality_low'
    assert capped['adjustments'][0]['score_before'] > capped['adjustments'][0]['score_after']
    assert capped['overall_score'] == pytest.approx(evaluator.low_retrieval_cap)
    assert capped['raw_score'] > capped['overall_score']
    assert capped['is_passing'] is False

    low_confidence = evaluator.evaluate('Q', context_one_chunk(), GOOD_ANSWER,
                                        boundary_result={'in_domain': True, 'confidence': 0.2})
    adjustment = low_confidence['adjustments'][0]
    assert adjustment['reason'] == 'boundary_confidence_low'
    assert adjustment['detail']['factor'] == pytest.approx(0.5)
    assert adjustment['score_after'] == pytest.approx(adjustment['score_before'] * 0.5, abs=1e-3)
    assert low_confidence['quality_flags']
    assert low_confidence['boundary_quality'] == '; '.join(low_confidence['quality_flags'])


def test_scenario_metric_allowlist_narrows_the_scored_set(monkeypatch):
    """`evaluation.metrics` in the scenario YAML used to be dead config."""
    evaluator = build_evaluator()
    fake_config = {'evaluation': {'metrics': ['answer_relevance', 'does_not_exist'],
                                  'target_values': {'answer_relevance': 0.5}}}
    monkeypatch.setattr(type(evaluator_module.scenario_config), 'get_effective_config',
                        lambda self, scenario_id: fake_config)

    result = evaluator.evaluate('Q', context_one_chunk(), GOOD_ANSWER,
                                scenario_id='technical_doc')
    # gate metrics stay, unknown names are ignored, and the scenario pass line wins
    assert set(result['metrics']) == {'answer_relevance', 'empty_response', 'hallucination_rate'}
    assert result['metrics']['hallucination_rate']['value'] is not None
    assert result['metrics']['answer_relevance']['target'] == 0.5


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
