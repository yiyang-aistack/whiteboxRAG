"""
Scheduled task scheduler module.
Based on APScheduler to schedule tasks like document optimization.
"""
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from config import config
from .logger import get_logger

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False
    BackgroundScheduler = None
    CronTrigger = None

logger = get_logger('scheduler')


class ScheduledTaskManager:
    """Scheduled task manager."""

    def __init__(self):
        self.scheduler = None
        self._lock = threading.Lock()
        self._is_running = False
        self._registered_tasks = {}

    def init_scheduler(self):
        """Initialize scheduler"""
        if not APSCHEDULER_AVAILABLE:
            logger.warning("APScheduler not installed, scheduled task feature is not available.")
            return

        if self.scheduler is not None:
            return

        self.scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        logger.info("Scheduled task scheduler initialized successfully.")

    def start(self):
        """ Start the scheduled task scheduler."""
        if not APSCHEDULER_AVAILABLE:
            return

        if self.scheduler is None:
            self.init_scheduler()

        if self.scheduler and not self._is_running:
            self.scheduler.start()
            self._is_running = True
            logger.info("Scheduled task scheduler started successfully.")

    def stop(self):
        """ Stop the scheduled task scheduler."""
        if self.scheduler and self._is_running:
            self.scheduler.shutdown(wait=True)
            self._is_running = False
            logger.info("Scheduled task scheduler stopped successfully.")

    def add_document_optimization_task(self, cron_expression: str = "0 2 * * *"):
        """
        Add document optimization task.

        Args:
            cron_expression: Cron expression to schedule the task, default is every day at 00:00:02.
        """
        if not APSCHEDULER_AVAILABLE:
            logger.warning("APScheduler not installed, cannot add scheduled task.")
            return

        if self.scheduler is None:
            self.init_scheduler()

        def run_optimization():
            """Run document optimization task."""
            logger.info("Running document optimization task...")
            try:
                from docAnalyze import DocumentAnalyzer
                analyzer = DocumentAnalyzer()
                analyzer.run_full_analysis()
                logger.info("Document optimization task completed successfully.")
            except Exception as e:
                logger.error(f"Document optimization task failed with error: {e}", exc_info=True)

        job = self.scheduler.add_job(
            run_optimization,
            trigger=CronTrigger.from_crontab(cron_expression),
            id="document_optimization",
            name="Document optimization",
            replace_existing=True
        )

        self._registered_tasks["document_optimization"] = {
            'id': "document_optimization",
            'name': "Document optimization",
            'cron_expression': cron_expression,
            'next_run_time': job.next_run_time.isoformat() if job.next_run_time else None
        }

        logger.info(f"  Scheduled task: Document optimization, Cron: {cron_expression}")

    def add_daily_analysis_task(self, cron_expression: str = "0 8 * * *"):
        """
        Add daily document analysis task.

        Args:
            cron_expression: Cron expression to schedule the task, default is every day at 08:00:00.
        """
        if not APSCHEDULER_AVAILABLE:
            logger.warning("APScheduler not installed, cannot add scheduled task.")
            return

        if self.scheduler is None:
            self.init_scheduler()

        def run_daily_analysis():
            """Run daily document analysis task."""
            logger.info("Running daily document analysis task...")
            try:
                from docAnalyze import DocumentAnalyzer
                analyzer = DocumentAnalyzer()
                analyzer.load_source_documents()
                analyzer.load_conversation_logs()
                analyzer.analyze_document_coverage()
                analyzer.analyze_query_effectiveness()
                issues = analyzer.identify_document_issues()
                if issues:
                    logger.warning(f"Daily document analysis found {len(issues)} document issues.")
                logger.info("Daily document analysis task completed successfully.")
            except Exception as e:
                logger.error(f"Daily document analysis task failed with error: {e}", exc_info=True)

        job = self.scheduler.add_job(
            run_daily_analysis,
            trigger=CronTrigger.from_crontab(cron_expression),
            id="daily_analysis",
            name="Daily document analysis",
            replace_existing=True
        )

        self._registered_tasks["daily_analysis"] = {
            'id': "daily_analysis",
            'name': "Daily document analysis",
            'cron_expression': cron_expression,
            'next_run_time': job.next_run_time.isoformat() if job.next_run_time else None
        }

        logger.info(f"  Scheduled task: Daily document analysis, Cron: {cron_expression}")

    def get_scheduled_tasks(self) -> Dict:
        """Get all registered scheduled tasks."""
        if not APSCHEDULER_AVAILABLE or not self.scheduler:
            return {'tasks': [], 'running': False}

        tasks = []
        for job in self.scheduler.get_jobs():
            tasks.append({
                'id': job.id,
                'name': job.name,
                'trigger': str(job.trigger),
                'next_run_time': job.next_run_time.isoformat() if job.next_run_time else None,
                'status': 'running' if self._is_running else 'stopped'
            })

        return {
            'tasks': tasks,
            'running': self._is_running,
            'registered_tasks': self._registered_tasks
        }

    def remove_task(self, task_id: str) -> bool:
        """
        Remove scheduled task by ID.

        Args:
            task_id: Task ID.

        Returns:
            Whether removal was successful.
        """
        if not APSCHEDULER_AVAILABLE or not self.scheduler:
            return False

        try:
            self.scheduler.remove_job(task_id)
            if task_id in self._registered_tasks:
                del self._registered_tasks[task_id]
            logger.info(f"  Removed scheduled task with ID: {task_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to remove scheduled task with ID {task_id}: {e}")
            return False

    def run_task_now(self, task_id: str) -> bool:
        """
        Run scheduled task immediately.

        Args:
            task_id: Task ID.

        Returns:
            Whether execution was successful.
        """
        if not APSCHEDULER_AVAILABLE or not self.scheduler:
            return False

        try:
            job = self.scheduler.get_job(task_id)
            if job:
                job.modify(next_run_time=datetime.now())
                logger.info(f"  Ran scheduled task with ID: {task_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to run scheduled task with ID {task_id}: {e}")
            return False


# Global scheduled task manager
scheduler_manager = ScheduledTaskManager()


def start_scheduler():
    """Start scheduled task manager."""
    scheduler_manager.start()


def stop_scheduler():
    """Stop scheduled task manager."""
    scheduler_manager.stop()


def init_scheduled_tasks():
    """ Initialize scheduled tasks."""
    scheduler_config = config.get('scheduler', {})

    if scheduler_config.get('enabled', False):
        logger.info("Scheduled task manager enabled.")

        if scheduler_config.get('document_optimization_enabled', False):
            cron_expr = scheduler_config.get('document_optimization_cron', "0 2 * * *")
            scheduler_manager.add_document_optimization_task(cron_expr)

        if scheduler_config.get('daily_analysis_enabled', False):
            cron_expr = scheduler_config.get('daily_analysis_cron', "0 8 * * *")
            scheduler_manager.add_daily_analysis_task(cron_expr)

        scheduler_manager.start()
    else:
        logger.info("Scheduled task manager disabled.")
