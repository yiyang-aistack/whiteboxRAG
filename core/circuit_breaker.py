"""
Circuit Breaker Module
State machine-based automatic circuit breaker mechanism, supports error rate circuit breaker and latency circuit breaker
State: closed(normal) -> open(circuit breaker) -> half_open(half-open detection)
"""
import time
from enum import Enum
from threading import Lock
from typing import Callable, Dict, Optional, Any

from config import config
from service.logger import get_logger

logger = get_logger('circuit_breaker')


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit breaker"""

    def __init__(self, name: str):
        self.name = name
        self.state = CircuitState.CLOSED

        self.failure_threshold = config.get('circuit_breaker.failure_threshold', 50)
        self.success_threshold = config.get('circuit_breaker.success_threshold', 5)
        self.timeout_seconds = config.get('circuit_breaker.timeout_seconds', 60)
        self.delay_threshold_ms = config.get('circuit_breaker.delay_threshold_ms', 30000)

        self._failure_count = 0
        self._success_count = 0
        self._total_requests = 0
        self._last_failure_time = 0
        self._last_reset_time = 0
        self._lock = Lock()

    def _reset(self):
        """Reset circuit breaker state"""
        with self._lock:
            self.state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._total_requests = 0
            self._last_reset_time = time.time()
            logger.info(f"[Circuit Breaker] {self.name} reset")

    def _open(self):
        """Open circuit breaker (circuit breaker state)"""
        with self._lock:
            if self.state != CircuitState.OPEN:
                self.state = CircuitState.OPEN
                self._last_failure_time = time.time()
                logger.warning(f"[Circuit Breaker] {self.name} tripped, failure rate exceeds threshold")

    def _try_half_open(self) -> bool:
        """Try entering half-open state (must be called outside lock, locks internally)"""
        with self._lock:
            if self.state != CircuitState.OPEN:
                return False

            elapsed = time.time() - self._last_failure_time
            if elapsed >= self.timeout_seconds:
                self.state = CircuitState.HALF_OPEN
                self._success_count = 0
                logger.info(f"[Circuit Breaker] {self.name} half-open, start detection")
                return True
            return False

    def _should_fuse(self) -> bool:
        """Determine whether to trip circuit breaker (must be called within lock)"""
        if self._total_requests < 10:
            return False

        failure_rate = (self._failure_count / self._total_requests) * 100
        return failure_rate >= self.failure_threshold

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute protected call

        Args:
            func: Function to execute
            *args: Function parameters
            **kwargs: Function keyword parameters

        Returns:
            Function execution result

        Raises:
            Exception: If circuit breaker is open or function execution fails
        """
        # 1. Check state (lock protected)
        with self._lock:
            if self.state == CircuitState.OPEN:
                # Try transitioning to HALF_OPEN
                if time.time() - self._last_failure_time >= self.timeout_seconds:
                    self.state = CircuitState.HALF_OPEN
                    self._success_count = 0
                    logger.info(f"[Circuit Breaker] {self.name} half-open, start detection")
                else:
                    # Still OPEN and timeout not reached
                    raise CircuitBreakerError(
                        f"Circuit breaker {self.name} is open, please try again later",
                        circuit_name=self.name,
                        state=self.state.value
                    )

        # 2. Execute function
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            elapsed_ms = (time.time() - start_time) * 1000

            with self._lock:
                self._total_requests += 1
                if elapsed_ms > self.delay_threshold_ms:
                    self._failure_count += 1
                    logger.warning(f"[Circuit Breaker] {self.name} timeout, duration: {elapsed_ms:.2f}ms")
                else:
                    if self.state == CircuitState.HALF_OPEN:
                        self._success_count += 1
                        if self._success_count >= self.success_threshold:
                            self._reset()

                if self._should_fuse():
                    self.state = CircuitState.OPEN
                    self._last_failure_time = time.time()
                    logger.warning(f"[Circuit Breaker] {self.name} tripped, failure rate exceeds threshold")

            return result

        except CircuitBreakerError:
            raise
        except Exception as e:
            with self._lock:
                self._total_requests += 1
                self._failure_count += 1
                self._last_failure_time = time.time()
                logger.error(f"[Circuit Breaker] {self.name} request failed: {e}")

                if self._should_fuse():
                    self.state = CircuitState.OPEN
                    logger.warning(f"[Circuit Breaker] {self.name} tripped, failure rate exceeds threshold")

            raise

    def get_status(self) -> Dict:
        """Get circuit breaker state"""
        with self._lock:
            failure_rate = (self._failure_count / self._total_requests * 100) if self._total_requests > 0 else 0
            return {
                'name': self.name,
                'state': self.state.value,
                'total_requests': self._total_requests,
                'failure_count': self._failure_count,
                'success_count': self._success_count,
                'failure_rate': round(failure_rate, 2),
                'last_failure_time': self._last_failure_time,
                'last_reset_time': self._last_reset_time,
                'time_until_half_open': max(0, self.timeout_seconds - (time.time() - self._last_failure_time))
            }


class CircuitBreakerError(Exception):
    """Circuit breaker error"""

    def __init__(self, message: str, circuit_name: str, state: str):
        super().__init__(message)
        self.circuit_name = circuit_name
        self.state = state


class CircuitBreakerManager:
    """Circuit breaker manager"""

    _breakers: Dict[str, CircuitBreaker] = {}
    _lock = Lock()

    @classmethod
    def get_breaker(cls, name: str) -> CircuitBreaker:
        """Get or create circuit breaker"""
        with cls._lock:
            if name not in cls._breakers:
                cls._breakers[name] = CircuitBreaker(name)
                logger.info(f"[Circuit Breaker Manager] Created circuit breaker: {name}")
            return cls._breakers[name]

    @classmethod
    def get_all_status(cls) -> Dict[str, Dict]:
        """Get all circuit breaker states"""
        return {name: breaker.get_status() for name, breaker in cls._breakers.items()}

    @classmethod
    def reset_all(cls):
        """Reset all circuit breakers"""
        with cls._lock:
            for breaker in cls._breakers.values():
                breaker._reset()
            logger.info("[Circuit Breaker Manager] Reset all circuit breakers")


circuit_breaker_manager = CircuitBreakerManager()
