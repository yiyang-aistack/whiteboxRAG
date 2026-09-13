# whiteBoxRAG 部署手册

## 环境要求

### 最低配置
- **CPU**: 4核+
- **内存**: 8GB RAM
- **磁盘**: 20GB可用空间
- **系统**: Ubuntu 20.04+ / Windows 10+ / macOS 12+

### 软件依赖
- **Python**: 3.12+
- **Ollama**: 0.1.25+（已运行并加载模型）

## 快速部署

### 方式一：本地开发模式

```bash
# 1. 进入项目目录
cd whiteBoxRAG

# 2. 一键启动（自动检测并安装依赖）
bash deploy/start.sh dev

# 或分步执行
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn api.api:app --host 0.0.0.0 --port 8080
```

### 方式二：Gunicorn生产模式

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
gunicorn --bind 0.0.0.0:8080 --workers 1 --threads 4 --timeout 120 api.api:app

# 或使用配置文件
gunicorn -c deploy/gunicorn.conf.py api.api:app
```

### 方式三：Docker Compose 一键启动（推荐）

```bash
# Ollama + 模型拉取 + 应用，一条命令
docker compose up -d

# 查看日志 / 停止
docker compose logs -f app
docker compose down
```

`docker-compose.yml` 会自动设置 `OLLAMA_BASE_URL=http://ollama:11434`（容器内的 `localhost` 指向容器
自身，不能用来访问宿主机的 Ollama），并在应用启动前拉取 `qwen2.5:7b` 与 `nomic-embed-text`。
需要 Docker Compose v2（`docker compose`，不是旧版 `docker-compose`）。

### 方式四：Docker 单容器部署

```bash
# 1. 构建镜像
docker build -t whiteboxrag:latest -f deploy/Dockerfile .

# 2. 运行容器（必须显式指定宿主机 Ollama 地址）
docker run -d \
  --name rag-system \
  -p 8080:8080 \
  -v "$(pwd)/storage:/app/storage" \
  --memory=8g \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  whiteboxrag:latest

# 3. 查看日志
docker logs -f rag-system

# 4. 停止服务
docker stop rag-system && docker rm rag-system
```

> Windows PowerShell 下卷参数写作 `"${PWD}/storage:/app/storage"`。
>
> 若未设置 `OLLAMA_BASE_URL`，容器的 `/api/health` 仍可能正常返回，但每次大模型调用都会连接失败。

## Ollama模型配置

### 安装Ollama
```bash
# Linux/macOS
curl -fsSL https://ollama.com/install.sh | sh

# Windows
# 从 https://ollama.com/download 下载安装
```

### 下载模型
```bash
# LLM模型（7B量化，推荐）
ollama pull qwen2.5:7b

# 或其他7B模型
ollama pull llama2:7b
ollama pull mistral:7b

# Embedding模型（必需）
ollama pull nomic-embed-text

# 验证模型
ollama list
```

### 启动Ollama服务
```bash
# 后台运行
ollama serve

# 验证服务
curl http://localhost:11434/api/tags
```

## 配置说明

### 配置文件位置
- 主配置: `config/settings.yaml`

### 关键配置项

```yaml
# Ollama配置
ollama:
  llm_model: "qwen2.5:7b"        # LLM模型
  embedding_model: "nomic-embed-text"  # Embedding模型
  base_url: "http://localhost:11434"

# 检索配置
retriever:
  mode: "hybrid"               # 检索模式: vector/bm25/hybrid
  bm25_weight: 0.4             # BM25权重
  similarity_threshold: 0.3     # 相似度阈值

# 文档处理
document_parser:
  chunk_size: 512              # 分块大小
  max_file_size: 50            # 文件大小限制(MB)
```

### 自定义配置

```bash
# 修改配置后重启服务生效
# 开发模式：自动重载
# 生产模式：systemctl restart whiteboxrag
```

## 验证部署

### 1. 健康检查
```bash
curl http://localhost:8080/api/health
```

### 2. 创建知识库
```bash
curl -X POST http://localhost:8080/api/knowledge/create \
  -H "Content-Type: application/json" \
  -d '{"name": "测试知识库", "description": "测试用"}'
```

### 3. 上传文档
```bash
curl -X POST http://localhost:8080/api/knowledge/{kb_id}/upload \
  -F "file=@/path/to/document.pdf"
```

### 4. 测试问答
```bash
curl -X POST http://localhost:8080/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"kb_id": "{kb_id}", "query": "你好", "stream": true}'
```

### 5. 访问前端
```
浏览器打开: http://localhost:8080/static/index.html
```

## 常见问题

### 1. Ollama连接失败
```
错误: HTTPConnectionPool(host='localhost', port=11434)
解决: 确保Ollama服务正在运行: ollama serve
```

### 2. 模型加载失败
```
错误: model not found
解决: 重新下载模型: ollama pull qwen2:7b
```

### 3. 内存不足
```
错误: OOM (Out of Memory)
解决:
  - 关闭其他占用内存的程序
  - 降低模型量化等级: ollama pull qwen2:7b-instruct-q4_0
  - 修改Docker内存限制: --memory=8g
```

### 4. 端口被占用
```
错误: Port already in use
解决:
  - 方式1: 使用其他端口: PORT=8001 bash deploy/start.sh
  - 方式2: 杀死占用进程: lsof -ti:8080 | xargs kill
```

### 5. 中文乱码
```
解决: 确保文件编码为UTF-8
```

## 性能优化

### 1. 内存优化
- 使用量化模型（Q4_0, Q5_1）
- 限制并发请求数
- 定期清理向量库

### 2. 检索优化
```yaml
# 调整检索参数
vector_store:
  top_k: 5                    # 检索数量
similarity_threshold: 0.5    # 提高阈值减少返回
```

### 3. 启动优化
```bash
# 预加载模型
OLLAMA_HOST=0.0.0.0:11434 ollama serve &

# 使用多线程
gunicorn --threads 8 ...
```

## 监控维护

### 查看日志
```bash
# 实时日志
tail -f storage/logs/app.log

# 错误日志
tail -f storage/logs/app_error.log
```

### 清理数据
```bash
# 清理向量库
rm -rf storage/vectordb/*

# 清理日志
find storage/logs -name "*.log" -mtime +30 -delete
```

### 备份数据
```bash
# 备份存储目录
tar -czf storage_backup_$(date +%Y%m%d).tar.gz storage/

# 恢复备份
tar -xzf storage_backup_20240101.tar.gz
```

## 目录结构

```
whiteBoxRAG/
├── api/                      # API接口
│   ├── api.py              # FastAPI主应用
│   └── routes/             # 路由模块
├── config/                  # 配置模块
├── core/                    # 核心功能
│   ├── document_parser.py   # 文档解析
│   ├── vector_store.py     # 向量存储
│   ├── retriever.py        # 检索器
│   └── llm_pipeline.py     # LLM流水线
├── service/                  # 服务层
├── static/                  # 前端静态文件
├── storage/                  # 数据存储
│   ├── vectordb/           # ChromaDB向量库
│   ├── documents/          # 原始文档
│   ├── logs/               # 日志文件
│   ├── tasks/              # 异步任务
│   ├── monitor/            # 监控数据
│   └── traces/             # 溯源记录
├── deploy/                  # 部署脚本
│   ├── Dockerfile
│   ├── gunicorn.conf.py
│   └── start.sh
├── tests/                   # 单元测试
├── requirements.txt
└── README.md
```

## 技术支持

- 问题反馈: https://github.com/your-repo/whiteBoxRAG/issues
- 文档更新: 请参考项目Wiki
