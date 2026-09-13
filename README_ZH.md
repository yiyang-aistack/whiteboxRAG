# whiteBoxRAG

> **8G 内存的机器就能跑，每句话都能溯源。** whiteBoxRAG 是一套完全私有化的**白盒 RAG 系统，内置 RAG 调试台（RAG Debugger）** —— FastAPI + LlamaIndex + ChromaDB + Ollama，提供句子级溯源、未召回根因诊断和可 A/B 对比的检索流水线，而不是一个黑盒。

[English](README.md) | [中文文档](README_ZH.md)

![license](https://img.shields.io/badge/license-MIT-green)
![python](https://img.shields.io/badge/python-3.12%2B-blue)
![stack](https://img.shields.io/badge/FastAPI%20%7C%20LlamaIndex%200.10%20%7C%20ChromaDB-009688)
![LLM](https://img.shields.io/badge/LLM-Ollama--local-black)
![API](https://img.shields.io/badge/REST%20API-58%20endpoints-6E4AFF)
![RAG debugger](https://img.shields.io/badge/built--in-RAG%20debugger-blueviolet)
![tests](https://img.shields.io/badge/tests-pytest%20%2B%20GitHub%20Actions-0A9EDC)

![检索溯源面板：逐句可信度标签](assets/RagTrace.png)

### 30 秒上手

```bash
ollama pull qwen2.5:7b && ollama pull nomic-embed-text   # 一次性模型下载（约 5GB）

python main.py --install        # 终端 1：检查环境 + 安装依赖 + 启动服务（:8080）
python scripts/seed_demo.py     # 终端 2：建演示知识库、提问并打印逐句溯源结果
```

然后打开 <http://localhost:8080>。想用容器？`docker compose up -d` 一条命令即可拉起 Ollama、拉取模型并启动应用，见 [容器部署](#容器部署)。

<details>
<summary><code>python scripts/seed_demo.py</code> 的输出长这样</summary>

```text
==> Asking: 混合检索里 BM25 的默认权重是多少？

==============================================================================
ANSWER
==============================================================================
混合检索默认启用，BM25 的默认权重为 0.4，向量权重为 0.6。[1]

------------------------------------------------------------------------------
SENTENCE-LEVEL TRACING (the white-box part)
------------------------------------------------------------------------------
  [OK]   direct evidence in the retrieved chunks
        混合检索默认启用，BM25 的默认权重为 0.4，向量权重为 0.6。
  sentences=3  direct_quote=2  summary=1  drift=0  drift_rate=0.0

------------------------------------------------------------------------------
EVALUATION
------------------------------------------------------------------------------
  overall_score=0.86  is_passing=True
  retrieval_recall=1.0 (target=0.7, pass=True)
```

示意输出：具体措辞取决于模型、命中的分块与场景配置。
</details>

## 项目简介

whiteBoxRAG 是一套面向生产的 RAG 平台，所有组件都留在本地：文档解析、向量化、向量存储、混合检索、大模型推理、溯源与评估全部在本机完成。它面向需要"白盒"知识助手的团队 —— 数据不出内网，但回答必须可被检查；同时保留 OpenAI 作为可选的云端模型提供者。

名字就是承诺：不做黑盒。内置的 **RAG 调试台**会告诉你哪一句话依据的是哪个分块、某篇文档为什么没有被召回，让"检索/生成失败"可以被诊断，而不是靠猜。

系统自带免构建的 Web 前端、58 个 REST 端点、场景化配置、带引用校验的句子级溯源、业务边界（OOD）检测、能从用户反馈中沉淀规则的规则引擎、可 A/B 对比的检索流水线，以及一套内置评估框架。

## 核心亮点

| | 你能得到什么 |
|---|---|
| **句子级溯源** | 回答被拆成句子逐句向量化并与检索到的分块对齐，标注为*有文档依据*、*摘要*、*低置信度*或*无依据推断*，同时审计大模型被强制输出的 `[doc_id]` 引用标记。 |
| **文档为什么没被召回** | 未召回诊断回答"文档明明存在，为什么没出现"：元数据过滤、得分低于阈值、检索模式不匹配、关键词缺失，四类根因。 |
| **反馈沉淀为规则** | 答错、漏召回、意图判别错误会进入规则引擎；当某个模式超过 `rule_engine.min_feedback_count` 后自动成为业务规则，其生效命中率可通过接口回查。 |
| **调参靠证据，不靠感觉** | 场景化配置档、内置评估框架（召回率、忠实度、相关性、上下文利用率、幻觉率、拒答准确率）以及检索配置的 A/B 对比。 |
| **在数据所在的地方运行** | 8G 内存 / 4 核即可；不需要 Redis、MySQL 或消息队列。向量、文档、溯源、任务状态都是本地文件；推理走本地 Ollama，OpenAI 为可选项。 |
| **默认不外传任何数据** | 解析、向量化、检索、生成全部在本机；敏感配置来自 `.env`，不落库、不进 YAML。 |

## 效果预览

| 检索溯源面板（业务 + 技术视图） | A/B 测试配置 | A/B 测试结果 |
|---|---|---|
| ![逐句可信度溯源的检索面板](assets/RagTrace.png) | ![A/B 测试变体配置](assets/abTest.png) | ![A/B 测试对比结果](assets/abTest_result.png) |
| 匹配结论、召回汇总与逐句 ✅/⚠️/❌ 标签；技术视图另有查询改写流水、BM25 与向量三路得分对比、逐句相似度数值。 | 每个变体可单独设置检索模式、BM25 权重、相似度阈值与 `top_k`。 | 并排展示评估得分、召回数量与漂移率，并给出各变体均值。 |

## 与常见 RAG 示例的差异

逐个功能与其他生态做对比既不客观也很快过时，这里只说明本项目想补上的差距：

| 关注点 | 常见 RAG 示例 | whiteBoxRAG |
|---|---|---|
| 可溯源 | 在回答旁列出命中的分块 | 逐句可信度标签 + 引用校验 |
| 漏召回 | 无感知 | 对漏召回文档给出根因诊断 |
| 用户反馈 | 存下来就没人看 | 沉淀为可生效、可度量的规则 |
| 调参 | 改代码碰运气 | 场景配置 + 评估指标 + A/B 测试 |
| 资源占用 | 需要向量服务、消息队列或数据库 | 8G 内存 CPU 机器上的本地文件 |
| 语言 | 只支持英文或硬编码 | 按 `Accept-Language` 切换 zh-CN / en-US |

## 主要功能

**轻量化设计**
- 8G 内存 CPU 机器即可运行，不需要 Redis、MySQL 或消息队列
- 向量、文档、日志、溯源、任务状态全部以本地文件存储
- 依赖面尽量小，部署快

**文档处理**
- 多格式解析：PDF、Word（`.docx`/`.doc`）、TXT、Excel（`.xlsx`/`.xls`）、PowerPoint（`.pptx`）
- 扩展名校验 + 文件魔数（MIME）双重校验，拦截伪装上传
- 场景驱动的分块策略，可按知识库单独覆盖
- 异步入库与实时进度追踪

**检索**
- BM25（jieba 分词）+ 向量语义检索加权融合，权重可配置
- 相似度阈值过滤、重排序与上下文压缩
- 查询改写：同义词扩展、错别字纠正、停用词净化，词典 YAML 支持热更新
- 检索为空时优雅降级

**回答质量与安全**
- 强制引用格式：大模型必须用 `[doc_id]` 标注事实，随后统一校验
- 句子级溯源：区分直接引用、摘要、低置信度与无依据漂移
- 业务边界（OOD）检测：关键词白/黑名单 + 大模型语义判定
- 熔断器：错误率或时延异常时打开，保护模型后端

**可观测与调优**
- 未召回诊断：解释某篇文档为何被漏掉（元数据过滤、得分阈值、模式不匹配、关键词缺失）
- 评估框架：召回率、忠实度、相关性、上下文利用率、幻觉率、拒答准确率
- 规则引擎：把用户反馈聚合为业务规则，并回报其实际生效情况
- A/B 测试：并排比较不同检索配置
- 性能监控、结构化日志与对话历史分析

**运维**
- 按 IP 的接口限流（静态资源与健康检查豁免）
- 国际化：按 `Accept-Language` 切换 zh-CN / en-US
- 敏感配置（API Key 等）只从环境变量读取，不以明文入库
- 定时任务：周期性文档优化与每日分析

## 技术栈

| 层级 | 技术 |
|------|------|
| Web 框架 | FastAPI、Uvicorn、Gunicorn |
| RAG 框架 | LlamaIndex 0.10 |
| 向量库 | ChromaDB（本地持久化） |
| 稀疏检索 | rank-bm25 + jieba 分词 |
| 大模型 / 向量模型 | Ollama（qwen2.5:7b、nomic-embed-text）或 OpenAI |
| 文档解析 | pypdf、python-docx、openpyxl、python-pptx、BeautifulSoup |
| 配置 | PyYAML、python-dotenv |
| 限流 | slowapi |
| 文件类型校验 | python-magic |
| 前端 | 原生 HTML/CSS/JS、Tailwind CSS CDN、SSE 流式输出 |
| 部署 | Docker、Gunicorn |

## 项目结构

```
whiteBoxRAG/
├── api/                    # FastAPI 应用与路由（knowledge / chat / monitor / scenario / evaluation / document_optimizer）
├── core/                   # RAG 核心：解析、向量库、混合检索、问答流水线、边界检测、熔断、
│                           #          查询改写、规则引擎、评估、未召回诊断、句子级溯源、意图分类
├── service/                # 日志、异步任务、监控、限流、定时任务、i18n、响应编码
├── config/                 # settings.yaml、同义词/错别字词典、scenarios/ 场景配置
├── static/                 # 前端（index.html / admin.html / ab_test.html / i18n.js，免构建）
├── storage/                # 运行时数据（vectordb、documents、traces、tasks、monitor、logs）
├── deploy/                 # Dockerfile、gunicorn.conf.py、start.sh、DEPLOY.md
├── scripts/seed_demo.py    # 一键演示：建库 → 入库 → 提问 → 打印逐句溯源
├── examples/demo/          # 演示用示例文档
├── docs/ARCHITECTURE_ZH.md # 模块级技术规格（各模块职责、流程与参数）
├── tests/                  # 单元与集成测试
├── tools/                  # 日志分析、i18n 一致性检查
├── docker-compose.yml      # Ollama + 模型拉取 + 应用，一条命令
├── main.py                 # 一键启动脚本
├── requirements.txt        # 运行依赖
└── requirements-dev.txt    # 测试 / 静态检查依赖
```

各模块的详细说明（LLM 流水线、混合检索、边界检测、熔断、评估、规则引擎、前端页面等）见
[`docs/ARCHITECTURE_ZH.md`](docs/ARCHITECTURE_ZH.md)。

## 快速开始

### 环境要求

| 要求 | 说明 |
|------|------|
| Python 3.12+ | `python main.py --install` 会自动创建虚拟环境并安装运行依赖 |
| Ollama 0.1.25+ | 监听 `:11434`，且已拉取所需模型 |
| 资源 | 最低 8G 内存 / 4 核 CPU / 20GB 可用磁盘 |

```bash
ollama serve                                                   # 启动本地 Ollama
ollama pull qwen2.5:7b && ollama pull nomic-embed-text:latest  # 两个模型约 5GB，只需一次
```

模型名与 Ollama 地址可通过 `.env` 配置（`OLLAMA_LLM_MODEL`、`OLLAMA_EMBEDDING_MODEL`、
`OLLAMA_BASE_URL`），见 [环境变量](#环境变量)。

### 一键启动（推荐）

```bash
# 首次运行：检测环境、安装依赖、启动服务
python main.py --install

# 开发模式（代码变更自动重载）
python main.py --install --dev

# 生产模式（跳过环境检查）
python main.py --no-check

# 指定监听地址与端口
python main.py --host 0.0.0.0 --port 8080
```

### 手动部署

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn api.api:app --host 0.0.0.0 --port 8080
```

### 容器部署

```bash
docker compose up -d          # 构建镜像、启动 Ollama、拉取模型、启动应用
docker compose logs -f app    # 查看应用日志
docker compose down           # 停止（模型保留在 ollama-models 卷中）
```

`docker-compose.yml` 通过 `OLLAMA_BASE_URL=http://ollama:11434` 把应用接到 Ollama 容器，并用一个
一次性初始化容器在应用启动前拉取 `qwen2.5:7b` 与 `nomic-embed-text`，保证第一条请求就能用。需要
Docker Compose v2（`docker compose`，不是旧版 `docker-compose`）。

只用单容器时：

```bash
docker build -t whiteboxrag:latest -f deploy/Dockerfile .
docker run -d --name rag-system -p 8080:8080 \
  -v "$(pwd)/storage:/app/storage" \
  --memory=8g \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  whiteboxrag:latest
```

PowerShell 下卷参数写作 `"${PWD}/storage:/app/storage"`。

> **Ollama 不在镜像里。** 容器内的 `localhost` 指向容器自身，因此必须显式传入
> `OLLAMA_BASE_URL=http://host.docker.internal:11434`（Docker Desktop）或你自己的 Ollama 地址。
> 否则 `/api/health` 可能正常返回，但每次大模型调用都会连接失败。

### 验证部署

```bash
# 1. 健康检查  ->  {"status": "ok", ...}
curl http://localhost:8080/api/health

# 2. 端到端自检：自动建库、入库、提问并打印逐句溯源，无需手动填参数
python scripts/seed_demo.py

# 3. 或手动调用 REST 接口
curl -X POST http://localhost:8080/api/knowledge/create -H "Content-Type: application/json" \
  -d '{"name": "产品文档", "description": "用户手册与规格"}'                  # -> {"kb_id": "..."}
curl -X POST http://localhost:8080/api/knowledge/<kb_id>/upload -F "file=@/path/to/document.pdf"
curl -X POST http://localhost:8080/api/chat/stream -H "Content-Type: application/json" \
  -d '{"kb_id": "<kb_id>", "query": "保修期是多久？", "stream": false}'
```

`stream: false` 返回完整溯源数据（回答、检索上下文、逐句可信度、评估指标）；`stream: true` 为 SSE
逐字输出。浏览器打开 `http://localhost:8080` 即可使用内置前端。

### 没有 Ollama？改用 OpenAI

```bash
cp .env.example .env
```

```dotenv
# .env
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

`.env` 的优先级高于 `config/settings.yaml`，两种模型提供者走同一条代码路径。

## 配置说明

所有运行期配置位于 `config/settings.yaml`，常用项如下：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `llm.provider` | 模型提供者：`ollama` / `openai` | `ollama` |
| `ollama.llm_model` | 大模型名称 | `qwen2.5:7b` |
| `ollama.embedding_model` | 向量模型名称 | `nomic-embed-text:latest` |
| `vector_store.top_k` | 向量召回数量 | `5` |
| `vector_store.similarity_threshold` | 相似度阈值 | `0.1` |
| `retriever.mode` | 检索模式：`vector` / `bm25` / `hybrid` | `hybrid` |
| `retriever.bm25_weight` | 混合检索中 BM25 权重（向量权重 = 1 − 该值） | `0.4` |
| `retriever.compression.enabled` | 调用大模型前压缩上下文 | `true` |
| `document_parser.chunk_size` | 分块大小（字符） | `512` |
| `document_parser.max_file_size` | 单文件上传上限（MB） | `50` |
| `boundary.enabled` | 业务边界（OOD）检测 | `true` |
| `circuit_breaker.failure_threshold` | 触发熔断的错误率（%） | `50` |
| `api.rate_limit` | 每 IP 每分钟请求数 | `60` |
| `trace.enabled` | 问答溯源记录 | `true` |
| `recall_diagnostic.enabled` | 未召回根因分析 | `true` |

### 环境变量

敏感或环境相关的值（API Key、服务地址、模型名）通过环境变量管理，从项目根目录的 `.env` 读取，
优先级高于 `settings.yaml`：

```bash
cp .env.example .env
```

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `LLM_PROVIDER` | 模型提供者：`ollama` / `openai` | `ollama` |
| `OLLAMA_BASE_URL` | Ollama 服务地址 | `http://localhost:11434` |
| `OLLAMA_LLM_MODEL` | 大模型名称 | `qwen2.5:7b` |
| `OLLAMA_EMBEDDING_MODEL` | 向量模型名称 | `nomic-embed-text:latest` |
| `OPENAI_API_KEY` | OpenAI API Key（`LLM_PROVIDER=openai` 时必填） | — |
| `OPENAI_BASE_URL` | OpenAI 接口地址（可指向代理） | `https://api.openai.com/v1` |
| `API_KEY` | 仅保留、**尚未生效**：系统没有内置鉴权，见 [安全说明](#安全说明) | — |
| `API_RATE_LIMIT` | 每 IP 每分钟请求上限 | `60` |

`.env` 由 `config/__init__.py` 通过 `python-dotenv` 统一加载并注入配置字典，其它模块不直接读取环境变量。

### 场景配置

场景把分块、检索、提示词与评估参数打包为命名配置档（如 `customer_service`、`technical_doc`）。每个知识库
可绑定场景：客服知识库使用更短的分块与更友好的提示词，技术文档知识库使用更大分块与更严格的引用要求。
场景文件位于 `config/scenarios/`，可用 `/api/scenario/{id}/validate` 校验。

## API 概览

系统共提供 **58 个 REST 端点**，分为六个模块：知识库管理、对话问答、场景管理、质量评估、文档优化、系统监控。
交互式文档位于 `http://localhost:8080/docs`（Swagger UI）与 `/redoc`。

完整的端点清单（方法、功能说明与请求示例）见 [`docs/ARCHITECTURE_ZH.md`](docs/ARCHITECTURE_ZH.md)；
英文完整表格见 [`README.md`](README.md#api-reference)。

## 测试

```bash
pip install -r requirements-dev.txt          # 安装 pytest、pytest-asyncio、flake8

# 离线用例（与 CI 完全一致，不需要运行中的服务或 Ollama）
pytest -q \
  tests/test_boundary_detector.py tests/test_query_rewrite.py tests/test_retriever.py \
  tests/test_regression.py tests/test_llm_citations.py tests/test_doc_analyzer.py \
  tests/test_abtest_integration.py tests/test_async_tasks_kb.py \
  tests/test_i18n_consistency.py tests/test_http_encoding.py \
  tests/test_parser.py tests/test_path_safety.py tests/test_abtest_rules.py

# 全部用例（test_api / test_bm25_simple / test_hybrid_search_demo 需要服务或 Ollama）
pytest tests/ -q

python tools/check_i18n.py                   # 中英文案 key 与占位符一致性检查
```

每个测试文件都可以直接当脚本运行，例如 `python tests/test_parser.py`；逐文件覆盖范围见
[`docs/ARCHITECTURE_ZH.md`](docs/ARCHITECTURE_ZH.md) 第 10 节。CI 配置见 `.github/workflows/ci.yml`。

## 生产部署

```bash
gunicorn --bind 0.0.0.0:8080 --workers 1 --threads 4 --timeout 120 api.api:app

# 或使用自带配置文件
gunicorn -c deploy/gunicorn.conf.py api.api:app
```

推荐单 worker：向量库与 BM25 索引保存在进程内存中，多 worker 会复制这份状态。

| 资源 | 最低 | 推荐 |
|------|------|------|
| CPU | 4 核 | 8 核 |
| 内存 | 8 GB | 16 GB |
| 磁盘 | 20 GB | 50 GB |
| 操作系统 | Ubuntu 20.04+ / Windows 10+ / macOS 12+ | — |

Ollama 模型准备、性能调优与备份流程见 [`deploy/DEPLOY.md`](deploy/DEPLOY.md)。

## 安全说明

本项目定位为**本地优先**部署，默认不带鉴权，安全性取决于你把它暴露在哪里。

- **没有内置鉴权**：任何能访问端口的人都能调用全部端点；`API_KEY` 配置项**仅保留、尚未生效**（见 `.env.example`）。请把它放在可信网络内，或绑定本机（`python main.py --host 127.0.0.1`），或置于带鉴权的反向代理之后。
- **CORS 默认放开**：`api.cors_origins` 默认为 `*`，意味着你访问的任意网页都可能驱动本机实例。生产环境请收窄为你自己的前端域名并删掉 `*`；只有配置了显式列表时才会开启 credentials。
- **限流**按客户端 IP 生效（`api.rate_limit`，默认 60 次/分钟）；`/static`、`/api/health`、`/docs`、`/openapi.json`、`/redoc` 免限流。
- **上传/下载路径已净化**：客户端提供的文件名（multipart 文件名、`trace_id`、`task_id`、`logger_name`、`file_name`）都会被 `service/path_safety.py` 收敛为单个路径片段，构造 `../` 也无法读写目录之外；新增任何用户可控路径请使用 `safe_join()`。
- **密钥**（`OPENAI_API_KEY` 等）来自 `.env`，已被 git 忽略；`config/settings.yaml` 中不存放敏感信息。
- **Swagger UI**（`/docs`）始终开放，如需对外发布请自行关闭或保护。

需要多用户权限控制时，请在反向代理层实现（可选的 API-Key 中间件已列入路线图）。

## 路线图

以下只是方向而不是承诺，欢迎提 Issue 或 PR：

- [ ] 可选的 API-Key 中间件，让预留的 `API_KEY` 配置真正生效
- [ ] 可插拔重排序（cross-encoder），支持按场景选择
- [ ] 流式评估：评分随 token 一起返回，而不是等回答结束
- [ ] 多租户知识库，规则与场景按租户隔离
- [ ] 用 `tests/golden_test_set.json` 在 CI 中做检索回归门禁
- [ ] 可选的 PostgreSQL/pgvector 后端，供规模超出本地 ChromaDB 的团队使用

## 贡献

欢迎提交 Issue 与 PR，请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)：其中包含开发环境、离线测试子集、
代码约定（注释用英文、面向用户的文案走 i18n）与 PR 检查清单。行为变更请附带测试，并保持
`README.md` 与 `README_ZH.md` 同步。

## 许可

[MIT](LICENSE) © whiteBoxRAG contributors。本项目面向学习与研究用途，用于敏感数据前请自行评估
数据处理合规要求。

## 致谢

基于 [FastAPI](https://fastapi.tiangolo.com/)、[LlamaIndex](https://www.llamaindex.ai/)、
[ChromaDB](https://www.trychroma.com/)、[Ollama](https://ollama.com/)、
[rank-bm25](https://github.com/dorianbrown/rank_bm25) 与 [jieba](https://github.com/fxsjy/jieba) 构建。
