"""
Knowledge base management API routes
Provides CRUD operations and document upload for knowledge bases
"""
import os
import shutil
import tempfile
import threading
import uuid
from contextlib import contextmanager
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

# How many leading bytes of an upload to keep for the magic-number check.
_MIME_SNIFF_BYTES = 64 * 1024


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


# Serialises every read-modify-write of the metadata file. It is read and written by the request
# handlers on the event loop and by the ingestion worker threads, so without this a create or
# delete racing an upload silently drops one of the two updates. Reentrant because the
# transaction below holds it while the nested _load_metadata() / _save_metadata() re-acquire it.
_metadata_lock = threading.RLock()


def _load_metadata() -> Dict:
    """Load knowledge base metadata.

    Callers that mutate the result and save it back must hold `_metadata_lock` across the whole
    read-modify-write — use the `_metadata_transaction()` context manager. A bare load/save pair
    is a lost-update race: another request can write between the two calls.
    """
    with _metadata_lock:
        return _read_metadata()


def _read_metadata() -> Dict:
    """Read the metadata file. The caller must already hold `_metadata_lock`."""
    if _metadata_file.exists():
        try:
            import json
            with open(_metadata_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Load metadata failed, error: {e}")
    return {}


def _save_metadata(metadata: Dict):
    """Save knowledge base metadata atomically. See `_metadata_transaction()`.

    Raises on failure. Swallowing the error would let a full disk or a permissions problem
    report success while nothing was persisted, and the caller's next read would show the old
    data with no indication that the write was lost.
    """
    with _metadata_lock:
        _write_metadata(metadata)


def _write_metadata(metadata: Dict):
    """Write the metadata file atomically. The caller must already hold `_metadata_lock`.

    The document is written to a temporary file in the same directory and then moved into place,
    so a reader never sees a half-written file. The previous `open(..., 'w')` truncated the real
    file first: an interrupted or concurrent write left invalid JSON behind, `_load_metadata()`
    then returned `{}`, and every knowledge base disappeared at once.
    """
    import json
    _metadata_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(_metadata_file.parent), prefix='.kb_metadata-', suffix='.tmp'
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, _metadata_file)
    except BaseException as e:
        # The dump or the move failed: do not leave the temporary file behind.
        logger.error(f"Save metadata failed, error: {e}")
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


async def _stream_upload_to(file: UploadFile, dest: Path, max_size: int, lang: Optional[str] = None) -> bytes:
    """Write an upload to `dest` in chunks and return its leading bytes.

    `await file.read()` buffers the whole request body in memory *before* any size check can run,
    so a single large upload could exhaust RAM. Reading in chunks lets the transfer abort as soon
    as the limit is crossed, and the partial file is removed on the way out.

    The returned head is what the magic-number check needs; libmagic only inspects the leading
    bytes, so there is no reason to keep the whole document around for it.
    """
    head = b''
    received = 0
    try:
        with open(dest, 'wb') as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > max_size:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=_('kb.file_too_large', lang, max_size / 1024 / 1024)
                    )
                if len(head) < _MIME_SNIFF_BYTES:
                    head += chunk[:_MIME_SNIFF_BYTES - len(head)]
                f.write(chunk)
    except BaseException:
        # Never leave a partial or oversized upload behind for the parser to pick up.
        try:
            dest.unlink()
        except OSError:
            pass
        raise
    return head


@contextmanager
def _metadata_transaction():
    """Hold `_metadata_lock` across a read-modify-write of the metadata file.

    Use it around any handler that loads the metadata, changes it and saves it back:

        with _metadata_transaction():
            metadata = _load_metadata()
            ...
            _save_metadata(metadata)

    The lock is reentrant, so the two nested helpers are free to take it again. Nothing inside
    the block may `await`: it would stall the event loop for every other request.
    """
    with _metadata_lock:
        yield


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

        # Update metadata. This runs on an ingestion worker thread while requests may be
        # creating or deleting knowledge bases, so the read-modify-write must be serialised.
        with _metadata_transaction():
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

        max_kbs = config.get('knowledge_base.max_knowledge_bases', 10)

        # Cheap pre-check so an obviously over-limit request fails without building anything.
        if len(_load_metadata()) >= max_kbs:
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

        # Save metadata (including scenario ID). The count is re-read and re-checked here rather
        # than reusing the pre-check result, so two concurrent creates cannot both pass the limit.
        # Only the map update is inside the lock — create_collection above is slow and must not
        # block every other knowledge-base request.
        try:
            with _metadata_transaction():
                metadata = _load_metadata()
                if len(metadata) >= max_kbs:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=_('kb.max_limit', lang, max_kbs)
                    )
                metadata[kb_id] = {
                    'name': request.name,
                    'description': request.description,
                    'document_count': 0,
                    'chunk_count': 0,
                    'created_at': datetime.now().isoformat(),
                    'scenario_id': scenario_id
                }
                _save_metadata(metadata)
        except Exception:
            # The registry entry is the source of truth: without it the collection and the
            # document directory we just built are unreachable, so roll them back.
            _vector_store.delete_collection(kb_id)
            shutil.rmtree(kb_doc_dir, ignore_errors=True)
            raise

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
        with _metadata_transaction():
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


# NOTE: GET /{kb_id} is registered at the very END of this module on purpose. Starlette
# resolves routes in registration order and takes the first full match, so a catch-all path
# parameter registered early swallows every later static sibling ("/synonyms", "/typos" would
# bind kb_id="synonyms" and answer 404). Any new static GET route must be added above it.


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

        # Stream to disk with the size limit enforced as we go; see _stream_upload_to().
        max_size = config.get('document_parser.max_file_size', 50) * 1024 * 1024
        head = await _stream_upload_to(file, file_path, max_size, lang)

        # MIME type dual validation (Magic Number): prevent disguised attacks like virus.exe renamed to virus.pdf
        mime_ok, mime_err = _document_parser.validate_mime_type(head, file_name)
        if not mime_ok:
            file_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=mime_err
            )

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
        with _metadata_transaction():
            metadata = _load_metadata()
            if kb_id not in metadata:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=_('kb.not_found', lang)
                )

            kb_info = metadata[kb_id]
            files = kb_info.get('files', [])

            # Lazy backfill for knowledge bases created before the file list was tracked.
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
        with _metadata_transaction():
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

        # Stream to disk with the size limit enforced as we go; see _stream_upload_to().
        max_size = config.get('document_parser.max_file_size', 50) * 1024 * 1024
        head = await _stream_upload_to(file, file_path, max_size, lang)

        # MIME type dual validation (Magic Number): prevent disguised attacks like virus.exe renamed to virus.pdf
        mime_ok, mime_err = _document_parser.validate_mime_type(head, new_file_name)
        if not mime_ok:
            file_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=mime_err
            )

        if stored_name and stored_name != safe_name:
            old_file_path = safe_join(kb_doc_dir, stored_name)
            if old_file_path.exists():
                os.remove(old_file_path)

        _vector_store.delete_documents_by_file(kb_id, original_name)

        scenario_id = metadata[kb_id].get('scenario_id')

        task_id = task_manager.create_task(
            f"Update document task: {new_file_name}",
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

        logger.info(f"Added synonym config: {term} -> {synonyms}")

        return {
            'success': True,
            'term': term,
            'synonyms': synonyms,
            'message': _('kb.synonym_add_success', lang)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Add synonym config failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.delete("/synonyms/{term}", summary="Delete synonym config")
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
            logger.info(f"Deleted synonym config: {term} -> {synonym}")
            return {
                'success': True,
                'term': term,
                'synonym': synonym,
                'message': _('kb.synonym_delete_success', lang, synonym)
            }
        else:
            logger.info(f"Deleted synonym group: {term}")
            return {
                'success': True,
                'term': term,
                'message': _('kb.synonym_group_delete', lang, term)
            }

    except Exception as e:
        logger.error(f"Failed to delete synonym config: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.get("/typos", summary="Get all typo rules")
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
        logger.error(f"Failed to get typo rules: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.post("/typos", summary="Add new typo rule")
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

        logger.info(f"Added typo rule: {typo} -> {correction}")

        return {
            'success': True,
            'typo': typo,
            'correction': correction,
            'message': _('kb.typo_add_success', lang)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to add typo rule: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.delete("/typos/{typo}", summary="Delete typo rule")
async def delete_typo(typo: str, request: Request):
    """Delete typo rule"""
    try:
        lang = get_lang_from_request(request)
        from core.typo_checker import typo_checker

        typo_checker.remove_typo(typo)

        logger.info(f"Deleted typo rule: {typo}")

        return {
            'success': True,
            'typo': typo,
            'message': _('kb.typo_delete_success', lang, typo)
        }

    except Exception as e:
        logger.error(f"Failed to delete typo rule: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.post("/synonyms/reload", summary="Reload synonym Dictionary")
async def reload_synonyms(request: Request):
    """Reload synonym dictionary from config/synonym_dict.yaml, no service restart needed"""
    try:
        lang = get_lang_from_request(request)
        # Reload the long-lived instance in retriever
        count = _retriever._query_rewriter.reload_synonym_dict()
        logger.info(f"Synonym dictionary reloaded, total {count} entries")
        return {
            'success': True,
            'count': count,
            'message': _('kb.synonym_add_success', lang)
        }
    except Exception as e:
        logger.error(f"Reload synonym dictionary failed: {e}", exc_info=True)
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
            content = _('kb.binary_preview', lang, ext)
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
        logger.error(f"Failed to get raw document content: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.get("/{kb_id}/chunks", summary="Get Knowledge Base Document List of Chunks")
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
        logger.error(f"Failed to get document chunk list: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


@router.get("/{kb_id}/chunks/{chunk_id}", summary="Get Knowledge Base Document Chunk Detail")
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
        logger.error(f"Failed to get document chunk detail: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_('api.internal_error', lang, str(e))
        )


# Catch-all: keep this LAST in the module (see the note above the route table).
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
