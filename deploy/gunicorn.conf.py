"""
Gunicorn config file
For production deployment
"""
import multiprocessing
import os

# Service bind address
bind = "0.0.0.0:8080"

# Number of worker processes (lightweight deployment uses 1 process)
workers = 1

# Number of threads per worker process
threads = 4

# Timeout (seconds)
timeout = 120

# Keepalive duration
keepalive = 5

# Log level
loglevel = os.getenv("LOG_LEVEL", "info")

# Access log
accesslog = "-"

# Error log
errorlog = "-"

# Log format
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Preload app (avoid multi-process loading issues)
preload_app = True

# Process name
proc_name = "whiteboxrag"

# Max requests (prevent memory leaks)
max_requests = 1000
max_requests_jitter = 50

# Graceful restart
graceful_timeout = 30
