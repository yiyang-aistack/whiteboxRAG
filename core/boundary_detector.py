"""
Business scope boundary detection module
Used to detect whether user queries are within business scope (OOD detection)
Supports keyword whitelist, semantic boundary detection and retrieval result judgment
"""
from typing import Dict, List, Optional

from config import config
from core.llm_adapter import LLMAdapterFactory
from service.i18n import _
from service.logger import get_logger

logger = get_logger('boundary_detector')


class BoundaryDetector:
    """Business scope boundary detector"""

    def __init__(self):
        self.enabled = config.get('boundary.enabled', True)
        self.confidence_threshold = config.get('boundary.confidence_threshold', 0.5)
        self.semantic_threshold = config.get('boundary.semantic_threshold', 0.3)

        self._whitelist_keywords = config.get('boundary.whitelist_keywords', [])
        self._blacklist_keywords = config.get('boundary.blacklist_keywords', [])
        self._business_topics = config.get('boundary.business_topics', [])

        self.llm_model = config.get_llm_model()

        self._llm_adapter = LLMAdapterFactory.get_adapter()

    def _keyword_based_check(self, query: str) -> Dict:
        """Keyword-based boundary detection"""
        blacklist_match = any(keyword in query for keyword in self._blacklist_keywords)
        whitelist_match = any(keyword in query for keyword in self._whitelist_keywords)

        if blacklist_match:
            return {
                'in_domain': False,
                'confidence': 0.0,
                'reason': f'Hit blacklist keywords: {self._blacklist_keywords}',
                'detector': 'keyword_blacklist'
            }

        if whitelist_match:
            return {
                'in_domain': True,
                'confidence': 0.8,
                'reason': f'Hit whitelist keywords: {self._whitelist_keywords}',
                'detector': 'keyword_whitelist'
            }

        return None

    def _semantic_based_check(self, query: str) -> Dict:
        """Semantic-based boundary detection"""
        if not self._business_topics:
            return None

        try:
            topics_str = '\n'.join([f"- {topic}" for topic in self._business_topics])

            prompt = f"""You are an out-of-domain (OOD) detector for a business knowledge assistant.

Business scope (only these topics are allowed):
{topics_str}

User query: {query}

Requirements:
1. is_in_domain: whether the user query falls strictly within the business scope above
2. confidence: confidence score (range 0-1)
3. reason: reason for the judgment (up to 50 characters, concise)
4. matched_topics: the business topics from the list above that match the query (empty list if none)

Reply with a single valid JSON object only, no other text, for example:
{{
  "is_in_domain": true,
  "confidence": 0.9,
  "reason": "Query asks about order status",
  "matched_topics": ["Order Management and Payment Settlement"]
}}
"""

            response = self._llm_adapter.chat(
                model=self.llm_model,
                messages=[{'role': 'user', 'content': prompt}],
                stream=False
            )

            import json
            content = response.get('message', {}).get('content', '')
            json_start = content.find('{')
            json_end = content.rfind('}') + 1

            if json_start >= 0 and json_end > json_start:
                result = json.loads(content[json_start:json_end])
                return {
                    'in_domain': result.get('is_in_domain', False),
                    'confidence': result.get('confidence', 0.0),
                    'reason': result.get('reason', ''),
                    'detector': 'semantic',
                    'matched_topics': result.get('matched_topics', [])
                }
        except Exception as e:
            logger.error(f"Semantic boundary detection failed: {e}")

        return None

    def _retrieval_based_check(self, retrieval_results: List[Dict]) -> Dict:
        """Retrieval result-based boundary detection"""
        if not retrieval_results:
            return {
                'in_domain': False,
                'confidence': 0.0,
                'reason': 'No relevant documents found',
                'detector': 'retrieval_empty'
            }

        avg_score = sum(r.get('score', 0.0) for r in retrieval_results) / len(retrieval_results)

        if avg_score < self.semantic_threshold:
            return {
                'in_domain': False,
                'confidence': avg_score,
                'reason': f'Retrieval results average similarity is low ({avg_score:.2f})',
                'detector': 'retrieval_low_score'
            }

        return None

    def detect(self, query: str, retrieval_results: Optional[List[Dict]] = None, lang: Optional[str] = None) -> Dict:
        """
        Business scope boundary detection main interface

        Args:
            query: User query
            retrieval_results: Retrieval results (optional)

        Returns:
            Boundary detection result:
            {
                'in_domain': True/False,
                'confidence': 0.0-1.0,
                'reason': 'Judgment reason',
                'detector': 'keyword_blacklist/keyword_whitelist/semantic/retrieval_empty/retrieval_low_score/voting',
                'matched_topics': ['Matched topics'],
                'suggestion': 'Suggested reply'
            }
        """
        if not self.enabled:
            return {
                'in_domain': True,
                'confidence': 1.0,
                'reason': 'Boundary detection disabled',
                'detector': 'disabled',
                'matched_topics': [],
                'suggestion': ''
            }

        results = []

        keyword_result = self._keyword_based_check(query)
        if keyword_result:
            # If keyword detection already made a clear judgment (hit blacklist or whitelist), return directly, skip time-consuming semantic detection
            if keyword_result.get('detector') in ('keyword_blacklist', 'keyword_whitelist'):
                keyword_result['matched_topics'] = keyword_result.get('matched_topics', [])
                keyword_result['suggestion'] = self._generate_suggestion(keyword_result, lang=lang)
                return keyword_result
            results.append(keyword_result)

        semantic_result = self._semantic_based_check(query)
        if semantic_result:
            results.append(semantic_result)

        if retrieval_results is not None:
            retrieval_result = self._retrieval_based_check(retrieval_results)
            if retrieval_result:
                results.append(retrieval_result)

        if not results:
            return {
                'in_domain': True,
                'confidence': 0.7,
                'reason': 'No boundary rules hit, default to in domain',
                'detector': 'default',
                'matched_topics': [],
                'suggestion': ''
            }

        if len(results) == 1:
            result = results[0]
            result['matched_topics'] = result.get('matched_topics', [])
            result['suggestion'] = self._generate_suggestion(result, lang=lang)
            return result

        in_domain_count = sum(1 for r in results if r['in_domain'])
        total = len(results)
        avg_confidence = sum(r['confidence'] for r in results) / total

        in_domain = in_domain_count >= total / 2

        reasons = [r['reason'] for r in results]

        return {
            'in_domain': in_domain,
            'confidence': avg_confidence,
            'reason': '; '.join(reasons),
            'detector': 'voting',
            'matched_topics': semantic_result.get('matched_topics', []) if semantic_result else [],
            'suggestion': self._generate_suggestion({'in_domain': in_domain, 'reason': reasons[0]}, lang=lang)
        }

    def _generate_suggestion(self, result: Dict, lang: Optional[str] = None) -> str:
        """Generate boundary detection suggestion reply"""
        if result['in_domain']:
            return ''

        default_rejection = config.get(
            'boundary.rejection_message',
            _('boundary.rejection_default', lang)
        )

        detector = result.get('detector', '')
        reason = result.get('reason', '')

        if 'blacklist' in detector:
            return default_rejection

        if 'retrieval_empty' in detector:
            return config.get(
                'retriever.empty_response',
                _('boundary.retrieval_empty', lang)
            )

        if 'retrieval_low_score' in detector:
            return default_rejection

        return default_rejection


boundary_detector = BoundaryDetector()