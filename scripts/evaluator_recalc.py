#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Offline rubric calibration harness (rubric v2).

Re-scores the evaluation blocks already stored in ``storage/traces`` with the *current*
scoring code (``core/evaluator.py``) and prints how the corpus lands on it:

    uv run python scripts/evaluator_recalc.py
    uv run python scripts/evaluator_recalc.py --target answer_faithfulness=0.8
    uv run python scripts/evaluator_recalc.py --targets stored --json report.json

Why a harness instead of a runtime switch: the rubric v1 score is still readable from the
``overall_score`` field of every stored trace, so there is no need to keep two rubrics
alive in production code -- the comparison happens here, offline.

What is reused and what is recomputed:

* metric *values* come from the stored trace (no embedding call, no LLM, no retriever);
* ``citation_coverage`` did not exist in rubric v1, so it is recomputed from the stored
  answer text and the stored ``context_count`` with the real metric implementation;
* the score, the gates and the credit anchors come from ``RAGEvaluator`` itself, never
  from a copy of the formula (a copy would drift away from the code it documents).

How to read the output: for every metric, a pass rate near 100% means the pass line
measures nothing (that is exactly how rubric v1 ended up with 47% of the traces at
>= 0.9), while ~0% means the line asks for something the corpus never does. Pick a line a
reviewer would defend while 50-90% of a working corpus still clears it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.evaluator import EvaluationContext, RAGEvaluator  # noqa: E402

DEFAULT_TRACE_DIR = ROOT / "storage" / "traces"

#: rubric v1 key -> rubric v2 key; the stored value itself is unchanged.
_RENAMED_METRICS = {"retrieval_recall": "retrieval_score_avg"}

#: Metrics rubric v2 no longer scores: v1 divided by the token set of the whole context,
#: so the number measured answer length (observed mean 0.23 against a 0.3 pass line).
_DROPPED_METRICS = {"context_usage_ratio"}

#: Metrics that are not applicable on a stored trace. ``rejection_accuracy`` only means
#: something when the boundary detector flagged the question as out of domain, and an
#: out-of-domain question never reaches the evaluator (core/llm_pipeline.py returns a
#: `boundary_rejected` answer before step 5), so every stored trace is in-domain and the
#: metric drops out. v1 fell back to answer_faithfulness there, which is why the stored
#: values look meaningful (they were the same number twice).
_NOT_APPLICABLE_METRICS = {"rejection_accuracy"}


def _percentile(values: List[float], quantile: float) -> Optional[float]:
    """Nearest-rank percentile; the corpus is small and numpy is not needed here."""
    ordered = sorted(value for value in values if value is not None)
    if not ordered:
        return None
    position = min(len(ordered) - 1, max(0, int(round(quantile * (len(ordered) - 1)))))
    return ordered[position]


def _mean(values: List[float]) -> Optional[float]:
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else None


def _fmt(value: Optional[float], digits: int = 3) -> str:
    return '  n/a' if value is None else f'{value:.{digits}f}'.rjust(5)


def load_traces(trace_dir: Path, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Read every trace that carries an evaluation block."""
    traces = []
    for path in sorted(trace_dir.glob('*.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        evaluation = data.get('evaluation') or {}
        if not evaluation.get('metrics'):
            continue
        traces.append({
            'trace_id': data.get('trace_id', path.stem),
            'query': data.get('query') or '',
            'answer': data.get('final_answer') or '',
            'scenario_id': evaluation.get('scenario_id') or data.get('scenario_id'),
            'context_count': int(evaluation.get('context_count') or 0),
            'stored_score': evaluation.get('overall_score'),
            'stored_passing': evaluation.get('is_passing'),
            'stored_flags': evaluation.get('boundary_quality'),
            'stored_metrics': evaluation.get('metrics') or {},
        })
        if limit and len(traces) >= limit:
            break
    return traces


def build_metrics(evaluator: RAGEvaluator, trace: Dict[str, Any],
                  targets: Dict[str, Any]) -> Dict[str, Dict]:
    """Rebuild the metric rows of one trace for the given pass lines.

    ``RAGEvaluator._metric_entry`` is used on purpose: direction, group, weight and the
    pass verdict then come from the production code instead of being reproduced here.
    """
    metrics: Dict[str, Dict] = {}

    for stored_name, row in trace['stored_metrics'].items():
        if stored_name in _DROPPED_METRICS:
            continue
        name = _RENAMED_METRICS.get(stored_name, stored_name)
        value = row.get('value')
        # v1 stored 0.0 when only one chunk was retrieved, and its formula turned that
        # into a *full* score; v2 reports the situation as not applicable instead.
        if name == 'retrieval_score_std' and trace['context_count'] < 2:
            value = None
        # See _NOT_APPLICABLE_METRICS: the live path never measures these.
        if name in _NOT_APPLICABLE_METRICS:
            value = None
        metrics[name] = evaluator._metric_entry(name, value, targets.get(name))

    if 'citation_coverage' not in metrics:
        context = EvaluationContext(
            query=trace['query'],
            # Only len() is used by the metric, and a trace does not keep the chunk
            # texts; the resolvable-citation rule needs the chunk *count* anyway.
            context_results=[{}] * trace['context_count'],
            answer=trace['answer'],
        )
        value = evaluator._compute_citation_coverage(context)
        metrics['citation_coverage'] = evaluator._metric_entry(
            'citation_coverage', value, targets.get('citation_coverage'))

    return metrics


def score_trace(evaluator: RAGEvaluator, trace: Dict[str, Any],
                targets: Dict[str, Any]) -> Dict[str, Any]:
    """Score one trace with the current rubric code."""
    metrics = build_metrics(evaluator, trace, targets)
    score, breakdown, coverage = evaluator._compute_overall_score(metrics)
    passed, gates = evaluator._check_overall_passing(score, metrics, coverage)
    return {
        'trace_id': trace['trace_id'],
        'score': score,
        'passing': passed,
        'coverage': coverage,
        'gates': gates,
        'metrics': metrics,
        'breakdown': breakdown,
    }


def _parse_target_overrides(pairs: Optional[List[str]]) -> Dict[str, Any]:
    """``--target answer_faithfulness=0.8`` / ``--target response_length=50:500``."""
    overrides: Dict[str, Any] = {}
    for pair in pairs or []:
        if '=' not in pair:
            raise SystemExit(f'--target expects name=value, got: {pair}')
        name, raw = pair.split('=', 1)
        name, raw = name.strip(), raw.strip()
        if ':' in raw:
            low, high = raw.split(':', 1)
            overrides[name] = {'min': int(low), 'max': int(high)}
        elif raw.lower() in ('true', 'false'):
            overrides[name] = raw.lower() == 'true'
        else:
            overrides[name] = float(raw)
    return overrides


def _distribution(label: str, scores: List[float], passing: List[bool]) -> str:
    """One formatted distribution line (mean/median/range/ceiling share/pass rate)."""
    if not scores:
        return f'  {label:<34} n=0'
    high = sum(1 for score in scores if score >= 0.9) / len(scores)
    pass_rate = (sum(1 for ok in passing if ok) / len(passing)) if passing else 0.0
    return (f'  {label:<34} n={len(scores):<3} '
            f'mean {_mean(scores):.3f}  median {_percentile(scores, 0.5):.3f}  '
            f'min {min(scores):.3f}  max {max(scores):.3f}  '
            f'>=0.90 {high * 100:5.1f}%  passing {pass_rate * 100:5.1f}%')


def print_report(evaluator: RAGEvaluator, traces: List[Dict[str, Any]],
                 current_targets: Dict[str, Any]) -> Dict[str, Any]:
    """Print the calibration report and return the raw numbers behind it."""
    stored_targets: Dict[str, Any] = {}
    for trace in traces:
        for name, row in trace['stored_metrics'].items():
            if row.get('target') is not None:
                stored_targets[_RENAMED_METRICS.get(name, name)] = row['target']

    current_rows = [score_trace(evaluator, trace, current_targets) for trace in traces]
    stored_rows = [score_trace(evaluator, trace, stored_targets) for trace in traces]

    print('=' * 104)
    print(f'Rubric recalibration report   rubric v{evaluator.rubric_version}   '
          f'traces: {len(traces)}   pass score: {evaluator.pass_score}')
    print('=' * 104)

    stored_scores = [t['stored_score'] for t in traces if t['stored_score'] is not None]
    stored_passing = [bool(t['stored_passing']) for t in traces if t['stored_score'] is not None]
    print('\nscore distribution (0-1):')
    print(_distribution('v1 (stored in the traces)', stored_scores, stored_passing))
    print(_distribution('v2 math, v1 pass lines', [r['score'] for r in stored_rows],
                        [r['passing'] for r in stored_rows]))
    print(_distribution('v2 math, current pass lines', [r['score'] for r in current_rows],
                        [r['passing'] for r in current_rows]))

    flagged = sum(1 for trace in traces if trace.get('stored_flags'))
    print(f'\ntraces whose stored score carried a quality flag (v1 adjustments): {flagged}')

    print('\ngates under the current pass lines:')
    for gate in ('score', 'weight_coverage', 'not_empty_response', 'hallucination_within_limit'):
        passed = sum(1 for row in current_rows if row['gates'].get(gate))
        seen = sum(1 for row in current_rows if gate in row['gates'])
        print(f'  {gate:<30} passed {passed:3}/{seen:<3}')

    print('\nper-metric calibration (current pass lines):')
    header = (f'  {"metric":<24}{"n":>4}{"pass%":>7}{"p10":>8}{"p50":>8}{"p90":>8}'
              f'{"credit":>8}  target')
    print(header)
    print('  ' + '-' * (len(header) - 2))
    metric_names = sorted({name for row in current_rows for name in row['metrics']})
    for name in metric_names:
        values: List[float] = []
        credits: List[float] = []
        passing: List[bool] = []
        for row in current_rows:
            metric = row['metrics'].get(name)
            if not metric or metric.get('value') is None:
                continue
            if isinstance(metric['value'], bool):
                continue
            values.append(float(metric['value']))
            passing.append(bool(metric.get('is_pass')))
            credit = row['breakdown'].get(name, {}).get('credit')
            if credit is not None:
                credits.append(float(credit))
        pass_rate = (sum(1 for ok in passing if ok) / len(passing) * 100) if passing else 0.0
        print(f'  {name:<24}{len(values):>4}{pass_rate:>6.0f}%'
              f'{_fmt(_percentile(values, 0.1), 2):>8}{_fmt(_percentile(values, 0.5), 2):>8}'
              f'{_fmt(_percentile(values, 0.9), 2):>8}{_fmt(_mean(credits), 3):>8}'
              f'  {current_targets.get(name)}')

    not_applicable = {}
    for name in metric_names:
        count = sum(1 for row in current_rows
                    if row['metrics'].get(name) and row['metrics'][name].get('value') is None)
        if count:
            not_applicable[name] = count
    if not_applicable:
        print('\nnot applicable (weight redistributed): '
              + ', '.join(f'{name} {count}' for name, count in not_applicable.items()))

    print('\ncorpus mean contribution per dimension (current pass lines):')
    groups: Dict[str, List[float]] = {}
    for row in current_rows:
        for part in row['breakdown'].values():
            groups.setdefault(part.get('group') or 'other', []).append(part['contribution'])
    for group in sorted(groups):
        print(f'  {group:<12} contribution {_fmt(_mean(groups[group]), 4)}')

    return {
        'traces': len(traces),
        'targets': current_targets,
        'stored': {'scores': stored_scores, 'passing': stored_passing},
        'v2_stored_targets': {
            'scores': [row['score'] for row in stored_rows],
            'passing': [row['passing'] for row in stored_rows],
        },
        'v2_current_targets': {
            'scores': [row['score'] for row in current_rows],
            'passing': [row['passing'] for row in current_rows],
            'coverage': [round(row['coverage'], 4) for row in current_rows],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--trace-dir', type=Path, default=DEFAULT_TRACE_DIR,
                        help='directory holding the stored traces (default: storage/traces)')
    parser.add_argument('--target', action='append', metavar='NAME=VALUE',
                        help='override one pass line, e.g. --target answer_faithfulness=0.8 '
                             '(repeatable; response_length takes min:max)')
    parser.add_argument('--targets', choices=('current', 'stored'), default='current',
                        help='pass lines used by the report (default: current)')
    parser.add_argument('--json', type=Path, help='also write the raw numbers to this file')
    parser.add_argument('--limit', type=int, help='only read the first N traces')
    args = parser.parse_args()

    if not args.trace_dir.exists():
        print(f'[FAIL] trace directory not found: {args.trace_dir}', file=sys.stderr)
        return 1

    traces = load_traces(args.trace_dir, args.limit)
    if not traces:
        print(f'[FAIL] no trace with an evaluation block in {args.trace_dir}', file=sys.stderr)
        return 1

    evaluator = RAGEvaluator()
    targets = dict(evaluator._default_targets)
    if args.targets == 'stored':
        for trace in traces:
            for name, row in trace['stored_metrics'].items():
                if row.get('target') is not None:
                    targets[_RENAMED_METRICS.get(name, name)] = row['target']
    targets.update(_parse_target_overrides(args.target))

    report = print_report(evaluator, traces, targets)

    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'\nraw numbers written to {args.json}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
