# -*- coding: utf-8 -*-
"""
Retrieval defaults are declarative.

The hybrid retrieval defaults (mode / BM25 weight / similarity threshold / top_k / stage
switches) must live in config/settings.yaml (`retriever:` block) and reach both the
retriever and the web UI from there. These tests pin that contract:

* every default the UI shows is declared in settings.yaml,
* `HybridRetriever`, `get_retrieval_defaults()` and the REST endpoint agree,
* the two static pages contain no hardcoded retrieval value - their inputs get their
  values from `GET /api/chat/retrieval-defaults` (fetched in JS), which is what makes a
  settings.yaml edit actually change what the UI offers.

No server / LLM / vector store is needed: the retriever is driven with a stub store.
"""
import asyncio
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.routes import chat  # noqa: E402
from config import config, scenario_config  # noqa: E402
from core.retriever import HybridRetriever, get_retrieval_defaults  # noqa: E402

STATIC_DIR = Path(__file__).parent.parent / 'static'
STATIC_PAGES = ('index.html', 'ab_test.html')
# Input fields whose value must come from config/settings.yaml, never from the markup
RETRIEVAL_INPUT_NAMES = ('bm25_weight', 'similarity_threshold', 'top_k')
DEFAULTS_ENDPOINT = '/api/chat/retrieval-defaults'


def page_sources(page):
    """The page markup plus every local script it loads.

    The UI JavaScript lives in static/js/*.js (split out of the pages'
    inline <script> blocks), so the markup alone no longer shows which
    endpoints the page actually calls.
    """
    html = (STATIC_DIR / page).read_text(encoding='utf-8')
    parts = [html]
    for src in re.findall(r'<script\b[^>]*\bsrc="/(static/[^"]+)"', html):
        script = STATIC_DIR.parent / src
        if script.is_file():
            parts.append(script.read_text(encoding='utf-8'))
    return '\n'.join(parts)


class MockVectorStore:
    """Minimal vector store stand-in: the retriever only stores it during __init__."""

    def search(self, kb_id, query, top_k):
        return []

    def get_collection(self, kb_id):
        return None

def test_settings_yaml_declares_every_default_the_ui_needs():
    """The retriever block owns these keys - the UI has no fallback constant of its own."""
    for key in ('mode', 'bm25_weight', 'top_k', 'similarity_threshold', 'rerank_top_k',
                'query_rewrite_enabled', 'rerank_enabled'):
        assert config.get(f'retriever.{key}') is not None, \
            f'retriever.{key} is missing from config/settings.yaml'


def test_retrieval_defaults_mirror_settings_yaml():
    """get_retrieval_defaults() reports the configured values, not module literals."""
    defaults = get_retrieval_defaults()

    assert defaults['mode'] == config.get('retriever.mode')
    assert defaults['bm25_weight'] == config.get('retriever.bm25_weight')
    assert defaults['top_k'] == config.get('retriever.top_k')
    assert defaults['similarity_threshold'] == config.get('retriever.similarity_threshold')
    assert defaults['rerank_top_k'] == config.get('retriever.rerank_top_k')
    assert defaults['query_rewrite_enabled'] == config.get('retriever.query_rewrite_enabled')
    assert defaults['rerank_enabled'] == config.get('retriever.rerank_enabled')
    # vector weight is derived from bm25_weight, never configured twice
    assert defaults['vector_weight'] == pytest.approx(1 - config.get('retriever.bm25_weight'))


def test_retriever_and_effective_params_use_the_configured_defaults():
    """A query that tunes nothing runs with exactly the values the UI displays."""
    retriever = HybridRetriever(MockVectorStore())
    defaults = get_retrieval_defaults()

    assert retriever.mode == defaults['mode']
    assert retriever.bm25_weight == defaults['bm25_weight']
    assert retriever.vector_weight == pytest.approx(defaults['vector_weight'])
    assert retriever.top_k == defaults['top_k']
    assert retriever.similarity_threshold == defaults['similarity_threshold']

    params = retriever.get_effective_params()
    assert params['top_k'] == defaults['top_k']
    assert params['similarity_threshold'] == defaults['similarity_threshold']
    assert params['query_rewrite_enabled'] == defaults['query_rewrite_enabled']
    assert params['rerank_enabled'] == defaults['rerank_enabled']


def test_scenario_overrides_still_win_over_the_global_defaults():
    """Scenario files keep working: their vector_store.* values beat the global retriever.*."""
    retriever = HybridRetriever(MockVectorStore())
    for scenario_id in ('customer_service', 'technical_doc'):
        scenario = scenario_config.get_scenario(scenario_id) or {}
        override = scenario.get('retriever', {}) or {}
        legacy = scenario.get('vector_store', {}) or {}
        params = retriever.get_effective_params(scenario_id)

        expected_top_k = override.get('top_k', legacy.get('top_k'))
        expected_threshold = override.get('similarity_threshold', legacy.get('similarity_threshold'))
        if expected_top_k is not None:
            assert params['top_k'] == expected_top_k
        if expected_threshold is not None:
            assert params['similarity_threshold'] == expected_threshold


def test_retrieval_defaults_endpoint_serves_the_config_values():
    """The endpoint the UI reads is wired to the same accessor (no pipeline needed)."""
    result = asyncio.run(chat.retrieval_defaults())

    assert result['success'] is True
    assert result['data'] == get_retrieval_defaults()


def test_static_pages_pull_defaults_from_the_endpoint():
    """Both pages must fetch the defaults instead of shipping their own numbers."""
    for page in STATIC_PAGES:
        assert DEFAULTS_ENDPOINT in page_sources(page), \
            f'{page} does not read {DEFAULTS_ENDPOINT}'


def test_static_pages_have_no_hardcoded_retrieval_defaults():
    """A literal default (value="0.4", value="5", ...) in the markup would silently
    disagree with settings.yaml, so the retrieval inputs must stay value-less and be
    filled from the fetched defaults. ab_test.html builds those inputs in JS, so the
    page's own scripts are part of the check (see page_sources)."""
    pattern = re.compile(r'<input\b[^>]*name="(?:%s)"[^>]*>' % '|'.join(RETRIEVAL_INPUT_NAMES))
    offenders = []
    for page in STATIC_PAGES:
        for tag in pattern.findall(page_sources(page)):
            if 'value="' in tag:
                offenders.append(f'{page}: {tag.strip()}')
    assert not offenders, 'Hardcoded retrieval input defaults found: ' + '; '.join(offenders)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

    def collection_exists(self, kb_id):
        return True
