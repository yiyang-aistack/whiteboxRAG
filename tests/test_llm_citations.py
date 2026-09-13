# -*- coding: utf-8 -*-
"""
Unit tests for LLM answer citation-marker validation.

The validation logic is self-contained (a regex against the retrieved context),
so we exercise it through an uninitialised LLMPipeline instance (object.__new__)
to avoid constructing real LLM adapters/retrievers.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.llm_pipeline import LLMPipeline  # noqa: E402


def _validator():
    return LLMPipeline.__new__(LLMPipeline)


def _context(n=5):
    """Fake retrieved context with the shape _validate_citations consumes."""
    return [{'id': f'chunk{i+1}', 'text': f'content {i+1}'} for i in range(n)]


def test_en_document_space_markers_are_valid():
    """[Document 1] (the English prompt format) must validate against context doc 1."""
    v = _validator()
    answer = 'Returns are allowed for 7 days according to [Document 1]. Policy details in [Document 3].'
    res = v._validate_citations(answer, _context(), lang='en-US')
    assert res['has_citations'] is True
    assert res['valid_citations'] == 2
    assert res['missing_citations'] == []
    assert res['invalid_citations'] == 0
    assert '1' in res['citation_to_chunk_map']
    assert res['citation_to_chunk_map']['1'] == 'chunk1'


def test_zh_document_markers_are_valid():
    """[文档1] (the Chinese format) must validate too."""
    v = _validator()
    answer = '根据[文档2]，退款方式为原路径返回。保修政策见[文档4]。'
    res = v._validate_citations(answer, _context(), lang='zh-CN')
    assert res['has_citations'] is True
    assert res['valid_citations'] == 2
    assert res['missing_citations'] == []
    assert res['invalid_citations'] == 0


def test_plain_number_markers_still_valid():
    """Legacy [N] markers remain supported."""
    v = _validator()
    answer = 'x stated in [1] and [5]'
    res = v._validate_citations(answer, _context(), lang='zh-CN')
    assert res['has_citations'] is True
    assert {c for c in res['found_citations']} >= {'1', '5'}


def test_chunk_marker_valids_when_doc_exists():
    v = _validator()
    answer = 'see [chunk_2]'
    res = v._validate_citations(answer, _context())
    assert res['has_citations'] is True


def test_out_of_range_marker_is_invalid():
    """A marker pointing past the number of context docs counts as invalid, not missing."""
    v = _validator()
    answer = 'Claim from [Document 99]'
    res = v._validate_citations(answer, _context(n=3))
    assert res['invalid_citations'] == 1
    assert res['missing_citations'] == ['99']


def test_no_markers_reports_missing():
    v = _validator()
    answer = 'There is no citation marker here at all.'
    res = v._validate_citations(answer, _context(), lang='en-US')
    assert res['has_citations'] is False
    assert res['valid_citations'] == 0
    assert res['found_citations'] == []
    # A human-readable suggestion must be produced (not the raw key).
    assert res['suggestion'] and res['suggestion'] != 'pipeline.citation_missing'

def test_mixed_valid_and_invalid():
    v = _validator()
    answer = '[Document 1] ok but [Document 9] and [x] are not valid indexes [Document 8]'
    res = v._validate_citations(answer, _context(n=4), lang='en-US')
    assert res['has_citations'] is True
    assert res['valid_citations'] == 1
    assert '9' in res['missing_citations']
    assert '8' in res['missing_citations']

def test_regression_marker_never_crashes_on_chunk_boundary():
    """[chunk_1] must be viewed as context doc #1 (1-based), matching Document 1 numbering."""
    v = _validator()
    answer = 'source A = [1] source B = [chunk_2]'
    res = v._validate_citations(answer, _context())
    assert res['has_citations'] is True
    assert set(res['found_citations']) == {'1', '2'}


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])

