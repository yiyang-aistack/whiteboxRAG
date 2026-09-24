# -*- coding: utf-8 -*-
"""
Sentence splitter tests (service/text_split.py).

The splitter is the foundation of sentence-level provenance: if it cuts a
number in half, every per-sentence verdict behind it is attached to a fragment
instead of to a statement. The first test in this file is the regression that
motivated the module — before it, "the BM25 weight is 0.4 and vector weight is
0.6." became three "sentences" ("...is 0", "4 and vector weight is 0", "6").

Pure text handling: no vector store, no LLM, no running service.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from service.text_split import (  # noqa: E402
    CITATION_MARKER_RE,
    split_complete_sentences,
    split_sentence_spans,
    split_sentences,
)


def test_decimals_are_not_sentence_boundaries():
    """"0.4" / "0.6" must stay inside one sentence (the original bug)."""
    text = ('Mixed retrieval is on by default and the BM25 weight is 0.4 '
            'while the vector weight is 0.6.')
    assert split_sentences(text) == [text]


def test_offsets_reproduce_the_original_slice():
    """Every span must satisfy text == source[start:end] so offsets are usable."""
    source = '第一句。\n\n第二句有 0.4 和 0.6。[1] 第三句！'
    for span in split_sentence_spans(source):
        assert span.text == source[span.start:span.end]
        assert span.start < span.end


def test_citation_marker_stays_with_its_sentence():
    """A trailing [N] is evidence, so it belongs to the sentence that cites it."""
    sentences = split_sentences('默认权重为 0.4。[1]')
    assert sentences == ['默认权重为 0.4。[1]']
    assert CITATION_MARKER_RE.search(sentences[0]).group(1) == '1'


def test_disclaimer_prefix_and_answer_are_separate_sentences():
    """The injected hybrid-mode disclaimer must not swallow the first answer sentence."""
    text = ('当前知识库中没有这方面的信息。\n\n'
            '以下内容来自大模型自身知识，仅供参考：\n\n'
            '混合检索默认启用，BM25 的默认权重为 0.4，向量权重为 0.6。[1]')
    sentences = split_sentences(text)
    assert sentences == [
        '当前知识库中没有这方面的信息。',
        '以下内容来自大模型自身知识，仅供参考：',
        '混合检索默认启用，BM25 的默认权重为 0.4，向量权重为 0.6。[1]',
    ]


def test_complete_split_keeps_the_unterminated_tail_pending():
    """Streaming buffers: finished sentences are emitted, the tail stays buffered."""
    buffer = '第一个完整句子。还有半个句子没有结束'
    completed, pending_start = split_complete_sentences(buffer)
    assert [s.text for s in completed] == ['第一个完整句子。']
    assert buffer[pending_start:] == '还有半个句子没有结束'

    # A terminator at the very end of the buffer stays PENDING: it may be the beginning
    # of "……" or the decimal point of a number whose digits have not arrived yet
    # ("...is 0." followed by ".4"). Closing it here is what used to cut numbers in half
    # for the streamed offsets.
    completed, pending_start = split_complete_sentences('一句。')
    assert completed == []
    assert pending_start == 0

    # As soon as anything follows the terminator, the sentence is complete.
    completed, pending_start = split_complete_sentences('一句。 ')
    assert [s.text for s in completed] == ['一句。']
    assert pending_start == len('一句。 ')


def test_versions_file_names_and_domains_are_not_split():
    sentences = split_sentences('See docs/api.md and version 1.2.3. Then stop.')
    assert sentences == ['See docs/api.md and version 1.2.3.', 'Then stop.']


def test_abbreviations_do_not_end_a_sentence():
    assert split_sentences('Check Dr. Smith et al. for details. Thanks.') == [
        'Check Dr. Smith et al. for details.', 'Thanks.']


def test_markdown_list_items_are_one_sentence_each():
    assert split_sentences('- 权重 0.4\n- 阈值 0.5\n') == ['- 权重 0.4', '- 阈值 0.5']
    assert split_sentences('1. 第一步。\n2. 第二步。') == ['1. 第一步。', '2. 第二步。']


def test_single_newline_inside_a_wrapped_paragraph_is_not_a_boundary():
    sentences = split_sentences('This sentence wraps\nonto the next line. And another.')
    assert sentences == ['This sentence wraps\nonto the next line.', 'And another.']


def test_inline_code_is_not_split_on_its_dots():
    """A `.` inside `code` is not a sentence end (regression for the naive regex)."""
    sentences = split_sentences('Use `a.b()` to call it. Then stop.')
    assert sentences == ['Use `a.b()` to call it.', 'Then stop.']


def test_fenced_code_block_is_one_unit():
    text = 'Example:\n\n```python\nvalue = 1.5\nprint(value)\n```\n\nDone.'
    sentences = split_sentences(text)
    assert sentences[-1] == 'Done.'
    assert any('print(value)' in s for s in sentences)


def test_ellipsis_and_closing_quotes_end_a_sentence():
    assert split_sentences('第一句……第二句') == ['第一句……', '第二句']
    assert split_sentences('他说“可以。”然后走了。') == ['他说“可以。”', '然后走了。']


def test_english_terminators_are_supported():
    assert split_sentences('First one! Second one? Third one.') == [
        'First one!', 'Second one?', 'Third one.']


def test_blank_lines_split_paragraphs_without_terminators():
    sentences = split_sentences('段落一没有句号\n\n段落二也没有句号')
    assert sentences == ['段落一没有句号', '段落二也没有句号']


def test_empty_and_whitespace_input_is_safe():
    assert split_sentences('') == []
    assert split_sentences('   \n\n  ') == []
    assert split_complete_sentences('') == ([], 0)


def test_streaming_buffer_offsets_equal_the_final_batch_offsets():
    """The streaming offsets must equal the ones the final batch trace reports.

    Replicates the buffering ``LLMPipeline.query_stream`` performs (feed a piece, emit
    every completed sentence with its absolute offset, keep the unterminated tail) with
    deliberately awkward 7-character chunks, so numbers, the citation marker and the
    sentence boundaries all land across chunk splits. Any drift here would mean a badge
    attached to the wrong sentence in the browser.
    """
    from service.text_split import split_complete_sentences, split_sentence_spans

    answer = '第一句结论。第二句里有 0.4 和 0.6 两个数值。第三句以引用结尾。[1]'
    full, buffer, emitted = '', '', []
    for index in range(0, len(answer), 7):
        piece = answer[index:index + 7]
        full += piece
        buffer += piece
        completed, pending_start = split_complete_sentences(buffer)
        if completed:
            offset = len(full) - len(buffer)
            for span in completed:
                emitted.append((offset + span.start, offset + span.end, span.text))
            buffer = buffer[pending_start:]
    if buffer.strip():
        offset = len(full) - len(buffer)
        for span in split_sentence_spans(buffer):
            emitted.append((offset + span.start, offset + span.end, span.text))

    final = [(span.start, span.end, span.text) for span in split_sentence_spans(answer)]
    assert emitted == final
    assert len(final) == 3


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
