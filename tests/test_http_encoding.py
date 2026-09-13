# -*- coding: utf-8 -*-
"""
Unit tests for the HTTP UTF-8 charset helper used by the API response middleware.

These tests only need the (dependency-free) ``service.http_encoding`` module, so they
run without the full FastAPI / slowapi / Ollama stack.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from service.http_encoding import with_utf8_charset  # noqa: E402


def test_empty_is_passthrough():
    assert with_utf8_charset('') == ''


def test_json_without_charset_gets_charset():
    assert with_utf8_charset('application/json') == 'application/json; charset=utf-8'


def test_json_that_already_has_charset_is_untouched():
    value = 'application/json; charset=utf-8'
    assert with_utf8_charset(value) == value


def test_json_with_other_params_appends_not_overwrites():
    assert with_utf8_charset('application/json; profile=x') == \
        'application/json; profile=x; charset=utf-8'


def test_case_insensitive_charset_detection():
    # lowercase 'charset' inside the value must not be duplicated.
    value = 'Application/JSON; Charset=ISO-8859-1'
    assert with_utf8_charset(value) == value


def test_text_media_type_gets_charset():
    assert with_utf8_charset('text/plain') == 'text/plain; charset=utf-8'


def test_non_json_text_media_types_untouched():
    # binary media types must NOT be flagged as UTF-8.
    assert with_utf8_charset('application/octet-stream') == 'application/octet-stream'
    assert with_utf8_charset('image/png') == 'image/png'
    assert with_utf8_charset('multipart/form-data') == 'multipart/form-data'


def test_mixed_case_media_type_still_flagged():
    # 'Json' in an unusual casing should still be treated as JSON.
    assert with_utf8_charset('Application/JSON') == 'Application/JSON; charset=utf-8'


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
