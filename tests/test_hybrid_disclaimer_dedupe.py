# -*- coding: utf-8 -*-
"""
The hybrid-answer disclaimer must reach the user exactly once.

Hybrid answer mode (retrieval quality 'low') prepends a two-line disclaimer, and the prompt asks
the model to open with those same two lines - so the model echoes them. The finished answer in the
stored traces carries the prefix twice (trace 02aa342b), and `_ensure_hybrid_format` could not see
it: its substring check matched the copy the pipeline itself had written, so it returned early and
the duplicate stayed in the answer the user reads.

Three pieces are pinned here:

* `_LeadingDuplicateFilter` - the streamed duplicate never reaches the client, and therefore never
  shifts the sentence offsets the incremental provenance reports,
* `_collapse_hybrid_disclaimer` - a finished answer that holds two copies is reduced to one,
  whitespace differences included,
* `_ensure_hybrid_format` - a model that follows the format on its own is not injected twice.

No LLM, no vector store: only the pipeline's formatting helpers.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.llm_pipeline import (  # noqa: E402
    LLMPipeline,
    _LeadingDuplicateFilter,
    _disclaimer_pattern,
)
from service.i18n import _  # noqa: E402

DISCLAIMER = _('pipeline.hybrid_disclaimer', 'en-US')
# How the model actually echoed it in the stored traces: one newline after the first sentence and
# a trailing space before the second one, i.e. not an exact copy of the injected text.
MODEL_ECHO = (
    'No relevant information found in the current knowledge base.\n'
    "The following content is from the LLM's own knowledge and is for reference only: \n"
)
ANSWER = "DeepResearch is Alibaba's open-source web agent; it plans, searches and cites sources."


def _pipeline() -> LLMPipeline:
    """The formatting helpers carry no state, so no retriever/LLM wiring is needed."""
    return LLMPipeline.__new__(LLMPipeline)


def test_model_echo_is_recognized_ignoring_whitespace():
    """An exact substring search misses the echo; the whitespace-insensitive pattern does not."""
    pattern = _disclaimer_pattern(DISCLAIMER)
    assert pattern is not None
    assert MODEL_ECHO not in DISCLAIMER and DISCLAIMER.strip() not in MODEL_ECHO
    assert pattern.search(MODEL_ECHO)
    assert len(pattern.findall(DISCLAIMER + MODEL_ECHO)) == 2


def test_streaming_filter_drops_the_repeated_disclaimer():
    stream_filter = _LeadingDuplicateFilter(DISCLAIMER)
    stream = MODEL_ECHO + ANSWER
    forwarded = ''.join(stream_filter.feed(stream[i:i + 5]) for i in range(0, len(stream), 5))

    assert forwarded == ANSWER
    assert stream_filter.flush() == ''


def test_streaming_filter_passes_a_normal_answer_through():
    stream_filter = _LeadingDuplicateFilter(DISCLAIMER)
    text = 'Refunds are processed within 7 days of delivery.'
    forwarded = ''.join(stream_filter.feed(text[i:i + 4]) for i in range(0, len(text), 4))

    assert forwarded == text
    assert stream_filter.flush() == ''


def test_streaming_filter_releases_a_truncated_echo():
    """A model that stops inside the disclaimer must not swallow its own text."""
    stream_filter = _LeadingDuplicateFilter(DISCLAIMER)
    partial = 'No relevant information found'

    assert stream_filter.feed(partial) == ''
    assert stream_filter.flush() == partial


def test_collapse_removes_the_second_copy():
    pipeline = _pipeline()
    collapsed = pipeline._collapse_hybrid_disclaimer(DISCLAIMER + MODEL_ECHO + ANSWER)

    assert collapsed == DISCLAIMER + ANSWER
    assert collapsed.count('No relevant information found in the current knowledge base.') == 1


def test_collapse_keeps_a_single_copy_untouched():
    pipeline = _pipeline()
    single = DISCLAIMER + ANSWER

    assert pipeline._collapse_hybrid_disclaimer(single) == single


def test_ensure_hybrid_format_does_not_inject_a_second_copy():
    pipeline = _pipeline()
    # The model followed the format on its own (non-streaming path: nothing was injected).
    assert pipeline._ensure_hybrid_format(MODEL_ECHO + ANSWER) == MODEL_ECHO + ANSWER
    # Streamed case: the injected copy plus the model's echo collapse into one.
    assert pipeline._ensure_hybrid_format(DISCLAIMER + MODEL_ECHO + ANSWER) == DISCLAIMER + ANSWER


def test_ensure_hybrid_format_still_injects_when_the_model_ignores_it():
    pipeline = _pipeline()

    assert pipeline._ensure_hybrid_format(ANSWER).startswith(DISCLAIMER)


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
