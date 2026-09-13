"""
RAG Evaluator Module
Used to compute evaluation metrics such as retrieval quality and answer faithfulness
Supports semantic-level evaluation (Embedding-based similarity computation)
"""
from typing import Dict, List, Optional, Any

import numpy as np

from config import config, scenario_config
from core.llm_adapter import LLMAdapterFactory
from service.i18n import _
from service.logger import get_logger

logger = get_logger('evaluator')


class RAGEvaluator:
    """RAG evaluator"""

    def __init__(self):
        self._metrics = {
            'retrieval_recall': self._compute_retrieval_score_avg,
            'retrieval_score_std': self._compute_retrieval_score_std,
            'answer_faithfulness': self._compute_semantic_faithfulness,
            'answer_relevance': self._compute_semantic_relevance,
            'context_usage_ratio': self._compute_context_usage_ratio,
            'empty_response': self._compute_empty_response,
            'response_length': self._compute_response_length,
            'semantic_consistency': self._compute_semantic_consistency,
            'hallucination_rate': self._compute_hallucination_rate,
            'rejection_accuracy': self._compute_rejection_accuracy,
        }

        self._embedding_model = config.get_embedding_model()

        self._llm_adapter = LLMAdapterFactory.get_adapter()

    def evaluate(
        self,
        query: str,
        context_results: List[Dict],
        answer: str,
        scenario_id: Optional[str] = None,
        boundary_result: Optional[Dict] = None,
        lang: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute evaluation

        Args:
            query: User query
            context_results: Retrieval context result list
            answer: Answer text
            scenario_id: Scenario ID (used to get target metric values)
            boundary_result: Boundary detection result (from boundary_detector.detect())

        Returns:
            Evaluation result dictionary
        """
        results = {
            'query_length': len(query),
            'context_count': len(context_results),
            'answer_length': len(answer),
            'scenario_id': scenario_id,
            'metrics': {},
            'overall_score': None,
            'is_passing': None,
            'boundary_quality': None,  # New: boundary quality adjustment flag
        }

        if not context_results:
            results['metrics']['empty_response'] = {'value': True, 'target': False, 'is_pass': False}
            results['overall_score'] = 0.0
            results['is_passing'] = False
            return results

        scenario_targets = self._get_scenario_targets(scenario_id)

        for metric_name, compute_func in self._metrics.items():
            try:
                # rejection_accuracy directly reuses externally passed boundary detection result, avoiding redundant LLM calls
                if metric_name == 'rejection_accuracy':
                    value = compute_func(query, context_results, answer, boundary_result=boundary_result)
                else:
                    value = compute_func(query, context_results, answer)
                target = scenario_targets.get(metric_name)

                is_pass = self._check_pass(metric_name, value, target)

                results['metrics'][metric_name] = {
                    'value': round(value, 4) if isinstance(value, float) else value,
                    'target': target,
                    'is_pass': is_pass,
                }
            except Exception as e:
                logger.error(f"Compute metric {metric_name} failed: {e}", exc_info=True)
                results['metrics'][metric_name] = {
                    'value': None,
                    'target': scenario_targets.get(metric_name),
                    'is_pass': None,
                    'error': str(e),
                }

        # Calculate initial overall score
        raw_score = self._compute_overall_score(results['metrics'])

        # ===== Suggestion 1: introduce retrieval quality threshold =====
        retrieval_recall = results['metrics'].get('retrieval_recall', {}).get('value', 0)
        if retrieval_recall is not None and retrieval_recall < 0.15:
            # Retrieval quality extremely poor (recall score < 0.15), cap overall score to avoid "garbage in garbage out" false high score
            capped_score = min(raw_score, 0.30)
            logger.warning(f"[Evaluation] retrieval quality low (recall={retrieval_recall:.4f}) from score {raw_score:.2f} to {capped_score:.2f}")
            raw_score = capped_score
            results['boundary_quality'] = _('eval.retrieval_quality_low', lang)

        # ===== Suggestion 2: integrate boundary detection signal =====
        if boundary_result and boundary_result.get('confidence', 1.0) < 0.5:
            # Boundary detection confidence low (system unsure if question is in-domain), indicates retrieval and answer reliability is questionable
            confidence = boundary_result.get('confidence', 0.5)
            # When confidence is low, proportionally reduce overall score (multiply by confidence factor, minimum retain 30%)
            adjustment_factor = max(0.3, confidence)
            adjusted_score = raw_score * adjustment_factor
            logger.warning(f"[Evaluation] boundary detection confidence low ({confidence:.2f}) from score {raw_score:.2f} to {adjusted_score:.2f}")
            raw_score = adjusted_score
            results['boundary_quality'] = _('eval.boundary_low_confidence', lang, f"{confidence:.0%}")

        results['overall_score'] = round(raw_score, 4)
        results['is_passing'] = self._check_overall_passing(results['metrics'])

        return results

    def _get_scenario_targets(self, scenario_id: Optional[str]) -> Dict[str, Any]:
        """Get scenario target metric values, provide reasonable defaults"""
        defaults = {
            'retrieval_recall': 0.3,
            'retrieval_score_std': 0.3,
            'answer_faithfulness': 0.5,
            'answer_relevance': 0.5,
            'context_usage_ratio': 0.3,
            'empty_response': False,
            'response_length': {'min': 50, 'max': 2000},
            'semantic_consistency': 0.5,
            'hallucination_rate': 0.3,
            'rejection_accuracy': 0.8,
        }

        if not scenario_id:
            return defaults

        effective_config = scenario_config.get_effective_config(scenario_id)
        evaluation = effective_config.get('evaluation', {})
        targets = evaluation.get('target_values', {})
        
        return {**defaults, **targets}

    def _check_pass(self, metric_name: str, value: Any, target: Any) -> Optional[bool]:
        """Check if metric meets target"""
        if target is None:
            return None

        if metric_name == 'response_length' and isinstance(target, dict):
            min_len = target.get('min', 0)
            max_len = target.get('max', float('inf'))
            return min_len <= value <= max_len

        comparison_map = {
            'retrieval_recall': lambda v, t: v >= t,
            'retrieval_score_std': lambda v, t: v <= t,
            'answer_faithfulness': lambda v, t: v >= t,
            'answer_relevance': lambda v, t: v >= t,
            'context_usage_ratio': lambda v, t: v >= t,
            'empty_response': lambda v, t: v == t,
            'response_length': lambda v, t: v >= t,
            'semantic_consistency': lambda v, t: v >= t,
            'hallucination_rate': lambda v, t: v <= t,
            'rejection_accuracy': lambda v, t: v >= t,
        }

        comparator = comparison_map.get(metric_name)
        if comparator:
            return comparator(value, target)
        return None

    def _check_overall_passing(self, metrics: Dict[str, Dict]) -> bool:
        """Check overall pass"""
        pass_count = 0
        total_count = 0
        failing_metrics = []

        for metric_name, metric in metrics.items():
            if metric['is_pass'] is not None:
                total_count += 1
                if metric['is_pass']:
                    pass_count += 1
                else:
                    failing_metrics.append(metric_name)

        if total_count == 0:
            return False

        if 'empty_response' in metrics and metrics['empty_response']['is_pass'] is False:
            return False

        pass_rate = pass_count / total_count
        return pass_rate >= 0.7

    def _compute_overall_score(self, metrics: Dict[str, Dict]) -> float:
        """Compute overall score"""
        scores = []

        for metric_name, metric in metrics.items():
            value = metric['value']
            target = metric['target']

            if value is None or target is None:
                continue

            if metric_name == 'empty_response':
                score = 0.0 if value else 1.0
            elif metric_name == 'retrieval_score_std':
                score = max(0, 1 - value / target)
            elif metric_name == 'hallucination_rate':
                # Negative metric: lower hallucination rate = higher score (0 hallucination = full score, reaching target = 0 score)
                score = max(0, 1 - value / target) if target else 1.0
            elif metric_name == 'response_length' and isinstance(target, dict):
                min_len = target.get('min', 0)
                max_len = target.get('max', float('inf'))
                if min_len <= value <= max_len:
                    score = 1.0
                else:
                    score = max(0, 1 - abs(value - (min_len + max_len) / 2) / (max_len - min_len))
            elif metric_name == 'response_length':
                score = min(1.0, value / target)
            else:
                score = min(1.0, value / target)

            scores.append(score)

        if not scores:
            return 0.0

        return sum(scores) / len(scores)

    def _compute_retrieval_score_avg(self, query: str, context_results: List[Dict], answer: str) -> float:
        """Compute retrieval score average"""
        scores = [r.get('score', 0) for r in context_results if r.get('score') is not None]
        if not scores:
            return 0.0
        return sum(scores) / len(scores)

    def _compute_retrieval_score_std(self, query: str, context_results: List[Dict], answer: str) -> float:
        """Compute retrieval score standard deviation"""
        scores = [r.get('score', 0) for r in context_results if r.get('score') is not None]
        if len(scores) < 2:
            return 0.0

        avg = sum(scores) / len(scores)
        variance = sum((s - avg) ** 2 for s in scores) / len(scores)
        return variance ** 0.5

    def _compute_answer_coverage_ratio(self, query: str, context_results: List[Dict], answer: str) -> float:
        """Compute answer coverage ratio (proportion of answer words found in context)"""
        if not answer or not context_results:
            return 0.0

        context_text = " ".join(r.get('text', '') for r in context_results)
        answer_tokens = set(self._tokenize(answer))
        context_tokens = set(self._tokenize(context_text))

        if not answer_tokens:
            return 0.0

        overlap = len(answer_tokens & context_tokens) / len(answer_tokens)
        return overlap

    def _compute_hallucination_rate(self, query: str, context_results: List[Dict], answer: str) -> float:
        if not answer or not context_results:
            return 0.0

        context_text = " ".join(r.get('text', '') for r in context_results)
        context_embedding = self._get_embedding(context_text)
        if not context_embedding:
            return 0.0

        sentences = self._split_sentences(answer)
        if not sentences:
            return 0.0

        drift_count = 0
        for sentence in sentences:
            sentence_embedding = self._get_embedding(sentence)
            if sentence_embedding:
                sim = self._cosine_similarity(sentence_embedding, context_embedding)
                if sim < 0.3:
                    drift_count += 1

        return drift_count / len(sentences)

    def _compute_rejection_accuracy(self, query: str, context_results: List[Dict], answer: str, boundary_result: Optional[Dict] = None) -> float:
        """
        Compute rejection accuracy.

        Directly reuses externally passed boundary_result (from boundary_detector.detect()),
        to avoid redundant LLM calls here causing inconsistent evaluation results and performance waste.
        If boundary_result is not passed, treat as in-domain question (in_domain=True).
        """
        if not answer:
            return 0.0

        if boundary_result is None:
            boundary_result = {}
        in_domain = boundary_result.get('in_domain', True)

        if not in_domain:
            rejection_keywords = ['business scope', 'cannot answer', 'beyond scope', 'out of scope', 'cannot answer', 'beyond scope']
            correctly_rejected = any(kw in answer.lower() for kw in rejection_keywords)
            return 1.0 if correctly_rejected else 0.0

        if context_results:
            return self._compute_semantic_faithfulness(query, context_results, answer)
        else:
            return 0.5

    def _compute_context_usage_ratio(self, query: str, context_results: List[Dict], answer: str) -> float:
        """Compute context usage ratio (proportion of context used by answer)"""
        if not answer or not context_results:
            return 0.0

        context_text = " ".join(r.get('text', '') for r in context_results)
        answer_tokens = set(self._tokenize(answer))
        context_tokens = set(self._tokenize(context_text))

        if not context_tokens:
            return 0.0

        overlap = len(answer_tokens & context_tokens) / len(context_tokens)
        return overlap

    def _compute_empty_response(self, query: str, context_results: List[Dict], answer: str) -> bool:
        """Check if it is an empty response"""
        if not answer or not answer.strip():
            return True
        empty_phrases = [
            "Sorry, no relevant information found",
            "No relevant information found",
            "Cannot answer",
            "Don't know",
            "Not found in knowledge base",
            "Please try a different question",
            "Please upload relevant documents",
            "Cannot analyze document content in detail",
            "Sorry, no relevant information found",
            "no relevant information found",
            "cannot answer",
            "don't know",
            "not found in knowledge base",
            "please try a different question",
            "please upload relevant documents",
        ]
        return any(phrase in answer.lower() for phrase in empty_phrases)

    def _compute_response_length(self, query: str, context_results: List[Dict], answer: str) -> int:
        """Compute response length (character count)"""
        return len(answer) if answer else 0

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize (lazy import jieba)"""
        try:
            import jieba
            return [token for token in jieba.cut(text) if token.strip() and len(token.strip()) > 1]
        except ImportError:
            return [word for word in text.split() if len(word) > 1]

    def _get_embedding(self, text: str) -> List[float]:
        """Get Embedding vector for text"""
        try:
            response = self._llm_adapter.embeddings(model=self._embedding_model, prompt=text)
            return response
        except Exception as e:
            logger.error(f"[Evaluation] get embedding failed for text: {e}")
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

    def _compute_semantic_faithfulness(self, query: str, context_results: List[Dict], answer: str) -> float:
        """
        Compute semantic-level answer faithfulness
        Evaluate answer faithfulness by computing semantic similarity between answer and context
        """
        if not answer or not context_results:
            return 0.0

        answer_embedding = self._get_embedding(answer)
        if not answer_embedding:
            return self._compute_answer_coverage_ratio(query, context_results, answer)

        context_text = " ".join(r.get('text', '') for r in context_results)
        context_embedding = self._get_embedding(context_text)
        if not context_embedding:
            return self._compute_answer_coverage_ratio(query, context_results, answer)

        similarity = self._cosine_similarity(answer_embedding, context_embedding)
        return similarity

    def _compute_semantic_relevance(self, query: str, context_results: List[Dict], answer: str) -> float:
        """
        Compute semantic-level answer relevance
        Evaluate answer relevance by computing semantic similarity between answer and query
        """
        if not query or not answer:
            return 0.0

        query_embedding = self._get_embedding(query)
        answer_embedding = self._get_embedding(answer)

        if not query_embedding or not answer_embedding:
            # Fallback to word overlap ratio
            query_tokens = set(self._tokenize(query))
            answer_tokens = set(self._tokenize(answer))
            if not query_tokens:
                return 0.0
            return len(query_tokens & answer_tokens) / len(query_tokens)

        similarity = self._cosine_similarity(query_embedding, answer_embedding)
        return similarity

    def _compute_semantic_consistency(self, query: str, context_results: List[Dict], answer: str) -> float:
        """
        Compute semantic consistency
        Check whether each sentence in the answer maintains semantic consistency with context
        """
        if not answer or not context_results:
            return 0.0

        context_text = " ".join(r.get('text', '') for r in context_results)
        context_embedding = self._get_embedding(context_text)
        if not context_embedding:
            return 0.0

        sentences = self._split_sentences(answer)
        if not sentences:
            return 0.0

        similarities = []
        for sentence in sentences:
            sentence_embedding = self._get_embedding(sentence)
            if sentence_embedding:
                sim = self._cosine_similarity(sentence_embedding, context_embedding)
                similarities.append(sim)

        if not similarities:
            return 0.0

        return sum(similarities) / len(similarities)

    def _split_sentences(self, text: str) -> List[str]:
        """Split text by sentence boundary"""
        import re
        sentences = re.split(r'[。！？.!?\n]', text)
        return [s.strip() for s in sentences if s.strip()]
