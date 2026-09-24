"""
LLM Pipeline Module
Q&A pipeline based on unified LLM adapter, supports streaming output and tracing
Supports scenario-based prompt template loading
Supports enforced citation format and sentence-level tracing
"""
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from config import config, scenario_config
from core.contradiction import contradiction_detector
from core.llm_adapter import LLMAdapterFactory
from core.retriever import get_retrieval_defaults
from core.trace import trace_manager
from core.sentence_tracing import sentence_tracer
from service.i18n import _
from service.logger import get_logger
from service.path_safety import safe_join
from service.text_split import split_complete_sentences, split_sentence_spans

logger = get_logger('llm_pipeline')


def _disclaimer_pattern(disclaimer: str):
    """Regex matching a disclaimer with any whitespace between its words.

    An injected disclaimer is echoed back by the model with slightly different line breaks (a
    trailing space before the newline, one blank line instead of two), so an exact substring
    match does not see the duplicate and the answer shows the two opening lines twice.

    Args:
        disclaimer: Localized disclaimer text

    Returns:
        Compiled pattern, or None when there is nothing to match.
    """
    stripped = (disclaimer or '').strip()
    if not stripped:
        return None
    return re.compile(r'\s+'.join(re.escape(part) for part in stripped.split()))


class _LeadingDuplicateFilter:
    """Drop a leading copy of an already-injected prefix while the answer streams.

    Hybrid answer mode injects the disclaimer before the first token, and the prompt asks the
    model to open with those same two lines, so it usually repeats them (the stored traces show
    the prefix twice in the final answer). Patching the finished answer cannot fix what the user
    reads - the duplicate has already been streamed - and deleting it afterwards would shift
    every sentence offset the incremental provenance reported. So the leading characters are held
    back until they can be classified, then either dropped or released verbatim.
    """

    def __init__(self, prefix: str):
        # Whitespace-insensitive needle: the model's copy differs in line breaks.
        self._needle = ''.join((prefix or '').split())
        self._buffer = ''
        self._matched = 0     # non-whitespace characters of the buffer matched so far
        self._cursor = 0      # raw scan position inside the buffer
        self._decided = not self._needle

    def feed(self, text: str) -> str:
        """Return the text allowed to reach the client ('' while still undecided).

        Args:
            text: Next piece of the model's output

        Returns:
            The piece to forward, possibly empty.
        """
        if self._decided:
            return text
        self._buffer += text
        while self._cursor < len(self._buffer):
            char = self._buffer[self._cursor]
            if char.isspace():
                self._cursor += 1
                continue
            if self._matched >= len(self._needle) or char != self._needle[self._matched]:
                return self._release()
            self._matched += 1
            self._cursor += 1
            if self._matched == len(self._needle):
                # The whole disclaimer was repeated: drop it and keep the rest of this piece.
                remainder = self._buffer[self._cursor:]
                self._buffer = ''
                self._decided = True
                return remainder.lstrip()
        return ''

    def flush(self) -> str:
        """Release what is still held back when the stream ended without a verdict."""
        return '' if self._decided else self._release()

    def _release(self) -> str:
        """Forward the held-back text untouched (it was not a duplicate after all)."""
        self._decided = True
        held, self._buffer = self._buffer, ''
        return held


class LLMPipeline:
    """LLM Q&A pipeline"""

    def __init__(self, retriever):
        """
        Initialize LLM pipeline

        Args:
            retriever: Hybrid retriever instance
        """
        self.retriever = retriever
        self.llm_model = config.get_llm_model()

        self.max_retries = config.get('ollama.max_retries', 3)

        self.trace_enabled = config.get('trace.enabled', True)
        self.trace_dir = Path(config.get('trace.trace_directory', './storage/traces'))
        self.trace_dir.mkdir(parents=True, exist_ok=True)

        self.evaluation_enabled = config.get('evaluation.enabled', True)

        self._llm_adapter = LLMAdapterFactory.get_adapter()
        self._evaluator = None

    def _get_evaluator(self):
        """Get evaluator (lazy loading)"""
        if self._evaluator is None and self.evaluation_enabled:
            try:
                from core.evaluator import RAGEvaluator
                self._evaluator = RAGEvaluator()
                logger.info("Evaluator initialized")
            except Exception as e:
                logger.error(f"Evaluator initialization failed: {e}")
                self.evaluation_enabled = False
        return self._evaluator

    def _get_llm_adapter(self):
        """Get LLM adapter"""
        return self._llm_adapter

    def _build_prompt(self, query: str, context_results: List[Dict], scenario_id: Optional[str] = None, retrieval_quality: str = 'high', lang: Optional[str] = None) -> str:
        """
        Build RAG prompt

        Args:
            query: User question
            context_results: Retrieved context
            scenario_id: Scenario ID, used to load scenario-specific prompt template
            retrieval_quality: Retrieval quality level ('high' normal RAG / 'low' low relevance triggers hybrid answer mode)
            lang: Language code

        Returns:
            Complete prompt
        """
        system_prompt = self._get_system_prompt(scenario_id, lang=lang)

        # ===== Hybrid answer mode: switch prompt template when retrieval quality is low =====
        if retrieval_quality == 'low':
            hybrid_system_prompt = _('pipeline.system_prompt_hybrid', lang)
            return f"""{hybrid_system_prompt}

【User Question】
{query}

【Please follow the above format strictly to answer】"""

        # ===== Normal RAG mode =====
        if not context_results:
            return f"""{system_prompt}

【User Question】
{query}"""

        context_parts = []
        for i, result in enumerate(context_results, 1):
            metadata = result.get('metadata', {})
            file_name = metadata.get('file_name', _('pipeline.unknown_doc', lang))
            score = result.get('score', 0)

            context_parts.append(f"""【Document {i}】Source: {file_name} (Score: {score:.2f})
Content: {result['text']}""")

        context_text = "\n\n".join(context_parts)

        prompt = f"""{system_prompt}

【Reference Documents】
{context_text}

【User Question】
{query}

【Please answer the question】"""

        return prompt

    def _get_system_prompt(self, scenario_id: Optional[str] = None, lang: Optional[str] = None) -> str:
        """
        Get system prompt (supports scenario-based loading)

        Args:
            scenario_id: Scenario ID
            lang: Language code

        Returns:
            System prompt
        """
        if scenario_id is None:
            return _('pipeline.system_prompt_default', lang)

        effective_config = scenario_config.get_effective_config(scenario_id)
        scenario_prompt = effective_config.get('llm_pipeline', {}).get('system_prompt')

        if scenario_prompt:
            logger.debug(f"Loading scenario prompt template: {scenario_id}")
            return scenario_prompt

        logger.warning(f"Scenario {scenario_id} has no system prompt configured, using default prompt")
        return self._get_system_prompt(None, lang=lang)

    def _save_trace(self, trace_id: str, data: Dict):
        """Save tracing record"""
        if not self.trace_enabled:
            return

        try:
            # trace_id may come from the request body: keep it a single path segment so a
            # crafted id cannot write outside the trace directory (see core/trace.py).
            trace_file = safe_join(self.trace_dir, f'{trace_id}.json')
            with open(trace_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save trace record: {e}")

    def get_trace(self, trace_id: str) -> Optional[Dict]:
        """
        Get tracing record

        Args:
            trace_id: Tracing ID

        Returns:
            Tracing record dictionary
        """
        # trace_id arrives from the URL path: keep it a single path segment so this cannot
        # become an arbitrary *.json file read (same guard as core/trace.py load_trace).
        trace_file = safe_join(self.trace_dir, f'{trace_id}.json')
        if trace_file.exists():
            try:
                with open(trace_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to read trace record: {e}")
        return None

    def query(self, kb_id: str, query: str, stream: bool = False, trace_id: str = None, scenario_id: Optional[str] = None, lang: Optional[str] = None, **kwargs) -> Dict:
        """
        Q&A main interface (non-streaming)

        Args:
            kb_id: Knowledge base ID
            query: User question
            stream: Whether to stream output
            trace_id: Optional, tracing ID
            scenario_id: Scenario ID, used to load scenario-specific config
            **kwargs: Retrieval parameters (mode, bm25_weight, similarity_threshold, top_k, etc.)

        Returns:
            Answer result dictionary
        """
        if trace_id is None:
            trace_id = str(uuid.uuid4())

        start_time = time.time()
        logger.info(f"[Response Process] Start Q&A, KB ID: {kb_id}, Query: {query[:50]}..., Scenario: {scenario_id or 'Default'}")

        empty_response = self._get_empty_response(scenario_id, lang=lang)

        trace = trace_manager.create_trace(kb_id, query, scenario_id)
        trace.trace_id = trace_id

        # 0. Intent classification
        intent_info = None
        try:
            from core.intent_classifier import intent_classifier
            logger.info(f"[Response Process] Start intent classification, Query: {query[:50]}...")
            intent_info = intent_classifier.classify(query, lang=lang)
            trace.set_intent_info(intent_info)
            trace.add_stage('intent_classification', {'query': query}, intent_info)
            logger.info(f"[Response Process] Intent classification completed, Intent type: {intent_info.get('intent_type')}, Confidence: {intent_info.get('confidence', 0):.2f}")
        except Exception as e:
            logger.warning(f"[Response Process] Intent classification failed: {e}")

        # 0.5 Business scope boundary detection
        boundary_result = None
        try:
            from core.boundary_detector import boundary_detector
            logger.info(f"[Response Process] Start boundary detection, Query: {query[:50]}...")
            boundary_result = boundary_detector.detect(query, lang=lang)
            trace.add_stage('boundary_detection', {'query': query}, boundary_result)

            if not boundary_result.get('in_domain', True):
                logger.warning(f"[Response Process] Boundary detection rejected, Reason: {boundary_result.get('reason')}, Confidence: {boundary_result.get('confidence', 0):.2f}")
                duration = round(time.time() - start_time, 2)
                trace.mark_complete('boundary_rejected')
                # Callers index context/has_results/retrieval_mode unconditionally, so the
                # early return has to carry the same keys as a normal response.
                return {
                    'answer': boundary_result.get('suggestion', empty_response),
                    'sources': [],
                    'trace_id': trace_id,
                    'duration': duration,
                    'intent_info': intent_info,
                    'boundary_result': boundary_result,
                    'context': [],
                    'context_count': 0,
                    'has_results': False,
                    'retrieval_mode': 'boundary_rejected',
                    'status': 'boundary_rejected'
                }
            logger.info(f"[Response Process] Boundary detection passed, Confidence: {boundary_result.get('confidence', 0):.2f}")
        except Exception as e:
            # exc_info so a genuine bug in the detector is visible instead of looking like a
            # routine "detector unavailable" skip.
            logger.warning(f"[Response Process] Boundary detection failed, continue execution: {e}", exc_info=True)

        # 1. Retrieve context (pass through retrieval parameters)
        logger.info(f"[Response Process] Start context retrieval, KB ID: {kb_id}, Scenario: {scenario_id or 'Default'}, Parameters: {kwargs}")
        retrieval_result = self.retriever.retrieve(kb_id, query, scenario_id=scenario_id, **kwargs)
        context_results = retrieval_result['results']
        logger.info(f"[Response Process] Context retrieval completed, Mode: {retrieval_result['mode']}, Returns {retrieval_result['count']} results, Has results: {retrieval_result['has_results']}")
        trace.add_stage('retrieval', {'query': query}, {
            'count': retrieval_result['count'],
            'mode': retrieval_result['mode'],
            'has_results': retrieval_result['has_results']
        }, retrieval_result.get('debug_info', {}))

        # ===== Suggestion A: retrieval quality secondary validation =====
        # Empty results / low scores are all classified as low, forcing hybrid answer mode (white-box RAG core principle: do not fabricate content not in knowledge base)
        # Threshold description: <0.4 -> weak relevance (should use hybrid answer); >=0.4 -> true match (normal RAG)
        retrieval_quality = 'low'   # Default is low, only upgraded to high when retrieval results exist and average score > 0.4
        avg_retrieval_score = 0.0
        if context_results:
            avg_retrieval_score = sum(r.get('score', 0) for r in context_results) / len(context_results)
            if avg_retrieval_score > 0.4:
                retrieval_quality = 'high'
            else:
                logger.warning(f"[Response Process] Retrieval quality low (avg score ≤ 0.4), enabling hybrid answer mode")
        else:
            logger.warning("[Response Process] Retrieval results empty, enabling hybrid answer mode (no knowledge base match)")
        logger.info(f"[Response Process] Retrieval quality check: {retrieval_quality} {avg_retrieval_score:.4f} {len(context_results)}")

        # 2. Build prompt (switch template based on retrieval quality)
        logger.info(f"[Response Process] Start prompt building, Context count: {len(context_results)}, Retrieval quality: {retrieval_quality}")
        prompt = self._build_prompt(query, context_results, scenario_id, retrieval_quality=retrieval_quality, lang=lang)
        logger.info(f"[Response Process] Prompt building completed, Length: {len(prompt)} characters")

        # 3. Call LLM (with circuit breaker protection)
        answer = ""
        error = None

        try:
            from core.circuit_breaker import circuit_breaker_manager, CircuitBreakerError

            adapter = self._get_llm_adapter()
            breaker = circuit_breaker_manager.get_breaker('llm_service')
            logger.info(f"[Response Process] Start LLM call, Model: {self.llm_model}, Max retries: {self.max_retries}, Circuit breaker status: {breaker.get_status()['state']}")

            def llm_call():
                for attempt in range(self.max_retries):
                    try:
                        llm_start_time = time.time()
                        response = adapter.chat(
                            model=self.llm_model,
                            messages=[
                                {'role': 'user', 'content': prompt}
                            ],
                            stream=False
                        )
                        llm_duration = round(time.time() - llm_start_time, 2)
                        logger.info(f"[Response Process] LLM call successful, Duration: {llm_duration}s, Answer length: {len(response.get('message', {}).get('content', ''))} characters")
                        return response
                    except Exception as e:
                        if attempt < self.max_retries - 1:
                            logger.warning(f"[Response Process] LLM call failed, retry {attempt + 1}/{self.max_retries}: {e}")
                            time.sleep(1)
                        else:
                            raise

            response = breaker.call(llm_call)
            answer = response.get('message', {}).get('content', '')

        except CircuitBreakerError as e:
            error = str(e)
            logger.error(f"[Response Process] LLM circuit breaker is open: {e}")
            answer = _('pipeline.circuit_busy', lang)
        except Exception as e:
            error = str(e)
            logger.error(f"[Response Process] LLM call failed: {e}", exc_info=True)
            if retrieval_result['has_results']:
                answer = _('pipeline.llm_unavailable', lang)
            else:
                answer = retrieval_result.get('empty_response', empty_response)
            logger.info(f"[Response Process] Use fallback answer: {answer[:50]}...")

        # ===== Forced post-processing: in hybrid answer mode, ensure answer starts with disclaimer opening =====
        if retrieval_quality == 'low' and answer:
            answer = self._ensure_hybrid_format(answer, lang=lang)

        trace.add_stage('generation', {'prompt': prompt[:200] + '...' if len(prompt) > 200 else prompt}, {'answer': answer[:200] + '...' if len(answer) > 200 else answer})

        # 4. Sentence-level tracing
        sentence_tracing = None
        drift_analysis = None
        try:
            logger.info(f"[Response Process] Start sentence-level tracing, Answer length: {len(answer)}, Context count: {len(context_results)}")
            sentence_tracing = sentence_tracer.trace(answer, context_results, lang=lang)
            drift_analysis = sentence_tracer.analyze_drift(sentence_tracing, lang=lang)
            trace.set_sentence_tracing(sentence_tracing)
            trace.add_stage('sentence_tracing', {'answer': answer}, drift_analysis)
            logger.info(f"[Response Process] Sentence tracing completed, Sentence count: {len(sentence_tracing)}, Drift rate: {drift_analysis.get('drift_rate', 0):.2f}, Drifted sentence count: {drift_analysis.get('drift_count', 0)}, Unverified: {drift_analysis.get('unverified_count', 0)}, Citation-verified: {drift_analysis.get('citation_verified_count', 0)}")
        except Exception as e:
            logger.warning(f"[Response Process] Sentence tracing failed: {e}")

        # 4.5 Contradiction check (numbers / amounts / dates / polarity vs the context)
        contradictions = self._detect_contradictions(answer, context_results, sentence_tracing, lang)
        trace.set_contradictions(contradictions)

        # 5. Evaluation (performed in non-streaming mode)
        evaluation_result = None
        if self.evaluation_enabled:
            try:
                evaluator = self._get_evaluator()
                if evaluator:
                    logger.info(f"[Response Process] Start evaluation, evaluator type: {type(evaluator).__name__}")
                    evaluation_result = evaluator.evaluate(query, context_results, answer, scenario_id, boundary_result=boundary_result, lang=lang)
                    logger.info(f"[Response Process] Evaluation completed, overall_score: {evaluation_result.get('overall_score', 0):.3f}, is_passing: {evaluation_result.get('is_passing')}, Scores: {evaluation_result.get('scores', {})}")
            except Exception as e:
                logger.error(f"[Response Process] Evaluation failed: {e}")
        else:
            logger.info(f"[Response Process] Evaluation disabled")

        # 6. Citation validation
        citation_validation = None
        try:
            logger.info(f"[Response Process] Start citation validation, Answer length: {len(answer)}")
            citation_validation = self._validate_citations(answer, context_results, lang=lang)
            trace.add_stage('citation_validation', {'answer': answer}, citation_validation)
            if not citation_validation.get('has_citations', True):
                logger.warning(f"[Response Process] Citation validation failed, answer does not contain citation markers: {citation_validation.get('suggestion', '')}")
            else:
                logger.info(f"[Response Process] Citation validation passed, detected {len(citation_validation.get('found_citations', []))} citation markers")
        except Exception as e:
            logger.warning(f"[Response Process] Citation validation failed: {e}")

        trace.set_evaluation(evaluation_result)
        trace.set_final_answer(answer)
        trace.set_error(error)
        trace_manager.save_trace(trace)

        # 6. Record tracing
        end_time = time.time()
        duration = round(end_time - start_time, 2)

        logger.info(f"[Response Process] Question completed in {duration}s, retrieved {retrieval_result['count']} results, evaluation score: {evaluation_result.get('overall_score', 0) if evaluation_result else 0:.3f}, trace_id: {trace_id}")

        return {
            'trace_id': trace_id,
            'answer': answer,
            'context': context_results,
            'retrieval_mode': retrieval_result['mode'],
            'retrieval_count': retrieval_result['count'],
            'has_results': retrieval_result['has_results'],
            'retrieval_quality': retrieval_quality,
            'avg_retrieval_score': round(avg_retrieval_score, 4),
            'duration': duration,
            'error': error,
            'scenario_id': scenario_id,
            'evaluation': evaluation_result,
            'intent_info': intent_info,
            'sentence_tracing': sentence_tracing,
            'drift_analysis': drift_analysis,
            'contradictions': contradictions,
            'contradiction_count': len(contradictions),
            'citation_validation': citation_validation,
            'debug_info': retrieval_result.get('debug_info', {}),
            'recall_diagnosis': retrieval_result.get('recall_diagnosis', {}),
            'business_diagnosis': retrieval_result.get('business_diagnosis', {})
        }

    def _single_sentence_trace(self, sentence: str, chunk_emb_cache: Dict, lang: Optional[str] = None,
                               running_top1: Optional[List[float]] = None,
                               citation_map: Optional[Dict[str, str]] = None,
                               start: Optional[int] = None, end: Optional[int] = None,
                               index: Optional[int] = None) -> Optional[Dict]:
        """Thin wrapper delegating to SentenceTracer.trace_single().

        This must stay a delegate: the streaming path and the final full-answer
        trace have to classify identically, and sharing the implementation is the
        only way to guarantee it. An earlier version kept its own copy of the
        similarity + threshold logic, which had drifted from the batch tracer
        (hard-coded thresholds, single-scale matching), so the badges shown while
        streaming disagreed with the trace shown at the end.

        Args:
            sentence: A single complete answer sentence (stripped).
            chunk_emb_cache: Output of sentence_tracer.build_chunk_index(...).
            lang: i18n language.
            running_top1: Top-1 fused scores accumulated so far during streaming —
                passed in so the relative-rank part of the verdict can converge as
                more sentences become available. The caller owns the list.
            citation_map: Ordinal -> chunk id map of the retrieved context, so a
                streamed sentence can be attributed to the chunk it cites.
            start / end: Character offsets of this sentence inside the full answer
                text (the client attributes evidence by offset, not by index).
            index: Optional 0-based sentence index, kept as a UI hint.
        """
        try:
            result = sentence_tracer.trace_single(
                sentence, chunk_emb_cache, lang=lang,
                all_top1_for_adaptive=running_top1,
                citation_map=citation_map, start=start, end=end, index=index,
            )
            # Feed the fused top-1 score back so subsequent sentences rank against a
            # richer sample of this answer's own score distribution.
            if result and running_top1 is not None and result.get('sources'):
                running_top1.append(result['sources'][0]['score'])
            return result
        except Exception as e:
            logger.warning(f"_single_sentence_trace failed for '{sentence[:50]}...': {e}")
            return None

    def _detect_contradictions(self, answer: str, context_results: List[Dict],
                               sentence_tracing: Optional[List[Dict]] = None,
                               lang: Optional[str] = None) -> List[Dict]:
        """Run the structured contradiction check and flag the matching trace sentences.

        The detector compares claims (numbers, amounts, durations, dates, polarity) against
        the chunks that were placed in the prompt, so its findings belong to the trace: the
        sentences it disagrees with carry ``has_contradiction`` for the UI to highlight.

        Args:
            answer: Final answer text.
            context_results: Chunks that were placed in the prompt.
            sentence_tracing: Trace items to flag (optional).
            lang: i18n language.

        Returns:
            Contradiction list; empty when the check is disabled or raises.
        """
        try:
            contradictions = contradiction_detector.detect(answer, context_results, lang=lang)
        except Exception as e:
            # Never let an optional audit break the answer: log with a traceback so a real
            # bug stays visible instead of looking like "nothing was found".
            logger.warning(f"[Response Process] Contradiction detection failed: {e}", exc_info=True)
            return []
        if contradictions:
            flagged = contradiction_detector.attach_to_sentences(sentence_tracing or [], contradictions)
            logger.info(f"[Response Process] Contradictions found: {len(contradictions)}, "
                        f"{flagged} sentence(s) flagged")
        return contradictions

    def _collapse_hybrid_disclaimer(self, answer: str, lang: Optional[str] = None) -> str:
        """
        Keep only the first copy of the hybrid disclaimer.

        The pipeline injects the disclaimer before the model answers and the prompt asks the model
        to open with the same two lines, so the finished answer can carry the prefix twice (the
        stored traces show exactly that). The check in `_ensure_hybrid_format` cannot see the
        duplicate: it matches the copy the pipeline itself wrote.

        Args:
            answer: Answer text (raw model output, or injected + streamed answer)
            lang: Language code

        Returns:
            Answer with a single leading disclaimer copy; unchanged when there is nothing to do.
        """
        pattern = _disclaimer_pattern(_('pipeline.hybrid_disclaimer', lang))
        if not answer or pattern is None:
            return answer

        matches = list(pattern.finditer(answer))
        if len(matches) < 2:
            return answer

        # The copy that is kept is the one the pipeline injected (or the model's first one). The
        # disclaimer ends with whitespace (a blank line) that the pattern does not consume, so it
        # has to be restored after the later copies are removed.
        disclaimer = _('pipeline.hybrid_disclaimer', lang) or ''
        separator = disclaimer[len(disclaimer.rstrip()):] or '\n\n'

        head = answer[:matches[0].end()]
        tail = answer[matches[0].end():].lstrip()
        while True:
            match = pattern.search(tail)
            if match is None:
                break
            # Drop the copy and the whitespace it left behind at the junction.
            tail = (tail[:match.start()] + tail[match.end():]).lstrip()

        logger.info(
            f"[Response Process] Collapsed {len(matches) - 1} duplicated hybrid disclaimer copy/copies"
        )
        return head + separator + tail

    def _ensure_hybrid_format(self, answer: str, lang: Optional[str] = None) -> str:
        """
        Force ensure answer starts with hybrid answer mode disclaimer opening.
        If the LLM has already followed the format (contains disclaimer), do not inject repeatedly,
        and collapse the copies it did emit into one.

        Args:
            answer: LLM raw answer
            lang: Language code

        Returns:
            Normalized answer
        """
        if not answer:
            return answer
        # The model repeats the injected disclaimer (the prompt asks it to), so collapse before
        # deciding whether to inject: otherwise the substring check below matches the copy the
        # streaming path itself wrote and the duplicate is left in the answer.
        answer = self._collapse_hybrid_disclaimer(answer, lang=lang)
        # Detect whether LLM already contains disclaimer (allow slight wording differences)
        answer_lower = answer.strip().lower()
        localized_disclaimer = _('pipeline.hybrid_disclaimer', lang) or ''
        if localized_disclaimer.strip() and localized_disclaimer.strip() in answer:
            logger.info("[Response Process] Knowledge disclaimer already present, no need to inject again")
            return answer
        has_disclaimer = any(
            keyword in answer_lower
            for keyword in (
                            'no relevant information found', 'no relevant content',
                            'knowledge base.*no', 'not found in.*knowledge')
        )
        if has_disclaimer:
            logger.info("[Response Process] LLM already contains knowledge disclaimer, no need to inject again")
            return answer
        logger.info("[Response Process] Force inject hybrid answer disclaimer prefix")
        return localized_disclaimer + answer.strip()

    def _validate_citations(self, answer: str, context_results: List[Dict], lang: Optional[str] = None) -> Dict:
        """
        Validate whether citation markers in the answer are correct

        Args:
            answer: LLM-generated answer
            context_results: Retrieved context results
            lang: Language code

        Returns:
            Citation validation result:
            {
                'has_citations': bool,
                'found_citations': List[str],
                'citation_to_chunk_map': Dict[str, str],
                'missing_citations': List[str],
                'suggestion': str,
                'valid_citations': int,
                'invalid_citations': int
            }
        """
        import re

        # Citation markers are referenced in the system prompts as 1-based document numbers,
        # with language-specific prefixes depending on the model language:
        #   zh: [文档N]   en: [Document N]
        # plus legacy/alias forms [N], [documentN], [DocumentN], [chunk_N].
        # Match all of these and extract the 1-based document NUMBER so validation is language-agnostic.
        citation_pattern = re.compile(r'\[\s*(?:(?:document|doc|文档|chunk)\s*_?)?(\d+)\s*\]', re.IGNORECASE)

        # Map each 1-based context slot to its real chunk id (both the positional tag and the chunk id are mapped)
        total = len(context_results)
        citation_to_chunk_map = {}
        for idx, result in enumerate(context_results, 1):
            chunk_id = result.get('id', str(idx))
            citation_to_chunk_map[str(idx)] = chunk_id
            citation_to_chunk_map[f'chunk_{idx}'] = chunk_id

        found_citations = []
        seen = set()
        for match in citation_pattern.finditer(answer):
            num_str = match.group(1)
            # Only record in-range references as "found"; anything else counts as invalid
            if num_str not in seen:
                seen.add(num_str)
                found_citations.append(num_str)

        valid_citations = []
        invalid_citations = []
        for citation in found_citations:
            # The captured number must be a valid 1-based context slot (1..total)
            if citation in citation_to_chunk_map:
                valid_citations.append(citation)
            else:
                invalid_citations.append(citation)

        has_citations = len(valid_citations) > 0

        if not has_citations:
            suggestion = _('pipeline.citation_missing', lang)
        elif invalid_citations:
            suggestion = _('pipeline.citation_invalid', lang, len(invalid_citations), ", ".join(invalid_citations))
        else:
            suggestion = _('pipeline.citation_valid', lang)

        return {
            'has_citations': has_citations,
            'found_citations': found_citations,
            'citation_to_chunk_map': citation_to_chunk_map,
            'missing_citations': invalid_citations,
            'suggestion': suggestion,
            'valid_citations': len(valid_citations),
            'invalid_citations': len(invalid_citations)
        }

    def _get_empty_response(self, scenario_id: Optional[str] = None, lang: Optional[str] = None) -> str:
        """
        Get response for empty retrieval (supports scenario-based config)

        Args:
            scenario_id: Scenario ID
            lang: Language code

        Returns:
            Empty response text
        """
        if scenario_id is None:
            return _('pipeline.empty_response', lang)

        effective_config = scenario_config.get_effective_config(scenario_id)
        empty_response = effective_config.get('retriever', {}).get('empty_response')

        if empty_response:
            return empty_response

        return self._get_empty_response(None, lang=lang)

    async def _async_iter_stream(self, adapter, prompt: str):
        """
        Convert synchronous streaming generator to async generator, avoiding blocking the event loop when iterating a synchronous generator in an async function.

        Consumes the synchronous generator returned by adapter.chat(stream=True) through a background thread,
        and dispatches chunks asynchronously to the event loop via asyncio.Queue + call_soon_threadsafe.
        """
        import asyncio

        loop = asyncio.get_running_loop()
        out_queue: "asyncio.Queue" = asyncio.Queue()
        _SENTINEL = object()

        def _produce():
            """Iterate synchronous streaming generator in background thread, push chunks to async queue"""
            try:
                stream = adapter.chat(
                    model=self.llm_model,
                    messages=[{'role': 'user', 'content': prompt}],
                    stream=True
                )
                for chunk in stream:
                    loop.call_soon_threadsafe(out_queue.put_nowait, chunk)
            except Exception as e:
                loop.call_soon_threadsafe(out_queue.put_nowait, e)
            finally:
                loop.call_soon_threadsafe(out_queue.put_nowait, _SENTINEL)

        # Start background thread to execute synchronous streaming generation (does not block event loop)
        loop.run_in_executor(None, _produce)

        while True:
            item = await out_queue.get()
            if item is _SENTINEL:
                break
            if isinstance(item, Exception):
                raise item
            yield item

    async def query_stream(self, kb_id: str, query: str, trace_id: str = None, scenario_id: Optional[str] = None, lang: Optional[str] = None) -> AsyncGenerator[str, None]:
        """
        Streaming Q&A interface (SSE)
        it is called by the frontend to get a response for a single user question as a stream of SSE-format events.

        Args:
            kb_id: Knowledge base ID
            query: User question
            trace_id: Optional, tracing ID
            scenario_id: Scenario ID, used to load scenario-specific config
            lang: Language code

        Yields:
            SSE-format event data
        """
        import asyncio

        if trace_id is None:
            trace_id = str(uuid.uuid4())

        start_time = time.time()
        full_answer = ""

        empty_response = self._get_empty_response(scenario_id, lang=lang)

        trace = trace_manager.create_trace(kb_id, query, scenario_id)
        trace.trace_id = trace_id

        # 0. Intent classification
        intent_info = None
        try:
            from core.intent_classifier import intent_classifier
            intent_info = intent_classifier.classify(query, lang=lang)
            trace.set_intent_info(intent_info)
            trace.add_stage('intent_classification', {'query': query}, intent_info)
            logger.debug(f"Intent classification completed: {intent_info.get('intent_type')}")
        except Exception as e:
            logger.warning(f"Intent classification failed: {e}")

        # 0.5 Boundary detection (consistent with sync interface)
        # it is used to detect whether the query is in-domain or out-of-domain
        # the switch of boundary detection is controlled by the service config, default is FALSE
        boundary_result = None
        try:
            from core.boundary_detector import boundary_detector
            logger.info(f"[Response Process] Start domain boundary detection, query: {query[:50]}...")
            boundary_result = boundary_detector.detect(query, lang=lang)
            trace.add_stage('boundary_detection', {'query': query}, boundary_result)

            if not boundary_result.get('in_domain', True):
                logger.warning(f"[Response Process] Boundary detection rejected, reason: {boundary_result.get('reason')}")
                duration = round(time.time() - start_time, 2)
                trace.mark_complete('boundary_rejected')
                yield f"event: status\ndata: {json.dumps({'message': _('pipeline.status_boundary_rejected', lang), 'trace_id': trace_id}, ensure_ascii=False)}\n\n"
                done_payload = {
                    'trace_id': trace_id,
                    'answer': boundary_result.get('suggestion', empty_response),
                    'sources': [],
                    'context': [],
                    'has_results': False,
                    'retrieval_mode': 'boundary_rejected',
                    'duration': duration,
                    'status': 'boundary_rejected',
                    'boundary_result': boundary_result
                }
                yield f"event: done\ndata: {json.dumps(done_payload, ensure_ascii=False)}\n\n"
                return
        except Exception as e:
            # exc_info so a genuine bug in the detector is visible instead of looking like a
            # routine "detector unavailable" skip.
            logger.warning(f"[Response Process] Boundary detection failed: {e}", exc_info=True)

        # Send start event (includes intent info)
        yield f"event: start\ndata: {json.dumps({'trace_id': trace_id, 'scenario_id': scenario_id, 'intent_info': intent_info}, ensure_ascii=False)}\n\n"

        # 1. Retrieve context (use run_in_executor to avoid blocking)
        yield f"event: status\ndata: {json.dumps({'message': _('pipeline.status_retrieving', lang), 'trace_id': trace_id}, ensure_ascii=False)}\n\n"

        loop = asyncio.get_event_loop()
        retrieval_result = await loop.run_in_executor(
            None,
            self.retriever.retrieve,
            kb_id,
            query,
            scenario_id
        )
        context_results = retrieval_result['results']

        trace.add_stage('retrieval', {'query': query}, {
            'count': retrieval_result['count'],
            'mode': retrieval_result['mode'],
            'has_results': retrieval_result['has_results']
        }, retrieval_result.get('debug_info', {}))

        # ===== Suggestion A: retrieval quality secondary validation =====
        # Empty results / low scores are all classified as low, forcing hybrid answer mode
        retrieval_quality = 'low'   # Default is low, only upgraded to high when retrieval results exist and average score > 0.4
        avg_retrieval_score = 0.0
        if context_results:
            avg_retrieval_score = sum(r.get('score', 0) for r in context_results) / len(context_results)
            if avg_retrieval_score > 0.4:
                retrieval_quality = 'high'
            else:
                logger.warning(f"[Response Process] Retrieval quality low (avg score {avg_retrieval_score:.4f} ≤ 0.4), enabling hybrid answer mode")
        else:
            logger.warning("[Response Process] Retrieval results are empty, enabling hybrid answer mode (no knowledge match found)")
        logger.info(f"[Response Process] Retrieval quality check: {retrieval_quality} (avg score {avg_retrieval_score:.4f}, result count {len(context_results)}")

        # Send retrieval result event (includes retrieval quality flag)
        # White-box contract: ship the evidence actually handed to the LLM (all of it, not a
        # 3-item preview) together with the retrieval funnel - which candidates were dropped and
        # at which stage. Also surface whether the "missed documents" scan was skipped, otherwise
        # an empty miss list looks like "nothing was missed".
        # Chunk text is truncated here to keep the SSE frame small; the full chunk is available
        # on demand via GET /api/knowledge/{kb_id}/chunks/{chunk_id}.
        _stream_results = []
        for _r in context_results:
            _text = _r.get('text') or ''
            _stream_results.append({
                **_r,
                'text': _text[:500] + ('…' if len(_text) > 500 else ''),
                'text_truncated': len(_text) > 500
            })

        _debug_payload = retrieval_result.get('debug_info', {}) or {}
        _recall_diagnosis = retrieval_result.get('recall_diagnosis', {}) or {}
        _misses_skipped = bool(_recall_diagnosis.get('potential_misses_skipped', False))

        yield f"event: retrieval\ndata: {json.dumps({'count': retrieval_result['count'], 'mode': retrieval_result['mode'], 'results': _stream_results, 'results_total': len(context_results), 'funnel': _debug_payload.get('funnel', {}), 'debug_info': _debug_payload, 'retrieval_quality': retrieval_quality, 'avg_retrieval_score': round(avg_retrieval_score, 4), 'skipped': {'potential_misses': _misses_skipped, 'reason': 'debug_off' if _misses_skipped else None}}, ensure_ascii=False)}\n\n"

        # ===== Empty retrieval no longer short-circuits =====
        # query() always proceeds to LLM even with zero results — retrieval_quality is
        # already 'low' here, which forces the hybrid-answer prompt (LLM self-knowledge
        # disclaimer + free-form reply). query_stream() must behave identically so that
        # abtest and the chat UI produce the same answer for the same query.
        # (Previously query_stream() returned early with empty_response and skipped LLM,
        # which is why the user saw "no relevant docs" while abtest produced a real answer.)

        # 2. Build prompt (switch template based on retrieval quality)
        prompt = self._build_prompt(query, context_results, scenario_id, retrieval_quality=retrieval_quality, lang=lang)

        # 3. Streaming call to LLM
        yield f"event: status\ndata: {json.dumps({'message': _('pipeline.status_generating', lang)}, ensure_ascii=False)}\n\n"

        try:
            from core.circuit_breaker import circuit_breaker_manager, CircuitBreakerError

            adapter = self._get_llm_adapter()

            # Circuit-breaker guard: short-circuit before we even start the
            # streaming request if the LLM service is known to be down.
            # (Cannot wrap the whole async generator with breaker.call() because
            # a stream may stay open for many seconds — we only protect the
            # initiation step here.)
            breaker = circuit_breaker_manager.get_breaker('llm_service')
            breaker_state = breaker.get_status()['state']
            logger.info(f"[Response Process] Streaming LLM call, model: {self.llm_model}, circuit breaker: {breaker_state}")
            if breaker_state == 'open':
                raise CircuitBreakerError(
                    f"Circuit breaker 'llm_service' is OPEN, rejecting request",
                    circuit_name='llm_service', state='open'
                )

            # ===== Hybrid answer mode: first force yield disclaimer prefix, then forward LLM streaming output =====
            hybrid_disclaimer = ''
            _disclaimer_filter = None
            if retrieval_quality == 'low':
                hybrid_disclaimer = _('pipeline.hybrid_disclaimer', lang)
                print(f"##### [Response Process] Hybrid answer disclaimer prefix: {hybrid_disclaimer}")
                full_answer += hybrid_disclaimer
                print(f"##### [Response Process] Full answer after disclaimer prefix: {full_answer}")
                yield f"event: answer\ndata: {json.dumps({'content': hybrid_disclaimer}, ensure_ascii=False)}\n\n"
                logger.info("[Response Process] Hybrid answer disclaimer prefix injected")
                # The model is asked to open with the same two lines, so it repeats the prefix we
                # just sent. Hold back the leading characters until that can be told apart from
                # real content, otherwise the user reads the disclaimer twice.
                _disclaimer_filter = _LeadingDuplicateFilter(hybrid_disclaimer)

            # ===== Pre-compute chunk embeddings once for incremental sentence tracing =====
            # Delegates to SentenceTracer.build_chunk_index() which embeds each chunk at
            # BOTH scales — the full chunk (coarse) and every sentence inside it (fine).
            _chunk_emb_cache = {}
            _running_top1_for_adaptive: List[float] = []
            # Ordinal -> chunk id map, shared with the final trace so a streamed
            # verdict and the final verdict resolve [文档N] markers identically.
            _citation_map = sentence_tracer.build_citation_map(context_results)
            try:
                _chunk_emb_cache = sentence_tracer.build_chunk_index(context_results)
                logger.debug(
                    f"[Sentence Tracing] Multi-scale chunk index ready: "
                    f"{len(_chunk_emb_cache)} chunks, "
                    f"{sum(len(c.get('sentences', [])) for c in _chunk_emb_cache.values())} inner sentences"
                )
            except Exception as _pre_err:
                logger.warning(f"[Sentence Tracing] build_chunk_index failed: {_pre_err}")

            # ===== Streaming LLM call WITH retry (align with query() behaviour) =====
            # query() retries up to self.max_retries times on transient failures
            # (DNS blips, brief timeouts). Without retry here, a single network hiccup
            # kills the whole stream while abtest still succeeds — a major behavioural
            # divergence between the two entry points.
            llm_exception = None
            _sentence_buf = ""
            _sentence_index = 0
            # Everything appended so far is the injected disclaimer: it is rendered as
            # its own card and never traced incrementally. Boundaries are decided by
            # OFFSET, not by "skip the first sentence" — the disclaimer contains two
            # sentence ends, so a boolean skip swallowed the first real answer
            # sentence while the client still counted the disclaimer's segments.
            _disclaimer_end = len(full_answer) if retrieval_quality == 'low' else 0
            for attempt in range(self.max_retries):
                try:
                    logger.info(f"[Response Process] Streaming LLM attempt {attempt + 1}/{self.max_retries}")

                    # Consume synchronous streaming output via async generator, avoiding blocking event loop when iterating synchronous generator
                    async for chunk in self._async_iter_stream(adapter, prompt):
                        if chunk.get('done'):
                            break
                        content = chunk.get('message', {}).get('content', '')
                        if content:
                            if _disclaimer_filter is not None:
                                # '' while the leading characters could still be the repeated
                                # disclaimer; anything else is the model's own answer text.
                                content = _disclaimer_filter.feed(content)
                                if not content:
                                    continue
                            full_answer += content
                            _sentence_buf += content
                            yield f"event: answer\ndata: {json.dumps({'content': content}, ensure_ascii=False)}\n\n"
                            # Emit incremental provenance for every sentence the buffer now
                            # completes. The shared splitter decides what a sentence is, so
                            # numbers such as "0.4" are never cut in half, and a chunk that
                            # carries two sentences no longer merges them into one.
                            # Each event carries the sentence's character offsets inside the
                            # full answer: that is what the client attributes by, instead of
                            # trusting two independent sentence index spaces to stay aligned.
                            _completed, _pending_start = split_complete_sentences(_sentence_buf)
                            if _completed:
                                _buffer_offset = len(full_answer) - len(_sentence_buf)
                                for _span in _completed:
                                    _span_start = _buffer_offset + _span.start
                                    _span_end = _buffer_offset + _span.end
                                    if _span_start < _disclaimer_end:
                                        continue
                                    _trace_payload = self._single_sentence_trace(
                                        _span.text, _chunk_emb_cache, lang,
                                        running_top1=_running_top1_for_adaptive,
                                        citation_map=_citation_map,
                                        start=_span_start, end=_span_end, index=_sentence_index,
                                    )
                                    if _trace_payload:
                                        yield f"event: sentence_provenance\ndata: {json.dumps({'sentence_idx': _sentence_index, 'sentence_start': _span_start, 'sentence_end': _span_end, 'sentence': _span.text, 'sentence_provenance': _trace_payload}, ensure_ascii=False)}\n\n"
                                        _sentence_index += 1
                                _sentence_buf = _sentence_buf[_pending_start:]

                    llm_exception = None
                    break

                except Exception as stream_err:
                    llm_exception = stream_err
                    logger.warning(f"[Response Process] Streaming LLM attempt {attempt + 1}/{self.max_retries} failed: {stream_err}")
                    # Don't retry if we already streamed any content to the frontend —
                    # retrying would yield duplicate tokens.
                    if full_answer and retrieval_quality == 'low' and full_answer.strip() != (hybrid_disclaimer or '').strip():
                        logger.warning("[Response Process] Partial content already streamed, aborting retry")
                        break
                    if attempt < self.max_retries - 1:
                        import time as _t
                        _t.sleep(1)

            if llm_exception is not None:
                raise llm_exception

            # ===== Release whatever the duplicate filter still held back =====
            # Only reachable when the whole answer is a truncated copy of the disclaimer; a real
            # answer diverges from it and is released by feed(). The trailing-sentence flush below
            # reports the provenance of whatever is released here.
            if _disclaimer_filter is not None:
                _released = _disclaimer_filter.flush()
                if _released:
                    full_answer += _released
                    _sentence_buf += _released
                    yield f"event: answer\ndata: {json.dumps({'content': _released}, ensure_ascii=False)}\n\n"

            # ===== Flush the trailing sentence buffer (no terminator before stream end) =====
            if _sentence_buf.strip():
                _buffer_offset = len(full_answer) - len(_sentence_buf)
                for _span in split_sentence_spans(_sentence_buf):
                    _span_start = _buffer_offset + _span.start
                    _span_end = _buffer_offset + _span.end
                    if _span_start < _disclaimer_end:
                        continue
                    _trace_payload = self._single_sentence_trace(
                        _span.text, _chunk_emb_cache, lang,
                        running_top1=_running_top1_for_adaptive,
                        citation_map=_citation_map,
                        start=_span_start, end=_span_end, index=_sentence_index,
                    )
                    if _trace_payload:
                        yield f"event: sentence_provenance\ndata: {json.dumps({'sentence_idx': _sentence_index, 'sentence_start': _span_start, 'sentence_end': _span_end, 'sentence': _span.text, 'sentence_provenance': _trace_payload}, ensure_ascii=False)}\n\n"
                        _sentence_index += 1

            # After streaming completes, ensure hybrid answer format is correct again (remove LLM's possibly duplicated disclaimer)
            if retrieval_quality == 'low' and full_answer:
                full_answer = self._ensure_hybrid_format(full_answer, lang=lang)

        except CircuitBreakerError as e:
            error_msg = _('pipeline.circuit_busy', lang)
            logger.error(f"[Response Process] Streaming LLM circuit breaker is open: {e}")
            yield f"event: error\ndata: {json.dumps({'message': error_msg}, ensure_ascii=False)}\n\n"

            trace.add_stage('generation', {'prompt': prompt[:200] + '...'}, {'answer': full_answer, 'error': str(e)})
            trace.set_final_answer(error_msg)
            trace.set_error(str(e))
            trace_manager.save_trace(trace)

            yield f"event: done\ndata: {json.dumps({'answer': error_msg, 'duration': round(time.time() - start_time, 2), 'error': str(e), 'intent_info': intent_info}, ensure_ascii=False)}\n\n"
            return

        except Exception as e:
            logger.error(f"[Response Process] Streaming LLM call failed: {e}", exc_info=True)

            # Align with query() fallback: use llm_unavailable when retrieval had
            # results (LLM failure is the problem) vs empty_response when retrieval
            # was empty (no knowledge base match anyway).
            if retrieval_result['has_results']:
                fallback = _('pipeline.llm_unavailable', lang)
            else:
                fallback = retrieval_result.get('empty_response', empty_response)
            if full_answer:
                # Partial stream already reached frontend — finish the hybrid disclaimer
                # if one was injected, then let the partial content stand.
                full_answer = self._ensure_hybrid_format(full_answer, lang=lang) if retrieval_quality == 'low' else full_answer
                fallback = full_answer

            yield f"event: error\ndata: {json.dumps({'message': str(e)}, ensure_ascii=False)}\n\n"

            trace.add_stage('generation', {'prompt': prompt[:200] + '...'}, {'answer': full_answer or fallback, 'error': str(e)})
            trace.set_final_answer(fallback)
            trace.set_error(str(e))
            trace_manager.save_trace(trace)

            yield f"event: done\ndata: {json.dumps({'answer': fallback, 'duration': round(time.time() - start_time, 2), 'error': str(e), 'intent_info': intent_info}, ensure_ascii=False)}\n\n"
            return

        # 4. Sentence-level tracing
        sentence_tracing = None
        drift_analysis = None
        try:
            sentence_tracing = sentence_tracer.trace(full_answer, context_results, lang=lang)
            drift_analysis = sentence_tracer.analyze_drift(sentence_tracing, lang=lang)
            trace.set_sentence_tracing(sentence_tracing)
            trace.add_stage('sentence_tracing', {'answer': full_answer}, drift_analysis)
            logger.debug(f"[Response Process] Sentence tracing completed: {len(sentence_tracing)} sentences, drift rate: {drift_analysis.get('drift_rate', 0):.2f}")
        except Exception as e:
            logger.warning(f"Sentence tracing failed: {e}")

        # 4.5 Contradiction check (numbers / amounts / dates / polarity vs the context)
        contradictions = self._detect_contradictions(full_answer, context_results, sentence_tracing, lang)
        trace.set_contradictions(contradictions)

        # 5. Evaluation
        evaluation_result = None
        if self.evaluation_enabled:
            try:
                evaluator = self._get_evaluator()
                if evaluator:
                    evaluation_result = evaluator.evaluate(query, context_results, full_answer, scenario_id, boundary_result=boundary_result, lang=lang)
            except Exception as e:
                logger.error(f"Evaluation failed: {e}")

        trace.set_evaluation(evaluation_result)
        trace.add_stage('generation', {'prompt': prompt[:200] + '...'}, {'answer': full_answer[:200] + '...'})
        trace.set_final_answer(full_answer)
        trace.set_error(None)
        trace_manager.save_trace(trace)

        # 4. Complete
        duration = round(time.time() - start_time, 2)

        # Send sentence-level tracing event
        if sentence_tracing:
            yield f"event: sentence_tracing\ndata: {json.dumps({'sentence_tracing': sentence_tracing, 'drift_analysis': drift_analysis}, ensure_ascii=False)}\n\n"

        # Send citation validation event
        citation_validation = None
        try:
            citation_validation = self._validate_citations(full_answer, context_results, lang=lang)
        except Exception as e:
            logger.warning(f"Citation validation failed: {e}")

        if citation_validation:
            yield f"event: citation_validation\ndata: {json.dumps({'citation_validation': citation_validation}, ensure_ascii=False)}\n\n"

        # Send evaluation event
        if evaluation_result:
            yield f"event: evaluation\ndata: {json.dumps({'evaluation': evaluation_result}, ensure_ascii=False)}\n\n"

        # Send non-recall diagnosis event
        recall_diagnosis = retrieval_result.get('recall_diagnosis', {})
        if recall_diagnosis and recall_diagnosis.get('enabled', False):
            yield f"event: recall_diagnosis\ndata: {json.dumps({'recall_diagnosis': recall_diagnosis}, ensure_ascii=False)}\n\n"

        # Send business diagnosis event
        business_diagnosis = retrieval_result.get('business_diagnosis', {})
        if business_diagnosis:
            yield f"event: business_diagnosis\ndata: {json.dumps({'business_diagnosis': business_diagnosis}, ensure_ascii=False)}\n\n"

        # Send contradictions event (answer statements that disagree with the source)
        if contradictions:
            yield f"event: contradictions\ndata: {json.dumps({'contradictions': contradictions, 'contradiction_count': len(contradictions)}, ensure_ascii=False)}\n\n"

        # Send completion event (carries avg_retrieval_score for frontend final confirmation of conclusion card status)
        yield f"event: done\ndata: {json.dumps({'answer': full_answer, 'duration': duration, 'context_count': len(context_results), 'intent_info': intent_info, 'retrieval_quality': retrieval_quality, 'avg_retrieval_score': round(avg_retrieval_score, 4), 'contradiction_count': len(contradictions)}, ensure_ascii=False)}\n\n"

        logger.info(f"[Resoinse Process] Completed in {duration}s, scenario: {scenario_id or 'Default'}")

    def _collect_retrieval_defaults(self) -> Dict:
        """
        Default retrieval parameters to report to the UI.

        Values come from config/settings.yaml (`retriever:` block, see
        core.retriever.get_retrieval_defaults); when this pipeline already holds a live
        retriever, its effective attributes win so the UI can never display a default the
        running retriever does not actually use.
        """
        defaults = get_retrieval_defaults()
        retriever = getattr(self, 'retriever', None)
        if retriever is not None:
            for key in ('mode', 'bm25_weight', 'vector_weight', 'top_k',
                        'similarity_threshold', 'rerank_top_k'):
                value = getattr(retriever, key, None)
                if value is not None:
                    defaults[key] = value
        return defaults

    def check_health(self) -> Dict:
        """
        Check LLM service health status

        Returns:
            Health status dictionary
        """
        try:
            adapter = self._get_llm_adapter()
            health = adapter.check_health()

            embedding_model = config.get_embedding_model()
            provider = config.get('llm.provider', 'ollama')

            llm_available = self.llm_model in health.get('available_models', [])
            embedding_available = embedding_model in health.get('available_models', [])

            return {
                'healthy': health.get('healthy', False),
                'provider': health.get('provider', provider),
                'base_url': health.get('base_url', ''),
                'llm_model': self.llm_model,
                'llm_available': llm_available,
                'embedding_model': embedding_model,
                'embedding_available': embedding_available,
                'available_models': health.get('available_models', []),
                'retrieval_defaults': self._collect_retrieval_defaults(),
                'chunk_defaults': {
                    'chunk_size': config.get('document_parser.chunk_size', 512),
                    'chunk_overlap': config.get('document_parser.chunk_overlap', 128),
                }
            }
        except Exception as e:
            logger.error(f"[Response Process] Failed to check LLM health status: {e}")
            return {
                'healthy': False,
                'error': str(e),
                'provider': config.get('llm.provider', 'ollama')
            }