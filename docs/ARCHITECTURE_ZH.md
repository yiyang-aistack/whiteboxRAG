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
├── pyproject.toml             # 依赖声明（运行依赖 + dev 依赖组）
├── uv.lock                    # 锁定依赖版本（提交）
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
│   ├── contradiction.py       # 答案/原文矛盾检测（结构化声明比对）
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
│   ├── text_split.py          # 统一分句器（带字符偏移）
│   └── async_tasks.py         # 异步任务
├── static/                    # 前端页面
│   ├── index.html             # 主页面（仅结构 + 内联事件绑定）
│   ├── ab_test.html           # A/B 测试页面
│   ├── admin.html             # 管理页面
│   ├── css/                   # 各页面自定义样式（index / ab_test / admin）
│   └── js/                    # 页面脚本，按功能拆分（共享 theme.js，见 8.1 / 8.2 / 8.3）
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
- BM25 权重: 0.6，向量权重: 0.4
- top_k: 5，重排序数量: 10
- 相似度阈值: 0.5
- 上下文压缩: 启用，最大 2048 tokens
- 索引等待: `bm25.build_wait_ms` = 500 ms；空路由权重重分配: `reroute_on_empty_route` = true

**关键流程**：
1. 查询改写（错别字纠正 + 同义词扩展）
2. jieba 分词
3. BM25 检索 + 向量检索并行执行
4. 加权融合得分：`score = BM25×0.6 + Vector×0.4`
5. 重排序（可选）
6. 上下文压缩（可选）

**路由降级**（否则会静默产生"知识库中没有"的假结论）：

- 融合公式会为每一路保留权重，若某一路无结果但权重仍在，候选得分上限就只有另一路的权重（0.6/0.4 配置下为 0.4 < 阈值 0.5），全部候选被阈值淘汰 —— 这正是"重启后首次提问返回空、同一问题第二次正常"的原因（BM25 懒加载，构建完成比该次请求晚约 20 ms）。
- 因此：检索会等待进行中的构建最多 `bm25.build_wait_ms`（典型知识库重建仅数十毫秒）；等待超时或该路本就没有词面命中时，把这一路的权重在该次查询中让给仍有效的路（`reroute_on_empty_route`）。
- 白盒可见：`debug_info.route_degraded`、`debug_info.effective_weights` 与检索漏斗的 `params` 都会给出实际参与融合的权重；`validate_retrieval_weights()` 会在启动时对"权重 < 阈值"的组合告警。

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
| 检索质量（平均相似度） | 命中片段融合分的均值（原字段名 retrieval_recall，是平均相似度不是召回率） | `mean(chunk.score)` |
| 检索分数标准差 | 命中片段分数的离散程度；只召回一条时不适用 | `std(chunk.score)` |
| 答案忠实度 | 回答与检索上下文的语义相似度（Embedding 余弦，失败时退化为词覆盖率） | `cos(answer, context)` |
| 语义一致性 | 逐句与上下文语义相似度的均值 | `mean(cos(sentence, context))` |
| 引用覆盖率 | 带可解析 `[文档N]` 标记（编号在上下文范围内）的句子占比 | `cited_sentences / sentences` |
| 答案相关性 | 问题与答案的语义相似度 | `cos(query, answer)` |
| 幻觉率 | 与上下文相似度低于 `hallucination_sim_floor` 的句子占比 | `unsupported / sentences` |
| 拒答准确率 | 仅对业务范围外的问题计分；域内不计分（权重分摊） | 拒答 1 / 未拒答 0 |
| 空响应 | 回答是否为兜底文案（中英双语 + 场景自定义文案） | 布尔 |
| 响应长度 | 落在场景/全局 `[min, max]` 区间内得满分 | 字符数 |
| 综合评分 | 各指标「达标信用」的加权平均（达标线 = `pass_score` 0.7，理想值 = 1.0） | `Σ w·credit / Σ w` |

**评分口径（rubric v2）**：
- 每个指标按达标线归一化：`value == target` 恰好得 `pass_score`（0.7），达到理想值得 1.0；因此「指标达标」与「该指标拿到 ≥ 0.7」是同一件事，分数与判定不会再互相矛盾
- 权重可配（`evaluation.weights`，默认 有据性 0.45 / 相关性 0.15 / 安全 0.20 / 检索 0.15 / 格式 0.05），实际参与打分的子集自动重新归一化；权重与达标线见 `config/settings.yaml`
- 不可测的指标按 0 分计入（fail-closed），不适用的指标退出打分并在 `weight_coverage` 中体现；`weight_coverage` 低于 `min_weight_coverage` 直接判不通过
- 判定 `is_passing` = 综合分 ≥ `pass_score` + 显式硬门槛（非空响应、幻觉率不超限、可评估权重足够），失败原因写入 `gate_failures`
- 评分口径版本随结果返回（`rubric_version`），跨版本/跨场景的分数不可比
- 调参与标定：`scripts/evaluator_recalc.py` 用已存 trace 离线重算（无需 Embedding/LLM）；回归测试见 `tests/test_evaluator.py`

**特殊机制**：
- 低检索质量封顶：`similarity_threshold × retrieval_gate_ratio`（默认 0.5 × 0.3 = 0.15）以下时综合分上限 `low_retrieval_cap`（默认 0.3）
- 边界检测置信度 < `boundary_confidence_min`（默认 0.5）时按 `max(boundary_penalty_floor, 置信度)` 缩放分数，调整前后分数与原因记录在 `raw_score` / `adjustments` / `quality_flags` 中

### 4.9 意图分类器 — `intent_classifier.py`

对用户查询进行业务意图分类，输出意图类型和置信度。

### 4.10 查询改写器 — `query_rewriter.py`

优化用户查询以提高检索质量：
- 错别字纠正（TypoChecker）
- 同义词扩展（synonym_dict.yaml）
- 查询净化（去除无意义字符）

### 4.11 句子级溯源 — `sentence_tracing.py`

将 AI 回答拆分为句子，**先核验引用、再做相似度判定**，逐句给出归因与结论：

| 结论 | 说明 | 依据 |
|---|---|---|
| 🔗 引用已核验 | 句子带 `[文档N]` 标记，且该文档确实在本次送入 Prompt 的检索上下文中 | **引用（证据）** |
| ✅ 直接引用 / 摘要改写 | 与原文片段高度一致 / 由原文概括改写 | 相似度启发式（≥ 绝对下限，且位于本答案分数分布的相对高位） |
| ⚠️ 低置信度 | 相似度偏低但未判为漂移，建议人工核对 | 相似度启发式 |
| ❌ 无依据推断 / 无来源 | 与上下文语义偏离较大，存在幻觉风险 | 相似度启发式 |
| ❔ 未核验 | 溯源组件不可用（如 Embedding 失败），既未证实也未判为幻觉 | 无（不计入漂移率） |

要点：

- **两种归因路径**：有可解析引用时归属被引用分块（`attribution=citation`，即使该分块相似度为 0 也会直接打分并作为来源展示）；无可用引用时才走相似度（`attribution=similarity`）。每句都会附带 `similarity_level`，便于同时看到相似度结论。
- **阈值策略**：绝对下限（`sentence_tracing.abs_*_floor`）保证不会凭「离某段最近」就宣称有依据；相对秩（`*_percentile`）保证长答案里只有排名靠前的句子才能被判为直接引用；句子数少于 `min_adaptive_samples` 时只用绝对下限。全部阈值见 `config/settings.yaml`。
- **相似度结论是启发式**，界面与文档均明确标注，不构成事实核验。
- **字符偏移**：每句返回 `start`/`end`/`index`，流式增量事件同样携带偏移，前端据此归因，不再依赖两套分句下标对齐；分句统一由 `service/text_split.py` 提供（不会把 `0.4` 这类小数切断）。
- **流式与最终一致**：流式增量与最终全量溯源都走同一个 `_classify_sentence` 原语。

`SentenceTracer.detect_contradictions()` 保留为兼容入口，内部委托 [§4.12](#412-矛盾检测--contradictionpy) 的结构化实现。

### 4.12 矛盾检测 — `contradiction.py`

把答案与**真正送入 Prompt 的分块**逐条比对，找出"回答写错了"的句子。与相似度溯源的区别：溯源回答"这句话从哪来"，矛盾检测回答"这句话对不对"。

- **比较对象是"声明"而不是子串**：数值 + 单位（时长/长度/重量/金额/百分比/计数/日期）先归一化再比较（7 天 == 168 小时、1 万元 == 10000 元；不同币种不做比较），极性表述（支持/不支持、全额/部分、免费/收费、已发货/未发货）单独一趟识别。
- **阈值来自 `contradiction:` 配置段**：相对偏差需超过 `relative_tolerance`（默认 0.2，故 0.4 vs 0.6 会报出）；百分比另有 `percent_point_tolerance`（默认 5 个百分点，避免 50% vs 60% 只有 17% 相对偏差而漏报）；日期用 `date_tolerance_days`。
- **先和解、再判定**：只有当原文中**没有任何同主题声明**能解释该数值时才报矛盾（原文同时写了 0.4 与 0.6，则答案写 0.6 不算矛盾）；比较前要求两侧句子共享主题 token（`require_shared_topic`），避免同一分块内无关数字互相"矛盾"。
- **产出与呈现**：每条结论带答案句偏移、原文分块 id、原文句子及其片段偏移，因此前端既能给句子挂"数值/状态不一致"标记（一句话可以既是原样引用、又在数字上写错），也能高亮原文片段；结论同时进入 trace（`GET /api/chat/trace/{id}`）与 SSE 事件，历史溯源可回放。
- **接入点**：`LLMPipeline.query()` / `query_stream()`（正常问答）、`POST /api/chat/simulate`（假设分析：删掉某个分块后是否开始与原文矛盾）、A/B 对比（`contradiction_count` 参与对比汇总）。`SentenceTracer.detect_contradictions()` 保留为兼容入口，内部委托本模块。

### 4.13 未召回诊断 — `recall_diagnostic.py`

诊断哪些文档预期应该被召回但未命中，分析根因：
- chunk 过大
- embedding 不匹配
- 关键词缺失
- 文档解析失败

### 4.14 规则引擎 — `rule_engine.py`

监控用户反馈模式，自动检测重复问题模式并固化为业务规则。

### 4.15 溯源管理 — `trace.py`

`TraceManager` 和 `Trace` 类，记录和管理每次问答的完整溯源信息，持久化到 `./storage/traces/`。

### 4.16 拼写检查 — `typo_checker.py`

中文错别字检测与纠正，基于配置的字符映射表。

---

## 5. API 路由层（api/）

### 5.1 对话问答 — `chat.py`

| 端点 | 方法 | 功能 |
|---|---|---|
| `/api/chat/stream` | POST | 流式问答（SSE），支持实时输出；`stream: false` 时返回完整溯源 JSON |
| `/api/chat/trace` | POST | 按 trace_id 查询溯源详情 |
| `/api/chat/trace/{trace_id}` | GET | 按 trace_id 查询溯源详情 |
| `/api/chat/simulate` | POST | 模拟问答（假设分析：手动增删证据片段后重新生成，可与原回答对比） |
| `/api/chat/miss-scan` | POST | 按需全库漏召回扫描（重新向量化候选片段，返回临界未召回片段与根因） |
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
| `/api/chat/retrieval-defaults` | GET | 混合检索默认参数（直接来自 `settings.yaml` 的 `retriever` 段，前端默认值来源） |

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
| `text_split.py` | 统一分句器（带字符偏移）：句子级溯源 / 评估指标 / 上下文压缩 / 流式增量共用同一套句子边界 |

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
| `vector_store` | persist_directory, top_k(5), similarity_threshold(0.1)（组件级，混合检索优先用 `retriever.*`） |
| `bm25` | top_k(5), tokenizer: jieba, build_wait_ms(500)（检索等待进行中索引构建的上限） |
| `retriever` | **混合检索默认值的唯一来源**：mode: hybrid, bm25_weight: 0.6, top_k: 5, similarity_threshold: 0.5, reroute_on_empty_route: true, query_rewrite_enabled, rerank_enabled, rerank_top_k: 10；由 `GET /api/chat/retrieval-defaults` 输出给前端 |
| `document_parser` | chunk_size: 512, chunk_overlap: 50, max_file_size: 50MB |
| `boundary` | confidence_threshold: 0.5, semantic_threshold: 0.3 |
| `circuit_breaker` | failure_threshold: 50%, timeout: 60s |
| `trace` | enabled: true, trace_directory: ./storage/traces |
| `sentence_tracing` | abs_drift_floor(0.35), abs_summary_floor(0.55), abs_direct_quote_floor(0.75), fine_weight/coarse_weight, summary_percentile(0.75), direct_quote_percentile(0.90), min_adaptive_samples(3) |
| `contradiction` | enabled: true, relative_tolerance(0.2), absolute_tolerance(0), percent_point_tolerance(5), date_tolerance_days(1), high/medium_severity_ratio, max_results(20), require_shared_topic(true) |
| `evaluation` | rubric_version(2.0), pass_score(0.7), min_weight_coverage(0.6), weights(有据性 0.45 / 相关性 0.15 / 安全 0.20 / 检索 0.15 / 格式 0.05), target_values（各指标达标线，标定自 storage/traces）, hallucination_sim_floor(0.35), retrieval_gate_ratio(0.3), low_retrieval_cap(0.3), boundary_confidence_min(0.5), boundary_penalty_floor(0.5), report_directory |
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

主页面只保留结构与内联事件绑定，样式与脚本全部外置（免构建、无打包步骤，按依赖顺序加载）：

| 文件 | 职责 |
|---|---|
| `static/css/index.css` | 自定义滚动条、流式光标 / 加载动画、拖拽上传区、消息气泡、白盒溯源着色、深色主题覆盖 |
| `static/js/theme.js` | 主题初始化与切换（在 `<head>` 同步加载，三个页面共用） |
| `static/js/core.js` | 全局 `state`、初始化、`fetchJSON`、术语表与得分 / 颜色 / 转义工具 |
| `static/js/kb.js` | 知识库管理、文件管理、上传 |
| `static/js/chat.js` | 对话输入、消息渲染、边界拒答卡片 |
| `static/js/provenance.js` | 答案句级取证渲染、每条消息的溯源快照 |
| `static/js/diagnostics.js` | 引用标注校验、后端业务诊断、检索漏斗、漏召回扫描 |
| `static/js/simulation.js` | 假设分析（删掉证据片段后的答案对比） |
| `static/js/source-panel.js` | 流式对话、来源面板、质量评估、意图卡片、逐句可信度溯源、漏召回诊断 |
| `static/js/trace-view.js` | 溯源视图切换、业务摘要视图（模块 1-5）、弹窗居中 / 拖拽工具 |
| `static/js/monitor.js` | LLM 健康检查、监控看板与请求分布图 |
| `static/js/ui.js` | Toast 通知、意图 / 回答反馈对话框、ESC 关闭 |
| `static/js/abtest.js` | A/B 测试与检索默认配置（`/api/chat/retrieval-defaults`）拉取 |
| `static/js/history.js` | 历史记录与按 trace_id 回放溯源 |

脚本以经典 `<script src>` 形式加载（非 ES module），顶层函数仍在全局作用域，因此 HTML 中的
内联事件（`onclick="..."`）无需修改。

核心交互界面，包含以下功能模块：

| 模块 | 功能 |
|---|---|
| **聊天对话** | 实时流式问答，支持上下文连续对话 |
| **检索溯源面板** | 占屏幕 1/3 宽度，分"业务摘要"和"技术详情"两个视图 |
| **知识库管理** | 知识库选择、创建、删除 |
| **文件管理** | 文档上传、查看、删除 |
| **A/B 测试入口** | 点击打开独立 A/B 测试窗口 |
| **场景选择** | 按业务场景切换检索策略 |

**聊天区 — 答案内联取证**：
- 答案按句着色：直接引用（绿）、摘要改写（黄）、低置信（橙）、无依据推断（红），风险句尾挂角标
- 答案中的 `[文档N]` 引用渲染为可点击 chip：有效引用蓝色、无效引用红色，点击后切到技术视图并高亮对应来源卡片
- 每条回答底部显示取证概览（取证度 x/y 句、覆盖文档数、漂移率、综合分）与「查看本次溯源 / 反馈」入口
- 每次问答保存独立溯源快照，历史消息可单独回看，面板顶部提示条可「回到最新」
- 流式阶段展示 pipeline 进度提示（检索中 / 生成中），替代静默等待

**检索溯源 — 业务摘要视图**（面向业务管理员）：
- 知识库匹配结论卡片（首屏第一眼，红色/琥珀色/绿色状态）
- 本次问答总览（综合评分、风险标签、业务意图）
- 检索漏斗（候选 → 阈值过滤 → 压缩 → 进入回答，含被淘汰候选与原因及调参建议）
- 漏斗内置两个白盒动作：「🔎 扫描全库漏召回」（按需全库扫描，展示临界未召回片段与根因）与「🧪 假设分析」（勾选增删证据片段后重新生成答案，并与原回答并排对比漂移率与句级取证）
- 参考资料召回汇总（进入回答的文档列表 + 中文化的命中理由）
- AI 逐句可信度溯源（✅/⚠️/❌ 标签 + 引用标注校验 + 点击查看原文）
- 智能诊断 & 优化建议（含后端业务诊断状态与漏召回扫描是否执行）
- 质量指标（百分比展示，中文释义）

**检索溯源 — 技术详情视图**（面向技术人员）：
- 查询改写流水
- 三路检索对比（BM25 vs 向量 vs 融合，可视化得分条）
- 融合计算详情
- 完整检索参数
- 检索结果卡片（含融合分/分项分、命中理由、按需拉取完整原文）
- 检索漏斗明细（进入回答 / 未进入回答，逐条标注淘汰环节）
- 句子级溯源详情（含相似度数值）
- 引用标注校验原文、未召回诊断原始日志、后端业务诊断原文

### 8.2 A/B 测试页面 — `ab_test.html`

独立窗口打开的 A/B 对比测试工具：
- 多变体配置（检索模式、BM25 权重、相似度阈值、top_k）
- 测试执行进度栏（步骤指示器 + 进度条动画）
- 配置区域始终可见（点击"开始测试"后不隐藏）
- 变体对比结果卡片（评估得分、召回数量、漂移率）
- 统计摘要（平均值对比）

样式与脚本同样外置（`static/css/ab_test.css` + `static/js/ab_test-*.js`，加载顺序即依赖顺序）：

| 文件 | 职责 |
|---|---|
| `static/css/ab_test.css` | 滑动进入动画、滚动条、color-scheme 与深色主题覆盖 |
| `static/js/theme.js` | 主题初始化与切换（三个页面共用） |
| `static/js/ab_test-core.js` | i18n 别名与状态、`init()`、检索默认值拉取、模式切换、批量问题解析、知识库列表 |
| `static/js/ab_test-variants.js` | 变体表单生成 / 按配置回填默认值 / 移除变体 |
| `static/js/ab_test-utils.js` | `escapeHtml`、`numberOrNull`（空值 -> null 交后端处理）、`collectVariants` |
| `static/js/ab_test-run.js` | 执行测试与进度栏 |
| `static/js/ab_test-batch.js` | 批量测试与逐问题对比渲染 |
| `static/js/ab_test-results.js` | 单次对比结果渲染 |
| `static/js/ab_test-boot.js` | 页面入口 `init()`，必须最后加载 |

### 8.3 管理页面 — `admin.html`

后台管理界面：
- 知识库创建与删除
- 文档上传与管理
- 分块内容浏览
- 向量数据可视化
- 分页查询

样式与脚本同样外置（`static/css/admin.css` + `static/js/admin-*.js`）：

| 文件 | 职责 |
|---|---|
| `static/css/admin.css` | 侧边栏过渡、知识库卡片、标签页、代码预览、深色主题覆盖 |
| `static/js/theme.js` | 主题初始化与切换（三个页面共用） |
| `static/js/admin-core.js` | 全局 `state`、知识库列表加载与渲染、切换当前知识库 |
| `static/js/admin-files.js` | 文件列表、原文预览、删除文件 |
| `static/js/admin-chunks.js` | 分块列表 / 分页 / 向量详情 |
| `static/js/admin-actions.js` | 知识库增删、上传、刷新、时间与大小格式化 |
| `static/js/admin-init.js` | DOMContentLoaded 初始化与事件绑定 |

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
| `test_retrieval_funnel.py` | 检索漏斗淘汰归因（阈值/压缩/top_k） | ✅ |
| `test_retrieval_degradation.py` | 空路由不得导致空回答：等待索引构建（`bm25.build_wait_ms`）、权重让渡（`reroute_on_empty_route`）、漏斗报告实际权重、启动告警 | ✅ |
| `test_hybrid_disclaimer_dedupe.py` | 混合回答免责声明只出现一次：流式丢弃重复（偏移仍有效）、已生成答案折叠重复（忽略空白差异） | ✅ |
| `test_streaming_trace_offsets.py` | `query_stream` 流式偏移与最终溯源一致；模型复述免责声明时也不会重复展示 | ✅ |
| `test_chat_debug_endpoints.py` | 调试端点：`/api/chat/simulate` 与 `/api/chat/miss-scan` | ✅ |
| `test_retrieval_defaults.py` | 检索默认值来自 `settings.yaml` 并提供给前端；前端页面无硬编码默认值 | ✅ |
| `test_doc_analyzer.py` | 文档分析器功能 | ✅ |
| `test_query_rewrite.py` | 查询改写（错别字、同义词） | ✅ |
| `test_boundary_detector.py` | 业务边界（OOD）检测与投票逻辑 | ✅ |
| `test_llm_citations.py` | 引用强制与校验 | ✅ |
| `test_abtest_integration.py` | A/B 测试流程完整性 | ✅ |
| `test_abtest_rules.py` | A/B 测试规则与生效率持久化 | ✅ |
| `test_async_tasks_kb.py` | 异步入库任务与知识库元数据 | ✅ |
| `test_i18n_consistency.py` | 中英文案与占位符一致性（后端 i18n.py + 前端 i18n.js，并检查英文词典无汉字残留） | ✅ |
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

# 安装依赖后启动（内部执行 uv sync --locked）
uv run python main.py --install

# 开发模式（自动重载）
uv run python main.py --dev

# 跳过环境检查直接启动
uv run python main.py --no-check

# 指定端口
uv run python main.py --port 9000
```

**环境要求**：
- Python 3.12+ 与 uv 0.10+（`uv sync` 负责创建 `.venv` 并安装 `uv.lock` 锁定的依赖）
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
