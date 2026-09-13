"""
Scenario management API routes
Provides scenario list, detail query and other endpoints
"""
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from config import scenario_config
from service.i18n import _, get_lang_from_request
from service.logger import get_logger

logger = get_logger('api.scenario')

router = APIRouter(prefix="/api/scenario", tags=["场景管理"])


class ScenarioInfo(BaseModel):
    """Scenario info"""
    scenario_id: str
    scenario_name: str
    description: str


class ScenarioDetail(BaseModel):
    """Scenario detail"""
    scenario_id: str
    scenario_name: str
    description: str
    document_parser: Dict
    vector_store: Dict
    bm25: Dict
    retriever: Dict
    ollama: Dict
    llm_pipeline: Dict
    evaluation: Optional[Dict] = None


@router.get("/list", response_model=Dict, summary="获取场景列表")
async def list_scenarios(request: Request):
    """Get list of all available scenarios"""
    lang = get_lang_from_request(request)
    try:
        scenarios = scenario_config.list_scenarios()

        return {
            'success': True,
            'data': scenarios,
            'total': len(scenarios)
        }

    except Exception as e:
        logger.error(f"获取场景列表失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('scenario.list_failed', lang)}: {str(e)}"
        )


@router.get("/{scenario_id}", response_model=Dict, summary="获取场景详情")
async def get_scenario(scenario_id: str, request: Request):
    """Get detailed config of a specific scenario"""
    try:
        lang = get_lang_from_request(request)
        effective_config = scenario_config.get_effective_config(scenario_id)

        scenario_info = scenario_config.get_scenario(scenario_id)
        if not scenario_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{_('scenario.not_found', lang)}: {scenario_id}"
            )

        detail = {
            'scenario_id': scenario_info.get('scenario_id', scenario_id),
            'scenario_name': scenario_info.get('scenario_name', scenario_id),
            'description': scenario_info.get('description', ''),
            'document_parser': effective_config.get('document_parser', {}),
            'vector_store': effective_config.get('vector_store', {}),
            'bm25': effective_config.get('bm25', {}),
            'retriever': effective_config.get('retriever', {}),
            'ollama': effective_config.get('ollama', {}),
            'llm_pipeline': effective_config.get('llm_pipeline', {}),
            'evaluation': effective_config.get('evaluation'),
        }

        return {
            'success': True,
            'data': detail
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取场景详情失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('scenario.detail_failed', lang)}: {str(e)}"
        )


@router.get("/{scenario_id}/params", response_model=Dict, summary="获取场景检索参数")
async def get_scenario_params(scenario_id: str, request: Request):
    """Get retrieval-related parameters of a specific scenario (for frontend display)"""
    try:
        lang = get_lang_from_request(request)
        effective_config = scenario_config.get_effective_config(scenario_id)

        scenario_info = scenario_config.get_scenario(scenario_id)
        if not scenario_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{_('scenario.not_found', lang)}: {scenario_id}"
            )

        params = {
            'scenario_id': scenario_id,
            'scenario_name': scenario_info.get('scenario_name', scenario_id),
            'chunk_size': effective_config.get('document_parser', {}).get('chunk_size'),
            'chunk_overlap': effective_config.get('document_parser', {}).get('chunk_overlap'),
            'retrieval_mode': effective_config.get('retriever', {}).get('mode'),
            'bm25_weight': effective_config.get('retriever', {}).get('bm25_weight'),
            'vector_top_k': effective_config.get('vector_store', {}).get('top_k'),
            'bm25_top_k': effective_config.get('bm25', {}).get('top_k'),
            'rerank_top_k': effective_config.get('retriever', {}).get('rerank_top_k'),
            'similarity_threshold': effective_config.get('vector_store', {}).get('similarity_threshold'),
            'compression_enabled': effective_config.get('retriever', {}).get('compression', {}).get('enabled'),
            'compression_max_tokens': effective_config.get('retriever', {}).get('compression', {}).get('max_tokens'),
            'llm_temperature': effective_config.get('ollama', {}).get('temperature'),
            'llm_max_tokens': effective_config.get('llm_pipeline', {}).get('max_tokens'),
            'llm_timeout': effective_config.get('ollama', {}).get('timeout'),
        }

        return {
            'success': True,
            'data': params
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取场景参数失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('scenario.params_failed', lang)}: {str(e)}"
        )


@router.post("/{scenario_id}/validate", response_model=Dict, summary="验证场景配置")
async def validate_scenario(scenario_id: str, request: Request):
    """Validate whether the scenario config is valid"""
    lang = get_lang_from_request(request)
    try:
        scenarios = scenario_config.list_scenarios()
        scenario_ids = [s['scenario_id'] for s in scenarios]

        if scenario_id not in scenario_ids:
            return {
                'success': False,
                'valid': False,
                'message': _('scenario.invalid_id', lang),
                'available_scenarios': scenario_ids
            }

        effective_config = scenario_config.get_effective_config(scenario_id)

        checks = []

        if effective_config.get('document_parser', {}).get('chunk_size'):
            checks.append({'name': '分块大小', 'status': 'ok'})
        else:
            checks.append({'name': '分块大小', 'status': 'warning', 'message': '未配置'})

        if effective_config.get('retriever', {}).get('mode'):
            checks.append({'name': '检索模式', 'status': 'ok'})
        else:
            checks.append({'name': '检索模式', 'status': 'warning', 'message': '未配置'})

        if effective_config.get('llm_pipeline', {}).get('system_prompt'):
            checks.append({'name': '系统提示词', 'status': 'ok'})
        else:
            checks.append({'name': '系统提示词', 'status': 'warning', 'message': '未配置'})

        all_ok = all(c['status'] == 'ok' for c in checks)

        return {
            'success': True,
            'valid': all_ok,
            'scenario_id': scenario_id,
            'scenario_name': scenario_config.get_scenario(scenario_id).get('scenario_name', scenario_id),
            'checks': checks,
            'message': _('scenario.config_valid', lang) if all_ok else _('scenario.config_warning', lang)
        }

    except Exception as e:
        logger.error(f"验证场景配置失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{_('scenario.validate_failed', lang)}: {str(e)}"
        )
