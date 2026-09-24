"""
Intent classifier module
Uses unified LLM adapter for lightweight intent classification, outputs structured intent cards
"""
import json
import re
from typing import Dict, List, Optional

from config import config
from core.llm_adapter import LLMAdapterFactory
from service.i18n import _
from service.logger import get_logger

logger = get_logger('intent_classifier')


class IntentClassifier:
    """Business intent recognizer"""

    def __init__(self):
        self.llm_model = config.get_llm_model(purpose='intent')
        self._llm_adapter = LLMAdapterFactory.get_adapter()

        self._intent_templates = {
            'after_sales': ['return', 'refund', 'replace', 'repair', 'warranty', 'after_sales'],
            # 'order': ['order', '下单', 'Purchase', 'Shop', 'Pay', '发货', '收货'],
            # 'account': ['login', '注册', '账号', '密码', '账户', '会员'],
            # 'product': ['product', '功能', '使用', '操作', '设置', '配置'],
            # 'technical': ['技术', '开发', '接口', 'API', '部署', '安装'],
            # 'financial': ['发票', '费用', '价格', '结算', '报销'],
            # '物流': ['物流', '快递', '配送', '运输', '签收'],
            # '咨询': ['咨询', '帮助', '问题', '了解', '查询'],
        }

        self._time_patterns = [
            (r'(\d{4})Year(\d{1,2})M', 'year_month'),
            (r'(\d{4})-(\d{1,2})-(\d{1,2})', 'date'),
            (r'(\d{1,2})M(\d{1,2})D', 'month_day'),
            (r'Current Year|This year', 'current_year'),
            (r'Current Month|This month', 'current_month'),
            (r'Last week|This week|Next week', 'week'),
            (r'Yesterday|Today|Tomorrow', 'day'),
            (r'Last(\d+)D|Next(\d+)D', 'days'),
            (r'Last(\d+)M|Next(\d+)M', 'months'),
        ]

        self._region_patterns = [
            (r'(北京|上海|广州|深圳|杭州|成都|南京|武汉|西安|重庆)', 'city'),
            (r'(华东|华南|华北|华中|西南|西北|东北)', 'region'),
            (r'(国内|国际|海外)', 'scope'),
        ]

    def _get_llm_adapter(self):
        """Get LLM adapter"""
        return self._llm_adapter

    def _extract_time_constraints(self, query: str) -> List[Dict]:
        """Extract time constraints"""
        constraints = []
        for pattern, constraint_type in self._time_patterns:
            matches = re.findall(pattern, query)
            for match in matches:
                if isinstance(match, tuple):
                    value = ''.join(match)
                else:
                    value = match
                constraints.append({
                    'type': 'time',
                    'subtype': constraint_type,
                    'value': value
                })
        return constraints

    def _extract_region_constraints(self, query: str) -> List[Dict]:
        """Extract region constraints"""
        constraints = []
        for pattern, constraint_type in self._region_patterns:
            matches = re.findall(pattern, query)
            for match in matches:
                constraints.append({
                    'type': 'region',
                    'subtype': constraint_type,
                    'value': match
                })
        return constraints

    def _keyword_based_classify(self, query: str, lang: Optional[str] = None) -> Dict:
        """Keyword-based fast intent classification"""
        matched_intents = []
        for intent_type, keywords in self._intent_templates.items():
            for keyword in keywords:
                if keyword in query:
                    matched_intents.append({
                        'type': intent_type,
                        'keyword': keyword,
                        'confidence': 0.7
                    })
                    break

        if matched_intents:
            return matched_intents[0]
        return {'type': _('intent.other', lang), 'keyword': None, 'confidence': 0.3}

    def classify(self, query: str, kb_summary: Optional[str] = None, lang: Optional[str] = None) -> Dict:
        """
        Intent classification main interface

        Args:
            query: User query
            kb_summary: Knowledge base summary (optional)

        Returns:
            Structured intent card:
            {
                'intent_type': 'after_sales-return_process',
                'confidence': 0.85,
                'keywords': ['return', 'refund'],
                'constraints': {'time': [{'type': 'time', 'subtype': 'year_month', 'value': '2026-07'}], 'region': []},
                'interpretation': 'Determined as after_sales-return_process, automatically applied 2026-07 time constraint',
                'business_context': 'User asking about return-related questions, need to find after-sales policy documents',
                'suggested_kb_filters': {'file_type': ['policy', 'after_sales']}
            }
        """
        try:
            intent_info = {
                'intent_type': _('intent.other', lang),
                'confidence': 0.0,
                'keywords': [],
                'constraints': {'time': [], 'region': [], 'other': []},
                'interpretation': _('intent.cannot_identify', lang),
                'business_context': '',
                'suggested_kb_filters': {}
            }

            keyword_intent = self._keyword_based_classify(query, lang=lang)
            intent_info['intent_type'] = keyword_intent['type']
            intent_info['confidence'] = keyword_intent['confidence']

            if keyword_intent['keyword']:
                intent_info['keywords'] = [keyword_intent['keyword']]

            time_constraints = self._extract_time_constraints(query)
            region_constraints = self._extract_region_constraints(query)

            intent_info['constraints']['time'] = time_constraints
            intent_info['constraints']['region'] = region_constraints

            adapter = self._get_llm_adapter()

            context_prompt = f"\nKnowledge Base Summary: {kb_summary}" if kb_summary else ""

            prompt = f"""Please analyze the following user query and output JSON results.

User Query: {query}{context_prompt}

Requirements：
1. intent_type: Business intent type, e.g., "after_sales-return_process" for return process, "order-delivery_query" for delivery query, "product-function_usage" for function usage, etc
2. confidence: Confidence level (range 0-1)
3. keywords: List of business keywords extracted from the query
4. constraints: Time/region constraints extracted from the query
5. interpretation: Description of the user query in natural language (up to 50 characters)
6. business_context: Business context description (up to 100 characters)
7. suggested_kb_filters: Suggested knowledge base filters (e.g., file type, tags, etc)

"""

            response = adapter.chat(
                model=self.llm_model,
                messages=[{'role': 'user', 'content': prompt}],
                stream=False
            )

            content = response.get('message', {}).get('content', '')

            try:
                json_start = content.find('{')
                json_end = content.rfind('}') + 1
                if json_start >= 0 and json_end > json_start:
                    llm_result = json.loads(content[json_start:json_end])
                    intent_info.update(llm_result)
                else:
                    logger.warning(f"LLM response is not in JSON format: {content[:100]}")
            except json.JSONDecodeError as e:
                logger.warning(f"Error parsing LLM response: {e}")
                logger.debug(f"Original content: {content}")

            if not intent_info['keywords']:
                intent_info['keywords'] = self._extract_keywords(query)

            if time_constraints:
                intent_info['constraints']['time'] = time_constraints
            if region_constraints:
                intent_info['constraints']['region'] = region_constraints

            if not intent_info['interpretation']:
                intent_info['interpretation'] = self._generate_interpretation(intent_info)

            if not intent_info['business_context']:
                intent_info['business_context'] = f"User asking about {intent_info['intent_type']} questions"

            logger.debug(f"Intent classification result: {intent_info['intent_type']} (confidence: {intent_info['confidence']})")
            return intent_info

        except Exception as e:
            logger.error(f"Intent classification failed: {e}", exc_info=True)
            return self._fallback_classify(query, lang=lang)

    def _extract_keywords(self, query: str) -> List[str]:
        """Extract keywords"""
        try:
            import jieba
            tokens = list(jieba.cut(query))
            stop_words = {'的', '了', '是', '我', '你', '他', '在', '有', '不', '很', '都', '也', '就'}
            return [t for t in tokens if len(t) > 1 and t not in stop_words]
        except ImportError:
            return re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9]{2,}', query)

    def _generate_interpretation(self, intent_info: Dict) -> str:
        """Generate intent interpretation"""
        parts = [f"User query is {intent_info['intent_type']}"]

        if intent_info['constraints']['time']:
            time_values = [c['value'] for c in intent_info['constraints']['time']]
            parts.append(f"，Time range: {', '.join(time_values)}")

        if intent_info['constraints']['region']:
            region_values = [c['value'] for c in intent_info['constraints']['region']]
            parts.append(f"，Region: {', '.join(region_values)}")

        if intent_info['keywords']:
            parts.append(f"，Keywords: {', '.join(intent_info['keywords'][:3])}")

        return ''.join(parts)

    def _fallback_classify(self, query: str, lang: Optional[str] = None) -> Dict:
        """Fallback intent classification (when LLM is unavailable)"""
        intent_info = {
            'intent_type': _('intent.other', lang),
            'confidence': 0.3,
            'keywords': [],
            'constraints': {'time': [], 'region': [], 'other': []},
            'interpretation': _('intent.interpretation_fallback', lang),
            'business_context': _('intent.business_context_fallback', lang),
            'suggested_kb_filters': {}
        }

        keyword_intent = self._keyword_based_classify(query, lang=lang)
        intent_info['intent_type'] = keyword_intent['type']
        intent_info['confidence'] = keyword_intent['confidence']

        if keyword_intent['keyword']:
            intent_info['keywords'] = [keyword_intent['keyword']]

        intent_info['constraints']['time'] = self._extract_time_constraints(query)
        intent_info['constraints']['region'] = self._extract_region_constraints(query)

        intent_info['interpretation'] = self._generate_interpretation(intent_info)

        return intent_info


intent_classifier = IntentClassifier()