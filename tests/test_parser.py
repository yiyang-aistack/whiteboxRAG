"""
Document parser unit tests
"""
import os
import tempfile
import pytest
from pathlib import Path

# Add project root directory to Python path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.document_parser import DocumentParser, DocumentChunk


class TestDocumentParser:
    """Document parser test class"""

    def setup_method(self):
        """Execute before each test method"""
        self.parser = DocumentParser()

    def test_is_supported(self):
        """Test file format support judgment"""
        assert self.parser.is_supported("test.pdf") == True
        assert self.parser.is_supported("test.docx") == True
        assert self.parser.is_supported("test.xlsx") == True
        assert self.parser.is_supported("test.txt") == True
        assert self.parser.is_supported("test.pptx") == True
        assert self.parser.is_supported("test.mp4") == False
        assert self.parser.is_supported("test.jpg") == False

    def test_clean_text(self):
        """Test text cleaning"""
        dirty_text = "Hello\r\n\r\n\r\nWorld\n\n\nTest"
        clean_text = self.parser._clean_text(dirty_text)
        assert "\r" not in clean_text
        assert "\n\n\n" not in clean_text

    def test_clean_text_removes_extra_whitespace(self):
        """Test removing extra whitespace"""
        text = "Hello    World  Test"
        clean = self.parser._clean_text(text)
        assert "    " not in clean

    def test_split_text_empty(self):
        """Test empty text chunking"""
        chunks = self.parser._split_text("", "test.txt", {})
        assert len(chunks) == 0

    def test_split_text_small(self):
        """Test small text chunking"""
        text = "这是一个小段落。"
        chunks = self.parser._split_text(text, "test.txt", {})
        assert len(chunks) == 1
        assert chunks[0].text == text

    def test_split_text_large(self):
        """Test large text chunking"""
        # Create a large paragraph
        text = "这是第{}句话。".format("测试内容 " * 50) * 20
        chunks = self.parser._split_text(text, "test.txt", {})
        assert len(chunks) > 1

        # Verify each chunk has metadata
        for chunk in chunks:
            assert chunk.metadata is not None
            assert 'file_name' in chunk.metadata
            assert chunk.metadata['file_name'] == "test.txt"

    def test_chunk_metadata(self):
        """Test chunk metadata"""
        text = "测试文本。"
        chunks = self.parser._split_text(text, "myfile.pdf", {})
        assert chunks[0].metadata['file_name'] == "myfile.pdf"
        assert 'chunk_index' in chunks[0].metadata

    def test_extract_txt_utf8(self):
        """Test TXT file extraction (UTF-8)"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write("这是UTF-8编码的测试内容。\n第二行内容。")
            temp_path = f.name

        try:
            text = self.parser._extract_txt(temp_path)
            assert "UTF-8" in text
            assert "第二行" in text
        finally:
            os.unlink(temp_path)

    def test_extract_txt_gbk(self):
        """Test TXT file extraction (GBK)"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='gbk') as f:
            f.write("这是GBK编码的测试内容。")
            temp_path = f.name

        try:
            text = self.parser._extract_txt(temp_path)
            assert "GBK" in text
        finally:
            os.unlink(temp_path)


class TestDocumentChunk:
    """Document chunk test"""

    def test_creation(self):
        """Test chunk creation"""
        chunk = DocumentChunk("测试文本", {"source": "test"})
        assert chunk.text == "测试文本"
        assert chunk.metadata["source"] == "test"
        assert chunk.chunk_id is None

    def test_to_dict(self):
        """Test convert to dict"""
        chunk = DocumentChunk("测试", {"idx": 1})
        d = chunk.to_dict()
        assert d['text'] == "测试"
        assert d['metadata']['idx'] == 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
