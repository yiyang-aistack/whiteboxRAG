"""
Logger module
Refer to generallogging specification, merge scattered module logs into unified files:
- app.log：Full log (INFO and above, all modules)
- error.log：Error log (ERROR and above, all modules)
- debug.log：DEBUG level (only enabled in DEBUG mode)

Each Log line format：Time - [ModuleTag] - LEVEL - Message
Roll by date, keep specified days.
"""
import logging
import os
import re
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from config import config


# ==================== Windows ANSI Terminal Enable ====================
if sys.platform == 'win32':
    # Windows 10+ need enable explicit processing of VT processing
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass


# ==================== ANSI Color Definition ====================
# Foreground color
_FG = {
    'black':   '30',
    'red':     '31',
    'green':   '32',
    'yellow':  '33',
    'blue':    '34',
    'magenta': '35',
    'cyan':    '36',
    'white':   '37',
    'gray':    '90',
    'bright_red':     '91',
    'bright_green':   '92',
    'bright_yellow':  '93',
    'bright_blue':    '94',
    'bright_magenta': '95',
    'bright_cyan':    '96',
}
# Style
_BOLD = '1'
_DIM = '2'
_RESET = '\033[0m'


def _ansi(color: str, bold: bool = False, dim: bool = False) -> str:
    """Generate ANSI prefix"""
    codes = []
    if bold:
        codes.append(_BOLD)
    if dim:
        codes.append(_DIM)
    codes.append(_FG.get(color, '37'))
    return f'\033[{";".join(codes)}m'


# Log level -> Color mapping
_LEVEL_COLORS = {
    logging.DEBUG:    ('gray',          False),
    logging.INFO:     ('green',         False),
    logging.WARNING:  ('yellow',        True),
    logging.ERROR:    ('red',           True),
    logging.CRITICAL: ('bright_magenta', True),
}

# Module tag -> Color mapping (grouped by functional domain for easy distinction)
_TAG_COLORS = {
    # API Layer - Blue
    'API':       'bright_blue',
    'CHAT':      'bright_cyan',
    'KB':        'blue',
    'EVAL':      'bright_magenta',
    'DOCOPT':    'cyan',
    'SCENARIO':  'bright_blue',
    'MONITOR':   'blue',
    # Service Layer - Magenta
    'TASK':      'magenta',
    'SCHED':     'magenta',
    # Core Layer - Cyan
    'BOUNDARY':  'bright_cyan',
    'BREAKER':   'bright_red',
    'LLM':       'bright_yellow',
    'PIPELINE':  'cyan',
    'PARSER':    'bright_green',
    'INTENT':    'bright_cyan',
    'RETRIEVE':  'bright_green',
    'RECALL':    'green',
    'REWRITE':   'cyan',
    'TRACE':     'gray',
    'RULE':      'bright_magenta',
    'SENTTRACE': 'green',
    'TYPO':      'yellow',
    'VECTOR':    'bright_blue',
    'DOCANALYZE': 'bright_green',
}


# Module name -> Business tag mapping
_MODULE_TAGS = {
    # API Layer
    'api.api': 'API',
    'api.chat': 'CHAT',
    'api.knowledge': 'KB',
    'api.evaluation': 'EVAL',
    'api.document_optimizer': 'DOCOPT',
    'api.scenario': 'SCENARIO',
    'api.monitor': 'MONITOR',
    # Service Layer
    'monitor': 'MONITOR',
    'async_tasks': 'TASK',
    'scheduler': 'SCHED',
    # Core Layer
    'boundary_detector': 'BOUNDARY',
    'circuit_breaker': 'BREAKER',
    'llm_adapter': 'LLM',
    'llm_pipeline': 'PIPELINE',
    'document_parser': 'PARSER',
    'evaluator': 'EVAL',
    'intent_classifier': 'INTENT',
    'retriever': 'RETRIEVE',
    'recall_diagnostic': 'RECALL',
    'query_rewriter': 'REWRITE',
    'trace': 'TRACE',
    'rule_engine': 'RULE',
    'sentence_tracing': 'SENTTRACE',
    'typo_checker': 'TYPO',
    'vector_store': 'VECTOR',
    # Tool
    'doc_analyzer': 'DOCANALYZE',
}


class TaggedFormatter(logging.Formatter):
    """Inject functional tags [TAG] into log lines"""

    def __init__(self, fmt=None, datefmt=None):
        super().__init__(fmt, datefmt)

    def format(self, record):
        # Get module tag: prioritize exact match, then short name
        tag = _MODULE_TAGS.get(record.name)
        if tag is None:
            # core.retriever -> retriever; api.chat -> api.chat
            short_name = record.name.split('.')[-1]
            tag = _MODULE_TAGS.get(short_name, record.name.upper())
        # Inject tag field into record
        record.tag = tag
        return super().format(record)


class ColoredFormatter(TaggedFormatter):
    """
    Colored console formatter - inherit from TaggedFormatter.

    Coloring strategy:
    - Timestamp: dim (dim)
    - [TAG] by module tag
    - LEVEL by log level (WARNING+ bold)
    - Message: default color
    """

    # Precompiled regex: match "Timestamp - [TAG] - LEVEL - Message" format
    # Format: "2026-08-03 21:03:18,698 - [RETRIEVE] - INFO - content"
    _LINE_PATTERN = re.compile(
        r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - \[([A-Z]+)\] - (\w+) - (.*)$',
        re.DOTALL
    )

    def format(self, record):
        # First call parent class to do tag injection and basic formatting
        formatted = super().format(record)

        # Get log level color and bold style
        color, bold = _LEVEL_COLORS.get(
            record.levelno, ('white', False)
        )

        # Get module tag color: default gray
        tag_color = _TAG_COLORS.get(record.tag, 'gray')

        # Color by "Timestamp - [TAG] - LEVEL - Message" format
        match = self._LINE_PATTERN.match(formatted)

        if match:
            timestamp, tag, level, message = match.groups()

            # Assemble colored output
            parts = [
                _ansi('gray', dim=True) + timestamp + _RESET,
                ' - ',
                _ansi(tag_color, bold=True) + f'[{tag}]' + _RESET,
                ' - ',
                _ansi(color, bold=bold) + level + _RESET,
                ' - ',
            ]

            # Color message by log level: ERROR red, WARNING yellow, other default color
            if record.levelno >= logging.ERROR:
                parts.append(_ansi('red') + message + _RESET)
            elif record.levelno >= logging.WARNING:
                parts.append(_ansi('yellow') + message + _RESET)
            else:
                parts.append(message)

            return ''.join(parts)
        else:
            # Format not match (like multi-line traceback), only color by log level bold
            return _ansi(color, bold=bold) + formatted + _RESET


class LoggerManager:
    """Log manager singleton - Singleton pattern, all modules share the same file handler"""

    _instance = None
    _loggers = {}
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        """Initialize log manager, create unified file handler"""
        if self._initialized:
            return

        self.log_dir = Path(config.get('logging.directory', './storage/logs'))
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.log_level = getattr(logging, config.get('logging.level', 'INFO').upper())
        self.retention_days = config.get('logging.retention_days', 30)

        # Log format: Timestamp - [TAG] - LEVEL - Message
        self.log_format = config.get(
            'logging.format',
            '%(asctime)s - [%(tag)s] - %(levelname)s - %(message)s'
        )

        # File use plain text formatter, console use colored formatter
        self.formatter = TaggedFormatter(self.log_format)
        self.colored_formatter = ColoredFormatter(self.log_format)

        # === Reference Tomcat catalina.log ===
        # 1. app.log：Full log (INFO+), rotated daily by midnight
        self.app_handler = TimedRotatingFileHandler(
            self.log_dir / 'app.log',
            when='midnight',
            interval=1,
            backupCount=self.retention_days,
            encoding='utf-8'
        )
        self.app_handler.setLevel(self.log_level)
        self.app_handler.setFormatter(self.formatter)
        self.app_handler.suffix = "%Y-%m-%d"

        # 2. error.log：Error log (ERROR+), rotated daily by midnight
        self.error_handler = TimedRotatingFileHandler(
            self.log_dir / 'error.log',
            when='midnight',
            interval=1,
            backupCount=self.retention_days,
            encoding='utf-8'
        )
        self.error_handler.setLevel(logging.ERROR)
        self.error_handler.setFormatter(self.formatter)
        self.error_handler.suffix = "%Y-%m-%d"

        # 3. debug.log：Debug log (DEBUG+), rotated daily by midnight (only when DEBUG mode enabled)
        self.debug_handler = None
        if self.log_level <= logging.DEBUG:
            self.debug_handler = TimedRotatingFileHandler(
                self.log_dir / 'debug.log',
                when='midnight',
                interval=1,
                backupCount=self.retention_days,
                encoding='utf-8'
            )
            self.debug_handler.setLevel(logging.DEBUG)
            self.debug_handler.setFormatter(self.formatter)
            self.debug_handler.suffix = "%Y-%m-%d"

        self._initialized = True

    def get_logger(self, name: str = 'app') -> logging.Logger:
        """
        Get logger instance by name.
        All loggers share same app.log / error.log file,
        distinguish by module [TAG] tag.

        Args:
            name: Logger name (used for tag mapping)

        Returns:
            Configured Logger instance
        """
        if name in self._loggers:
            return self._loggers[name]

        logger = logging.getLogger(name)
        logger.setLevel(self.log_level)
        logger.propagate = False

        # Console handler (output colored logs to stdout)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(self.log_level)
        console_handler.setFormatter(self.colored_formatter)
        logger.addHandler(console_handler)

        # Unified file handler: all modules write to app.log (plain text, no color codes)
        logger.addHandler(self.app_handler)
        # Unified error handler: all modules' ERROR writes to error.log
        logger.addHandler(self.error_handler)
        # DEBUG separate file
        if self.debug_handler:
            logger.addHandler(self.debug_handler)

        self._loggers[name] = logger
        return logger

    def set_level(self, level: str):
        """
        Set log level dynamically.

        Args:
            level: Log level name
        """
        self.log_level = getattr(logging, level.upper())
        for logger in self._loggers.values():
            logger.setLevel(self.log_level)
            for handler in logger.handlers:
                if handler is not self.error_handler:
                    handler.setLevel(self.log_level)


# Global log manager
logger_manager = LoggerManager()


def get_logger(name: str = 'app') -> logging.Logger:
    """Get logger instance by name."""
    return logger_manager.get_logger(name)
