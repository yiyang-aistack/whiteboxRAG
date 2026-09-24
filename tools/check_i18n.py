#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
i18n consistency linter.

Verifies the whiteBoxRAG translation dictionaries are internally consistent:

  1. backend (service/i18n.py) and frontend (static/i18n.js): zh-CN and en-US expose
     exactly the same set of keys, so a missing en-US entry cannot silently fall back
     to Chinese.
  2. Every shared key declares the same placeholders in both languages, so formatting
     cannot silently produce an IndexError / a literal "{name}" in the UI.
  3. Every translation key referenced via _('literal_key', ...) in the application
     source actually exists in the dictionaries.
  4. Every literal key referenced from the frontend (t('key'), data-i18n="key", ...)
     exists in the frontend dictionaries.
  5. The frontend en-US dictionary contains no Han characters - the UI is fully
     translated, not half-translated with Chinese leaking into English mode.

Run from the project root:
    python tools/check_i18n.py

Exit code 0 on success, 1 when any inconsistency is found.
"""
import ast
import re
import sys
from pathlib import Path

# Allow running without installing the package: point at project root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from service.i18n import _TRANSLATIONS  # noqa: E402

_SOURCE_ROOTS = ['api', 'config', 'core', 'service', 'tools']

# Frontend files scanned for i18n references / dictionary definitions.
# The pages' own JavaScript lives in static/js/*.js (split out of their
# inline <script> blocks), so those files are scanned too - otherwise a page
# could reference an undefined key from its script and this linter would
# never see it.
_FRONTEND_FILES = [
    'static/i18n.js',
    'static/index.html',
    'static/admin.html',
    'static/ab_test.html',
]
_FRONTEND_JS_GLOB = 'static/js/*.js'


def _frontend_files():
    """Every frontend file that can carry a translation key (pages + scripts)."""
    files = [ROOT / rel for rel in _FRONTEND_FILES]
    files.extend(sorted(ROOT.glob(_FRONTEND_JS_GLOB)))
    return files


_HAN = re.compile(r'[\u4e00-\u9fff]')
# Missing en-US entries fall back to this language, so a gap shows up as Chinese text.
_FALLBACK_LANG = 'zh-CN'



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


def _strip_js_comments(text: str) -> str:
    """Blank out JS line/block comments, preserving offsets and newlines.

    Needed because the dictionaries are parsed by brace matching, and an apostrophe
    inside a comment (e.g. "the backend's metric") would otherwise be read as the
    start of a string literal.
    """
    out = []
    i, n = 0, len(text)
    state, quote = 'code', None
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ''
        if state == 'code':
            if ch == '/' and nxt == '/':
                state, i = 'line_comment', i + 2
                out.append('  ')
            elif ch == '/' and nxt == '*':
                state, i = 'block_comment', i + 2
                out.append('  ')
            elif ch in '\'"`':
                quote, state = ch, 'string'
                out.append(ch)
                i += 1
            else:
                out.append(ch)
                i += 1
        elif state == 'line_comment':
            if ch == '\n':
                state = 'code'
                out.append(ch)
            else:
                out.append(' ')
            i += 1
        elif state == 'block_comment':
            if ch == '*' and nxt == '/':
                state, i = 'code', i + 2
                out.append('  ')
            else:
                out.append(ch if ch == '\n' else ' ')
                i += 1
        else:  # string
            if ch == '\\':
                out.append(text[i:i + 2])
                i += 2
            elif ch == quote:
                state = 'code'
                out.append(ch)
                i += 1
            else:
                out.append(ch)
                i += 1
    return ''.join(out)


def _js_object_block(text: str, marker: str) -> str:
    """Return the `{ ... }` object literal that follows `marker` (brace matched)."""
    start = text.index('{', text.index(marker))
    depth, i, quote = 0, start, None
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == '\\':
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in '\'"`':
            quote = ch
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    raise ValueError(f'unbalanced object literal after {marker!r}')


def _js_dict_entries(block: str) -> dict:
    """Map every `'key': 'value'` entry of a flat dictionary literal to its raw value."""
    entries = {}
    for m in re.finditer(r"'((?:[^'\\]|\\.)+)'\s*:\s*'((?:[^'\\]|\\.)*)'", block):
        entries.setdefault(m.group(1), m.group(2))
    return entries


def load_frontend_dictionaries():
    """Return (zh-CN, en-US) dictionaries parsed out of static/i18n.js."""
    text = _strip_js_comments((ROOT / 'static' / 'i18n.js').read_text(encoding='utf-8'))
    zh = _js_dict_entries(_js_object_block(text, "'zh-CN': {"))
    en = _js_dict_entries(_js_object_block(text, "'en-US': {"))
    return zh, en


def frontend_dictionary_problems(zh: dict, en: dict):
    """Key parity, placeholder parity and en-US purity for static/i18n.js."""
    problems = []
    only_zh = sorted(set(zh) - set(en))
    only_en = sorted(set(en) - set(zh))
    if only_zh:
        problems.append(f'Frontend keys only in zh-CN (would leak Chinese into en-US): {only_zh}')
    if only_en:
        problems.append(f'Frontend keys only in en-US: {only_en}')

    for key in set(zh) & set(en):
        ph_zh = sorted(re.findall(r'\{([A-Za-z0-9_]+)\}', zh[key]))
        ph_en = sorted(re.findall(r'\{([A-Za-z0-9_]+)\}', en[key]))
        if ph_zh != ph_en:
            problems.append(
                f"Frontend placeholder mismatch for '{key}': zh {ph_zh} vs en {ph_en}"
            )

    han = sorted(k for k, v in en.items() if _HAN.search(v))
    if han:
        problems.append(
            f'Frontend en-US values still containing Han characters ({len(han)}): {han[:12]}'
        )
    return problems


def _collect_frontend_referenced_keys() -> set:
    """Literal translation keys used by the frontend JS/HTML (t('k'), data-i18n="k")."""
    keys = set()
    for path in _frontend_files():
        if not path.exists():
            continue
        text = _strip_js_comments(path.read_text(encoding='utf-8'))
        # t('key') / tt('key') / html('key')
        keys.update(re.findall(r"\b(?:t|tt|html)\(\s*'((?:[^'\\]|\\.)+)'", text))
        # data-i18n="key", data-i18n-placeholder="key", data-i18n-title / data-i18n-html
        keys.update(re.findall(
            r'data-i18n(?:-placeholder|-title|-html)?="([^"]+)"', text))
    return keys


def frontend_reference_problems(zh: dict, en: dict):
    """Every key referenced by the frontend must exist in both frontend dictionaries."""
    defined = set(zh) | set(en)
    missing = sorted(_collect_frontend_referenced_keys() - defined)
    if missing:
        return [f'Frontend referenced but undefined keys: {missing}']
    return []


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

    # ---- frontend dictionary (static/i18n.js) ----
    fe_zh, fe_en = load_frontend_dictionaries()
    problems.extend(frontend_dictionary_problems(fe_zh, fe_en))
    problems.extend(frontend_reference_problems(fe_zh, fe_en))

    if problems:
        print('[i18n] inconsistencies found:', file=sys.stderr)
        for p in problems:
            print(f'  - {p}', file=sys.stderr)
        return 1

    print(f'[i18n] OK  backend zh-CN={len(zh_keys)} en-US={len(en_keys)}  '
          f'frontend zh-CN={len(fe_zh)} en-US={len(fe_en)}  '
          f'placeholder-parity, source coverage and en-US purity verified')
    return 0


if __name__ == '__main__':
    sys.exit(main())
