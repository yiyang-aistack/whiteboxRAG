# -*- coding: utf-8 -*-
"""
Unit tests for async task kb_id association + filtering.

These validate the knowledge-base scoping fix added so that GET
/api/knowledge/{kb_id}/status returns only that KB's tasks instead of
every KB's tasks (previously the data was not scoped). No LLM / filesystem
writes occur here: a detached AsyncTaskManager instance is used instead of
the process-wide singleton.
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from service.async_tasks import AsyncTaskManager  # noqa: E402


def _manager(tmp_path):
    m = AsyncTaskManager.__new__(AsyncTaskManager)
    m.task_dir = Path(str(tmp_path)) / 'tasks'
    m.task_dir.mkdir(parents=True, exist_ok=True)
    m.timeout = 300
    m.cleanup_interval = 1_000_000_000  # effectively disable auto cleanup in tests
    m.tasks = {}
    m._lock = threading.Lock()
    m._last_cleanup = time.time()
    return m


def _echo(**kwargs):
    return kwargs


def test_kb_id_stored_as_metadata_and_in_func_kwargs(tmp_path):
    m = _manager(tmp_path)
    tid = m.create_task('kb-task', _echo, kb_id='kb_alpha', x=1)

    task = m.tasks[tid]
    # stored as metadata on the task for filtering...
    assert task.kb_id == 'kb_alpha'
    # ...and still forwarded to the wrapped function at execution time.
    assert task.kwargs['kb_id'] == 'kb_alpha'
    assert task.kwargs['x'] == 1


def test_create_without_kb_id_yields_none(tmp_path):
    m = _manager(tmp_path)
    tid = m.create_task('plain', _echo)
    assert m.tasks[tid].kb_id is None


def test_list_tasks_filters_by_kb_id(tmp_path):
    m = _manager(tmp_path)
    ta = m.create_task('alpha-1', _echo, kb_id='kb_a')
    tb = m.create_task('alpha-2', _echo, kb_id='kb_a')
    tx = m.create_task('other', _echo, kb_id='kb_b')
    tn = m.create_task('no-kb', _echo)

    names = {t['name'] for t in m.list_tasks(kb_id='kb_a')}
    assert names == {'alpha-1', 'alpha-2'}
    assert 'other' not in names

    names_b = {t['name'] for t in m.list_tasks(kb_id='kb_b')}
    assert names_b == {'other'}

    all_names = {t['name'] for t in m.list_tasks()}
    assert all_names == {'alpha-1', 'alpha-2', 'other', 'no-kb'}


def test_list_tasks_status_filter(tmp_path):
    m = _manager(tmp_path)
    m.create_task('pending-task', _echo, kb_id='kb_a')
    # A second call path: status filter defaults to nothing when not provided.
    items = m.list_tasks()
    assert all('status' in t for t in items)


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
