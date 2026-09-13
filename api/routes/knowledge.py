"""
Knowledge base management API routes
Provides CRUD operations and document upload for knowledge bases
"""
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from config import config, scenario_config
from core.document_parser import DocumentParser
from core.vector_store import VectorStoreManager
from core.retriever import HybridRetriever
from service.async_tasks import task_manager
from service.i18n import _, get_lang_from_request
from service.logger import get_logger
from service.path_safety import safe_join, sanitize_filename

logger = get_logger('api.knowledge')

router = APIRouter(prefix="/api/knowledge", tags=["Knowledge Base Management"])

# Initialize core components
_document_parser = DocumentParser()
_vector_store = VectorStoreManager()
_retriever = HybridRetriever(_vector_store)

# Knowledge base document storage directory
_doc_dir = Path(config.get('knowledge_base.document_directory', './storage/documents'))
_doc_dir.mkdir(parents=True, exist_ok=True)

# Metadata file
_metadata_file = Path(config.get('knowledge_base.metadata_file', './storage/kb_metadata.json'))


class CreateKnowledgeBaseRequest(BaseModel):
    """Create knowledge base request"""
    name: str = Field(..., description="Knowledge base name", min_length=1, max_length=50)
    description: Optional[str] = Field("", description="Knowledge base description", max_length=200)
    scenario_id: Optional[str] = Field(None, description="Scenario ID for chunk strategy configuration")


class KnowledgeBaseInfo(BaseModel):
    """Knowledge base info"""
    kb_id: str
    name: str
    description: str
    document_count: int
    chunk_count: int
    created_at: str
    scenario_id: Optional[str] = None
    scenario_name: Optional[str] = None


class UploadResponse(BaseModel):
    """Upload response"""
    task_id: str
    file_name: str
    message: str


def _load_metadata() -> Dict:
    """Load knowledge base metadata"""
    if _metadata_file.exists():
        try:
            import json
            with open(_metadata_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Load metadata failed, error: {e}")
    return {}


def _save_metadata(metadata: Dict):
    """Save knowledge base metadata"""
    try:
        import json
        with open(_metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Save metadata failed, error: {e}")


def _process_document_task(kb_id: str, file_path: str, file_name: str, scenario_id: Optional[str] = None, progress_callback=None, file_id: Optional[str] = None, chunk_size: Optional[int] = None, chunk_overlap: Optional[int] = None):
    """
    Async task: process document and vectorize

    Args:
        kb_id: Knowledge base ID
        file_path: File path
        file_name: File name
        scenario_id: Scenario ID (for configuring chunk strategy)
        progress_callback: Progress callback
        file_id: File ID (used when updating)
        chunk_size: Custom chunk size (optional, overrides scenario config)
        chunk_overlap: Custom chunk overlap (optional, overrides scenario config)
    """
    try:
        if progress_callback:
            progress_callback(5, "Start parsing document")

        # 1. Parse document (use scenario-based chunk strategy; custom params have highest priority)
        chunks = _document_parser.parse_file(
            file_path,
            scenario_id=scenario_id,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            progress_callback=lambda p, m: progress_callback(5 + p * 0.5, m) if progress_callback else None
        )

        if progress_callback:
            progress_callback(60, f"Document parsed, total {len(chunks)} chunks, start vectorization")

        # 2. Vectorize
        added = _vector_store.add_documents(kb_id, chunks, progress_callback=lambda p, m: progress_callback(60 + p * 0.35, m) if progress_callback else None)

        # 3. Refresh BM25 index
        _retriever.invalidate_bm25_index(kb_id)

        if progress_callback:
            progress_callback(100, f"Document processed, successfully added {added} vectors")

        # Update metadata
        metadata = _load_metadata()
        if kb_id in metadata:
            stored_name = Path(file_path).name
            # file_id is uniformly generated and passed by the caller (upload/update route), stored filename is uniformly {file_id}_{original_name},
            # no longer relies on split('_')[0] inference, ensuring metadata file_id matches filename prefix.
            if not file_id:
                file_id = stored_name.split('_', 1)[0] if '_' in stored_name else str(uuid.uuid4())[:8]
            file_info = {
                'file_id': file_id,
                'original_name': file_name,
                'stored_name': stored_name,
                'chunk_count': added,
                'upload_date': datetime.now().isoformat()
            }

            if 'files' not in metadata[kb_id]:
                metadata[kb_id]['files'] = []

            # Update scenario: replace if same file_id exists; Upload scenario: append if new file_id doesn't exist
            replaced = False
            for i, f in enumerate(metadata[kb_id]['files']):
                if f['file_id'] == file_id:
                    metadata[kb_id]['files'][i] = file_info
                    replaced = True
                    break
            if not replaced:
                metadata[kb_id]['files'].append(file_info)

            metadata[kb_id]['document_count'] = len(metadata[kb_id]['files'])
            metadata[kb_id]['chunk_count'] = metadata[kb_id].get('chunk_count', 0) + added
            _save_metadata(metadata)

        return {
            'file_name': file_name,
            'chunk_count': len(chunks),
            'added_count': added,
            'kb_id': kb_id
        }

    except Exception as e:
        logger.error(f"Process document failed {file_name}: {e}", exc_info=True)
        raise


@router.post("/create", response_model=Dict, summary="Create knowledge base")
async def create_knowledge_base(request: CreateKnowledgeBaseRequest, http_request: Request):
    """Create a new knowledge base"""
    try:
        lang = get_lang_from_request(http_request)
        kb_id = str(uuid.uuid4())[:8]

        # Check quantity limit
        metadata = _load_metadata()
        max_kbs = config.get('knowledge_base.max_knowledge_bases', 10)
        if len(metadata) >= max_kbs:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_('kb.max_limit', lang, max_kbs)
            )

        # Validate scenario ID
        scenario_id = request.scenario_id
        if scenario_id:
            scenarios = scenario_config.list_scenarios()
            scenario_ids = [s['scenario_id'] for s in scenarios]
            if scenario_id not in scenario_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=_('kb.invalid_scenario', lang, scenario_id, ', '.join(scenario_ids))
                )

        # Create vector collection
        success = _vector_store.create_collection(kb_id, request.name)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=_('api.internal_error', lang, 'Create knowledge base failed')
            )

        # Create document directory
        kb_doc_dir = _doc_dir / kb_id
        kb_doc_dir.mkdir(parents=True, exist_ok=True)

        # Save metadata (including scenario ID)
        from datetime import datetime
        metadata[kb_id] = {
            'name': request.name,
            'description': request.description,
            'document_count': 0,
            'chunk_count': 0,
            'created_at': datetime.now().isoformat(),
            'scenario_id': scenario_id
        }
        _save_metadata(metadata)

        logger.info(f"Create knowledge base success: {kb_id} ({request.name}), scenario: {scenario_id or 'default'}")

        return JSONResponse(
            content={
                'success': True,
                'kb_id': kb_id,
                'name': request.name,
                'scenario_id': scenario_id,
                'message': _('kb.create_success', lang)
            },
            media_type="application/json; charset=utf-8"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create knowledge base failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.delete("/{kb_id}", summary="Delete knowledge base")
async def delete_knowledge_base(kb_id: str, request: Request):
    """Delete the specified knowledge base"""
    try:
        lang = get_lang_from_request(request)
        metadata = _load_metadata()
        if kb_id not in metadata:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('kb.not_found', lang)
            )

        # Delete vector collection
        _vector_store.delete_collection(kb_id)

        # Delete document directory
        kb_doc_dir = _doc_dir / kb_id
        if kb_doc_dir.exists():
            shutil.rmtree(kb_doc_dir)

        # Delete metadata
        del metadata[kb_id]
        _save_metadata(metadata)

        # Refresh BM25 cache
        _retriever.refresh_bm25_index(kb_id)

        logger.info(f"Delete knowledge base success: {kb_id}")

        return JSONResponse(
            content={
                'success': True,
                'message': _('kb.delete_success', lang)
            },
            media_type="application/json; charset=utf-8"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete knowledge base failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


def _get_scenario_name(scenario_id: Optional[str]) -> Optional[str]:
    """Get scenario name"""
    if not scenario_id:
        return None
    scenarios = scenario_config.list_scenarios()
    for s in scenarios:
        if s['scenario_id'] == scenario_id:
            return s['scenario_name']
    return None


@router.get("/list", summary="Get knowledge base list")
async def list_knowledge_bases(request: Request):
    """Get all knowledge base list"""
    try:
        lang = get_lang_from_request(request)
        metadata = _load_metadata()
        kb_list = []

        for kb_id, info in metadata.items():
            chunk_count = _vector_store.get_document_count(kb_id)
            scenario_id = info.get('scenario_id')
            kb_list.append({
                'kb_id': kb_id,
                'name': info.get('name', ''),
                'description': info.get('description', ''),
                'document_count': info.get('document_count', 0),
                'chunk_count': chunk_count,
                'created_at': info.get('created_at', ''),
                'scenario_id': scenario_id,
                'scenario_name': _get_scenario_name(scenario_id)
            })

        kb_list.sort(key=lambda x: x['created_at'], reverse=True)

        import json
        content = json.dumps({
            'success': True,
            'data': kb_list,
            'total': len(kb_list)
        }, ensure_ascii=False).encode('utf-8')

        from starlette.responses import Response
        response = Response(content=content, media_type="application/json")
        response.headers['Content-Type'] = "application/json; charset=utf-8"
        return response

    except Exception as e:
        logger.error(f"Get knowledge base list failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.get("/{kb_id}", response_model=Dict, summary="Get knowledge base details")
async def get_knowledge_base(kb_id: str, request: Request):
    """Get specified knowledge base details"""
    try:
        lang = get_lang_from_request(request)
        metadata = _load_metadata()
        if kb_id not in metadata:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('kb.not_found', lang)
            )

        info = metadata[kb_id]
        chunk_count = _vector_store.get_document_count(kb_id)
        scenario_id = info.get('scenario_id')

        return {
            'success': True,
            'data': {
                'kb_id': kb_id,
                'name': info.get('name', ''),
                'description': info.get('description', ''),
                'document_count': info.get('document_count', 0),
                'chunk_count': chunk_count,
                'created_at': info.get('created_at', ''),
                'scenario_id': scenario_id,
                'scenario_name': _get_scenario_name(scenario_id)
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get knowledge base details failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.post("/{kb_id}/upload", response_model=Dict, summary="Upload document to knowledge base")
async def upload_document(
    request: Request,
    kb_id: str,
    file: UploadFile = File(...),
    chunk_size: Optional[int] = Form(None, description="Custom chunk size (character count), not filled will use scenario/default config"),
    chunk_overlap: Optional[int] = Form(None, description="Custom chunk overlap (character count), not filled will use scenario/default config")
):
    """
    Upload document to knowledge base (async processing)
    Returns task ID, progress can be queried via task interface

    Supports custom chunk params chunk_size / chunk_overlap (higher priority than scenario config)
    """
    try:
        lang = get_lang_from_request(request)
        # Validate parameters
        if chunk_size is not None and chunk_size < 100:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_('kb.invalid_chunk_size', lang))
        if chunk_overlap is not None and chunk_overlap < 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_('kb.invalid_chunk_overlap', lang))
        if chunk_size is not None and chunk_overlap is not None and chunk_overlap >= chunk_size:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_('kb.invalid_chunk_params', lang))

        # Check if knowledge base exists
        metadata = _load_metadata()
        if kb_id not in metadata:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('kb.not_found', lang)
            )

        # Validate file format
        if not _document_parser.is_supported(file.filename or ''):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_('kb.unsupported_format', lang, ', '.join(config.get('document_parser.supported_formats', [])))
            )

        # Save file
        kb_doc_dir = _doc_dir / kb_id
        kb_doc_dir.mkdir(parents=True, exist_ok=True)

        # The multipart filename is client controlled (browsers may even send a full path),
        # so strip any directory component before it is used as a path segment.
        file_name = sanitize_filename(file.filename or 'unknown')
        # Force generate file_id, stored filename is uniformly {file_id}_{original_name}, ensuring consistency with metadata
        file_id = str(uuid.uuid4())[:8]
        safe_name = f"{file_id}_{file_name}"
        file_path = safe_join(kb_doc_dir, safe_name)

        content = await file.read()

        # Check file size
        max_size = config.get('document_parser.max_file_size', 50) * 1024 * 1024
        if len(content) > max_size:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_('kb.file_too_large', lang, max_size / 1024 / 1024)
            )

        # MIME type dual validation (Magic Number): prevent disguised attacks like virus.exe renamed to virus.pdf
        mime_ok, mime_err = _document_parser.validate_mime_type(content, file_name)
        if not mime_ok:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=mime_err
            )

        with open(file_path, 'wb') as f:
            f.write(content)

        # Get knowledge base scenario ID
        scenario_id = metadata[kb_id].get('scenario_id')

        # Create async task (pass scenario ID, file_id and custom chunk params)
        task_id = task_manager.create_task(
            f"Process document task: {file_name}",
            _process_document_task,
            kb_id=kb_id,
            file_path=str(file_path),
            file_name=file_name,
            scenario_id=scenario_id,
            file_id=file_id,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )

        # Start task
        task_manager.start_task(task_id)

        logger.info(f"Upload document success: {file_name} -> Knowledge base {kb_id}, Task ID: {task_id}, file_id: {file_id}, chunk_size: {chunk_size or 'default'}, chunk_overlap: {chunk_overlap or 'default'}")

        return {
            'success': True,
            'task_id': task_id,
            'file_id': file_id,
            'file_name': file_name,
            'chunk_size': chunk_size,
            'chunk_overlap': chunk_overlap,
            'message': _('kb.upload_success', lang)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload document failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.get("/{kb_id}/status", response_model=Dict, summary="Get knowledge base document processing status (recent tasks)")
async def get_kb_status(kb_id: str, request: Request):
    """Get knowledge base document processing status (recent tasks)"""
    try:
        lang = get_lang_from_request(request)
        # Get task list for THIS knowledge base only (each task is tagged with its kb_id at creation time)
        tasks = task_manager.list_tasks(limit=20, kb_id=kb_id)

        recent_tasks = []
        for task in tasks:
            recent_tasks.append({
                'task_id': task['task_id'],
                'name': task['name'],
                'status': task['status'],
                'progress': task['progress'],
                'message': task['message'],
                'created_at': task['created_at']
            })

        metadata = _load_metadata()
        kb_info = metadata.get(kb_id, {})

        return {
            'success': True,
            'data': {
                'kb_id': kb_id,
                'name': kb_info.get('name', ''),
                'document_count': kb_info.get('document_count', 0),
                'chunk_count': _vector_store.get_document_count(kb_id),
                'recent_tasks': recent_tasks[:10]
            }
        }

    except Exception as e:
        logger.error(f"Get knowledge base status failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.get("/{kb_id}/files", summary="Get all files list in the specified knowledge base")
async def list_kb_files(kb_id: str, request: Request):
    """Get all files list in the specified knowledge base"""
    try:
        lang = get_lang_from_request(request)
        metadata = _load_metadata()
        if kb_id not in metadata:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('kb.not_found', lang)
            )

        kb_info = metadata[kb_id]
        files = kb_info.get('files', [])

        if not files and kb_info.get('document_count', 0) > 0:
            files = _scan_kb_files(kb_id, metadata)
            if files:
                kb_info['files'] = files
                _save_metadata(metadata)

        return {
            'success': True,
            'data': files,
            'total': len(files)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get all files list failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


def _scan_kb_files(kb_id: str, metadata: Dict) -> List[Dict]:
    """Scan knowledge base files from storage directory"""
    kb_doc_dir = _doc_dir / kb_id
    if not kb_doc_dir.exists():
        return []

    files = []
    for file_path in kb_doc_dir.glob('*'):
        if file_path.is_file():
            stored_name = file_path.name
            original_name = stored_name
            file_id = str(uuid.uuid4())[:8]

            if '_' in stored_name:
                parts = stored_name.split('_', 1)
                if len(parts) == 2:
                    file_id = parts[0]
                    original_name = parts[1]

            files.append({
                'file_id': file_id,
                'original_name': original_name,
                'stored_name': stored_name,
                'chunk_count': 0,
                'upload_date': datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
            })

    return files


@router.get("/{kb_id}/files/{file_id}", summary="Get detailed info of the specified file")
async def get_kb_file(kb_id: str, file_id: str, request: Request):
    """Get detailed info of the specified file"""
    try:
        lang = get_lang_from_request(request)
        metadata = _load_metadata()
        if kb_id not in metadata:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('kb.not_found', lang)
            )

        kb_info = metadata[kb_id]
        files = kb_info.get('files', [])

        for file in files:
            if file.get('file_id') == file_id:
                return {
                    'success': True,
                    'data': file
                }

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_('kb.file_not_found', lang)
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get detailed info of the specified file failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.delete("/{kb_id}/files/{file_id}", summary="Delete the specified file in the knowledge base (also delete vector data)")
async def delete_kb_file(kb_id: str, file_id: str, request: Request):
    """Delete the specified file in the knowledge base (also delete vector data)"""
    try:
        lang = get_lang_from_request(request)
        metadata = _load_metadata()
        if kb_id not in metadata:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('kb.not_found', lang)
            )

        kb_info = metadata[kb_id]
        files = kb_info.get('files', [])

        file_info = None
        for i, f in enumerate(files):
            if f.get('file_id') == file_id:
                file_info = f
                break

        if not file_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('kb.file_not_found', lang)
            )

        stored_name = file_info.get('stored_name', '')
        original_name = file_info.get('original_name', '')

        kb_doc_dir = _doc_dir / kb_id

        if stored_name:
            # stored_name lives in metadata, which uploads from older versions may have
            # poisoned with a traversal sequence: never trust it as a path segment.
            file_path = safe_join(kb_doc_dir, stored_name)
            if file_path.exists():
                os.remove(file_path)
                logger.info(f"Delete file from storage: {file_path}")

        _vector_store.delete_documents_by_file(kb_id, original_name)

        files = [f for f in files if f.get('file_id') != file_id]
        kb_info['files'] = files
        kb_info['document_count'] = len(files)
        _save_metadata(metadata)

        _retriever.invalidate_bm25_index(kb_id)

        logger.info(f"Delete file from knowledge base success: {kb_id}/{file_id} ({original_name})")

        return {
            'success': True,
            'message': _('kb.delete_file_success', lang, original_name),
            'file_id': file_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete file from knowledge base failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.put("/{kb_id}/files/{file_id}", summary="Update the specified file in the knowledge base (delete old file then upload new file), supports custom chunk params")
async def update_kb_file(
    request: Request,
    kb_id: str,
    file_id: str,
    file: UploadFile = File(...),
    chunk_size: Optional[int] = Form(None, description="Custom chunk size (character count)"),
    chunk_overlap: Optional[int] = Form(None, description="Custom chunk overlap (character count)")
):
    """Update the specified file in the knowledge base (delete old file then upload new file), supports custom chunk params"""
    try:
        lang = get_lang_from_request(request)
        # Validate parameters
        if chunk_size is not None and chunk_size < 100:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_('kb.invalid_chunk_size', lang))
        if chunk_overlap is not None and chunk_overlap < 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_('kb.invalid_chunk_overlap', lang))
        if chunk_size is not None and chunk_overlap is not None and chunk_overlap >= chunk_size:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_('kb.invalid_chunk_params', lang))

        metadata = _load_metadata()
        if kb_id not in metadata:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('kb.not_found', lang)
            )

        kb_info = metadata[kb_id]
        files = kb_info.get('files', [])

        file_info = None
        for i, f in enumerate(files):
            if f.get('file_id') == file_id:
                file_info = f
                break

        if not file_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('kb.file_not_found', lang)
            )

        stored_name = file_info.get('stored_name', '')
        original_name = file_info.get('original_name', '')

        if not _document_parser.is_supported(file.filename or ''):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_('kb.unsupported_format', lang, ', '.join(config.get('document_parser.supported_formats', [])))
            )

        kb_doc_dir = _doc_dir / kb_id
        kb_doc_dir.mkdir(parents=True, exist_ok=True)

        # See the upload handler: the client supplied filename must not contain directories.
        new_file_name = sanitize_filename(file.filename or 'unknown')
        safe_name = f"{file_id}_{new_file_name}"
        file_path = safe_join(kb_doc_dir, safe_name)

        content = await file.read()
        max_size = config.get('document_parser.max_file_size', 50) * 1024 * 1024
        if len(content) > max_size:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_('kb.file_too_large', lang, max_size / 1024 / 1024)
            )

        # MIME type dual validation (Magic Number): prevent disguised attacks like virus.exe renamed to virus.pdf
        mime_ok, mime_err = _document_parser.validate_mime_type(content, new_file_name)
        if not mime_ok:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=mime_err
            )

        with open(file_path, 'wb') as f:
            f.write(content)

        if stored_name and stored_name != safe_name:
            old_file_path = safe_join(kb_doc_dir, stored_name)
            if old_file_path.exists():
                os.remove(old_file_path)

        _vector_store.delete_documents_by_file(kb_id, original_name)

        scenario_id = metadata[kb_id].get('scenario_id')

        task_id = task_manager.create_task(
            f"更新文档: {new_file_name}",
            _process_document_task,
            kb_id=kb_id,
            file_path=str(file_path),
            file_name=new_file_name,
            scenario_id=scenario_id,
            file_id=file_id,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )

        task_manager.start_task(task_id)

        logger.info(f"Update document success: {new_file_name} -> Knowledge base {kb_id}, Task ID: {task_id}")

        return {
            'success': True,
            'task_id': task_id,
            'file_id': file_id,
            'file_name': new_file_name,
            'message': _('kb.doc_update_success', lang)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update document failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.get("/synonyms", summary="Get all synonym/abbreviation configs")
async def get_synonyms(request: Request):
    """Get all synonym/abbreviation configs"""
    try:
        lang = get_lang_from_request(request)
        from core.query_rewriter import QueryRewriter
        rewriter = QueryRewriter()
        synonyms = rewriter.list_synonyms()
        return {
            'success': True,
            'data': synonyms,
            'total': len(synonyms)
        }
    except Exception as e:
        logger.error(f"Get synonym configs failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.post("/synonyms", summary="Add new synonym config")
async def add_synonym(body: Dict, request: Request):
    """Add new synonym config"""
    try:
        lang = get_lang_from_request(request)
        from core.query_rewriter import QueryRewriter
        rewriter = QueryRewriter()

        term = body.get('term')
        synonyms = body.get('synonyms', [])

        if not term:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_('kb.synonym_empty', lang)
            )

        if not isinstance(synonyms, list):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_('kb.synonym_array', lang)
            )

        rewriter.add_synonym(term, synonyms)
        rewriter.save_synonym_dict()

        # Hot reload: reload the long-lived QueryRewriter instance in retriever
        try:
            _retriever._query_rewriter.reload_synonym_dict()
        except Exception:
            pass

        logger.info(f"添加同义词: {term} -> {synonyms}")

        return {
            'success': True,
            'term': term,
            'synonyms': synonyms,
            'message': _('kb.synonym_add_success', lang)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"添加同义词失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.delete("/synonyms/{term}", summary="删除同义词")
async def delete_synonym(term: str, request: Request, synonym: Optional[str] = None):
    """Delete synonym config"""
    try:
        lang = get_lang_from_request(request)
        from core.query_rewriter import QueryRewriter
        rewriter = QueryRewriter()

        rewriter.remove_synonym(term, synonym)
        rewriter.save_synonym_dict()

        # Hot reload: reload the long-lived QueryRewriter instance in retriever
        try:
            _retriever._query_rewriter.reload_synonym_dict()
        except Exception:
            pass

        if synonym:
            logger.info(f"删除同义词: {term} -> {synonym}")
            return {
                'success': True,
                'term': term,
                'synonym': synonym,
                'message': _('kb.synonym_delete_success', lang, synonym)
            }
        else:
            logger.info(f"删除同义词组: {term}")
            return {
                'success': True,
                'term': term,
                'message': _('kb.synonym_group_delete', lang, term)
            }

    except Exception as e:
        logger.error(f"删除同义词失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.get("/typos", summary="获取错别字规则")
async def get_typos(request: Request):
    """Get all typo rules"""
    try:
        lang = get_lang_from_request(request)
        from core.typo_checker import typo_checker
        typos = typo_checker.list_typos()
        return {
            'success': True,
            'data': typos,
            'total': len(typos)
        }
    except Exception as e:
        logger.error(f"获取错别字规则失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.post("/typos", summary="添加错别字规则")
async def add_typo(body: Dict, request: Request):
    """Add new typo rule"""
    try:
        lang = get_lang_from_request(request)
        from core.typo_checker import typo_checker

        typo = body.get('typo')
        correction = body.get('correction')

        if not typo or not correction:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_('kb.typo_empty', lang)
            )

        typo_checker.add_typo(typo, correction)

        logger.info(f"添加错别字规则: {typo} -> {correction}")

        return {
            'success': True,
            'typo': typo,
            'correction': correction,
            'message': _('kb.typo_add_success', lang)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"添加错别字规则失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.delete("/typos/{typo}", summary="删除错别字规则")
async def delete_typo(typo: str, request: Request):
    """Delete typo rule"""
    try:
        lang = get_lang_from_request(request)
        from core.typo_checker import typo_checker

        typo_checker.remove_typo(typo)

        logger.info(f"删除错别字规则: {typo}")

        return {
            'success': True,
            'typo': typo,
            'message': _('kb.typo_delete_success', lang, typo)
        }

    except Exception as e:
        logger.error(f"删除错别字规则失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.post("/synonyms/reload", summary="热更新同义词词典")
async def reload_synonyms(request: Request):
    """Reload synonym dictionary from config/synonym_dict.yaml, no service restart needed"""
    try:
        lang = get_lang_from_request(request)
        # Reload the long-lived instance in retriever
        count = _retriever._query_rewriter.reload_synonym_dict()
        logger.info(f"同义词词典热更新完成，共 {count} 个词条")
        return {
            'success': True,
            'count': count,
            'message': _('kb.synonym_add_success', lang)
        }
    except Exception as e:
        logger.error(f"热更新同义词词典失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.post("/typos/reload", summary="Reload Typo Dictionary")
async def reload_typos(request: Request):
    """Reload typo dictionary from config/typo_dict.yaml, no service restart needed"""
    try:
        lang = get_lang_from_request(request)
        from core.typo_checker import typo_checker
        count = typo_checker.reload_typo_dict()
        logger.info(f"Typo dictionary reloaded, total {count} rules")
        return {
            'success': True,
            'count': count,
            'message': _('kb.typo_add_success', lang)
        }
    except Exception as e:
        logger.error(f"Reload typo dictionary failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


# Export core components for use by other routes
def get_vector_store():
    return _vector_store

def get_retriever():
    return _retriever

def get_document_parser():
    return _document_parser

def get_kb_metadata(kb_id: str) -> Optional[Dict]:
    """Get knowledge base metadata"""
    metadata = _load_metadata()
    return metadata.get(kb_id)


@router.get("/{kb_id}/raw/{file_id}", summary="Get Raw Document Content")
async def get_raw_document(kb_id: str, file_id: str, request: Request):
    """Get raw content of the specified file in the knowledge base"""
    try:
        lang = get_lang_from_request(request)
        metadata = _load_metadata()
        if kb_id not in metadata:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('kb.not_found', lang)
            )

        kb_info = metadata[kb_id]
        files = kb_info.get('files', [])

        file_info = None
        for f in files:
            if f.get('file_id') == file_id:
                file_info = f
                break

        if not file_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('kb.file_not_found', lang)
            )

        stored_name = file_info.get('stored_name', '')
        kb_doc_dir = _doc_dir / kb_id
        file_path = safe_join(kb_doc_dir, stored_name)

        if not file_path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('kb.file_not_found', lang)
            )

        with open(file_path, 'rb') as f:
            raw_content = f.read()

        # Determine file type, decide how to decode
        ext = Path(file_info.get('original_name', '')).suffix.lower()
        binary_exts = ['.pdf', '.docx', '.xlsx', '.pptx', '.doc', '.xls', '.ppt', '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.zip', '.rar']

        if ext in binary_exts:
            # Binary format, do not attempt text decoding
            content = f"[此文件为二进制格式 ({ext})，不支持直接以文本方式预览，请使用对应的文件查看器打开]"
            encoding = 'binary'
        else:
            # Text format, attempt encoding detection
            encoding = None
            try:
                import chardet
                result = chardet.detect(raw_content)
                encoding = result.get('encoding')
            except ImportError:
                logger.debug("chardet not installed, using fallback encoding detection")

            content = None
            # Candidate encoding order: chardet detection result first, then utf-8, gbk, finally latin-1 (never fails)
            candidates = [encoding] if encoding else []
            for enc in ['utf-8', 'gbk', 'latin-1']:
                if enc not in candidates:
                    candidates.append(enc)

            for enc in candidates:
                if not enc:
                    continue
                try:
                    content = raw_content.decode(enc)
                    encoding = enc
                    break
                except (UnicodeDecodeError, LookupError):
                    continue

            if content is None:
                content = raw_content.decode('utf-8', errors='replace')
                encoding = encoding or 'utf-8'

        return {
            'success': True,
            'data': {
                'file_id': file_id,
                'original_name': file_info.get('original_name', ''),
                'stored_name': stored_name,
                'content': content,
                'encoding': encoding,
                'size': len(raw_content)
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取原始文档失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.get("/{kb_id}/chunks", summary="获取知识库分块列表")
async def get_kb_chunks(kb_id: str, request: Request, limit: int = 20, offset: int = 0):
    """Get all document chunk list in the knowledge base"""
    try:
        lang = get_lang_from_request(request)
        metadata = _load_metadata()
        if kb_id not in metadata:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('kb.not_found', lang)
            )

        result = _vector_store.get_all_documents(kb_id, limit=limit, offset=offset)

        # Compatible with two return formats: dict (normal) or list (fallback when exception/empty)
        if isinstance(result, dict):
            return {
                'success': True,
                'data': result.get('documents', []),
                'total': result.get('total', 0),
                'limit': result.get('limit', limit),
                'offset': result.get('offset', offset)
            }
        elif isinstance(result, list):
            # list case has no total info, use list length as approximate total
            return {
                'success': True,
                'data': result,
                'total': len(result),
                'limit': limit,
                'offset': offset
            }
        else:
            return {
                'success': True,
                'data': [],
                'total': 0,
                'limit': limit,
                'offset': offset
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取分块列表失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.get("/{kb_id}/chunks/{chunk_id}", summary="获取分块详情及向量")
async def get_chunk_detail(kb_id: str, chunk_id: str, request: Request):
    """Get detailed info of a single chunk, including vector data"""
    try:
        lang = get_lang_from_request(request)
        metadata = _load_metadata()
        if kb_id not in metadata:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('kb.not_found', lang)
            )

        result = _vector_store.get_document_by_id(kb_id, chunk_id)

        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_('kb.chunk_not_found', lang)
            )

        embedding_info = {}
        embedding = result.get('embedding')
        if embedding is not None:
            import numpy as np
            if isinstance(embedding, np.ndarray):
                embedding = embedding.tolist()
            embedding_info = {
                'dimension': len(embedding),
                'sample_values': embedding[:5],
                'min_value': float(min(embedding)),
                'max_value': float(max(embedding)),
                'avg_value': float(sum(embedding) / len(embedding))
            }

        import numpy as np
        if isinstance(result.get('embedding'), np.ndarray):
            result['embedding'] = result['embedding'].tolist()

        return {
            'success': True,
            'data': {
                'id': result['id'],
                'text': result['text'],
                'metadata': result['metadata'],
                'embedding': result['embedding'],
                'embedding_info': embedding_info
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取分块详情失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )
