# -*- coding: utf-8 -*-
"""
Structured contradiction detection tests (core/contradiction.py).

The old checker was substring-based with hard-coded thresholds (a percentage had to differ by
more than 10 points, counts and amounts by a factor of 2) and only understood Chinese
keywords, so the example this project uses in its own README - the document says the weight
is 0.4 and the model answers 0.6 - was never flagged: 0.4 vs 0.6 is a ratio of 1.5.

These tests pin the replacement contract:

* claims are compared after unit normalisation (7 天 == 168 小时, 1 万元 == 10000 元);
* a claim is only reported when NO in-scope source claim can reconcile it (a document
  listing both 0.4 and 0.6 is not a contradiction for an answer that says 0.6);
* scope is required (unrelated numbers of the same chunk are never compared);
* thresholds come from the ``contradiction:`` block of settings.yaml;
* citation markers are not values, and the reported offsets point back into the source text.

Offline only: no model, no vector store, no service.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config import config  # noqa: E402
from core.contradiction import ContradictionDetector, contradiction_detector  # noqa: E402


def context(text: str, chunk_id: str = 'chunk-A') -> list:
    """One retrieved chunk, in the shape the retriever produces."""
    return [{'id': chunk_id, 'text': text, 'metadata': {'file_name': 'doc.txt'}}]


def detect(answer: str, source: str, lang: str = 'en-US') -> list:
    return contradiction_detector.detect(answer, context(source), lang=lang)


@pytest.fixture
def config_override():
    """Temporarily change one ``contradiction.*`` setting, then reload the detector."""
    original = dict(config._config.get('contradiction', {}))

    def _apply(**overrides):
        config._config['contradiction'] = {**original, **overrides}
        contradiction_detector.reload_config()

    yield _apply

    config._config['contradiction'] = original
    contradiction_detector.reload_config()


# ---------------------------------------------------------------------------
# The case that motivated the rewrite
# ---------------------------------------------------------------------------

def test_decimal_place_error_is_reported():
    """0.9 vs 0.4/0.6: a ratio of 1.5 slips under a "factor of 2" rule, a ratio of 0.33 must not."""
    found = detect('混合检索默认启用，BM25 的默认权重为 0.9。',
                   '2. 混合检索默认启用，BM25 权重为 0.4，向量权重为 0.6。')
    assert len(found) == 1
    item = found[0]
    assert item['type'] == 'number_quantity'
    assert item['detector'] == 'structured'
    assert item['answer_text'] == '0.9'
    assert item['source_text'] == '0.6'          # the closest in-scope claim
    assert item['severity'] == 'medium'
    assert 0.3 < item['relative_delta'] < 0.35
    assert item['explanation']


def test_a_value_present_in_the_source_reconciles_the_claim():
    """The document lists both 0.4 and 0.6, so an answer saying 0.6 is not a contradiction."""
    assert detect('混合检索默认启用，BM25 的默认权重为 0.6。',
                  '2. 混合检索默认启用，BM25 权重为 0.4，向量权重为 0.6。') == []


def test_citation_markers_are_not_treated_as_values():
    """[文档1] must not become the claim "1" and collide with a source number."""
    assert detect('BM25 的默认权重为 0.6。[文档1]',
                  'BM25 权重为 0.4，向量权重为 0.6。') == []


# ---------------------------------------------------------------------------
# Unit normalisation
# ---------------------------------------------------------------------------

def test_equivalent_durations_in_different_units_are_equal():
    assert detect('退货期限为 168 小时。', '签收后 7 天内可以申请退货。') == []
    assert detect('退款在 1 周内到账。', '退款在 7 天内到账。') == []


def test_duration_mismatch_is_reported_as_time_duration():
    found = detect('退货期限为 30 天。', '退货期限为 7 天。')
    assert [item['type'] for item in found] == ['time_duration']
    assert found[0]['severity'] == 'high'
    assert found[0]['answer_text'] == '30 天'
    assert found[0]['source_text'] == '7 天'


def test_money_scales_are_normalised():
    assert detect('退款金额为 1 万元。', '退款金额为 10000 元。') == []
    found = detect('退款金额为 2 万元。', '退款金额为 10000 元。')
    assert [item['type'] for item in found] == ['amount_money']
    assert found[0]['unit'] == 'CNY'
    assert found[0]['answer_value'] == 20000.0


def test_different_currencies_are_never_compared():
    """100 元 against 100 美元 is a unit question, not a contradiction."""
    assert detect('退款金额为 100 元。', '退款金额为 100 美元。') == []


# ---------------------------------------------------------------------------
# Percentages and dates
# ---------------------------------------------------------------------------

def test_percentage_uses_the_point_tolerance():
    """50% vs 60% is only 17% relative, so a relative-only rule would miss it."""
    found = detect('退款比例为 60%。', '退款比例为 50%。')
    assert [item['type'] for item in found] == ['number_percentage']
    assert found[0]['delta'] == pytest.approx(10.0)
    assert found[0]['severity'] == 'medium'      # scored in points, not relatively

    # A two-point difference is treated as rounding and stays unreported.
    assert detect('退款比例为 52%。', '退款比例为 50%。') == []


def test_dates_are_compared_in_days():
    found = detect('生效日期为 2026-03-20。', '生效日期为 2026-03-10。')
    assert [item['type'] for item in found] == ['date_mismatch']
    assert found[0]['delta'] == pytest.approx(10.0)
    assert found[0]['relative_delta'] == pytest.approx(10.0)   # days, not a ratio
    assert found[0]['severity'] == 'high'

    # Within the configured tolerance (1 day by default) nothing is reported.
    assert detect('生效日期为 2026-03-11。', '生效日期为 2026-03-10。') == []


def test_dates_without_a_year_still_compare():
    found = detect('生效日期为 5月20日。', '生效日期为 5月10日。')
    assert [item['type'] for item in found] == ['date_mismatch']


# ---------------------------------------------------------------------------
# Scope: only compare claims that are about the same thing
# ---------------------------------------------------------------------------

def test_unrelated_numbers_in_the_same_chunk_are_not_compared():
    """The chunk's 3 天/8 元 are about delivery, the answer's 24 个月 about warranty."""
    assert detect('保修期限为 24 个月。', '配送时间为 3 天，运费 8 元。') == []


def test_ranges_are_not_read_as_standalone_values():
    """Ranges are ambiguous for a strict comparison, so their bounds are skipped."""
    assert detect('配送时间为 3-5 天。', '配送时间为 4-6 天。') == []
    assert detect('配送时间为 3 到 5 天。', '配送时间为 4 到 6 天。') == []


def test_unrelated_ids_and_list_markers_are_ignored():
    """Version strings and list numbers are not claims."""
    assert detect('接口版本为 v1.2。', '接口版本为 v1.2。') == []
    assert detect('1. 第一步是提交申请。2. 第二步是等待审核。',
                  '1. 第一步是提交申请。2. 第二步是等待审核。') == []


# ---------------------------------------------------------------------------
# Polarity phrases
# ---------------------------------------------------------------------------

def test_supported_vs_unsupported_is_reported():
    found = detect('退货可以全额退款。', '退货不支持退款。')
    assert [item['type'] for item in found] == ['status']
    assert found[0]['detector'] == 'polarity'
    assert found[0]['severity'] == 'high'
    assert found[0]['concept'] == 'support'


def test_negated_phrases_do_not_count_as_positive():
    """The answer's own "不支持部分退款" must not be read as supporting it."""
    assert detect('不支持部分退款。', '不支持部分退款。') == []


def test_refund_scope_conflict_is_reported_once():
    found = detect('可以全额退款。', '仅支持部分退款，不支持全额退款。')
    assert [item['type'] for item in found] == ['status']
    assert found[0]['concept'] == 'refund_scope'
    assert found[0]['answer_text']
    assert found[0]['source_text']


def test_english_polarity_pairs_are_supported():
    found = detect('The refund service is free of charge.', 'The refund service is charged per request.')
    assert [item['type'] for item in found] == ['status']
    assert found[0]['concept'] == 'cost'


def test_completion_states_are_compared():
    found = detect('The order is shipped already.', 'The order is not shipped yet.')
    assert [item['type'] for item in found] == ['status']
    assert found[0]['concept'] == 'completion'


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def test_disabled_detector_returns_nothing(config_override):
    config_override(enabled=False)
    assert detect('退款金额为 200 元。', '退款金额为 100 元。') == []


def test_relative_tolerance_comes_from_settings(config_override):
    answer, source = '退款金额为 200 元。', '退款金额为 100 元。'
    assert detect(answer, source) != []
    # A 50% relative difference is tolerated once the tolerance is raised above it.
    config_override(relative_tolerance=0.8)
    assert detect(answer, source) == []


def test_max_results_caps_the_output(config_override):
    source = 'A 项为 1 个，B 项为 2 个，C 项为 3 个。'
    answer = 'A 项为 9 个，B 项为 8 个，C 项为 7 个。'
    config_override(max_results=1)
    assert len(detect(answer, source)) == 1


def test_findings_are_ordered_by_severity():
    source = '退款金额为 100 元。退款比例为 50%。'
    answer = '退款金额为 110 元。退款比例为 95%。'
    found = detect(answer, source)
    severities = [item['severity'] for item in found]
    assert severities == sorted(severities, key=lambda value: {'high': 0, 'medium': 1, 'low': 2}[value])
    assert severities[0] == 'high'          # the 45-point percentage error




# ---------------------------------------------------------------------------
# Trace integration helpers
# ---------------------------------------------------------------------------

def test_attach_to_sentences_flags_the_matching_item():
    answer = '退款金额为 200 元。退款将在 3 天内到账。'
    source = '退款金额为 100 元。退款将在 3 天内到账。'
    found = detect(answer, source)
    assert len(found) == 1

    tracer_items = [
        {'sentence': '退款金额为 200 元。', 'confidence_level': 'direct_quote'},
        {'sentence': '退款将在 3 天内到账。', 'confidence_level': 'direct_quote'},
    ]
    flagged = ContradictionDetector.attach_to_sentences(tracer_items, found)
    assert flagged == 1
    assert tracer_items[0]['has_contradiction'] is True
    assert tracer_items[0]['contradictions'] == found
    assert 'has_contradiction' not in tracer_items[1]


def test_attach_to_sentences_is_a_noop_without_findings():
    items = [{'sentence': '任意句子。'}]
    assert ContradictionDetector.attach_to_sentences(items, []) == 0
    assert 'has_contradiction' not in items[0]


def test_reported_offsets_slice_back_to_the_sources():
    """The UI highlights the source snippet with these offsets, so they must be usable."""
    answer = '退款金额为 200 元。'
    source = '退款政策：退款金额为 100 元，7 天内到账。'
    found = detect(answer, source)
    assert len(found) == 1
    item = found[0]
    assert answer[item['sentence_start']:item['sentence_end']] == item['sentence']
    assert source[item['source_start']:item['source_end']] == item['source_text']
    assert item['source_chunk_id'] == 'chunk-A'
    assert item['source_sentence'].startswith('退款政策：')
    assert item['matched_tokens']


def test_backward_compatible_entry_point_on_the_tracer():
    """SentenceTracer.detect_contradictions still answers with the historical type names."""
    from core.sentence_tracing import SentenceTracer

    found = SentenceTracer().detect_contradictions(
        '根据文档，退货期限为30天，可以全额退款，不支持部分退款',
        [{'id': 'chunk_1', 'text': '退货期限为7天，超过7天将不予处理。退款方式为原路径返回，仅支持部分退款，不支持全额退款。',
          'metadata': {'file_name': '退货政策.txt'}},
         {'id': 'chunk_2', 'text': '售后服务时间为工作日9:00-18:00，支持电话和在线客服两种方式。',
          'metadata': {'file_name': '服务指南.txt'}}],
    )
    types = {item['type'] for item in found}
    assert 'time_duration' in types
    assert 'status' in types
    assert all(item['explanation'] for item in found)


def test_module_singleton_is_ready_to_use():
    assert isinstance(contradiction_detector, ContradictionDetector)
    # An empty answer or an empty context is not an error.
    assert contradiction_detector.detect('', context('任意原文。')) == []
    assert contradiction_detector.detect('任意答案。', []) == []


if __name__ == '__main__':
    import pytest as _pytest
    _pytest.main([__file__, '-v'])
