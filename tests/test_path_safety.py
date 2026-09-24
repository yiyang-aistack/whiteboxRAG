#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Path safety regression tests.

Covers the helpers in ``service/path_safety.py`` and the endpoint that used to be exploitable
(``GET /api/document-optimizer/download/optimized?file_name=../../...``). These are static checks
and need neither a running server nor an LLM.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from service.path_safety import safe_join, sanitize_filename  # noqa: E402


class TestSanitizeFilename:
    """A client supplied name must never contain a directory component."""

    def test_posix_traversal_is_stripped(self):
        assert sanitize_filename('../../etc/passwd') == 'passwd'

    def test_windows_traversal_is_stripped(self):
        assert sanitize_filename('..\\..\\Windows\\win.ini') == 'win.ini'

    def test_mixed_separators(self):
        assert sanitize_filename('..\\../evil.txt') == 'evil.txt'

    def test_absolute_path_is_stripped(self):
        assert sanitize_filename('/etc/shadow') == 'shadow'

    def test_drive_letters_and_full_paths(self):
        # Browsers (and some clients) send a full path in the multipart filename
        assert sanitize_filename('C:\\fakepath\\report.pdf') == 'report.pdf'

    def test_plain_name_is_untouched(self):
        assert sanitize_filename('report.pdf') == 'report.pdf'

    def test_cjk_name_is_untouched(self):
        assert sanitize_filename('爱拼才会赢.pdf') == '爱拼才会赢.pdf'

    def test_stored_name_pattern_survives(self):
        assert sanitize_filename('abd7c11d_爱拼才会赢.pdf') == 'abd7c11d_爱拼才会赢.pdf'

    def test_empty_dot_and_dotdot_fall_back(self):
        for value in ('', '.', '..', None, '   '):
            assert sanitize_filename(value) == 'unnamed'

    def test_control_characters_are_removed(self):
        assert sanitize_filename('ev\x00il\n.txt') == 'evil.txt'


class TestSafeJoin:
    """safe_join must keep the result inside the base directory."""

    def test_normal_name_stays_inside(self):
        joined = safe_join('./storage/optimized_docs', 'doc_opt.txt')
        assert joined == Path('./storage/optimized_docs/doc_opt.txt')

    def test_traversal_cannot_escape(self):
        joined = safe_join('./storage/optimized_docs', '../../../pyproject.toml')
        assert joined.name == 'pyproject.toml'
        assert '..' not in joined.parts

    def test_windows_style_traversal_cannot_escape(self):
        joined = safe_join('./storage/optimized_docs', '..\\..\\pyproject.toml')
        assert joined.name == 'pyproject.toml'
        assert '..' not in joined.parts

    def test_multiple_segments_are_sanitized(self):
        joined = safe_join('./storage/documents', '../kb1', '../..\\file.pdf')
        assert joined.parts[0] == 'storage'
        assert 'kb1' in joined.parts
        assert joined.name == 'file.pdf'

    def test_dotdot_segments_are_neutralized(self):
        joined = safe_join('./storage/optimized_docs', 'x', '..', '..', 'y.txt')
        assert '..' not in joined.parts
        assert joined.name == 'y.txt'

    def test_containment_check_is_a_backstop(self, monkeypatch):
        # The resolved-containment check must still fire if sanitisation is ever bypassed
        import service.path_safety as path_safety

        monkeypatch.setattr(path_safety, 'sanitize_filename', lambda name: str(name))
        with pytest.raises(ValueError):
            path_safety.safe_join('./storage/optimized_docs', '../../etc/passwd')


class TestDownloadEndpointRejectsTraversal:
    """The endpoint that previously served arbitrary files must answer 400, not 200."""

    # Real router prefix is /api/document (see api/routes/document_optimizer.py)
    endpoint = '/api/document/download/optimized'

    @pytest.fixture(scope='class')
    def client(self):
        from fastapi.testclient import TestClient

        from api.api import app

        with TestClient(app) as test_client:
            yield test_client

    def test_traversal_is_rejected(self, client):
        response = client.get(self.endpoint, params={'file_name': '../../../pyproject.toml'})
        assert response.status_code == 400, response.text
        # pyproject.toml must never be served; [project] is its first section header
        assert '[project]' not in response.text[:2000]

    def test_windows_traversal_is_rejected(self, client):
        response = client.get(self.endpoint, params={'file_name': '..\\..\\pyproject.toml'})
        assert response.status_code == 400, response.text

    def test_absolute_path_is_rejected(self, client):
        response = client.get(self.endpoint, params={'file_name': '/etc/passwd'})
        assert response.status_code == 400, response.text

    def test_unknown_but_safe_name_is_not_found(self, client):
        response = client.get(self.endpoint, params={'file_name': 'does_not_exist.txt'})
        assert response.status_code == 404, response.text


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
