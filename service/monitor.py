"""
Performance monitor module.
Record request statistics, response time, error rate, etc.
"""
import json
import time
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import config
from .logger import get_logger

logger = get_logger('monitor')


class PerformanceMonitor:
    """Performance monitor class - Singleton pattern"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        """Initialize monitor."""
        self.enabled = config.get('monitoring.enabled', True)
        if not self.enabled:
            return

        self.data_path = Path(config.get('monitoring.data_path', './storage/monitor'))
        self.data_path.mkdir(parents=True, exist_ok=True)

        self.retention_hours = config.get('monitoring.retention_hours', 24)

        # Request statistics
        self._lock = threading.Lock()
        self._total_requests = 0
        self._total_errors = 0
        self._response_times = deque(maxlen=1000)

        # Endpoint statistics by request path
        self._endpoint_stats: Dict[str, Dict[str, Any]] = {}

        # System resource snapshots
        self._system_snapshots = deque(maxlen=60)

    def record_request(self, endpoint: str, duration: float, error: bool = False):
        """
        Record a request.

        Args:
            endpoint: Endpoint path
            duration: Response time in seconds
            error: Whether it's an error request
        """
        if not self.enabled:
            return

        with self._lock:
            self._total_requests += 1
            if error:
                self._total_errors += 1

            self._response_times.append(duration)

            # Endpoint statistics by request path
            if endpoint not in self._endpoint_stats:
                self._endpoint_stats[endpoint] = {
                    'count': 0,
                    'errors': 0,
                    'total_time': 0.0,
                    'avg_time': 0.0,
                    'max_time': 0.0,
                    'min_time': float('inf')
                }

            stats = self._endpoint_stats[endpoint]
            stats['count'] += 1
            if error:
                stats['errors'] += 1
            stats['total_time'] += duration
            stats['avg_time'] = stats['total_time'] / stats['count']
            stats['max_time'] = max(stats['max_time'], duration)
            stats['min_time'] = min(stats['min_time'], duration)

    def record_system_metrics(self):
        """Record system resource usage (CPU, memory)."""
        if not self.enabled:
            return

        try:
            import psutil
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage(str(self.data_path.parent))

            snapshot = {
                'timestamp': datetime.now().isoformat(),
                'cpu_percent': cpu_percent,
                'memory_percent': memory.percent,
                'memory_used_mb': round(memory.used / 1024 / 1024, 2),
                'memory_total_mb': round(memory.total / 1024 / 1024, 2),
                'disk_percent': disk.percent,
                'disk_used_gb': round(disk.used / 1024 / 1024 / 1024, 2),
                'disk_total_gb': round(disk.total / 1024 / 1024 / 1024, 2)
            }

            with self._lock:
                self._system_snapshots.append(snapshot)

        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"Failed to record system metrics: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """
        Get performance statistics.

        Returns:
            Statistics dictionary.
        """
        if not self.enabled:
            return {'enabled': False}

        with self._lock:
            response_times = list(self._response_times)
            endpoint_stats = {k: v.copy() for k, v in self._endpoint_stats.items()}
            system_snapshots = list(self._system_snapshots)

            avg_time = sum(response_times) / len(response_times) if response_times else 0
            error_rate = (self._total_errors / self._total_requests * 100) if self._total_requests > 0 else 0

        return {
            'enabled': True,
            'summary': {
                'total_requests': self._total_requests,
                'total_errors': self._total_errors,
                'error_rate': round(error_rate, 2),
                'avg_response_time': round(avg_time * 1000, 2),
                'recent_requests': len(response_times)
            },
            'endpoints': endpoint_stats,
            'recent_metrics': list(system_snapshots)[-10:] if system_snapshots else [],
            'timestamp': datetime.now().isoformat()
        }

    def reset_stats(self):
        """Reset statistics."""
        if not self.enabled:
            return

        with self._lock:
            self._total_requests = 0
            self._total_errors = 0
            self._response_times.clear()
            self._endpoint_stats.clear()
            self._system_snapshots.clear()

        logger.info("Performance statistics reset.")

    def save_snapshot(self):
        """Save monitoring snapshot to disk."""
        if not self.enabled:
            return

        try:
            snapshot_file = self.data_path / 'latest_snapshot.json'
            stats = self.get_stats()
            with open(snapshot_file, 'w', encoding='utf-8') as f:
                json.dump(stats, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save monitoring snapshot: {e}")


# Global performance monitor
monitor = PerformanceMonitor()


def monitor_middleware(endpoint: str):
    """
    Performance monitor middleware.

    Args:
        endpoint: Endpoint identifier.
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            start = time.time()
            error = False
            try:
                return func(*args, **kwargs)
            except Exception:
                error = True
                raise
            finally:
                duration = time.time() - start
                monitor.record_request(endpoint, duration, error)
        return wrapper
    return decorator
