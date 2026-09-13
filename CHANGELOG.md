# Changelog

All notable changes to whiteBoxRAG are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- Added `service/path_safety.py` (`sanitize_filename()` / `safe_join()`) and routed every
  user-controlled path segment through it. This fixes an unauthenticated arbitrary file read in
  `GET /api/document/download/optimized`, arbitrary file write/delete through upload
  filenames (multipart `filename` with `../`), and the same class of issue for `trace_id`,
  `task_id`, `logger_name` and the metadata `stored_name`.
- CORS no longer combines a wildcard origin list with `allow_credentials`, a combination browsers
  reject and which let any website drive the API.
- Documentation no longer claims that `API_KEY` protects the API: the setting is reserved and not
  enforced. Added "Security" sections to `README.md` / `README_ZH.md`, clarified comments in
  `.env.example` and `config/settings.yaml`, and a new `docopt.invalid_file_name` message.

### Added
- `docker-compose.yml` — one-command local stack (Ollama + model pull + app) so the system can be
  started with `docker compose up` without installing Ollama on the host.
- `scripts/seed_demo.py` — seeds a demo knowledge base from `examples/demo/whiteBoxRAG_FAQ.txt`,
  uploads it and asks a traced question, printing the reported answer, citation labels and metrics.
- `examples/demo/` — a small self-describing document used by the demo script.
- `requirements-dev.txt` — pytest/pytest-asyncio/flake8 for the test suite and linting.
- `.dockerignore` — keeps runtime data (`storage/`), caches and secrets out of the image.
- `LICENSE` (MIT), `CONTRIBUTING.md`, `CHANGELOG.md`.
- `.github/workflows/ci.yml` — i18n lint, offline test subset and flake8 error checks.
- `docs/ARCHITECTURE_ZH.md` — module/API/configuration specification (moved out of `README_ZH.md`).

### Changed
- Product name unified across the UI, startup banner and docs: the brand is `whiteBoxRAG` in both
  locales, and "RAG debugger"（RAG 调试台）is kept only as the positioning tagline. Removed the
  competing names that lived in the English UI (`RAG Trace & Debugger`, `Junsu - RAG Debugger
  System`, `YY LLM OPS`), aligned the README taglines with them, and documented the GitHub
  description/topics to use in `CONTRIBUTING.md`.
- `README.md` / `README_ZH.md` restructured as mirrored landing pages: badges, language switch,
  TL;DR quick start, screenshots, highlights and capability comparison moved to the top.
- Testing documentation now distinguishes deps for running (`requirements.txt`) from deps for
  development (`requirements-dev.txt`).
- Docker documentation clarifies that a container cannot reach the host Ollama via `localhost`
  (use `host.docker.internal` or the compose service name).

### Fixed
- `requirements.txt` now declares the packages the code imports directly but previously relied on
  transitively, or did not list at all: `pdfplumber`, `numpy`, `requests`, `ollama`, `httpx`,
  `psutil`, `APScheduler`, `scikit-learn`, `chardet`. On a clean install `psutil`, `pdfplumber`
  and `scikit-learn` were missing, which silently disabled system metrics, fell back to the weaker
  pypdf extractor and made the document-relation analysis fail.
- Documentation: the document-optimizer endpoints are now documented as `/api/document/...`,
  which matches the router prefix in `api/routes/document_optimizer.py`. The previously documented
  `/api/document-optimizer/...` paths returned 404.
- `.gitignore` covers `storage/chroma_db/`, a legacy persist directory that was not ignored even
  though the current default is `./storage/vectordb`.
- `core/recall_diagnostic.py`: `_analyze_score_threshold()` no longer raises `NameError` for an
  undefined `total_docs`, and `_analyze_metadata_filter()` no longer shadows the i18n `_()` helper
  (which made its "knowledge base not found" path raise `UnboundLocalError`). Both percentage
  calculations are now guarded against an empty knowledge base.
- `tests/test_parser.py` updated to the current `_split_text(text, file_name, chunking_params)`
  signature; `tests/test_abtest_rules.py` now calls the rule engine's documented `flush()` before
  simulating a restart. The full offline suite (57 tests) passes and runs in CI.
- `README_ZH.md` API tables corrected against the actual routes (`DELETE /api/knowledge/{kb_id}`,
  `DELETE /api/knowledge/{kb_id}/files/{file_id}`, `POST /api/scenario/{scenario_id}/validate`,
  `POST /api/evaluation/evaluate`, `POST /api/evaluation/report`, `POST /api/chat/history`) and the
  non-existent `/api/document-optimizer/query-analysis` entry was replaced with the real endpoints.
- Test coverage table now lists all files under `tests/`.

## [1.0.0] - 2026-09-12

### Added
- Fully local RAG platform: FastAPI + LlamaIndex 0.10 + ChromaDB + Ollama, sized for 8 GB CPU hosts
  with no Redis/MySQL/message-broker dependency.
- Hybrid retrieval (BM25 via jieba + dense vectors) with configurable weights, threshold filtering,
  reranking and context compression, plus background BM25 re-indexing.
- White-box answer provenance: enforced `[doc_id]` citations and sentence-level tracing that labels
  every sentence as direct evidence, low confidence or unsupported drift.
- Retrieval diagnostics: non-recall root-cause analysis (metadata filter, score threshold, mode
  mismatch, keyword gap) and document coverage analysis.
- Business-scope (OOD) boundary detection combining keyword rules and LLM semantic judgement, with
  an OOD rejection path.
- Feedback-driven rule engine with batched disk writes and rule-effectiveness reporting.
- Evaluation framework: retrieval, answer-quality and safety metrics, score caps for low recall and
  low boundary confidence, report generation.
- A/B testing of retrieval configurations (single and batch), exposed through a dedicated UI.
- Multi-format ingestion (PDF/Word/TXT/Excel/PPTX) with magic-number validation, asynchronous
  task execution and progress reporting.
- Scenario-based configuration (`technical_doc`, `customer_service`), per-KB overrides.
- Operations: rate limiting, structured logging, performance monitoring, scheduled jobs,
  circuit breaker for the LLM backend, zh-CN/en-US i18n driven by `Accept-Language`.
- Deployment assets: `main.py` one-click launcher, `deploy/Dockerfile`, Gunicorn config and
  deployment manual (`deploy/DEPLOY.md`).

<!-- Replace OWNER/REPO with the GitHub slug once the repository is published. -->
[Unreleased]: https://github.com/OWNER/REPO/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/OWNER/REPO/releases/tag/v1.0.0
