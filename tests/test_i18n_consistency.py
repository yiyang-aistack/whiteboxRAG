# -*- coding: utf-8 -*-
"""
i18n consistency checks.

Ensures the zh-CN and en-US translation dicts expose the SAME set of keys with
the SAME number of {} placeholders, and that every translation key referenced
via _('literal_key', ...) in the application source actually exists.

A second group of checks pins the other half of the contract: a page that
renders backend text (LLM answers, diagnostics, error messages) must tell the
backend which language its UI is in, otherwise an English session shows Chinese
text from the API.

These are static checks and need no server / LLM.
"""
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
# The shared linter holds the dictionary parsing / comparison logic so the CI step
# (tools/check_i18n.py) and these tests can never drift apart.
sys.path.insert(0, str(Path(__file__).parent.parent / 'tools'))

import check_i18n  # noqa: E402
from service.i18n import _TRANSLATIONS  # noqa: E402

# Top-level source roots scanned for _('literal', ...) references.
_SOURCE_ROOTS = ['api', 'config', 'core', 'service', 'tools']
_ROOTS_AS_PATH = [Path('service/i18n.py')]  # excludes the definition file itself


def _placeholder_count(text: str) -> int:
    """Count how many '{}' string-format placeholders a template has."""
    return text.count('{') - text.count('{{')


def test_zh_and_en_have_identical_key_sets():
    zh = set(_TRANSLATIONS['zh-CN'].keys())
    en = set(_TRANSLATIONS['en-US'].keys())
    only_zh = zh - en
    only_en = en - zh
    assert not only_zh, f'Keys present only in zh-CN: {sorted(only_zh)}'
    assert not only_en, f'Keys present only in en-US: {sorted(only_en)}'


def test_matching_placeholder_counts_between_languages():
    zh = _TRANSLATIONS['zh-CN']
    en = _TRANSLATIONS['en-US']
    bad = []
    for key in zh:
        if key not in en:
            continue
        n_zh = _placeholder_count(zh[key])
        n_en = _placeholder_count(en[key])
        if n_zh != n_en:
            bad.append((key, n_zh, n_en))
    assert not bad, f'Keys with mismatched {{}} placeholder counts (key, zh, en): {bad}'


def _collect_source_literal_keys() -> set:
    """Return the set of literal translation keys referenced via _('key', ...) in source."""
    keys = set()
    files = _iter_source_files()
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # _('key', ...) spread across languages
            if not (isinstance(node.func, ast.Name) and node.func.id == '_'):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                keys.add(first.value)
    return keys


def _iter_source_files():
    root = Path(__file__).parent.parent
    for sub in _SOURCE_ROOTS:
        base = root / sub
        if not base.exists():
            continue
        if base.is_dir():
            for p in base.rglob('*.py'):
                if '__pycache__' in p.parts:
                    continue
                yield p
        else:
            yield base
    # The definition module itself should, of course, not reference runtime keys.
    yield root / 'service' / 'i18n.py'


def test_all_source_referenced_keys_are_defined():
    zh = set(_TRANSLATIONS['zh-CN'].keys())
    en = set(_TRANSLATIONS['en-US'].keys())
    defined = zh | en
    referenced = {k for k in _collect_source_literal_keys() if k}
    missing = sorted(referenced - defined)
    assert not missing, f'Translation keys referenced in source but not defined: {missing}'


# ==================== Frontend dictionary (static/i18n.js) ====================
# The frontend falls back to zh-CN when a key is missing in en-US, so a missing
# English entry shows up as Chinese text in English mode instead of failing loudly.
# These checks make that impossible to merge unnoticed.

def _frontend_problems():
    zh, en = check_i18n.load_frontend_dictionaries()
    assert zh and en, 'static/i18n.js dictionaries could not be parsed'
    return check_i18n.frontend_dictionary_problems(zh, en) + \
        check_i18n.frontend_reference_problems(zh, en)


def test_frontend_dictionaries_are_consistent():
    problems = _frontend_problems()
    assert not problems, 'Frontend i18n problems: ' + '; '.join(problems)


def test_frontend_guard_detects_missing_key_and_chinese_leak():
    """The guard itself must fail on the exact defects it exists to catch."""
    zh = {'trace.only_zh': '仅中文', 'trace.common': '共 {n} 个文件'}
    en = {'trace.common': 'Total {n} files', 'trace.leak': '中文残留'}

    problems = check_i18n.frontend_dictionary_problems(zh, en)
    joined = '; '.join(problems)
    assert 'only in zh-CN' in joined
    assert 'only in en-US' in joined
    assert 'Han characters' in joined

    # placeholder mismatch must be reported as well
    mismatch = check_i18n.frontend_dictionary_problems(
        {'k': 'a {n} {m}'}, {'k': 'a {n}'})
    assert any('placeholder mismatch' in p for p in mismatch)

    # and a clean pair must produce no problems
    assert not check_i18n.frontend_dictionary_problems(
        {'k': '中文 {n}'}, {'k': 'English {n}'})


# ==================== Frontend -> backend language ====================
# The A/B test page renders the LLM answer (plus backend diagnostics and error messages),
# and its language is decided by the backend's get_lang_from_request(): the ?lang= query
# parameter first, then the Accept-Language header, then the configured default. The page
# used bare fetch() calls with neither, so an English session got Chinese answers there
# even though index.html answered in English.
_STATIC_DIR = Path(__file__).parent.parent / 'static'
_AB_TEST_PAGE = _STATIC_DIR / 'ab_test.html'
_AB_TEST_ENDPOINTS = ('/api/chat/abtest', '/api/chat/abtest/batch')
_LANG_QUERY = re.compile(r"lang=\$\{encodeURIComponent\(")
_INDEX_ABTEST_REQUEST = re.compile(r'/api/chat/abtest\?lang=')


def _page_and_scripts(page: Path) -> str:
    """The page markup plus every local script it loads.

    The pages' JavaScript lives in static/js/*.js (split out of their inline
    <script> blocks), so the markup alone no longer shows which endpoints the
    page calls or how (same helper as tests/test_retrieval_defaults.page_sources).
    """
    html = page.read_text(encoding='utf-8')
    parts = [html]
    for src in re.findall(r'<script\b[^>]*\bsrc="/(static/[^"]+)"', html):
        script = _STATIC_DIR.parent / src
        if script.is_file():
            parts.append(script.read_text(encoding='utf-8'))
    return '\n'.join(parts)


def test_ab_test_page_sends_the_ui_language_to_the_backend():
    """ab_test.html must tell the backend which language its UI is in.

    Both the header and the (higher priority) query parameter are expected: the
    header only survives while it is not stripped, the query parameter is what
    get_lang_from_request() reads first.
    """
    sources = _page_and_scripts(_AB_TEST_PAGE)

    assert "'Accept-Language'" in sources, 'ab_test.html scripts never send Accept-Language'
    assert _LANG_QUERY.search(sources), 'ab_test.html scripts never send an explicit ?lang='

    for endpoint in _AB_TEST_ENDPOINTS:
        assert f"apiFetch('{endpoint}'" in sources, \
            f'ab_test.html must call {endpoint} through the language-aware apiFetch()'
        assert f"await fetch('{endpoint}'" not in sources, \
            f'{endpoint} must not be requested with a language-less fetch()'


def test_ab_test_page_sends_a_language_code_not_a_display_label():
    """'EN' / '中文' are button labels, not dictionary keys.

    service/i18n.py only knows zh-CN / en-US, and get_lang_from_request() silently
    ignores an unknown value - so sending 'EN' would keep the answers in the
    backend default language while looking like a fix.
    """
    sources = _page_and_scripts(_AB_TEST_PAGE)

    assert 'i18n.getLang()' in sources, \
        'ab_test.html must send the UI language code from i18n.getLang()'


def test_index_abtest_dialog_also_carries_the_language_explicitly():
    """index.html's A/B dialog already sends Accept-Language through fetchJSON();
    the explicit ?lang= keeps the answers in the UI language even behind a proxy
    that strips the header."""
    source = (_STATIC_DIR / 'js' / 'abtest.js').read_text(encoding='utf-8')

    assert _INDEX_ABTEST_REQUEST.search(source), \
        'static/js/abtest.js must post to /api/chat/abtest with an explicit ?lang='


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
