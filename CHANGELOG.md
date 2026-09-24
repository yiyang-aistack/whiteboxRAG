# Changelog

All notable changes to whiteBoxRAG are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **The front-end comments are English only**: every Chinese comment left in `static/css/*.css`
  and `static/js/*.js` (file headers, section banners, inline notes) was translated, so the files
  split out of the three pages read consistently. Runtime strings are untouched: the
  `console.error` / `showToast` messages, the rendered labels and the regexes that still have to
  match Chinese backend output keep their Chinese text, since those are data rather than comments.
- **The three static pages are markup only now**: `index.html` 6.1k -> 0.9k lines,
  `ab_test.html` 878 -> 150, `admin.html` 816 -> 204. Every inline `<script>` block moved to
  `static/js/*.js` (one file per feature area - `core`, `kb`, `chat`, `provenance`,
  `diagnostics`, `simulation`, `source-panel`, `trace-view`, `monitor`, `ui`, `abtest`,
  `history` for the chat UI, `ab_test-*` / `admin-*` for the other two pages) and every inline
  `<style>` block to `static/css/index.css`, `ab_test.css` and `admin.css`. The files are still
  plain `<script src>` / `<link rel="stylesheet">` references loaded in dependency order and
  still define globals, so the pages' inline handlers (`onclick="..."`) and the rendered pages
  are unchanged. The theme snippet that had been copy-pasted into all three pages is now the
  single shared `static/js/theme.js`. The split is pinned by the static checks:
  `tools/check_i18n.py` scans `static/js/*.js` as well, and
  `tests/test_retrieval_defaults.py` checks a page together with the local scripts it loads, so
  a moved file can neither hide an i18n key nor drop the retrieval-defaults call.
- **Evaluation rubric v2** (`core/evaluator.py`, `evaluation:` in `config/settings.yaml`): the
  overall score is now a weighted mean of per-metric **credits** anchored at the pass line
  (`value == target` earns exactly `pass_score`, 0.7; the ideal value earns 1.0) instead of an
  unweighted mean of raw `value / target` ratios. Consequences:
  * a metric that merely reached its pass line no longer earns a full 1.0. Over
    `storage/traces` this removes the saturation that pinned three metrics at 1.0 for almost
    every answer (`answer_faithfulness` 61/67, `retrieval_recall` 57/67,
    `answer_relevance` 42/67);
  * the negative metrics (`hallucination_rate`, `retrieval_score_std`) no longer score 0 *at*
    their pass line, which had put two opposite conventions in one table;
  * metrics carry configurable weights (grounding 0.45, relevance 0.15, safety 0.20,
    retrieval 0.15, format 0.05) and whatever subset is scored is renormalized to 1.0.
- **`is_passing` is the same rubric as `overall_score`**: the verdict is the score plus named
  gates (`score`, `weight_coverage`, `not_empty_response`, `hallucination_within_limit`,
  `retrieved_context`). The old "70% of the metrics passed" rule could disagree with the number
  printed next to it -- 9 of the 72 stored traces read `8x 分 · 不通过`.
- `retrieval_recall` is renamed to `retrieval_score_avg`: the value always was the mean fused
  retrieval score, while the API, the architecture doc and the UI called it a recall rate.
- `context_usage_ratio` is replaced by `citation_coverage` (share of sentences carrying a
  resolvable `[Document N]` marker). The old metric divided by the token set of the whole
  context, so it measured answer length (observed mean 0.23 against a 0.3 pass line) and
  penalised concise, correct answers.
- `rejection_accuracy` is only measured on out-of-domain questions; on an in-domain question it
  reports "not applicable" and its weight is redistributed. It used to fall back to
  `answer_faithfulness`, i.e. the same number twice (identical in 67/67 sampled traces).
- `empty_response` recognises refusals in both languages, the scenario's own
  `retriever.empty_response` text and reworded/shortened refusals (the phrase list was eight
  English substrings), and only counts one when it dominates the answer.
- Pass lines are calibrated (`evaluation.target_values`; harness `scripts/evaluator_recalc.py`).
  Over the stored corpus the distribution moves from median 0.870 / 47% >= 0.90 to median
  0.729 / 0% >= 0.90: the number discriminates again instead of sitting on a ceiling.
- A/B comparison adds rubric-independent winners (`best_by_grounding`,
  `best_by_citation_coverage`), a `score_comparable` flag and `rubric_version`, because
  `overall_score` is only comparable inside one scenario and one rubric.
- Scenario `evaluation.metrics` is now a real allowlist (gate metrics are always evaluated,
  unknown names are logged and ignored), and `evaluation.target_values` from settings.yaml is
  actually read -- the evaluator used to hardcode its own defaults and ignore the file.

### Added
- `scripts/evaluator_recalc.py`: offline recalibration harness. It re-scores the traces in
  `storage/traces` with the current rubric code (no embeddings, no LLM) and prints per-metric
  pass rates, percentiles, saturations and the before/after score distribution.
- `tests/test_evaluator.py`: the evaluator had no test at all. It pins the credit anchors
  (pass line == `pass_score` in both directions, for every registered metric), the fail-closed
  rule for unmeasurable metrics, weight redistribution and `weight_coverage`, the named gates,
  bilingual refusal detection and the scenario allowlist. It is part of the CI test subset.
- `rubric_version` in every evaluation result and in `GET /api/evaluation/metrics`: a score is
  never compared against a score from another rubric, and both now live in `storage/traces`.
- The evaluation badge in the chat UI explains the score (rubric version, score before the
  adjustments, failed gates, quality flags) and the new/legacy metric keys are labelled.

### Fixed
- **The A/B test page answered in the wrong language.** `static/ab_test.html`'s scripts called
  `/api/chat/abtest`, `/api/chat/abtest/batch` (plus the knowledge-base and retrieval-defaults
  lookups) with bare `fetch()` and told the backend nothing about the UI language, while the
  answer language comes from `service/i18n.py::get_lang_from_request()` - the `?lang=` query
  parameter, then `Accept-Language`, then the configured default - and selects the system prompt.
  An English session therefore saw English labels around Chinese answers, while the chat UI
  (`static/js/core.js::fetchJSON`) and the A/B dialog inside `index.html` did pass the language.
  Every request now goes through the new `apiFetch()` helper (`static/js/ab_test-core.js`), which
  sends `Accept-Language` *and* the higher-priority `?lang=` of `i18n.getLang()`, and the
  `index.html` dialog carries `?lang=` explicitly too. The value must be the dictionary code
  (`en-US`), never the button label (`EN`): an unknown value is silently ignored, which would look
  like a fix while leaving answers in the default language. Pinned by
  `tests/test_i18n_consistency.py::test_ab_test_page_sends_the_ui_language_to_the_backend` and its
  siblings.
- **A restart made the first query claim the knowledge base had nothing.** The BM25 index is
  built lazily, so the request that triggered the build merged with `bm25_results = []` while its
  own index finished ~20 ms too late (`storage/logs/app.log`, 22:11:17: *BM25 retrieval completed,
  hit 0 documents* -> *BM25 index build completed ... with 33 documents* -> *Threshold filter
  completed, finally keep 0*; the identical second query kept 6). Because fusion reserves the BM25
  weight, that capped every candidate at the *vector* weight (0.4) while `similarity_threshold`
  was 0.5: with `bm25_weight 0.6` and `similarity_threshold 0.5` an empty BM25 route is
  unanswerable by construction, and 9 of the 87 stored traces (all with `bm25_status: building`)
  were exactly that. Two new switches fix it:
  * `bm25.build_wait_ms` (500) - a retrieval waits a bounded time for the in-flight build (a
    33-chunk rebuild takes ~20 ms warm, ~400 ms on the first query of a cold process) instead of
    answering from a single route, and the build thread handle is released when the build ends, so
    a finished build is never joined again. The build now reads its texts through the new
    `VectorStoreManager.get_documents()`: `get_collection()` attaches the configured embedding
    function, which cost 5.1 s (whole model load) on this path - while BM25 only needs texts - and
    no bounded wait could have covered the first query;
  * `retriever.reroute_on_empty_route` (true) - a route that returns nothing has its fusion weight
    redistributed onto the route that did answer, for that query only. `debug_info.route_degraded`,
    `debug_info.effective_weights` and the retrieval funnel report it, and the hit-reason
    attribution explains the score with the weights it was actually computed with.
  `validate_retrieval_weights()` warns at startup when a weight is below the similarity threshold
  (i.e. when it makes the other route mandatory), and `tests/test_retrieval_degradation.py` pins
  both the wait and the reroute, including the counterfactual (`0.4 * 0.667 < 0.5` -> 0 results).
- **The hybrid-answer disclaimer was shown twice.** Hybrid mode injects the two opening lines and
  the prompt asks the model to open with those same lines, so the model echoed them (trace
  02aa342b carries the prefix twice, with different line breaks). `_ensure_hybrid_format()` could
  not see the duplicate: its substring check matched the copy the pipeline itself had written and
  returned early. The duplicate is now dropped *while streaming* (`_LeadingDuplicateFilter`), which
  also keeps the streamed sentence offsets valid, and `_collapse_hybrid_disclaimer()` collapses a
  finished answer that already holds two copies - whitespace-insensitively, since the echo never
  matches byte for byte. Pinned by `tests/test_hybrid_disclaimer_dedupe.py` and by a new case in
  `tests/test_streaming_trace_offsets.py`.
- An embedding outage scored 0.0 hallucinations, i.e. a *perfect* safety score for an answer
  nobody could check; the metric now fails closed and is reported as an error.
- `retrieval_score_std` returned 0.0 for a single retrieved chunk, which the old formula turned
  into a full score ("perfect consistency" for retrieving one chunk); it is now not applicable.
- The low-retrieval cap now follows `retriever.similarity_threshold` instead of ignoring it:
  gate = `similarity_threshold * evaluation.retrieval_gate_ratio` (0.15 by default, as before).
- The two score adjustments no longer overwrite each other's reason (they shared one string
  field), and `raw_score` / `adjustments` make a capped score attributable.
- `docs/ARCHITECTURE_ZH.md` no longer describes `retrieval_recall` as
  `context_count / expected_count`, and `GET /api/evaluation/metrics` documents all ten metrics
  with the implementation each one actually has.

### Added
- `service/text_split.py` — one sentence splitter with character offsets, shared by the sentence
  tracer, the evaluation metrics, context compression and the streaming pipeline. It is aware of
  decimals and versions (`0.4`, `v1.2.3`, `docs/api.md`), English abbreviations (`Dr.`, `et al.`),
  ellipsis, closing quotes, `[文档N]` markers, fenced/inline code and Markdown lists. Four
  different regexes used to split on a bare `.`, so "the BM25 weight is 0.4 and the vector weight
  is 0.6." became three "sentences" — which is why per-sentence verdicts were attached to
  fragments of numbers and the demo printed `sentences=3` for a one-sentence answer.
- Sentence provenance now reports **character offsets** (`start`/`end`/`index`) for every sentence,
  and the streaming `sentence_provenance` event carries `sentence_start` / `sentence_end` /
  `sentence` as well. The chat UI attributes a badge by offset instead of re-splitting the answer
  with its own regex and hoping that two sentence-index spaces stay aligned.
- Sentence provenance is **citation-first**: a sentence whose `[文档N]` marker resolves to a chunk
  that was really in the prompt is attributed to that chunk and labelled `citation_verified`
  (`attribution='citation'`) — even when the similarity heuristic would have picked a different
  chunk. Sentences without a usable marker keep the similarity verdict
  (`attribution='similarity'`), now computed from absolute floors plus the sentence's rank inside
  this answer's own scores; every sentence also carries that similarity-only verdict in
  `similarity_level`, so both readings stay visible.
- New verdict `unverified` (with `tracing_error=True`) for sentences the tracer could not evaluate
  at all: an embedding backend failure is no longer reported as `no_source` and is excluded from
  `drift_count` / `drift_rate` (see `verified_drift_rate`, `unverified_count`,
  `citation_verified_count` in `analyze_drift`). The UI paints these neutral grey, "tracing
  unavailable", instead of red "possible hallucination".
- `core/contradiction.py` — the contradiction check rewritten as a structured comparison and wired
  into the normal Q&A path. Claims (a number plus its unit, or a polarity phrase) are extracted from
  both the answer and the chunks that were actually placed in the prompt, units are normalised
  (7 天 == 168 小时, 1 万元 == 10000 元; currencies are never mixed) and a mismatch is only reported
  when no in-scope source claim can reconcile it. Thresholds live in the new `contradiction:` block
  of settings.yaml (`relative_tolerance` 0.2, so 0.4 vs 0.6 is caught; `percent_point_tolerance` 5;
  `date_tolerance_days` 1; `require_shared_topic`, which keeps unrelated numbers of one chunk from
  "contradicting" each other). Findings carry the answer sentence offsets, the source chunk id, the
  source sentence and the source snippet offsets, are attached to the matching trace sentence
  (`has_contradiction`), are stored with the trace record, are streamed as a `contradictions` SSE
  event, and are returned by `/api/chat/simulate` — where dropping a chunk is exactly what can turn
  a supported answer into a contradicted one.
- `tests/test_contradiction.py` (26 offline tests) — decimal-place errors, unit normalisation,
  percentage points, dates, polarity phrases, topic scope, settings-driven thresholds, output
  offsets and the trace-flagging helper. Added to the CI offline subset, and
  `tests/test_streaming_trace_offsets.py` gained an end-to-end case proving a sentence can be a
  direct quote *and* flagged as contradicting the source.
- `tests/test_text_split.py`, `tests/test_sentence_tracing.py` and
  `tests/test_streaming_trace_offsets.py` (38 offline tests): splitter edge cases, the
  citation/similarity attribution contract, floors + relative rank, the unverified failure
  state, offsets slicing back to the sentence, streaming and batch agreeing on the same
  sentence, `LLMPipeline.query_stream` end to end through a stub adapter, and a guard
  against duplicate method definitions coming back. All three files join the CI offline
  subset.
- `GET /api/chat/retrieval-defaults` — serves the hybrid retrieval defaults straight from the
  `retriever:` block of `config/settings.yaml` (`mode`, `bm25_weight`, derived `vector_weight`,
  `similarity_threshold`, `top_k`, `rerank_top_k` and the query-rewrite / rerank switches).
  The web UI reads its variant defaults from here, and the endpoint is independent of the LLM
  pipeline on purpose: defaults must be displayable while the model backend is down, which is
  exactly when `/api/chat/health` cannot answer. `check_health` now reports the same payload
  (built by `LLMPipeline._collect_retrieval_defaults`), so both paths agree.
- `config/settings.yaml`: the `retriever:` block is now the documented single source of truth for
  hybrid retrieval defaults — `top_k`, `similarity_threshold`, `query_rewrite_enabled` and
  `rerank_enabled` were added (previously hardcoded in the retriever / the UI, or borrowed from
  `vector_store.*`). `vector_store.top_k` / `vector_store.similarity_threshold` stay as the
  component-level values the hybrid retriever falls back to, and `core/retriever.py` reads the
  whole block through the new `get_retrieval_defaults()` accessor (`HybridRetriever` uses it in
  `__init__`, so the running retriever and the UI can no longer disagree). Scenario overrides keep
  working: an explicit scenario `retriever.top_k` / `retriever.similarity_threshold` wins, then the
  legacy scenario `vector_store.*` value, then the global default.
- `tests/test_retrieval_defaults.py` — pins the contract: every UI default is declared in
  settings.yaml, the retriever / accessor / endpoint agree, scenario overrides still win, and the
  two static pages contain no hardcoded retrieval value (a scan rejects `value="0.4"`-style
  literals on the `bm25_weight` / `similarity_threshold` / `top_k` inputs). Added to the CI
  offline subset.
- `POST /api/chat/miss-scan` — on-demand full knowledge-base scan for relevant chunks that were
  never recalled. The regular chat flow skips this scan (it re-embeds every candidate and would
  double retrieval latency), which is why traces report "missed documents scan skipped"; this
  endpoint runs it when the operator explicitly asks for it, reusing the same retrieval result and
  similarity threshold as the question under investigation, and returns the near-threshold chunks
  with a structured root cause (`threshold_edge` / `keyword_missing` / `embedding_mismatch` /
  `partial_match`). Exposed in the UI as a button inside the retrieval funnel, with localized root
  causes and per-chunk similarity/threshold/keyword-overlap numbers.
- **Hypothesis analysis in the UI** (`POST /api/chat/simulate`): the funnel now offers "drop a
  chunk" — pick which evidence chunks stay in the prompt, regenerate the answer, and compare the
  original against the simulated one side by side (context count, sentence count, drift rate, and
  per-sentence provenance badges), with a plain-language conclusion such as "drift did not rise, so
  these chunks contributed little here". Chunk text is fetched in full before simulating, so the
  comparison is not skewed by the truncated streamed preview.
- `HybridRetriever.get_effective_params()` — public accessor for the effective scenario retrieval
  parameters, so the API layer reuses the exact threshold/top_k of a real query.
- `tests/test_chat_debug_endpoints.py` — offline tests for both debug endpoints (adapter used for
  regeneration, excluded chunks removed before prompting, localized fallback when the model is
  down, full scan forced with `debug=True`, retrieved ids excluded from the scan, 404 on unknown
  KB, 503 without a pipeline).

### Changed
- `static/index.html` and `static/ab_test.html` no longer hardcode retrieval defaults. Both pages
  fetch `/api/chat/retrieval-defaults` and fill the A/B variant inputs (BM25 weight, similarity
  threshold, `top_k`) from it — variant A defaults to the configured BM25 weight, variant B to the
  derived vector weight — and the previously hardcoded `query_rewrite_enabled: true` /
  `rerank_enabled: true` in the request payload now follow the config too (unset fields are sent as
  `null`, i.e. "do not override the backend default"). Empty/invalid inputs are parsed as `null`
  instead of `0`, so a legitimate `0` weight or threshold is no longer dropped.
- `config/settings.yaml`'s `sentence_tracing:` block now lists the keys the tracer actually reads
  (`abs_drift_floor`, `abs_summary_floor`, `abs_direct_quote_floor`, `fine_weight`,
  `coarse_weight`, `summary_percentile`, `direct_quote_percentile`, `min_adaptive_samples`). It
  used to document `drift_threshold` / `direct_quote_threshold` / `summary_threshold`, which no
  code read: the knobs were inert while the comment claimed they were "read by
  `core/sentence_tracing.py`".
- `README.md` / `README_ZH.md` / `docs/ARCHITECTURE_ZH.md` describe the tracing the way it now
  behaves: citation verification as evidence, similarity as an explicitly labelled heuristic, the
  `unverified` failure state, and the demo snippet that used to show `sentences=3` for a
  one-sentence answer.
- `SentenceTracer.detect_contradictions()` is now a thin, documented delegate to
  `core/contradiction.py`: the ~240 lines of substring heuristics (Chinese-only keyword pairs,
  hard-coded thresholds, `root_cause_zh` fields holding English text) are gone, while the method
  keeps its historical return shape so existing callers keep working. The A/B endpoint re-uses the
  findings the pipeline already produced instead of running a second pass over the same answer, and
  `LLMPipeline.query()` / `query_stream()` report `contradictions` / `contradiction_count`.

### Fixed
- `POST /api/chat/simulate` was dead: it called `_get_ollama_client()`, a helper that no longer
  exists on the pipeline, so every hypothesis run returned the "model unavailable" text instead of
  a regenerated answer. It now goes through the shared LLM adapter, reports `duration`, and its
  response carries `excluded_count` so callers can tell what was actually dropped.
- `core/sentence_tracing.py` carried three identical copies of `trace_single` and three of
  `build_chunk_index`. Python keeps the last definition, so the first two were dead code that
  could be edited with no effect — while `_single_sentence_trace()` in `core/llm_pipeline.py`
  claimed to delegate to "one primitive". There is now exactly one definition of each, both
  tracers share `_classify_sentence`, and a test fails if a duplicate returns.
- The per-sentence `citations` field could never be populated: `[文档1]` was compared against the
  retrieved chunks' Chroma ids, which are `uuid4()` values. Markers are now resolved through the
  prompt's document ordinals (`_build_citation_map`), the same numbering the UI turns into
  clickable chips.
- Streaming provenance no longer "skips the first sentence": the injected disclaimer is two
  sentences long, so the boolean skip dropped the first real answer sentence and shifted every
  live badge. Skipping is now decided by the sentence's character offset.
- The splitter is now also correct on *partial* text, which is what the streaming loop feeds it:
  a trailing terminator ("...is 0." while the next chunk still holds ".4") and a terminator
  followed by an unfinished citation marker ("...退款。[文") no longer close a sentence. Both
  bugs were caught by the new end-to-end streaming test, and both produced live badges attached
  to a sentence the final trace described differently.
- `_ensure_hybrid_format()` prepended a second copy of the knowledge-base disclaimer to hybrid-mode
  answers whose wording is not English: the "already contains disclaimer" check only looked for
  English phrases while the injected prefix is localized. It now checks the localized wording
  first, which also stops the prefix from shifting the offsets the incremental trace reported.
- The `drifted_count` key read by the sentence-tracing log line did not exist in
  `analyze_drift()`'s output (which returns `drift_count`), so the log always printed 0. The alias
  is now provided and the log uses the canonical key.
- `core/evaluator.py` and `core/retriever.py` (context compression) used their own sentence
  regexes — one of them splitting on a bare `.` — so `hallucination_rate`, `semantic_consistency`
  and the compressed chunk text disagreed with the trace about how many sentences an answer has.
  All three now use `service/text_split.py`.
- The contradiction check could not see the errors it existed for: a percentage had to differ by more
  than 10 points and a count or amount by a factor of 2, so "the document says 0.4, the answer says
  0.6" (ratio 1.5) was never reported; the patterns only understood Chinese keyword pairs; and it
  never ran on the normal Q&A path (only the A/B endpoint called it), so no trace could show it.
- Citation markers are no longer read as values: `[文档1]` used to be extracted as the number 1,
  which could then "contradict" any number of the source.
- The retrieval-trace panel was only half translated. Metric names and their tooltips, the
  business-summary wording (match conclusion, one-line summary, LLM-supplement / risk labels,
  optimization tips), the recall-diagnosis and sentence-tracing section labels, the source-list
  headings, the funnel/simulation hints and the history / intent-correction dialogues were
  hardcoded Chinese strings built inside `static/index.html`, so an English session still showed
  Chinese text there. They now resolve through the frontend dictionary: `TRACE_TERM_MAP` carries
  i18n keys instead of Chinese text (name = `<key>`, tooltip = `<key>.desc`) and the new
  `trace.*`, `boundary.*`, `funnel.*`, `history.*` and `intent_feedback.*` entries are defined for
  both languages. Mixed-language leftovers were fixed at the same time: the boundary-rejection
  card mapped its English reason to Chinese regardless of the UI language, `scoreBadge`'s tooltip
  and the file-count label ignored the current language, timestamps were always formatted with
  the `zh-CN` locale (and the file manager's counter span was destroyed by `data-i18n`, breaking
  `renderFileList`), and `clearChat` / `resetTracePanelDom` rendered two different placeholder
  texts for the same panel.
- `core/retriever.py` no longer injects the Chinese placeholder `未知` into `debug_info` file
  names (the UI localizes a missing name itself), `api/routes/knowledge.py`'s binary-file preview
  notice is translated (`kb.binary_preview`), and the clarifying-question template in
  `core/query_rewriter.py` no longer mixes `和` into an English sentence.

### Added
- `tools/check_i18n.py` — the i18n lint now also validates the frontend dictionary in
  `static/i18n.js`, not just `service/i18n.py`: identical key sets for zh-CN / en-US, identical
  `{placeholders}`, no Han characters in any `en-US` value, and every literal key referenced from
  `static/*.html` / `static/*.js` (`t('key')`, `data-i18n="key"`, `data-i18n-title`, ...) is
  defined. `tests/test_i18n_consistency.py` shares those helpers and includes a test that the
  guard itself fails on a missing key, a placeholder mismatch and a Chinese leak — the
  half-translated state it protects against was previously invisible because `i18n.t()` falls back
  to the `zh-CN` value.
- **Answer-level provenance in the chat UI**: each sentence of an answer is tinted by its
  confidence level (`direct_quote` / `summary` / `low_confidence` / `drift`) with a badge on the
  risky sentences, and every `[文档N]` citation marker becomes a clickable chip that scrolls to and
  highlights the matching source card (`renderAnswerWithProvenance()` in `static/index.html`).
  Sentence splitting mirrors `core/sentence_tracing.py::_split_sentences` exactly, including the
  hybrid-answer disclaimer offset, so badges can never drift by a sentence.
- **Retrieval funnel (hit vs. missed)** — `core/retriever.py::_build_retrieval_funnel()` now records,
  for every retrieved candidate, whether it reached the prompt and otherwise which stage dropped it
  (`threshold` / `compression` / `top_k`), with the score gap or rank needed to explain it. The
  streamed `retrieval` event carries it as `funnel`, plus `results_total` and a
  `skipped.potential_misses` flag so an empty miss list is never mistaken for "nothing was missed".
  The UI renders a stage strip, a kept list and a dropped list with a concrete tuning suggestion.
- Per-message trace snapshots: every completed answer stores its own trace data
  (`state.messageTraces`), so an earlier turn can be inspected without losing the latest one
  ("查看本次溯源" on each answer, "回到最新" in the panel banner).
- `business_diagnosis` and `citation_validation` SSE events are now consumed by the UI
  (localized status label + raw diagnosis in the technical view; citation marker check in both
  views). Both were already computed by the backend and previously discarded client-side.
- Answer feedback dialog for `recall_missing` / `answer_wrong` / `citation_issue` / `other`
  (`POST /api/chat/feedback` already accepted these types; only intent correction was reachable).
- `tests/test_retrieval_funnel.py` — stage-attribution tests for the funnel (threshold gap, top_k
  rank, compression, single-stage attribution, route attribution, missing optional fields).

### Changed
- The streamed `retrieval` event ships every candidate that reached the prompt instead of a
  3-item preview (`core/llm_pipeline.py`). Chunk text is truncated to 500 characters with a
  `text_truncated` flag; the full chunk is fetched on demand from
  `GET /api/knowledge/{kb_id}/chunks/{chunk_id}`, which also fixes "查看支撑原文" for chunks that
  were not in the visible list.
- Trace panel is no longer invisible by default: it auto-opens on the first retrieval of a query
  (respecting a manual close) and the toolbar button shows a `✓kept ✗dropped` badge when collapsed.
- Retrieval counters are consistent across views ("进入回答 N · 候选 M · 淘汰 K") instead of
  mixing `count` with the displayed list length.
- `hit_reasons` and diagnosis tips shown in the business view are localized from the structured
  `type`/`value` fields instead of echoing the backend's English `explanation`.

### Security
- Added `service/path_safety.py` (`sanitize_filename()` / `safe_join()`) and routed every
  user-controlled path segment through it. This fixes an unauthenticated arbitrary file read in
  `GET /api/document/download/optimized`, an arbitrary `*.json` read in
  `GET /api/evaluation/report/{report_id}` (the id reached `report_dir / f'{id}.json'` verbatim, so
  `../kb_metadata` returned the file), arbitrary file write/delete through upload
  filenames (multipart `filename` with `../`), an arbitrary file delete in
  `DELETE /api/chat/history/{trace_id}`, and the same class of issue for `trace_id`, `task_id`,
  `logger_name` and the metadata `stored_name`.
- `POST /api/document/optimize` no longer writes wherever the caller asks: the client-supplied
  `output_dir` was used as an absolute path, so a request could create directories and files
  anywhere the process can write. It is now confined to a single sub-directory of the configured
  `document_analyzer.output_dir` root, and the response reports the files actually written rather
  than a name guessed from the request.
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
- `pyproject.toml` + `uv.lock` — the single source of truth for dependencies: runtime packages under
  `[project].dependencies`, pytest/pytest-asyncio/flake8 as the PEP 735 `dev` dependency group, and a
  committed lockfile for byte-identical environments. `.python-version` pins the interpreter (3.12).
- The lint run excludes the uv environment: `.venv/` lives inside the repository and flake8's
  built-in exclude list does not cover that name, so CI passes
  `--extend-exclude=.venv,venv,env,ENV,.git,storage` explicitly. A `.flake8` file with the same
  settings is gitignored and exists for local runs only.
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
- Dependency management migrated from pip + `requirements.txt` / `requirements-dev.txt` to **uv**.
  `uv sync` (runtime + `dev` group), `uv sync --no-dev` (runtime only) and `uv sync --locked`
  (CI/Docker) replace `pip install -r ...`, and the environment moved from `venv/` to uv's default
  `.venv/`. CI installs `astral-sh/setup-uv` pinned to 0.10.11 and runs the suite through `uv run`;
  `deploy/Dockerfile` copies the uv binary and uses `uv sync --locked` with a BuildKit cache mount;
  `deploy/start.sh` uses `uv sync` / `uv run`. Every setup and test command in `README.md`,
  `README_ZH.md`, `CONTRIBUTING.md`, `deploy/DEPLOY.md` and `docs/ARCHITECTURE_ZH.md` was updated.
- Docker documentation clarifies that a container cannot reach the host Ollama via `localhost`
  (use `host.docker.internal` or the compose service name).

### Fixed
- `pyproject.toml` now declares the packages the code imports directly but previously relied on
  transitively, or did not list at all: `pdfplumber`, `numpy`, `requests`, `ollama`, `httpx`,
  `psutil`, `APScheduler`, `scikit-learn`, `chardet`. On a clean install `psutil`, `pdfplumber`
  and `scikit-learn` were missing, which silently disabled system metrics, fell back to the weaker
  pypdf extractor and made the document-relation analysis fail.
- `main.py --install` now installs *before* restarting into the environment. The previous
  implementation created `venv/` and then restarted the launcher without `--install`, so the
  documented one-click command never installed a single dependency; `--install` runs
  `uv sync --locked` first, and `--install --dev` includes the `dev` group.
- `python-magic` and `python-magic-bin` are now platform-exclusive markers. Both distributions ship a
  `magic` package and overwrite each other's `magic/__init__.py`, and the surviving copy decides
  whether libmagic can be loaded: on Windows `magic.from_buffer()` raised
  `ImportError: failed to find libmagic` (reproduced with plain pip as well), which silently
  downgraded MIME validation to the extension check. Windows installs `python-magic-bin` only.
- `deploy/Dockerfile` now installs `libmagic1`: on Linux the lockfile installs `python-magic`, which
  needs the system libmagic, so inside the image MIME validation likewise fell back to the extension
  check. Windows uses the DLL bundled with `python-magic-bin`, so only Linux needs the package.
- `pytest-asyncio` was pinned to `0.23.4`, which declares `pytest<8` and is therefore unsatisfiable
  next to the pinned `pytest==8.0.0`; the pin is `0.23.8` (`pytest<9`).
- `main.py`: `_kill_port_process()` no longer parses `netstat` output as UTF-8 text — the raw bytes
  are decoded with the console code page and `errors="replace"`, so a non-UTF-8 Windows console (or
  `PYTHONUTF8=1` in the launcher's environment) can no longer abort the port cleanup with a
  `UnicodeDecodeError` in a reader thread.
- `tests/test_path_safety.py` uses `pyproject.toml` (instead of the removed `requirements.txt`) as the
  traversal target and asserts that the served body never contains `[project]`.
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
- `core/vector_store.py`: collections are created with an explicit `hnsw:space: cosine` metadata
  entry. ChromaDB defaults to *squared L2*, but `search()` converts distance to a similarity with
  `1 - distance`, which is only meaningful for cosine. Every reported similarity score was therefore
  wrong — for a normalised query the L2 distance is ~1400, so every score clamped to `0.0` and the
  score threshold silently discarded the whole result set. `create_collection` also no longer
  pretends to fail silently: see the embedding-provider entry below.
- `core/vector_store.py`: a failure to build the embedding function no longer falls back to a
  384-dimension ONNX MiniLM model. The fallback was silent, so a mistyped provider or a missing
  optional dependency produced a knowledge base embedded with a different model than configured —
  and the dimension mismatch only surfaced much later, as nonsense recall. Initialisation now raises
  with a message naming the provider and, for `EMBEDDING_PROVIDER=local`, the
  `uv sync --extra local-embedding` command that installs it.
- `core/circuit_breaker.py`: `CircuitBreaker.call()` deadlocked its own thread whenever a
  half-open breaker recovered. The success path holds `self._lock` and then calls
  `self._reset()`, which takes the same non-reentrant `Lock`; the thread blocked forever while
  holding the lock, so every later LLM call through that breaker hung too. The lock is now an
  `RLock` (the manager's own lock stays a `Lock`).
- `core/typo_checker.py`: the single-character correction map (`则`→`这`, `那`→`哪`) applied with
  `str.replace` corrupted far more text than it fixed — `原则`/`否则`/`规则`/`准则` became
  `原这`/`否这`/`规这`/`准这` in the query that was then handed to BM25 and the embedding model. It is
  removed; corrections now come only from the whole phrases in `config/typo_dict.yaml`.
- `core/typo_checker.py`: corrections are applied in a single `re.sub` pass over the original text
  (longest key first) instead of a sequence of `str.replace` calls. Chained replacement re-scanned
  text it had already substituted, so `则么办` became `怎怎么办` — the dictionary contains both
  `则么办→怎么办` and `么办→怎么办`.
- `api/routes/knowledge.py`: `GET /{kb_id}` is registered at the end of the module. Starlette
  resolves routes in registration order and takes the first full match, so the catch-all path
  parameter registered near the top swallowed every later static sibling — `/api/knowledge/synonyms`
  and `/api/knowledge/typos` bound `kb_id="synonyms"` and answered `404`. Any new static `GET` route
  must be added above the catch-all.
- `service/scheduler.py`: scheduled tasks never ran. `init_scheduled_tasks()` registered the jobs
  before `start()`, and a job on a not-yet-started scheduler has no `next_run_time` attribute at all,
  so `job.next_run_time.isoformat()` raised `AttributeError`; the caller in `api/api.py` logs and
  swallows it, leaving the service up with an empty scheduler. The scheduler now starts before the
  jobs are added and every read goes through `getattr`, and both read sites
  (`_registered_tasks` and the `/api/monitor` listing) were fixed.
- `core/llm_pipeline.py`: the out-of-domain (business-scope) rejection path was dead code. It called
  `trace.mark_complete()`, which did not exist, and built a response dict missing the `context`,
  `context_count`, `has_results` and `retrieval_mode` keys — an `AttributeError` and a `KeyError`
  that were both swallowed by the surrounding `except`, so a rejected query fell through to the
  normal path. `RAGTrace.mark_complete()` now exists (it also records a terminal `status`), the
  early-return payload is complete, and both handlers log with `exc_info=True` so a future failure
  here is visible.
- `api/routes/document_optimizer.py` / `docAnalyze.py`: the endpoint reported file names for the
  "suggestions" and "optimized" outputs by guessing the document names, so the response listed files
  that were never written (and missed the ones that were). `generate_optimized_document()` now
  returns the names it actually wrote and the endpoint reports those.
- `config/settings.yaml`: removed a duplicated `default_lang` key with its comment block.
- `.env.example`: the quick-start defaults could not start the service — `EMBEDDING_PROVIDER=local`
  was set while `LOCAL_EMBEDDING_MODEL_PATH` / `LOCAL_EMBEDDING_DIM` were commented out, so the
  startup check in `main.py` aborted. The default is now `ollama` (matching the README quick start,
  `ollama pull nomic-embed-text`), the per-provider variables are grouped and commented so only the
  active provider's are live, and the `AUTHOR` entry is spelled in the upper case the loader actually
  reads.
- `README.md` / `README_ZH.md`: both quick starts now begin with `cp .env.example .env`. The service
  exits when `.env` is missing, so the previous four-line "Try it in 30 seconds" block failed on a
  fresh clone.
- `api/routes/knowledge.py`: `kb_metadata.json` is now written atomically (temp file + `os.replace`)
  and every read-modify-write is serialised by a lock. The old `open(..., 'w')` truncated the real
  file before writing, so an interrupted or concurrent write left invalid JSON behind,
  `_load_metadata()` fell back to `{}` and every knowledge base disappeared at once; a create
  racing an upload also silently dropped one of the two updates. A failed write now raises instead
  of logging and reporting success, and `create_knowledge_base` rolls back the collection and
  directory it built if the registry entry cannot be written.
- `api/routes/knowledge.py`: document upload and update stream the request body to disk in 1 MB
  chunks and abort the transfer as soon as `document_parser.max_file_size` is crossed. `await
  file.read()` buffered the entire body before the size check could run, so one large upload could
  exhaust memory; the magic-number check now inspects the first 64 KB of what was streamed. A
  rejected or failed upload no longer leaves a partial file behind.
- `api/routes/chat.py`: `GET /api/chat/health` returns `{success, data, message}` on the failure
  path too, instead of `{success, healthy, error}`. Nothing reads `healthy`/`error`, so when Ollama
  was down the endpoint answered in a shape no client understood — and that is exactly the case the
  endpoint exists to report. `tests/test_api.py::test_chat_health` was failing on it.
- `core/trace.py`: `TraceManager.save_trace()` removes the trace from `_active_traces`. Nothing ever
  popped the entry, and each one holds the retrieved context and the full answer, so a long-running
  process leaked one trace per query. `load_trace()` also restores the terminal `status` it had
  been dropping, and `import os` moved from the bottom of the module to the top.
- `core/sentence_tracing.py` / `core/evaluator.py`: sentence-level tracing and the
  semantic-consistency / hallucination metrics embed through the vector store's embedding function
  instead of the LLM provider's adapter. They combined the LLM provider's adapter with the
  *embedding* provider's model name, so any mixed configuration (`EMBEDDING_PROVIDER` set
  independently of `LLM_PROVIDER`) sent one backend a name it had never seen — `nomic-embed-text` to
  the OpenAI API, or a local directory path to Ollama — and every similarity came back as 0. Both
  now compare vectors produced by the same model as the index. `config.get_llm_base_url()` and
  `LLMAdapterFactory.get_adapter()` take the provider into account for the same reason, and
  `VectorStoreManager.embed_texts()` exposes the shared embedder.
- `api/routes/chat.py`: A/B test results are written to `abtest.result_directory`
  (`./storage/abtest_results`) instead of `async_tasks.task_directory`. The task cleanup job deletes
  every `*.json` under the latter once it is older than `timeout * 3`, so saved A/B results vanished
  about 15 minutes after being written. Added to `.gitignore` and to the startup directory list.
- `service/scheduler.py` (see above) and `docAnalyze.load_feedback_history()`: the latter reads
  `rule_engine.feedback_file` instead of a hardcoded path, so changing where feedback is stored no
  longer silently yields an empty history.
- `tests/test_hybrid_search_demo.py`: added the missing `retriever` and `kb_id` fixtures. Every test
  in the file errored during setup with `fixture 'retriever' not found`, so the file had never run;
  it now passes (and stays out of the CI offline subset, since it embeds real text). Also removed
  the stray `return retriever` statements that pytest warned will become errors.
- `CONTRIBUTING.md`: the paragraph after the offline-subset command listed `test_http_encoding.py`
  and `test_i18n_consistency.py` as needing a live server, contradicting the command itself and CI,
  which both include them precisely because they do not.
- `config/settings.yaml`: added the `sentence_tracing` and `evaluation` sections. Seven settings the
  code reads (`sentence_tracing.drift_threshold`, `.direct_quote_threshold`, `.summary_threshold`,
  `evaluation.enabled`, `evaluation.report_directory`, …) had no entry in the file at all, so they
  silently ran on hardcoded defaults that nothing documented.

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

[Unreleased]: https://github.com/yiyang-aistack/whiteboxRAG/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/yiyang-aistack/whiteboxRAG/releases/tag/v1.0.0
