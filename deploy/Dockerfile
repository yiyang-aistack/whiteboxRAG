# whiteBoxRAG Dockerfile
# 轻量化Docker镜像，适用于8G内存环境

FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖（PDF/Office文档处理需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    # PDF处理依赖
    libpoppler-cpp-dev \
    # Office文档依赖
    libreoffice \
    # 其他工具
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制应用代码
COPY . .

# 创建存储目录
RUN mkdir -p storage/vectordb storage/documents storage/logs storage/tasks storage/monitor storage/traces

# 暴露端口
EXPOSE 8080

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

# 启动命令
# 使用gunicorn多工作进程，但限制为1个避免向量库冲突
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "api.api:app"]
