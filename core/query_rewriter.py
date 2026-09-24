"""
Query Rewrite module
Rewrites natural language queries into forms more suitable for vector database retrieval
Core features: term mapping, query expansion, query purification, typo correction
"""
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from service.logger import get_logger

logger = get_logger('query_rewriter')


class QueryRewriter:
    """Query rewriter"""

    def __init__(self):
        self._term_mappings = {}
        self._synonym_dict_path = Path(__file__).parent.parent / 'config' / 'synonym_dict.yaml'
        self._synonym_map = {}
        self._load_synonym_dict()

        self._stop_words = {
            # '的', '了', '是', '我', '你', '他', '她', '它', '这', '那', '此', '其',
            # '能', '可以', '会', '就', '都', '也', '很', '在', '有', '不', '没', '无',
            # '要', '去', '来', '上', '下', '出', '进', '过', '给', '和', '与', '及', '等',
            # '对', '对于', '关于', '因为', '所以', '但是', '如果', '虽然', '还是', '或者',
            # '什么', '怎么', '如何', '为什么', '哪里', '谁', '哪个', '多少', '几', '何时',
            # '一些', '一点', '很多', '非常', '比较', '相当', '其实', '确实', '真的', '太',
            # '更', '最', '再', '又', '还', '已经', '正在', '将要', '曾经', '刚才', '现在',
            # '今天', '明天', '昨天', '之前', '之后', '然后', '最后', '首先', '其次', '接着',
            # '终于', '突然', '忽然', '慢慢', '渐渐', '一直', '始终', '经常', '偶尔', '有时',
            # '从不', '几乎', '大概', '差不多', '可能', '应该', '必须', '需要', '得', '愿意',
            # '想', '希望', '打算', '计划', '准备', '开始', '结束', '继续', '停止', '放弃',
            # '坚持', '努力', '尝试', '成功', '失败', '完成', '实现', '达到', '超过', '不足',
            # '缺少', '增加', '减少', '提高', '降低', '改变', '保持', '维持', '保护', '破坏',
            # '创造', '使用', '利用', '开发', '研究', '设计', '生产', '销售', '购买', '消费',
            # '提供', '获取', '获得', '失去', '拥有', '出现', '消失', '发生', '产生', '导致',
            # '引起', '影响', '决定', '控制', '管理', '处理', '解决', '避免', '防止', '应对',
            # '执行', '运行', '启动', '关闭', '打开', '保存', '删除', '修改', '更新', '添加',
            # '移除', '创建', '编辑', '查看', '搜索', '查找', '检索', '查询', '浏览', '下载',
            # '上传', '导入', '导出', '转换', '格式', '版本', '程序', '软件', '硬件', '设备',
            # '网络', '数据', '信息',
            # '标题', '摘要', '正文', '段落',
            # '句子', '单词', '字符', '长度', '大小', '数量', '时间', '日期', '地点', '位置',
            # '方向', '距离', '速度', '价格', '成本', '费用', '利润', '收入', '支出',
            # '预算', '投资', '回报', '风险', '机会', '挑战', '问题', '答案', '解决方案',
            # '方法', '技术', '工具', '资源',
            # '特点', '优势', '劣势',
            # '综上所述', '一言以蔽之', '简而言之', '概括起来', '归纳起来', '总结起来',
            # '总而言之', '综上所述', '一言以蔽之', '简而言之', '概括起来', '归纳起来',
            # '总结起来'
        }

        self._colloquial_words = {
            # '咋', '咋弄', '咋办', '咋整', '咋搞', '咋回事', '咋啦', '咋地', '咋说',
            # '弄', '弄弄', '弄一下', '弄好', '弄明白', '弄清楚', '弄到', '弄来', '弄出',
            # '啊', '呀', '呢', '吧', '嘛', '哦', '嗯', '哈', '嘿', '呵', '唉', '唉呀',
            # '哎哟', '哎呀', '哦哟', '嚯', '哇', '哟', '啧', '嘘', '嗯哼',
            # '这个', '那个', '这些', '那些', '这么', '那么', '怎样', '怎么样',
            # '啥', '啥子', '啥玩意儿', '啥情况', '啥意思', '啥时候', '啥地方',
            # '玩意儿', '事儿', '东西', '家伙', '玩意儿',
            # '呗', '罢了', '而已', '算了', '就是了', '罢了', '而已', '算了',
            # '嗯哼'
        }

        self._punctuation = {
            '，', '。', '！', '？', '：', '；', '、', '—', '…', '·', '《', '》',
            '‘', '’', '“', '”', '（', '）', '【', '】', '〔', '〕', '「', '」',
            '『', '』', '·', '·', '·', '·', '·', '·', '·', '·', '·', '·',
            ',', '.', '!', '?', ':', ';', '-', '--', '---', '...', '*', '**',
            '`', '``', "''", '(', ')', '[', ']', '{', '}', '<', '>', '&', '|',
            '#', '@', '$', '%', '^', '=', '+', '_', '~', '\\', '/', '"', "'"
        }

        self._business_keywords = {
            # Here to add business keywords that are important for the query
            # '退款', '退货', '客服', '登录', '注册', '密码', '账户', '账号', '订单',
            # '支付', '发货', '收货', '发票', '优惠', '活动', '功能', '设置', '安装',
            # '升级', '错误', '帮助', '联系', '查询', '文档', '指南', 'API', '接口',
            # '模型', '向量', '检索', '匹配', '结果', '回答', '系统', '服务', '开发',
            # '测试', '部署', '调试', '性能', '优化', '配置', '安全', '权限', '认证',
            # '授权', '数据', '存储', '数据库', '文件', '解析', '生成', '更新', '删除',
            # '修改', '添加', '上传', '下载', '预览', '分享', '打印', '导出', '导入',
            # '转换', '压缩', '解压', '合并', '分割', '加密', '解密', '备份', '恢复',
            # '同步', '冲突', '报告', '统计', '分析', '评估', '监控', '日志', '警告',
            # '同时', '非阻塞', '后台', '临时', 'RAM', '处理器', '计算', '核心', '硬盘',
            # 'SSD', '尺寸', '总数', '运算'
        }

    def tokenize(self, text: str) -> List[str]:
        """Tokenize (using jieba)"""
        try:
            import jieba
            return list(jieba.cut(text))
        except ImportError:
            return re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9]+', text)

    def _is_valid_term(self, term: str) -> bool:
        """Check if valid term"""
        if not term or not term.strip():
            return False
        if term in self._punctuation:
            return False
        if term in self._colloquial_words:
            return False
        if re.match(r'^\d+$', term):
            return False
        if term in self._stop_words and term not in self._business_keywords:
            return False
        return True

    def _remove_stop_words(self, tokens: List[str]) -> List[str]:
        """Remove stop words, punctuation, modal particles"""
        return [t for t in tokens if self._is_valid_term(t)]

    def _expand_with_synonyms(self, tokens: List[str]) -> List[str]:
        """Expand query with synonyms"""
        expanded = set(tokens)
        for token in tokens:
            if token in self._synonym_map:
                expanded.update(self._synonym_map[token])
        return list(expanded)

    def _extract_key_terms(self, text: str) -> List[str]:
        """Extract keywords"""
        tokens = self.tokenize(text)
        tokens = self._remove_stop_words(tokens)
        return tokens

    def rewrite(self, query: str, kb_summary: Optional[str] = None) -> Dict:
        """
        Rewrite query

        Args:
            query: Original query
            kb_summary: Knowledge base summary (optional, used for term mapping)

        Returns:
            Rewrite result dictionary, containing:
                - original_query: Original query
                - rewritten_query: Rewritten query
                - key_terms: Extracted keywords
                - expanded_terms: Expanded terms (including synonyms)
                - term_mappings: Term mapping relationships
                - rewrite_reason: Rewrite reason description
                - typo_correction: Typo correction info
        """
        result = {
            'original_query': query,
            'rewritten_query': query,
            'key_terms': [],
            'expanded_terms': [],
            'term_mappings': [],
            'rewrite_reason': 'Original query is suitable for retrieval',
            'typo_correction': {
                'has_correction': False,
                'corrected_text': query,
                'corrections': []
            }
        }

        from core.typo_checker import typo_checker
        typo_result = typo_checker.check_and_correct(query)
        corrected_query = typo_result['corrected_text']

        if typo_result['has_correction']:
            result['typo_correction'] = {
                'has_correction': True,
                'corrected_text': corrected_query,
                'corrections': typo_result['corrections']
            }
            logger.info(f"[Response Process] Typo correction: {query} -> {corrected_query}")

        key_terms = self._extract_key_terms(corrected_query)
        result['key_terms'] = key_terms

        if not key_terms:
            result['rewritten_query'] = corrected_query
            result['rewrite_reason'] = 'Failed to extract valid keywords, use corrected query instead'
            return result

        expanded_terms = self._expand_with_synonyms(key_terms)
        result['expanded_terms'] = expanded_terms

        rewritten_parts = []
        for term in key_terms:
            if term in self._synonym_map:
                mapped_terms = self._synonym_map[term]
                result['term_mappings'].append({
                    'original': term,
                    'synonyms': mapped_terms[:3]
                })
                rewritten_parts.append(term)
                rewritten_parts.extend(mapped_terms[:2])
            else:
                rewritten_parts.append(term)

        rewritten_query = ' '.join(rewritten_parts)

        reasons = []
        if typo_result['has_correction']:
            reasons.append(f'Corrected {len(typo_result["corrections"])} typos')

        if rewritten_query != corrected_query:
            reasons.append(f'Expanded {len(result["term_mappings"])} keywords')

        if len(key_terms) == 1 and len(key_terms[0]) <= 1:
            reasons.append('Single character query, expanded synonyms to enhance recall rate')

        if reasons:
            result['rewritten_query'] = rewritten_query
            result['rewrite_reason'] = '；'.join(reasons)
        else:
            result['rewritten_query'] = corrected_query

        if kb_summary:
            kb_terms = self._extract_key_terms(kb_summary)
            matched_kb_terms = [t for t in key_terms if t in kb_terms]
            if matched_kb_terms:
                result['rewrite_reason'] += f'；Matched knowledge base terms: {", ".join(matched_kb_terms)}'

        return result

    def batch_rewrite(self, queries: List[str], kb_summary: Optional[str] = None) -> List[Dict]:
        """Batch rewrite queries"""
        return [self.rewrite(q, kb_summary) for q in queries]

    def fit_knowledge_base(self, docs: List[str]):
        """Train term mappings based on knowledge base documents"""
        all_terms = set()
        for doc in docs:
            terms = self._extract_key_terms(doc)
            all_terms.update(terms)

        self._term_mappings['knowledge_terms'] = list(all_terms)
        logger.info(f"[Response Process] Extracted {len(all_terms)} terms from knowledge base")

    def get_knowledge_terms(self) -> List[str]:
        """Get knowledge base terms"""
        return self._term_mappings.get('knowledge_terms', [])

    def suggest_queries(self, kb_summary: str, max_suggestions: int = 5) -> List[str]:
        """
        Generate query suggestions based on knowledge base content

        Args:
            kb_summary: Knowledge base summary
            max_suggestions: Maximum number of suggestions

        Returns:
            Query suggestion list
        """
        if not kb_summary:
            return []
        
        kb_terms = self._extract_key_terms(kb_summary)
        if not kb_terms:
            return []
        
        suggestions = []
        
        product_terms = [t for t in kb_terms if len(t) >= 2]
        
        if product_terms:
            main_term = product_terms[0]
            suggestions.append(f'{main_term} technical specifications?')
            suggestions.append(f'{main_term} material requirements?')
            
        if len(product_terms) >= 2:
            suggestions.append(f'{product_terms[0]} and {product_terms[1]} performance parameters?')
        
        suggestions.append('Please provide detailed technical requirements for the product')
        suggestions.append('What are the quality standards and testing requirements for the product?')
        
        return suggestions[:max_suggestions]

    def _load_synonym_dict(self):
        """Load synonym dictionary from YAML file (config/synonym_dict.yaml)"""
        if self._synonym_dict_path.exists():
            try:
                import yaml
                with open(self._synonym_dict_path, 'r', encoding='utf-8') as f:
                    config_synonyms = yaml.safe_load(f) or {}

                # Load entirely from config file, no more hardcoded baseline
                self._synonym_map = {}
                for key, synonyms in config_synonyms.items():
                    if isinstance(synonyms, list):
                        # Deduplicate
                        self._synonym_map[key] = list(dict.fromkeys(synonyms))
                    elif isinstance(synonyms, str):
                        self._synonym_map[key] = [synonyms]

                logger.info(f"[Response Process] Loaded {len(self._synonym_map)} synonym groups from config file")
            except Exception as e:
                logger.error(f"Failed to load synonym dictionary: {e}")
                self._synonym_map = {}
        else:
            logger.warning(f"Synonym dictionary file not found: {self._synonym_dict_path}")
            self._synonym_map = {}

    def reload_synonym_dict(self):
        """
        Hot reload synonym dictionary: reload from YAML file without restarting service.
        Applies when config/synonym_dict.yaml is modified via API for immediate effect.
        """
        self._load_synonym_dict()
        logger.info(f"[Response Process] Synonym dictionary reloaded, current {len(self._synonym_map)} entries loaded")
        return len(self._synonym_map)

    def add_synonym(self, term: str, synonyms: List[str]):
        """
        Add synonym

        Args:
            term: Standard term
            synonyms: Synonym list
        """
        if term not in self._synonym_map:
            self._synonym_map[term] = []
        for synonym in synonyms:
            if synonym not in self._synonym_map[term]:
                self._synonym_map[term].append(synonym)
        logger.info(f"[Response Process] Added synonym group: {term} -> {synonyms}")

    def remove_synonym(self, term: str, synonym: str = None):
        """
        Delete synonym

        Args:
            term: Standard term
            synonym: Optional, specific synonym to delete; if not specified, delete the entire entry
        """
        if synonym:
            if term in self._synonym_map and synonym in self._synonym_map[term]:
                self._synonym_map[term].remove(synonym)
                logger.info(f"[Response Process] Removed synonym from group: {term} -> {synonym}")
        else:
            if term in self._synonym_map:
                del self._synonym_map[term]
                logger.info(f"[Response Process] Removed synonym group: {term}")

    def list_synonyms(self) -> Dict[str, List[str]]:
        """Get all synonyms"""
        return dict(self._synonym_map)

    def save_synonym_dict(self):
        """Save synonym dictionary to file"""
        try:
            import yaml
            with open(self._synonym_dict_path, 'w', encoding='utf-8') as f:
                yaml.dump(self._synonym_map, f, allow_unicode=True, indent=2)
            logger.info(f"[Response Process] Synonym dictionary saved to {self._synonym_dict_path}")
        except Exception as e:
            logger.error(f"Failed to save synonym dictionary: {e}")
