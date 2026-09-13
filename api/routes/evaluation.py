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

logger = get_logger('api.evaluation')

router = APIRouter(prefix="/api/evaluation", tags=["评估管理"])

_evaluator = None


def _get_evaluator():
    """Get evaluator instance"""
    global _evaluator
    if _evaluator is None:
        _evaluator = RAGEvaluator()
    return _evaluator


class EvaluationRequest(BaseModel):
    """Evaluation request"""
    query: str = Field(..., description="用户查询")
    context_results: List[Dict] = Field(..., description="检索上下文结果")
    answer: str = Field(..., description="回答文本")
    scenario_id: Optional[str] = Field(None, description="场景ID")


class BatchEvaluationRequest(BaseModel):
    """Batch evaluation request"""
    items: List[EvaluationRequest] = Field(..., description="评估项列表")


class EvaluationReport(BaseModel):
    """Evaluation report"""
    report_id: str
    generated_at: str
    total_count: int
    passing_count: int
    overall_score: float
    metrics_summary: Dict
    detailed_results: List[Dict]


@router.post("/evaluate", response_model=Dict, summary="单条评估")
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
        logger.error(f"评估失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('eval.failed', lang)}: {str(e)}"
        )


@router.post("/batch", response_model=Dict, summary="批量评估")
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
        logger.error(f"批量评估失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('eval.batch_failed', lang)}: {str(e)}"
        )


@router.post("/report", response_model=Dict, summary="生成评估报告")
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

        logger.info(f"评估报告生成成功: {report_id}, 总条数: {len(request.items)}")

        return {
            'success': True,
            'data': report,
            'message': _('eval.report_generate_success', lang, report_id)
        }

    except Exception as e:
        logger.error(f"生成评估报告失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('eval.report_generate_failed', lang)}: {str(e)}"
        )


@router.get("/report/{report_id}", response_model=Dict, summary="获取评估报告")
async def get_report(report_id: str, http_request: Request):
    """
    Get a saved evaluation report
    """
    lang = get_lang_from_request(http_request)
    try:
        report_dir = Path(config.get('evaluation.report_directory', './storage/evaluation'))
        report_file = report_dir / f'{report_id}.json'

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
        logger.error(f"获取评估报告失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('eval.report_get_failed', lang)}: {str(e)}"
        )


@router.get("/reports", response_model=Dict, summary="获取评估报告列表")
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
                logger.warning(f"读取报告文件失败: {json_file}: {e}")

        return {
            'success': True,
            'data': reports,
            'total': len(reports)
        }

    except Exception as e:
        logger.error(f"获取评估报告列表失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('eval.report_list_failed', lang)}: {str(e)}"
        )


@router.get("/metrics", response_model=Dict, summary="获取指标定义")
async def get_metrics(http_request: Request):
    """
    Get definition descriptions of all evaluation metrics
    """
    lang = get_lang_from_request(http_request)
    try:
        metrics_definition = {
            'retrieval_recall': {
                'name': '检索召回率',
                'description': '检索结果的平均相似度分数，反映检索的召回质量',
                'target': '越高越好',
                'range': '[0, 1]'
            },
            'retrieval_score_std': {
                'name': '检索分数标准差',
                'description': '检索结果相似度的离散程度，反映结果的一致性',
                'target': '越低越好',
                'range': '[0, 1]'
            },
            'answer_faithfulness': {
                'name': '答案忠实度',
                'description': '答案中能在上下文中找到的词比例，反映答案的忠实性',
                'target': '越高越好',
                'range': '[0, 1]'
            },
            'answer_relevance': {
                'name': '答案相关性',
                'description': '答案与查询的词重叠比例，反映答案的相关性',
                'target': '越高越好',
                'range': '[0, 1]'
            },
            'context_usage_ratio': {
                'name': '上下文利用率',
                'description': '上下文被答案使用的比例，反映上下文的利用效率',
                'target': '越高越好',
                'range': '[0, 1]'
            },
            'empty_response': {
                'name': '空响应',
                'description': '是否为空响应或无法回答的情况',
                'target': 'False',
                'range': '[True, False]'
            },
            'response_length': {
                'name': '响应长度',
                'description': '答案的字符数',
                'target': '在合理范围内',
                'range': '[0, ∞)'
            }
        }

        return {
            'success': True,
            'data': metrics_definition
        }
    except Exception as e:
        logger.error(f"获取指标定义失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('eval.metrics_get_failed', lang)}: {str(e)}"
        )
