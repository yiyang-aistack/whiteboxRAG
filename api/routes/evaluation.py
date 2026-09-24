"""
Evaluation report API routes
Provides single evaluation, batch evaluation, report generation and other endpoints
"""
import json
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from config import config
from core.evaluator import RAGEvaluator
from service.i18n import _, get_lang_from_request
from service.logger import get_logger
from service.path_safety import safe_join

logger = get_logger('api.evaluation')

router = APIRouter(prefix="/api/evaluation", tags=["evaluation"])

_evaluator = None


def _get_evaluator():
    """Get evaluator instance"""
    global _evaluator
    if _evaluator is None:
        _evaluator = RAGEvaluator()
    return _evaluator


class EvaluationRequest(BaseModel):
    """Evaluation request"""
    query: str = Field(..., description="User query text")
    context_results: List[Dict] = Field(..., description="Retrieved context results")
    answer: str = Field(..., description="Answer text from the model")
    scenario_id: Optional[str] = Field(None, description="Scenario ID for context")


class BatchEvaluationRequest(BaseModel):
    """Batch evaluation request"""
    items: List[EvaluationRequest] = Field(..., description="Batch of evaluation requests")


class EvaluationReport(BaseModel):
    """Evaluation report"""
    report_id: str
    generated_at: str
    total_count: int
    passing_count: int
    overall_score: float
    metrics_summary: Dict
    detailed_results: List[Dict]


@router.post("/evaluate", response_model=Dict, summary="Evaluate a single Q&A pair")
async def evaluate(request: EvaluationRequest, http_request: Request):
    """
    Evaluate a single Q&A pair
    """
    lang = get_lang_from_request(http_request)
    try:
        evaluator = _get_evaluator()
        result = evaluator.evaluate(
            query=request.query,
            context_results=request.context_results,
            answer=request.answer,
            scenario_id=request.scenario_id
        )

        return {
            'success': True,
            'data': result
        }

    except Exception as e:
        logger.error(f"Evaluate failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('eval.failed', lang)}: {str(e)}"
        )


@router.post("/batch", response_model=Dict, summary="Batch evaluate multiple Q&A pairs")
async def batch_evaluate(request: BatchEvaluationRequest, http_request: Request):
    """
    Batch evaluate multiple Q&A pairs
    """
    lang = get_lang_from_request(http_request)
    try:
        evaluator = _get_evaluator()
        results = []
        passing_count = 0
        total_scores = []

        for item in request.items:
            result = evaluator.evaluate(
                query=item.query,
                context_results=item.context_results,
                answer=item.answer,
                scenario_id=item.scenario_id
            )
            results.append(result)

            if result.get('is_passing'):
                passing_count += 1

            if result.get('overall_score'):
                total_scores.append(result['overall_score'])

        overall_score = sum(total_scores) / len(total_scores) if total_scores else 0.0

        metrics_summary = {}
        if results:
            first_result = results[0]
            for metric_name, metric_data in first_result.get('metrics', {}).items():
                values = [r['metrics'].get(metric_name, {}).get('value') for r in results]
                valid_values = [v for v in values if v is not None and isinstance(v, (int, float))]

                if valid_values:
                    metrics_summary[metric_name] = {
                        'avg': round(sum(valid_values) / len(valid_values), 4),
                        'min': min(valid_values),
                        'max': max(valid_values),
                        'pass_rate': sum(1 for r in results if r['metrics'].get(metric_name, {}).get('is_pass')) / len(results)
                    }

        return {
            'success': True,
            'data': {
                'total_count': len(request.items),
                'passing_count': passing_count,
                'overall_score': round(overall_score, 4),
                'metrics_summary': metrics_summary,
                'detailed_results': results
            }
        }

    except Exception as e:
        logger.error(f"Batch evaluation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('eval.batch_failed', lang)}: {str(e)}"
        )


@router.post("/report", response_model=Dict, summary="Generate evaluation report")
async def generate_report(request: BatchEvaluationRequest, http_request: Request):
    """
    Generate evaluation report (includes save feature)
    """
    lang = get_lang_from_request(http_request)
    try:
        import uuid
        from datetime import datetime

        evaluator = _get_evaluator()
        results = []
        passing_count = 0
        total_scores = []

        for item in request.items:
            result = evaluator.evaluate(
                query=item.query,
                context_results=item.context_results,
                answer=item.answer,
                scenario_id=item.scenario_id
            )
            results.append(result)

            if result.get('is_passing'):
                passing_count += 1

            if result.get('overall_score'):
                total_scores.append(result['overall_score'])

        overall_score = sum(total_scores) / len(total_scores) if total_scores else 0.0

        metrics_summary = {}
        if results:
            first_result = results[0]
            for metric_name, metric_data in first_result.get('metrics', {}).items():
                values = [r['metrics'].get(metric_name, {}).get('value') for r in results]
                valid_values = [v for v in values if v is not None and isinstance(v, (int, float))]

                if valid_values:
                    metrics_summary[metric_name] = {
                        'avg': round(sum(valid_values) / len(valid_values), 4),
                        'min': min(valid_values),
                        'max': max(valid_values),
                        'pass_rate': sum(1 for r in results if r['metrics'].get(metric_name, {}).get('is_pass')) / len(results)
                    }

        report_id = str(uuid.uuid4())[:12]
        generated_at = datetime.now().isoformat()

        report = {
            'report_id': report_id,
            'generated_at': generated_at,
            'total_count': len(request.items),
            'passing_count': passing_count,
            'overall_score': round(overall_score, 4),
            'metrics_summary': metrics_summary,
            'detailed_results': results
        }

        report_dir = Path(config.get('evaluation.report_directory', './storage/evaluation'))
        report_dir.mkdir(parents=True, exist_ok=True)

        report_file = report_dir / f'{report_id}.json'
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        logger.info(f"Generate evaluation report successfully: {report_id}, Total items: {len(request.items)}")

        return {
            'success': True,
            'data': report,
            'message': _('eval.report_generate_success', lang, report_id)
        }

    except Exception as e:
        logger.error(f"Generate evaluation report failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('eval.report_generate_failed', lang)}: {str(e)}"
        )


@router.get("/report/{report_id}", response_model=Dict, summary="Get evaluation report")
async def get_report(report_id: str, http_request: Request):
    """
    Get a saved evaluation report
    """
    lang = get_lang_from_request(http_request)
    try:
        report_dir = Path(config.get('evaluation.report_directory', './storage/evaluation'))
        # report_id comes straight from the URL path: keep it a single path segment so a
        # crafted id cannot read a *.json file outside the report directory.
        report_file = safe_join(report_dir, f'{report_id}.json')

        if not report_file.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('eval.report_not_found', lang, report_id)
            )

        with open(report_file, 'r', encoding='utf-8') as f:
            report = json.load(f)

        return {
            'success': True,
            'data': report
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get evaluation report failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('eval.report_get_failed', lang)}: {str(e)}"
        )


@router.get("/reports", response_model=Dict, summary="List evaluation reports")
async def list_reports(http_request: Request):
    """
    Get list of all evaluation reports
    """
    lang = get_lang_from_request(http_request)
    try:
        report_dir = Path(config.get('evaluation.report_directory', './storage/evaluation'))
        report_dir.mkdir(parents=True, exist_ok=True)

        reports = []
        for json_file in sorted(report_dir.glob('*.json'), reverse=True):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    report = json.load(f)
                    reports.append({
                        'report_id': report.get('report_id', json_file.stem),
                        'generated_at': report.get('generated_at', ''),
                        'total_count': report.get('total_count', 0),
                        'passing_count': report.get('passing_count', 0),
                        'overall_score': report.get('overall_score', 0)
                    })
            except Exception as e:
                logger.warning(f"Read report file failed: {json_file}: {e}")

        return {
            'success': True,
            'data': reports,
            'total': len(reports)
        }

    except Exception as e:
        logger.error(f"List evaluation reports failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('eval.report_list_failed', lang)}: {str(e)}"
        )


@router.get("/metrics", response_model=Dict, summary="Get evaluation metrics")
async def get_metrics(http_request: Request):
    """
    Get definition descriptions of all evaluation metrics
    """
    lang = get_lang_from_request(http_request)
    try:
        # Rubric v2 metric catalogue; every entry states what the metric *actually*
        # computes. (Rubric v1 shipped a "Retrieval Recall" whose implementation was an average
        # similarity, and a "Context Utilization" that divided by the token set of the whole
        # context, i.e. it measured answer length.)
        metrics_definition = {
            'retrieval_score_avg': {
                'name': 'Retrieval Quality (Average Similarity)',
                'description': 'The average of the fused scores of the retrieved passages. Note that this is not recall (true recall requires annotated data): '
                               'it measures "how relevant the retrieved items are", with a lower quality cap linked to the similarity threshold of the retrieval configuration',
                'target': 'The higher the better',
                'range': '[0, 1]'
            },
            'retrieval_score_std': {
                'name': 'Retrieval Score Standard Deviation',
                'description': 'The spread of the fused scores of the retrieved passages. Only applicable when recall is 1',
                'target': 'The lower the better',
                'range': '[0, 1]'
            },
            'answer_faithfulness': {
                'name': 'Answer Faithfulness',
                'description': 'The semantic similarity of the answer with the retrieved context (Embedding cosine similarity); degrades to word coverage when embeddings are not available',
                'target': 'The higher the better',
                'range': '[0, 1]'
            },
            'semantic_consistency': {
                'name': 'Semantic Consistency',
                'description': 'The average of the semantic similarities of the answer with the context, used to detect sentences that do not match the context',
                'target': 'The higher the better',
                'range': '[0, 1]'
            },
            'citation_coverage': {
                'name': 'Citation Coverage',
                'description': 'The proportion of sentences with parsable citation markers',
                'target': 'The higher the better',
                'range': '[0, 1]'
            },
            'answer_relevance': {
                'name': 'Answer Relevance',
                'description': 'The semantic similarity of the answer with the question (Embedding cosine similarity); degrades to word overlap when embeddings are not available',
                'target': 'The higher the better',
                'range': '[0, 1]'
            },
            'hallucination_rate': {
                'name': 'Hallucination Rate',
                'description': 'The proportion of sentences with a similarity below evaluation.hallucination_sim_floor',
                'target': 'The lower the better',
                'range': '[0, 1]'
            },
            'rejection_accuracy': {
                'name': 'Rejection Accuracy',
                'description': 'The proportion of sentences that are rejected as out-of-scope questions',
                'target': 'The higher the better',  
                'range': '[0, 1]'
            },
            'empty_response': {
                'name': 'Empty Response',
                'description': 'Whether the answer is the default response when no relevant information is found',
                'target': 'False',
                'range': '[True, False]'
            },
            'response_length': {
                'name': 'Response Length',
                'description': 'The max length of the answer',
                'target': 'Within reasonable range',
                'range': '[0, ∞)'
            }
        }

        # The rubric version travels with the catalogue: a client that caches metric
        # definitions must not mix them with scores produced by another rubric.
        evaluator = _get_evaluator()
        return {
            'success': True,
            'data': metrics_definition,
            'rubric_version': evaluator.rubric_version,
            'pass_score': evaluator.pass_score
        }
    except Exception as e:
        logger.error(f"Get metrics definition failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('eval.metrics_get_failed', lang)}: {str(e)}"
        )
