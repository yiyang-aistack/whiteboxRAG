#!/bin/bash
# whiteBoxRAG 启动脚本

# 配置
APP_NAME="whiteBoxRAG"
HOST="0.0.0.0"
PORT=${PORT:-8080}
WORKERS=${WORKERS:-1}
LOG_LEVEL=${LOG_LEVEL:-info}

# 颜色输出
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

# 检查Python版本
check_python() {
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
        log_info "Python版本: $PYTHON_VERSION"
    else
        log_error "未找到Python3，请先安装Python 3.12+"
        exit 1
    fi
}

# 检查Ollama服务
check_ollama() {
    log_info "检查Ollama服务..."
    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        log_info "Ollama服务正常"

        # 检查模型
        MODELS=$(curl -s http://localhost:11434/api/tags | grep -o '"name":"[^"]*"' | wc -l)
        if [ "$MODELS" -eq 0 ]; then
            log_warn "未检测到Ollama模型，请运行以下命令下载："
            echo "  - ollama pull qwen2.5:7b"
            echo "  - ollama pull nomic-embed-text:latest"
        else
            log_info "已安装 $MODELS 个模型"
        fi
    else
        log_error "Ollama服务未运行，请先启动Ollama："
        echo "  - 在后台运行: ollama serve"
        exit 1
    fi
}

# 安装依赖
install_deps() {
    if [ ! -f "requirements.txt" ]; then
        log_error "未找到requirements.txt"
        exit 1
    fi

    log_info "安装Python依赖..."

    # 检查虚拟环境
    if [ -z "$VIRTUAL_ENV" ]; then
        if [ ! -d "venv" ]; then
            log_info "创建虚拟环境..."
            python3 -m venv venv
        fi
        source venv/bin/activate
    fi

    pip install --upgrade pip
    pip install -r requirements.txt

    log_info "依赖安装完成"
}

# 创建存储目录
create_dirs() {
    log_info "创建存储目录..."
    mkdir -p storage/vectordb
    mkdir -p storage/documents
    mkdir -p storage/logs
    mkdir -p storage/tasks
    mkdir -p storage/monitor
    mkdir -p storage/traces
    log_info "存储目录创建完成"
}

# 启动服务
start_server() {
    log_info "启动 $APP_NAME 服务..."
    log_info "监听地址: http://$HOST:$PORT"
    log_info "前端地址: http://$HOST:$PORT/static/index.html"

    # 使用uvicorn开发模式启动
    # 生产环境建议使用gunicorn
    python3 -m uvicorn api.api:app \
        --host $HOST \
        --port $PORT \
        --workers $WORKERS \
        --log-level $LOG_LEVEL \
        --access-log
}

# 主函数
main() {
    echo "========================================"
    echo " $APP_NAME 企业级私有化RAG系统"
    echo "========================================"
    echo ""

    check_python
    check_ollama
    create_dirs

    if [ "$1" == "install" ]; then
        install_deps
    elif [ "$1" == "dev" ]; then
        install_deps
        start_server
    else
        # 直接启动
        start_server
    fi
}

main "$@"
