#!/bin/bash
# whiteBoxRAG Startup Script

# Configuration
APP_NAME="whiteBoxRAG"
HOST="0.0.0.0"
PORT=${PORT:-8080}
WORKERS=${WORKERS:-1}
LOG_LEVEL=${LOG_LEVEL:-info}

# Color Print Configuration
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check uv Installation
check_uv() {
    if command -v uv &> /dev/null; then
        log_info "uv version: $(uv --version)"
    else
        log_error "uv not found, please install uv, please install uv first:"
        echo "  - macOS/Linux: curl -LsSf https://astral.sh/uv/install.sh | sh"
        echo "  - Any platform:     pip install uv"
        exit 1
    fi
}

# Check Ollama Service by default
check_ollama() {
    log_info "Checking Ollama Service..."
    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        log_info "Ollama Service is running"

        # Check models
        MODELS=$(curl -s http://localhost:11434/api/tags | grep -o '"name":"[^"]*"' | wc -l)
        if [ "$MODELS" -eq 0 ]; then
            log_warn "No Ollama models detected, please run the following commands to download them:"
            echo "  - ollama pull qwen2.5:7b"
            echo "  - ollama pull nomic-embed-text:latest"
        else
            log_info "Installed $MODELS models"
        fi
    else
        log_error "Ollama Service is not running, please start Ollama first by:"
        echo "  - In the background: ollama serve"
        exit 1
    fi
}

# Install dependencies
# uv sync: create .venv and install locked versions
install_deps() {
    if [ ! -f "pyproject.toml" ] || [ ! -f "uv.lock" ]; then
        log_error "No pyproject.toml / uv.lock found, please run this script from the main project root directory."
        exit 1
    fi

    log_info "Running uv sync --locked..."
    uv sync --locked

    log_info "Dependencies synchronized to .venv"
}

# Create storage directories
create_dirs() {
    log_info "Creating storage directories..."
    mkdir -p storage/vectordb
    mkdir -p storage/documents
    mkdir -p storage/logs
    mkdir -p storage/tasks
    mkdir -p storage/monitor
    mkdir -p storage/traces
    log_info "Storage directories created"
}

# Start server
start_server() {
    log_info "Starting $APP_NAME server..."
    log_info "Listening address: http://$HOST:$PORT"
    log_info "Frontend address: http://$HOST:$PORT/static/index.html"

    # Start uvicorn development mode
    # Production environment recommends gunicorn
    uv run python -m uvicorn api.api:app \
        --host $HOST \
        --port $PORT \
        --workers $WORKERS \
        --log-level $LOG_LEVEL \
        --access-log
}

# Main Function
main() {
    echo "========================================"
    echo " $APP_NAME on-Prime RAG System"
    echo "========================================"
    echo ""

    check_uv
    check_ollama
    create_dirs

    if [ "$1" == "install" ]; then
        install_deps
    elif [ "$1" == "dev" ]; then
        install_deps
        start_server
    else
        # Start server
        start_server
    fi
}

main "$@"
