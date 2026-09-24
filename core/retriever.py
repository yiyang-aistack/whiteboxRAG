"""
Hybrid Retrieval Module
Supports BM25 keyword retrieval + vector semantic retrieval + reranking + context compression
Supports scenario-based parameter config
Supports Query Rewrite
Supports hit attribution (hit_reasons) - provides business-oriented reasons for each recalled chunk
"""
import re
import threading
from typing import Callable, Dict, List, Optional

import jieba
from rank_bm25 import BM25Okapi

from config import config, scenario_config
from core.query_rewriter import QueryRewriter
from service.logger import get_logger
from service.i18n import _

logger = get_logger('retriever')


def _first_defined(*values):
    """
    Return the first value that is not None.

    Unlike `a or b`, this keeps legitimate falsy values (0, 0.0, False) instead of
    skipping over them, which matters for weights and thresholds.
    """
    for value in values:
        if value is not None:
            return value
    return None


def get_retrieval_defaults() -> Dict:
    """
    Default hybrid retrieval parameters, read straight from config/settings.yaml.

    The `retriever:` block is the single source of truth: this is what `HybridRetriever`
    loads at startup, what `GET /api/chat/retrieval-defaults` serves, and therefore what
    the web UI (index.html / ab_test.html) shows as the default of its retrieval inputs.
    `vector_store.*` is only a fallback for configs written before `retriever.top_k` /
    `retriever.similarity_threshold` existed.

    Returns:
        Parameter dictionary (mode, bm25_weight, derived vector_weight, similarity
        threshold, top_k, rerank settings, stage switches)
    """
    bm25_weight = config.get('retriever.bm25_weight', 0.4)
    return {
        'mode': config.get('retriever.mode', 'hybrid'),
        'bm25_weight': bm25_weight,
        # Derived from bm25_weight so the two weights can never drift apart in config
        'vector_weight': round(1.0 - bm25_weight, 4),
        'top_k': config.get('retriever.top_k', config.get('vector_store.top_k', 5)),
        'similarity_threshold': config.get(
            'retriever.similarity_threshold',
            config.get('vector_store.similarity_threshold', 0.1)
        ),
        'rerank_top_k': config.get('retriever.rerank_top_k', 10),
        'rerank_enabled': config.get('retriever.rerank_enabled', True),
        'query_rewrite_enabled': config.get('retriever.query_rewrite_enabled', True),
    }


def validate_retrieval_weights(params: Dict) -> List[str]:
    """
    Report weight/threshold combinations under which a retrieval route cannot contribute.

    A hybrid score is `bm25_weight * bm25 + vector_weight * vector`. A route whose weight is
    below `similarity_threshold` can therefore never push a candidate past the filter on its
    own, and whenever the other route comes back empty (BM25 index still building after a
    restart, or a query with no lexical overlap at all) every candidate is dropped: the answer
    claims "no relevant information" although the route that did find the chunk returned it.

    Args:
        params: Parameter dictionary (retrieval defaults or scenario/scenario-less params)

    Returns:
        Warning strings; empty when the configuration is self-consistent.
    """
    warnings = []
    if params.get('mode', 'hybrid') != 'hybrid':
        return warnings

    threshold = params.get('similarity_threshold')
    if threshold is None:
        return warnings

    bm25_weight = params.get('bm25_weight', 0.0) or 0.0
    vector_weight = params.get('vector_weight')
    if vector_weight is None:
        vector_weight = round(1.0 - bm25_weight, 4)

    if vector_weight < threshold:
        warnings.append(
            f"vector_weight ({vector_weight}) < similarity_threshold ({threshold}): a chunk found "
            f"only by the vector route can never pass the threshold, so any query whose BM25 route "
            f"returns nothing is answered as 'no relevant information'. Lower similarity_threshold "
            f"or lower retriever.bm25_weight."
        )
    if bm25_weight < threshold:
        warnings.append(
            f"bm25_weight ({bm25_weight}) < similarity_threshold ({threshold}): a chunk found only "
            f"by the BM25 route can never pass the threshold. Lower similarity_threshold or lower "
            f"retriever.bm25_weight."
        )
    return warnings


class HybridRetriever:
    """Hybrid retriever"""

    def __init__(self, vector_store_manager):
        """
        Initialize hybrid retriever

        Args:
            vector_store_manager: Vector store manager instance
        """
        self.vector_store = vector_store_manager

        # Defaults live in config/settings.yaml (`retriever:` block). Reading them through
        # get_retrieval_defaults() guarantees the running retriever, the REST endpoint that
        # feeds the UI, and the UI itself can never disagree about what "default" means.
        defaults = get_retrieval_defaults()
        self.mode = defaults['mode']
        self.bm25_weight = defaults['bm25_weight']
        self.vector_weight = defaults['vector_weight']
        self.top_k = defaults['top_k']
        self.rerank_top_k = defaults['rerank_top_k']
        self.rerank_enabled = defaults['rerank_enabled']
        self.query_rewrite_enabled = defaults['query_rewrite_enabled']
        self.similarity_threshold = defaults['similarity_threshold']
        self.empty_response = config.get('retriever.empty_response', 'No relevant documents found')
        self.compression_enabled = config.get('retriever.compression.enabled', True)
        self.max_tokens = config.get('retriever.compression.max_tokens', 2048)
        self.tokenizer = config.get('bm25.tokenizer', 'jieba')

        # BM25 index cache
        self._bm25_cache = {}
        self._bm25_docs_cache = {}
        # BM25 index build status: building indicates kb_id being built in background, build_version used to invalidate stale builds
        self._bm25_building = set()
        self._bm25_build_version = {}
        self._bm25_lock = threading.Lock()
        # Build threads by kb_id: a retrieval waits a bounded time for an in-flight build
        # instead of answering from a single route (see _ensure_bm25_index).
        self._bm25_build_threads = {}
        self.bm25_build_wait_ms = config.get('bm25.build_wait_ms', 500)

        # Configuration self-check: a weight below the similarity threshold makes the other
        # route mandatory, so a degraded retrieval silently answers "nothing found".
        for _warning in validate_retrieval_weights(defaults):
            logger.warning(f"[Retriever Config] {_warning}")

        # Query rewriter
        self._query_rewriter = QueryRewriter()

    def _get_scenario_params(self, scenario_id: Optional[str] = None) -> Dict:
        """
        Get scenario-based retrieval parameters

        Args:
            scenario_id: Scenario ID

        Returns:
            Parameter dictionary, containing all retrieval-related parameters
        """
        if scenario_id is None:
            return {
                'mode': self.mode,
                'bm25_weight': self.bm25_weight,
                'vector_weight': self.vector_weight,
                'top_k': self.top_k,
                'bm25_top_k': config.get('bm25.top_k', 5),
                'rerank_top_k': self.rerank_top_k,
                'similarity_threshold': self.similarity_threshold,
                'compression_enabled': self.compression_enabled,
                'max_tokens': self.max_tokens,
                'empty_response': self.empty_response,
                'query_rewrite_enabled': self.query_rewrite_enabled,
                'rerank_enabled': self.rerank_enabled,
            }

        effective_config = scenario_config.get_effective_config(scenario_id)
        retriever_cfg = effective_config.get('retriever', {})
        vector_store_cfg = effective_config.get('vector_store', {})
        # `top_k` / `similarity_threshold` also live in the global `retriever:` block, which
        # a deep merge would always surface over the scenario's own values. Resolve them from
        # the scenario's raw file first (retriever.* before the legacy vector_store.*), then
        # fall back to the merged global value, so both spellings keep working:
        #   scenario retriever.top_k > scenario vector_store.top_k > global retriever.top_k > global vector_store.top_k
        scenario_override = scenario_config.get_scenario(scenario_id) or {}
        scenario_retriever = scenario_override.get('retriever', {}) or {}
        scenario_vector_store = scenario_override.get('vector_store', {}) or {}
        return {
            'mode': retriever_cfg.get('mode', self.mode),
            'bm25_weight': retriever_cfg.get('bm25_weight', self.bm25_weight),
            'vector_weight': round(1.0 - retriever_cfg.get('bm25_weight', self.bm25_weight), 4),
            'top_k': _first_defined(
                scenario_retriever.get('top_k'),
                scenario_vector_store.get('top_k'),
                retriever_cfg.get('top_k'),
                vector_store_cfg.get('top_k'),
                self.top_k,
            ),
            'bm25_top_k': effective_config.get('bm25', {}).get('top_k', config.get('bm25.top_k', 5)),
            'rerank_top_k': retriever_cfg.get('rerank_top_k', self.rerank_top_k),
            'similarity_threshold': _first_defined(
                scenario_retriever.get('similarity_threshold'),
                scenario_vector_store.get('similarity_threshold'),
                retriever_cfg.get('similarity_threshold'),
                vector_store_cfg.get('similarity_threshold'),
                self.similarity_threshold,
            ),
            'compression_enabled': retriever_cfg.get('compression', {}).get('enabled', self.compression_enabled),
            'max_tokens': retriever_cfg.get('compression', {}).get('max_tokens', self.max_tokens),
            'empty_response': retriever_cfg.get('empty_response', self.empty_response),
            'query_rewrite_enabled': retriever_cfg.get('query_rewrite_enabled', self.query_rewrite_enabled),
            'rerank_enabled': retriever_cfg.get('rerank_enabled', self.rerank_enabled),
        }

    def get_effective_params(self, scenario_id: Optional[str] = None) -> Dict:
        """
        Public accessor for the effective retrieval parameters of a scenario.

        Exposed so callers outside the retriever (e.g. the on-demand miss scan in the API
        layer) can reuse the exact threshold / top_k a real query used, instead of guessing
        them or reaching into the private helper.

        Args:
            scenario_id: Scenario ID (optional)

        Returns:
            Parameter dictionary identical to the one used by retrieve()
        """
        return self._get_scenario_params(scenario_id)

    def _tokenize(self, text: str) -> List[str]:
        """
        Tokenize text

        Args:
            text: Input text

        Returns:
            Token list
        """
        if self.tokenizer == 'jieba':
            return list(jieba.cut(text))
        else:
            return text.split()

    def _ensure_bm25_index(self, kb_id: str, documents: List[Dict] = None,
                           wait_ms: int = None) -> bool:
        """
        Ensure BM25 index is available.

        Index building executes in a background thread to avoid blocking the main thread
        by synchronously building a large index during retrieval requests. The caller may
        wait a bounded time for an *in-flight* build to finish: a rebuild is cheap (tens of
        milliseconds on a typical knowledge base) and answering from the vector route alone
        is not free - the fusion formula still reserves the BM25 weight, which can push every
        candidate below the similarity threshold. That is how the first query after a restart
        returned 0 documents while the second, identical query answered correctly.

        Args:
            kb_id: Knowledge base ID
            documents: Optional, document list (when provided, rebuilds index synchronously,
                       for callback scenarios that explicitly need immediate availability)
            wait_ms: Optional, upper bound (ms) to wait for an in-flight build; defaults to
                     `bm25.build_wait_ms`

        Returns:
            True means index is ready and available; False means it is still being built in the
            background (caller must degrade to the remaining route(s)).
        """
        # When documents are provided, rebuild synchronously (for callback scenarios that explicitly need immediate availability)
        if documents is not None:
            self._build_bm25_index(kb_id, documents)
            return True

        if wait_ms is None:
            wait_ms = getattr(self, 'bm25_build_wait_ms', None)

        # Index is ready
        if kb_id in self._bm25_cache:
            return True

        started = False
        current_version = self._bm25_build_version.get(kb_id, 0)

        with self._bm25_lock:
            # double-check to prevent concurrent duplicate triggers
            if kb_id in self._bm25_cache:
                return True
            thread = self._bm25_build_threads.get(kb_id)
            if thread is not None and not thread.is_alive():
                # The previous build is over (possibly without producing an index, e.g. the
                # knowledge base was empty or Chroma raised). Joining a dead thread would
                # report "still building" forever, so start a fresh one.
                thread = None
                self._bm25_build_threads.pop(kb_id, None)
                self._bm25_building.discard(kb_id)
            if thread is None:
                self._bm25_building.add(kb_id)
                thread = threading.Thread(
                    target=self._background_build_bm25,
                    args=(kb_id, current_version),
                    daemon=True
                )
                self._bm25_build_threads[kb_id] = thread
                started = True

        if started:
            thread.start()
            logger.info(f"BM25 index background build started for: {kb_id} (version {current_version})")

        # Bounded wait: cheaper than the wasted retrieval it prevents, and it keeps this query
        # identical to the next one instead of degrading only the first request after a restart.
        if wait_ms and wait_ms > 0:
            thread.join(wait_ms / 1000.0)
            if kb_id in self._bm25_cache:
                return True
            logger.warning(
                f"BM25 index still building after {wait_ms} ms, degrading to the remaining route(s): {kb_id}"
            )

        return kb_id in self._bm25_cache

    def _build_bm25_index(self, kb_id: str, documents: List[Dict] = None, expected_version: int = None):
        """
        Actually build BM25 index (synchronous blocking, should be called in background thread or when explicitly needed)

        Args:
            kb_id: Knowledge base ID
            documents: Optional, document list; when empty, loads all documents from vector store
            expected_version: Version number recorded at build start, validated before writing,
                              to avoid writing stale index
        """
        try:
            if documents is None:
                loader = getattr(self.vector_store, 'get_documents', None)
                if loader is not None:
                    # Preferred path: read the texts without building the embedding function.
                    # Building it costs seconds on a cold process, which pushed the BM25 index
                    # past every bounded wait and made the first query degrade.
                    documents = loader(kb_id) or []
                else:
                    # Fallback for vector stores that only expose a collection (test doubles).
                    collection = self.vector_store.get_collection(kb_id)
                    if collection is None:
                        logger.warning(f"BM25 index build failed: Knowledge base {kb_id} not found")
                        self._set_bm25_cache(kb_id, None, [], expected_version)
                        return

                    result = collection.get(include=['documents', 'metadatas'])
                    documents = []
                    for i in range(len(result['ids'])):
                        documents.append({
                            'id': result['ids'][i],
                            'text': result['documents'][i] if result.get('documents') else "",
                            'metadata': result['metadatas'][i] if result.get('metadatas') else {}
                        })

            if documents:
                tokenized_docs = [self._tokenize(doc['text']) for doc in documents]
                self._set_bm25_cache(kb_id, BM25Okapi(tokenized_docs), documents, expected_version)
                logger.info(f"BM25 index build completed for: Knowledge base {kb_id} with {len(documents)} documents")
            else:
                self._set_bm25_cache(kb_id, None, [], expected_version)
                logger.info(f"Knowledge base {kb_id} has no documents, BM25 index cleared")

        except Exception as e:
            logger.error(f"BM25 index build failed {kb_id}: {e}")
            self._set_bm25_cache(kb_id, None, [], expected_version)

    def _set_bm25_cache(self, kb_id: str, index, docs: List[Dict], expected_version: int = None):
        """
        Write to BM25 cache. If expected_version does not match current version, abort write,
        to avoid writing stale index when document updates occur during background build.
        """
        with self._bm25_lock:
            if expected_version is not None and self._bm25_build_version.get(kb_id, 0) != expected_version:
                logger.info(f"BM25 index version has changed, aborting write of stale index: {kb_id}")
                return
            self._bm25_cache[kb_id] = index
            self._bm25_docs_cache[kb_id] = docs

    def _background_build_bm25(self, kb_id: str, expected_version: int):
        """Build BM25 index in background thread"""
        try:
            logger.info(f"BM25 index background build started for: {kb_id} (version {expected_version})")
            self._build_bm25_index(kb_id, expected_version=expected_version)
        except Exception as e:
            logger.error(f"BM25 index background build failed for {kb_id}: {e}")
        finally:
            with self._bm25_lock:
                self._bm25_building.discard(kb_id)
                # Drop the thread handle too: a finished build must never be joined again,
                # otherwise the next retrieval would wait on a dead thread and then report
                # the index as "still building" forever.
                self._bm25_build_threads.pop(kb_id, None)
            logger.info(f"BM25 index background build completed for: {kb_id}")

    def _is_bm25_building(self, kb_id: str) -> bool:
        """Check whether the BM25 index for the specified knowledge base is being built in background"""
        with self._bm25_lock:
            return kb_id in self._bm25_building

    def invalidate_bm25_index(self, kb_id: str):
        """
        Clear the BM25 index cache for the specified knowledge base; it will be rebuilt
        in the background on next retrieval.

        Also increments the build version number to invalidate in-progress background builds
        when writing, to avoid writing stale index.

        Args:
            kb_id: Knowledge base ID
        """
        with self._bm25_lock:
            self._bm25_cache.pop(kb_id, None)
            self._bm25_docs_cache.pop(kb_id, None)
            self._bm25_build_version[kb_id] = self._bm25_build_version.get(kb_id, 0) + 1
        logger.info(f"BM25 index cache cleared for: {kb_id} (version incremented, next retrieval will trigger background build)")

    def _bm25_search(self, kb_id: str, query: str, top_k: int = None, params: Dict = None) -> List[Dict]:
        """
        BM25 keyword retrieval

        Args:
            kb_id: Knowledge base ID
            query: Query text
            top_k: Return count
            params: Parameter dictionary (optional, contains bm25_top_k)

        Returns:
            Retrieval result list
        """
        if top_k is None:
            if params and 'bm25_top_k' in params:
                top_k = params['bm25_top_k']
            else:
                top_k = config.get('bm25.top_k', 5)

        ready = self._ensure_bm25_index(kb_id)
        if not ready:
            logger.info(f"BM25 index not ready, fallback to vector retrieval for: {kb_id}")
            return []

        if self._bm25_cache.get(kb_id) is None:
            return []

        try:
            bm25 = self._bm25_cache[kb_id]
            docs = self._bm25_docs_cache.get(kb_id, [])

            tokenized_query = self._tokenize(query)
            scores = bm25.get_scores(tokenized_query)

            # Sort by score
            scored_docs = []
            for i, score in enumerate(scores):
                if score > 0 and i < len(docs):
                    doc = docs[i].copy()
                    doc['score'] = float(score)
                    doc['type'] = 'bm25'
                    scored_docs.append(doc)

            scored_docs.sort(key=lambda x: x['score'], reverse=True)

            # Normalize scores to 0-1 range
            if scored_docs:
                max_score = scored_docs[0]['score']
                if max_score > 0:
                    for doc in scored_docs:
                        doc['score'] = doc['score'] / max_score

            results = scored_docs[:top_k]
            logger.debug(f"BM25 retrieval completed, returning {len(results)} results")
            return results

        except Exception as e:
            logger.error(f"BM25 retrieval failed: {e}")
            return []

    def _vector_search(self, kb_id: str, query: str, top_k: int = None) -> List[Dict]:
        """
        Vector semantic retrieval

        Args:
            kb_id: Knowledge base ID
            query: Query text
            top_k: Return count

        Returns:
            Retrieval result list
        """
        try:
            results = self.vector_store.search(kb_id, query, top_k)
            return results
        except Exception as e:
            logger.error(f"Vector retrieval failed: {e}")
            return []

    def _merge_and_rerank(self, bm25_results: List[Dict], vector_results: List[Dict], params: Dict) -> List[Dict]:
        """
        Merge BM25 and vector retrieval results and rerank

        Args:
            bm25_results: BM25 retrieval results
            vector_results: Vector retrieval results
            params: Parameter dictionary (contains mode, bm25_weight, vector_weight, rerank_top_k)

        Returns:
            Merged and reranked results
        """
        mode = params.get('mode', self.mode)
        bm25_weight = params.get('bm25_weight', self.bm25_weight)
        vector_weight = params.get('vector_weight', self.vector_weight)
        rerank_top_k = params.get('rerank_top_k', self.rerank_top_k)

        if mode == 'bm25':
            return bm25_results
        if mode == 'vector':
            return vector_results

        merged = {}

        for doc in bm25_results:
            doc_id = doc['id']
            if doc_id not in merged:
                merged[doc_id] = doc.copy()
                merged[doc_id]['bm25_score'] = doc['score']
                merged[doc_id]['vector_score'] = 0.0
            else:
                merged[doc_id]['bm25_score'] = doc['score']

        for doc in vector_results:
            doc_id = doc['id']
            if doc_id not in merged:
                merged[doc_id] = doc.copy()
                merged[doc_id]['bm25_score'] = 0.0
                merged[doc_id]['vector_score'] = doc['score']
            else:
                merged[doc_id]['vector_score'] = doc['score']

        for doc_id, doc in merged.items():
            bm25_score = doc.get('bm25_score', 0.0)
            vector_score = doc.get('vector_score', 0.0)
            doc['score'] = (bm25_score * bm25_weight) + (vector_score * vector_weight)
            doc['type'] = 'hybrid'

        results = list(merged.values())
        results.sort(key=lambda x: x['score'], reverse=True)

        results = results[:rerank_top_k]

        logger.debug(f"Hybrid retrieval completed, returning {len(results)} results")
        return results

    def _compress_context(self, results: List[Dict], query: str, max_tokens: int = None, params: Dict = None) -> List[Dict]:
        """
        Context compression (simple relevance-based compression)

        Args:
            results: Retrieval results
            query: Query text
            max_tokens: Maximum token count
            params: Parameter dictionary (contains compression_enabled, max_tokens)

        Returns:
            Compressed results
        """
        compression_enabled = params.get('compression_enabled', self.compression_enabled) if params else self.compression_enabled
        
        if not compression_enabled or not results:
            return results

        if max_tokens is None:
            max_tokens = params.get('max_tokens', self.max_tokens) if params else self.max_tokens

        summary_keywords = ['enumerate', 'summary', 'overview', 'content', 'all', 'all', 'complete', 'detail', 'info']
        is_summary_query = any(kw in query for kw in summary_keywords)

        if is_summary_query:
            logger.debug(f"Detected summary query: {query}, skipping context compression")
            return results

        query_terms = set(self._tokenize(query))
        compressed_results = []
        current_tokens = 0

        for result in results:
            text = result['text']
            sentences = re.split(r'[。！？\n]', text)

            relevant_sentences = []
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue

                sentence_terms = set(self._tokenize(sentence))
                overlap = len(query_terms & sentence_terms)

                if overlap > 0 or len(relevant_sentences) < 2:
                    relevant_sentences.append(sentence)
                    current_tokens += len(sentence)

                    if current_tokens >= max_tokens:
                        break

            if relevant_sentences:
                compressed_text = '。'.join(relevant_sentences)
                compressed_result = result.copy()
                compressed_result['text'] = compressed_text
                compressed_result['compressed'] = True
                compressed_result['original_length'] = len(text)
                compressed_results.append(compressed_result)
            else:
                compressed_results.append(result)

            if current_tokens >= max_tokens:
                break

        logger.debug(f"Context compression completed: {len(results)} -> {len(compressed_results)} results")
        return compressed_results

    @staticmethod
    def _funnel_item(item: Dict, rank: int) -> Dict:
        """Normalize one retrieval candidate for the UI funnel view (text truncated)."""
        metadata = item.get('metadata') or {}
        text = item.get('text') or ''
        return {
            'id': item.get('id'),
            'rank': rank,
            'score': round(item.get('score', 0) or 0, 4),
            'bm25_score': round(item.get('bm25_score', 0) or 0, 4),
            'vector_score': round(item.get('vector_score', 0) or 0, 4),
            'file_name': metadata.get('file_name', _('pipeline.unknown_doc')),
            'chunk_index': metadata.get('chunk_index'),
            'total_chunks': metadata.get('total_chunks'),
            'text': text[:200],
            'compressed': bool(item.get('compressed'))
        }

    def _build_retrieval_funnel(self, merged: List[Dict], after_threshold: List[Dict],
                                after_compression: List[Dict], kept: List[Dict],
                                similarity_threshold: float, top_k: int, mode: str,
                                bm25_weight: float, vector_weight: float,
                                bm25_results: List[Dict] = None,
                                vector_results: List[Dict] = None) -> Dict:
        """
        Build the retrieval funnel: which candidates reached the prompt, which were dropped,
        and at which stage (threshold / compression / top_k).

        This is the data behind the white-box answer to "why is this document missing?".
        A candidate is attributed to the FIRST stage it disappears from, so the reason is
        unambiguous: threshold filter -> context compression -> top_k truncation.

        Args:
            merged: Candidates after merge/rerank (before the threshold filter)
            after_threshold: Candidates that survived the threshold filter
            after_compression: Candidates that survived context compression
            kept: Candidates finally handed to the LLM (after top_k)
            similarity_threshold: Similarity threshold applied in this run
            top_k: Number of results kept for the prompt
            mode: Retrieval mode
            bm25_weight: BM25 weight used for fusion
            vector_weight: Vector weight used for fusion
            bm25_results: Raw BM25 route results (optional, for route attribution)
            vector_results: Raw vector route results (optional, for route attribution)

        Returns:
            Funnel dict: counts, params, kept list and dropped list (each with its stage)
        """
        kept_ids = {r.get('id') for r in kept if r.get('id')}
        bm25_ids = {r.get('id') for r in (bm25_results or []) if r.get('id')}
        vector_ids = {r.get('id') for r in (vector_results or []) if r.get('id')}

        dropped = []
        attributed = set()

        def collect(candidates: List[Dict], stage: str):
            for rank, item in enumerate(candidates, 1):
                cid = item.get('id')
                if not cid or cid in kept_ids or cid in attributed:
                    continue
                attributed.add(cid)
                entry = self._funnel_item(item, rank)
                entry['stage'] = stage
                if stage == 'threshold':
                    entry['threshold'] = similarity_threshold
                    entry['gap'] = round(max(0.0, similarity_threshold - entry['score']), 4)
                elif stage == 'top_k':
                    entry['top_k'] = top_k
                elif stage == 'compression':
                    entry['reason_value'] = 'max_tokens'
                dropped.append(entry)

        # Order matters: the first stage a candidate is missing from is the one that dropped it.
        collect(after_compression, 'top_k')
        collect(after_threshold, 'compression')
        collect(merged, 'threshold')

        kept_items = []
        for rank, item in enumerate(kept, 1):
            entry = self._funnel_item(item, rank)
            entry['in_bm25'] = item.get('id') in bm25_ids
            entry['in_vector'] = item.get('id') in vector_ids
            kept_items.append(entry)

        return {
            'counts': {
                'merged': len(merged),
                'after_threshold': len(after_threshold),
                'after_compression': len(after_compression),
                'kept': len(kept),
                'dropped': len(dropped)
            },
            'params': {
                'mode': mode,
                'similarity_threshold': similarity_threshold,
                'top_k': top_k,
                'bm25_weight': bm25_weight,
                'vector_weight': vector_weight,
                'rerank_top_k': self.rerank_top_k
            },
            'kept': kept_items,
            'dropped': dropped
        }

    def _generate_hit_reasons(self, result: Dict, query: str, query_tokens: List[str], mode: str, params: Dict = None) -> List[Dict]:
        """
        Generate hit attribution reasons for each retrieval result

        Args:
            result: Single retrieval result
            query: Query text
            query_tokens: Query tokenization result
            mode: Retrieval mode
            params: Parameter dictionary (contains weights and other config)

        Returns:
            Hit reason list, each reason contains:
            {
                'type': 'keyword_match' | 'semantic_similarity' | 'metadata_match' | 'score_threshold',
                'value': specific value (keyword, score, etc.),
                'source': 'bm25' | 'vector' | 'hybrid',
                'explanation': business-oriented explanation
            }
        """
        reasons = []
        text = result.get('text', '')
        metadata = result.get('metadata', {})

        # Use weight values from params, default to global config
        if params is None:
            params = {}
        bm25_weight = params.get('bm25_weight', self.bm25_weight)
        vector_weight = params.get('vector_weight', self.vector_weight)
        similarity_threshold = params.get('similarity_threshold', self.similarity_threshold)

        matched_keywords = []
        for token in query_tokens:
            if len(token) > 1 and token in text:
                matched_keywords.append(token)

        if matched_keywords:
            reasons.append({
                'type': 'keyword_match',
                'value': matched_keywords[:5],
                'source': 'bm25' if mode == 'bm25' else 'hybrid',
                'explanation': f"Hit keywords: {', '.join(matched_keywords[:3])}"
            })

        bm25_score = result.get('bm25_score', 0)
        vector_score = result.get('vector_score', 0)
        final_score = result.get('score', 0)

        if mode == 'bm25' or (mode == 'hybrid' and bm25_score > 0):
            reasons.append({
                'type': 'bm25_score',
                'value': round(bm25_score, 4),
                'source': 'bm25',
                'explanation': f"BM25 keyword match score: {round(bm25_score, 4)}"
            })

        if mode == 'vector' or (mode == 'hybrid' and vector_score > 0):
            reasons.append({
                'type': 'semantic_similarity',
                'value': round(vector_score, 4),
                'source': 'vector',
                'explanation': f"Vector semantic similarity: {round(vector_score, 4)}"
            })

        if mode == 'hybrid':
            reasons.append({
                'type': 'score_contribution',
                'value': {
                    'bm25_weight': bm25_weight,
                    'vector_weight': vector_weight,
                    'bm25_contribution': round(bm25_score * bm25_weight, 4),
                    'vector_contribution': round(vector_score * vector_weight, 4),
                    'final_score': round(final_score, 4)
                },
                'source': 'hybrid',
                'explanation': f"Weighted hybrid score: BM25({round(bm25_score, 4)}×{bm25_weight}) + Vector({round(vector_score, 4)}×{vector_weight}) = {round(final_score, 4)}"
            })

        file_type = metadata.get('file_type', '')
        if file_type:
            reasons.append({
                'type': 'metadata_match',
                'value': {'file_type': file_type},
                'source': 'filter',
                'explanation': f"Document type match: {file_type}"
            })

        if final_score >= similarity_threshold:
            reasons.append({
                'type': 'score_threshold',
                'value': {
                    'score': round(final_score, 4),
                    'threshold': similarity_threshold
                },
                'source': 'filter',
                'explanation': f"Score {round(final_score, 4)} exceeds threshold {similarity_threshold}"
            })

        if not reasons:
            reasons.append({
                'type': 'unknown',
                'value': '',
                'source': mode,
                'explanation': 'Hit reason unknown, default to in domain'
            })

        return reasons

    def retrieve(self, kb_id: str, query: str, top_k: int = None, scenario_id: Optional[str] = None,
                 mode: str = None, bm25_weight: float = None, similarity_threshold: float = None,
                 vector_weight: float = None,
                 query_rewrite_enabled: Optional[bool] = None,
                 rerank_enabled: Optional[bool] = None) -> Dict:
        """
        Hybrid retrieval main interface

        Args:
            kb_id: Knowledge base ID
            query: Query text
            top_k: Return count
            scenario_id: Scenario ID (optional, used to load scenario-specific retrieval parameters)
            mode: Retrieval mode (overrides scenario config)
            bm25_weight: BM25 weight (overrides scenario config)
            similarity_threshold: Similarity threshold (overrides scenario config)
            vector_weight: Vector weight (overrides scenario config)
            query_rewrite_enabled: Whether to enable query rewrite (overrides scenario config, controllable via A/B test)
            rerank_enabled: Whether to enable reranking (overrides scenario config, controllable via A/B test)

        Returns:
            Retrieval result dictionary, containing:
            - results: Retrieval result list
            - count: Result count
            - has_results: Whether there are results
            - mode: Retrieval mode
            - debug_info: Debug info (contains tokenization, BM25/vector retrieval process)
        """
        params = self._get_scenario_params(scenario_id)

        if top_k is None:
            top_k = params['top_k']

        # Use passed parameters to override scenario config
        if mode:
            params['mode'] = mode
        if bm25_weight is not None:
            params['bm25_weight'] = bm25_weight
            params['vector_weight'] = 1 - bm25_weight if vector_weight is None else vector_weight
        if vector_weight is not None:
            params['vector_weight'] = vector_weight
            params['bm25_weight'] = 1 - vector_weight if bm25_weight is None else bm25_weight
        if similarity_threshold is not None:
            params['similarity_threshold'] = similarity_threshold
        if top_k is not None:
            params['top_k'] = top_k
        # A/B test switch override (default True, only override when explicitly passed)
        if query_rewrite_enabled is not None:
            params['query_rewrite_enabled'] = query_rewrite_enabled
        if rerank_enabled is not None:
            params['rerank_enabled'] = rerank_enabled

        mode = params['mode']
        similarity_threshold = params['similarity_threshold']
        rerank_top_k = params['rerank_top_k']
        compression_enabled = params['compression_enabled']
        empty_response = params['empty_response']
        bm25_weight = params['bm25_weight']
        vector_weight = params['vector_weight']

        # Query rewrite switch (controllable via A/B test, defaults from settings.yaml)
        query_rewrite_enabled = params.get('query_rewrite_enabled', self.query_rewrite_enabled)
        if query_rewrite_enabled:
            rewrite_result = self._query_rewriter.rewrite(query)
            rewritten_query = rewrite_result.get('rewritten_query', query)
        else:
            rewrite_result = {'rewritten_query': query, 'rewrite_reason': 'Query rewrite disabled', 'key_terms': [], 'term_mappings': [], 'expanded_terms': [], 'typo_correction': {'has_correction': False, 'corrected_text': query, 'corrections': []}}
            rewritten_query = query

        # Reranking switch (controllable via A/B test, defaults from settings.yaml):
        # when disabled, rerank_top_k = top_k, no extra truncation
        rerank_enabled = params.get('rerank_enabled', self.rerank_enabled)
        if not rerank_enabled:
            rerank_top_k = top_k if top_k is not None else rerank_top_k

        debug_info = {
            'query': query,
            'rewrite': rewrite_result,
            'query_tokens': self._tokenize(rewritten_query),
            'mode': mode,
            'params': {
                'mode': mode,
                'top_k': top_k,
                'bm25_weight': bm25_weight,
                'vector_weight': vector_weight,
                'similarity_threshold': similarity_threshold,
                'scenario_id': scenario_id
            },
            'embedding_model': self.vector_store.embedding_model if hasattr(self.vector_store, 'embedding_model') else 'unknown',
            'steps': [],
            'bm25_results': [],
            'vector_results': [],
            'merged_results': []
        }

        logger.info(f"[Retrieval Process] Start retrieval, kb: {kb_id}, Query: {query[:50]}..., Mode: {mode}, Scenario: {scenario_id or 'Default'}, top_k: {top_k}, 相似度阈值: {similarity_threshold}, BM25权重: {bm25_weight}, 向量权重: {vector_weight}")

        bm25_degraded = False
        route_degraded = False
        # Effective fusion weights for this query. They differ from the configured ones when a
        # route returned nothing (see the hybrid branch below); the funnel and the hit-reason
        # explanations must report the weights the scores were actually produced with.
        merge_params = params
        eff_bm25_weight = bm25_weight
        eff_vector_weight = vector_weight

        try:
            logger.info(f"[Retrieval Process] Step 1: Query rewrite, original query: {query[:50]}...")
            debug_info['steps'].append({
                'step': 'rewrite',
                'message': f"Query rewrite: {rewrite_result.get('rewrite_reason', 'None')}",
                'original_query': query,
                'rewritten_query': rewritten_query,
                'key_terms': rewrite_result.get('key_terms', []),
                'term_mappings': rewrite_result.get('term_mappings', []),
                'details': f"Original query: {query}, Rewritten query: {rewritten_query}"
            })
            logger.info(f"[Retrieval Process] Step 1: Query rewrite completed, rewritten query: {rewritten_query[:50]}..., Rewrite reason: {rewrite_result.get('rewrite_reason', 'None')}, Keywords: {rewrite_result.get('key_terms', [])}")

            logger.info(f"[Retrieval Process] Step 2: Query tokenize, token count: {len(debug_info['query_tokens'])}")
            debug_info['steps'].append({
                'step': 'tokenize',
                'message': f"Query tokenize: Split rewritten query into keywords, token count: {len(debug_info['query_tokens'])} keywords",
                'tokens': debug_info['query_tokens'],
                'details': 'Tokenize result for BM25 keyword matching, help to quickly locate documents containing the same vocabulary'
            })
            logger.info(f"[Retrieval Process] Step 2: Query tokenize completed, tokenize result: {debug_info['query_tokens']}")

            if mode == 'bm25':
                logger.info(f"[Retrieval Process] Step 3: BM25 retrieval, top_k: {top_k}")
                debug_info['steps'].append({
                    'step': 'bm25_search',
                    'message': 'BM25 retrieval: Use keywords to search inverted index for matching documents',
                    'details': 'Based on TF-IDF algorithm, calculate relevance score for each document'
                })
                results = self._bm25_search(kb_id, rewritten_query, top_k, params)
                bm25_degraded = self._is_bm25_building(kb_id) and len(results) == 0
                debug_info['bm25_results'] = [{
                    'id': r['id'],
                    'score': r.get('score', 0),
                    'text': r['text'][:100] + '...' if len(r['text']) > 100 else r['text'],
                    # Keep the fallback empty: the UI localizes a missing file name itself
                    'file_name': r.get('metadata', {}).get('file_name'),
                    'chunk_index': r.get('metadata', {}).get('chunk_index', 0),
                    'total_chunks': r.get('metadata', {}).get('total_chunks', 0)
                } for r in results]
                debug_info['steps'].append({'step': 'bm25_complete', 'message': f"BM25 retrieval completed, hit {len(results)} documents"})
                logger.info(f"[Retrieval Process] Step 3: BM25 retrieval completed, hit {len(results)} documents")

            elif mode == 'vector':
                logger.info(f"[Retrieval Process] Step 3: Vector retrieval, top_k: {top_k}, embedding model: {debug_info['embedding_model']}")
                debug_info['steps'].append({
                    'step': 'embedding',
                    'message': f"Generate vector: Use {debug_info['embedding_model']}",
                    'details': f"Embedding model convert text to vector representation, dimension: {self.vector_store.embedding_dim} dimension vector"
                })
                debug_info['steps'].append({
                    'step': 'vector_search',
                    'message': 'Vector retrieval: Use ChromaDB for cosine similarity query',
                    'details': 'Calculate query vector similarity with all document vectors, return the top_k results'
                })
                results = self._vector_search(kb_id, rewritten_query, top_k)
                debug_info['vector_results'] = [{
                    'id': r['id'],
                    'score': r.get('score', 0),
                    'text': r['text'][:100] + '...' if len(r['text']) > 100 else r['text'],
                    # Keep the fallback empty: the UI localizes a missing file name itself
                    'file_name': r.get('metadata', {}).get('file_name'),
                    'chunk_index': r.get('metadata', {}).get('chunk_index', 0),
                    'total_chunks': r.get('metadata', {}).get('total_chunks', 0)
                } for r in results]
                debug_info['steps'].append({
                    'step': 'vector_complete',
                    'message': f"Vector retrieval completed, hit {len(results)} documents",
                    'details': f"Total {len(results)} related documents, sorted by similarity",
                    'results': [{
                        'index': i + 1,
                        # Keep the fallback empty: the UI localizes a missing file name itself
                        'file_name': r.get('metadata', {}).get('file_name'),
                        'chunk_index': r.get('metadata', {}).get('chunk_index', 0),
                        'total_chunks': r.get('metadata', {}).get('total_chunks', 0),
                        'score': r.get('score', 0),
                        'text': r['text'][:150] + '...' if len(r['text']) > 150 else r['text']
                    } for i, r in enumerate(results)]
                })
                logger.info(f"[Retrieval Process] Step 3: Vector retrieval completed, hit {len(results)} documents")

            else:
                logger.info(f"[Retrieval Process] Step 3: Hybrid retrieval (BM25+vector), rerank_top_k: {rerank_top_k}, BM25 weight: {bm25_weight}, vector weight: {vector_weight}")
                debug_info['steps'].append({
                    'step': 'bm25_search',
                    'message': 'BM25 retrieval: Use keywords to search inverted index for matching documents',
                    'details': 'Based on TF-IDF algorithm, calculate relevance score for each document'
                })
                bm25_results = self._bm25_search(kb_id, rewritten_query, rerank_top_k, params)
                bm25_degraded = self._is_bm25_building(kb_id) and len(bm25_results) == 0
                debug_info['bm25_results'] = [{
                    'id': r['id'],
                    'score': r.get('score', 0),
                    'text': r['text'],
                    # Keep the fallback empty: the UI localizes a missing file name itself
                    'file_name': r.get('metadata', {}).get('file_name'),
                    'chunk_index': r.get('metadata', {}).get('chunk_index', 0),
                    'total_chunks': r.get('metadata', {}).get('total_chunks', 0)
                } for r in bm25_results]
                debug_info['steps'].append({'step': 'bm25_complete', 'message': f"BM25 retrieval completed, hit {len(bm25_results)} documents"})
                logger.info(f"[Retrieval Process] Step 3: BM25 retrieval completed, hit {len(bm25_results)} documents")

                logger.info(f"[Retrieval Process] Step 4: Vector retrieval, rerank_top_k: {rerank_top_k}")
                debug_info['steps'].append({
                    'step': 'embedding',
                    'message': f"Generate vector: Use {debug_info['embedding_model']}",
                    'details': f"Embedding model convert text to vector representation, dimension: {self.vector_store.embedding_dim} dimension vector"
                })
                debug_info['steps'].append({
                    'step': 'vector_search',
                    'message': 'Vector retrieval: Use ChromaDB for cosine similarity query',
                    'details': 'Calculate query vector similarity with all document vectors, return the top_k results'
                })
                vector_results = self._vector_search(kb_id, rewritten_query, rerank_top_k)
                debug_info['vector_results'] = [{
                    'id': r['id'],
                    'score': r.get('score', 0),
                    'text': r['text'],
                    # Keep the fallback empty: the UI localizes a missing file name itself
                    'file_name': r.get('metadata', {}).get('file_name'),
                    'chunk_index': r.get('metadata', {}).get('chunk_index', 0),
                    'total_chunks': r.get('metadata', {}).get('total_chunks', 0)
                } for r in vector_results]
                debug_info['steps'].append({
                    'step': 'vector_complete',
                    'message': f"Vector retrieval completed, hit {len(vector_results)} documents",
                    'details': f"Total {len(vector_results)} related documents, sorted by similarity",
                    'results': [{
                        'index': i + 1,
                        # Keep the fallback empty: the UI localizes a missing file name itself
                        'file_name': r.get('metadata', {}).get('file_name'),
                        'chunk_index': r.get('metadata', {}).get('chunk_index', 0),
                        'total_chunks': r.get('metadata', {}).get('total_chunks', 0),
                        'score': r.get('score', 0),
                        'text': r['text']
                    } for i, r in enumerate(vector_results)]
                })

                # ===== Route degradation (never let a dead route keep its fusion weight) =====
                # The fused score is `bm25*w + vector*(1-w)`. If the BM25 route yields nothing and
                # its weight is left in place, the best score any candidate can reach is
                # `vector_weight` - with the shipped defaults (0.6 / 0.4) that is 0.4, below the
                # 0.5 similarity threshold, so every candidate is dropped and the user is told the
                # knowledge base has nothing, although the vector route found the right chunk.
                # That is exactly what the first query after a restart did (the lazy BM25 build
                # finished ~20 ms too late) while the second, identical query answered correctly.
                if bm25_results or bm25_weight <= 0 or not config.get('retriever.reroute_on_empty_route', True):
                    merge_params = params
                else:
                    merge_params = dict(params, bm25_weight=0.0, vector_weight=1.0)
                    eff_bm25_weight, eff_vector_weight = 0.0, 1.0
                    route_degraded = True
                    logger.warning(
                        f"[Retrieval Process] BM25 route returned no candidate, fusion weights "
                        f"renormalized for this query: bm25_weight {bm25_weight} -> 0.0, "
                        f"vector_weight {vector_weight} -> 1.0 (kb: {kb_id})"
                    )
                    debug_info['steps'].append({
                        'step': 'route_degraded',
                        'message': 'BM25 route empty: its fusion weight is redistributed to the '
                                   'vector route for this query',
                        'details': f'Configured BM25 weight {bm25_weight} / vector weight {vector_weight}; '
                                   f'effective BM25 weight 0.0 / vector weight 1.0 (BM25 index: '
                                   f'{"building" if bm25_degraded else "ready, but no lexical match for this query"})'
                    })

                logger.info(f"[Retrieval Process] Step 5: Hybrid retrieval (BM25 results: {len(bm25_results)}, vector results: {len(vector_results)}, BM25 weight: {bm25_weight}, vector weight: {vector_weight}")
                debug_info['steps'].append({
                    'step': 'merge',
                    'message': f"Merge results: Combine BM25 and vector retrieval results",
                    'details': f"Use weighted fusion strategy, BM25 weight={eff_bm25_weight}, vector weight={eff_vector_weight}, rerank and take the best results"
                })
                results = self._merge_and_rerank(bm25_results, vector_results, merge_params)
                debug_info['merged_results'] = [{
                    'id': r['id'],
                    'score': r.get('score', 0),
                    'bm25_score': r.get('bm25_score', 0),
                    'vector_score': r.get('vector_score', 0),
                    'text': r['text'],
                    # Keep the fallback empty: the UI localizes a missing file name itself
                    'file_name': r.get('metadata', {}).get('file_name'),
                    'chunk_index': r.get('metadata', {}).get('chunk_index', 0),
                    'total_chunks': r.get('metadata', {}).get('total_chunks', 0)
                } for r in results]
                debug_info['steps'].append({'step': 'merge_complete', 'message': f"Merge results completed, finally keep {len(results)} documents"})
                logger.info(f"[Retrieval Process] Step 5: Hybrid retrieval completed, finally keep {len(results)} documents")

            logger.info(f"[Retrieval Process] Step 6: Threshold filter, before filter {len(results)} documents, threshold: {similarity_threshold}")
            debug_info['steps'].append({
                'step': 'filter',
                'message': f"Threshold filter: Remove results with similarity below {similarity_threshold}",
                'details': f"Before filter {len(results)} documents, threshold: {similarity_threshold}"
            })
            before_filter_count = len(results)
            # Snapshot before the threshold filter: every candidate that was retrieved at all.
            # Used by _build_retrieval_funnel to explain what never reached the prompt.
            after_merge_snapshot = list(results)
            results = [r for r in results if r.get('score', 0) >= similarity_threshold]
            after_filter_snapshot = list(results)
            debug_info['steps'].append({'step': 'filter_complete', 'message': f"Threshold filter completed, finally keep {len(results)} documents"})
            logger.info(f"[Retrieval Process] Step 6: Threshold filter completed, finally keep {len(results)} documents")

            if compression_enabled and results:
                logger.info(f"[Retrieval Process] Step 7: Context compression, before compress {len(results)} documents")
                debug_info['steps'].append({'step': 'compression', 'message': 'Context compression: Remove redundant information, keep most relevant content'})
                results = self._compress_context(results, query, params=params)
                debug_info['steps'].append({'step': 'compression_complete', 'message': f"Context compression completed, finally keep {len(results)} documents"})
                logger.info(f"[Retrieval Process] Step 7: Context compression completed, finally keep {len(results)} documents")

            after_compression_snapshot = list(results)

            results = results[:top_k]
            logger.info(f"[Retrieval Process] Step 8: Truncate to top_k: {top_k}, finally keep {len(results)} documents")

            # ===== Retrieval funnel attribution (white-box core) =====
            # Record, for every retrieved candidate, whether it reached the prompt and - when it
            # did not - which stage dropped it. Without this the UI can only show "what hit",
            # never "what missed and why".
            debug_info['funnel'] = self._build_retrieval_funnel(
                merged=after_merge_snapshot,
                after_threshold=after_filter_snapshot,
                after_compression=after_compression_snapshot,
                kept=results,
                similarity_threshold=similarity_threshold,
                top_k=top_k,
                mode=mode,
                bm25_weight=eff_bm25_weight,
                vector_weight=eff_vector_weight,
                bm25_results=bm25_results if 'bm25_results' in locals() else [],
                vector_results=vector_results if 'vector_results' in locals() else []
            )

            logger.info(f"[Retrieval Process] Step 9: Generate hit attribution for {len(results)} documents")
            debug_info['steps'].append({'step': 'hit_attribution', 'message': 'Generate hit attribution for each result: Generate business-specific hit reasons'})
            for result in results:
                result['hit_reasons'] = self._generate_hit_reasons(
                    result, rewritten_query, debug_info['query_tokens'], mode, merge_params
                )
            logger.info(f"[Retrieval Process] Step 9: Generate hit attribution for each result: Generate business-specific hit reasons")

            logger.info(f"[Retrieval Process] Step 10: Execute recall diagnosis")
            debug_info['steps'].append({'step': 'recall_diagnostic', 'message': 'Execute recall diagnosis: Analyze why some high-value documents are not recalled'})
            recall_diagnosis = {}
            try:
                from core.recall_diagnostic import recall_diagnostic
                # Only perform full scan in debug mode to avoid doubling retrieval latency under high load
                recall_diagnosis = recall_diagnostic.diagnose(
                    kb_id=kb_id,
                    query=query,
                    retrieved_results=results,
                    vector_store=self.vector_store,
                    bm25_results=bm25_results if 'bm25_results' in locals() else None,
                    vector_results=vector_results if 'vector_results' in locals() else None,
                    params=params,
                    debug=config.get('system.debug', False)
                )
                debug_info['steps'].append({'step': 'recall_diagnostic_complete', 'message': f"Execute recall diagnosis completed, found {len(recall_diagnosis.get('diagnosis_items', []))} diagnosis items"})
                logger.info(f"[Retrieval Process] Step 10: Execute recall diagnosis completed, found {len(recall_diagnosis.get('diagnosis_items', []))} diagnosis items")
            except Exception as e:
                logger.warning(f"[Retrieval Process] Step 10: Execute recall diagnosis failed: {e}")
                debug_info['steps'].append({'step': 'recall_diagnostic_error', 'message': f"Execute recall diagnosis failed: {str(e)}"})

            has_results = len(results) > 0

            logger.info(f"[Retrieval Process] Step 10: Return {len(results)} documents, has_results: {has_results}")

            business_diagnosis = self._generate_business_diagnosis(results, recall_diagnosis, query, kb_id)

            debug_info['bm25_status'] = 'building' if bm25_degraded else 'ready'
            # White-box fields: whether the weights above the fused scores were renormalized for
            # this query, and which fusion weights produced the scores that follow.
            debug_info['route_degraded'] = route_degraded
            debug_info['effective_weights'] = {
                'bm25_weight': eff_bm25_weight,
                'vector_weight': eff_vector_weight
            }

            return {
                'results': results,
                'count': len(results),
                'has_results': has_results,
                'mode': mode,
                'empty_response': empty_response if not has_results else "",
                'scenario_id': scenario_id,
                'debug_info': debug_info,
                'recall_diagnosis': recall_diagnosis,
                'business_diagnosis': business_diagnosis,
                'bm25_status': 'building' if bm25_degraded else 'ready'
            }

        except Exception as e:
            logger.error(f"Retrieval failed: {e}", exc_info=True)
            debug_info['steps'].append({'step': 'error', 'message': f"Retrieval failed: {str(e)}"})
            return {
                'results': [],
                'count': 0,
                'has_results': False,
                'mode': mode,
                'empty_response': empty_response,
                'error': str(e),
                'scenario_id': scenario_id,
                'debug_info': debug_info
            }

    def _generate_business_diagnosis(self, results: List[Dict], recall_diagnosis: Dict, query: str, kb_id: str) -> Dict:
        """
        Generate business-oriented diagnosis conclusion

        Args:
            results: Retrieval result list
            recall_diagnosis: Non-recall diagnosis result
            query: User query
            kb_id: Knowledge base ID

        Returns:
            Business-oriented diagnosis conclusion
        """
        diagnosis = {
            'overall_status': 'healthy',
            'problem_summary': '',
            'root_cause': '',
            'suggestions': [],
            'confidence_level': 'high',
            'key_findings': []
        }

        if not results:
            diagnosis['overall_status'] = 'critical'
            diagnosis['problem_summary'] = 'No relevant documents found'
            diagnosis['root_cause'] = 'No relevant documents found in the knowledge base'
            diagnosis['suggestions'] = [
                'Check if documents are correctly uploaded to the knowledge base',
                'Confirm document content is relevant to your query',
                'Try using more specific keywords to refine your query'
            ]
            diagnosis['confidence_level'] = 'none'
            return diagnosis

        avg_similarity = 0
        quality_warnings = []
        doc_count = len(results)

        for result in results:
            score = result.get('score', 0)
            avg_similarity += score
            metadata = result.get('metadata', {})
            if metadata.get('parse_quality_warnings'):
                quality_warnings.extend(metadata['parse_quality_warnings'])

        avg_similarity = avg_similarity / doc_count if doc_count > 0 else 0

        if avg_similarity < 0.2:
            diagnosis['overall_status'] = 'critical'
            diagnosis['problem_summary'] = 'Document similarity with query is extremely low, document content may not match your query intent'
            diagnosis['root_cause'] = f'Average similarity is {avg_similarity:.1%} for documents in the knowledge base'
            diagnosis['confidence_level'] = 'very_low'
        elif avg_similarity < 0.4:
            diagnosis['overall_status'] = 'warning'
            diagnosis['problem_summary'] = 'Retrieval result similarity is low'
            diagnosis['root_cause'] = f'Average similarity is {avg_similarity:.1%} for documents in the knowledge base'
            diagnosis['confidence_level'] = 'low'
        elif avg_similarity < 0.6:
            diagnosis['overall_status'] = 'caution'
            diagnosis['confidence_level'] = 'medium'
        else:
            diagnosis['overall_status'] = 'healthy'
            diagnosis['confidence_level'] = 'high'

        if quality_warnings:
            unique_warnings = list(set(quality_warnings))
            diagnosis['key_findings'].append({
                'type': 'document_quality',
                'title': 'Document parse quality warnings',
                'description': f'Found {len(unique_warnings)} document parse warnings',
                'details': unique_warnings
            })
            diagnosis['suggestions'].append('Check document parse quality or re-upload documents')

        if recall_diagnosis and recall_diagnosis.get('enabled', False):
            summary = recall_diagnosis.get('summary', {})
            if summary.get('problem_description'):
                diagnosis['problem_summary'] = summary['problem_description']
            if summary.get('root_cause'):
                diagnosis['root_cause'] = summary['root_cause']
            if summary.get('actionable_suggestions'):
                diagnosis['suggestions'].extend(summary['actionable_suggestions'])

        if diagnosis['overall_status'] == 'healthy' and not diagnosis['problem_summary']:
            diagnosis['problem_summary'] = 'Retrieval results are healthy, can provide answers'

        if not diagnosis['suggestions']:
            if diagnosis['overall_status'] == 'healthy':
                diagnosis['suggestions'] = ['Use the results directly']
            else:
                diagnosis['suggestions'] = ['Check document content']

        diagnosis['key_findings'].append({
            'type': 'retrieval_summary',
            'title': 'Retrieval summary summary',
            'description': f'Found {doc_count} results, average similarity {avg_similarity:.1%}',
            'details': {
                'result_count': doc_count,
                'avg_similarity': round(avg_similarity, 2),
                'confidence_level': diagnosis['confidence_level']
            }
        })

        return diagnosis

    def refresh_bm25_index(self, kb_id: str):
        """
        Refresh BM25 index (called after knowledge base deletion, thoroughly clears cache and build status)

        Args:
            kb_id: Knowledge base ID
        """
        with self._bm25_lock:
            self._bm25_cache.pop(kb_id, None)
            self._bm25_docs_cache.pop(kb_id, None)
            self._bm25_build_version.pop(kb_id, None)
            self._bm25_building.discard(kb_id)
            self._bm25_build_threads.pop(kb_id, None)
        logger.info(f"BM25 index refreshed (cleared cache): {kb_id}")

    def get_retrieval_mode(self) -> str:
        """Get current retrieval mode"""
        return self.mode

    def set_retrieval_mode(self, mode: str):
        """
        Set retrieval mode

        Args:
            mode: Retrieval mode (vector/bm25/hybrid)
        """
        if mode in ['vector', 'bm25', 'hybrid']:
            self.mode = mode
            logger.info(f"Retrieval mode switched to: {mode}")
        else:
            raise ValueError(_('retrieve.invalid_mode', None, mode))