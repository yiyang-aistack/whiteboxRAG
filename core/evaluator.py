"""
RAG Evaluator Module
Used to compute evaluation metrics such as retrieval quality and answer faithfulness
Supports semantic-level evaluation (Embedding-based similarity computation)

Rubric v2
---------
Rubric v1 averaged raw ``value / target`` ratios with an unweighted mean and derived
``is_passing`` from a second, unrelated rule (70% of the metrics passing). The default
pass lines were low enough that three of ten metrics saturated at a full 1.0 for almost
every answer (``answer_faithfulness`` 61/67, ``retrieval_recall`` 57/67,
``answer_relevance`` 42/67 over storage/traces), a metric that *reached* its pass line
earned a full 1.0 when "higher is better" but 0.0 when "lower is better", one dimension
(answer vs context) was measured by four separate metrics, and the number on screen
could contradict the verdict printed next to it ("85% · Failed").

Rubric v2 keeps the metric set but normalizes every metric onto one credit scale
anchored at two points, so "this metric passed" and "this metric earned >= 0.7" become
the same statement:

    value == pass line                                           -> PASS_CREDIT (0.7)
    ideal value (1.0 for the ratio metrics, 0 for negative ones) -> 1.0 / 0.0

``overall_score`` is the *weighted* mean of those credits (weights are configurable and
renormalized over whatever was measurable) and ``is_passing`` is that same score plus a
few explicit safety gates. Every result carries ``rubric_version``, so a score is never
compared against a score produced by a different rubric. See ``evaluation:`` in
config/settings.yaml, ``scripts/evaluator_recalc.py`` (offline calibration harness) and
tests/test_evaluator.py (the invariants above).
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from config import config, scenario_config
from service.i18n import _
from service.logger import get_logger
from service.text_split import CITATION_MARKER_RE, split_sentences

logger = get_logger('evaluator')

#: Credit a metric earns when it exactly reaches its pass line. One number for the whole
#: rubric is what keeps ``is_pass`` and the score from telling different stories.
PASS_CREDIT = 0.7

#: For "lower is better" metrics the credit reaches 0 at this multiple of the pass line
#: (twice the tolerated hallucination rate earns nothing).
_LOWER_WORST_RATIO = 2.0

#: Metric metadata: ``direction`` drives the normalization, ``group`` labels the
#: dimension a metric belongs to (used by the score breakdown).
_METRIC_DIRECTION = {
    'retrieval_score_avg': 'higher',
    'retrieval_score_std': 'lower',
    'answer_faithfulness': 'higher',
    'semantic_consistency': 'higher',
    'citation_coverage': 'higher',
    'answer_relevance': 'higher',
    'hallucination_rate': 'lower',
    'rejection_accuracy': 'higher',
    'empty_response': 'bool',
    'response_length': 'band',
}

_METRIC_GROUP = {
    'retrieval_score_avg': 'retrieval',
    'retrieval_score_std': 'retrieval',
    'answer_faithfulness': 'grounding',
    'semantic_consistency': 'grounding',
    'citation_coverage': 'grounding',
    'answer_relevance': 'relevance',
    'hallucination_rate': 'safety',
    'rejection_accuracy': 'safety',
    'empty_response': 'format',
    'response_length': 'format',
}

#: Default weights; config/settings.yaml ``evaluation.weights`` overrides them and the
#: scored subset is renormalized to 1.0. Grounding is deliberately the heaviest group: it
#: is what the product sells ("every sentence is traceable"), and rubric v1 counted it
#: four times by accident (answer_faithfulness, semantic_consistency, hallucination_rate
#: and rejection_accuracy were all the same answer-vs-context signal).
_DEFAULT_WEIGHTS = {
    'retrieval_score_avg': 0.10,
    'retrieval_score_std': 0.05,
    'answer_faithfulness': 0.22,
    'semantic_consistency': 0.13,
    'citation_coverage': 0.10,
    'answer_relevance': 0.15,
    'hallucination_rate': 0.15,
    'rejection_accuracy': 0.05,
    'empty_response': 0.03,
    'response_length': 0.02,
}

#: Default pass lines: the value a reviewer would call "acceptable", NOT the system's
#: current average (a line drawn at the average is meaningless by construction). Chosen
#: with scripts/evaluator_recalc.py over the 72 traces in storage/traces: every line keeps
#: a defensible pass rate (36-96% per metric) instead of the ~90% saturation of rubric v1,
#: and ``retrieval_score_avg`` is anchored at the retriever's own similarity threshold, so
#: tuning that threshold keeps the two in step.
_DEFAULT_TARGETS = {
    'retrieval_score_avg': 0.50,
    'retrieval_score_std': 0.10,
    'answer_faithfulness': 0.70,
    'semantic_consistency': 0.50,
    'citation_coverage': 0.45,
    'answer_relevance': 0.50,
    'hallucination_rate': 0.15,
    'rejection_accuracy': 0.80,
    'empty_response': False,
    'response_length': {'min': 50, 'max': 2000},
}

#: Metrics that are always evaluated even when a scenario narrows the metric set: they are
#: pass gates, not preferences (an empty answer, or an answer with too many unsupported
#: sentences, must fail whatever else the scenario cares about).
_GATE_METRICS = frozenset({'empty_response', 'hallucination_rate'})

#: Translation keys whose text is a refusal / "nothing found" answer. Both languages are
#: always checked, because a zh-CN answer to an en-US question (and vice versa) must still
#: be recognised as a refusal -- rubric v1 matched English substrings only, and missed the
#: refusal text a scenario configures in ``retriever.empty_response``.
_REFUSAL_KEYS = (
    'pipeline.empty_response',
    'pipeline.boundary_rejected_msg',
    'pipeline.status_boundary_rejected',
)
_RUBRIC_LANGUAGES = ('zh-CN', 'en-US')

#: Secondary signal for refusals that are *reworded* or shortened by the model instead of
#: returned verbatim (the boundary rejection text, for instance, is often echoed in a
#: shorter form). Only counted when it dominates the answer or the answer is short, so a
#: long answer that merely mentions "cannot answer" is still an answer.
_REFUSAL_KEYWORDS = (
    #'不在我的业务范围', '超出业务范围', '业务范围外', '无法回答', '未找到相关', '没有找到相关',
    'out of scope', 'beyond scope', 'outside my business scope', 'cannot answer',
    'no relevant information', "couldn't find any relevant", 'not found in the knowledge base',
)

#: A matched refusal phrase must cover this share of the answer (or the answer must be at
#: most _REFUSAL_SHORT_ANSWER characters) before the answer counts as "no relevant
#: information": real refusals are short, real answers that mention a refusal are not.
_REFUSAL_DOMINANCE = 0.4
_REFUSAL_SHORT_ANSWER = 120


class MetricUnavailableError(RuntimeError):
    """A metric could not be measured at all (e.g. the embedding backend is down).

    Such a metric is scored 0 instead of being dropped: rubric v1 removed failing metrics
    from the mean *and* from the pass-rate denominator, so a broken backend made an answer
    look better than a verified one.
    """


@dataclass
class EvaluationContext:
    """Everything a metric may look at, so every metric shares one signature."""

    query: str
    context_results: List[Dict]
    answer: str
    scenario_id: Optional[str] = None
    lang: Optional[str] = None
    boundary_result: Optional[Dict] = None
    refusal_phrases: Tuple[str, ...] = ()
    hallucination_sim_floor: float = 0.35


class RAGEvaluator:
    """RAG evaluator (rubric v2)"""

    def __init__(self):
        self._metrics = {
            'retrieval_score_avg': self._compute_retrieval_score_avg,
            'retrieval_score_std': self._compute_retrieval_score_std,
            'answer_faithfulness': self._compute_semantic_faithfulness,
            'semantic_consistency': self._compute_semantic_consistency,
            'citation_coverage': self._compute_citation_coverage,
            'answer_relevance': self._compute_semantic_relevance,
            'hallucination_rate': self._compute_hallucination_rate,
            'rejection_accuracy': self._compute_rejection_accuracy,
            'empty_response': self._compute_empty_response,
            'response_length': self._compute_response_length,
        }

        # ===== Rubric configuration (config/settings.yaml `evaluation:`) =====
        self.rubric_version = str(config.get('evaluation.rubric_version', '2.0'))
        self.pass_score = float(config.get('evaluation.pass_score', PASS_CREDIT))
        self.min_weight_coverage = float(config.get('evaluation.min_weight_coverage', 0.6))
        self.hallucination_sim_floor = float(config.get('evaluation.hallucination_sim_floor', 0.35))
        # Low-retrieval cap: gate = retriever.similarity_threshold * retrieval_gate_ratio
        self.retrieval_gate_ratio = float(config.get('evaluation.retrieval_gate_ratio', 0.3))
        self.low_retrieval_cap = float(config.get('evaluation.low_retrieval_cap', 0.3))
        self.boundary_confidence_min = float(config.get('evaluation.boundary_confidence_min', 0.5))
        self.boundary_penalty_floor = float(config.get('evaluation.boundary_penalty_floor', 0.5))
        # `evaluation.weights` / `evaluation.target_values` take precedence over the
        # module defaults above; scenarios may override target_values per metric.
        self._weights = {**_DEFAULT_WEIGHTS, **(config.get('evaluation.weights') or {})}
        self._default_targets = {**_DEFAULT_TARGETS, **(config.get('evaluation.target_values') or {})}

        self._embedding_model = config.get_embedding_model()

    def evaluate(
        self,
        query: str,
        context_results: List[Dict],
        answer: str,
        scenario_id: Optional[str] = None,
        boundary_result: Optional[Dict] = None,
        lang: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute evaluation

        Args:
            query: User query
            context_results: Retrieval context result list
            answer: Answer text
            scenario_id: Scenario ID (target values + optional metric allowlist)
            boundary_result: Boundary detection result (from boundary_detector.detect())
            lang: Request language, used for the human readable quality flags

        Returns:
            Evaluation result dictionary. Rubric v2 fields on top of the v1 ones:

            * ``rubric_version`` -- rubric that produced the numbers; scores from two
              rubrics are never comparable (storage/traces keeps both).
            * ``metrics[name]`` -- ``value`` / ``target`` / ``is_pass`` plus
              ``direction``, ``group`` and ``weight``, so the score is explainable.
            * ``score_breakdown`` -- per-metric credit and its weighted contribution.
            * ``raw_score`` -- the weighted mean before the two adjustments below, so a
              capped score can still be attributed.
            * ``weight_coverage`` -- share of the configured weight that was actually
              measurable; an unverifiable answer can no longer look better than a
              verified one (rubric v1 dropped failing metrics from the mean).
            * ``gates`` / ``gate_failures`` -- the explicit reasons behind
              ``is_passing``, which is now derived from ``overall_score`` instead of a
              separate "70% of the metrics" rule that could print "85 分 · 不通过".
            * ``adjustments`` -- low-retrieval cap and boundary-confidence penalty, each
              with the score before/after.
        """
        results = {
            'rubric_version': self.rubric_version,
            'query_length': len(query),
            'context_count': len(context_results),
            'answer_length': len(answer),
            'scenario_id': scenario_id,
            'metrics': {},
            'score_breakdown': {},
            'weight_coverage': None,
            'raw_score': None,
            'overall_score': None,
            'is_passing': None,
            'gates': {},
            'gate_failures': [],
            'quality_flags': [],
            'adjustments': [],
            # Kept for callers written against rubric v1: the quality flags as one string.
            'boundary_quality': None,
        }

        if not context_results:
            # Nothing was retrieved, so the answer can only be the configured fallback
            # text and no quality claim is possible: the empty-response gate decides.
            entry = self._metric_entry('empty_response', True, False)
            entry['is_pass'] = False
            results['metrics']['empty_response'] = entry
            results['raw_score'] = 0.0
            results['overall_score'] = 0.0
            results['weight_coverage'] = 0.0
            results['gates'] = {'retrieved_context': False}
            results['gate_failures'] = ['retrieved_context']
            results['is_passing'] = False
            results['quality_flags'] = [_('eval.no_context', lang)]
            results['boundary_quality'] = results['quality_flags'][0]
            return results

        scenario_targets = self._get_scenario_targets(scenario_id)
        allowlist = self._get_scenario_metric_allowlist(scenario_id)
        context = EvaluationContext(
            query=query,
            context_results=context_results,
            answer=answer,
            scenario_id=scenario_id,
            lang=lang,
            boundary_result=boundary_result,
            refusal_phrases=self._refusal_phrases(scenario_id),
            hallucination_sim_floor=self.hallucination_sim_floor,
        )

        for metric_name, compute_func in self._metrics.items():
            # A scenario may narrow the metric set it cares about (`evaluation.metrics`
            # in config/scenarios/*.yaml). The gate metrics are always evaluated: they
            # are safety verdicts, not preferences.
            if allowlist is not None and metric_name not in allowlist:
                continue
            target = scenario_targets.get(metric_name)
            try:
                value = compute_func(context)
                results['metrics'][metric_name] = self._metric_entry(metric_name, value, target)
            except Exception as e:
                logger.error(f"Compute metric {metric_name} failed: {e}", exc_info=True)
                results['metrics'][metric_name] = self._metric_entry(
                    metric_name, None, target, error=str(e))

        raw_score, breakdown, coverage = self._compute_overall_score(results['metrics'])
        results['score_breakdown'] = breakdown
        score = raw_score
        flags: List[str] = []

        # ===== Adjustment 1: low retrieval quality =====
        # Cap the score so that "garbage in, garbage out" cannot earn a high grade. The
        # gate is derived from the retriever's own similarity threshold (rubric v1
        # hardcoded 0.15 and therefore ignored the configured 0.5), so it follows the
        # retrieval configuration instead of silently drifting away from it.
        gate_value = self._retrieval_gate_value(scenario_id)
        retrieval_avg = results['metrics'].get('retrieval_score_avg', {}).get('value')
        if gate_value and retrieval_avg is not None and retrieval_avg < gate_value:
            capped = min(score, self.low_retrieval_cap)
            if capped < score:
                results['adjustments'].append({
                    'reason': 'retrieval_quality_low',
                    'detail': {'retrieval_score_avg': retrieval_avg, 'gate': round(gate_value, 4)},
                    'score_before': round(score, 4),
                    'score_after': round(capped, 4),
                })
                logger.warning(
                    f"[Evaluation] retrieval quality low (avg={retrieval_avg:.4f} < "
                    f"gate={gate_value:.4f}), score capped from {score:.4f} to {capped:.4f}")
                score = capped
            flags.append(_('eval.retrieval_quality_low', lang))

        # ===== Adjustment 2: low boundary confidence =====
        # A low-confidence in-domain judgement means both the retrieval and the answer are
        # questionable, so the score is scaled down. The reasons accumulate in a list --
        # rubric v1 wrote them into one string, so the second one silently erased the first.
        confidence = (boundary_result or {}).get('confidence')
        if boundary_result and isinstance(confidence, (int, float)) \
                and confidence < self.boundary_confidence_min:
            factor = max(self.boundary_penalty_floor, float(confidence))
            before = score
            score = score * factor
            results['adjustments'].append({
                'reason': 'boundary_confidence_low',
                'detail': {'confidence': round(float(confidence), 4), 'factor': round(factor, 4)},
                'score_before': round(before, 4),
                'score_after': round(score, 4),
            })
            logger.warning(
                f"[Evaluation] boundary confidence low ({confidence:.2f}), score adjusted "
                f"from {before:.4f} to {score:.4f}")
            flags.append(_('eval.boundary_low_confidence', lang, f"{float(confidence):.0%}"))

        results['raw_score'] = round(raw_score, 4)
        results['overall_score'] = round(score, 4)
        results['weight_coverage'] = round(coverage, 4)
        results['quality_flags'] = flags
        results['boundary_quality'] = '; '.join(flags) if flags else None

        # The verdict is the same number the UI shows, plus the safety gates -- there is
        # no second rubric that can disagree with it.
        passed, gates = self._check_overall_passing(
            results['overall_score'], results['metrics'], coverage)
        results['is_passing'] = passed
        results['gates'] = gates
        results['gate_failures'] = [name for name, ok in gates.items() if not ok]

        return results

    def _get_scenario_targets(self, scenario_id: Optional[str]) -> Dict[str, Any]:
        """Return the pass line of every metric.

        Layer order (last wins): ``_DEFAULT_TARGETS`` in this module ->
        ``evaluation.target_values`` in config/settings.yaml -> ``evaluation.target_values``
        of the scenario. An earlier version hardcoded the defaults in this method, so the
        documented ``target_values`` block of settings.yaml did nothing, and the low
        hardcoded lines made three metrics saturate at 1.0 for almost every answer.
        """
        targets = {}
        if scenario_id:
            effective_config = scenario_config.get_effective_config(scenario_id)
            evaluation = effective_config.get('evaluation', {}) or {}
            targets = evaluation.get('target_values', {}) or {}

        return {**self._default_targets, **targets}

    def _get_scenario_metric_allowlist(self, scenario_id: Optional[str]) -> Optional[set]:
        """Metrics a scenario asks for, or None when it does not narrow the set.

        ``evaluation.metrics`` in config/scenarios/*.yaml used to be documentation only:
        nothing read it, so a scenario could list metrics that do not exist
        (``answer_completeness``, ``code_quality``) and nobody noticed. Unknown names are
        now logged and ignored, and the gate metrics are always added back.
        """
        if not scenario_id:
            return None

        effective_config = scenario_config.get_effective_config(scenario_id)
        evaluation = effective_config.get('evaluation', {}) or {}
        listed = evaluation.get('metrics')
        if not listed:
            return None

        known = set(self._metrics) | set(_GATE_METRICS)
        unknown = [name for name in listed if name not in known]
        if unknown:
            logger.warning(
                f"[Evaluation] scenario '{scenario_id}' lists unknown metrics, ignored: {unknown}")

        return {name for name in listed if name in known} | set(_GATE_METRICS)

    def _retrieval_gate_value(self, scenario_id: Optional[str]) -> Optional[float]:
        """Similarity average below which the score is capped ("garbage in, garbage out").

        Derived from the threshold the retriever actually filtered with, so tuning
        ``retriever.similarity_threshold`` moves the gate along with it.
        """
        threshold = config.get('retriever.similarity_threshold')
        if scenario_id:
            effective = scenario_config.get_effective_config(scenario_id) or {}
            threshold = (effective.get('retriever') or {}).get('similarity_threshold', threshold)
        try:
            return float(threshold) * self.retrieval_gate_ratio
        except (TypeError, ValueError):
            return None

    def _refusal_phrases(self, scenario_id: Optional[str]) -> Tuple[str, ...]:
        """Texts that count as "no relevant information" in either supported language.

        An earlier version matched eight hardcoded English substrings, so a zh-CN refusal
        -- or the per-scenario ``retriever.empty_response`` fallback text -- was scored as
        a normal answer and even earned the empty-response gate.
        """
        phrases = set(_REFUSAL_KEYWORDS)
        for key in _REFUSAL_KEYS:
            for language in _RUBRIC_LANGUAGES:
                text = _(key, language)
                if text and text != key:
                    phrases.add(text.strip().lower())

        candidates = [config.get('retriever.empty_response')]
        if scenario_id:
            effective = scenario_config.get_effective_config(scenario_id) or {}
            candidates.append((effective.get('retriever') or {}).get('empty_response'))
        for text in candidates:
            if isinstance(text, str) and text.strip():
                phrases.add(text.strip().lower())

        return tuple(sorted(phrase for phrase in phrases if phrase))

    def _metric_entry(self, metric_name: str, value: Any, target: Any,
                      error: Optional[str] = None) -> Dict[str, Any]:
        """One metric row: value, pass line, verdict and the metadata behind them."""
        entry = {
            'value': round(value, 4) if isinstance(value, float) else value,
            'target': target,
            'is_pass': None if value is None else self._check_pass(metric_name, value, target),
            'direction': _METRIC_DIRECTION.get(metric_name, 'higher'),
            'group': _METRIC_GROUP.get(metric_name, 'other'),
            'weight': round(float(self._weights.get(metric_name, 0.0)), 4),
        }
        if error:
            entry['error'] = error
        return entry

    def _check_pass(self, metric_name: str, value: Any, target: Any) -> Optional[bool]:
        """Check whether a metric reached its pass line (``target``)"""
        if target is None:
            return None

        if metric_name == 'response_length' and isinstance(target, dict):
            min_len = target.get('min', 0)
            max_len = target.get('max', float('inf'))
            return min_len <= value <= max_len

        # Each comparator is the verbal form of the credit anchor in
        # ``_normalize_metric_score`` (pass line -> PASS_CREDIT): that is what keeps
        # ``is_pass`` and ``overall_score`` from telling two different stories.
        comparison_map = {
            'retrieval_score_avg': lambda v, t: v >= t,
            'retrieval_score_std': lambda v, t: v <= t,
            'answer_faithfulness': lambda v, t: v >= t,
            'semantic_consistency': lambda v, t: v >= t,
            'citation_coverage': lambda v, t: v >= t,
            'answer_relevance': lambda v, t: v >= t,
            'hallucination_rate': lambda v, t: v <= t,
            'rejection_accuracy': lambda v, t: v >= t,
            'empty_response': lambda v, t: v == t,
            'response_length': lambda v, t: v >= t,
        }

        comparator = comparison_map.get(metric_name)
        if comparator:
            return comparator(value, target)
        return None

    def _check_overall_passing(self, score: float, metrics: Dict[str, Dict],
                               weight_coverage: float) -> Tuple[bool, Dict[str, bool]]:
        """Verdict = the score the UI shows, plus a few explicit safety gates.

        Rubric v1 derived the verdict from "70% of the metrics passed" while the score
        came from somewhere else, so one trace could read "85 分 · 不通过" (9 of the 72
        traces in storage/traces did). The gates are named, so the reason is visible:

        * ``score`` -- the weighted mean reached ``evaluation.pass_score``.
        * ``weight_coverage`` -- enough of the rubric was actually measurable. A metric
          that failed to compute is no longer a way to make a bad answer pass.
        * ``not_empty_response`` -- the answer is not the "nothing found" fallback.
        * ``hallucination_within_limit`` -- safety veto on the hallucination rate.
        """
        gates = {
            'score': bool(score is not None and score >= self.pass_score),
            'weight_coverage': bool(weight_coverage is not None
                                    and weight_coverage >= self.min_weight_coverage),
        }

        empty = metrics.get('empty_response')
        if empty is not None and empty.get('is_pass') is not None:
            gates['not_empty_response'] = bool(empty['is_pass'])

        hallucination = metrics.get('hallucination_rate')
        if hallucination is not None and hallucination.get('is_pass') is not None:
            gates['hallucination_within_limit'] = bool(hallucination['is_pass'])

        return all(gates.values()), gates

    def _normalize_metric_score(self, metric_name: str, value: Any, target: Any) -> Optional[float]:
        """Credit in [0, 1] for one metric, anchored at the pass line (``PASS_CREDIT``).

        The anchors are what make "this metric passed" and "this metric earned >= 0.7" the
        same statement:

        * higher is better: ``0 -> 0``, ``target -> 0.7``, ``1.0 -> 1.0``
        * lower is better: ``0 -> 1.0``, ``target -> 0.7``, ``2 x target -> 0``
        * bool (``empty_response``): ``False -> 1.0``, ``True -> 0``
        * band (``response_length``): inside the band -> 1.0, outside it decays from the
          pass credit down to 0, so a value just outside the band earns < 0.7

        Rubric v1 used ``min(1, value / target)``, which handed a *full* 1.0 to a positive
        metric that merely touched its pass line (with the low default lines that pinned
        three metrics at 1.0 for almost every trace) and scored the negative metrics 0 at
        their pass line -- exactly the opposite convention in one table.
        """
        if value is None or target is None:
            return None

        direction = _METRIC_DIRECTION.get(metric_name, 'higher')

        if direction == 'bool':
            return 0.0 if value else 1.0

        if direction == 'band':
            return self._band_credit(value, target) if isinstance(target, dict) else None

        try:
            value = float(value)
            target = float(target)
        except (TypeError, ValueError):
            return None

        if target <= 0:
            # A pass line at (or below) zero carries no scale: whatever was measured is fine.
            return 1.0

        if direction == 'lower':
            ratio = value / target
            if ratio <= 1.0:
                return min(1.0, PASS_CREDIT + (1.0 - PASS_CREDIT) * (1.0 - ratio))
            return max(0.0, PASS_CREDIT * (1.0 - (ratio - 1.0) / (_LOWER_WORST_RATIO - 1.0)))

        # higher is better; the ratio metrics all live in [0, 1], so 1.0 is the ideal value
        if value >= target:
            span = max(1e-9, 1.0 - target)
            return min(1.0, PASS_CREDIT + (1.0 - PASS_CREDIT) * (value - target) / span)
        return max(0.0, PASS_CREDIT * value / target)

    @staticmethod
    def _band_credit(value: Any, target: Dict[str, Any]) -> Optional[float]:
        """Credit for a "must stay inside [min, max]" metric (``response_length``)."""
        try:
            value = float(value)
            min_len = float(target.get('min', 0))
            max_len = float(target.get('max', float('inf')))
        except (TypeError, ValueError):
            return None

        if min_len <= value <= max_len:
            return 1.0

        width = (max_len - min_len) if max_len != float('inf') else min_len
        width = max(1.0, width)
        distance = (min_len - value) if value < min_len else (value - max_len)
        # Outside the band the credit is by construction below the pass credit.
        return max(0.0, PASS_CREDIT * (1.0 - distance / width))

    def _compute_overall_score(self, metrics: Dict[str, Dict]) -> Tuple[float, Dict[str, Dict], float]:
        """Weighted mean of the per-metric credits.

        Returns ``(score, breakdown, weight_coverage)``:

        * a metric that could not be measured at all (``error``) scores 0 -- fail closed,
          because "we could not check" must never outrank "we checked and it is bad";
        * a metric that is not applicable (no value, no error) drops out and its weight is
          redistributed over the rest -- ``rejection_accuracy`` on an in-domain question,
          for instance, instead of pretending to be ``answer_faithfulness``;
        * ``weight_coverage`` is the share of the configured weight that was measurable,
          and the pass verdict requires enough of it, so dropping metrics is not a way to
          raise a score (rubric v1 removed them from the mean *and* from the denominator).
        """
        breakdown: Dict[str, Dict] = {}
        weighted = 0.0
        available_weight = 0.0
        configured_weight = 0.0

        for metric_name, metric in metrics.items():
            weight = float(metric.get('weight') or 0.0)
            if weight <= 0:
                continue
            configured_weight += weight

            value = metric.get('value')
            target = metric.get('target')
            if metric.get('error'):
                credit, status = 0.0, 'error'
            elif value is None or target is None:
                continue
            else:
                credit = self._normalize_metric_score(metric_name, value, target)
                if credit is None:
                    continue
                status = 'scored'

            available_weight += weight
            weighted += weight * credit
            breakdown[metric_name] = {
                'group': metric.get('group'),
                'weight': round(weight, 4),
                'value': metric.get('value'),
                'target': metric.get('target'),
                'credit': round(credit, 4),
                'contribution': round(weight * credit, 4),
                'status': status,
            }

        coverage = (available_weight / configured_weight) if configured_weight else 0.0
        score = (weighted / available_weight) if available_weight else 0.0
        return score, breakdown, coverage

    def _compute_retrieval_score_avg(self, context: EvaluationContext) -> float:
        """Mean fused score of the retrieved chunks.

        This is the metric rubric v1 emitted as ``retrieval_recall`` while the docs called
        it "retrieved / expected documents" and the UI called it "检索召回率". The value has
        always been an average similarity, so the key now says what it is.
        """
        scores = [r.get('score') for r in context.context_results if r.get('score') is not None]
        if not scores:
            return 0.0
        return sum(scores) / len(scores)

    def _compute_retrieval_score_std(self, context: EvaluationContext) -> Optional[float]:
        """Standard deviation of the retrieved chunk scores.

        Returns ``None`` (not applicable) for fewer than two chunks: the value 0.0 that
        rubric v1 returned in that case was turned into a *full* score by the old formula,
        so retrieving a single chunk earned "perfect consistency".
        """
        scores = [r.get('score') for r in context.context_results if r.get('score') is not None]
        if len(scores) < 2:
            return None

        avg = sum(scores) / len(scores)
        variance = sum((s - avg) ** 2 for s in scores) / len(scores)
        return variance ** 0.5

    def _compute_answer_coverage_ratio(self, context: EvaluationContext) -> float:
        """Lexical fallback for the semantic metrics: share of answer words in the context."""
        if not context.answer or not context.context_results:
            return 0.0

        context_text = " ".join(r.get('text', '') for r in context.context_results)
        answer_tokens = set(self._tokenize(context.answer))
        context_tokens = set(self._tokenize(context_text))

        if not answer_tokens:
            return 0.0

        overlap = len(answer_tokens & context_tokens) / len(answer_tokens)
        return overlap

    def _compute_citation_coverage(self, context: EvaluationContext) -> float:
        """Share of the answer's sentences carrying a *resolvable* citation marker.

        Replaces rubric v1's ``context_usage_ratio``, which divided by the token set of
        the whole context: with five chunks in the denominator no concise answer could ever
        win (observed mean 0.23 against a 0.3 pass line, i.e. a metric that measured answer
        length). Markers are resolved against the number of chunks that were actually in
        the prompt, so ``[Document 9]`` with four chunks earns nothing -- the same rule the
        sentence tracer uses for ``citation_verified``.
        """
        if not context.answer or not context.context_results:
            return 0.0

        sentences = self._split_sentences(context.answer)
        if not sentences:
            return 0.0

        valid_ids = {str(position) for position in range(1, len(context.context_results) + 1)}
        cited = 0
        for sentence in sentences:
            if any(match.group(1) in valid_ids for match in CITATION_MARKER_RE.finditer(sentence)):
                cited += 1

        return cited / len(sentences)

    def _compute_hallucination_rate(self, context: EvaluationContext) -> float:
        """Share of answer sentences that no chunk supports (cosine below the floor).

        The floor is configurable (``evaluation.hallucination_sim_floor``, default aligned
        with ``sentence_tracing.abs_drift_floor``) instead of the hardcoded 0.3 that made
        this metric and the drift rate shown next to it disagree about the same answer.
        """
        if not context.answer or not context.context_results:
            return 0.0

        context_text = " ".join(r.get('text', '') for r in context.context_results)
        context_embedding = self._get_embedding(context_text)
        if not context_embedding:
            # Rubric v1 returned 0.0 here, i.e. a *perfect* hallucination score whenever
            # the embedding backend was down. Fail closed instead.
            raise MetricUnavailableError('context embedding unavailable')

        sentences = self._split_sentences(context.answer)
        if not sentences:
            return 0.0

        floor = context.hallucination_sim_floor
        unsupported = 0
        for sentence in sentences:
            sentence_embedding = self._get_embedding(sentence)
            if not sentence_embedding:
                raise MetricUnavailableError('sentence embedding unavailable')
            if self._cosine_similarity(sentence_embedding, context_embedding) < floor:
                unsupported += 1

        return unsupported / len(sentences)

    def _compute_rejection_accuracy(self, context: EvaluationContext) -> Optional[float]:
        """Did the system refuse an out-of-domain question (and answer an in-domain one)?

        Only the out-of-domain case is measured; on an in-domain question the metric
        returns ``None`` (not applicable) and its weight is redistributed. Rubric v1 fell
        back to ``_compute_semantic_faithfulness`` there, which made this metric an exact
        duplicate of ``answer_faithfulness`` (identical in 67 of 67 sampled traces) while
        the UI presented it as a separate safety check.
        """
        boundary = context.boundary_result or {}
        if boundary.get('in_domain', True):
            return None

        if not context.answer:
            return 0.0
        return 1.0 if self._looks_like_refusal(context) else 0.0

    def _looks_like_refusal(self, context: EvaluationContext) -> bool:
        """True when the answer is *predominantly* a refusal / "nothing found" text.

        A long answer that merely mentions "cannot answer" inside one sentence is still an
        answer, so a matched phrase must dominate the text (or the answer must be short).
        """
        answer = (context.answer or '').strip().lower()
        if not answer:
            return True

        for phrase in context.refusal_phrases:
            if phrase and phrase in answer:
                if len(phrase) >= _REFUSAL_DOMINANCE * len(answer):
                    return True
                if len(answer) <= _REFUSAL_SHORT_ANSWER:
                    return True
        return False

    def _compute_empty_response(self, context: EvaluationContext) -> bool:
        """Whether the answer is the "no relevant information" fallback text.

        Recognises both supported languages and the scenario's own
        ``retriever.empty_response`` text (rubric v1 matched eight hardcoded English
        substrings, so a zh-CN refusal -- or a scenario specific fallback -- was scored as
        a normal answer and earned the gate).
        """
        if not context.answer or not context.answer.strip():
            return True
        return self._looks_like_refusal(context)

    def _compute_response_length(self, context: EvaluationContext) -> int:
        """Compute response length (character count)"""
        return len(context.answer) if context.answer else 0

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize (lazy import jieba)"""
        try:
            import jieba
            return [token for token in jieba.cut(text) if token.strip() and len(token.strip()) > 1]
        except ImportError:
            return [word for word in text.split() if len(word) > 1]

    def _get_embedding(self, text: str) -> List[float]:
        """Get Embedding vector for text, in the same space retrieval used.

        The semantic-consistency and hallucination metrics compare the answer against the
        retrieved context, so the vectors must come from the same model as the index. Embedding
        through the LLM provider's adapter while naming the embedding provider's model broke for
        every mixed configuration — see SentenceTracer._get_embedding.
        """
        try:
            from core.vector_store import vector_store_manager
            return vector_store_manager.embed_texts([text])[0]
        except Exception as e:
            logger.error(f"[Evaluation] get embedding failed for text: {e}", exc_info=True)
            return []

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity"""
        if not vec1 or not vec2:
            return 0.0
        try:
            v1 = np.array(vec1)
            v2 = np.array(vec2)
            dot = np.dot(v1, v2)
            norm = np.linalg.norm(v1) * np.linalg.norm(v2)
            if norm == 0:
                return 0.0
            return float(dot / norm)
        except Exception:
            return 0.0

    def _compute_semantic_faithfulness(self, context: EvaluationContext) -> float:
        """Semantic distance between the answer and the retrieved context.

        Falls back to the lexical coverage ratio when the embedding backend is
        unavailable: a weaker signal is reported rather than a fake 0 or 1.
        """
        if not context.answer or not context.context_results:
            return 0.0

        answer_embedding = self._get_embedding(context.answer)
        if not answer_embedding:
            return self._compute_answer_coverage_ratio(context)

        context_text = " ".join(r.get('text', '') for r in context.context_results)
        context_embedding = self._get_embedding(context_text)
        if not context_embedding:
            return self._compute_answer_coverage_ratio(context)

        return self._cosine_similarity(answer_embedding, context_embedding)

    def _compute_semantic_relevance(self, context: EvaluationContext) -> float:
        """Semantic distance between the user query and the answer."""
        if not context.query or not context.answer:
            return 0.0

        query_embedding = self._get_embedding(context.query)
        answer_embedding = self._get_embedding(context.answer)

        if not query_embedding or not answer_embedding:
            # Fallback to word overlap ratio
            query_tokens = set(self._tokenize(context.query))
            answer_tokens = set(self._tokenize(context.answer))
            if not query_tokens:
                return 0.0
            return len(query_tokens & answer_tokens) / len(query_tokens)

        return self._cosine_similarity(query_embedding, answer_embedding)

    def _compute_semantic_consistency(self, context: EvaluationContext) -> float:
        """Mean per-sentence similarity between the answer and the context."""
        if not context.answer or not context.context_results:
            return 0.0

        context_text = " ".join(r.get('text', '') for r in context.context_results)
        context_embedding = self._get_embedding(context_text)
        if not context_embedding:
            return self._compute_answer_coverage_ratio(context)

        sentences = self._split_sentences(context.answer)
        if not sentences:
            return 0.0

        similarities = []
        for sentence in sentences:
            sentence_embedding = self._get_embedding(sentence)
            if sentence_embedding:
                similarities.append(self._cosine_similarity(sentence_embedding, context_embedding))

        if not similarities:
            return self._compute_answer_coverage_ratio(context)

        return sum(similarities) / len(similarities)

    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences with the shared splitter (service/text_split).

        The implementation is shared on purpose: the evaluation metrics and the
        sentence-level trace must never disagree about how many sentences an answer
        has. A local regex used to live here too and split on a bare ``.``, so
        "weight is 0.4" counted as two sentences and inflated
        ``hallucination_rate`` / ``semantic_consistency``.
        """
        return split_sentences(text)
