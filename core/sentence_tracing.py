"""
Sentence-level tracing module
Splits answer by sentences, calculates semantic similarity with each source chunk, annotates confidence level
"""
import re
from typing import Dict, List, Optional

import numpy as np

from config import config
from core.llm_adapter import LLMAdapterFactory
from service.i18n import _
from service.logger import get_logger

logger = get_logger('sentence_tracing')


class SentenceTracer:
    """Sentence-level tracer"""

    def __init__(self):
        self.embedding_model = config.get_embedding_model()

        self._llm_adapter = LLMAdapterFactory.get_adapter()
        self._drift_threshold = config.get('sentence_tracing.drift_threshold', 0.5)
        self._direct_quote_threshold = config.get('sentence_tracing.direct_quote_threshold', 0.85)
        self._summary_threshold = config.get('sentence_tracing.summary_threshold', 0.7)

    def _get_embedding(self, text: str) -> List[float]:
        """Get text embedding vector"""
        try:
            response = self._llm_adapter.embeddings(model=self.embedding_model, prompt=text)
            return response
        except Exception as e:
            logger.error(f"Failed to get embedding: {e}")
            return []

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity"""
        if not vec1 or not vec2:
            return 0.0
        try:
            v1 = np.array(vec1)
            v2 = np.array(vec2)
            dot = np.dot(v1, v2)
            norm = np.linalg.norm(v1) * np.linalg.norm(v2)
            if norm == 0:
                return 0.0
            return float(dot / norm)
        except Exception:
            return 0.0

    def _split_sentences(self, text: str) -> List[str]:
        """Split text by sentence boundaries"""
        sentences = re.split(r'[。！？.!?\n]', text)
        return [s.strip() for s in sentences if s.strip()]

    def _extract_citations(self, text: str) -> List[str]:
        """Extract citation markers from text (e.g. [1], [chunk_3])"""
        citations = re.findall(r'\[(chunk_\d+|\d+)\]', text)
        return list(set(citations))

    def trace(self, answer: str, context_results: List[Dict], lang: Optional[str] = None) -> List[Dict]:
        """
        Perform sentence-level tracing

        Args:
            answer: LLM-generated answer
            context_results: Retrieved context results

        Returns:
            Sentence-level tracing list:
            [
                {
                    'sentence': 'A sentence in the answer',
                    'sources': [
                        {
                            'chunk_id': 'chunk_1',
                            'score': 0.85,
                            'confidence_level': 'direct_quote',
                            'text_preview': 'Related content in source chunk'
                        }
                    ],
                    'confidence_level': 'direct_quote',
                    'is_drift': False,
                    'drift_reason': ''
                }
            ]
            confidence_level: direct_quote(green), summary(yellow), no_source(red), drift(red)
        """
        if not answer or not context_results:
            return []

        sentences = self._split_sentences(answer)
        if not sentences:
            return []

        citations = self._extract_citations(answer)
        logger.debug(f"Extract citations from answer: {citations}")

        chunk_embeddings = {}
        for chunk in context_results:
            chunk_id = chunk.get('id', str(chunk.get('metadata', {}).get('chunk_index', 0)))
            chunk_text = chunk.get('text', '')
            embedding = self._get_embedding(chunk_text)
            if embedding:
                chunk_embeddings[chunk_id] = {
                    'embedding': embedding,
                    'text': chunk_text,
                    'metadata': chunk.get('metadata', {})
                }

        tracing_results = []

        for sentence in sentences:
            sentence_embedding = self._get_embedding(sentence)
            if not sentence_embedding:
                tracing_results.append({
                    'sentence': sentence,
                    'sources': [],
                    'confidence_level': 'no_source',
                    'is_drift': True,
                    'drift_reason': _('trace.drift_embedding_failed', lang),
                    'citations': []
                })
                continue

            similarities = []
            for chunk_id, data in chunk_embeddings.items():
                sim = self._cosine_similarity(sentence_embedding, data['embedding'])
                similarities.append({
                    'chunk_id': chunk_id,
                    'score': sim,
                    'text_preview': data['text'][:100],
                    'metadata': data['metadata']
                })

            similarities.sort(key=lambda x: x['score'], reverse=True)
            top_similarities = similarities[:3]

            if not top_similarities or top_similarities[0]['score'] < self._drift_threshold:
                tracing_results.append({
                    'sentence': sentence,
                    'sources': top_similarities,
                    'confidence_level': 'drift' if top_similarities else 'no_source',
                    'is_drift': True,
                    'drift_reason': _('trace.drift_low_similarity', lang, f"{top_similarities[0]['score']:.2f}") if top_similarities else _('trace.drift_no_source', lang),
                    'citations': [c for c in citations if c in [s['chunk_id'] for s in top_similarities]]
                })
            elif top_similarities[0]['score'] >= self._direct_quote_threshold:
                tracing_results.append({
                    'sentence': sentence,
                    'sources': top_similarities,
                    'confidence_level': 'direct_quote',
                    'is_drift': False,
                    'drift_reason': '',
                    'citations': [c for c in citations if c in [s['chunk_id'] for s in top_similarities]]
                })
            elif top_similarities[0]['score'] >= self._summary_threshold:
                tracing_results.append({
                    'sentence': sentence,
                    'sources': top_similarities,
                    'confidence_level': 'summary',
                    'is_drift': False,
                    'drift_reason': '',
                    'citations': [c for c in citations if c in [s['chunk_id'] for s in top_similarities]]
                })
            else:
                tracing_results.append({
                    'sentence': sentence,
                    'sources': top_similarities,
                    'confidence_level': 'low_confidence',
                    'is_drift': False,
                    'drift_reason': _('trace.drift_low_similarity', lang, f"{top_similarities[0]['score']:.2f}"),
                    'citations': [c for c in citations if c in [s['chunk_id'] for s in top_similarities]]
                })

        logger.debug(f"Sentences tracing completed, total {len(tracing_results)} sentences, drift {sum(1 for r in tracing_results if r['is_drift'])} sentences")
        return tracing_results

    def get_confidence_color(self, level: str) -> str:
        """Get confidence color marker"""
        colors = {
            'direct_quote': '#22c55e',
            'summary': '#eab308',
            'low_confidence': '#f97316',
            'drift': '#ef4444',
            'no_source': '#ef4444'
        }
        return colors.get(level, '#6b7280')

    def get_confidence_label(self, level: str, lang: Optional[str] = None) -> str:
        """Get confidence label"""
        labels = {
            'direct_quote': _('trace.citation_direct_quote', lang),
            'summary': _('trace.citation_summary', lang),
            'low_confidence': _('trace.citation_low_confidence', lang),
            'drift': _('trace.citation_drift', lang),
            'no_source': _('trace.citation_no_source', lang)
        }
        return labels.get(level, _('trace.citation_unknown', lang))

    def analyze_drift(self, tracing_results: List[Dict], lang: Optional[str] = None) -> Dict:
        """Analyze semantic drift situation"""
        total_sentences = len(tracing_results)
        drift_count = sum(1 for r in tracing_results if r['is_drift'])
        direct_quote_count = sum(1 for r in tracing_results if r['confidence_level'] == 'direct_quote')
        summary_count = sum(1 for r in tracing_results if r['confidence_level'] == 'summary')

        return {
            'total_sentences': total_sentences,
            'drift_count': drift_count,
            'direct_quote_count': direct_quote_count,
            'summary_count': summary_count,
            'drift_rate': drift_count / total_sentences if total_sentences > 0 else 0,
            'drift_sentences': [r for r in tracing_results if r['is_drift']],
            'high_confidence_rate': (direct_quote_count + summary_count) / total_sentences if total_sentences > 0 else 0
        }

    def detect_contradictions(self, answer: str, context_results: List[Dict], lang: Optional[str] = None) -> List[Dict]:
        """
        Detect semantic contradictions between answer and source documents

        Args:
            answer: LLM-generated answer
            context_results: Retrieved context results

        Returns:
            Contradiction detection result list:
            [
                {
                    'sentence': 'Contradictory sentence in answer',
                    'type': 'number_percentage' | 'time_duration' | 'amount_money' | 'status' | 'other',
                    'severity': 'high' | 'medium' | 'low',
                    'source_text': 'Corresponding content in source document',
                    'source_value': 'Value in source document',
                    'answer_value': 'Value in answer',
                    'explanation': 'Contradiction explanation'
                }
            ]
        """
        logger.info(f"Start semantic contradiction detection, answer length: {len(answer)}, context count: {len(context_results)}")
        
        if not answer or not context_results:
            logger.info(f"Skip detection")
            return []

        contradictions = []
        source_text = " ".join([r.get('text', '') for r in context_results])
        logger.info(f"Source document total length: {len(source_text)} characters")

        sentences = self._split_sentences(answer)
        logger.info(f"Answer split into sentences {len(sentences)} sentences")

        for i, sentence in enumerate(sentences):
            sentence_contradictions = self._detect_sentence_contradictions(sentence, source_text)
            for contradiction in sentence_contradictions:
                contradiction['sentence'] = sentence
                contradictions.append(contradiction)
                logger.info(f"Detect contradiction, type: {contradiction['type']}, severity: {contradiction['severity']}, source value: {contradiction['source_value']}, answer value: {contradiction['answer_value']}")

        logger.info(f"[语义检测] 语义矛盾检测完成，共发现 {len(contradictions)} 个矛盾（high: {len([c for c in contradictions if c['severity'] == 'high'])}, medium: {len([c for c in contradictions if c['severity'] == 'medium'])}, low: {len([c for c in contradictions if c['severity'] == 'low'])}）")
        return contradictions

    def _detect_sentence_contradictions(self, sentence: str, source_text: str) -> List[Dict]:
        """Detect contradictions in a single sentence"""
        contradictions = []

        number_contradictions = self._detect_number_contradictions(sentence, source_text)
        contradictions.extend(number_contradictions)

        time_contradictions = self._detect_time_contradictions(sentence, source_text)
        contradictions.extend(time_contradictions)

        money_contradictions = self._detect_money_contradictions(sentence, source_text)
        contradictions.extend(money_contradictions)

        status_contradictions = self._detect_status_contradictions(sentence, source_text)
        contradictions.extend(status_contradictions)

        return contradictions

    def _detect_number_contradictions(self, sentence: str, source_text: str) -> List[Dict]:
        """Detect number and percentage contradictions"""
        contradictions = []

        answer_percentages = re.findall(r'(\d+(?:\.\d+)?)%\s*(?:退款|折扣|赔偿|比例|退|返还)', sentence)
        source_percentages = re.findall(r'(\d+(?:\.\d+)?)%\s*(?:退款|折扣|赔偿|比例|退|返还)', source_text)

        if answer_percentages and source_percentages:
            answer_pct = float(answer_percentages[0])
            source_pct = float(source_percentages[0])
            
            if abs(answer_pct - source_pct) > 10:
                contradictions.append({
                    'type': 'number_percentage',
                    'severity': 'high',
                    'source_text': f"{source_pct}%",
                    'source_value': source_pct,
                    'answer_value': answer_pct,
                    'explanation': f"Answer percentage is {answer_pct}% but source document shows {source_pct}% by 10%"
                })

        answer_numbers = re.findall(r'(\d+(?:\.\d+)?)\s*(?:天|个|次|件|人|页|条)', sentence)
        source_numbers = re.findall(r'(\d+(?:\.\d+)?)\s*(?:天|个|次|件|人|页|条)', source_text)

        if answer_numbers and source_numbers:
            for answer_num in answer_numbers:
                for source_num in source_numbers:
                    try:
                        a_num = float(answer_num)
                        s_num = float(source_num)
                        if a_num > 0 and s_num > 0:
                            ratio = max(a_num, s_num) / min(a_num, s_num)
                            if ratio >= 2:
                                contradictions.append({
                                    'type': 'number_percentage',
                                    'severity': 'medium',
                                    'source_text': f"{source_num}",
                                    'source_value': s_num,
                                    'answer_value': a_num,
                                    'explanation': f"Answer number {answer_num} and source document number {source_num} differ by more than 100 times"
                                })
                    except ValueError:
                        continue

        return contradictions

    def _detect_time_contradictions(self, sentence: str, source_text: str) -> List[Dict]:
        """Detect time and duration contradictions"""
        contradictions = []

        time_patterns = [
            (r'(\d+)\s*(?:天|小时|小时内|工作日)', 'duration'),
            (r'(\d+)年(\d+)月', 'date'),
            (r'(\d+)月(\d+)日', 'date'),
        ]

        for pattern, _ in time_patterns:
            answer_matches = re.findall(pattern, sentence)
            source_matches = re.findall(pattern, source_text)

            if answer_matches and source_matches:
                for answer_match in answer_matches:
                    for source_match in source_matches:
                        try:
                            if isinstance(answer_match, tuple):
                                answer_value = int(answer_match[0]) * 30 + int(answer_match[1]) if len(answer_match) == 2 else int(answer_match[0])
                                source_value = int(source_match[0]) * 30 + int(source_match[1]) if len(source_match) == 2 else int(source_match[0])
                            else:
                                answer_value = int(answer_match)
                                source_value = int(source_match)

                            if source_value > 0:
                                ratio = max(answer_value, source_value) / min(answer_value, source_value)
                                if ratio >= 2:
                                    contradictions.append({
                                        'type': 'time_duration',
                                        'severity': 'high' if ratio >= 3 else 'medium',
                                        'source_text': str(source_match),
                                        'source_value': source_value,
                                        'answer_value': answer_value,
                                        'explanation': f"Answer time {answer_match} and source document time {source_match} differ by more than 100 times"
                                    })
                        except (ValueError, IndexError):
                            continue

        return contradictions

    def _detect_money_contradictions(self, sentence: str, source_text: str) -> List[Dict]:
        """Detect amount contradictions"""
        contradictions = []

        money_pattern = r'(\d+(?:\.\d+)?)\s*(?:元|人民币|RMB|美元|USD)'

        answer_money = re.findall(money_pattern, sentence)
        source_money = re.findall(money_pattern, source_text)

        if answer_money and source_money:
            for answer_val in answer_money:
                for source_val in source_money:
                    try:
                        a_money = float(answer_val)
                        s_money = float(source_val)
                        if s_money > 0:
                            ratio = max(a_money, s_money) / min(a_money, s_money)
                            if ratio >= 2:
                                contradictions.append({
                                    'type': 'amount_money',
                                    'severity': 'high' if ratio >= 3 else 'medium',
                                    'source_text': f"{source_val}元",
                                    'source_value': s_money,
                                    'answer_value': a_money,
                                    'explanation': f"答案中的金额{answer_val}元与源文档中的金额{source_val}元相差超过一倍"
                                })
                    except ValueError:
                        continue

        return contradictions

    def _detect_status_contradictions(self, sentence: str, source_text: str) -> List[Dict]:
        """Detect status contradictions"""
        contradictions = []

        status_pairs = [
            ('支持', '不支持'),
            ('可以', '不可以'),
            ('能够', '不能够'),
            ('允许', '不允许'),
            ('需要', '不需要'),
            ('必须', '不必'),
            ('已完成', '未完成'),
            ('已处理', '未处理'),
            ('已发货', '未发货'),
            ('已退款', '未退款'),
            ('全额', '部分'),
            ('免费', '收费'),
            ('无限', '有限'),
        ]

        for positive, negative in status_pairs:
            if positive in sentence and negative in source_text:
                contradictions.append({
                    'type': 'status',
                    'severity': 'high',
                    'source_text': f"{negative}",
                    'source_value': negative,
                    'answer_value': positive,
                    'explanation': f"Answer uses '{positive}' but source document explicitly mentions '{negative}'"
                })
            elif negative in sentence and positive in source_text:
                contradictions.append({
                    'type': 'status',
                    'severity': 'high',
                    'source_text': f"Include'{positive}'",
                    'source_value': positive,
                    'answer_value': negative,
                    'explanation': f"Answer uses '{negative}' but source document explicitly mentions '{positive}'"
                })

        if '全额退款' in sentence and '%' in source_text:
            pct_matches = re.findall(r'(\d+(?:\.\d+)?)%', source_text)
            for pct in pct_matches:
                try:
                    if float(pct) < 100:
                        contradictions.append({
                            'type': 'status',
                            'severity': 'high',
                            'source_text': f"{pct}%",
                            'source_value': float(pct),
                            'answer_value': 100,
                            'explanation': f"答案声称'全额退款'，但源文档显示退款比例为{pct}%，并非全额"
                        })
                except ValueError:
                    continue

        return contradictions


sentence_tracer = SentenceTracer()