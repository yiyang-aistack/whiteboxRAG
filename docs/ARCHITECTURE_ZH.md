# whiteBoxRAG 架构与功能规格说明书

> **版本**: 1.0.0  
> **定位**: 企业级轻量化私有化 RAG（检索增强生成）系统  
> **适配环境**: 8G 内存 CPU 环境，基于 LlamaIndex + ChromaDB + Ollama  
> **最后更新**: 2026-09-12

> 本文件是**模块级技术规格**，面向开发者与维护者，说明各模块职责、流程与参数。
> 想快速跑起来、看功能亮点与截图，请读 [README_ZH.md](../README_ZH.md)（English: [README.md](../README.md)）。
> 部署与运维细节见 [deploy/DEPLOY.md](../deploy/DEPLOY.md)。

---

## 目录

- [1. 系统概述](#1-系统概述)
- [2. 技术栈](#2-技术栈)
- [3. 项目结构](#3-项目结构)
- [4. 核心模块（core/）](#4-核心模块core)
- [5. API 路由层（api/）](#5-api-路由层api)
- [6. 服务层（service/）](#6-服务层service)
- [7. 配置系统（config/）](#7-配置系统config)
- [8. 前端页面（static/）](#8-前端页面static)
- [9. 部署方案（deploy/）](#9-部署方案deploy)
- [10. 测试覆盖（tests/）](#10-测试覆盖tests)
- [11. 启动方式](#11-启动方式)

---

## 1. 系统概述

whiteBoxRAG 是一套面向企业的轻量化私有化 RAG 系统，核心设计目标：

| 特性 | 说明 |
|---|---|
| **白盒可溯源** | 每次问答全链路可追踪：查询改写 → 检索 → LLM 生成 → 句子级溯源 |
| **混合检索** | BM25 关键词 + 向量语义检索加权融合，支持重排序和上下文压缩 |
| **私有化部署** | 基于 Ollama 本地模型，数据不出企业网络 |
| **轻量化** | 适配 8G 内存 CPU 环境，单机即可运行 |
| **场景化** | 支持按业务场景（技术文档、客服等）配置不同的检索和生成策略 |
| **智能诊断** | 未召回诊断、幻觉检测、语义矛盾检测、评估评分 |

### 系统架构图

```
┌─────────────────────────────────────────────────────────┐
│                    前端 (static/)                        │
│  index.html (主页面)  │  ab_test.html (A/B测试)  │  admin.html (管理)
└──────────────┬──────────────────────────────────────────┘
               │ HTTP / SSE
┌──────────────▼──────────────────────────────────────────┐
│                 API 层 (api/)                            │
│  chat │ knowledge │ monitor │ scenario │ evaluation │ optimizer
└──────────────┬──────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────┐
│               核心引擎 (core/)                           │
│  LLMPipeline (问答流水线)                                │
│    ├─ IntentClassifier (意图分类)                        │
│    ├─ BoundaryDetector (边界检测)                        │
│    ├─ HybridRetriever (混合检索)                         │
│    │    ├─ BM25 检索                                     │
│    │    ├─ Vector 检索 (ChromaDB)                        │
│    │    └─ 融合 + 重排序 + 压缩                           │
│    ├─ LLMAdapter (Ollama/OpenAI)                        │
│    ├─ SentenceTracer (句子级溯源)                        │
│    ├─ RAGEvaluator (质量评估)                            │
│    └─ RecallDiagnostic (未召回诊断)                      │
│  CircuitBreaker (熔断器)                                 │
│  TraceManager (溯源管理)                                 │
└──────────────┬──────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────┐
│              基础设施 (service/ + config/)               │
│  Logger │ Monitor │ Scheduler │ AsyncTasks │ Config     │
└─────────────────────────────────────────────────────────┘
```

---

## 2. 技术栈

| 层级 | 技术 | 用途 |
|---|---|---|
| **Web 框架** | FastAPI + Uvicorn | 高性能异步 API 服务 |
| **RAG 框架** | LlamaIndex | 文档索引、检索、生成 |
| **向量存储** | ChromaDB | 本地持久化向量数据库 |
| **LLM 引擎** | Ollama (qwen2.5:7b) | 本地大语言模型推理 |
| **Embedding** | nomic-embed-text (768维) | 文本向量化 |
| **关键词检索** | rank-bm25 + jieba | BM25 中文分词检索 |
| **文档解析** | pypdf / python-docx / openpyxl / python-pptx | 多格式文档解析 |
| **前端** | Tailwind CSS + 原生 JS | 响应式 UI |
| **部署** | Docker + Gunicorn | 容器化生产部署 |

---

## 3. 项目结构

```
whiteBoxRAG/
├── main.py                    # 根启动脚本（环境检查 + 服务启动）
├── requirements.txt           # Python 依赖
├── api/                       # API 路由层
│   ├── api.py                # FastAPI 应用入口
│   └── routes/                # 路由模块
│       ├── chat.py            # 对话问答
│       ├── knowledge.py       # 知识库管理
│       ├── monitor.py         # 系统监控
│       ├── scenario.py        # 场景管理
│       ├── evaluation.py      # 质量评估
│       └── document_optimizer.py  # 文档优化
├── core/                      # 核心引擎
│   ├── llm_pipeline.py        # LLM 问答流水线
│   ├── retriever.py           # 混合检索器
│   ├── llm_adapter.py         # LLM 适配器
│   ├── vector_store.py        # 向量存储管理
│   ├── document_parser.py     # 文档解析器
│   ├── boundary_detector.py   # 边界检测器
│   ├── circuit_breaker.py     # 熔断器
│   ├── evaluator.py           # RAG 质量评估器
│   ├── intent_classifier.py   # 意图分类器
│   ├── query_rewriter.py      # 查询改写器
│   ├── sentence_tracing.py    # 句子级溯源
│   ├── recall_diagnostic.py   # 未召回诊断
│   ├── rule_engine.py         # 规则引擎
│   ├── trace.py               # 溯源管理
│   └── typo_checker.py        # 拼写检查
├── config/                    # 配置系统
│   ├── __init__.py            # 配置加载器
│   ├── settings.yaml          # 全局配置
│   ├── scenarios/             # 场景配置
│   │   ├── technical_doc.yaml
│   │   └── customer_service.yaml
│   └── synonym_dict.yaml      # 同义词典
├── service/                   # 基础服务
│   ├── logger.py              # 日志管理
│   ├── monitor.py             # 性能监控
│   ├── scheduler.py           # 定时任务
│   └── async_tasks.py         # 异步任务
├── static/                    # 前端页面
│   ├── index.html             # 主页面
│   ├── ab_test.html           # A/B 测试页面
│   └── admin.html             # 管理页面
├── deploy/                    # 部署方案
│   ├── Dockerfile
│   ├── gunicorn.conf.py
│   └── start.sh
├── tests/                     # 测试用例
├── storage/                   # 运行时存储
│   ├── vectordb/              # 向量数据库
│   ├── documents/             # 原始文档
│   ├── traces/                # 溯源记录
│   ├── logs/                  # 日志文件
│   ├── tasks/                 # 异步任务
│   └── monitor/               # 监控数据
└── tools/
    └── log_analyzer.py        # 日志分析工具
```

---

## 4. 核心模块（core/）

### 4.1 LLM 问答流水线 — `llm_pipeline.py`

系统核心调度器，编排完整的问答流程。

**问答流程**：
```
用户查询
  → 意图分类 (IntentClassifier)
  → 边界检测 (BoundaryDetector)
  → 查询改写 (QueryRewriter)
  → 混合检索 (HybridRetriever)
  → 检索质量校验 (avg_score > 0.4 ?)
  → Prompt 构建 (高质量 / 混合回答模式)
  → LLM 生成 (经熔断器保护)
  → 句子级溯源 (SentenceTracer)
  → 质量评估 (RAGEvaluator)
  → 未召回诊断 (RecallDiagnostic)
  → 返回结果 + 溯源数据
```

**关键特性**：
- **检索质量阈值**：平均检索得分 ≤ 0.4 时启用混合回答模式，先声明"知识库无相关内容"再补充大模型知识
- **混合回答后处理**：`_ensure_hybrid_format()` 确保回答以声明开场白开头，不依赖 LLM 遵守指令
- **同步/流式双接口**：`query()` 同步返回，`query_stream()` SSE 流式输出
- **熔断器保护**：LLM 调用经 CircuitBreaker 包装，错误率过高自动熔断

### 4.2 混合检索器 — `retriever.py`

支持三种检索模式的统一检索接口。

| 模式 | 说明 | 适用场景 |
|---|---|---|
| `hybrid`（默认） | BM25 + 向量加权融合 | 通用场景，兼顾关键词和语义 |
| `vector` | 纯向量语义检索 | 语义相近但用词不同 |
| `bm25` | 纯关键词检索 | 精确术语匹配 |

**默认参数**（来自 `settings.yaml`）：
- BM25 权重: 0.4，向量权重: 0.6
- top_k: 5，重排序数量: 10
- 相似度阈值: 0.1
- 上下文压缩: 启用，最大 2048 tokens

**关键流程**：
1. 查询改写（错别字纠正 + 同义词扩展）
2. jieba 分词
3. BM25 检索 + 向量检索并行执行
4. 加权融合得分：`score = BM25×0.4 + Vector×0.6`
5. 重排序（可选）
6. 上下文压缩（可选）

### 4.3 LLM 适配器 — `llm_adapter.py`

统一不同 LLM 提供者的调用接口。

| 适配器 | 模型 | 说明 |
|---|---|---|
| OllamaAdapter | qwen2.5:7b | 本地部署，默认提供者 |
| OpenAIAdapter | gpt-4o | 云端调用（需配置 API Key） |

支持同步生成、流式生成和 Embedding 生成。

### 4.4 向量存储管理 — `vector_store.py`

基于 ChromaDB 的本地向量存储管理。

- 每个知识库对应一个 ChromaDB Collection
- 支持文档添加、批量删除、相似度搜索
- Embedding 模型: nomic-embed-text (768 维)
- 持久化路径: `./storage/vectordb`

### 4.5 文档解析器 — `document_parser.py`

多格式文档解析与分块。

| 格式 | 解析库 |
|---|---|
| PDF | pypdf |
| DOCX/DOC | python-docx |
| XLSX/XLS | openpyxl |
| PPTX | python-pptx |
| TXT | 原生读取 |

**分块策略**：
- 分块大小: 512 字符
- 重叠大小: 50 字符
- 批量处理线程数: 2

### 4.6 边界检测器 — `boundary_detector.py`

检测用户查询是否在业务范围内（OOD 检测）。

**三层检测机制**：
1. **关键词白名单/黑名单**：快速匹配，命中即判定
2. **语义检测**：基于业务主题列表计算语义相似度
3. **检索结果判断**：平均检索相似度低于阈值视为 OOD

**配置参数**：
- 置信度阈值: 0.5
- 语义相似度阈值: 0.3
- 白名单关键词: 订单、售后、产品、账户、技术、财务、物流、咨询
- 黑名单关键词: 非法、违法、色情、暴力、赌博、毒品

### 4.7 熔断器 — `circuit_breaker.py`

保护 LLM 调用的状态机熔断机制。

| 状态 | 说明 |
|---|---|
| CLOSED | 正常调用，记录成功/失败 |
| OPEN | 熔断中，直接返回错误，等待超时 |
| HALF_OPEN | 半开状态，允许有限试探性调用 |

**触发条件**：
- 错误率 > 50%
- 响应延迟 > 30 秒
- 熔断超时: 60 秒
- 半开恢复: 连续成功 5 次

### 4.8 RAG 质量评估器 — `evaluator.py`

对 RAG 系统回答质量进行多维度评估。

| 指标 | 说明 | 计算方式 |
|---|---|---|
| 检索召回率 | 检索到的相关文档比例 | context_count / expected_count |
| 答案忠实度 | 回答是否基于检索内容 | 基于引用覆盖率 |
| 幻觉率 | 无依据推断比例 | `max(0, 1 - value/target)` |
| 综合评分 | 加权综合得分 | 各指标加权平均 |

**特殊机制**：
- 检索召回率 < 0.15 时，综合评分上限 0.3
- 边界检测置信度 < 0.5 时，按置信度调整评分

### 4.9 意图分类器 — `intent_classifier.py`

对用户查询进行业务意图分类，输出意图类型和置信度。

### 4.10 查询改写器 — `query_rewriter.py`

优化用户查询以提高检索质量：
- 错别字纠正（TypoChecker）
- 同义词扩展（synonym_dict.yaml）
- 查询净化（去除无意义字符）

### 4.11 句子级溯源 — `sentence_tracing.py`

将 AI 回答拆分为句子，逐句与源文档对齐，标注可信度：

| 标签 | 说明 |
|---|---|
| ✅ 有文档依据 | 句子与某文档片段语义相似度 > 阈值 |
| ⚠️ 低置信度 | 相似度中等，存在但不完全匹配 |
| ❌ 无依据推断 | 无匹配文档，存在幻觉风险 |

同时支持语义矛盾检测（检测回答中相互矛盾的句子）。

### 4.12 未召回诊断 — `recall_diagnostic.py`

诊断哪些文档预期应该被召回但未命中，分析根因：
- chunk 过大
- embedding 不匹配
- 关键词缺失
- 文档解析失败

### 4.13 规则引擎 — `rule_engine.py`

监控用户反馈模式，自动检测重复问题模式并固化为业务规则。

### 4.14 溯源管理 — `trace.py`

`TraceManager` 和 `Trace` 类，记录和管理每次问答的完整溯源信息，持久化到 `./storage/traces/`。

### 4.15 拼写检查 — `typo_checker.py`

中文错别字检测与纠正，基于配置的字符映射表。

---

## 5. API 路由层（api/）

### 5.1 对话问答 — `chat.py`

| 端点 | 方法 | 功能 |
|---|---|---|
| `/api/chat/stream` | POST | 流式问答（SSE），支持实时输出；`stream: false` 时返回完整溯源 JSON |
| `/api/chat/trace` | POST | 按 trace_id 查询溯源详情 |
| `/api/chat/trace/{trace_id}` | GET | 按 trace_id 查询溯源详情 |
| `/api/chat/simulate` | POST | 模拟问答（假设分析模式） |
| `/api/chat/feedback` | POST | 提交用户反馈 |
| `/api/chat/rules` | GET / POST | 业务规则查询 / 新建 |
| `/api/chat/rules/{rule_id}` | DELETE | 删除业务规则 |
| `/api/chat/rule-effectiveness` | GET | 规则生效率报告 |
| `/api/chat/rule-logs` | GET | 规则命中日志 |
| `/api/chat/abtest` | POST | A/B 对比测试 |
| `/api/chat/abtest/batch` | POST | 批量 A/B 对比测试 |
| `/api/chat/history` | POST | 查询对话历史 |
| `/api/chat/history/stats` | GET | 对话统计 |
| `/api/chat/history/{trace_id}` | DELETE | 删除对话记录 |
| `/api/chat/health` | GET | LLM 服务健康检查 |

### 5.2 知识库管理 — `knowledge.py`

| 端点 | 方法 | 功能 |
|---|---|---|
| `/api/knowledge/list` | GET | 获取知识库列表 |
| `/api/knowledge/create` | POST | 创建知识库 |
| `/api/knowledge/{kb_id}` | GET / DELETE | 获取详情 / 删除知识库 |
| `/api/knowledge/{kb_id}/upload` | POST | 上传文档（异步向量化，返回 task_id） |
| `/api/knowledge/{kb_id}/status` | GET | 入库任务进度 |
| `/api/knowledge/{kb_id}/files` | GET | 获取文件列表 |
| `/api/knowledge/{kb_id}/files/{file_id}` | GET / PUT / DELETE | 文件详情 / 替换 / 删除 |
| `/api/knowledge/{kb_id}/chunks` | GET | 获取分块列表 |
| `/api/knowledge/{kb_id}/chunks/{chunk_id}` | GET | 获取分块详情及向量 |
| `/api/knowledge/{kb_id}/raw/{file_id}` | GET | 获取原始文档内容 |
| `/api/knowledge/synonyms` | GET / POST | 同义词配置查询 / 新增 |
| `/api/knowledge/synonyms/{term}` | DELETE | 删除同义词 |
| `/api/knowledge/synonyms/reload` | POST | 热更新同义词词典 |
| `/api/knowledge/typos` | GET / POST | 错别字规则查询 / 新增 |
| `/api/knowledge/typos/{typo}` | DELETE | 删除错别字规则 |
| `/api/knowledge/typos/reload` | POST | 热更新错别字词典 |

### 5.3 系统监控 — `monitor.py`

| 端点 | 方法 | 功能 |
|---|---|---|
| `/api/monitor/stats` | GET | 性能统计（请求数、响应时间、错误率） |
| `/api/monitor/logs` | GET | 日志查询 |
| `/api/monitor/tasks` | GET | 异步任务状态 |
| `/api/monitor/task/{task_id}` | GET | 任务详情 |
| `/api/monitor/stats/reset` | POST | 重置统计 |
| `/api/health` | GET | 系统健康检查 |

### 5.4 场景管理 — `scenario.py`

| 端点 | 方法 | 功能 |
|---|---|---|
| `/api/scenario/list` | GET | 场景列表 |
| `/api/scenario/{scenario_id}` | GET | 场景详情 |
| `/api/scenario/{scenario_id}/params` | GET | 场景检索参数 |
| `/api/scenario/{scenario_id}/validate` | POST | 场景配置验证 |

**内置场景**：
- `technical_doc`：技术文档场景（强调准确性、代码示例）
- `customer_service`：客服场景（简洁友好、限制响应长度）

### 5.5 质量评估 — `evaluation.py`

| 端点 | 方法 | 功能 |
|---|---|---|
| `/api/evaluation/evaluate` | POST | 单条问答评估 |
| `/api/evaluation/batch` | POST | 批量评估 |
| `/api/evaluation/report` | POST | 生成评估报告 |
| `/api/evaluation/report/{report_id}` | GET | 获取评估报告 |
| `/api/evaluation/reports` | GET | 评估报告列表 |
| `/api/evaluation/metrics` | GET | 指标定义 |

### 5.6 文档优化 — `document_optimizer.py`

| 端点 | 方法 | 功能 |
|---|---|---|
| `/api/document/analyze` | POST | 文档分析 |
| `/api/document/optimize` | POST | 文档优化 |
| `/api/document/issues` | GET | 文档问题列表 |
| `/api/document/coverage` | GET | 查询覆盖度 |
| `/api/document/download/optimized` | GET | 下载优化后文档 |
| `/api/document/full-analysis` | POST | 完整分析流水线 |

---

## 6. 服务层（service/）

| 模块 | 功能 |
|---|---|
| `logger.py` | 多级日志系统，按模块分级管理，同时输出到控制台和文件 |
| `monitor.py` | 性能监控，记录接口调用统计、响应时间、系统资源 |
| `scheduler.py` | 定时任务调度（默认禁用），支持文档自动优化和每日分析 |
| `async_tasks.py` | 异步任务管理，用于文档向量化等耗时操作 |
| `rate_limiter.py` | 按 IP 的接口限流（静态资源与健康检查豁免） |
| `i18n.py` | 国际化（zh-CN / en-US），按 `Accept-Language` 选择语言 |
| `http_encoding.py` | 统一 UTF-8 响应编码，避免中文乱码 |

**日志配置**：
- 级别: INFO
- 路径: `./storage/logs/`
- 单文件最大: 10MB
- 保留天数: 30 天

**监控配置**：
- 数据保留: 24 小时
- 存储路径: `./storage/monitor/`

---

## 7. 配置系统（config/）

### 7.1 全局配置 — `settings.yaml`

| 配置块 | 关键参数 |
|---|---|
| `system` | app_name, version, host(0.0.0.0), port(8080), max_workers(4) |
| `llm` | provider: ollama |
| `ollama` | llm_model: qwen2.5:7b, embedding_model: nomic-embed-text, dim: 768 |
| `vector_store` | persist_directory, top_k(5), similarity_threshold(0.1) |
| `bm25` | top_k(5), tokenizer: jieba |
| `retriever` | mode: hybrid, bm25_weight: 0.4, rerank_top_k: 10 |
| `document_parser` | chunk_size: 512, chunk_overlap: 50, max_file_size: 50MB |
| `boundary` | confidence_threshold: 0.5, semantic_threshold: 0.3 |
| `circuit_breaker` | failure_threshold: 50%, timeout: 60s |
| `trace` | enabled: true, trace_directory: ./storage/traces |
| `scheduler` | enabled: false（默认禁用定时任务） |

### 7.2 场景配置

| 场景 | 文件 | 特点 |
|---|---|---|
| 技术文档 | `scenarios/technical_doc.yaml` | 强调准确性、代码示例、较大 chunk |
| 客服场景 | `scenarios/customer_service.yaml` | 简洁友好、限制响应长度、较小 top_k |

### 7.3 同义词典 — `synonym_dict.yaml`

用于查询改写时的同义词扩展，提升检索召回率。

---

## 8. 前端页面（static/）

### 8.1 主页面 — `index.html`

核心交互界面，包含以下功能模块：

| 模块 | 功能 |
|---|---|
| **聊天对话** | 实时流式问答，支持上下文连续对话 |
| **检索溯源面板** | 占屏幕 1/3 宽度，分"业务摘要"和"技术详情"两个视图 |
| **知识库管理** | 知识库选择、创建、删除 |
| **文件管理** | 文档上传、查看、删除 |
| **A/B 测试入口** | 点击打开独立 A/B 测试窗口 |
| **场景选择** | 按业务场景切换检索策略 |

**检索溯源 — 业务摘要视图**（面向业务管理员）：
- 知识库匹配结论卡片（首屏第一眼，红色/琥珀色/绿色状态）
- 本次问答总览（综合评分、风险标签、业务意图）
- 参考资料召回汇总（最终生效文档列表）
- AI 逐句可信度溯源（✅/⚠️/❌ 标签 + 点击查看原文）
- 智能诊断 & 优化建议
- 质量指标（百分比展示，中文释义）

**检索溯源 — 技术详情视图**（面向技术人员）：
- 查询改写流水
- 三路检索对比（BM25 vs 向量 vs 融合，可视化得分条）
- 融合计算详情
- 完整检索参数
- 句子级溯源详情（含相似度数值）
- 未召回诊断原始日志

### 8.2 A/B 测试页面 — `ab_test.html`

独立窗口打开的 A/B 对比测试工具：
- 多变体配置（检索模式、BM25 权重、相似度阈值、top_k）
- 测试执行进度栏（步骤指示器 + 进度条动画）
- 配置区域始终可见（点击"开始测试"后不隐藏）
- 变体对比结果卡片（评估得分、召回数量、漂移率）
- 统计摘要（平均值对比）

### 8.3 管理页面 — `admin.html`

后台管理界面：
- 知识库创建与删除
- 文档上传与管理
- 分块内容浏览
- 向量数据可视化
- 分页查询

---

## 9. 部署方案（deploy/）

| 文件 | 功能 |
|---|---|
| `Dockerfile` | 容器化部署镜像定义 |
| `gunicorn.conf.py` | Gunicorn Web 服务器配置（多 worker、超时、日志） |
| `start.sh` | 部署启动脚本（初始化 + 启动服务） |
| `../docker-compose.yml` | 一键本地栈：Ollama 服务 + 模型拉取 + 应用（`docker compose up -d`） |

**部署架构**：
```
Docker Container
  └─ Gunicorn (多 worker)
       └─ Uvicorn (ASGI)
            └─ FastAPI (api.api:app)
                 └─ Ollama (外部服务, localhost:11434)
```

---

## 10. 测试覆盖（tests/）

| 测试文件 | 覆盖范围 | CI |
|---|---|---|
| `test_api.py` | API 接口正确性与响应验证 | 需运行中的服务 |
| `test_retriever.py` | 检索器多策略切换（BM25/向量/混合） | ✅ |
| `test_parser.py` | 文档解析与分块逻辑 | ✅ |
| `test_path_safety.py` | 文件名净化、`safe_join` 与目录穿越拦截 | ✅ |
| `test_doc_analyzer.py` | 文档分析器功能 | ✅ |
| `test_query_rewrite.py` | 查询改写（错别字、同义词） | ✅ |
| `test_boundary_detector.py` | 业务边界（OOD）检测与投票逻辑 | ✅ |
| `test_llm_citations.py` | 引用强制与校验 | ✅ |
| `test_abtest_integration.py` | A/B 测试流程完整性 | ✅ |
| `test_abtest_rules.py` | A/B 测试规则与生效率持久化 | ✅ |
| `test_async_tasks_kb.py` | 异步入库任务与知识库元数据 | ✅ |
| `test_i18n_consistency.py` | 中英文案与占位符一致性 | ✅ |
| `test_http_encoding.py` | UTF-8 响应编码 | ✅ |
| `test_bm25_simple.py` | BM25 基础功能 | 需运行中的服务 |
| `test_hybrid_search_demo.py` | 混合检索策略演示 | 需 Ollama |
| `test_regression.py` | 回归测试 | ✅ |
| `golden_test_set.json` | 黄金测试集（标准问答对） | 数据 |

CI 配置见 `.github/workflows/ci.yml`：i18n 一致性检查 + 上述可离线用例 + flake8（E9,F63,F7,F82）+ 字节编译。

---

## 11. 启动方式

```bash
# 默认启动（含环境检查）
python main.py

# 安装依赖后启动
python main.py --install

# 开发模式（自动重载）
python main.py --dev

# 跳过环境检查直接启动
python main.py --no-check

# 指定端口
python main.py --port 9000
```

**环境要求**：
- Python 3.12+
- Ollama 服务运行中（`ollama serve`）
- 已拉取模型：`ollama pull qwen2.5:7b` 和 `ollama pull nomic-embed-text:latest`
- 可选：复制 `.env.example` 为 `.env`，用环境变量覆盖模型名、Ollama 地址、API Key、限流阈值等

**一键演示**（服务已启动后）：
```bash
python scripts/seed_demo.py     # 建库 → 上传示例文档 → 提问 → 打印逐句溯源与评估指标
```

**容器启动**：
```bash
docker compose up -d            # Ollama + 模型拉取 + 应用，一条命令
```

**访问地址**：
- 主页面: `http://localhost:8080/static/index.html`
- 管理页面: `http://localhost:8080/static/admin.html`
- API 文档: `http://localhost:8080/docs`
- 健康检查: `http://localhost:8080/api/health`
