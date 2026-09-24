"""
Chat API routes
Provides streaming Q&A, tracing query and other interfaces
"""
import uuid
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from config import config
from core.retriever import get_retrieval_defaults
from service.i18n import _, get_lang_from_request
from service.logger import get_logger
from service.monitor import monitor
from service.path_safety import safe_join

logger = get_logger('api.chat')

router = APIRouter(prefix="/api/chat", tags=["Chat"])


class ChatRequest(BaseModel):
    """Chat request"""
    kb_id: str = Field(..., description="Knowledge base ID")
    query: str = Field(..., description="User question", min_length=1, max_length=1000)
    stream: bool = Field(True, description="Enable streaming output")
    trace_id: Optional[str] = Field(None, description="Trace ID (optional)")
    scenario_id: Optional[str] = Field(None, description="Scenario ID (optional, uses KB scenario by default)")


class TraceRequest(BaseModel):
    """Trace request"""
    trace_id: str = Field(..., description="Trace ID")


class HistoryRequest(BaseModel):
    """Chat history query request"""
    kb_id: Optional[str] = Field(None, description="Knowledge base ID (optional, filter conversations by KB)")
    start_time: Optional[str] = Field(None, description="Start time (ISO format, e.g. 2026-07-01T00:00:00)")
    end_time: Optional[str] = Field(None, description="End time (ISO format)")
    page: int = Field(1, description="Page number, starting from 1")
    page_size: int = Field(20, description="Items per page, max 50")
    query_filter: Optional[str] = Field(None, description="Query keyword filter")


class SimulateRequest(BaseModel):
    """Simulate request (hypothesis analysis mode)"""
    kb_id: str = Field(..., description="Knowledge base ID")
    query: str = Field(..., description="User question")
    selected_chunks: List[Dict] = Field(..., description="Manually selected chunk list")
    excluded_chunk_ids: Optional[List[str]] = Field(None, description="Excluded chunk ID list")
    compare_with_original: Optional[bool] = Field(False, description="Compare with original retrieval results (triggers extra LLM calls, may be slower)")
    scenario_id: Optional[str] = Field(None, description="Scenario ID")


class MissScanRequest(BaseModel):
    """On-demand miss scan: which chunks exist in the KB but never reached the prompt"""
    kb_id: str = Field(..., description="Knowledge base ID")
    query: str = Field(..., description="User question", min_length=1, max_length=1000)
    scenario_id: Optional[str] = Field(None, description="Scenario ID (optional)")


class FeedbackRequest(BaseModel):
    """User feedback request"""
    trace_id: str = Field(..., description="Trace ID")
    query: str = Field(..., description="User query")
    feedback_type: Optional[str] = Field(None, description="Feedback type: intent_error/recall_missing/answer_wrong/citation_issue/other")
    intent_correction: Optional[str] = Field(None, description="Corrected intent type")
    intent_correction_reason: Optional[str] = Field(None, description="Intent correction reason")
    answer_rating: Optional[int] = Field(None, description="Answer rating (1-5)", ge=1, le=5)
    answer_feedback: Optional[str] = Field(None, description="Answer feedback text")
    kb_id: Optional[str] = Field(None, description="Knowledge base ID")
    scenario_id: Optional[str] = Field(None, description="Scenario ID")
    suggested_action: Optional[Dict] = Field(None, description="Suggested rule action")


class ABTestVariant(BaseModel):
    """A/B test variant config"""
    name: str = Field(..., description="Variant name")
    scenario_id: Optional[str] = Field(None, description="Scenario ID")
    retrieval_mode: Optional[str] = Field(None, description="Retrieval mode: vector/bm25/hybrid")
    bm25_weight: Optional[float] = Field(None, description="BM25 weight")
    vector_weight: Optional[float] = Field(None, description="Vector weight (auto = 1 - bm25_weight if omitted)")
    similarity_threshold: Optional[float] = Field(None, description="Similarity threshold")
    top_k: Optional[int] = Field(None, description="Number of results to return")
    query_rewrite_enabled: Optional[bool] = Field(None, description="Enable query rewriting")
    rerank_enabled: Optional[bool] = Field(None, description="Enable reranking")


class ABTestRequest(BaseModel):
    """A/B test request"""
    kb_id: str = Field(..., description="Knowledge base ID")
    query: str = Field(..., description="User question")
    variants: List[ABTestVariant] = Field(..., description="Variant config list", min_items=2, max_items=5)


# Global LLM pipeline instance (initialized in main.py)
_llm_pipeline = None


def set_llm_pipeline(pipeline):
    """Set LLM pipeline instance"""
    global _llm_pipeline
    _llm_pipeline = pipeline


def _check_llm_pipeline():
    """Check if LLM pipeline is initialized, raise exception if not"""
    if _llm_pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_('api.service_unavailable')
        )


def _resolve_scenario_id(kb_id: str, request_scenario_id: Optional[str] = None) -> Optional[str]:
    """
    Resolve scenario ID: request parameter takes priority, then knowledge base config scenario, finally return None

    Args:
        kb_id: Knowledge base ID
        request_scenario_id: Scenario ID specified in the request

    Returns:
        Resolved scenario ID
    """
    if request_scenario_id:
        return request_scenario_id

    from api.routes.knowledge import get_kb_metadata
    kb_metadata = get_kb_metadata(kb_id)
    if kb_metadata:
        return kb_metadata.get('scenario_id')

    return None


@router.post("/stream", summary="Streaming chat (SSE)")
async def chat_stream(request: ChatRequest, http_request: Request):
    """
    Streaming Q&A interface, uses SSE (Server-Sent Events) for real-time output
    """
    try:
        _check_llm_pipeline()
        lang = get_lang_from_request(http_request)

        import time
        start_time = time.time()

        # Check if knowledge base exists
        from api.routes.knowledge import get_vector_store
        vector_store = get_vector_store()
        if not vector_store.collection_exists(request.kb_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('kb.not_found', lang)
            )

        # Resolve scenario ID
        scenario_id = _resolve_scenario_id(request.kb_id, request.scenario_id)

        logger.info(f"Received chat request, kb_id: {request.kb_id}, Query: {request.query[:50]}..., Scenario: {scenario_id or 'default'}")

        # Generate trace_id
        trace_id = request.trace_id or str(uuid.uuid4())

        if not request.stream:
            # Non-streaming
            result = _llm_pipeline.query(
                kb_id=request.kb_id,
                query=request.query,
                stream=False,
                trace_id=trace_id,
                scenario_id=scenario_id,
                lang=lang
            )

            # Record monitoring
            duration = time.time() - start_time
            monitor.record_request('/api/chat/stream', duration, result.get('error') is not None)

            return {
                'success': True,
                'trace_id': trace_id,
                'answer': result['answer'],
                'context': result['context'],
                'has_results': result['has_results'],
                'retrieval_mode': result['retrieval_mode'],
                'duration': result['duration'],
                'scenario_id': scenario_id,
                'evaluation': result.get('evaluation'),
                'intent_info': result.get('intent_info'),
                'sentence_tracing': result.get('sentence_tracing'),
                'drift_analysis': result.get('drift_analysis'),
                'business_diagnosis': result.get('business_diagnosis')
            }

        # Streaming response
        async def event_generator():
            try:
                async for event in _llm_pipeline.query_stream(
                    kb_id=request.kb_id,
                    query=request.query,
                    trace_id=trace_id,
                    scenario_id=scenario_id,
                    lang=lang
                ):
                    # Check if client disconnected
                    if await http_request.is_disconnected():
                        logger.info(f"Client disconnected, stop streaming output: {trace_id}")
                        break
                    yield event

                # Record monitoring
                duration = time.time() - start_time
                monitor.record_request('/api/chat/stream', duration, False)

            except Exception as e:
                logger.error(f"Streaming output exception: {e}", exc_info=True)
                import json
                yield f"event: error\ndata: {json.dumps({'message': str(e)}, ensure_ascii=False)}\n\n"

                duration = time.time() - start_time
                monitor.record_request('/api/chat/stream', duration, True)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat service exception: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('chat.service_error', lang, str(e))
        )


@router.post("/trace", summary="Get trace info")
@router.get("/trace/{trace_id}", summary="Get trace info")
async def get_trace(http_request: Request, request: TraceRequest = None, trace_id: str = None):
    """Get complete Q&A tracing info by trace ID (supports POST and GET)"""
    try:
        _check_llm_pipeline()
        lang = get_lang_from_request(http_request)

        # Support both POST body and GET path
        _trace_id = trace_id or (request.trace_id if request else None)

        if not _trace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_('chat.trace_id_required', lang)
            )

        trace_data = _llm_pipeline.get_trace(_trace_id)

        if not trace_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('chat.trace_not_found', lang)
            )

        return {
            'success': True,
            'data': trace_data
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get trace info exception: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('chat.trace_get_failed', lang, str(e))
        )


@router.get("/health", summary="Check LLM service health")
async def check_health(http_request: Request):
    """Check if Ollama service and model are available"""
    lang = get_lang_from_request(http_request)
    try:
        _check_llm_pipeline()

        health = _llm_pipeline.check_health()
        logger.info(f"Health check result: {health}")

        return {
            'success': True,
            'data': health
        }

    except Exception as e:
        logger.error(f"Health check exception: {e}", exc_info=True)
        # Keep the standard {success, data, message} envelope on this path too. It used to return
        # {success, healthy, error}, which no client understands: the UI reads `data`/`message`, and
        # the Ollama-down case is exactly the one the endpoint exists to report.
        return {
            'success': False,
            'data': {'healthy': False},
            'message': _('chat.health_failed', lang),
            'error': str(e)
        }


@router.get("/retrieval-defaults", summary="Get default hybrid retrieval parameters")
async def retrieval_defaults():
    """
    Default hybrid retrieval parameters declared in config/settings.yaml (`retriever:`).

    The web UI (static/js/abtest.js behind static/index.html, and static/ab_test.html) fills
    its retrieval parameter inputs - mode, BM25 weight, similarity threshold, top_k - from
    this endpoint instead of hardcoding them, so tuning settings.yaml is enough to change
    what the UI offers.

    Deliberately independent of the LLM pipeline: the correct defaults must be displayable
    while the model backend is down, which is exactly when /api/chat/health cannot answer.
    """
    return {
        'success': True,
        'data': get_retrieval_defaults()
    }


@router.post("/simulate", summary="Simulate chat (hypothesis analysis)")
async def simulate_chat(request: SimulateRequest, http_request: Request):
    """
    Hypothesis analysis mode: allows users to manually select/exclude chunks, simulate regenerating answers
    Used for debugging the impact of "which one was missed in recall" or "which one was incorrectly recalled"
    Supports comparison with original retrieval results
    """
    try:
        _check_llm_pipeline()
        lang = get_lang_from_request(http_request)

        import time
        start_time = time.time()

        scenario_id = _resolve_scenario_id(request.kb_id, request.scenario_id)

        logger.info(f"Received simulate chat request, kb_id: {request.kb_id}, Query: {request.query[:50]}..., Select {len(request.selected_chunks)} chunks, Exclude {len(request.excluded_chunk_ids or [])} chunks, Compare: {request.compare_with_original}")

        filtered_chunks = request.selected_chunks

        if request.excluded_chunk_ids:
            excluded_ids = set(request.excluded_chunk_ids)
            filtered_chunks = [
                chunk for chunk in filtered_chunks
                if chunk.get('id') not in excluded_ids
            ]
            logger.info(f"Exclude chunks, remaining {len(filtered_chunks)} chunks")

        prompt = _llm_pipeline._build_prompt(request.query, filtered_chunks, scenario_id, lang=lang)

        answer = ""
        error = None

        try:
            # Go through the shared LLM adapter. The previous implementation called a
            # `_get_ollama_client()` helper that no longer exists on the pipeline, so every
            # simulation silently fell back to "model unavailable" instead of regenerating.
            adapter = _llm_pipeline._get_llm_adapter()
            response = adapter.chat(
                model=_llm_pipeline.llm_model,
                messages=[{'role': 'user', 'content': prompt}],
                stream=False
            )
            if isinstance(response, dict):
                answer = (response.get('message') or {}).get('content', '') or ''
            else:
                answer = str(response or '')
        except Exception as e:
            error = str(e)
            logger.error(f"LLM call exception: {e}")
            answer = _('pipeline.llm_unavailable', lang)

        sentence_tracing = None
        drift_analysis = None
        try:
            from core.sentence_tracing import sentence_tracer
            sentence_tracing = sentence_tracer.trace(answer, filtered_chunks)
            drift_analysis = sentence_tracer.analyze_drift(sentence_tracing)
        except Exception as e:
            logger.warning(f"Sentence tracing exception: {e}")

        # Hypothesis analysis is exactly where a contradiction matters: after dropping a
        # chunk the answer may start disagreeing with what is left in the prompt.
        contradictions = []
        try:
            from core.contradiction import contradiction_detector
            contradictions = contradiction_detector.detect(answer, filtered_chunks, lang=lang)
            contradiction_detector.attach_to_sentences(sentence_tracing or [], contradictions)
        except Exception as e:
            logger.warning(f"Contradiction detection exception: {e}")

        original_result = None
        if request.compare_with_original:
            try:
                original_result = _llm_pipeline.query(
                    kb_id=request.kb_id,
                    query=request.query,
                    stream=False,
                    scenario_id=scenario_id,
                    lang=lang
                )
                logger.info(f"Get original retrieval results, return {original_result.get('retrieval_count', 0)} chunks")
            except Exception as e:
                logger.warning(f"Get original retrieval results failed: {e}")

        result = {
            'success': True,
            'answer': answer,
            'selected_chunks': request.selected_chunks,
            'filtered_chunks': filtered_chunks,
            'excluded_chunk_ids': request.excluded_chunk_ids,
            'excluded_count': len(request.excluded_chunk_ids or []),
            'chunk_count': len(filtered_chunks),
            'scenario_id': scenario_id,
            'sentence_tracing': sentence_tracing,
            'drift_analysis': drift_analysis,
            'contradictions': contradictions,
            'contradiction_count': len(contradictions),
            'duration': round(time.time() - start_time, 2),
            'error': error
        }

        if original_result:
            result['original_result'] = {
                'answer': original_result.get('answer'),
                'context_count': original_result.get('retrieval_count', 0),
                'context': original_result.get('context'),
                'evaluation': original_result.get('evaluation'),
                'sentence_tracing': original_result.get('sentence_tracing'),
                'drift_analysis': original_result.get('drift_analysis'),
                'contradictions': original_result.get('contradictions') or []
            }

            if answer and original_result.get('answer'):
                result['comparison'] = {
                    'answer_length_diff': len(answer) - len(original_result['answer']),
                    'context_count_diff': len(filtered_chunks) - original_result.get('retrieval_count', 0),
                    'drift_rate_diff': (drift_analysis.get('drift_rate', 0) if drift_analysis else 0) -
                                      (original_result.get('drift_analysis', {}).get('drift_rate', 0) if original_result.get('drift_analysis') else 0)
                }

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Simulate chat exception: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('chat.simulate_failed', lang, str(e))
        )


def _run_miss_scan(kb_id: str, query: str, scenario_id: Optional[str], lang: Optional[str] = None) -> Dict:
    """
    Run the expensive "which chunks never reached the prompt" scan for one query.

    Blocking on purpose: the caller offloads it to a thread so the event loop stays free.

    Args:
        kb_id: Knowledge base ID
        query: User question
        scenario_id: Scenario ID (optional)
        lang: Language code for localized diagnosis text

    Returns:
        Diagnosis payload with potential misses, threshold and timing info
    """
    import time

    from core.recall_diagnostic import recall_diagnostic

    start = time.time()
    retriever = _llm_pipeline.retriever

    # Authoritative basis: re-run the very same retrieval the question used, so the scan
    # (a) excludes exactly the chunks that reached the LLM and (b) compares the remaining
    # chunks against the same similarity threshold.
    retrieval = retriever.retrieve(kb_id, query, scenario_id=scenario_id)
    retrieved_results = retrieval.get('results') or []
    params = retriever.get_effective_params(scenario_id)

    diagnosis = recall_diagnostic.diagnose(
        kb_id=kb_id,
        query=query,
        retrieved_results=retrieved_results,
        vector_store=retriever.vector_store,
        params=params,
        lang=lang,
        debug=True,  # this endpoint exists precisely to run the full scan
    )

    return {
        'query': query,
        'kb_id': kb_id,
        'scenario_id': scenario_id,
        'retrieved_count': len(retrieved_results),
        'threshold': params.get('similarity_threshold'),
        'scan_limit': config.get('recall_diagnostic.full_scan_limit', 500),
        'diagnostic_enabled': bool(diagnosis.get('enabled', False)),
        'potential_misses': diagnosis.get('potential_misses') or [],
        'diagnosis_summary': diagnosis.get('summary') or {},
        'duration': round(time.time() - start, 2),
    }


@router.post("/miss-scan", summary="On-demand knowledge base miss scan")
async def miss_scan(request: MissScanRequest, http_request: Request):
    """
    Scan the knowledge base for relevant chunks that were never recalled.

    The normal chat flow skips this scan (it re-embeds every candidate and would double
    retrieval latency), which is why a trace reports "missed documents scan skipped". This
    endpoint runs it on demand - the UI exposes it as a button inside the retrieval funnel -
    and returns the near-threshold chunks with a structured root cause for each one.
    """
    lang = get_lang_from_request(http_request)
    try:
        _check_llm_pipeline()

        vector_store = _llm_pipeline.retriever.vector_store
        if not vector_store.collection_exists(request.kb_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('kb.not_found', lang)
            )

        scenario_id = _resolve_scenario_id(request.kb_id, request.scenario_id)

        import asyncio
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, _run_miss_scan, request.kb_id, request.query, scenario_id, lang
        )
        return {'success': True, **result}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Miss scan failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('chat.miss_scan_failed', lang, str(e))
        )


@router.post("/feedback", summary="Submit feedback")
async def submit_feedback(request: FeedbackRequest, http_request: Request):
    """
    User feedback interface: collects intent correction, answer rating and other feedback for rule precipitation
    Supports multiple feedback types: intent_error, recall_missing, answer_wrong, citation_issue
    """
    lang = get_lang_from_request(http_request)
    try:
        from core.rule_engine import rule_engine

        feedback_type = request.feedback_type
        if not feedback_type:
            if request.intent_correction:
                feedback_type = 'intent_error'
            elif request.answer_feedback or request.answer_rating:
                feedback_type = 'answer_wrong'
            else:
                feedback_type = 'other'

        feedback = {
            'trace_id': request.trace_id,
            'query': request.query,
            'feedback_type': feedback_type,
            'intent_correction': request.intent_correction,
            'intent_correction_reason': request.intent_correction_reason,
            'answer_rating': request.answer_rating,
            'answer_feedback': request.answer_feedback,
            'kb_id': request.kb_id,
            'scenario_id': request.scenario_id,
            'correction_type': 'intent' if request.intent_correction else 'answer',
            'suggested_action': request.suggested_action,
            'timestamp': str(__import__('datetime').datetime.now())
        }

        suggestions = rule_engine.record_feedback(feedback)

        logger.info(f"  Record feedback, trace_id: {request.trace_id}, feedback_type: {feedback_type}")

        return {
            'success': True,
            'message': 'Feedback recorded',
            'suggested_rules': suggestions
        }

    except Exception as e:
        logger.error(f"Process feedback exception: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('chat.feedback_failed', lang, str(e))
        )


@router.get("/rules", summary="Get rule list")
async def get_rules(http_request: Request):
    """Get all precipitated business rules"""
    lang = get_lang_from_request(http_request)
    try:
        from core.rule_engine import rule_engine
        rules = rule_engine.list_rules()
        return {
            'success': True,
            'data': rules
        }
    except Exception as e:
        logger.error(f"Get rule list exception: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('chat.rules_get_failed', lang, str(e))
        )


@router.post("/rules", summary="Create rule")
async def create_rule(request: Dict, http_request: Request):
    """Create a new business rule"""
    lang = get_lang_from_request(http_request)
    try:
        from core.rule_engine import rule_engine, BusinessRule
        import uuid

        rule_id = request.get('rule_id', str(uuid.uuid4()))
        rule_type = request.get('rule_type', 'intent_routing')
        pattern = request.get('pattern', {})
        action = request.get('action', {})
        description = request.get('description', '')

        rule = BusinessRule(rule_id, rule_type, pattern, action, description)
        rule.created_at = str(__import__('datetime').datetime.now())
        rule_engine.add_rule(rule)
        logger.info(f"Create business rule: {rule_id} ({description})")

        return {
            'success': True,
            'rule_id': rule_id,
            'message': 'Rule created successfully'
        }

    except Exception as e:
        logger.error(f"Create business rule failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('chat.rule_create_failed', lang, str(e))
        )


@router.delete("/rules/{rule_id}", summary="Delete rule")
async def delete_rule(rule_id: str, http_request: Request):
    """Delete the specified business rule"""
    lang = get_lang_from_request(http_request)
    try:
        from core.rule_engine import rule_engine
        rule_engine.remove_rule(rule_id)
        return {
            'success': True,
            'message': 'Rule deleted successfully'
        }
    except Exception as e:
        logger.error(f"Delete business rule failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('chat.rule_delete_failed', lang, str(e))
        )


@router.post("/abtest", summary="A/B test")
async def ab_test(request: ABTestRequest, http_request: Request):
    """
    A/B comparison test: supports comparing results of the same query under different configs
    Can compare different retrieval modes, weight configs, thresholds, etc.
    """
    try:
        _check_llm_pipeline()
        lang = get_lang_from_request(http_request)

        logger.info(f"  A/B test request, kb_id: {request.kb_id}, query: {request.query[:50]}..., variant count: {len(request.variants)}")
        

        results = []
        for variant in request.variants:
            variant_params = {}
            if variant.retrieval_mode:
                variant_params['mode'] = variant.retrieval_mode
            if variant.bm25_weight is not None or variant.vector_weight is not None:
                bm25_w = variant.bm25_weight if variant.bm25_weight is not None else (1 - variant.vector_weight)
                vector_w = variant.vector_weight if variant.vector_weight is not None else (1 - variant.bm25_weight)
                variant_params['bm25_weight'] = bm25_w
                variant_params['vector_weight'] = vector_w
            if variant.similarity_threshold is not None:
                variant_params['similarity_threshold'] = variant.similarity_threshold
            if variant.top_k is not None:
                variant_params['top_k'] = variant.top_k
            if variant.query_rewrite_enabled is not None:
                variant_params['query_rewrite_enabled'] = variant.query_rewrite_enabled
            if variant.rerank_enabled is not None:
                variant_params['rerank_enabled'] = variant.rerank_enabled

            scenario_id = _resolve_scenario_id(request.kb_id, variant.scenario_id)

            result = _llm_pipeline.query(
                kb_id=request.kb_id,
                query=request.query,
                stream=False,
                scenario_id=scenario_id,
                lang=lang,
                **variant_params
            )

            # query() already ran the structured check (core/contradiction.py) and returned
            # its findings, so re-use them instead of paying for a second pass over the same
            # answer; only compute them when an older pipeline did not provide them.
            contradictions = result.get('contradictions')
            if contradictions is None:
                try:
                    from core.sentence_tracing import sentence_tracer
                    contradictions = sentence_tracer.detect_contradictions(
                        result['answer'], result['context'], lang)
                except Exception as e:
                    logger.warning(f"Contradiction detection failed: {e}")
                    contradictions = []

            results.append({
                'name': variant.name,
                'config': variant.dict(),
                'answer': result['answer'],
                'context': result['context'],
                'context_count': len(result['context']),
                'has_results': result['has_results'],
                'retrieval_mode': result['retrieval_mode'],
                'duration': result['duration'],
                'evaluation': result.get('evaluation') or {},
                'intent_info': result.get('intent_info') or {},
                'sentence_tracing': result.get('sentence_tracing') or [],
                'drift_analysis': result.get('drift_analysis') or {},
                'contradictions': contradictions,
                'contradiction_count': len(contradictions),
                'recall_diagnosis': result.get('recall_diagnosis', {}),
                'trace_id': result.get('trace_id')
            })

        comparison = _compare_results(results)

        # Persist the test result so it can be reviewed later. This must not live in
        # async_tasks.task_directory: the TaskManager cleanup job deletes every *.json there
        # once it is older than timeout * 3 (~15 minutes), which silently removed saved A/B
        # results.
        import json as _json
        import uuid as _uuid
        from pathlib import Path as _Path
        from datetime import datetime as _dt
        result_dir = _Path(config.get('abtest.result_directory', './storage/abtest_results'))
        result_dir.mkdir(parents=True, exist_ok=True)
        task_id = str(_uuid.uuid4())
        task_data = {
            'task_id': task_id,
            'type': 'abtest',
            'query': request.query,
            'kb_id': request.kb_id,
            'variants': results,
            'comparison': comparison,
            'created_at': _dt.now().isoformat()
        }
        try:
            with open(result_dir / f'{task_id}.json', 'w', encoding='utf-8') as f:
                _json.dump(task_data, f, ensure_ascii=False, indent=2)
        except Exception as save_err:
            logger.warning(f"A/B test result persistence failed: {save_err}")

        return {
            'success': True,
            'task_id': task_id,
            'query': request.query,
            'kb_id': request.kb_id,
            'variants': results,
            'comparison': comparison
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"A/B test failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('abtest.failed', lang, str(e))
        )


class ABTestBatchRequest(BaseModel):
    """A/B batch test request"""
    kb_id: str = Field(..., description="Knowledge base ID")
    queries: List[str] = Field(..., description="Test query list", min_items=1, max_items=50)
    variants: List[ABTestVariant] = Field(..., description="Variant config list", min_items=2, max_items=5)


@router.post("/abtest/batch", summary="A/B batch test")
async def ab_test_batch(request: ABTestBatchRequest, http_request: Request):
    """
    Batch A/B test: run all variants on multiple queries, output metric averages
    """
    try:
        _check_llm_pipeline()
        lang = get_lang_from_request(http_request)
        logger.info(f"Received A/B test batch request for knowledge base: {request.kb_id}, queries: {len(request.queries)}, variants: {len(request.variants)}")

        all_results = []
        for query in request.queries:
            single_request = ABTestRequest(kb_id=request.kb_id, query=query, variants=request.variants)
            # Reuse single test logic
            variant_results = []
            for variant in request.variants:
                variant_params = {}
                if variant.retrieval_mode:
                    variant_params['mode'] = variant.retrieval_mode
                if variant.bm25_weight is not None or variant.vector_weight is not None:
                    bm25_w = variant.bm25_weight if variant.bm25_weight is not None else (1 - variant.vector_weight)
                    vector_w = variant.vector_weight if variant.vector_weight is not None else (1 - variant.bm25_weight)
                    variant_params['bm25_weight'] = bm25_w
                    variant_params['vector_weight'] = vector_w
                if variant.similarity_threshold is not None:
                    variant_params['similarity_threshold'] = variant.similarity_threshold
                if variant.top_k is not None:
                    variant_params['top_k'] = variant.top_k
                if variant.query_rewrite_enabled is not None:
                    variant_params['query_rewrite_enabled'] = variant.query_rewrite_enabled
                if variant.rerank_enabled is not None:
                    variant_params['rerank_enabled'] = variant.rerank_enabled

                scenario_id = _resolve_scenario_id(request.kb_id, variant.scenario_id)
                result = _llm_pipeline.query(
                    kb_id=request.kb_id, query=query, stream=False,
                    scenario_id=scenario_id, lang=lang, **variant_params
                )
                variant_results.append({
                    'name': variant.name,
                    'eval_score': (result.get('evaluation') or {}).get('overall_score', 0),
                    'context_count': len(result['context']),
                    'drift_rate': (result.get('drift_analysis') or {}).get('drift_rate', 0),
                    'trace_id': result.get('trace_id')
                })
            all_results.append({'query': query, 'variants': variant_results})

        # Calculate average for each variant
        summary = []
        for i, variant in enumerate(request.variants):
            scores = [r['variants'][i]['eval_score'] for r in all_results]
            counts = [r['variants'][i]['context_count'] for r in all_results]
            drifts = [r['variants'][i]['drift_rate'] for r in all_results]
            summary.append({
                'name': variant.name,
                'avg_eval_score': round(sum(scores) / len(scores), 4) if scores else 0,
                'avg_context_count': round(sum(counts) / len(counts), 1) if counts else 0,
                'avg_drift_rate': round(sum(drifts) / len(drifts), 4) if drifts else 0
            })

        # Mark best variant
        best_eval = max(summary, key=lambda x: x['avg_eval_score']) if summary else None
        best_recall = max(summary, key=lambda x: x['avg_context_count']) if summary else None
        best_drift = min(summary, key=lambda x: x['avg_drift_rate']) if summary else None

        # Persist (see the single-query handler: not under async_tasks.task_directory, whose
        # cleanup job sweeps it)
        import json as _json
        import uuid as _uuid
        from pathlib import Path as _Path
        from datetime import datetime as _dt
        result_dir = _Path(config.get('abtest.result_directory', './storage/abtest_results'))
        result_dir.mkdir(parents=True, exist_ok=True)
        task_id = str(_uuid.uuid4())
        task_data = {
            'task_id': task_id,
            'type': 'abtest_batch',
            'kb_id': request.kb_id,
            'query_count': len(request.queries),
            'variant_count': len(request.variants),
            'results': all_results,
            'summary': summary,
            'best_by_eval': best_eval,
            'best_by_recall': best_recall,
            'best_by_drift': best_drift,
            'created_at': _dt.now().isoformat()
        }
        try:
            with open(result_dir / f'{task_id}.json', 'w', encoding='utf-8') as f:
                _json.dump(task_data, f, ensure_ascii=False, indent=2)
        except Exception as save_err:
            logger.warning(f"Batch A/B test result persistence failed: {save_err}")

        return {
            'success': True,
            'task_id': task_id,
            'query_count': len(request.queries),
            'results': all_results,
            'summary': summary,
            'best_by_eval': best_eval,
            'best_by_recall': best_recall,
            'best_by_drift': best_drift
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Batch A/B test failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('abtest.batch_failed', lang, str(e))
        )


def _raw_metric(result: Dict, name: str) -> Optional[float]:
    """Raw value of one evaluation metric, independent of the rubric's pass lines.

    A/B winners used to be picked from ``overall_score`` alone, which is normalized
    against the scenario pass lines: two variants can only be ranked by it if they were
    scored by the same rubric *and* the same scenario. These raw metrics are what the
    score is built from, so they survive a rubric change.
    """
    metric = ((result.get('evaluation') or {}).get('metrics') or {}).get(name) or {}
    value = metric.get('value')
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _scores_comparable(results: List[Dict]) -> bool:
    """True when every variant was scored by the same rubric and scenario."""
    signatures = {
        ((result.get('evaluation') or {}).get('rubric_version'),
         (result.get('evaluation') or {}).get('scenario_id'))
        for result in results
    }
    return len(signatures) <= 1


def _compare_results(results: List[Dict]) -> Dict:
    """
    Compare A/B test results

    Args:
        results: List of results for each variant

    Returns:
        Comparison analysis result
    """
    if len(results) < 2:
        return {}

    comparison = {
        # Rubric-dependent winner: only meaningful inside one scenario/rubric, which is
        # what `score_comparable` reports.
        'best_by_evaluation': None,
        # Rubric-independent winners, straight from the metric values.
        'best_by_grounding': None,
        'best_by_citation_coverage': None,
        'best_by_recall': None,
        'best_by_drift_rate': None,
        'score_comparable': True,
        'rubric_version': None,
        'differences': [],
        'summary': {}
    }

    best_eval = None
    best_eval_score = -1
    best_recall = None
    best_recall_count = -1
    best_drift = None
    best_drift_rate = 1.0
    best_grounding = None
    best_grounding_value = -1.0
    best_citation = None
    best_citation_value = -1.0

    for result in results:
        # Use `or {}` to guard against dict keys that are explicitly None (a Python
        # classic: dict.get(key, default) only returns default when the key is absent,
        # NOT when the key exists with value None). Both evaluation and drift_analysis
        # can legitimately be None when their upstream stages fail (e.g. LLM offline).
        eval_score = (result.get('evaluation') or {}).get('overall_score', 0)
        recall_count = result.get('context_count', 0)
        drift_rate = (result.get('drift_analysis') or {}).get('drift_rate', 1.0)
        grounding = _raw_metric(result, 'answer_faithfulness')
        citation = _raw_metric(result, 'citation_coverage')

        if eval_score > best_eval_score:
            best_eval_score = eval_score
            best_eval = result['name']
        if recall_count > best_recall_count:
            best_recall_count = recall_count
            best_recall = result['name']
        if drift_rate < best_drift_rate:
            best_drift_rate = drift_rate
            best_drift = result['name']
        if grounding is not None and grounding > best_grounding_value:
            best_grounding_value = grounding
            best_grounding = result['name']
        if citation is not None and citation > best_citation_value:
            best_citation_value = citation
            best_citation = result['name']

    comparison['best_by_evaluation'] = {
        'name': best_eval,
        'score': best_eval_score
    }
    comparison['best_by_grounding'] = {
        'name': best_grounding,
        'answer_faithfulness': best_grounding_value if best_grounding else None
    }
    comparison['best_by_citation_coverage'] = {
        'name': best_citation,
        'citation_coverage': best_citation_value if best_citation else None
    }
    comparison['best_by_recall'] = {
        'name': best_recall,
        'count': best_recall_count
    }
    comparison['best_by_drift_rate'] = {
        'name': best_drift,
        'drift_rate': best_drift_rate
    }

    # Flag instead of silently crowning a winner: `overall_score` is normalized against
    # the scenario pass lines, so variants scored by different scenarios -- or by
    # different rubric versions -- are simply not on the same scale.
    comparison['score_comparable'] = _scores_comparable(results)
    comparison['rubric_version'] = (results[0].get('evaluation') or {}).get('rubric_version')

    answer_lengths = [len(r['answer']) for r in results]
    context_counts = [r.get('context_count', 0) for r in results]
    eval_scores = [r.get('evaluation', {}).get('overall_score', 0) for r in results]
    drift_rates = [r.get('drift_analysis', {}).get('drift_rate', 0) for r in results]
    contradiction_counts = [r.get('contradiction_count', 0) for r in results]

    comparison['summary'] = {
        'avg_answer_length': sum(answer_lengths) / len(answer_lengths),
        'min_answer_length': min(answer_lengths),
        'max_answer_length': max(answer_lengths),
        'avg_context_count': sum(context_counts) / len(context_counts),
        'min_context_count': min(context_counts),
        'max_context_count': max(context_counts),
        'avg_evaluation_score': sum(eval_scores) / len(eval_scores),
        'min_evaluation_score': min(eval_scores),
        'max_evaluation_score': max(eval_scores),
        'avg_drift_rate': sum(drift_rates) / len(drift_rates),
        'min_drift_rate': min(drift_rates),
        'max_drift_rate': max(drift_rates),
        'avg_contradictions': sum(contradiction_counts) / len(contradiction_counts)
    }

    for i in range(len(results)):
        for j in range(i + 1, len(results)):
            diff = {
                'variant_a': results[i]['name'],
                'variant_b': results[j]['name'],
                'answer_length_diff': len(results[i]['answer']) - len(results[j]['answer']),
                'context_count_diff': results[i].get('context_count', 0) - results[j].get('context_count', 0),
                'eval_score_diff': (results[i].get('evaluation', {}).get('overall_score', 0) -
                                   results[j].get('evaluation', {}).get('overall_score', 0)),
                'drift_rate_diff': (results[i].get('drift_analysis', {}).get('drift_rate', 0) -
                                   results[j].get('drift_analysis', {}).get('drift_rate', 0))
            }
            comparison['differences'].append(diff)

    return comparison


@router.get("/rule-effectiveness", summary="Get rule effectiveness report")
async def get_rule_effectiveness(http_request: Request, rule_id: Optional[str] = None):
    """
    Get rule effectiveness report
    Can query a single rule or all rules' effectiveness
    """
    lang = get_lang_from_request(http_request)
    try:
        from core.rule_engine import rule_engine
        report = rule_engine.get_rule_effectiveness_report(rule_id)
        return {
            'success': True,
            'data': report
        }
    except Exception as e:
        logger.error(f"Failed to get rule effectiveness report: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('chat.rule_effectiveness_failed', lang, str(e))
        )


@router.get("/rule-logs", summary="Get rule logs")
async def get_rule_logs(http_request: Request, rule_id: Optional[str] = None, limit: int = 50):
    """
    Get rule application logs
    """
    lang = get_lang_from_request(http_request)
    try:
        from core.rule_engine import rule_engine
        logs = rule_engine.get_application_logs(rule_id, limit)
        return {
            'success': True,
            'data': logs
        }
    except Exception as e:
        logger.error(f"Failed to get rule application logs: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('chat.rule_logs_failed', lang, str(e))
        )


@router.post("/history", summary="Get conversation history")
async def get_conversation_history(request: HistoryRequest, http_request: Request):
    """
    Get chat history list, supports filtering by knowledge base, time range, keyword
    """
    lang = get_lang_from_request(http_request)
    try:
        import json
        import glob
        from pathlib import Path
        from datetime import datetime

        trace_dir = Path('./storage/traces')
        if not trace_dir.exists():
            return {
                'success': True,
                'data': [],
                'total': 0,
                'page': request.page,
                'page_size': request.page_size
            }

        trace_files = sorted(
            glob.glob(str(trace_dir / '*.json')),
            key=lambda f: Path(f).stat().st_mtime,
            reverse=True
        )

        filtered = []
        for trace_file in trace_files:
            try:
                with open(trace_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                if request.kb_id and data.get('kb_id') != request.kb_id:
                    continue

                if request.start_time:
                    try:
                        start_dt = datetime.fromisoformat(request.start_time.replace('Z', '+00:00'))
                        if data.get('created_at'):
                            created_dt = datetime.fromisoformat(data.get('created_at').replace('Z', '+00:00'))
                            if created_dt < start_dt:
                                continue
                    except Exception:
                        pass

                if request.end_time:
                    try:
                        end_dt = datetime.fromisoformat(request.end_time.replace('Z', '+00:00'))
                        if data.get('created_at'):
                            created_dt = datetime.fromisoformat(data.get('created_at').replace('Z', '+00:00'))
                            if created_dt > end_dt:
                                continue
                    except Exception:
                        pass

                if request.query_filter:
                    query = data.get('query', '')
                    answer = data.get('final_answer', '')
                    if request.query_filter not in query and request.query_filter not in answer:
                        continue

                eval_score = data.get('evaluation', {}).get('overall_score', 0)
                is_passing = data.get('evaluation', {}).get('is_passing', False)
                context_count = len(data.get('context', [])) if isinstance(data.get('context'), list) else data.get('retrieval_count', 0)

                filtered.append({
                    'trace_id': data.get('trace_id'),
                    'kb_id': data.get('kb_id'),
                    'query': data.get('query'),
                    'answer': data.get('final_answer'),
                    'created_at': data.get('created_at'),
                    'duration': data.get('duration'),
                    'intent_type': data.get('intent_info', {}).get('intent_type'),
                    'intent_confidence': data.get('intent_info', {}).get('confidence'),
                    'evaluation_score': eval_score,
                    'is_passing': is_passing,
                    'context_count': context_count,
                    'has_results': data.get('has_results', False)
                })
            except Exception as e:
                logger.warning(f"Failed to read trace file: {trace_file}, {e}")
                continue

        total = len(filtered)
        page_size = min(request.page_size, 50)
        start = (request.page - 1) * page_size
        end = start + page_size
        paginated = filtered[start:end]

        return {
            'success': True,
            'data': paginated,
            'total': total,
            'page': request.page,
            'page_size': page_size,
            'total_pages': (total + page_size - 1) // page_size
        }

    except Exception as e:
        logger.error(f"Failed to get conversation history: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('chat.history_get_failed', lang, str(e))
        )


@router.get("/history/stats", summary="Get conversation stats")
async def get_conversation_stats(http_request: Request, kb_id: Optional[str] = None):
    """
    Get chat statistics
    """
    lang = get_lang_from_request(http_request)
    try:
        import json
        import glob
        from pathlib import Path
        from datetime import datetime, timedelta

        trace_dir = Path('./storage/traces')
        if not trace_dir.exists():
            return {
                'success': True,
                'data': {
                    'total_conversations': 0,
                    'today_conversations': 0,
                    'avg_duration': 0,
                    'pass_rate': 0,
                    'avg_evaluation_score': 0
                }
            }

        trace_files = glob.glob(str(trace_dir / '*.json'))

        total = 0
        today_count = 0
        total_duration = 0
        pass_count = 0
        total_eval_score = 0

        today = datetime.now().date()

        for trace_file in trace_files:
            try:
                with open(trace_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                if kb_id and data.get('kb_id') != kb_id:
                    continue

                total += 1
                total_duration += data.get('duration', 0)

                if data.get('created_at'):
                    try:
                        created_dt = datetime.fromisoformat(data.get('created_at').replace('Z', '+00:00'))
                        if created_dt.date() == today:
                            today_count += 1
                    except Exception:
                        pass

                is_passing = data.get('evaluation', {}).get('is_passing', False)
                if is_passing:
                    pass_count += 1

                eval_score = data.get('evaluation', {}).get('overall_score', 0)
                total_eval_score += eval_score

            except Exception as e:
                continue

        return {
            'success': True,
            'data': {
                'total_conversations': total,
                'today_conversations': today_count,
                'avg_duration': round(total_duration / total, 2) if total > 0 else 0,
                'pass_rate': round(pass_count / total * 100, 2) if total > 0 else 0,
                'avg_evaluation_score': round(total_eval_score / total, 4) if total > 0 else 0
            }
        }

    except Exception as e:
        logger.error(f"Failed to get conversation stats: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('chat.stats_get_failed', lang, str(e))
        )


@router.delete("/history/{trace_id}", summary="Delete conversation")
async def delete_conversation(trace_id: str, http_request: Request):
    """
    Delete the specified chat record
    """
    lang = get_lang_from_request(http_request)
    try:
        from pathlib import Path

        # trace_id comes from the URL: safe_join keeps it a single path segment so a crafted
        # id cannot unlink a file outside the trace directory.
        trace_dir = Path(config.get('trace.trace_directory', './storage/traces'))
        trace_file = safe_join(trace_dir, f'{trace_id}.json')
        if not trace_file.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('chat.conversation_not_found', lang)
            )

        trace_file.unlink()
        logger.info(f"Deleted conversation record: {trace_id}")

        return {
            'success': True,
            'message': _('chat.conversation_delete_success', lang)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete conversation record: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('chat.conversation_delete_failed', lang, str(e))
        )