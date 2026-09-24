"""Sentence-level tracing: per-sentence provenance for a generated answer.

Two attribution routes, applied in this order (see ``SentenceTracer.trace``):

1. **citation** — the sentence carries a ``[documentN]`` / ``[Document N]`` / ``[N]``
   marker, emitted under prompt enforcement. If that ordinal maps onto a chunk
   that really reached the prompt, the sentence is attributed to that chunk and
   labelled ``citation_verified``. That is evidence, not a similarity score.
2. **similarity** — no marker, or a marker pointing outside the prompt: the
   sentence is embedded and matched against the retrieved chunks at two scales
   (see ``_prepare_chunk_index``). This verdict is a *heuristic*: it says "this
   sentence looks closest to chunk X", never "this claim is true". Thresholds
   combine model-independent absolute floors with the sentence's rank inside
   this answer's own score distribution.

When the embedding backend fails, a sentence is reported as ``unverified``
(``tracing_error=True``) instead of ``no_source``: a broken tracer must never be
presented to the user as a hallucination.

Sentence boundaries and character offsets come from ``service/text_split``, so
verdicts, evaluation metrics and the browser all agree on what "one sentence" is
and where it starts.
"""
import re
from typing import Dict, List, Optional

import numpy as np

from config import config
from service.i18n import _
from service.logger import get_logger
from service.text_split import CITATION_MARKER_RE, SentenceSpan, split_sentence_spans


logger = get_logger('sentence_tracing')


class SentenceTracer:
    """Sentence-level tracer with multi-scale semantic alignment.

    The original implementation compared each answer sentence against the
    full chunk embedding. That ignored a fundamental granularity mismatch:
    answer sentences (~15 tokens) and chunks (~200 tokens) live on different
    semantic scales, so cosine similarity between them is often a poor
    signal.

    This tracer now runs TWO complementary similarity signals:
      * fine-grained: answer sentence vs every sentence inside each chunk
        → catches direct quotes & near-verbatim reuse
      * coarse-grained: answer sentence vs the full chunk embedding
        → catches summary-style answers that paraphrase a chunk broadly

    The two are fused by max() so either signal can elevate confidence.
    Confidence thresholds are also made adaptive (percentile-based) so
    the classifier does not collapse when a different embedding model is
    swapped in.
    """

    def __init__(self):
        self.embedding_model = config.get_embedding_model()

        # Absolute floors: model-independent lower bounds a similarity verdict
        # must clear before it may claim anything (see _decide_confidence).
        self._abs_drift_floor = config.get('sentence_tracing.abs_drift_floor', 0.35)
        self._abs_summary_floor = config.get('sentence_tracing.abs_summary_floor', 0.55)
        self._abs_direct_quote_floor = config.get('sentence_tracing.abs_direct_quote_floor', 0.75)

        # Fine vs coarse weight in the final score. max() is resilient to
        # bad chunks — one strong signal is enough.
        self._fine_weight = config.get('sentence_tracing.fine_weight', 1.0)
        self._coarse_weight = config.get('sentence_tracing.coarse_weight', 0.8)

        # Relative-rank anchors. A long answer always contains sentences that are
        # "closest to some chunk", so an absolute floor alone over-claims: only
        # sentences high in this answer's own score distribution may be called a
        # direct quote.
        #
        # These anchors are deliberately NOT turned into percentile *thresholds* on
        # the score distribution: the distribution of per-sentence maxima is narrow
        # and high (every sentence is compared against chunks already selected for
        # relevance to the same query), so `max(percentile, floor)` used to
        # collapse onto the floor while looking adaptive.
        self._summary_percentile = config.get('sentence_tracing.summary_percentile', 0.75)
        self._direct_quote_percentile = config.get('sentence_tracing.direct_quote_percentile', 0.90)
        # Below this many sentences the ranks carry no information -> floors only.
        self._min_adaptive_samples = config.get('sentence_tracing.min_adaptive_samples', 3)

    def _get_embedding(self, text: str) -> List[float]:
        """Get text embedding vector, in the same space retrieval used.

        This used to embed through the LLM provider's adapter while taking the model name from
        the *embedding* provider. The two are configured independently, so any mixed setup sent
        one backend a model name it has never heard of (`nomic-embed-text` to the OpenAI API, or
        a local directory path to Ollama) and every similarity came back as 0. Going through the
        vector store guarantees the comparison is against the same vectors retrieval produced.
        """
        try:
            from core.vector_store import vector_store_manager
            return vector_store_manager.embed_texts([text])[0]
        except Exception as e:
            logger.error(f"Failed to get embedding: {e}", exc_info=True)
            return []

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity"""
        if not vec1 or not vec2:
            return 0.0
        try:
            v1 = np.array(vec1)
            v2 = np.array(vec2)
            dot = np.dot(v1, v2)
            norm = np.linalg.norm(v1) * np.linalg.norm(v2)
            if norm == 0:
                return 0.0
            return float(dot / norm)
        except Exception:
            return 0.0

    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences via the shared splitter (service/text_split).

        A local regex used to live here and split on a bare ``.``, which cut
        numbers in half ("weights are 0.4 and 0.6" -> "0", "4 and 0", "6") and
        made every downstream metric meaningless for numeric answers.
        """
        return [span.text for span in split_sentence_spans(text)]

    def _sentence_spans(self, text: str) -> List[SentenceSpan]:
        """Sentence spans (text + character offsets) for the given text."""
        return split_sentence_spans(text)

    def _extract_citations(self, text: str) -> List[str]:
        """Return the document ordinals a sentence cites (``[document2]`` -> ``'2'``)."""
        return [match.group(1) for match in CITATION_MARKER_RE.finditer(text or '')]

    def _build_citation_map(self, context_results: List[Dict]) -> Dict[str, str]:
        """Map the prompt's 1-based document ordinals onto chunk ids.

        The prompt numbers the context blocks "Document 1..N", so the ordinal the
        model cites *is* the position in ``context_results``. That is the same
        numbering ``LLMPipeline._validate_citations`` validates and the UI turns
        into clickable chips — which is what makes a sentence's citation
        resolvable to a real chunk instead of a similarity guess.
        """
        return {
            str(index): result.get('id')
            for index, result in enumerate(context_results, 1)
            if result.get('id')
        }

    # ------------------------------------------------------------------
    # Internal helpers for multi-scale alignment
    # ------------------------------------------------------------------

    def _prepare_chunk_index(self, context_results: List[Dict]) -> Dict:
        """Embed each chunk at TWO scales: the full chunk and each sentence inside it.

        Returns:
            {
                chunk_id: {
                    'embedding': [...],                     # full chunk embedding (coarse)
                    'text': original_chunk_text,
                    'metadata': {...},
                    'sentences': [                           # fine-grained, one per split
                        {'text': '...', 'embedding': [...]},
                        ...
                    ]
                }
            }
        """
        chunk_index = {}
        for chunk in context_results:
            chunk_id = chunk.get('id', str(chunk.get('metadata', {}).get('chunk_index', 0)))
            chunk_text = chunk.get('text', '')
            if not chunk_text:
                continue

            full_emb = self._get_embedding(chunk_text)
            fine_items = []
            for s in self._split_sentences(chunk_text):
                emb = self._get_embedding(s)
                if emb:
                    fine_items.append({'text': s, 'embedding': emb})

            if not full_emb and not fine_items:
                continue

            chunk_index[chunk_id] = {
                'embedding': full_emb, 'text': chunk_text,
                'metadata': chunk.get('metadata', {}), 'sentences': fine_items,
            }
        return chunk_index

    def _score_answer_sentence(self, sentence_emb: List[float], chunk_index: Dict) -> List[Dict]:
        """Compute per-chunk fused similarity (fine ∨ coarse) for one answer sentence.

        For each chunk:
            fine  = max similarity between answer sentence and any sentence inside the chunk
            coarse = similarity between answer sentence and the full chunk embedding
            fused  = max(fine * fine_weight, coarse * coarse_weight)

        Returns a list of dicts sorted by fused score desc.
        """
        ranked = []
        for cid, cdata in chunk_index.items():
            fine_max = 0.0
            fine_fragment = ''
            for s in cdata.get('sentences', []):
                sim = self._cosine_similarity(sentence_emb, s['embedding'])
                if sim > fine_max:
                    fine_max = sim
                    fine_fragment = s['text']

            coarse_sim = self._cosine_similarity(sentence_emb, cdata['embedding']) if cdata.get('embedding') else 0.0

            fine_weighted = fine_max * self._fine_weight
            coarse_weighted = coarse_sim * self._coarse_weight
            fused = max(fine_weighted, coarse_weighted)

            if fused == 0.0:
                continue

            match_type = 'fine' if fine_weighted >= coarse_weighted else 'coarse'
            ranked.append({
                'chunk_id': cid,
                'score': round(fused, 4),
                'fine_score': round(fine_max, 4),
                'coarse_score': round(coarse_sim, 4),
                'match_type': match_type,
                'matched_fragment': fine_fragment if match_type == 'fine' else '',
                'text_preview': cdata['text'][:100],
                'metadata': cdata['metadata'],
            })

        ranked.sort(key=lambda x: x['score'], reverse=True)
        return ranked

    def _decide_confidence(self, top1_score: float, all_top1_scores: List[float]) -> str:
        """Classify one similarity score: absolute floors + relative rank.

        Absolute floors alone over-claim, because every answer sentence sits
        close to *some* retrieved chunk (the context is the query-relevant
        subset, and the per-sentence score is the max over chunks). Relative rank
        alone is unstable on short answers. Using both means:

        * below ``abs_drift_floor`` -> ``drift``, whatever the rank;
        * ``direct_quote`` requires ``abs_direct_quote_floor`` *and* the top
          ``1 - direct_quote_percentile`` of this answer's own scores;
        * ``summary`` requires ``abs_summary_floor`` and the top quartile;
        * everything else is ``low_confidence``.

        With fewer than ``min_adaptive_samples`` sentences the ranks carry no
        information, so the floors decide alone.

        The verdict stays a *similarity heuristic*: the UI labels it as one. Only
        ``citation_verified`` (a resolvable ``[文档N]`` marker) claims evidence.
        """
        if top1_score is None or top1_score <= 0.0 or not all_top1_scores:
            return 'no_source'

        sample_size = len(all_top1_scores)
        if sample_size < self._min_adaptive_samples:
            return self._absolute_level(top1_score)

        # Position of this sentence among the answer's own scores: 1.0 means "strictly
        # the best sentence of this answer", and it drops as more sentences score at
        # least as high. A percentile of the score distribution was used before, but
        # that promotes every sentence when all scores tie (typical for a tiny
        # knowledge base: 1 - epsilon of the distribution is still "the top"), which is
        # exactly the over-claiming this rule exists to prevent.
        rank_ratio = (sum(1 for score in all_top1_scores if score < top1_score) + 1) / sample_size

        if top1_score < self._abs_drift_floor:
            return 'drift'
        if (top1_score >= self._abs_direct_quote_floor
                and rank_ratio >= self._direct_quote_percentile):
            return 'direct_quote'
        if top1_score >= self._abs_summary_floor and rank_ratio >= self._summary_percentile:
            return 'summary'
        return 'low_confidence'

    def _absolute_level(self, top1_score: float) -> str:
        """Floors-only verdict (short answers / streaming with partial ranks)."""
        if top1_score < self._abs_drift_floor:
            return 'drift'
        if top1_score >= self._abs_direct_quote_floor:
            return 'direct_quote'
        if top1_score >= self._abs_summary_floor:
            return 'summary'
        return 'low_confidence'

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def trace(self, answer: str, context_results: List[Dict], lang: Optional[str] = None,
              citation_map: Optional[Dict[str, str]] = None) -> List[Dict]:
        """Trace every sentence of ``answer`` against the retrieved context.

        Attribution is citation-first (see the module docstring): a sentence that
        carries a resolvable ``[文档N]`` marker is attributed to that chunk and
        labelled ``citation_verified``; only sentences without a usable marker go
        through the similarity heuristic.

        Args:
            answer: Final answer text.
            context_results: Chunks actually placed in the prompt, in prompt order
                (document 1..N).
            lang: i18n language.
            citation_map: Optional ordinal -> chunk id map. Built from
                ``context_results`` when omitted, so the numbering always matches
                the prompt the model saw.

        Returns:
            One dict per sentence. Fields marked ``(new)`` are additive:
            {
                'sentence': str,
                'start': int, 'end': int, 'index': int,        # (new) offsets
                'sources': [ {chunk_id, score, text_preview, metadata, fine_score,
                              coarse_score, match_type, matched_fragment, cited} ],
                'confidence_level': 'citation_verified'|'direct_quote'|'summary'
                                    |'low_confidence'|'drift'|'no_source'|'unverified',
                'attribution': 'citation'|'similarity'|'none',  # (new)
                'citation_markers': [str],                      # (new)
                'invalid_citation_markers': [str],              # (new)
                'similarity_level': str|None,                   # (new)
                'tracing_error': bool,                          # (new)
                'is_drift': bool,
                'drift_reason': str,
                'citations': [str]        # resolved markers (kept for compatibility)
            }
        """
        if not answer or not context_results:
            return []

        spans = self._sentence_spans(answer)
        if not spans:
            return []

        if citation_map is None:
            citation_map = self._build_citation_map(context_results)

        chunk_index = self._prepare_chunk_index(context_results)
        if not chunk_index:
            # Every chunk failed to embed: report "cannot verify", never "hallucination".
            logger.warning("Sentence tracing: every chunk failed to embed, marking all sentences unverified")
            return [
                self._unverified_payload(
                    span.text, lang, start=span.start, end=span.end, index=index,
                )
                for index, span in enumerate(spans)
            ]

        # Pass 1: score every sentence; the distribution feeds the relative ranks.
        scored = []
        for span in spans:
            sentence_embedding = self._get_embedding(span.text)
            ranks = self._score_answer_sentence(sentence_embedding, chunk_index) if sentence_embedding else []
            scored.append((span, sentence_embedding, ranks))

        all_top1 = [ranks[0]['score'] for _, _, ranks in scored if ranks]

        # Pass 2: classify through the shared primitive (identical to streaming).
        tracing_results = []
        for index, (span, sentence_embedding, ranks) in enumerate(scored):
            tracing_results.append(self._classify_sentence(
                span.text, chunk_index, ranks, all_top1, citation_map, lang,
                sentence_emb=sentence_embedding,
                start=span.start, end=span.end, index=index,
                embed_failed=not sentence_embedding,
            ))

        logger.debug(
            f"Sentence tracing completed (multi-scale): total {len(tracing_results)} sentences, "
            f"citation-verified {sum(1 for r in tracing_results if r['attribution'] == 'citation')}, "
            f"drift {sum(1 for r in tracing_results if r['is_drift'])}, "
            f"unverified {sum(1 for r in tracing_results if r['tracing_error'])}"
        )
        return tracing_results

    # ------------------------------------------------------------------
    # Reusable single-sentence variant (used by streaming incremental trace)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Color / label helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Sentence classification — the single primitive both tracers share
    # ------------------------------------------------------------------

    def _classify_sentence(self, sentence: str, chunk_index: Dict, ranks: List[Dict],
                           all_top1_scores: List[float],
                           citation_map: Optional[Dict[str, str]],
                           lang: Optional[str] = None,
                           sentence_emb: Optional[List[float]] = None,
                           start: Optional[int] = None, end: Optional[int] = None,
                           index: Optional[int] = None,
                           embed_failed: bool = False) -> Dict:
        """Build the provenance payload for ONE sentence.

        ``trace()`` (batch) and ``trace_single()`` (streaming) both delegate here.
        They used to hold separate copies of this logic, which is exactly how the
        live badges and the final trace drifted apart.

        Attribution order:

        1. a resolvable ``[文档N]`` marker -> ``citation_verified``, citing chunks
           first (scored directly when the chunk is not among the similarity
           candidates);
        2. otherwise the similarity verdict of ``_decide_confidence``.

        ``similarity_level`` always carries the similarity-only verdict, so a
        citation-verified sentence still shows how close it actually is.
        """
        markers = self._extract_citations(sentence)
        map_ = citation_map or {}

        resolved_markers: List[str] = []
        cited_chunk_ids: List[str] = []
        invalid_markers: List[str] = []
        for marker in markers:
            chunk_id = map_.get(marker)
            if chunk_id and chunk_id in chunk_index:
                resolved_markers.append(marker)
                if chunk_id not in cited_chunk_ids:
                    cited_chunk_ids.append(chunk_id)
            elif marker not in invalid_markers:
                invalid_markers.append(marker)

        if embed_failed:
            return self._unverified_payload(
                sentence, lang, start=start, end=end, index=index,
                citation_markers=markers, invalid_citation_markers=invalid_markers,
            )

        ranks = ranks or []
        similarity_level = None
        if ranks:
            similarity_level = self._decide_confidence(
                ranks[0]['score'], all_top1_scores or [ranks[0]['score']]
            )

        payload = {
            'sentence': sentence,
            'start': start,
            'end': end,
            'index': index,
            'citation_markers': markers,
            'invalid_citation_markers': invalid_markers,
            'similarity_level': similarity_level,
            'tracing_error': False,
            'citations': resolved_markers,
        }

        if cited_chunk_ids:
            sources = self._citation_sources(cited_chunk_ids, chunk_index, ranks, sentence_emb)
            if sources:
                payload.update({
                    'sources': sources,
                    'confidence_level': 'citation_verified',
                    'attribution': 'citation',
                    'is_drift': False,
                    'drift_reason': '',
                })
                return payload

        if not ranks:
            payload.update({
                'sources': [],
                'confidence_level': 'no_source',
                'attribution': 'none',
                'is_drift': True,
                'drift_reason': _('trace.drift_no_source', lang),
            })
            return payload

        level = similarity_level or 'no_source'
        is_drift = level in ('drift', 'no_source')
        reason = ''
        if level in ('drift', 'no_source', 'low_confidence'):
            reason = _('trace.drift_low_similarity', lang, f"{ranks[0]['score']:.2f}")

        payload.update({
            'sources': ranks[:3],
            'confidence_level': level,
            'attribution': 'similarity',
            'is_drift': is_drift,
            'drift_reason': reason,
        })
        return payload

    def _citation_sources(self, cited_chunk_ids: List[str], chunk_index: Dict,
                          ranks: List[Dict], sentence_emb: Optional[List[float]] = None,
                          limit: int = 3) -> List[Dict]:
        """Sources for a cited sentence: the cited chunks first, with real scores.

        A cited chunk may be missing from ``ranks`` (its similarity can be 0), but
        it is still the chunk the model pointed at, so it is scored directly here
        instead of being dropped — otherwise a correct citation to a
        low-similarity chunk would look unsupported.
        """
        by_id = {entry.get('chunk_id'): entry for entry in (ranks or [])}
        sources: List[Dict] = []
        for chunk_id in cited_chunk_ids:
            entry = by_id.get(chunk_id)
            if entry is None and sentence_emb and chunk_id in chunk_index:
                scored = self._score_answer_sentence(sentence_emb, {chunk_id: chunk_index[chunk_id]})
                entry = scored[0] if scored else None
            if entry is None:
                chunk_data = chunk_index.get(chunk_id, {})
                entry = {
                    'chunk_id': chunk_id,
                    'score': 0.0,
                    'fine_score': 0.0,
                    'coarse_score': 0.0,
                    'match_type': 'citation',
                    'matched_fragment': '',
                    'text_preview': (chunk_data.get('text') or '')[:100],
                    'metadata': chunk_data.get('metadata', {}),
                }
            entry = dict(entry)
            entry['cited'] = True
            sources.append(entry)

        for entry in (ranks or []):
            if len(sources) >= limit:
                break
            if entry.get('chunk_id') in cited_chunk_ids:
                continue
            sources.append(entry)
        return sources[:limit]

    def _unverified_payload(self, sentence: str, lang: Optional[str] = None,
                            start: Optional[int] = None, end: Optional[int] = None,
                            index: Optional[int] = None,
                            citation_markers: Optional[List[str]] = None,
                            invalid_citation_markers: Optional[List[str]] = None) -> Dict:
        """Payload for a sentence the tracer could not evaluate at all.

        Deliberately distinct from ``no_source``: when the embedding backend is
        down, reporting the sentence as unsupported would blame the model for a
        tooling failure. ``tracing_error=True`` lets the UI say "cannot verify"
        instead of "hallucination".
        """
        return {
            'sentence': sentence,
            'start': start,
            'end': end,
            'index': index,
            'sources': [],
            'confidence_level': 'unverified',
            'attribution': 'none',
            'citation_markers': citation_markers or [],
            'invalid_citation_markers': invalid_citation_markers or [],
            'similarity_level': None,
            'tracing_error': True,
            'is_drift': False,
            'drift_reason': _('trace.drift_embedding_failed', lang),
            'citations': [],
        }

    def build_chunk_index(self, context_results: List[Dict]) -> Dict:
        """Public alias of _prepare_chunk_index for external callers (streaming)."""
        return self._prepare_chunk_index(context_results)

    def build_citation_map(self, context_results: List[Dict]) -> Dict[str, str]:
        """Public accessor for the prompt's ordinal -> chunk id map (see above)."""
        return self._build_citation_map(context_results)

    # ------------------------------------------------------------------
    # Reusable single-sentence variant (used by streaming incremental trace)
    # ------------------------------------------------------------------

    def trace_single(self, sentence: str, chunk_index: Dict, lang: Optional[str] = None,
                     all_top1_for_adaptive: Optional[List[float]] = None,
                     citation_map: Optional[Dict[str, str]] = None,
                     start: Optional[int] = None, end: Optional[int] = None,
                     index: Optional[int] = None) -> Optional[Dict]:
        """Trace ONE answer sentence through a pre-built chunk index.

        Delegates to :meth:`_classify_sentence`, the very same primitive
        :meth:`trace` uses, so the incremental badges shown while streaming cannot
        disagree with the final trace.

        Args:
            sentence: One answer sentence (already stripped).
            chunk_index: Output of :meth:`build_chunk_index`.
            lang: i18n language.
            all_top1_for_adaptive: Top-1 scores accumulated so far. The current
                sentence's own score is added to a local copy before deciding, so
                the relative-rank part of the verdict matches the batch tracer.
            citation_map: Ordinal -> chunk id map (see :meth:`_build_citation_map`).
            start: Character offset of the sentence in the full answer text.
            end: Offset just past the sentence's last character.
            index: Optional 0-based sentence index (hint for the UI; the client
                attributes by ``start``/``end``).

        Returns:
            The per-sentence provenance payload, or ``None`` when there is nothing
            to trace (empty sentence / empty chunk index).
        """
        if not sentence or not chunk_index:
            return None

        sentence_embedding = self._get_embedding(sentence)
        ranks = self._score_answer_sentence(sentence_embedding, chunk_index) if sentence_embedding else []

        running_scores = list(all_top1_for_adaptive or [])
        if ranks:
            running_scores.append(ranks[0]['score'])

        return self._classify_sentence(
            sentence, chunk_index, ranks, running_scores, citation_map, lang,
            sentence_emb=sentence_embedding, start=start, end=end, index=index,
            embed_failed=not sentence_embedding,
        )

    # ------------------------------------------------------------------
    # Color / label helpers
    # ------------------------------------------------------------------

    def get_confidence_color(self, level: str) -> str:
        """Get confidence color marker"""
        colors = {
            'citation_verified': '#0d9488',
            'direct_quote': '#22c55e',
            'summary': '#eab308',
            'low_confidence': '#f97316',
            'drift': '#ef4444',
            'no_source': '#ef4444',
            'unverified': '#64748b',
            'unknown': '#6b7280',
        }
        return colors.get(level, '#6b7280')

    def get_confidence_label(self, level: str, lang: Optional[str] = None) -> str:
        """Get confidence label"""
        labels = {
            'citation_verified': _('trace.citation_citation_verified', lang),
            'direct_quote': _('trace.citation_direct_quote', lang),
            'summary': _('trace.citation_summary', lang),
            'low_confidence': _('trace.citation_low_confidence', lang),
            'drift': _('trace.citation_drift', lang),
            'no_source': _('trace.citation_no_source', lang),
            'unverified': _('trace.citation_unverified', lang),
        }
        return labels.get(level, _('trace.citation_unknown', lang))

    def analyze_drift(self, tracing_results: List[Dict], lang: Optional[str] = None) -> Dict:
        """Aggregate the per-sentence verdicts of one answer.

        ``unverified`` sentences (the tracer could not evaluate them) are counted
        separately and stay OUT of ``drift_count`` / ``drift_rate``: an embedding
        outage or a missing context must not be reported as a hallucination.
        ``verified_drift_rate`` offers the same ratio over evaluated sentences only.
        """
        results = tracing_results or []
        total_sentences = len(results)

        def count_level(level: str) -> int:
            return sum(1 for r in results if r.get('confidence_level') == level)

        drift_count = sum(1 for r in results if r.get('is_drift'))
        unverified_count = sum(1 for r in results if r.get('tracing_error'))
        citation_verified_count = count_level('citation_verified')
        direct_quote_count = count_level('direct_quote')
        summary_count = count_level('summary')
        low_confidence_count = count_level('low_confidence')
        verified_count = total_sentences - unverified_count

        return {
            'total_sentences': total_sentences,
            'drift_count': drift_count,
            # Alias: the log line and the UI term map have always read this name.
            'drifted_count': drift_count,
            'direct_quote_count': direct_quote_count,
            'summary_count': summary_count,
            'low_confidence_count': low_confidence_count,
            'citation_verified_count': citation_verified_count,
            'unverified_count': unverified_count,
            'verified_count': verified_count,
            'drift_rate': drift_count / total_sentences if total_sentences > 0 else 0,
            'verified_drift_rate': drift_count / verified_count if verified_count > 0 else 0,
            'unverified_rate': unverified_count / total_sentences if total_sentences > 0 else 0,
            'drift_sentences': [r for r in results if r.get('is_drift')],
            'unverified_sentences': [r for r in results if r.get('tracing_error')],
            # "solid" for the UI: attributed to the context, by a resolved citation
            # or by the similarity heuristic.
            'high_confidence_rate': (
                (citation_verified_count + direct_quote_count + summary_count) / total_sentences
                if total_sentences > 0 else 0
            ),
        }

    def detect_contradictions(self, answer: str, context_results: List[Dict],
                              lang: Optional[str] = None) -> List[Dict]:
        """Report statements where the answer disagrees with the retrieved context.

        Backward-compatible entry point: the implementation moved to
        ``core/contradiction.py``, which compares *claims* (numbers, amounts, durations,
        dates, polarity phrases) structurally instead of matching substrings, reads its
        thresholds from the ``contradiction:`` block of settings.yaml, and now also runs on
        the normal Q&A path so a trace can flag the sentence itself.

        Args:
            answer: Final answer text.
            context_results: Chunks that were placed in the prompt.
            lang: i18n language for the explanation strings.

        Returns:
            Contradiction dicts (see ``core/contradiction.py`` for the field list).
        """
        from core.contradiction import contradiction_detector
        return contradiction_detector.detect(answer, context_results, lang=lang)

sentence_tracer = SentenceTracer()