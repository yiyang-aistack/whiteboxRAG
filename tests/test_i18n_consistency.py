# -*- coding: utf-8 -*-
"""
i18n consistency checks.

Ensures the zh-CN and en-US translation dicts expose the SAME set of keys with
the SAME number of {} placeholders, and that every translation key referenced
via _('literal_key', ...) in the application source actually exists.

These are static checks and need no server / LLM.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

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


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
