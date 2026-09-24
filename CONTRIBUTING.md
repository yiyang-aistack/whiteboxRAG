# Contributing to whiteBoxRAG

Thanks for helping improve whiteBoxRAG. This guide covers the fastest way to get a working
development environment and the checks your pull request is expected to pass.

## 1. Development setup

```bash
git clone https://github.com/yiyang-aistack/whiteboxRAG
cd whiteBoxRAG

uv sync                      # creates .venv and installs the locked dependencies + dev group
```

Dependencies are declared in `pyproject.toml` and pinned by the committed `uv.lock` — never install
into the system interpreter and never edit the lockfile by hand. `uv sync --no-dev` is enough to
*run* the service; the `dev` dependency group adds pytest and flake8 for the test suite and linting.
CI installs exactly what the lockfile pins (`uv sync --locked`).

Changing a dependency:

```bash
uv add <package>            # adds it to pyproject.toml and refreshes uv.lock
uv lock                     # or refresh the lockfile after editing pyproject.toml by hand
uv sync                     # update .venv
```

Runtime dependencies are declared with `>=` floors, dev tooling is pinned exactly. CI and
`deploy/Dockerfile` pin uv 0.10.11 (the version that generated the lockfile) — bump those pins
together with `uv.lock` when upgrading uv.

Slow or blocked PyPI? Point uv at a mirror for that command only — `uv` ignores `.env` files, so set
the variable in your shell, in CI or in the Dockerfile:

```bash
UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple uv sync
```

Do not hardcode a mirror in `pyproject.toml`/`uv.lock`: the lockfile records the origin URL of every
sdist/wheel, so it would then only resolve from that network.

Prerequisites for a full local run:

- Python 3.12+ (uv reads `.python-version` and can install the interpreter itself)
- uv 0.10+ (`pip install uv`, `winget install --id=astral-sh.uv -e`, or the standalone installer)
- Ollama reachable on `http://localhost:11434` (override with `OLLAMA_BASE_URL` in `.env`)
- Models: `ollama pull qwen2.5:7b` and `ollama pull nomic-embed-text:latest`

## 2. Running the service

```bash
uv run python main.py --install --dev   # first run: uv sync (.venv + dev group), auto-reload
uv run python main.py --no-check        # skip the environment/Ollama pre-flight check
```

- UI: <http://localhost:8080> (redirects to `/static/index.html`)
- Swagger UI: <http://localhost:8080/docs>
- Health: <http://localhost:8080/api/health>

## 3. Seed a demo knowledge base

Instead of clicking through the UI, you can hand the pipeline a ready-made document and get a
traced answer back in one command (the server must already be running):

```bash
uv run python scripts/seed_demo.py
```

## 4. Tests

Every file under `tests/` is runnable as a plain script, so pytest is optional:

```bash
# Full suite (needs a running server/Ollama for a few files)
uv run pytest tests/ -q

# Offline subset — no Ollama, no server, safe for CI (same list as .github/workflows/ci.yml)
uv run pytest tests/test_boundary_detector.py tests/test_query_rewrite.py tests/test_retriever.py \
       tests/test_regression.py tests/test_llm_citations.py tests/test_doc_analyzer.py \
       tests/test_abtest_integration.py tests/test_abtest_rules.py tests/test_async_tasks_kb.py \
       tests/test_i18n_consistency.py tests/test_http_encoding.py tests/test_parser.py \
       tests/test_path_safety.py -q

# i18n consistency linter (exits non-zero on missing/mismatched keys)
uv run python tools/check_i18n.py
```

`test_api.py`, `test_bm25_simple.py` and `test_hybrid_search_demo.py` need a running Ollama
instance (they embed real text), so they are excluded from the offline subset above and from CI.
`test_http_encoding.py` and `test_i18n_consistency.py` are in that subset precisely because they
need neither a server nor Ollama — running them in the full suite alongside the rest is fine.

## 5. Code conventions

- **Comments and log messages in English** (user-facing strings go through the i18n layer instead).
- Keep the existing module layout: HTTP layer in `api/`, RAG logic in `core/`, cross-cutting
  services in `service/`, configuration in `config/`.
- Route every user-controlled path segment (multipart filename, id from a URL or query string)
  through `service/path_safety.safe_join()` / `sanitize_filename()` — never join raw request
  values onto a directory.
- **Never hardcode secrets.** Read them from environment variables via `config/__init__.py`
  (declare them in `.env.example` as well).
- The API has **no authentication layer**: do not add endpoints that assume an authenticated
  caller, and keep `API_KEY` documented as reserved until the middleware exists.
- All user-facing text must exist in both `zh-CN` and `en-US` in `service/i18n.py`; run
  `python tools/check_i18n.py` after touching translations.
- Prefer editing YAML (`config/settings.yaml`, `config/scenarios/*.yaml`) over adding new magic
  numbers in code.
- **Dependencies**: declare runtime packages in `[project].dependencies` and tooling in
  `[dependency-groups].dev` (`uv add <pkg>` does both), then commit the regenerated `uv.lock`. The
  lockfile is the single source of truth — do not add `requirements*.txt` files back, and never
  install into the system interpreter.
- Never let two distributions provide the same import name (see the platform-exclusive
  `python-magic` / `python-magic-bin` markers): the second install silently overwrites the first.
- Line length ≤ 127 characters. The CI lint step fails on syntax errors and undefined names
  (`flake8 --select=E9,F63,F7,F82 --extend-exclude=...`). The exclusion is passed on the command
  line because `.flake8` is gitignored — but it matters: uv creates `.venv/` inside the repository
  and flake8's built-in exclude list does not cover that name. Recreate the file locally (it stays
  untracked) if you want the same behaviour when running flake8 by hand:

  ```ini
  [flake8]
  extend-exclude = .venv,venv,env,ENV,.git,storage,node_modules
  max-line-length = 127
  ```

## 6. Pull request checklist

- [ ] `python tools/check_i18n.py` passes
- [ ] The offline pytest subset (section 4) passes
- [ ] Any new user-controlled path goes through `service/path_safety.safe_join()`
- [ ] New behaviour is covered by a test under `tests/`
- [ ] Documentation updated (`README.md` + `README_ZH.md` stay in sync; deep architecture notes go
      to `docs/ARCHITECTURE_ZH.md`)
- [ ] No secrets, no new files under `storage/` (it is mounted/ignored on purpose)

## 7. Branding and repository metadata

One product name, one place to change it:

- The product name is **whiteBoxRAG** everywhere: UI, startup banner, logs, docs, Docker project and
  container names.
- **"RAG debugger" / "RAG 调试台" is a positioning term, not a second brand.** It belongs in
  subtitles, the GitHub description, topics and the README tagline — never as an alternative name.
  Earlier drafts used `RAG Trace & Debugger`, `Junsu - RAG Debugger System` and `YY LLM OPS` in the
  English UI; those have been removed, please do not reintroduce one.
- Single sources of truth: `config/settings.yaml` → `system.app_name` (FastAPI title),
  `static/i18n.js` → `nav.title` / `nav.subtitle` (both `zh-CN` and `en-US`),
  `deploy/gunicorn.conf.py` → `proc_name`, `docker-compose.yml` → `name` / `container_name`.
- Run `python tools/check_i18n.py` after touching translations and keep the two locales in sync.

### Suggested GitHub repository metadata

Description (paste into the repository's "About" field — it is indexed by GitHub search and by Google):

> White-box RAG system with a built-in RAG debugger — sentence-level citation tracing, recall
> root-cause diagnosis, evaluation metrics and A/B-tested hybrid retrieval. Fully local on 8 GB RAM
> (Ollama + ChromaDB + LlamaIndex), no Redis or MySQL required.

Topics (GitHub allows up to 20; the first block is the search-relevant core):

`rag` · `rag-debugger` · `rag-observability` · `rag-evaluation` · `retrieval-augmented-generation` ·
`llmops` · `explainable-ai` · `sentence-citations` · `hybrid-search` · `bm25` · `ollama` ·
`chromadb` · `llamaindex` · `fastapi` · `local-first` · `knowledge-base` · `python` · `docker`

The repository slug (`yiyang-aistack/whiteboxRAG`) lives in one place per file — update all of them if
the project ever moves: `pyproject.toml` → `[project.urls]`, the two link definitions at the bottom of
`CHANGELOG.md`, the CI badge at the top of `README.md`, and the `clone` URL in this file.

## 8. Reporting bugs

When reporting a problem, please include:

1. The failing request or UI action, with the `trace_id` if the answer was generated by the app.
2. Relevant lines from `storage/logs/app.log` (keep `storage/logs/error.log` in mind too).
3. Your OS, Python version, Ollama version and the model names in use.
