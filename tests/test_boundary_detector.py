# -*- coding: utf-8 -*-
"""
Unit tests for the business boundary detector (OOD detection).

These tests exercise the keyword / retrieval / semantic / voting detectors
WITHOUT touching any live LLM or external services: the LLM adapter is mocked
and detector instances are created bypassing __init__ (which would otherwise
read config and build real adapters).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.boundary_detector import BoundaryDetector  # noqa: E402


def _make_detector(**overrides):
    """Build a BoundaryDetector with lightweight literal attributes, no config/LLM init."""
    d = BoundaryDetector.__new__(BoundaryDetector)
    d.enabled = overrides.get('enabled', True)
    d.confidence_threshold = overrides.get('confidence_threshold', 0.5)
    d.semantic_threshold = overrides.get('semantic_threshold', 0.3)
    d._whitelist_keywords = overrides.get('whitelist_keywords', [])
    d._blacklist_keywords = overrides.get('blacklist_keywords', [])
    d._business_topics = overrides.get('business_topics', [])
    d.llm_model = 'test-model'
    d._llm_adapter = _FakeAdapter()
    return d


class _FakeAdapter:
    """Stand-in for an LLM adapter whose .chat returns a canned JSON answer."""
    def __init__(self):
        self.last_prompt = None

    def chat(self, model=None, messages=None, stream=False):
        # messages is a list of dicts; the boundary detector only sends the user message.
        user_content = ''
        for m in (messages or []):
            if m.get('role') == 'user':
                user_content = m.get('content', '')
        self.last_prompt = user_content
        reply = (
            '{"is_in_domain": false, "confidence": 0.95, '
            '"reason": "outside business scope", "matched_topics": []}'
        )
        return {'message': {'content': reply}}


def test_disabled_detector_always_in_domain():
    d = _make_detector(enabled=False)
    r = d.detect('any query that would otherwise be OOD')
    assert r['in_domain'] is True
    assert r['detector'] == 'disabled'


def test_keyword_blacklist_short_circuits():
    d = _make_detector(blacklist_keywords=['转账', '股市'])
    r = d.detect('如何把钱转账给别人')
    assert r['in_domain'] is False
    assert r['detector'] == 'keyword_blacklist'


def test_keyword_whitelist_short_circuits():
    d = _make_detector(whitelist_keywords=['退货', '退款'])
    r = d.detect('退货期限是多久')
    assert r['in_domain'] is True
    assert r['detector'] == 'keyword_whitelist'


def test_retrieval_empty_rejected():
    d = _make_detector()
    r = d._retrieval_based_check([])
    assert r is not None
    assert r['in_domain'] is False
    assert r['detector'] == 'retrieval_empty'


def test_retrieval_low_score_rejected():
    d = _make_detector()
    r = d._retrieval_based_check([{'score': 0.05}, {'score': 0.1}])
    assert r is not None
    assert r['in_domain'] is False
    assert r['detector'] == 'retrieval_low_score'


def test_retrieval_good_score_passes():
    d = _make_detector()
    r = d._retrieval_based_check([{'score': 0.9}, {'score': 0.95}])
    assert r is None


def test_semantic_prompt_built_without_brace_interpolation():
    """
    Regression test for the repaired semantic prompt.

    Original bug: the topics were f-string interpolated *into* an outer f-string
    using already-interpolated text, so any literal '{' in data (e.g. a topic name
    that itself contains placeholder braces) would either crash or corrupt the prompt.

    With the fix, topics are joined as plain '\n- topic' lines so the enclosed data
    is never re-interpolated.
    """
    tricky_topic = '包含 {重要指标} 和 {机密} 的订单问题'
    d = _make_detector(business_topics=['订单管理和支付结算', tricky_topic])

    # The detector itself parses only the reply text after LLM returns; the class
    # under test here must not raise even when topics contain '{...}' literals.
    semantic = d._semantic_based_check('今天股票涨了吗')
    # LLM (mocked) answered OOD -> parsed result should reflect that
    assert semantic is not None
    assert semantic['in_domain'] is False
    assert semantic['detector'] == 'semantic'
    # Prompt must have contained the careful instruction marker, and the raw topic text
    # (including literal braces) must be present verbatim (i.e. not "successfully interpolated away").
    assert d._llm_adapter.last_prompt is not None
    assert 'out-of-domain' in d._llm_adapter.last_prompt.lower()
    assert '{重要指标}' in d._llm_adapter.last_prompt
    assert semantic.get('confidence', 0) >= 0


def test_default_when_no_business_topics_and_no_rules():
    # No business topics means semantic returns None; no retrieval provided -> 'default' path.
    d = _make_detector(business_topics=[], whitelist_keywords=[], blacklist_keywords=[])
    r = d.detect('随便问点什么都不在规则里', retrieval_results=None)
    assert r['detector'] == 'default'
    assert r['in_domain'] is True


def test_voting_mix():
    d = _make_detector(business_topics=['订单问题'])
    # Force a semantic OOD + a good retrieval (no retrieval result appended)
    # Provide no retrieval so only semantic runs -> single result branch.
    r = d.detect('退货流程', retrieval_results=None)
    assert r is not None
    assert 'in_domain' in r


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
