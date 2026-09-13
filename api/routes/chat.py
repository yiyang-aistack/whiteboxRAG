"""
Chat API routes
Provides streaming Q&A, tracing query and other interfaces
"""
import uuid
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from service.i18n import _, get_lang_from_request
from service.logger import get_logger
from service.monitor import monitor

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
async def check_health():
    """Check if Ollama service and model are available"""
    try:
        _check_llm_pipeline()

        health = _llm_pipeline.check_health()
        logger.info(f"Health check result: {health}")
        logger.info(f"Available models type: {type(health.get('available_models'))}")
        logger.info(f"Available models: {health.get('available_models')}")

        return {
            'success': True,
            'data': health
        }

    except Exception as e:
        logger.error(f"Health check exception: {e}", exc_info=True)
        return {
            'success': False,
            'healthy': False,
            'error': str(e)
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
            client = _llm_pipeline._get_ollama_client()
            response = client.chat(
                model=_llm_pipeline.llm_model,
                messages=[
                    {'role': 'user', 'content': prompt}
                ],
                stream=False
            )
            answer = response.get('message', {}).get('content', '')
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
            'chunk_count': len(filtered_chunks),
            'scenario_id': scenario_id,
            'sentence_tracing': sentence_tracing,
            'drift_analysis': drift_analysis,
            'error': error
        }

        if original_result:
            result['original_result'] = {
                'answer': original_result.get('answer'),
                'context_count': original_result.get('retrieval_count', 0),
                'context': original_result.get('context'),
                'evaluation': original_result.get('evaluation'),
                'sentence_tracing': original_result.get('sentence_tracing'),
                'drift_analysis': original_result.get('drift_analysis')
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

            contradictions = []
            try:
                from core.sentence_tracing import sentence_tracer
                contradictions = sentence_tracer.detect_contradictions(result['answer'], result['context'])
            except Exception as e:
                logger.warning(f"Semantic contradiction detection failed: {e}")

            results.append({
                'name': variant.name,
                'config': variant.dict(),
                'answer': result['answer'],
                'context': result['context'],
                'context_count': len(result['context']),
                'has_results': result['has_results'],
                'retrieval_mode': result['retrieval_mode'],
                'duration': result['duration'],
                'evaluation': result.get('evaluation'),
                'intent_info': result.get('intent_info'),
                'sentence_tracing': result.get('sentence_tracing'),
                'drift_analysis': result.get('drift_analysis'),
                'contradictions': contradictions,
                'contradiction_count': len(contradictions),
                'recall_diagnosis': result.get('recall_diagnosis', {}),
                'trace_id': result.get('trace_id')
            })

        comparison = _compare_results(results)

        # P1-3: Persist test results to storage/tasks/
        import json as _json
        import uuid as _uuid
        from pathlib import Path as _Path
        from datetime import datetime as _dt
        task_dir = _Path('./storage/tasks')
        task_dir.mkdir(parents=True, exist_ok=True)
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
            with open(task_dir / f'{task_id}.json', 'w', encoding='utf-8') as f:
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
        logger.error(f"A/B测试异常: {e}", exc_info=True)
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
                    'eval_score': result.get('evaluation', {}).get('overall_score', 0),
                    'context_count': len(result['context']),
                    'drift_rate': result.get('drift_analysis', {}).get('drift_rate', 0),
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

        # Persist
        import json as _json
        import uuid as _uuid
        from pathlib import Path as _Path
        from datetime import datetime as _dt
        task_dir = _Path('./storage/tasks')
        task_dir.mkdir(parents=True, exist_ok=True)
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
            with open(task_dir / f'{task_id}.json', 'w', encoding='utf-8') as f:
                _json.dump(task_data, f, ensure_ascii=False, indent=2)
        except Exception as save_err:
            logger.warning(f"批量A/B测试结果持久化失败: {save_err}")

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
        logger.error(f"批量A/B测试异常: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('abtest.batch_failed', lang, str(e))
        )


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
        'best_by_evaluation': None,
        'best_by_recall': None,
        'best_by_drift_rate': None,
        'differences': [],
        'summary': {}
    }

    best_eval = None
    best_eval_score = -1
    best_recall = None
    best_recall_count = -1
    best_drift = None
    best_drift_rate = 1.0

    for result in results:
        eval_score = result.get('evaluation', {}).get('overall_score', 0)
        recall_count = result.get('context_count', 0)
        drift_rate = result.get('drift_analysis', {}).get('drift_rate', 1.0)

        if eval_score > best_eval_score:
            best_eval_score = eval_score
            best_eval = result['name']
        if recall_count > best_recall_count:
            best_recall_count = recall_count
            best_recall = result['name']
        if drift_rate < best_drift_rate:
            best_drift_rate = drift_rate
            best_drift = result['name']

    comparison['best_by_evaluation'] = {
        'name': best_eval,
        'score': best_eval_score
    }
    comparison['best_by_recall'] = {
        'name': best_recall,
        'count': best_recall_count
    }
    comparison['best_by_drift_rate'] = {
        'name': best_drift,
        'drift_rate': best_drift_rate
    }

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
        logger.error(f"获取规则生效率报告失败: {e}", exc_info=True)
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
        logger.error(f"获取规则应用日志失败: {e}", exc_info=True)
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
                logger.warning(f"读取追踪文件失败: {trace_file}, {e}")
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
        logger.error(f"获取对话历史失败: {e}", exc_info=True)
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
        logger.error(f"获取对话统计失败: {e}", exc_info=True)
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

        trace_file = Path('./storage/traces') / f'{trace_id}.json'
        if not trace_file.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('chat.conversation_not_found', lang)
            )

        trace_file.unlink()
        logger.info(f"删除对话记录: {trace_id}")

        return {
            'success': True,
            'message': '对话记录已删除'
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除对话记录失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('chat.conversation_delete_failed', lang, str(e))
        )
