#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
i18n consistency linter.

Verifies the whiteBoxRAG translation dictionaries are internally consistent:

  1. zh-CN and en-US expose exactly the same set of keys.
  2. Every shared key declares the same number of {} placeholders in both languages
     (so formatting cannot silently produce an IndexError that gets swallowed).
  3. Every translation key referenced via _('literal_key', ...) in the application
     source actually exists in the dictionaries.

Run from the project root:
    python tools/check_i18n.py

Exit code 0 on success, 1 when any inconsistency is found.
"""
import ast
import sys
from pathlib import Path

# Allow running without installing the package: point at project root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from service.i18n import _TRANSLATIONS  # noqa: E402

_SOURCE_ROOTS = ['api', 'config', 'core', 'service', 'tools']


def _placeholder_count(text: str) -> int:
    # Count real '{}' substitutions; ignore escaped '{{' / '}}'.
    return text.count('{') - text.count('{{')


def _iter_source_py_files():
    for sub in _SOURCE_ROOTS:
        base = ROOT / sub
        if base.is_dir():
            for p in base.rglob('*.py'):
                if '__pycache__' in p.parts:
                    continue
                yield p
        elif base.exists():
            yield base
    yield ROOT / 'service' / 'i18n.py'


def _collect_referenced_keys() -> set:
    keys = set()
    for path in _iter_source_py_files():
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Name) and node.func.id == '_'):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                keys.add(first.value)
    return keys


def main() -> int:
    zh = _TRANSLATIONS.get('zh-CN', {})
    en = _TRANSLATIONS.get('en-US', {})
    problems = []

    zh_keys = set(zh)
    en_keys = set(en)
    if zh_keys != en_keys:
        only_zh = sorted(zh_keys - en_keys)
        only_en = sorted(en_keys - zh_keys)
        if only_zh:
            problems.append(f'Keys only in zh-CN: {only_zh}')
        if only_en:
            problems.append(f'Keys only in en-US: {only_en}')

    for key in zh.keys() & en.keys():
        n_zh = _placeholder_count(zh[key])
        n_en = _placeholder_count(en[key])
        if n_zh != n_en:
            problems.append(
                f"Placeholder mismatch for '{key}': zh has {n_zh}, en has {n_en}"
            )

    defined = zh_keys | en_keys
    missing = sorted(_collect_referenced_keys() - defined)
    if missing:
        problems.append(f'Referenced but undefined keys: {missing}')

    if problems:
        print('[i18n] inconsistencies found:', file=sys.stderr)
        for p in problems:
            print(f'  - {p}', file=sys.stderr)
        return 1

    print(f'[i18n] OK  zh-CN={len(zh_keys)}  en-US={len(en_keys)}  '
          f'placeholder-parity and source coverage verified')
    return 0


if __name__ == '__main__':
    sys.exit(main())
