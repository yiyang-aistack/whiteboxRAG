"""
Monitor API routes
Provides performance stats, log query and other endpoints
"""
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status

from config import config
from service.async_tasks import task_manager
from service.i18n import _, get_lang_from_request
from service.logger import get_logger
from service.monitor import monitor
from service.path_safety import sanitize_filename

logger = get_logger('api.monitor')

router = APIRouter(prefix="/api/monitor", tags=["Monitor"])


@router.get("/stats", summary="Get performance statistics")
async def get_stats(request: Request):
    """Get system performance statistics"""
    lang = get_lang_from_request(request)
    try:
        # Record system metrics
        monitor.record_system_metrics()

        stats = monitor.get_stats()

        # Add extra info
        stats['system_info'] = {
            'app_name': config.get('system.app_name'),
            'version': config.get('system.version')
        }

        # Add task statistics
        tasks = task_manager.list_tasks(limit=100)
        running_tasks = [t for t in tasks if t['status'] == 'running']
        completed_tasks = [t for t in tasks if t['status'] == 'completed']
        failed_tasks = [t for t in tasks if t['status'] == 'failed']

        stats['tasks'] = {
            'total': len(tasks),
            'running': len(running_tasks),
            'completed': len(completed_tasks),
            'failed': len(failed_tasks)
        }

        return {
            'success': True,
            'data': stats
        }

    except Exception as e:
        logger.error(f"Failed to get stats: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('monitor.stats_failed', lang)}: {str(e)}"
        )


@router.get("/logs", summary="Query logs")
async def get_logs(
    request: Request,
    level: str = Query("INFO", description="Log level"),
    lines: int = Query(100, description="Number of lines to return", ge=10, le=500),
    logger_name: str = Query("app", description="Logger name")
):
    """Query system logs (latest N lines)"""
    lang = get_lang_from_request(request)
    try:
        from pathlib import Path

        log_dir = Path(config.get('logging.directory', './storage/logs'))
        # logger_name comes from the query string: keep it a single path segment, otherwise
        # this endpoint can be used to read arbitrary *.log files on the host.
        log_file = log_dir / f'{sanitize_filename(logger_name)}.log'

        if not log_file.exists():
            return {
                'success': True,
                'data': [],
                'total': 0
            }

        # Read the last N lines
        with open(log_file, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()

        recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

        # Filter by level
        target_level = level.upper()
        if target_level != 'ALL':
            level_order = {'DEBUG': 0, 'INFO': 1, 'WARNING': 2, 'ERROR': 3, 'CRITICAL': 4}
            target_value = level_order.get(target_level, 1)

            filtered = []
            for line in recent_lines:
                # Simple log level parsing
                for lv, val in level_order.items():
                    if f' - {lv} -' in line and val >= target_value:
                        filtered.append(line)
                        break
            recent_lines = filtered

        return {
            'success': True,
            'data': [line.strip() for line in recent_lines],
            'total': len(recent_lines)
        }

    except Exception as e:
        logger.error(f"Failed to query logs: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('monitor.logs_query_failed', lang)}: {str(e)}"
        )


@router.get("/tasks", summary="Get task list")
async def list_tasks(
    request: Request,
    status_filter: Optional[str] = Query(None, description="Filter by task status"),
    limit: int = Query(20, description="Number of items to return", ge=1, le=100)
):
    """Get async task list"""
    lang = get_lang_from_request(request)
    try:
        tasks = task_manager.list_tasks(status=status_filter, limit=limit)
        return {
            'success': True,
            'data': tasks,
            'total': len(tasks)
        }

    except Exception as e:
        logger.error(f"Failed to get task list: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('monitor.tasks_failed', lang)}: {str(e)}"
        )


@router.get("/task/{task_id}", summary="Get task details")
async def get_task(task_id: str, request: Request):
    """Get detailed status of a specific task"""
    lang = get_lang_from_request(request)
    try:
        task = task_manager.get_task_status(task_id)

        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('monitor.task_not_found', lang)
            )

        return {
            'success': True,
            'data': task
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get task details: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('monitor.task_detail_failed', lang)}: {str(e)}"
        )


@router.post("/stats/reset", summary="Reset statistics")
async def reset_stats(request: Request):
    """Reset performance statistics"""
    lang = get_lang_from_request(request)
    try:
        monitor.reset_stats()
        return {
            'success': True,
            'message': _('monitor.stats_reset_done', lang)
        }

    except Exception as e:
        logger.error(f"Failed to reset stats: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('monitor.stats_reset_failed', lang)}: {str(e)}"
        )
