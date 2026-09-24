# whiteBoxRAG — Architecture and Functional Specification

> **Version**: 1.1.0  
> **Target**: Enterprise-grade, lightweight, on-premise RAG (Retrieval-Augmented Generation) system  
> **Runtime**: 8 GB RAM CPU, built on LlamaIndex + ChromaDB + Ollama  
> **Last updated**: 2026-09-24

> This document is a **module-level technical specification** for developers and maintainers, covering module responsibilities, data flow and parameters.
> To get started quickly, see [README.md](../README.md) (中文: [README_ZH.md](../README_ZH.md)).
> Deployment and operations details: [deploy/DEPLOY.md](../deploy/DEPLOY.md).

---

## Table of Contents

- [1. System Overview](#1-system-overview)
- [2. Technology Stack](#2-technology-stack)
- [3. Project Structure](#3-project-structure)
- [4. Core Modules (`core/`)](#4-core-modules-core)
- [5. API Route Layer (`api/`)](#5-api-route-layer-api)
- [6. Service Layer (`service/`)](#6-service-layer-service)
- [7. Configuration System (`config/`)](#7-configuration-system-config)
- [8. Frontend Pages (`static/`)](#8-frontend-pages-static)
- [9. Deployment (`deploy/`)](#9-deployment-deploy)
- [10. Test Coverage (`tests/`)](#10-test-coverage-tests)
- [11. How to Start](#11-how-to-start)

---

## 1. System Overview

whiteBoxRAG is a lightweight, on-premise RAG system for enterprises. Its core design goals:

| Capability | Description |
|---|---|
| **White-box provenance** | Full pipeline trace for every Q&A: query rewrite → retrieval → LLM generation → sentence-level tracing. **Citation verification first, similarity heuristic second** — a citation-verified sentence = actual evidence. |
| **Hybrid retrieval** | BM25 keyword × dense vector weighted fusion (BM25 weight 0.6, vector weight 0.4), with optional rerank, context compression and graceful empty-route degradation (`reroute_on_empty_route`). |
| **Structured contradiction detection** | Not just similarity comparison: extracts structured *claims* (numbers, dates, amounts, polarity phrases) from the answer and normalizes them before comparing against the source — 7 days == 168 hours, 0.4 vs 0.6 reports a 33% relative deviation. |
| **Feedback → rule engine** | User feedback (wrong answer, missed recall, intent misclassification) accumulates; once a pattern exceeds `rule_engine.min_feedback_count` it auto-promotes to a business rule with live hit-rate reporting via API. |
| **On-premise by design** | Built on local Ollama models; data never leaves the enterprise network. Parsing, embedding, retrieval and generation all run locally — zero external services by default. |
| **Lightweight footprint** | Runs on an 8 GB RAM / 4-core CPU box. Vectors, documents, traces and task state are plain local files — no Redis, no MySQL, no broker. |
| **Tune-and-evaluate** | Scenario profiles (technical_doc, customer_service, etc.) + rubric v2 evaluation framework + A/B testing. Weights and pass lines are configurable; every score carries a rubric version stamp. |

### System Architecture


---

## 2. Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| **Web framework** | FastAPI + Uvicorn | High-performance async API |
| **RAG framework** | LlamaIndex 0.10 | Document indexing, retrieval, generation |
| **Vector store** | ChromaDB | Local persistent vector database |
| **LLM runtime** | Ollama (qwen2.5:7b) | Local LLM inference |
| **Embedding** | nomic-embed-text (768-dim) | Text vectorization |
| **Sparse retrieval** | rank-bm25 + jieba | BM25 keyword retrieval (Chinese + English) |
| **Document parsing** | pypdf / python-docx / openpyxl / python-pptx | Multi-format document extraction |
| **Frontend** | Tailwind CSS CDN + vanilla JS | Build-free responsive UI |
| **Deployment** | Docker + Gunicorn | Containerized production |

---

## 3. Project Structure

---

## 4. Core Modules (`core/`)

### 4.1 LLM Q&A Pipeline — `llm_pipeline.py`

The system's central orchestrator that drives the full Q&A flow.

**Flow**:

**Key properties**:
- **Retrieval quality gate**: when the average fused score ≤ 0.4 the pipeline switches to *hybrid-answer mode* — a disclaimer is prepended ("no relevant content in the knowledge base") followed by an LLM-based answer. `_ensure_hybrid_format()` enforces this format regardless of whether the LLM followed instructions.
- **Sync + streaming interfaces**: `query()` returns a full trace JSON; `query_stream()` emits SSE token stream plus incremental trace events.
- **CircuitBreaker protection**: every LLM call is wrapped; the breaker trips on error-rate spikes or latency spikes.

### 4.2 Hybrid Retriever — `retriever.py`

Unified retrieval interface supporting three modes.

| Mode | Description | Best for |
|---|---|---|
| `hybrid` (default) | BM25 keyword × vector semantic, weighted fusion | General-purpose, keyword + semantics |
| `vector` | Dense vector similarity only | Semantically close but differently worded queries |
| `bm25` | Keyword only | Precise term matching |

**Default parameters** (from `settings.yaml`):
- BM25 weight: 0.6, vector weight: 0.4
- top_k: 5, rerank top_k: 10
- Similarity threshold: 0.5
- Context compression: enabled, max 2048 tokens
- Index build wait: `bm25.build_wait_ms` = 500 ms; empty-route weight handover: `reroute_on_empty_route` = true

**Flow**:
1. Query rewrite (typo correction + synonym expansion)
2. jieba tokenization (zh + en)
3. BM25 retrieval and vector retrieval run in parallel
4. Weighted fused score: `score = BM25 × 0.6 + Vector × 0.4`
5. Optional rerank
6. Optional context compression

**Graceful degradation** (prevents false "KB has nothing" conclusions after a restart):

- The fusion formula reserves a weight for each route. If one route returns nothing but its weight is still present, the fused-score ceiling for every candidate equals the other route's weight (e.g., 0.4 < threshold 0.5 in the 0.6/0.4 config), and every candidate gets filtered out — the classic "first query after restart returns empty, second query works" symptom.
- Fix: retrieval waits up to `bm25.build_wait_ms` (typically tens of ms for a KB rebuild) for any in-flight BM25 build. If the wait times out or the route genuinely has no hits, its weight is handed over to the working route for *this query only* (`reroute_on_empty_route`).
- White-box visibility: `debug_info.route_degraded`, `debug_info.effective_weights` and the retrieval funnel's `params` all report the weights actually used; `validate_retrieval_weights()` warns at startup whenever a weight/threshold combination risks self-filtering.

### 4.3 LLM Adapter — `llm_adapter.py`

Unified interface over different LLM providers.

| Adapter | Model | Notes |
|---|---|---|
| OllamaAdapter | qwen2.5:7b | Local deployment, default provider |
| OpenAIAdapter | gpt-4o (or configured) | Cloud call (API key required) |

Supports sync generation, streaming generation and embedding generation.

### 4.4 Vector Store Management — `vector_store.py`

ChromaDB-backed local persistent vector store.

- One ChromaDB collection per knowledge base
- Document add, bulk delete, similarity search
- Embedding model: nomic-embed-text (768-dim)
- Persistence path: `./storage/vectordb`

### 4.5 Document Parser — `document_parser.py`

Multi-format document parsing and chunking.

| Format | Parser |
|---|---|
| PDF | pypdf (falls back to pdfplumber when pypdf missing) |
| DOCX / DOC | python-docx (DOC only partially compatible — convert to DOCX first) |
| XLSX / XLS | openpyxl |
| PPTX | python-pptx |
| TXT | native read |

Chunking strategy:
- Chunk size: 512 characters
- Chunk overlap: 50 characters
- Per-file upload limit: 50 MB (`document_parser.max_file_size`)

### 4.6 Boundary Detector — `boundary_detector.py`

Business-scope Out-Of-Domain (OOD) detection.

Three-layer mechanism:
1. **Keyword blacklist/whitelist**: fast match; hit → immediate verdict
2. **Semantic check**: embedding similarity against configured business topics
3. **Retrieval-result fallback**: average retrieval similarity below threshold → OOD

Configurable parameters:
- Confidence threshold: 0.5
- Semantic similarity threshold: 0.3
- Whitelist topics: orders, after-sales, products, accounts, tech, finance, logistics, consulting
- Blacklist topics: illegal, porn, violence, gambling, drugs

### 4.7 Circuit Breaker — `circuit_breaker.py`

State-machine circuit breaker protecting LLM calls.

| State | Description |
|---|---|
| CLOSED | Normal operation; records success/failure |
| OPEN | Fuse blown; returns error directly, waits for timeout |
| HALF_OPEN | Probing state; allows limited trial calls |

Trip conditions:
- Error rate > 50%
- Response latency > 30 s
- Fuse timeout: 60 s
- Recovery: 5 consecutive successful calls

### 4.8 RAG Quality Evaluator — `evaluator.py`

Multi-dimensional scoring of RAG answer quality.

| Metric | Description | Calculation |
|---|---|---|
| Retrieval quality (mean fused score) | Mean of retrieved chunk fused scores (note: original field `retrieval_recall` — *similarity average*, not recall) | `mean(chunk.score)` |
| Retrieval score std | Score spread among retrieved chunks; N/A when only one chunk recalled | `std(chunk.score)` |
| Answer faithfulness | Semantic similarity between answer and retrieved context (embedding cosine; falls back to word-coverage on failure) | `cos(answer, context)` |
| Semantic consistency | Mean per-sentence embedding cosine against context | `mean(cos(sentence, context))` |
| Citation coverage | Fraction of sentences carrying a parseable `[Document N]` marker whose ordinal is within the context range | `cited_sentences / sentences` |
| Answer relevance | Semantic similarity between query and answer | `cos(query, answer)` |
| Hallucination rate | Fraction of sentences whose similarity to context is below `hallucination_sim_floor` | `unsupported / sentences` |
| Rejection accuracy | Scored only for OOD queries; in-domain queries do not count (weight is redistributed) | reject=1 / not_reject=0 |
| Empty response | Whether the answer equals a configured fallback (zh/en + scenario-specific) | Boolean |
| Response length | Full score when inside the scenario/global `[min, max]` interval | Character count |
| **Overall score** | Weighted average of per-metric *credit* (value == target earns exactly `pass_score` 0.7; ideal earns 1.0) | `Σ w·credit / Σ w` |

**Rubric v2**:
- Every metric is normalized to a credit scale anchored at its pass line, so "metric passes" and "metric scores ≥ 0.7" are the same thing — the score and the verdict can never contradict each other.
- Weights are configurable (`evaluation.weights`), defaults: faithfulness 0.45 / retrieval quality 0.15 / relevance 0.15 / safety + format 0.25. The subset actually evaluable re-normalizes automatically.
- Non-applicable metrics → exit scoring, reflected in `weight_coverage`. When `weight_coverage < min_weight_coverage` the run is immediately marked failing.
- `is_passing` = overall score ≥ `pass_score` **plus** explicit hard gates (non-empty response, hallucination rate within limit, sufficient evaluable weight). Fail reasons are recorded in `gate_failures`.
- Every result carries a `rubric_version` — scores are not comparable across versions or scenarios.
- Offline recalculation from stored traces: `scripts/evaluator_recalc.py` (no embedding or LLM required). Regression tests: `tests/test_evaluator.py`.

**Special mechanisms**:
- Low-retrieval cap: when similarity < `similarity_threshold × retrieval_gate_ratio` (default 0.5 × 0.3 = 0.15), the overall score is capped at `low_retrieval_cap` (default 0.3).
- Boundary-confidence penalty: when boundary confidence < `boundary_confidence_min` (default 0.5), the score is scaled by `max(boundary_penalty_floor, confidence)`. Raw score, adjustments and quality flags are all retained.

### 4.9 Intent Classifier — `intent_classifier.py`

Classifies user queries into business intent types with a confidence score.

### 4.10 Query Rewriter — `query_rewriter.py`

Optimizes user queries to improve retrieval:
- Typo correction (`typo_checker.py`)
- Synonym expansion (`synonym_dict.yaml`, hot-reloadable)
- Query purification (strip noise)

### 4.11 Sentence-Level Provenance — `sentence_tracing.py`

Splits the AI answer into sentences, then for each sentence **runs citation verification first, similarity second**, producing per-sentence attribution and verdict:

| Verdict | Description | Basis |
|---|---|---|
| 🔗 **citation verified** | Sentence carries `[Document N]` and that document is actually present in the retrieval context fed to the prompt | **Citation = evidence** |
| ✅ **direct quote / faithful summary** | High overlap with a source chunk / faithful paraphrase | Similarity heuristic (passes absolute floor + ranks high in this answer's own score distribution) |
| ⚠️ **low confidence** | Similarity is moderate but not drift; manual review recommended | Similarity heuristic |
| ❌ **unsupported drift / no source** | Semantic divergence from context; hallucination risk | Similarity heuristic |
| ❔ **unverified** | Tracer itself unavailable (e.g. embedding backend down); neither confirmed nor counted as a hallucination | None (excluded from drift rate) |

Highlights:

- **Two attribution paths**: a parseable citation routes to `attribution=citation` (attributed sentence is displayed even if the chunk's similarity is 0). Only when no usable citation exists does the system fall back to `attribution=similarity`. Every sentence also carries `similarity_level`.
- **Threshold strategy**: absolute floors (`sentence_tracing.abs_*_floor`) prevent claims of "supported" just because a sentence is closest to some chunk; relative percentiles (`*_percentile`) ensure only top-ranked sentences in long answers are labelled direct quote. Below `min_adaptive_samples` only absolute floors are used. All thresholds live in `config/settings.yaml`.
- **Similarity verdicts are heuristics** — both the UI and docs state this plainly; they do not constitute fact checks.
- **Character offsets**: every sentence returns `start`/`end`/`index`. Streaming incremental events also carry offsets, so the browser attributes badges by character position rather than re-deriving sentence indices. Sentence splitting is unified via `service/text_split.py` (decimals like `0.4` are never broken across sentence boundaries).
- **Streaming and final are identical**: both paths go through the same `_classify_sentence` primitive.

`SentenceTracer.detect_contradictions()` is kept as a compatibility entrypoint; internally it delegates to §4.12.

### 4.12 Contradiction Detection — `contradiction.py`

Compares the answer against **exactly the chunks that were fed into the prompt** and flags sentences that are factually wrong. Difference from similarity provenance: provenance answers *where does this sentence come from*; contradiction answers *is this sentence correct*.

- **Claims, not substrings.** A claim is either a number plus its unit (duration, length, weight, money, percentage, count, calendar date) or a polarity phrase (supported vs unsupported, free vs paid, full refund vs partial refund). Units are normalized to a base (7 天 == 168 小时, 10 千元 == 10000 元); different currencies are never compared.
- **Thresholds that catch real errors.** Deviation must exceed `contradiction.relative_tolerance` (default 0.2, so the README example — source says 0.4, model says 0.6 — reports at 33% relative). Percentages additionally use `percent_point_tolerance` (default 5 points, because 50% vs 60% is only 17% relative). Dates use `date_tolerance_days`.
- **Reconciliation before accusation.** A claim is only reported when no in-scope source claim can explain it. A document listing both 0.4 and 0.6 does not contradict an answer that says 0.6. Comparisons also require both sentences to share a topic token (`require_shared_topic`), preventing unrelated numbers within a single chunk from "contradicting" each other.
- **In the trace.** Each finding carries the answer sentence's offsets, the source chunk id, the source sentence and the source snippet's offsets — so the UI can both mark the sentence (a sentence can be a perfect quote *and* wrong on a number) and highlight the source text. Findings also appear in `GET /api/chat/trace/{id}` and SSE events.
- **Entry points**: `LLMPipeline.query()` / `query_stream()` (normal Q&A), `POST /api/chat/simulate` (hypothesis analysis: removing a chunk is exactly what can turn a supported answer into a contradicted one), A/B comparison (`contradiction_count` feeds the comparison summary).

### 4.13 Non-Recall Diagnostic — `recall_diagnostic.py`

Explains *why* a document the user expected was not recalled. Root causes fall into four coarse categories, each with fine-grained labels:

| Coarse | Description | Fine-grained label |
|---|---|---|
| **metadata filtered** | Document metadata (KB binding, scenario, etc.) excluded it | `metadata_filtered` |
| **borderline** | Score landed just below `similarity_threshold` — almost made it | `borderline` |
| **single-mode hit** | BM25 or vector matched, but the other route's absence dragged the fused score below threshold | `single_mode_hit` |
| **missing keywords** | Query lacked the keywords the document needed (BM25 path returned zero) | `missing_keywords` |

Key design points:
- Full-database scans run asynchronously and cap at `recall_diagnostic.full_scan_limit` (default 500) chunks to protect throughput.
- Auto-scan only kicks in when retrieval returns empty or when debug mode is on. Users can also click 🔎 *Scan full database for missed recalls* in the UI to trigger on demand.
- Candidate non-recalled chunks get both a root-cause tag and a distance-to-threshold measure, helping tuning.

### 4.14 Rule Engine — `rule_engine.py`

Turns user feedback into executable, measurable business rules:

- **Trigger threshold**: feedback for a pattern (wrong answer / missed recall / intent misclassification) accumulating past `rule_engine.min_feedback_count` auto-promotes to a business rule.
- **Runtime application**: at query time, a matching rule can rewrite retrieval parameters, force a拒答, or enable stricter citation checking. Hit and processing logs are retained.
- **Live effectiveness**: `/api/chat/rule-effectiveness` returns hit count, hit rate and last-hit time per rule; ineffective rules can be retired manually.
- **Batched writes**: feedback writes go through an in-memory buffer flushed every 10 s or 50 changes, avoiding I/O spikes.

### 4.15 Trace Manager — `trace.py`

`TraceManager` and `Trace` classes record the full Q&A trace for each request, persisted to `./storage/traces/`.

### 4.16 Typo Checker — `typo_checker.py`

Chinese + English typo correction backed by a configurable character-mapping dictionary.

---

## 5. API Route Layer (`api/`)

### 5.1 Q&A — `chat.py`

| Endpoint | Method | Description |
|---|---|---|
| `/api/chat/stream` | POST | Streaming Q&A (SSE). Set `stream: false` for a full trace JSON in one response. |
| `/api/chat/trace` | POST | Fetch trace by trace_id |
| `/api/chat/trace/{trace_id}` | GET | Fetch trace by trace_id |
| `/api/chat/simulate` | POST | Hypothesis analysis: manually select/remove evidence chunks, regenerate, compare side-by-side with the original answer |
| `/api/chat/miss-scan` | POST | On-demand full-database scan for relevant chunks that were never recalled |
| `/api/chat/feedback` | POST | Submit user feedback on an answer |
| `/api/chat/rules` | GET / POST | List business rules / create one |
| `/api/chat/rules/{rule_id}` | DELETE | Delete a business rule |
| `/api/chat/rule-effectiveness` | GET | Rule effectiveness report |
| `/api/chat/rule-logs` | GET | Rule hit logs |
| `/api/chat/abtest` | POST | Run an A/B comparison |
| `/api/chat/abtest/batch` | POST | Run batch A/B comparisons |
| `/api/chat/history` | POST | Query conversation history |
| `/api/chat/history/stats` | GET | Conversation statistics |
| `/api/chat/history/{trace_id}` | DELETE | Delete a conversation record |
| `/api/chat/health` | GET | LLM service health check |
| `/api/chat/retrieval-defaults` | GET | Default hybrid retrieval parameters (pulled straight from `retriever:` in settings.yaml; UI reads its defaults here) |

### 5.2 Knowledge Base Management — `knowledge.py`

| Endpoint | Method | Description |
|---|---|---|
| `/api/knowledge/list` | GET | List all knowledge bases |
| `/api/knowledge/create` | POST | Create a knowledge base |
| `/api/knowledge/{kb_id}` | GET / DELETE | Get details / delete a knowledge base |
| `/api/knowledge/{kb_id}/upload` | POST | Upload a document (async vectorization, returns task_id) |
| `/api/knowledge/{kb_id}/status` | GET | Ingestion progress |
| `/api/knowledge/{kb_id}/files` | GET | List files in a KB |
| `/api/knowledge/{kb_id}/files/{file_id}` | GET / PUT / DELETE | File details / replace / delete |
| `/api/knowledge/{kb_id}/chunks` | GET | List chunks |
| `/api/knowledge/{kb_id}/chunks/{chunk_id}` | GET | Chunk detail with its vector |
| `/api/knowledge/{kb_id}/raw/{file_id}` | GET | Raw document content |
| `/api/knowledge/synonyms` | GET / POST | Synonym dictionary query / add |
| `/api/knowledge/synonyms/{term}` | DELETE | Remove synonym |
| `/api/knowledge/synonyms/reload` | POST | Hot-reload synonym dict |
| `/api/knowledge/typos` | GET / POST | Typo rule query / add |
| `/api/knowledge/typos/{typo}` | DELETE | Remove typo rule |
| `/api/knowledge/typos/reload` | POST | Hot-reload typo dict |

### 5.3 Monitoring — `monitor.py`

| Endpoint | Method | Description |
|---|---|---|
| `/api/monitor/stats` | GET | Performance statistics (request count, response time, error rate) |
| `/api/monitor/logs` | GET | Log query |
| `/api/monitor/tasks` | GET | Async task status |
| `/api/monitor/task/{task_id}` | GET | Task detail |
| `/api/monitor/stats/reset` | POST | Reset statistics |
| `/api/health` | GET | System health check |

### 5.4 Scenario Management — `scenario.py`

| Endpoint | Method | Description |
|---|---|---|
| `/api/scenario/list` | GET | List scenarios |
| `/api/scenario/{scenario_id}` | GET | Scenario detail |
| `/api/scenario/{scenario_id}/params` | GET | Resolved retrieval parameters for a scenario |
| `/api/scenario/{scenario_id}/validate` | POST | Validate scenario configuration |

**Built-in scenarios**:
- `technical_doc`: accuracy-focused, larger chunks, code examples
- `customer_service`: concise, friendly, limited response length

### 5.5 Quality Evaluation — `evaluation.py`

| Endpoint | Method | Description |
|---|---|---|
| `/api/evaluation/evaluate` | POST | Single Q&A evaluation |
| `/api/evaluation/batch` | POST | Batch evaluation |
| `/api/evaluation/report` | POST | Generate a shareable evaluation report |
| `/api/evaluation/report/{report_id}` | GET | Fetch a report by ID |
| `/api/evaluation/reports` | GET | List evaluation reports |
| `/api/evaluation/metrics` | GET | Metric definitions |

### 5.6 Document Optimizer — `document_optimizer.py`

| Endpoint | Method | Description |
|---|---|---|
| `/api/document/analyze` | POST | Analyze documents against query logs |
| `/api/document/optimize` | POST | Optimize documents |
| `/api/document/issues` | GET | Detected document issues |
| `/api/document/coverage` | GET | Query coverage stats |
| `/api/document/download/optimized` | GET | Download optimized documents |
| `/api/document/full-analysis` | POST | Run the full analysis pipeline |

---

## 6. Service Layer (`service/`)

| Module | Function |
|---|---|
| `logger.py` | Multi-level structured logging; per-module log levels; console + file output |
| `monitor.py` | Performance metrics: request count, response time, error rate, system resources |
| `scheduler.py` | Cron-based scheduled tasks (disabled by default): document optimize + daily analysis |
| `async_tasks.py` | Background task manager (document vectorization, etc.) |
| `rate_limiter.py` | Per-IP API rate limiting; static assets + health check exempt |
| `i18n.py` | zh-CN / en-US internationalization driven by `Accept-Language` |
| `http_encoding.py` | Consistent UTF-8 response encoding |
| `text_split.py` | Unified sentence splitter with character offsets — shared by provenance, evaluation, context compression and streaming |

**Logging config**:
- Level: INFO
- Path: `./storage/logs/`
- Max per file: 10 MB
- Retention: 30 days

**Monitoring config**:
- Data retention: 24 hours
- Storage: `./storage/monitor/`

---

## 7. Configuration System (`config/`)

### 7.1 Global Config — `settings.yaml`

| Section | Key parameters |
|---|---|
| `system` | app_name, version, host(0.0.0.0), port(8080), max_workers(4) |
| `llm` | provider: ollama |
| `ollama` | llm_model: qwen2.5:7b, embedding_model: nomic-embed-text, dim: 768 |
| `vector_store` | persist_directory, top_k(5), similarity_threshold(0.1) — **component-level only; hybrid retriever uses `retriever.*`** |
| `bm25` | top_k(5), tokenizer: jieba, build_wait_ms(500) |
| `retriever` | **sole source of hybrid retrieval defaults**: mode: hybrid, bm25_weight: 0.6, top_k: 5, similarity_threshold: 0.5, reroute_on_empty_route: true, query_rewrite_enabled, rerank_enabled, rerank_top_k: 10; exposed to the UI via `GET /api/chat/retrieval-defaults` |
| `document_parser` | chunk_size: 512, chunk_overlap: 50, max_file_size: 50MB |
| `boundary` | confidence_threshold: 0.5, semantic_threshold: 0.3 |
| `circuit_breaker` | failure_threshold: 50%, timeout: 60s |
| `trace` | enabled: true, trace_directory: ./storage/traces |
| `sentence_tracing` | abs_drift_floor(0.35), abs_summary_floor(0.55), abs_direct_quote_floor(0.75), fine_weight/coarse_weight, summary_percentile(0.75), direct_quote_percentile(0.90), min_adaptive_samples(3) |
| `contradiction` | enabled: true, relative_tolerance(0.2), absolute_tolerance(0), percent_point_tolerance(5), date_tolerance_days(1), high/medium_severity_ratio, max_results(20), require_shared_topic(true) |
| `evaluation` | rubric_version(2.0), pass_score(0.7), min_weight_coverage(0.6), weights(faithfulness 0.45 / retrieval_quality 0.15 / relevance 0.15 / safety+format 0.25), target_values (calibrated from storage/traces), hallucination_sim_floor(0.35), retrieval_gate_ratio(0.3), low_retrieval_cap(0.3), boundary_confidence_min(0.5), boundary_penalty_floor(0.5), report_directory |
| `scheduler` | enabled: false (scheduled tasks disabled by default) |

### 7.2 Scenario Profiles

| Scenario | File | Characteristics |
|---|---|---|
| technical_doc | `scenarios/technical_doc.yaml` | Accuracy-focused, larger chunks, code examples |
| customer_service | `scenarios/customer_service.yaml` | Concise, friendly, limited response length, smaller top_k |

### 7.3 Synonym Dictionary — `synonym_dict.yaml`

Used by the query rewriter for synonym expansion to improve recall. Hot-reloadable via `/api/knowledge/synonyms/reload`.

---

## 8. Frontend Pages (`static/`)

### 8.1 Main Page — `index.html`

The HTML is markup only — all styles and scripts are external (no build step).

| File | Responsibility |
|---|---|
| `static/css/index.css` | Custom scrollbar, streaming cursor/loading animation, drag-drop upload zone, message bubbles, sentence-color provenance, dark-theme overrides |
| `static/js/theme.js` | Theme init + toggle (loaded synchronously in `<head>`; shared across all three pages) |
| `static/js/core.js` | Global `state`, init, `fetchJSON`, glossary, score/color/escaping utilities |
| `static/js/kb.js` | KB management, file management, upload |
| `static/js/chat.js` | Chat input, message rendering, OOD boundary cards |
| `static/js/provenance.js` | Per-message provenance snapshot rendering |
| `static/js/diagnostics.js` | Citation validation, backend diagnostics, retrieval funnel, full-scan trigger |
| `static/js/simulation.js` | Hypothesis analysis (answer comparison after chunk removal) |
| `static/js/source-panel.js` | Source panel, quality eval, intent cards, per-sentence confidence, non-recall diagnostics |
| `static/js/trace-view.js` | Trace view switching, business-summary view (modules 1–5), modal centering/drag utils |
| `static/js/monitor.js` | LLM health check, monitor dashboard, request distribution charts |
| `static/js/ui.js` | Toast notifications, feedback dialogs, ESC-to-close |
| `static/js/abtest.js` | A/B test + retrieval-defaults fetch |
| `static/js/history.js` | Conversation history + trace replay by trace_id |

Scripts are classic `<script src>` tags (not ES modules), so inline `onclick="..."` handlers remain valid.

Functional modules on the main page:

| Module | Description |
|---|---|
| **Chat** | Real-time streaming Q&A with multi-turn context |
| **Retrieval provenance panel** | Occupies 1/3 of screen width; two views: Business Summary and Technical Detail |
| **KB management** | Create / delete KBs, switch active KB |
| **File management** | Upload, view, delete documents |
| **A/B test entry** | Opens the A/B test page in a separate window |
| **Scenario selector** | Switch retrieval strategy by business scenario |

**In-answer sentence coloring**:
- Direct quote (green), faithful summary (yellow), low confidence (orange), unsupported drift (red) — riskier sentences get a badge.
- `[Document N]` markers render as clickable chips: valid = blue, invalid = red. Clicking switches to technical view and highlights the source card.
- Every answer ends with a provenance summary (evidence rate, documents covered, drift rate, overall score) plus "view full trace / leave feedback" entry points.
- Streaming phase shows pipeline progress hints (retrieving / generating) instead of silent waiting.

**Business Summary view** (for business admins):
- KB match verdict card (red / amber / green status — first thing you see)
- Session overview (overall score, risk labels, business intent)
- Retrieval funnel (candidates → threshold filter → compression → enters answer; rejected candidates listed with reasons + tuning suggestions)
- Two white-box actions inside the funnel: 🔎 *Scan full database for missed recalls* and 🧪 *Hypothesis analysis* (selectively add/remove chunks, compare drift rate and per-sentence evidence side-by-side)
- Recall summary (documents entering the answer + hit reasons)
- Per-sentence confidence tracing (✅ / ⚠️ / ❌ badges + citation validation + click-to-view-source)
- Intelligent diagnostics & optimization suggestions
- Quality metrics (percentages with plain-language labels)

**Technical Detail view** (for engineers):
- Query rewrite trace
- 3-way retrieval comparison (BM25 vs vector vs fused, visualised score bars)
- Fusion calculation detail
- Full retrieval parameters
- Chunk cards (fused + per-route scores, hit reason, on-demand full-text fetch)
- Funnel breakdown (entered / not entered, each rejection stage labelled)
- Per-sentence similarity scores
- Citation-validated source text, raw non-recall diagnostics log, raw backend diagnostic text

### 8.2 A/B Test Page — `ab_test.html`

Standalone A/B comparison tool opened in its own window:
- Multi-variant configuration (mode, BM25 weight, similarity threshold, top_k)
- Progress bar (step indicator + animation)
- Config area stays visible after "Start test" is clicked
- Result cards: evaluation score, recall count, drift rate — side by side per variant
- Summary averages across variants

Styles and scripts live in `static/css/ab_test.css` + `static/js/ab_test-*.js` (load order = dependency order):

| File | Responsibility |
|---|---|
| `static/css/ab_test.css` | Slide-in animation, custom scrollbar, color-scheme, dark-theme overrides |
| `static/js/theme.js` | Theme init (shared) |
| `static/js/ab_test-core.js` | i18n aliases + state, `init()`, retrieval-defaults fetch, mode switch, batch question parser, KB list |
| `static/js/ab_test-variants.js` | Variant form generation / default-value backfill / variant removal |
| `static/js/ab_test-utils.js` | `escapeHtml`, `numberOrNull` (empty → null for backend), `collectVariants` |
| `static/js/ab_test-run.js` | Test execution + progress bar |
| `static/js/ab_test-batch.js` | Batch test + per-question comparison rendering |
| `static/js/ab_test-results.js` | Single-comparison result rendering |
| `static/js/ab_test-boot.js` | Entry-point `init()` — must load last |

### 8.3 Admin Page — `admin.html`

Backend administration UI:
- KB create / delete
- Document upload / management
- Chunk content browsing
- Vector data visualization
- Paginated queries

Styles and scripts: `static/css/admin.css` + `static/js/admin-*.js`

| File | Responsibility |
|---|---|
| `static/css/admin.css` | Sidebar transition, KB cards, tabs, code preview, dark-theme overrides |
| `static/js/admin-core.js` | Global `state`, KB list load + render, switch current KB |
| `static/js/admin-files.js` | File list, raw preview, delete |
| `static/js/admin-chunks.js` | Chunk list / pagination / vector detail |
| `static/js/admin-actions.js` | KB create/delete, upload, refresh, timestamp + size formatters |
| `static/js/admin-init.js` | DOMContentLoaded init + event binding |

---

## 9. Deployment (`deploy/`)

| File | Function |
|---|---|
| `Dockerfile` | Container image definition |
| `gunicorn.conf.py` | Gunicorn configuration (workers, timeout, logging) |
| `start.sh` | Deploy startup script (init + launch service) |
| `../docker-compose.yml` | One-command local stack: Ollama + model pull + app |

**Deploy architecture**:

⚠️ **Single worker only**: the vector-store client and BM25 index are in-process memory. Multiple workers would each maintain their own copy, causing inconsistent indexes and caches across processes.

---

## 10. Test Coverage (`tests/`)

| Test File | Coverage | CI |
|---|---|---|
| `test_api.py` | API correctness + response validation | Requires running service |
| `test_retriever.py` | Retriever strategy switching (BM25 / vector / hybrid) | ✅ |
| `test_parser.py` | Document parsing + chunking | ✅ |
| `test_path_safety.py` | Filename sanitization, `safe_join`, directory-traversal block | ✅ |
| `test_retrieval_funnel.py` | Funnel rejection attribution (threshold / compression / top_k) | ✅ |
| `test_retrieval_degradation.py` | Empty route must not cause empty answer: `bm25.build_wait_ms`, `reroute_on_empty_route`, funnel reports actual weights, startup warnings | ✅ |
| `test_hybrid_disclaimer_dedupe.py` | Hybrid-answer disclaimer appears only once: streaming drops duplicates, final answers collapse whitespace-only diffs | ✅ |
| `test_streaming_trace_offsets.py` | Streaming offsets match final trace; model-disclaimer repetition does not cause duplicate display | ✅ |
| `test_chat_debug_endpoints.py` | Debug endpoints: `/api/chat/simulate` and `/api/chat/miss-scan` | ✅ |
| `test_retrieval_defaults.py` | Retrieval defaults come from `settings.yaml`; UI has no hardcoded fallback | ✅ |
| `test_doc_analyzer.py` | Document analyzer | ✅ |
| `test_query_rewrite.py` | Query rewrite (typo + synonym) | ✅ |
| `test_boundary_detector.py` | OOD detection + voting logic | ✅ |
| `test_llm_citations.py` | Citation enforcement + validation | ✅ |
| `test_abtest_integration.py` | A/B test end-to-end | ✅ |
| `test_abtest_rules.py` | A/B test rules + effectiveness persistence | ✅ |
| `test_async_tasks_kb.py` | Async ingestion + KB metadata | ✅ |
| `test_i18n_consistency.py` | zh/en copy + placeholder consistency (backend `i18n.py` + frontend `i18n.js`, plus checks that the English dict has zero Hanzi) | ✅ |
| `test_http_encoding.py` | UTF-8 response encoding | ✅ |
| `test_bm25_simple.py` | BM25 basics | Requires running service |
| `test_hybrid_search_demo.py` | Hybrid retrieval demo | Requires Ollama |
| `test_regression.py` | Regression suite | ✅ |
| `golden_test_set.json` | Golden Q&A pair data | Data |

CI config: `.github/workflows/ci.yml` — i18n consistency check + offline-capable suite + flake8 (E9, F63, F7, F82) + bytecode compile.

---

## 11. How to Start

```bash
# Default start (with env check)
uv run python main.py

# Install deps first (runs uv sync --locked internally)
uv run python main.py --install

# Dev mode (auto-reload)
uv run python main.py --dev

# Skip env check
uv run python main.py --no-check

# Custom port
uv run python main.py --port 9000
```

**Prerequisites**:
- Python 3.12+ and uv 0.10+ (`uv sync` creates `.venv` and installs `uv.lock`-locked deps)
- Ollama running (`ollama serve`)
- Models pulled: `ollama pull qwen2.5:7b` and `ollama pull nomic-embed-text:latest`
- Optional: copy `.env.example` to `.env` and override model names, Ollama URL, API key, rate limit, etc.

**One-command demo** (service already up):
```bash
python scripts/seed_demo.py   # create KB → upload sample doc → ask → print per-sentence trace + eval metrics
```

**Container**:
```bash
docker compose up -d          # Ollama + model pull + app, single command
```

**URLs**:
- Main chat: `http://localhost:8080/static/index.html`
- Admin: `http://localhost:8080/static/admin.html`
- API docs: `http://localhost:8080/docs`
- Health check: `http://localhost:8080/api/health`
