# whiteBoxRAG Dockerfile
# Lightweight Docker image for production use
# Dependencies managed by uv (pyproject.toml + uv.lock); no requirements.txt

FROM python:3.12-slim

# uv binary: copied from official distroless image; version matches uv.lock
COPY --from=ghcr.io/astral-sh/uv:0.10.11 /uv /uvx /bin/

# Set working directory to /app for the application
WORKDIR /app

# uv run parameters
#   UV_COMPILE_BYTECODE     Compile bytecode, container starts faster
#   UV_LINK_MODE=copy       Copy hard links instead of sharing them inode
#   UV_NO_DEV=1             No dev group installed in production image (pytest / flake8)
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1

# Install system dependencies (PDF/Office document processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    # PDF processing dependencies
    libpoppler-cpp-dev \
    # Office document dependencies
    libreoffice \
    # MIME sniffing
    # （Windows python-magic-bin  DLL,refer to pyproject.toml）
    libmagic1 \
    # Other tools
    && rm -rf /var/lib/apt/lists/*

# Copy only the dependency list first to allow reusing the cache when source code changes
COPY pyproject.toml uv.lock ./

# Install uv dependencies (locked version)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

# Copy application code
COPY . .

# Put virtual environment's executables first in PATH so gunicorn / uvicorn can call them directly
ENV PATH="/app/.venv/bin:$PATH"

# Create storage directories
RUN mkdir -p storage/vectordb storage/documents storage/logs storage/tasks storage/monitor storage/traces

# Expose port
EXPOSE 8080

# Health check endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

# Start command to run the application
# Use gunicorn with multiple workers, but limit to 1 to avoid vector database conflicts
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "api.api:app"]
