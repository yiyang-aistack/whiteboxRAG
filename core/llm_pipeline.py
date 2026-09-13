"""
LLM Pipeline Module
Q&A pipeline based on unified LLM adapter, supports streaming output and tracing
Supports scenario-based prompt template loading
Supports enforced citation format and sentence-level tracing
"""
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from config import config, scenario_config
from core.llm_adapter import LLMAdapterFactory
from core.trace import trace_manager
from core.sentence_tracing import sentence_tracer
from service.i18n import _
from service.logger import get_logger

logger = get_logger('llm_pipeline')


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
            trace_file = self.trace_dir / f'{trace_id}.json'
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
        trace_file = self.trace_dir / f'{trace_id}.json'
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
        logger.info(f"[问答流程] Start Q&A, KB ID: {kb_id}, Query: {query[:50]}..., Scenario: {scenario_id or 'Default'}")

        empty_response = self._get_empty_response(scenario_id, lang=lang)

        trace = trace_manager.create_trace(kb_id, query, scenario_id)
        trace.trace_id = trace_id

        # 0. Intent classification
        intent_info = None
        try:
            from core.intent_classifier import intent_classifier
            logger.info(f"[Resoinse Process] Start intent classification, Query: {query[:50]}...")
            intent_info = intent_classifier.classify(query, lang=lang)
            trace.set_intent_info(intent_info)
            trace.add_stage('intent_classification', {'query': query}, intent_info)
            logger.info(f"[Resoinse Process] Intent classification completed, Intent type: {intent_info.get('intent_type')}, Confidence: {intent_info.get('confidence', 0):.2f}")
        except Exception as e:
            logger.warning(f"[Resoinse Process] Intent classification failed: {e}")

        # 0.5 Business scope boundary detection
        boundary_result = None
        try:
            from core.boundary_detector import boundary_detector
            logger.info(f"[Resoinse Process] Start boundary detection, Query: {query[:50]}...")
            boundary_result = boundary_detector.detect(query, lang=lang)
            trace.add_stage('boundary_detection', {'query': query}, boundary_result)

            if not boundary_result.get('in_domain', True):
                logger.warning(f"[Resoinse Process] Boundary detection rejected, Reason: {boundary_result.get('reason')}, Confidence: {boundary_result.get('confidence', 0):.2f}")
                duration = round(time.time() - start_time, 2)
                trace.mark_complete('boundary_rejected')
                return {
                    'answer': boundary_result.get('suggestion', empty_response),
                    'sources': [],
                    'trace_id': trace_id,
                    'duration': duration,
                    'intent_info': intent_info,
                    'boundary_result': boundary_result,
                    'context_count': 0,
                    'status': 'boundary_rejected'
                }
            logger.info(f"[Resoinse Process] Boundary detection passed, Confidence: {boundary_result.get('confidence', 0):.2f}")
        except Exception as e:
            logger.warning(f"[Resoinse Process] Boundary detection failed, continue execution: {e}")

        # 1. Retrieve context (pass through retrieval parameters)
        logger.info(f"[Resoinse Process] Start context retrieval, KB ID: {kb_id}, Scenario: {scenario_id or 'Default'}, Parameters: {kwargs}")
        retrieval_result = self.retriever.retrieve(kb_id, query, scenario_id=scenario_id, **kwargs)
        context_results = retrieval_result['results']
        logger.info(f"[Resoinse Process] Context retrieval completed, Mode: {retrieval_result['mode']}, Returns {retrieval_result['count']} results, Has results: {retrieval_result['has_results']}")
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
                logger.warning(f"[Resoinse Process] Retrieval quality low (avg score ≤ 0.4), enabling hybrid answer mode")
        else:
            logger.warning("[Resoinse Process] Retrieval results empty, enabling hybrid answer mode (no knowledge base match)")
        logger.info(f"[Resoinse Process] Retrieval quality check: {retrieval_quality} {avg_retrieval_score:.4f} {len(context_results)}")

        # 2. Build prompt (switch template based on retrieval quality)
        logger.info(f"[Resoinse Process] Start prompt building, Context count: {len(context_results)}, Retrieval quality: {retrieval_quality}")
        prompt = self._build_prompt(query, context_results, scenario_id, retrieval_quality=retrieval_quality, lang=lang)
        logger.info(f"[Resoinse Process] Prompt building completed, Length: {len(prompt)} characters")

        # 3. Call LLM (with circuit breaker protection)
        answer = ""
        error = None

        try:
            from core.circuit_breaker import circuit_breaker_manager, CircuitBreakerError

            adapter = self._get_llm_adapter()
            breaker = circuit_breaker_manager.get_breaker('llm_service')
            logger.info(f"[Resoinse Process] Start LLM call, Model: {self.llm_model}, Max retries: {self.max_retries}, Circuit breaker status: {breaker.get_status()['state']}")

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
                        logger.info(f"[Resoinse Process] LLM call successful, Duration: {llm_duration}s, Answer length: {len(response.get('message', {}).get('content', ''))} characters")
                        return response
                    except Exception as e:
                        if attempt < self.max_retries - 1:
                            logger.warning(f"[Resoinse Process] LLM call failed, retry {attempt + 1}/{self.max_retries}: {e}")
                            time.sleep(1)
                        else:
                            raise

            response = breaker.call(llm_call)
            answer = response.get('message', {}).get('content', '')

        except CircuitBreakerError as e:
            error = str(e)
            logger.error(f"[Resoinse Process] LLM circuit breaker is open: {e}")
            answer = _('pipeline.circuit_busy', lang)
        except Exception as e:
            error = str(e)
            logger.error(f"[Resoinse Process] LLM call failed: {e}", exc_info=True)
            if retrieval_result['has_results']:
                answer = _('pipeline.llm_unavailable', lang)
            else:
                answer = retrieval_result.get('empty_response', empty_response)
            logger.info(f"[Resoinse Process] Use fallback answer: {answer[:50]}...")

        # ===== Forced post-processing: in hybrid answer mode, ensure answer starts with disclaimer opening =====
        if retrieval_quality == 'low' and answer:
            answer = self._ensure_hybrid_format(answer, lang=lang)

        trace.add_stage('generation', {'prompt': prompt[:200] + '...' if len(prompt) > 200 else prompt}, {'answer': answer[:200] + '...' if len(answer) > 200 else answer})

        # 4. Sentence-level tracing
        sentence_tracing = None
        drift_analysis = None
        try:
            logger.info(f"[Resoinse Process] Start sentence-level tracing, Answer length: {len(answer)}, Context count: {len(context_results)}")
            sentence_tracing = sentence_tracer.trace(answer, context_results, lang=lang)
            drift_analysis = sentence_tracer.analyze_drift(sentence_tracing, lang=lang)
            trace.set_sentence_tracing(sentence_tracing)
            trace.add_stage('sentence_tracing', {'answer': answer}, drift_analysis)
            logger.info(f"[Resoinse Process] Sentence tracing completed, Sentence count: {len(sentence_tracing)}, Drift rate: {drift_analysis.get('drift_rate', 0):.2f}, Drifted sentence count: {drift_analysis.get('drifted_count', 0)}")
        except Exception as e:
            logger.warning(f"[问答流程] 句子级溯源失败: {e}")

        # 5. Evaluation (performed in non-streaming mode)
        evaluation_result = None
        if self.evaluation_enabled:
            try:
                evaluator = self._get_evaluator()
                if evaluator:
                    logger.info(f"[Resoinse Process] Start evaluation, evaluator type: {type(evaluator).__name__}")
                    evaluation_result = evaluator.evaluate(query, context_results, answer, scenario_id, boundary_result=boundary_result, lang=lang)
                    logger.info(f"[Resoinse Process] Evaluation completed, overall_score: {evaluation_result.get('overall_score', 0):.3f}, is_passing: {evaluation_result.get('is_passing')}, Scores: {evaluation_result.get('scores', {})}")
            except Exception as e:
                logger.error(f"[Resoinse Process] Evaluation failed: {e}")
        else:
            logger.info(f"[Resoinse Process] Evaluation disabled")

        # 6. Citation validation
        citation_validation = None
        try:
            logger.info(f"[Resoinse Process] Start citation validation, Answer length: {len(answer)}")
            citation_validation = self._validate_citations(answer, context_results, lang=lang)
            trace.add_stage('citation_validation', {'answer': answer}, citation_validation)
            if not citation_validation.get('has_citations', True):
                logger.warning(f"[Resoinse Process] Citation validation failed, answer does not contain citation markers: {citation_validation.get('suggestion', '')}")
            else:
                logger.info(f"[Resoinse Process] Citation validation passed, detected {len(citation_validation.get('found_citations', []))} citation markers")
        except Exception as e:
            logger.warning(f"[Resoinse Process] Citation validation failed: {e}")

        trace.set_evaluation(evaluation_result)
        trace.set_final_answer(answer)
        trace.set_error(error)
        trace_manager.save_trace(trace)

        # 6. Record tracing
        end_time = time.time()
        duration = round(end_time - start_time, 2)

        logger.info(f"[Resoinse Process] Question completed in {duration}s, retrieved {retrieval_result['count']} results, evaluation score: {evaluation_result.get('overall_score', 0) if evaluation_result else 0:.3f}, trace_id: {trace_id}")

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
            'citation_validation': citation_validation,
            'debug_info': retrieval_result.get('debug_info', {}),
            'recall_diagnosis': retrieval_result.get('recall_diagnosis', {}),
            'business_diagnosis': retrieval_result.get('business_diagnosis', {})
        }

    @staticmethod
    def _ensure_hybrid_format(answer: str, lang: Optional[str] = None) -> str:
        """
        Force ensure answer starts with hybrid answer mode disclaimer opening.
        If the LLM has already followed the format (contains disclaimer), do not inject repeatedly.

        Args:
            answer: LLM raw answer
            lang: Language code

        Returns:
            Normalized answer
        """
        if not answer:
            return answer
        # Detect whether LLM already contains disclaimer (allow slight wording differences)
        answer_lower = answer.strip().lower()
        has_disclaimer = any(
            keyword in answer_lower
            for keyword in (
                            'no relevant information found', 'no relevant content',
                            'knowledge base.*no', 'not found in.*knowledge')
        )
        if has_disclaimer:
            logger.info("[Resoinse Process] LLM already contains knowledge disclaimer, no need to inject again")
            return answer
        logger.info("[Resoinse Process] Force inject hybrid answer disclaimer prefix")
        return _('pipeline.hybrid_disclaimer', lang) + answer.strip()

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
        boundary_result = None
        try:
            from core.boundary_detector import boundary_detector
            logger.info(f"[Resoinse Process] Start domain boundary detection, query: {query[:50]}...")
            boundary_result = boundary_detector.detect(query, lang=lang)
            trace.add_stage('boundary_detection', {'query': query}, boundary_result)

            if not boundary_result.get('in_domain', True):
                logger.warning(f"[Resoinse Process] Boundary detection rejected, reason: {boundary_result.get('reason')}")
                duration = round(time.time() - start_time, 2)
                trace.mark_complete('boundary_rejected')
                yield f"event: status\ndata: {json.dumps({'message': _('pipeline.status_boundary_rejected', lang), 'trace_id': trace_id}, ensure_ascii=False)}\n\n"
                done_payload = {
                    'trace_id': trace_id,
                    'answer': boundary_result.get('suggestion', empty_response),
                    'sources': [],
                    'duration': duration,
                    'status': 'boundary_rejected',
                    'boundary_result': boundary_result
                }
                yield f"event: done\ndata: {json.dumps(done_payload, ensure_ascii=False)}\n\n"
                return
        except Exception as e:
            logger.warning(f"[Resoinse Process] Boundary detection failed: {e}")

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
                logger.warning(f"[Resoinse Process] Retrieval quality low (avg score {avg_retrieval_score:.4f} ≤ 0.4), enabling hybrid answer mode")
        else:
            logger.warning("[Resoinse Process] Retrieval results are empty, enabling hybrid answer mode (no knowledge match found)")
        logger.info(f"[Resoinse Process] Retrieval quality check: {retrieval_quality} (avg score {avg_retrieval_score:.4f}, result count {len(context_results)}")

        # Send retrieval result event (includes retrieval quality flag)
        yield f"event: retrieval\ndata: {json.dumps({'count': retrieval_result['count'], 'mode': retrieval_result['mode'], 'results': context_results[:3], 'debug_info': retrieval_result.get('debug_info', {}), 'retrieval_quality': retrieval_quality, 'avg_retrieval_score': round(avg_retrieval_score, 4)}, ensure_ascii=False)}\n\n"

        if not retrieval_result['has_results']:
            # Empty retrieval fallback: force low + carry avg_retrieval_score for frontend conclusion card display
            empty_response = retrieval_result.get('empty_response', empty_response)
            yield f"event: answer\ndata: {json.dumps({'content': empty_response}, ensure_ascii=False)}\n\n"

            trace.add_stage('generation', {'prompt': 'Empty retrieval results'}, {'answer': empty_response})
            trace.set_final_answer(empty_response)
            trace.set_error(None)
            trace_manager.save_trace(trace)

            yield f"event: done\ndata: {json.dumps({'duration': round(time.time() - start_time, 2), 'intent_info': intent_info, 'retrieval_quality': retrieval_quality, 'avg_retrieval_score': round(avg_retrieval_score, 4)}, ensure_ascii=False)}\n\n"
            return

        # 2. Build prompt (switch template based on retrieval quality)
        prompt = self._build_prompt(query, context_results, scenario_id, retrieval_quality=retrieval_quality, lang=lang)

        # 3. Streaming call to LLM
        yield f"event: status\ndata: {json.dumps({'message': _('pipeline.status_generating', lang)}, ensure_ascii=False)}\n\n"

        try:
            adapter = self._get_llm_adapter()

            # ===== Hybrid answer mode: first force yield disclaimer prefix, then forward LLM streaming output =====
            if retrieval_quality == 'low':
                hybrid_disclaimer = _('pipeline.hybrid_disclaimer', lang)
                full_answer += hybrid_disclaimer
                yield f"event: answer\ndata: {json.dumps({'content': hybrid_disclaimer}, ensure_ascii=False)}\n\n"
                logger.info("[Resoinse Process] Hybrid answer disclaimer prefix injected")

            # Consume synchronous streaming output via async generator, avoiding blocking event loop when iterating synchronous generator
            async for chunk in self._async_iter_stream(adapter, prompt):
                if chunk.get('done'):
                    break
                content = chunk.get('message', {}).get('content', '')
                if content:
                    full_answer += content
                    yield f"event: answer\ndata: {json.dumps({'content': content}, ensure_ascii=False)}\n\n"

            # After streaming completes, ensure hybrid answer format is correct again (remove LLM's possibly duplicated disclaimer)
            if retrieval_quality == 'low' and full_answer:
                full_answer = self._ensure_hybrid_format(full_answer, lang=lang)

        except Exception as e:
            error_msg = _('pipeline.llm_exception', lang, str(e))
            logger.error(error_msg)
            yield f"event: error\ndata: {json.dumps({'message': error_msg}, ensure_ascii=False)}\n\n"

            trace.add_stage('generation', {'prompt': prompt[:200] + '...'}, {'answer': full_answer, 'error': str(e)})
            trace.set_final_answer(full_answer)
            trace.set_error(str(e))
            trace_manager.save_trace(trace)

            yield f"event: done\ndata: {json.dumps({'duration': round(time.time() - start_time, 2), 'error': str(e), 'intent_info': intent_info}, ensure_ascii=False)}\n\n"
            return

        # 4. Sentence-level tracing
        sentence_tracing = None
        drift_analysis = None
        try:
            sentence_tracing = sentence_tracer.trace(full_answer, context_results, lang=lang)
            drift_analysis = sentence_tracer.analyze_drift(sentence_tracing, lang=lang)
            trace.set_sentence_tracing(sentence_tracing)
            trace.add_stage('sentence_tracing', {'answer': full_answer}, drift_analysis)
            logger.debug(f"[Resoinse Process] Sentence tracing completed: {len(sentence_tracing)} sentences, drift rate: {drift_analysis.get('drift_rate', 0):.2f}")
        except Exception as e:
            logger.warning(f"Sentence tracing failed: {e}")

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

        # Send completion event (carries avg_retrieval_score for frontend final confirmation of conclusion card status)
        yield f"event: done\ndata: {json.dumps({'duration': duration, 'context_count': len(context_results), 'intent_info': intent_info, 'retrieval_quality': retrieval_quality, 'avg_retrieval_score': round(avg_retrieval_score, 4)}, ensure_ascii=False)}\n\n"

        logger.info(f"[Resoinse Process] Completed in {duration}s, scenario: {scenario_id or 'Default'}")

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
                'available_models': health.get('available_models', [])
            }
        except Exception as e:
            logger.error(f"[Resoinse Process] Failed to check LLM health status: {e}")
            return {
                'healthy': False,
                'error': str(e),
                'provider': config.get('llm.provider', 'ollama')
            }
