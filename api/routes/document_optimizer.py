"""
Document optimization API routes
Provides document analysis and optimization features
"""
import os
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, Field

from config import config
from docAnalyze import DocumentAnalyzer
from service.i18n import _, get_lang_from_request
from service.logger import get_logger
from service.path_safety import safe_join, sanitize_filename

logger = get_logger('api.document_optimizer')

router = APIRouter(prefix="/api/document", tags=["文档优化"])


class AnalyzeRequest(BaseModel):
    """Document analysis request"""
    kb_id: Optional[str] = Field(None, description="知识库ID（可选）")


class AnalyzeResponse(BaseModel):
    """Document analysis response"""
    success: bool
    coverage_analysis: Dict
    query_effectiveness: Dict
    document_issues: List[Dict]
    suggestion_file: Optional[str] = None
    optimized_file: Optional[str] = None


class OptimizeRequest(BaseModel):
    """Document optimization request"""
    kb_id: Optional[str] = Field(None, description="知识库ID（可选）")
    output_dir: Optional[str] = Field(None, description="输出目录（可选）")


class OptimizeResponse(BaseModel):
    """Document optimization response"""
    success: bool
    message: str
    documents: List[Dict]


@router.post("/analyze", response_model=AnalyzeResponse, summary="分析文档")
async def analyze_documents(request: AnalyzeRequest, req: Request):
    """Analyze document quality of a specified knowledge base"""
    lang = get_lang_from_request(req)
    try:
        analyzer = DocumentAnalyzer(kb_id=request.kb_id)
        
        analyzer.load_source_documents()
        analyzer.load_conversation_logs()
        
        analyzer.analyze_document_coverage()
        analyzer.analyze_query_effectiveness()
        document_issues = analyzer.identify_document_issues()

        query_effectiveness = {
            'effective_queries': len([q for q in analyzer.user_queries if q['evaluation_score'] > 0 or q['is_passing']]),
            'ineffective_queries': len([q for q in analyzer.user_queries if not q['is_passing']]),
            'total_queries': len(analyzer.user_queries),
            'intent_patterns': dict(analyzer.intent_patterns),
            'top_keywords': dict(analyzer.query_keywords.most_common(10))
        }

        logger.info(f"文档分析完成: 覆盖度分析{len(analyzer.coverage_analysis)}条, 问题{len(document_issues)}个")

        return {
            'success': True,
            'coverage_analysis': analyzer.coverage_analysis,
            'query_effectiveness': query_effectiveness,
            'document_issues': document_issues
        }

    except Exception as e:
        logger.error(f"文档分析失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('docopt.analyze_failed', lang) + ': ' + str(e)
        )


@router.post("/optimize", response_model=OptimizeResponse, summary="优化文档")
async def optimize_documents(request: OptimizeRequest, req: Request):
    """Optimize documents of a specified knowledge base"""
    lang = get_lang_from_request(req)
    try:
        analyzer = DocumentAnalyzer(kb_id=request.kb_id)
        
        analyzer.load_source_documents()
        analyzer.load_conversation_logs()
        
        output_dir = request.output_dir or analyzer.output_dir
        analyzer.generate_optimized_document(output_dir=output_dir)

        result_docs = []
        for doc in analyzer.source_docs:
            name_without_ext = os.path.splitext(doc['file_name'])[0]
            ext = os.path.splitext(doc['file_name'])[1]
            
            result_docs.append({
                'file_name': doc['file_name'],
                'original_char_count': doc['char_count'],
                'suggestion_file': f"{name_without_ext}{_('docopt.suggestion_suffix', lang)}{ext}",
                'optimized_file': f"{name_without_ext}{_('docopt.optimized_suffix', lang)}{ext}"
            })

        logger.info(f"文档优化完成: 优化了{len(result_docs)}个文档")

        return {
            'success': True,
            'message': _('docopt.optimize_success', lang, len(result_docs)),
            'documents': result_docs
        }

    except Exception as e:
        logger.error(f"文档优化失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('docopt.optimize_failed', lang) + ': ' + str(e)
        )


@router.get("/issues", summary="获取文档问题列表")
async def get_document_issues(req: Request, kb_id: Optional[str] = None):
    """Get document issues list of a specified knowledge base"""
    lang = get_lang_from_request(req)
    try:
        analyzer = DocumentAnalyzer(kb_id=kb_id)
        analyzer.load_source_documents()
        issues = analyzer.identify_document_issues()

        return {
            'success': True,
            'data': issues,
            'total': len(issues)
        }

    except Exception as e:
        logger.error(f"获取文档问题失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('docopt.issues_failed', lang) + ': ' + str(e)
        )


@router.get("/coverage", summary="获取文档覆盖度")
async def get_document_coverage(req: Request, kb_id: Optional[str] = None):
    """Get document coverage analysis of a specified knowledge base"""
    lang = get_lang_from_request(req)
    try:
        analyzer = DocumentAnalyzer(kb_id=kb_id)
        analyzer.load_source_documents()
        analyzer.load_conversation_logs()
        analyzer.analyze_document_coverage()

        return {
            'success': True,
            'data': analyzer.coverage_analysis
        }

    except Exception as e:
        logger.error(f"获取文档覆盖度失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('docopt.coverage_failed', lang) + ': ' + str(e)
        )


@router.get("/download/optimized", summary="下载优化后文档")
async def download_optimized_document(req: Request, file_name: str):
    """Download the optimized document"""
    lang = get_lang_from_request(req)
    try:
        output_dir = Path(config.get('document_analyzer.output_dir', './storage/optimized_docs'))
        # file_name arrives from the query string. A legitimate caller never sends a path
        # separator, and without this guard the endpoint could read any file the process can
        # access (e.g. ?file_name=../../../requirements.txt).
        if not file_name or sanitize_filename(file_name) != file_name.strip():
            logger.warning(f"Rejected unsafe download path: {file_name!r}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_('docopt.invalid_file_name', lang, file_name)
            )
        try:
            file_path = safe_join(output_dir, file_name)
        except ValueError:
            logger.warning(f"Rejected unsafe download path: {file_name!r}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_('docopt.invalid_file_name', lang, file_name)
            )

        if not file_path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('docopt.file_not_found', lang)
            )

        return FileResponse(
            path=str(file_path),
            filename=file_name,
            media_type="text/plain; charset=utf-8"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"下载文档失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('docopt.download_failed', lang) + ': ' + str(e)
        )


@router.post("/full-analysis", summary="执行完整分析")
async def run_full_analysis(req: Request, kb_id: Optional[str] = None):
    """Run the complete document analysis and optimization workflow"""
    lang = get_lang_from_request(req)
    try:
        analyzer = DocumentAnalyzer(kb_id=kb_id)
        analyzer.run_full_analysis()

        return {
            'success': True,
            'message': _('docopt.full_analysis_done', lang),
            'document_count': len(analyzer.source_docs),
            'query_count': len(analyzer.user_queries)
        }

    except Exception as e:
        logger.error(f"完整分析失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('docopt.full_analysis_failed', lang) + ': ' + str(e)
        )
