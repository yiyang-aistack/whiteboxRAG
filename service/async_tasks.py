"""
Async task management module
Local async tasks based on threading, supports progress tracking and status query
"""
import json
import threading
import time
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config import config
from .logger import get_logger
from .path_safety import sanitize_filename

logger = get_logger('async_tasks')


class TaskStatus(str, Enum):
    """Task status enum"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AsyncTask:
    """Async task wrapper"""

    def __init__(self, task_id: str, name: str, func: Callable, *args, **kwargs):
        self.task_id = task_id
        self.name = name
        self.func = func
        self.args = args
        self.kwargs = kwargs
        # Business association: if the task is created with a kb_id among its kwargs, it is
        # recorded as task metadata so per-knowledge-base task filtering works
        # (see knowledge.py get_kb_status and AsyncTaskManager.list_tasks). kb_id is left in
        # kwargs as well because the wrapped function also consumes it.
        self.kb_id = kwargs.get('kb_id')
        self.status = TaskStatus.PENDING
        self.progress = 0.0
        self.message = "Pending"
        self.result: Optional[Any] = None
        self.error: Optional[str] = None
        self.created_at = datetime.now().isoformat()
        self.started_at: Optional[str] = None
        self.completed_at: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._cancel_flag = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'task_id': self.task_id,
            'name': self.name,
            'status': self.status.value,
            'progress': self.progress,
            'message': self.message,
            'result': self.result,
            'error': self.error,
            'created_at': self.created_at,
            'started_at': self.started_at,
            'completed_at': self.completed_at,
            'kb_id': self.kb_id
        }


class AsyncTaskManager:
    """Async task manager - Singleton pattern"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        """Initialize task manager"""
        self.task_dir = Path(config.get('async_tasks.task_directory', './storage/tasks'))
        self.task_dir.mkdir(parents=True, exist_ok=True)

        self.timeout = config.get('async_tasks.timeout', 300)
        self.cleanup_interval = config.get('async_tasks.cleanup_interval', 3600)

        self.tasks: Dict[str, AsyncTask] = {}
        self._lock = threading.Lock()
        self._last_cleanup = time.time()

    def create_task(self, name: str, func: Callable, *args, **kwargs) -> str:
        """
        Create async task

        Args:
            name: Task name
            func: Execution function
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function. If a ``kb_id`` key is present it is
                      recorded as task metadata for per-knowledge-base filtering but also still
                      forwarded to ``func`` at execution time.

        Returns:
            Task ID
        """
        self._maybe_cleanup()

        task_id = str(uuid.uuid4())
        task = AsyncTask(task_id, name, func, *args, **kwargs)

        with self._lock:
            self.tasks[task_id] = task

        logger.info(f"Created task: {name} ({task_id})")
        return task_id

    def start_task(self, task_id: str) -> bool:
        """
        Start task

        Args:
            task_id: Task ID

        Returns:
            Whether the task was successfully started
        """
        with self._lock:
            task = self.tasks.get(task_id)

        if not task or task.status != TaskStatus.PENDING:
            return False

        def progress_callback(progress: float, message: str = ""):
            """Progress callback function, injected into task function"""
            task.progress = max(0.0, min(100.0, progress))
            if message:
                task.message = message
            self._save_task(task)

        def run_task():
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now().isoformat()
            task.message = "Running"
            try:
                # Pass progress callback as keyword argument
                kwargs = task.kwargs.copy()
                kwargs['progress_callback'] = progress_callback
                result = task.func(*task.args, **kwargs)

                if task._cancel_flag:
                    task.status = TaskStatus.CANCELLED
                    task.message = "Cancelled"
                else:
                    task.status = TaskStatus.COMPLETED
                    task.progress = 100.0
                    task.message = "Completed"
                    task.result = result

            except Exception as e:
                task.status = TaskStatus.FAILED
                task.error = str(e)
                task.message = f"Failed: {str(e)}"
                logger.error(f"Task failed {task_id}: {e}", exc_info=True)
            finally:
                task.completed_at = datetime.now().isoformat()
                self._save_task(task)

        task._thread = threading.Thread(target=run_task, daemon=True)
        task._thread.start()

        logger.info(f"Started task: {task.name} ({task_id})")
        return True

    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        Get task status

        Args:
            task_id: Task ID

        Returns:
            Task status dictionary
        """
        with self._lock:
            task = self.tasks.get(task_id)

        if not task:
            # Try loading from disk
            # task_id arrives from the URL: keep it a single path segment.
            task_file = self.task_dir / f'{sanitize_filename(task_id)}.json'
            if task_file.exists():
                try:
                    with open(task_file, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except Exception:
                    pass
            return None

        return task.to_dict()

    def cancel_task(self, task_id: str) -> bool:
        """
        Cancel task (cooperative cancellation)

        Args:
            task_id: Task ID

        Returns:
            Whether the task was successfully cancelled
        """
        with self._lock:
            task = self.tasks.get(task_id)

        if not task or task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
            return False

        task._cancel_flag = True
        task.status = TaskStatus.CANCELLED
        task.message = "Cancelled"
        logger.info(f"Cancelled task: {task.name} ({task_id})")
        return True

    def list_tasks(self, status: Optional[str] = None, limit: int = 50, kb_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List tasks

        Args:
            status: Optional, filter by status
            limit: Return count limit
            kb_id: Optional, filter by knowledge base id

        Returns:
            Task list
        """
        with self._lock:
            tasks = list(self.tasks.values())

        if status:
            tasks = [t for t in tasks if t.status.value == status]

        if kb_id is not None:
            tasks = [t for t in tasks if t.kb_id == kb_id]

        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return [t.to_dict() for t in tasks[:limit]]

    def _save_task(self, task: AsyncTask):
        """Save task status to disk"""
        try:
            task_file = self.task_dir / f'{sanitize_filename(task.task_id)}.json'
            with open(task_file, 'w', encoding='utf-8') as f:
                json.dump(task.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save task status: {e}")

    def _maybe_cleanup(self):
        """Periodically clean up expired tasks"""
        now = time.time()
        if now - self._last_cleanup < self.cleanup_interval:
            return

        self._last_cleanup = now
        try:
            cutoff = now - (self.timeout * 3)
            for task_file in self.task_dir.glob('*.json'):
                if task_file.stat().st_mtime < cutoff:
                    task_file.unlink()
        except Exception as e:
            logger.error(f"Failed to clean up task files: {e}")


# Global task manager
task_manager = AsyncTaskManager()
