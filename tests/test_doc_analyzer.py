"""
Document analyzer unit test
"""
import os
import tempfile
import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from docAnalyze import DocumentAnalyzer


class TestDocumentAnalyzer:
    """Document analyzer test class"""

    def __init__(self):
        self.analyzer = DocumentAnalyzer(kb_id=None)
        self.passed = 0
        self.failed = 0

    def run(self):
        """Run all tests"""
        print("=" * 60)
        print("文档分析器单元测试")
        print("=" * 60)

        self.test_parse_document_structure()
        self.test_parse_document_structure_no_title()
        self.test_parse_document_structure_flat()
        self.test_section_to_qa()
        self.test_generate_opt_document()
        self.test_generate_opt_document_empty()
        self.test_extract_keywords()
        self.test_generate_suggestion_document()
        self.test_load_source_documents()
        self.test_load_source_documents_nonexistent()
        self.test_identify_document_issues_too_short()
        self.test_identify_document_issues_no_chinese()
        self.test_analyze_document_coverage()

        print("=" * 60)
        print(f"测试结果: 通过 {self.passed} / 失败 {self.failed}")
        print("=" * 60)
        return self.failed == 0

    def _assert(self, condition, message):
        """Custom assertion"""
        if condition:
            print(f"✅ {message}")
            self.passed += 1
        else:
            print(f"❌ {message}")
            self.failed += 1

    def test_parse_document_structure(self):
        """Test document structure parsing"""
        content = """产品规范文档

1. 产品基本信息
   - 产品名称：发动机涡轮叶片组件
   - 产品编号：ENG-TB-001

2. 技术规范
   2.1 材料要求
   - 叶片材料：钛合金 Ti-6Al-4V
   - 表面处理：阳极氧化处理

   2.2 性能参数
   - 工作温度：≤1200°C
   - 最大转速：15000 rpm"""

        structure = self.analyzer._parse_document_structure(content)
        
        self._assert(structure['title'] == "产品规范文档", "标题解析正确")
        self._assert(len(structure['sections']) == 2, "章节数量正确")
        self._assert(structure['sections'][0]['title'] == "产品基本信息", "章节1标题正确")
        self._assert(structure['sections'][1]['title'] == "技术规范", "章节2标题正确")
        self._assert(len(structure['sections'][1]['subsections']) == 2, "子章节数量正确")
        self._assert(structure['sections'][1]['subsections'][0]['title'] == "材料要求", "子章节1标题正确")
        self._assert(structure['sections'][1]['subsections'][1]['title'] == "性能参数", "子章节2标题正确")

    def test_parse_document_structure_no_title(self):
        """Test document structure parsing without title"""
        content = """1. 第一部分
   - 项目A：值A
   - 项目B：值B"""

        structure = self.analyzer._parse_document_structure(content)
        
        self._assert(structure['title'] == "", "无标题处理正确")
        self._assert(len(structure['sections']) == 1, "章节数量正确")

    def test_parse_document_structure_flat(self):
        """Test flat structure document parsing"""
        content = """产品说明

- 名称：测试产品
- 版本：V1.0
- 描述：这是一个测试"""

        structure = self.analyzer._parse_document_structure(content)
        
        self._assert(structure['title'] == "产品说明", "标题解析正确")
        self._assert(len(structure['sections']) == 1, "章节数量正确")
        self._assert(len(structure['sections'][0]['items']) == 3, "条目数量正确")

    def test_section_to_qa(self):
        """Test section to QA pairs"""
        items = ["材料：钛合金", "温度：1200°C"]
        qa_pairs = self.analyzer._section_to_qa("技术规范", items)
        
        self._assert(len(qa_pairs) > 0, "生成了问答对")
        self._assert(any("技术规范" in q for q, _ in qa_pairs), "问答对包含章节名")
        self._assert(any("钛合金" in a for _, a in qa_pairs), "问答对包含答案")

    def test_generate_opt_document(self):
        """Test generate optimized document"""
        content = """测试文档

1. 产品信息
   - 名称：测试产品
   - 版本：V1.0

2. 规格参数
   - 尺寸：100mm"""

        opt_content = self.analyzer._generate_opt_document(content)
        
        self._assert("# 测试文档" in opt_content, "包含标题")
        self._assert("## 产品信息" in opt_content, "包含章节1")
        self._assert("## 规格参数" in opt_content, "包含章节2")
        self._assert("## 常见问答" in opt_content, "包含问答部分")
        self._assert("Q1" in opt_content, "包含问答编号")

    def test_generate_opt_document_empty(self):
        """Test empty document generation"""
        opt_content = self.analyzer._generate_opt_document("")
        
        self._assert("# 优化后的文档" in opt_content, "空文档生成默认标题")

    def test_extract_keywords(self):
        """Test keyword extraction"""
        text = "这个产品的材料是什么？"
        keywords = self.analyzer._extract_keywords(text)
        
        self._assert(isinstance(keywords, list), "返回列表类型")
        self._assert("产品" in keywords or "材料" in keywords, "提取到关键词")

    def test_generate_suggestion_document(self):
        """Test generate suggestion document"""
        content = """测试文档

1. 产品信息
   - 名称：测试产品"""

        self.analyzer.source_docs = [{'file_name': 'test.txt', 'content': content}]
        self.analyzer.user_queries = []
        
        suggestion = self.analyzer._generate_suggestion_document(content)
        
        self._assert("# 文档优化建议" in suggestion, "包含建议标题")
        self._assert("## 一、分析摘要" in suggestion, "包含分析摘要")
        self._assert("## 二、用户高频提问关键词" in suggestion, "包含关键词部分")

    def test_load_source_documents(self):
        """Test load source documents"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            doc_dir = Path(tmp_dir) / "docs"
            doc_dir.mkdir()
            doc_file = doc_dir / "test.txt"
            doc_file.write_text("测试内容", encoding='utf-8')
            
            self.analyzer.source_docs = []
            self.analyzer.doc_dir = doc_dir
            self.analyzer.load_source_documents()
            
            self._assert(len(self.analyzer.source_docs) == 1, "加载了1个文档")
            self._assert(self.analyzer.source_docs[0]['file_name'] == "test.txt", "文件名正确")

    def test_load_source_documents_nonexistent(self):
        """Test load nonexistent document directory"""
        self.analyzer.source_docs = []
        self.analyzer.doc_dir = Path("/nonexistent/path")
        self.analyzer.load_source_documents()
        
        self._assert(len(self.analyzer.source_docs) == 0, "无文档时返回空列表")

    def test_identify_document_issues_too_short(self):
        """Test identify too short document issue"""
        self.analyzer.source_docs = [{'file_name': 'short.txt', 'content': '短'}]
        issues = self.analyzer.identify_document_issues()
        
        self._assert(len(issues) >= 1, "识别到问题")
        self._assert(any(issue['type'] == 'too_short' for issue in issues), "识别到过短问题")

    def test_identify_document_issues_no_chinese(self):
        """Test identify no Chinese document issue"""
        self.analyzer.source_docs = [{'file_name': 'english.txt', 'content': 'Only English content here'}]
        issues = self.analyzer.identify_document_issues()
        
        self._assert(len(issues) >= 1, "识别到问题")
        self._assert(any(issue['type'] == 'no_chinese' for issue in issues), "识别到无中文问题")

    def test_analyze_document_coverage(self):
        """Test document coverage analysis"""
        content = '产品材料是钛合金'
        self.analyzer.source_docs = [{
            'file_name': 'test.txt', 
            'content': content,
            'char_count': len(content),
            'line_count': len(content.split('\n'))
        }]
        self.analyzer.user_queries = [{'query': '产品材料是什么'}]
        
        self.analyzer.analyze_document_coverage()
        
        self._assert('test.txt' in self.analyzer.coverage_analysis, "覆盖度分析有记录")
        self._assert(self.analyzer.coverage_analysis['test.txt']['coverage_rate'] > 0, "覆盖度大于0")


if __name__ == '__main__':
    tester = TestDocumentAnalyzer()
    success = tester.run()
    sys.exit(0 if success else 1)
