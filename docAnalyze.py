#!/usr/bin/env python3
"""
Document analysis tool - docAnalyze.py
Uses existing conversation logs to reverse-analyze source document quality and generate optimized document versions

Features:
1. Extract user question patterns and keywords from conversation logs
2. Analyze structural issues and information gaps in source documents
3. Optimize document structure based on user question patterns
4. Generate cleaned document versions to improve retrieval hit rate and answer accuracy

TODO:
1. integrate with LLM wiki to setup multi layers of document analysis

"""

import json
import glob
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

from config import config
from service.logger import get_logger


class DocumentAnalyzer:
    """Document analyzer"""

    def __init__(self, kb_id: str = None):
        self.kb_id = kb_id
        self.config = config.get('document_analyzer', {})
        self.trace_dir = Path(self.config.get('trace_dir', './storage/traces'))
        self.doc_dir = Path(self.config.get('doc_dir', './storage/documents'))
        self.output_dir = self.config.get('output_dir', './storage/optimized_docs')
        self.min_keyword_length = self.config.get('min_keyword_length', 2)
        self.max_top_keywords = self.config.get('max_top_keywords', 20)
        self.max_ineffective_examples = self.config.get('max_ineffective_examples', 5)
        self.min_doc_length = self.config.get('min_doc_length', 100)
        self.max_doc_length = self.config.get('max_doc_length', 5000)
        self.min_chinese_ratio = self.config.get('min_chinese_ratio', 50)
        self.use_llm_for_qa = self.config.get('use_llm_for_qa', False)
        self.llm_base_url = config.get('ollama.llm_base_url', '')
        self.llm_model = config.get('ollama.llm_model', '')
        
        self.source_docs = []
        self.user_queries = []
        self.query_keywords = Counter()
        self.intent_patterns = Counter()
        self.coverage_analysis = {}
        self.feedback_history = []
        self.logger = get_logger('doc_analyzer')
        self._ollama_client = None

    def load_source_documents(self):
        """Load source documents"""
        if self.kb_id:
            doc_folder = self.doc_dir / self.kb_id
        else:
            doc_folder = self.doc_dir

        if not doc_folder.exists():
            self.logger.error(f" Document directory not found: {doc_folder}")
            return

        for ext in ['*.txt', '*.md', '*.json']:
            for doc_file in doc_folder.glob(ext):
                try:
                    with open(doc_file, 'r', encoding='utf-8') as f:
                        content = f.read()
                    self.source_docs.append({
                        'file_name': doc_file.name,
                        'path': str(doc_file),
                        'content': content,
                        'char_count': len(content),
                        'line_count': len(content.split('\n'))
                    })
                except Exception as e:
                    self.logger.warning(f" Document read failed for: {doc_file}, {e}")

        self.logger.info(f"Loaded {len(self.source_docs)} source documents")

    def load_conversation_logs(self):
        """Load conversation logs"""
        trace_files = sorted(
            self.trace_dir.glob('*.json'),
            key=lambda f: f.stat().st_mtime,
            reverse=True
        )

        for trace_file in trace_files:
            try:
                with open(trace_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                if self.kb_id and data.get('kb_id') != self.kb_id:
                    continue

                query = data.get('query', '')
                answer = data.get('final_answer', '')
                intent_type = data.get('intent_info', {}).get('intent_type', '')
                created_at = data.get('created_at', '')
                eval_score = data.get('evaluation', {}).get('overall_score', 0)
                is_passing = data.get('evaluation', {}).get('is_passing', False)

                self.user_queries.append({
                    'trace_id': data.get('trace_id'),
                    'query': query,
                    'answer': answer,
                    'intent_type': intent_type,
                    'created_at': created_at,
                    'evaluation_score': eval_score,
                    'is_passing': is_passing,
                    'has_results': data.get('has_results', False),
                    'context_count': data.get('retrieval_count', 0)
                })

                self.intent_patterns[intent_type] += 1

                keywords = self._extract_keywords(query)
                self.query_keywords.update(keywords)

            except Exception as e:
                self.logger.warning(f"read conversation log failed for: {trace_file}, {e}")

        self.logger.info(f"Loaded {len(self.user_queries)} conversation records")
        self.logger.info(f"Intent distribution: {dict(self.intent_patterns)}")

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract keywords (bilingual: Chinese via jieba + English via sklearn stop words)"""
        import jieba
        words = jieba.lcut(text)
        """Stop word list
        Chinese stop words + English stop words (from sklearn.feature_extraction.text).
        sklearn is already a project dependency (used in analyze_document_relations).
        Customized based on actual conversation logs.
        """
        stop_words = {
            # Chinese stop words
            '的', '了', '是', '我', '你', '他', '她', '它', '这', '那',
            '不会', '不必', '不用', '未', '尚未', '未然', '未曾',
            # English stop words (lowercase, matched case-insensitively below)
            'a', 'an', 'the', 'and', 'or', 'but', 'if', 'then', 'else', 'when',
            'at', 'by', 'for', 'with', 'about', 'against', 'between', 'into',
            'through', 'during', 'before', 'after', 'above', 'below', 'to',
            'from', 'up', 'down', 'in', 'out', 'on', 'off', 'over', 'under',
            'again', 'further', 'once', 'is', 'are', 'was', 'were', 'be',
            'been', 'being', 'have', 'has', 'had', 'having', 'do', 'does',
            'did', 'doing', 'i', 'me', 'my', 'myself', 'we', 'our', 'ours',
            'ourselves', 'you', 'your', 'yours', 'yourself', 'yourselves',
            'he', 'him', 'his', 'himself', 'she', 'her', 'hers', 'herself',
            'it', 'its', 'itself', 'they', 'them', 'their', 'theirs',
            'themselves', 'what', 'which', 'who', 'whom', 'this', 'that',
            'these', 'those', 'am', 'of', 'as', 'so', 'than', 'too', 'very',
            'can', 'will', 'just', 'don', 'should', 'now', 'how', 'why',
            'where', 'when', 'who', 'whom', 'whose'
        }

        # Case-insensitive matching for English tokens
        keywords = [
            w for w in words
            if len(w) >= self.min_keyword_length
            and w.lower() not in stop_words
            and not re.fullmatch(r'[]+', w)   # also filter pure digits/punctuation
        ]
        return keywords

    def analyze_document_coverage(self):
        """Analyze document coverage"""
        if not self.source_docs or not self.user_queries:
            self.logger.warning(" Please load documents and conversation logs first")
            return

        for doc in self.source_docs:
            content = doc['content']
            covered_keywords = []
            uncovered_keywords = []
            question_coverage = 0

            for query_info in self.user_queries:
                query = query_info['query']
                query_keywords = self._extract_keywords(query)

                has_match = any(kw in content for kw in query_keywords)
                if has_match:
                    question_coverage += 1
                    covered_keywords.extend([kw for kw in query_keywords if kw in content])

            coverage_rate = question_coverage / len(self.user_queries) if self.user_queries else 0

            self.coverage_analysis[doc['file_name']] = {
                'coverage_rate': coverage_rate,
                'covered_questions': question_coverage,
                'total_questions': len(self.user_queries),
                'covered_keywords': list(set(covered_keywords)),
                'uncovered_keywords': []
            }

            self.logger.info(f"[Analysis] Document: {doc['file_name']}")
            self.logger.info(f"  Coverage: {coverage_rate:.1%} ({question_coverage}/{len(self.user_queries)})")
            self.logger.info(f"  Character count: {doc['char_count']}, Line count: {doc['line_count']}")

    def analyze_query_effectiveness(self):
        """Analyze query effectiveness"""
        self.logger.info("=== Query effectiveness analysis ===")

        effective_queries = [q for q in self.user_queries if q['evaluation_score'] > 0 or q['is_passing']]
        ineffective_queries = [q for q in self.user_queries if not q['is_passing']]

        self.logger.info(f"Effective queries: {len(effective_queries)}/{len(self.user_queries)}")
        self.logger.info(f"Invalid queries: {len(ineffective_queries)}/{len(self.user_queries)}")

        self.logger.info("Top keywords for effective queries:")
        for keyword, count in self.query_keywords.most_common(self.max_top_keywords):
            self.logger.info(f"  {keyword}: {count} times")

        self.logger.info("Invalid queries examples:")
        for q in ineffective_queries[:self.max_ineffective_examples]:
            self.logger.info(f"  [{q['intent_type']}] {q['query']}")

    def identify_document_issues(self):
        """Identify document issues"""
        self.logger.info("=== Document issue identification ===")

        issues = []
        for doc in self.source_docs:
            content = doc['content']

            if len(content) < self.min_doc_length:
                issues.append({
                    'doc': doc['file_name'],
                    'type': 'too_short',
                    'severity': 'critical',
                    'description': f'Document content is too short ({len(content)} characters)',
                    'suggestion': 'Add more detailed information'
                })

            if len(content) > self.max_doc_length:
                issues.append({
                    'doc': doc['file_name'],
                    'type': 'too_long',
                    'severity': 'warning',
                    'description': f'Document content is too long ({len(content)} characters)',
                    'suggestion': 'Consider splitting the document or adding hierarchical structure'
                })

            if re.search(r'[\u4e00-\u9fff]', content) and len(re.findall(r'[\u4e00-\u9fff]', content)) < self.min_chinese_ratio:
                issues.append({
                    'doc': doc['file_name'],
                    'type': 'low_chinese_ratio',
                    'severity': 'warning',
                    'description': 'Chinese content ratio is low',
                    'suggestion': 'Add more Chinese description content'
                })

            if '---' in content and '第' in content and '页' in content:
                issues.append({
                    'doc': doc['file_name'],
                    'type': 'page_markers',
                    'severity': 'medium',
                    'description': 'Document contains page markers, which may affect retrieval',
                    'suggestion': 'Remove page markers to keep content pure'
                })

            if not re.search(r'[\u4e00-\u9fff]', content):
                issues.append({
                    'doc': doc['file_name'],
                    'type': 'no_chinese',
                    'severity': 'critical',
                    'description': 'Document does not contain Chinese content, which may affect retrieval',
                    'suggestion': 'Add Chinese description or translate key content'
                })

        for issue in issues:
            severity_icon = {'critical': '🔴', 'medium': '🟡', 'warning': '🟢'}
            self.logger.warning(f"{severity_icon.get(issue['severity'], '⚪')} [{issue['type']}] {issue['doc']}: {issue['description']}")
            self.logger.info(f"    Suggestion: {issue['suggestion']}")

        return issues

    def generate_optimized_document(self, output_dir: str = None) -> Dict[str, Dict[str, str]]:
        """Generate optimized document

        Returns:
            Mapping of original file name -> {"suggestion_file", "optimized_file"} holding the
            *actual* base names written to disk. Callers must report these instead of
            reconstructing the names (the suffixes are not localized).
        """
        output_dir = output_dir or self.output_dir
        self.logger.info(f"===  Generate optimized document ===")

        os.makedirs(output_dir, exist_ok=True)
        written: Dict[str, Dict[str, str]] = {}

        for doc in self.source_docs:
            suggestion_content = self._generate_suggestion_document(doc['content'])
            opt_content = self._generate_opt_document(doc['content'])

            file_name = doc['file_name']
            name_without_ext = os.path.splitext(file_name)[0]
            ext = os.path.splitext(file_name)[1]

            suggestion_path = Path(output_dir) / f"{name_without_ext}_suggestion{ext}"
            with open(suggestion_path, 'w', encoding='utf-8') as f:
                f.write(suggestion_content)

            opt_path = Path(output_dir) / f"{name_without_ext}_opt{ext}"
            with open(opt_path, 'w', encoding='utf-8') as f:
                f.write(opt_content)

            written[file_name] = {
                'suggestion_file': suggestion_path.name,
                'optimized_file': opt_path.name,
            }

            self.logger.info(f"  Suggestion document generated: {suggestion_path}")
            self.logger.info(f"  Optimized document generated: {opt_path}")
            self.logger.info(f"  Original character count: {doc['char_count']}")
            self.logger.info(f"  Suggestion character count: {len(suggestion_content)}")
            self.logger.info(f"  Optimized character count: {len(opt_content)}")

            self._show_optimization_diff(doc['content'], opt_content)

        return written

    def _optimize_document(self, content: str) -> str:
        """Optimize document content"""
        lines = content.split('\n')
        optimized_lines = []

        qa_pairs = []
        current_section = ""
        current_items = []

        for line in lines:
            line = line.strip()

            if not line:
                if current_section and current_items:
                    qa_pairs.extend(self._section_to_qa(current_section, current_items))
                    current_section = ""
                    current_items = []
                continue

            if re.match(r'^\d+\.?\s+', line):
                if current_section and current_items:
                    qa_pairs.extend(self._section_to_qa(current_section, current_items))

                current_section = re.sub(r'^\d+\.?\s+', '', line)
                current_items = []
                optimized_lines.append(line)
            elif line.startswith(('-', '*', '•', '·')):
                item = re.sub(r'^[-*•·]\s*', '', line)
                current_items.append(item)

                parts = re.split(r'[:：]', item, 1)
                if len(parts) == 2:
                    question = f"{current_section} in {parts[0].strip()} ？"
                    answer = parts[1].strip()
                    qa_pairs.append((question, answer))
                else:
                    qa_pairs.append((f"{current_section} contains content？", item))
            else:
                optimized_lines.append(line)

        if current_section and current_items:
            qa_pairs.extend(self._section_to_qa(current_section, current_items))

        optimized_content = content + "\n\n" + "=" * 60 + "\n"
        optimized_content += "# Optimized QA pairs (enhanced to increase retrieval hit rate)\n\n"

        for i, (question, answer) in enumerate(qa_pairs, 1):
            optimized_content += f"## Q{i}: {question}\n"
            optimized_content += f"A{i}: {answer}\n\n"

        for keyword, count in self.query_keywords.most_common(self.max_top_keywords):
            optimized_content += f"## Keyword: {keyword} ({count} times)\n"

        return optimized_content

    def _generate_suggestion_document(self, content: str) -> str:
        """Generate document with modification suggestions"""
        structure = self._parse_document_structure(content)
        qa_pairs = []

        for section in structure['sections']:
            section_items = []
            if section.get('items'):
                section_items.extend(section['items'])
            for subsection in section.get('subsections', []):
                section_items.extend(subsection.get('items', []))
            
            if section_items:
                qa_pairs.extend(self._section_to_qa(section['title'], section_items))

        suggestion = "# Document optimization suggestions\n\n"
        suggestion += "## Analysis summary of the document\n\n"
        
        first_doc = self.source_docs[0]['file_name'] if self.source_docs else ''
        suggestion += f"- Document coverage rate: {self.coverage_analysis.get(first_doc, {}).get('coverage_rate', 0):.1%}\n"
        suggestion += f"- User queries: {len(self.user_queries)} times\n"
        suggestion += f"- Top intents: {', '.join([f'{k}({v} times)' for k, v in self.intent_patterns.most_common(3)])}\n\n"

        suggestion += "## Top keywords in user queries\n\n"
        for keyword, count in self.query_keywords.most_common(10):
            suggestion += f"- {keyword}: {count} times\n"
        suggestion += "\n"

        suggestion += "## Suggested QA pairs to add\n\n"
        suggestion += "Based on actual conversation logs, the following QA pairs are suggested to be added to the document to improve retrieval hit rate:\n\n"

        for i, (question, answer) in enumerate(qa_pairs, 1):
            suggestion += f"### Q{i}: {question}\n"
            suggestion += f"**Answer**: {answer}\n\n"

        suggestion += "## Optimization reasons\n\n"
        suggestion += "1. **Increase retrieval hit rate**: Add QA pairs to the document to improve retrieval hit rate\n\n"
        suggestion += "2. **Enhance semantic richness**: QA pairs contain rich semantic information\n\n"
        suggestion += "3. **Cover user query patterns**: Generate based on actual conversation logs to cover user query patterns and match user real-world usage\n\n"

        suggestion += "## Original document content\n"
        suggestion += "---\n"
        suggestion += content

        return suggestion

    def _parse_document_structure(self, content: str) -> Dict:
        """Parse document structure"""
        lines = content.split('\n')
        structure = {
            'title': '',
            'sections': []
        }
        
        current_section = None
        current_subsection = None
        current_items = []
        title_set = False

        for line in lines:
            line = line.strip()
            
            if not line:
                if current_section and current_items:
                    if current_subsection:
                        current_section['subsections'].append({
                            'title': current_subsection,
                            'items': current_items
                        })
                        current_subsection = None
                    else:
                        current_section['items'] = current_items
                    current_items = []
                continue

            if not title_set and len(line) > 0 and not re.match(r'^\d+', line) and not line.startswith(('-', '*', '•', '·')):
                structure['title'] = line
                title_set = True
                continue

            level1_match = re.match(r'^(\d+)\.?\s+(.+)', line)
            level2_match = re.match(r'^(\d+\.\d+)\.?\s+(.+)', line)
            
            if level1_match:
                if current_section:
                    if current_subsection:
                        current_section['subsections'].append({
                            'title': current_subsection,
                            'items': current_items
                        })
                    elif current_items:
                        current_section['items'] = current_items
                    structure['sections'].append(current_section)
                
                current_section = {
                    'level': 1,
                    'number': level1_match.group(1),
                    'title': level1_match.group(2),
                    'subsections': [],
                    'items': []
                }
                current_subsection = None
                current_items = []
            
            elif level2_match:
                if current_section:
                    if current_subsection and current_items:
                        current_section['subsections'].append({
                            'title': current_subsection,
                            'items': current_items
                        })
                
                current_subsection = level2_match.group(2)
                current_items = []
            
            elif line.startswith(('-', '*', '•', '·')):
                item = re.sub(r'^[-*•·]\s*', '', line)
                current_items.append(item)
            
            elif current_section and not current_subsection:
                current_items.append(line)

        if current_section:
            if current_subsection:
                current_section['subsections'].append({
                    'title': current_subsection,
                    'items': current_items
                })
            elif current_items:
                current_section['items'] = current_items
            structure['sections'].append(current_section)
        elif current_items:
            structure['sections'].append({
                'level': 1,
                'number': '1',
                'title': structure['title'] or 'Overview',
                'subsections': [],
                'items': current_items
            })

        return structure

    def _generate_opt_document(self, content: str) -> str:
        """Generate standalone optimized document (without original content)"""
        structure = self._parse_document_structure(content)
        qa_pairs = []

        opt_content = ""
        
        if structure['title']:
            opt_content += f"# {structure['title']}\n\n"
        else:
            opt_content += "# Optimized document\n\n"

        for section in structure['sections']:
            opt_content += f"## {section['title']}\n\n"
            
            if section.get('items'):
                for item in section['items']:
                    opt_content += f"- {item}\n"
                    parts = re.split(r'[:：]', item, 1)
                    if len(parts) == 2:
                        question = f"{section['title']} of {parts[0].strip()} ？"
                        answer = parts[1].strip()
                        qa_pairs.append((question, answer))
                opt_content += "\n"
            
            for subsection in section.get('subsections', []):
                opt_content += f"### {subsection['title']}\n\n"
                
                for item in subsection['items']:
                    opt_content += f"- {item}\n"
                    parts = re.split(r'[:：]', item, 1)
                    if len(parts) == 2:
                        question = f"{subsection['title']} of {parts[0].strip()} ？"
                        answer = parts[1].strip()
                        qa_pairs.append((question, answer))
                        question2 = f"{section['title']} of {parts[0].strip()} ？"
                        qa_pairs.append((question2, answer))
                opt_content += "\n"

        for section in structure['sections']:
            section_items = []
            if section.get('items'):
                section_items.extend(section['items'])
            for subsection in section.get('subsections', []):
                section_items.extend(subsection.get('items', []))
            
            if section_items:
                qa_pairs.extend(self._section_to_qa(section['title'], section_items))

                for subsection in section.get('subsections', []):
                    subsection_items = subsection.get('items', [])
                    if subsection_items:
                        qa_pairs.extend(self._section_to_qa(subsection['title'], subsection_items))

        opt_content += "## Common Questions\n\n"
        for i, (question, answer) in enumerate(qa_pairs, 1):
            opt_content += f"### Q{i}: {question}\n"
            opt_content += f"{answer}\n\n"

        return opt_content

    def _section_to_qa(self, section: str, items: List[str]) -> List[Tuple[str, str]]:
        """Convert section to Q&A pairs"""
        qa_pairs = []

        qa_pairs.append((f"{section} has what content？", "; ".join(items)))

        for item in items:
            parts = re.split(r'[:：]', item, 1)
            if len(parts) == 2:
                qa_pairs.append((f"{section} of {parts[0].strip()} ？", parts[1].strip()))
                qa_pairs.append((f"{parts[0].strip()} ？", parts[1].strip()))
            else:
                qa_pairs.append((f"{section} include {item} ？", f"Yes，{section} include {item}"))

        return qa_pairs

    def _show_optimization_diff(self, original: str, optimized: str):
        """Show optimization diff"""
        original_lines = [l.strip() for l in original.split('\n') if l.strip()]
        optimized_only = []

        for line in optimized.split('\n'):
            if line.strip() and line.strip() not in original_lines and not line.startswith('='):
                optimized_only.append(line[:80])

        self.logger.info("Preview of new content (first 5 lines):")
        for line in optimized_only[:5]:
            self.logger.info(f"    + {line}")

    def load_feedback_history(self):
        """Load user feedback history"""
        # Read the same file the rule engine writes. Hardcoding the path here meant that
        # pointing rule_engine.feedback_file somewhere else silently produced an empty history.
        feedback_file = Path(config.get('rule_engine.feedback_file', './storage/feedbacks.json'))
        if feedback_file.exists():
            try:
                with open(feedback_file, 'r', encoding='utf-8') as f:
                    self.feedback_history = json.load(f)
                self.logger.info(f"Loaded {len(self.feedback_history)} feedback records from file")
            except Exception as e:
                self.logger.error(f"Failed to load feedback file from file: {e}")
                self.feedback_history = []

    def analyze_feedback_coverage(self):
        """Analyze feedback coverage, find frequently reported issue types"""
        if not self.feedback_history:
            self.logger.info("No feedback records available")
            return {}

        feedback_type_counts = Counter(f.get('feedback_type', 'other') for f in self.feedback_history)
        intent_counts = Counter(f.get('intent_type', '') for f in self.feedback_history)
        
        self.logger.info(f"Feedback type distribution: {dict(feedback_type_counts)}")
        self.logger.info(f"Intent type distribution: {dict(intent_counts)}")

        return {
            'feedback_type_counts': dict(feedback_type_counts),
            'intent_counts': dict(intent_counts),
            'total_feedbacks': len(self.feedback_history)
        }

    def generate_feedback_based_qa(self) -> List[Tuple[str, str]]:
        """Generate supplementary Q&A pairs based on user feedback"""
        qa_pairs = []
        
        for feedback in self.feedback_history:
            feedback_type = feedback.get('feedback_type', '')
            query = feedback.get('query', '')
            correction = feedback.get('correction', '')
            intent_type = feedback.get('intent_type', '')
            answer = feedback.get('answer', '')
            
            if feedback_type == 'intent_error' and correction:
                question = f"{query} ？"
                qa_pairs.append((question, correction))
                
            elif feedback_type == 'recall_missing' and query and answer:
                question = query
                qa_pairs.append((question, answer))
                
            elif feedback_type == 'answer_wrong' and correction:
                question = query
                qa_pairs.append((question, correction))
                
            elif feedback_type == 'citation_issue' and query:
                question = f"{query} from where？"
                qa_pairs.append((question, "Please refer to the relevant sections in the document"))

        qa_pairs = list(dict.fromkeys(qa_pairs))
        self.logger.info(f"Generated {len(qa_pairs)} Q&A pairs based on feedback")
        return qa_pairs

    def optimize_document_with_feedback(self, content: str) -> str:
        """Optimize document with user feedback"""
        self.load_feedback_history()
        
        structure = self._parse_document_structure(content)
        qa_pairs = []

        opt_content = ""
        
        if structure['title']:
            opt_content += f"# {structure['title']}\n\n"
        else:
            opt_content += "# Optimized Document\n\n"

        for section in structure['sections']:
            opt_content += f"## {section['title']}\n\n"
            
            if section.get('items'):
                for item in section['items']:
                    opt_content += f"- {item}\n"
                opt_content += "\n"
            
            for subsection in section.get('subsections', []):
                opt_content += f"### {subsection['title']}\n\n"
                
                for item in subsection['items']:
                    opt_content += f"- {item}\n"
                opt_content += "\n"

        feedback_qa = self.generate_feedback_based_qa()
        qa_pairs.extend(feedback_qa)

        for section in structure['sections']:
            section_items = []
            if section.get('items'):
                section_items.extend(section['items'])
            for subsection in section.get('subsections', []):
                section_items.extend(subsection.get('items', []))
            
            if section_items:
                qa_pairs.extend(self._section_to_qa(section['title'], section_items))

                for subsection in section.get('subsections', []):
                    subsection_items = subsection.get('items', [])
                    if subsection_items:
                        qa_pairs.extend(self._section_to_qa(subsection['title'], subsection_items))

        qa_pairs = list(dict.fromkeys(qa_pairs))

        opt_content += "## Common Questions\n\n"
        for i, (question, answer) in enumerate(qa_pairs, 1):
            opt_content += f"### Q{i}: {question}\n"
            opt_content += f"{answer}\n\n"

        if feedback_qa:
            opt_content += "## Feedback Based Q&A Pairs\n\n"
            for i, (question, answer) in enumerate(feedback_qa, 1):
                opt_content += f"### Q{i}: {question}\n"
                opt_content += f"{answer}\n\n"

        return opt_content

    def _get_ollama_client(self):
        """Get Ollama client"""
        if self._ollama_client is None:
            try:
                import ollama
                self._ollama_client = ollama.Client(host=self.llm_base_url)
                self.logger.info(f"Ollama client initialized successfully: {self.llm_base_url}")
            except Exception as e:
                self.logger.error(f"Ollama client initialization failed: {e}")
                return None
        return self._ollama_client

    def generate_qa_pairs_with_llm(self, content: str, section_title: str = "") -> List[Tuple[str, str]]:
        """
        Use LLM to generate high-quality Q&A pairs

        Args:
            content: Document content
            section_title: Section title

        Returns:
            Q&A pair list
        """
        if not self.use_llm_for_qa:
            return []

        client = self._get_ollama_client()
        if not client:
            self.logger.warning("Ollama client is not available, skipping LLM-based Q&A pair generation")
            return []

        try:
            prompt = f"""
            Generate high-quality Q&A pairs based on the following document content. Please ensure:
                1. Questions cover the core information points in the document
                2. Answers are accurate and concise
                3. Questions have a variety of forms (what, how, why, etc.)
                4. Each Q&A pair is on a separate line, formatted as: Question|Answer

                Document content:
                {content[:2000]}

                Section title (optional): {section_title}

                    Generate 8-12 Q&A pairs:
                """

            response = client.generate(
                model=self.llm_model,
                prompt=prompt,
                options={"temperature": 0.3, "max_tokens": 1500}
            )

            result = response.get('response', '')
            qa_pairs = []
            
            for line in result.split('\n'):
                line = line.strip()
                if '|' in line:
                    parts = line.split('|', 1)
                    if len(parts) == 2:
                        question = parts[0].strip().replace('Q:', '').replace('Question:', '').strip()
                        answer = parts[1].strip().replace('A:', '').replace('Answer:', '').strip()
                        if question and answer:
                            qa_pairs.append((question, answer))

            self.logger.info(f"LLM generated {len(qa_pairs)} Q&A pairs")
            return qa_pairs

        except Exception as e:
            self.logger.error(f"LLM generation failed: {e}")
            return []

    def generate_optimized_document_with_llm(self, content: str) -> str:
        """
        Use LLM to assist generating optimized document

        Args:
            content: Original document content

        Returns:
            Optimized document content
        """
        structure = self._parse_document_structure(content)
        qa_pairs = []

        opt_content = ""
        
        if structure['title']:
            opt_content += f"# {structure['title']}\n\n"
        else:
            opt_content += "# Optimized Document\n\n"

        for section in structure['sections']:
            opt_content += f"## {section['title']}\n\n"
            
            if section.get('items'):
                for item in section['items']:
                    opt_content += f"- {item}\n"
                opt_content += "\n"
            
            for subsection in section.get('subsections', []):
                opt_content += f"### {subsection['title']}\n\n"
                
                for item in subsection['items']:
                    opt_content += f"- {item}\n"
                opt_content += "\n"

        if self.use_llm_for_qa:
            self.logger.info("Using LLM to generate Q&A pairs")
            for section in structure['sections']:
                section_text = section['title'] + "\n"
                if section.get('items'):
                    section_text += "\n".join(section['items']) + "\n"
                for subsection in section.get('subsections', []):
                    section_text += subsection['title'] + "\n"
                    section_text += "\n".join(subsection.get('items', [])) + "\n"
                
                llm_qa_pairs = self.generate_qa_pairs_with_llm(section_text, section['title'])
                qa_pairs.extend(llm_qa_pairs)

        for section in structure['sections']:
            section_items = []
            if section.get('items'):
                section_items.extend(section['items'])
            for subsection in section.get('subsections', []):
                section_items.extend(subsection.get('items', []))
            
            if section_items:
                qa_pairs.extend(self._section_to_qa(section['title'], section_items))

                for subsection in section.get('subsections', []):
                    subsection_items = subsection.get('items', [])
                    if subsection_items:
                        qa_pairs.extend(self._section_to_qa(subsection['title'], subsection_items))

        qa_pairs = list(dict.fromkeys(qa_pairs))

        opt_content += "## Common Questions\n\n"
        for i, (question, answer) in enumerate(qa_pairs, 1):
            opt_content += f"### Q{i}: {question}\n"
            opt_content += f"{answer}\n\n"

        return opt_content

    def analyze_document_relations(self) -> Dict:
        """Analyze relationships and duplicate content between multiple documents"""
        if len(self.source_docs) < 2:
            self.logger.info("Insufficient documents to analyze relationships, skipping analysis. Required: 2 documents")
            return {}

        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        doc_contents = []
        doc_names = []
        
        for doc in self.source_docs:
            content = doc.get('content', '')
            if len(content) > self.min_doc_length:
                doc_contents.append(content)
                doc_names.append(doc.get('file_name', f"文档{len(doc_names)+1}"))

        if len(doc_contents) < 2:
            self.logger.info("Insufficient valid documents to analyze relationships, skipping analysis. Required: 2 documents")
            return {}

        self.logger.info(f"Analyzing relationships between {len(doc_contents)} documents")

        vectorizer = TfidfVectorizer(max_features=1000, stop_words='english')
        tfidf_matrix = vectorizer.fit_transform(doc_contents)
        similarity_matrix = cosine_similarity(tfidf_matrix)

        relations = {}
        duplicate_pairs = []
        related_pairs = []

        for i in range(len(doc_names)):
            for j in range(i + 1, len(doc_names)):
                similarity = similarity_matrix[i][j]
                doc_pair = (doc_names[i], doc_names[j])
                
                if similarity > 0.8:
                    duplicate_pairs.append({
                        'documents': doc_pair,
                        'similarity': round(similarity, 4),
                        'relation_type': 'duplicate'
                    })
                elif similarity > 0.4:
                    related_pairs.append({
                        'documents': doc_pair,
                        'similarity': round(similarity, 4),
                        'relation_type': 'related'
                    })

        relations['duplicate_pairs'] = duplicate_pairs
        relations['related_pairs'] = related_pairs
        relations['total_documents'] = len(doc_names)

        if duplicate_pairs:
            self.logger.warning(f"Detected {len(duplicate_pairs)} duplicate document pairs, suggesting merging")
            for pair in duplicate_pairs:
                self.logger.warning(f"  - {pair['documents'][0]} 与 {pair['documents'][1]} (similarity: {pair['similarity']})")
        
        if related_pairs:
            self.logger.info(f"Detected {len(related_pairs)} related document pairs")
            for pair in related_pairs[:5]:
                self.logger.info(f"  - {pair['documents'][0]} 与 {pair['documents'][1]} (similarity: {pair['similarity']})")

        return relations

    def generate_cross_document_qa(self) -> List[Tuple[str, str]]:
        """Generate cross-document Q&A pairs, integrating information from multiple documents"""
        if len(self.source_docs) < 2:
            return []

        qa_pairs = []
        doc_summaries = []

        for doc in self.source_docs:
            content = doc.get('content', '')
            if len(content) > self.min_doc_length:
                structure = self._parse_document_structure(content)
                doc_title = structure.get('title', doc.get('file_name', ''))
                key_points = []
                
                for section in structure.get('sections', []):
                    key_points.append(section['title'])
                    if section.get('items'):
                        key_points.extend(section['items'][:3])
                
                doc_summaries.append({
                    'title': doc_title,
                    'key_points': key_points[:10]
                })

        if len(doc_summaries) >= 2:
            for i in range(len(doc_summaries)):
                for j in range(i + 1, len(doc_summaries)):
                    doc1 = doc_summaries[i]
                    doc2 = doc_summaries[j]
                    
                    question = f"{doc1['title']} and {doc2['title']} are related documents. What is the relationship between them?"
                    answer = f"{doc1['title']} has key points: {'; '.join(doc1['key_points'][:5])}。{doc2['title']} has key points: {'; '.join(doc2['key_points'][:5])}."
                    qa_pairs.append((question, answer))

        self.logger.info(f"Generated {len(qa_pairs)} cross-document Q&A pairs")
        return qa_pairs

    def merge_duplicate_documents(self) -> str:
        """Merge highly similar documents"""
        relations = self.analyze_document_relations()
        duplicate_pairs = relations.get('duplicate_pairs', [])
        
        if not duplicate_pairs:
            self.logger.info("No duplicate document pairs found, skipping merge")
            return ""

        merged_content = "# Merged Documents\n\n"
        
        for pair in duplicate_pairs:
            doc1_name, doc2_name = pair['documents']
            doc1_content = ""
            doc2_content = ""
            
            for doc in self.source_docs:
                if doc.get('file_name') == doc1_name:
                    doc1_content = doc.get('content', '')
                elif doc.get('file_name') == doc2_name:
                    doc2_content = doc.get('content', '')
            
            if doc1_content and doc2_content:
                merged_content += f"## Merged Source: {doc1_name} + {doc2_name}\n\n"
                
                lines1 = set(doc1_content.split('\n'))
                lines2 = set(doc2_content.split('\n'))
                
                unique_lines = lines1.union(lines2)
                merged_lines = sorted(unique_lines)
                
                for line in merged_lines[:100]:
                    if line.strip():
                        merged_content += f"{line}\n"
                
                merged_content += "\n"

        self.logger.info(f"Merged {len(duplicate_pairs)} document pairs")
        return merged_content

    def run_full_analysis(self):
        """Run full analysis"""
        self.logger.info("=" * 60)
        self.logger.info("Document Analysis Tool - docAnalyze.py")
        self.logger.info("=" * 60)

        self.load_source_documents()
        self.load_conversation_logs()
        self.load_feedback_history()
        
        self.analyze_document_coverage()
        self.analyze_query_effectiveness()
        self.identify_document_issues()
        self.analyze_feedback_coverage()
        self.analyze_document_relations()
        self.generate_optimized_document()

        self.logger.info("=" * 60)
        self.logger.info("Analysis completed!")
        self.logger.info("=" * 60)


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Document Analysis Tool')
    parser.add_argument('--kb-id', dest='kb_id', default=None, help='Knowledge Base ID (optional)')
    parser.add_argument('--output', dest='output', default=None, help='Output directory')

    args = parser.parse_args()

    analyzer = DocumentAnalyzer(kb_id=args.kb_id)
    analyzer.run_full_analysis()


if __name__ == "__main__":
    main()