"""
Document Parser Module
Supports multi-format document parsing (PDF/Word/Excel/PPT/TXT), text cleaning and chunking
Supports scenario-based chunking parameter config
"""
import os
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

from config import config, scenario_config
from service.logger import get_logger
from service.i18n import _

logger = get_logger('document_parser')


class DocumentChunk:
    """Document chunk"""

    def __init__(self, text: str, metadata: Optional[Dict] = None):
        self.text = text
        self.metadata = metadata or {}
        self.chunk_id: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            'text': self.text,
            'metadata': self.metadata
        }


class DocumentParser:
    """Document parser"""

    def __init__(self):
        self.supported_formats = config.get('document_parser.supported_formats', [])
        self.max_file_size = config.get('document_parser.max_file_size', 50) * 1024 * 1024
        self.chunk_size = config.get('document_parser.chunk_size', 512)
        self.chunk_overlap = config.get('document_parser.chunk_overlap', 50)

    def _get_chunking_params(self, scenario_id: Optional[str] = None,
                             chunk_size: Optional[int] = None,
                             chunk_overlap: Optional[int] = None) -> Dict:
        """
        Get scenario-based chunking parameters

        Args:
            scenario_id: Scenario ID
            chunk_size: Custom chunk size (highest priority, overrides scenario config)
            chunk_overlap: Custom chunk overlap (highest priority, overrides scenario config)

        Returns:
            Parameter dictionary, containing chunk_size and chunk_overlap
        """
        if scenario_id is None:
            params = {
                'chunk_size': self.chunk_size,
                'chunk_overlap': self.chunk_overlap,
                'scenario_id': scenario_id,
            }
        else:
            effective_config = scenario_config.get_effective_config(scenario_id)
            params = {
                'chunk_size': effective_config.get('document_parser', {}).get('chunk_size', self.chunk_size),
                'chunk_overlap': effective_config.get('document_parser', {}).get('chunk_overlap', self.chunk_overlap),
                'scenario_id': scenario_id,
            }

        # Custom parameters have highest priority
        if chunk_size is not None:
            params['chunk_size'] = chunk_size
        if chunk_overlap is not None:
            params['chunk_overlap'] = chunk_overlap
        return params

    def is_supported(self, file_path: str) -> bool:
        """
        Check if file format is supported

        Args:
            file_path: File path

        Returns:
            Whether supported
        """
        ext = Path(file_path).suffix.lower()
        return ext in self.supported_formats

    # Extension -> expected MIME type set (for Magic Number dual validation)
    # OOXML (docx/xlsx/pptx) is essentially a ZIP archive, python-magic usually identifies as application/zip;
    # legacy OLE2 (doc/xls) may be identified as application/cdfv2 or application/x-ole-storage.
    _EXT_MIME_MAP = {
        '.pdf': {'application/pdf'},
        '.docx': {
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/zip',
        },
        '.doc': {'application/msword', 'application/cdfv2', 'application/x-ole-storage'},
        '.xlsx': {
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/zip',
        },
        '.xls': {'application/vnd.ms-excel', 'application/cdfv2', 'application/x-ole-storage'},
        '.pptx': {
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'application/zip',
        },
        '.txt': {'text/plain', 'application/x-empty'},
    }

    def validate_mime_type(self, content: bytes, filename: str) -> tuple[bool, str]:
        """
        Validate file real type via file header (Magic Number), dual validation with extension.
        Prevents attackers from uploading malicious files and renaming them to bypass extension detection
        (e.g. virus.exe renamed to virus.pdf).

        Args:
            content: File binary content
            filename: File name (used to get extension)

        Returns:
            (whether validation passed, error message)
        """
        ext = Path(filename).suffix.lower()
        expected_mimes = self._EXT_MIME_MAP.get(ext)
        if not expected_mimes:
            # Extension not in mapping table (theoretically already intercepted by is_supported), pass through to subsequent logic
            return True, ""

        try:
            import magic
        except ImportError:
            # When python-magic is not installed, degrade to extension-only validation, log warning for ops awareness
            logger.warning("python-magic not installed, skipping MIME type validation, only using suffix validation.")
            return True, ""

        try:
            detected_mime = magic.from_buffer(content, mime=True).lower()
        except Exception as e:
            # libmagic dynamic library missing or similar runtime exception, degrade to pass-through to avoid blocking normal uploads
            logger.warning(f"MIME type detection failed ({e}), skipping Magic Number validation.")
            return True, ""

        expected_lower = {m.lower() for m in expected_mimes}
        if detected_mime in expected_lower:
            return True, ""

        logger.warning(
            f"File type mismatch: suffix {ext} expected {expected_lower} but detected {detected_mime}, likely fake file: {filename}"
        )
        return False, _('kb.mime_mismatch', None, ext, detected_mime)

    def validate_file(self, file_path: str) -> tuple[bool, str]:
        """
        Validate file

        Args:
            file_path: File path

        Returns:
            (whether valid, error message)
        """
        if not os.path.exists(file_path):
            return False, _('parser.file_not_found')

        if not self.is_supported(file_path):
            return False, _('parser.unsupported_format', None, Path(file_path).suffix)

        file_size = os.path.getsize(file_path)
        if file_size > self.max_file_size:
            return False, _('parser.file_too_large', None, f"{file_size / 1024 / 1024:.1f}", f"{self.max_file_size / 1024 / 1024}")

        return True, ""

    def parse_file(self, file_path: str, progress_callback: Optional[Callable] = None,
                   scenario_id: Optional[str] = None,
                   chunk_size: Optional[int] = None,
                   chunk_overlap: Optional[int] = None) -> List[DocumentChunk]:
        """
        Parse file and return chunks

        Args:
            file_path: File path
            progress_callback: Progress callback function
            scenario_id: Scenario ID (optional, used to load scenario-specific chunking parameters)
            chunk_size: Custom chunk size (optional, highest priority, overrides scenario config)
            chunk_overlap: Custom chunk overlap (optional, highest priority, overrides scenario config)

        Returns:
            Document chunk list
        """
        file_name = os.path.basename(file_path)
        ext = Path(file_path).suffix.lower()

        logger.info(f"Start parsing file: {file_name}, scenario: {scenario_id or 'default'}, chunk_size: {chunk_size or 'default'}, chunk_overlap: {chunk_overlap or 'default'}")

        if progress_callback:
            progress_callback(10, "Read file content")

        text = self._extract_text(file_path, ext)

        if progress_callback:
            progress_callback(40, "Clean text content")

        text = self._clean_text(text)

        quality_warnings = self._check_parse_quality(text, file_name, ext)
        for warning in quality_warnings:
            logger.warning(f"[Document Parse Quality] {warning}")

        if progress_callback:
            progress_callback(60, "Text chunking processing")

        chunking_params = self._get_chunking_params(scenario_id, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        chunks = self._split_text(text, file_name, chunking_params)

        for chunk in chunks:
            if quality_warnings:
                chunk.metadata['parse_quality_warnings'] = quality_warnings
            chunk.metadata['total_chars'] = len(text)

        if progress_callback:
            progress_callback(100, f"Text chunking completed, total {len(chunks)} chunks")

        logger.info(f"File parsed: {file_name}, total {len(chunks)} chunks, total chars: {len(text)}")
        return chunks

    def _check_parse_quality(self, text: str, file_name: str, ext: str) -> List[str]:
        """
        Check document parse quality, detect possible parsing issues

        Args:
            text: Parsed text
            file_name: File name
            ext: File extension

        Returns:
            Quality warning list
        """
        warnings = []
        total_chars = len(text)

        if ext == '.pdf':
            if total_chars < 50:
                warnings.append(f"{file_name} parse result too short (only {total_chars} chars), likely scanned or encrypted PDF")
            
            page_mark_count = text.count('--- 第 ')
            if page_mark_count > 1 and total_chars < 200:
                warnings.append(f"{file_name} detected {page_mark_count} pages but few chars, likely image PDF")
            
            if '--- No ' in text and not any(c.isalpha() for c in text):
                warnings.append(f"{file_name} only contains page markers, no actual text content")

        if total_chars == 0:
            warnings.append(f"{file_name} parse result is empty")
        
        elif total_chars < 100:
            warnings.append(f"{file_name} parse result few (only {total_chars} chars), suggest check document quality")

        return warnings

    def _extract_text(self, file_path: str, ext: str) -> str:
        """
        Extract text based on file format

        Args:
            file_path: File path
            ext: File extension

        Returns:
            Extracted text content
        """
        text = ""

        try:
            if ext == '.pdf':
                text = self._extract_pdf(file_path)
            elif ext in ['.docx', '.doc']:
                text = self._extract_docx(file_path)
            elif ext in ['.xlsx', '.xls']:
                text = self._extract_excel(file_path)
            elif ext in ['.pptx', '.ppt']:
                text = self._extract_pptx(file_path)
            elif ext == '.txt':
                text = self._extract_txt(file_path)
            else:
                raise ValueError(_('parser.unsupported_format', None, ext))
        except Exception as e:
            logger.error(f"提取文本失败 {file_path}: {e}")
            raise

        return text

    def _extract_pdf(self, file_path: str) -> str:
        """Extract PDF text, prefer pdfplumber (better Chinese support), fall back to pypdf if unavailable"""
        text_parts = []
        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    try:
                        page_text = page.extract_text() or ""
                        text_parts.append(f"\n--- 第 {i + 1} 页 ---\n{page_text}")
                    except Exception as e:
                        logger.warning(f"PDF第 {i + 1} 页解析失败 (pdfplumber): {e}")
            logger.info(f"使用 pdfplumber 解析 PDF: {file_path}")
        except ImportError:
            logger.warning("pdfplumber 未安装，回退到 pypdf。建议安装 pdfplumber 以获得更好的中文支持: pip install pdfplumber")
            from pypdf import PdfReader
            reader = PdfReader(file_path)
            for i, page in enumerate(reader.pages):
                try:
                    page_text = page.extract_text() or ""
                    text_parts.append(f"\n--- 第 {i + 1} 页 ---\n{page_text}")
                except Exception as e:
                    logger.warning(f"PDF第 {i + 1} 页解析失败 (pypdf): {e}")

        return "\n".join(text_parts)

    def _extract_docx(self, file_path: str) -> str:
        """Extract Word document text"""
        from docx import Document

        doc = Document(file_path)
        text_parts = []

        # Extract paragraphs
        for para in doc.paragraphs:
            if para.text.strip():
                text_parts.append(para.text)

        # Extract tables
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(cell.text.strip() for cell in row.cells)
                if row_text.strip():
                    text_parts.append(row_text)

        return "\n".join(text_parts)

    def _extract_excel(self, file_path: str) -> str:
        """Extract Excel text"""
        from openpyxl import load_workbook

        wb = load_workbook(file_path, read_only=True, data_only=True)
        text_parts = []

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            text_parts.append(f"\n=== Sheet: {sheet_name} ===\n")

            for row in ws.iter_rows(values_only=True):
                row_text = " | ".join(str(cell) if cell is not None else "" for cell in row)
                if row_text.strip():
                    text_parts.append(row_text)

        wb.close()
        return "\n".join(text_parts)

    def _extract_pptx(self, file_path: str) -> str:
        """Extract PPT text"""
        from pptx import Presentation

        prs = Presentation(file_path)
        text_parts = []

        for i, slide in enumerate(prs.slides):
            text_parts.append(f"\n--- 第 {i + 1} 页 ---\n")
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    text_parts.append(shape.text)

        return "\n".join(text_parts)

    def _extract_txt(self, file_path: str) -> str:
        """Extract plain text"""
        encodings = ['utf-8', 'gbk', 'gb2312', 'latin-1']

        for encoding in encodings:
            try:
                with open(file_path, 'r', encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue

        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()

    def _clean_text(self, text: str) -> str:
        """
        Clean text

        Args:
            text: Raw text

        Returns:
            Cleaned text
        """
        # Remove extra whitespace
        text = re.sub(r'\r\n', '\n', text)
        text = re.sub(r'\r', '\n', text)

        # Remove extra blank lines
        text = re.sub(r'\n{3,}', '\n\n', text)

        # Remove extra spaces
        text = re.sub(r'[ \t]{2,}', ' ', text)

        # Remove invisible characters (keep basic whitespace characters)
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)

        return text.strip()

    def _split_text(self, text: str, file_name: str, chunking_params: Dict) -> List[DocumentChunk]:
        """
        Smart chunking (by sentence boundary)

        Args:
            text: Text content
            file_name: File name (for metadata)
            chunking_params: Chunking parameter dictionary (contains chunk_size, chunk_overlap, scenario_id)

        Returns:
            Chunk list
        """
        if not text:
            return []

        chunks = []
        chunk_size = chunking_params.get('chunk_size', self.chunk_size)
        overlap = chunking_params.get('chunk_overlap', self.chunk_overlap)
        scenario_id = chunking_params.get('scenario_id')

        paragraphs = text.split('\n')

        current_chunk = ""
        current_chars = 0
        chunk_index = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            para_len = len(para)

            if current_chars + para_len <= chunk_size:
                if current_chunk:
                    current_chunk += "\n" + para
                else:
                    current_chunk = para
                current_chars += para_len + 1
            else:
                if current_chunk:
                    chunks.append(DocumentChunk(
                        text=current_chunk,
                        metadata={
                            'file_name': file_name,
                            'chunk_index': chunk_index,
                            'char_count': len(current_chunk),
                            'scenario_id': scenario_id,
                            'chunk_size': chunk_size,
                            'chunk_overlap': overlap,
                        }
                    ))
                    chunk_index += 1

                    if overlap > 0 and len(current_chunk) > overlap:
                        overlap_text = current_chunk[-overlap:]
                        sentence_end = overlap_text.rfind('。')
                        if sentence_end == -1:
                            sentence_end = overlap_text.rfind('.')
                        if sentence_end > 0:
                            overlap_text = overlap_text[sentence_end + 1:]

                        current_chunk = overlap_text + "\n" + para
                        current_chars = len(current_chunk)
                    else:
                        current_chunk = para
                        current_chars = para_len
                else:
                    for i in range(0, len(para), chunk_size - overlap):
                        chunk_text = para[i:i + chunk_size]
                        if chunk_text:
                            chunks.append(DocumentChunk(
                                text=chunk_text,
                                metadata={
                                    'file_name': file_name,
                                    'chunk_index': chunk_index,
                                    'char_count': len(chunk_text),
                                    'scenario_id': scenario_id,
                                    'chunk_size': chunk_size,
                                    'chunk_overlap': overlap,
                                }
                            ))
                            chunk_index += 1
                    current_chunk = ""
                    current_chars = 0

        if current_chunk:
            chunks.append(DocumentChunk(
                text=current_chunk,
                metadata={
                    'file_name': file_name,
                    'chunk_index': chunk_index,
                    'char_count': len(current_chunk),
                    'scenario_id': scenario_id,
                    'chunk_size': chunk_size,
                    'chunk_overlap': overlap,
                }
            ))

        return chunks

    def parse_directory(self, dir_path: str, progress_callback: Optional[Callable] = None, scenario_id: Optional[str] = None) -> Dict[str, List[DocumentChunk]]:
        """
        Batch parse documents in a directory

        Args:
            dir_path: Directory path
            progress_callback: Progress callback
            scenario_id: Scenario ID (optional, used to load scenario-specific chunking parameters)

        Returns:
            {file_name: chunk list} dictionary
        """
        dir_path = Path(dir_path)
        if not dir_path.is_dir():
            raise ValueError(_('parser.dir_not_found', None, dir_path))

        result = {}
        files = [f for f in dir_path.iterdir() if f.is_file() and self.is_supported(str(f))]
        total_files = len(files)

        logger.info(f"开始批量解析目录: {dir_path}, 共 {total_files} 个文件, 场景: {scenario_id or '默认'}")

        for i, file_path in enumerate(files):
            try:
                def file_progress(p, msg, fname=file_path.name):
                    if progress_callback:
                        overall_p = (i + p / 100) / total_files * 100
                        progress_callback(overall_p, f"[{fname}] {msg}")

                chunks = self.parse_file(str(file_path), file_progress, scenario_id)
                result[file_path.name] = chunks
            except Exception as e:
                logger.error(f"解析文件失败 {file_path}: {e}")
                result[file_path.name] = []

        return result
