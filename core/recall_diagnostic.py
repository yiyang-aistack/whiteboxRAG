"""
Non-recall Diagnosis Module
Analyzes why certain high-value documents were not recalled, provides explainable diagnosis reports
Supports multiple diagnosis strategies: metadata filter analysis, score threshold analysis, retrieval mode difference, keyword gap analysis, etc.
"""
import re
from typing import Dict, List, Optional

import numpy as np

from config import config
from service.i18n import _
from service.logger import get_logger

logger = get_logger('recall_diagnostic')


class RecallDiagnostic:
    """Non-recall diagnoser"""

    def __init__(self):
        self._embedding_client = None
        self._embedding_model = config.get('ollama.embedding_model', 'nomic-embed-text:latest')
        self._embedding_base_url = config.get('ollama.llm_base_url', 'http://localhost:11434')
        self._similarity_threshold = config.get('vector_store.similarity_threshold', 0.1)
        self._diagnostic_enabled = config.get('recall_diagnostic.enabled', True)
        self._full_scan_limit = config.get('recall_diagnostic.full_scan_limit', 500)

    def _get_embedding_client(self):
        """Get Embedding client (lazy loading)"""
        if self._embedding_client is None:
            try:
                import ollama
                self._embedding_client = ollama.Client(host=self._embedding_base_url)
                logger.info(f"[Resoinse Process] Embedding client initialized at: {self._embedding_base_url}")
            except Exception as e:
                logger.error(f"Failed to initialize Embedding client: {e}")
                raise
        return self._embedding_client

    def _get_embedding(self, text: str) -> List[float]:
        """Get Embedding vector for text"""
        try:
            client = self._get_embedding_client()
            response = client.embeddings(model=self._embedding_model, prompt=text)
            return response.get('embedding', [])
        except Exception as e:
            logger.error(f"Failed to get Embedding vector: {e}")
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

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize (lazy import jieba)"""
        try:
            import jieba
            return [token for token in jieba.cut(text) if token.strip() and len(token.strip()) > 1]
        except ImportError:
            return re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9]{2,}', text)

    def diagnose(
        self,
        kb_id: str,
        query: str,
        retrieved_results: List[Dict],
        vector_store,
        bm25_results: Optional[List[Dict]] = None,
        vector_results: Optional[List[Dict]] = None,
        params: Optional[Dict] = None,
        lang: Optional[str] = None,
        debug: bool = False
    ) -> Dict:
        """
        Execute non-recall diagnosis

        Args:
            kb_id: Knowledge base ID
            query: User query
            retrieved_results: Final retrieval results
            vector_store: Vector store instance
            bm25_results: BM25 retrieval results (optional)
            vector_results: Vector retrieval results (optional)
            params: Retrieval parameters (optional)
            debug: Whether to enable debug mode. Only when debug=True or retrieval results are empty
                   will _find_potential_misses full scan be executed (iterating at most
                   full_scan_limit documents and requesting Embedding API for each), to avoid
                   doubling retrieval latency under high load.

        Returns:
            Diagnosis report dictionary
        """
        if not self._diagnostic_enabled:
            return {'enabled': False, 'diagnosis': []}

        try:
            logger.debug(f"[Resoinse Process] Starting non-recall diagnosis for knowledge base: {kb_id}, Query: {query[:50]}...")

            diagnosis = {
                'enabled': True,
                'query': query,
                'kb_id': kb_id,
                'total_retrieved': len(retrieved_results),
                'diagnosis_items': [],
                'potential_misses': [],
                'metadata_filter_analysis': {},
                'score_threshold_analysis': {},
                'mode_difference_analysis': {},
                'keyword_gap_analysis': {},
                'summary': {}
            }

            retrieved_ids = {r['id'] for r in retrieved_results}

            bm25_ids = {r['id'] for r in (bm25_results or [])}
            vector_ids = {r['id'] for r in (vector_results or [])}

            if params is None:
                params = {}

            metadata_filter_result = self._analyze_metadata_filter(kb_id, vector_store, query, params)
            diagnosis['metadata_filter_analysis'] = metadata_filter_result
            if metadata_filter_result.get('filtered_count', 0) > 0:
                diagnosis['diagnosis_items'].append({
                    'type': 'metadata_filter',
                    'severity': 'medium',
                    'title': _('recall.meta_filter_title', lang),
                    'description': metadata_filter_result.get('summary', ''),
                    'details': metadata_filter_result
                })

            score_threshold_result = self._analyze_score_threshold(
                retrieved_results, bm25_results, vector_results, params
            )
            diagnosis['score_threshold_analysis'] = score_threshold_result
            if score_threshold_result.get('near_threshold_count', 0) > 0:
                diagnosis['diagnosis_items'].append({
                    'type': 'score_threshold',
                    'severity': 'high' if score_threshold_result['near_threshold_count'] >= 3 else 'medium',
                    'title': _('recall.score_threshold_title', lang),
                    'description': score_threshold_result.get('summary', ''),
                    'details': score_threshold_result
                })

            mode_diff_result = self._analyze_mode_difference(
                bm25_results, vector_results, retrieved_results, query
            )
            diagnosis['mode_difference_analysis'] = mode_diff_result
            if mode_diff_result.get('bm25_only_count', 0) > 0 or mode_diff_result.get('vector_only_count', 0) > 0:
                diagnosis['diagnosis_items'].append({
                    'type': 'mode_difference',
                    'severity': 'high' if len(mode_diff_result.get('high_value_misses', [])) > 0 else 'medium',
                    'title': _('recall.retrieval_mode_title', lang),
                    'description': mode_diff_result.get('summary', ''),
                    'details': mode_diff_result
                })

            keyword_gap_result = self._analyze_keyword_gap(
                kb_id, vector_store, query, retrieved_results, params
            )
            diagnosis['keyword_gap_analysis'] = keyword_gap_result
            if keyword_gap_result.get('missing_keywords', []):
                diagnosis['diagnosis_items'].append({
                    'type': 'keyword_gap',
                    'severity': 'medium',
                    'title': _('recall.keyword_missing_title', lang),
                    'description': keyword_gap_result.get('summary', ''),
                    'details': keyword_gap_result
                })

            # Only execute full scan in debug mode or when retrieval results are empty.
            # _find_potential_misses will iterate at most full_scan_limit documents and request
            # Embedding API for each, which can double retrieval latency under high CPU/GPU load.
            run_full_scan = debug or len(retrieved_results) == 0
            if run_full_scan:
                potential_misses = self._find_potential_misses(
                    kb_id, vector_store, query, retrieved_results, params
                )
                diagnosis['potential_misses'] = potential_misses
                diagnosis['potential_misses_skipped'] = False
                if potential_misses:
                    diagnosis['diagnosis_items'].append({
                        'type': 'potential_misses',
                        'severity': 'high',
                        'title': _('recall.potential_miss_title', lang),
                        'description': _('recall.potential_miss_desc', lang, len(potential_misses)),
                        'details': {'potential_misses': potential_misses}
                    })
            else:
                # Non-debug mode and retrieval results non-empty: skip full scan, do not block main response.
                # To get potential non-recalled documents, call diagnose_async for async execution.
                diagnosis['potential_misses'] = []
                diagnosis['potential_misses_skipped'] = True

            diagnosis['summary'] = self._generate_summary(diagnosis)

            logger.debug(f"[Resoinse Process] Non-recall diagnosis completed, found {len(diagnosis['diagnosis_items'])} diagnosis items")
            return diagnosis

        except Exception as e:
            logger.error(f"Failed to complete non-recall diagnosis: {e}", exc_info=True)
            return {
                'enabled': True,
                'error': str(e),
                'diagnosis_items': [],
                'potential_misses': []
            }

    async def diagnose_async(
        self,
        kb_id: str,
        query: str,
        retrieved_results: List[Dict],
        vector_store,
        bm25_results: Optional[List[Dict]] = None,
        vector_results: Optional[List[Dict]] = None,
        params: Optional[Dict] = None,
        lang: Optional[str] = None,
        debug: bool = False,
        on_complete=None
    ) -> Dict:
        """
        Execute non-recall diagnosis asynchronously.

        First synchronously returns a quick diagnosis result without potential_misses (does not block main response),
        then schedules the time-consuming _find_potential_misses full scan to a background thread via asyncio,
        and notifies the caller via on_complete callback when complete.

        Args:
            on_complete: Optional callback ``async def on_complete(diagnosis: Dict)``,
                         called when background scan completes.
        """
        import asyncio

        # 1. First execute lightweight diagnosis (without full scan), return immediately
        diagnosis = self.diagnose(
            kb_id=kb_id,
            query=query,
            retrieved_results=retrieved_results,
            vector_store=vector_store,
            bm25_results=bm25_results,
            vector_results=vector_results,
            params=params,
            lang=lang,
            debug=debug,
        )

        # 2. If quick diagnosis skipped full scan, async retry (does not block current response)
        if diagnosis.get('potential_misses_skipped') and not debug:
            async def _background_scan():
                try:
                    loop = asyncio.get_event_loop()
                    potential_misses = await loop.run_in_executor(
                        None,
                        self._find_potential_misses,
                        kb_id, vector_store, query, retrieved_results, params or {}
                    )
                    diagnosis['potential_misses'] = potential_misses
                    diagnosis['potential_misses_skipped'] = False
                    if potential_misses:
                        diagnosis['diagnosis_items'].append({
                            'type': 'potential_misses',
                            'severity': 'high',
                            'title': _('recall.potential_miss_title', lang),
                            'description': _('recall.potential_miss_desc', lang, len(potential_misses)),
                            'details': {'potential_misses': potential_misses}
                        })
                        diagnosis['summary'] = self._generate_summary(diagnosis)
                    logger.debug(f"[Resoinse Process] Background non-recall scan completed, found {len(potential_misses)} potential non-recalled documents")
                    if on_complete:
                        await on_complete(diagnosis) if asyncio.iscoroutinefunction(on_complete) else on_complete(diagnosis)
                except Exception as e:
                    logger.error(f"Failed to complete background non-recall scan: {e}", exc_info=True)



            # Schedule background task, do not wait for completion
            asyncio.create_task(_background_scan())

        return diagnosis

    def _analyze_metadata_filter(self, kb_id: str, vector_store, query: str, params: Dict) -> Dict:
        """
        Analyze metadata filter situation

        Args:
            kb_id: Knowledge base ID
            vector_store: Vector store instance
            query: User query
            params: Retrieval parameters

        Returns:
            Metadata filter analysis result
        """
        try:
            collection = vector_store.get_collection(kb_id)
            if collection is None:
                return {'error': _('recall.kb_not_found')}

            result = collection.get(include=['metadatas', 'documents'])
            total_docs = len(result['ids'])

            filtered_by = {
                'file_type': [],
                'date_range': [],
                'region': [],
                'tags': [],
                'other': []
            }
            filtered_count = 0
            filtered_docs = []

            time_patterns = [
                (r'(\d{4})-(\d{1,2})', 'year_month'),
                (r'(\d{4})-(\d{1,2})-(\d{1,2})', 'date'),
                (r'(\d{1,2})-(\d{1,2})', 'month_day'),
                (r'This year', 'current_year'),
                (r'This month', 'current_month'),
            ]

            region_patterns = [
                (r'(北京|上海|广州|深圳|杭州|成都|南京|武汉|西安|重庆)', 'city'),
                (r'(华东|华南|华北|华中|西南|西北|东北)', 'region'),
            ]

            for i in range(total_docs):
                metadata = result['metadatas'][i] if result.get('metadatas') else {}
                doc_text = result['documents'][i] if result.get('documents') else ""

                filters_applied = []

                file_type = metadata.get('file_type', '')
                if file_type:
                    if 'policy' in file_type.lower() and 'policy' not in query:
                        filters_applied.append(f'Document type is {file_type}')
                    elif 'technical' in file_type.lower() and 'technical' not in query:
                        filters_applied.append(f'Document type is {file_type}')

                for pattern, _ignored in time_patterns:
                    if re.search(pattern, query):
                        doc_date = metadata.get('date', '') or metadata.get('created_at', '')
                        if doc_date and not re.search(pattern, doc_date):
                            filters_applied.append(f'Date does not match: {doc_date}')
                        break

                for pattern, _ignored in region_patterns:
                    if re.search(pattern, query):
                        doc_region = metadata.get('region', '') or metadata.get('location', '')
                        if doc_region and not re.search(pattern, doc_region):
                            filters_applied.append(f'Region does not match: {doc_region}')
                        break

                if filters_applied:
                    filtered_count += 1
                    filtered_docs.append({
                        'id': result['ids'][i],
                        'metadata': metadata,
                        'filters_applied': filters_applied,
                        'text_preview': doc_text[:100]
                    })

            return {
                'total_documents': total_docs,
                'filtered_count': filtered_count,
                'filtered_ratio': filtered_count / total_docs if total_docs > 0 else 0,
                'filtered_by': filtered_by,
                'filtered_docs': filtered_docs[:10],
                'summary': f'Knowledge base has {total_docs} documents, of which {filtered_count} documents may be filtered due to metadata mismatch ({(filtered_count / total_docs * 100) if total_docs else 0:.1f}%)'
            }

        except Exception as e:
            logger.error(f"Metadata filter analysis failed: {e}")
            return {'error': str(e)}

    def _analyze_score_threshold(
        self,
        retrieved_results: List[Dict],
        bm25_results: Optional[List[Dict]],
        vector_results: Optional[List[Dict]],
        params: Dict
    ) -> Dict:
        """
        Analyze score threshold filter situation

        Args:
            retrieved_results: Final retrieval results
            bm25_results: BM25 retrieval results
            vector_results: Vector retrieval results
            params: Retrieval parameters

        Returns:
            Score threshold analysis result
        """
        threshold = params.get('similarity_threshold', self._similarity_threshold)
        near_threshold_count = 0
        near_threshold_docs = []

        all_candidates = []
        if bm25_results:
            all_candidates.extend(bm25_results)
        if vector_results:
            all_candidates.extend(vector_results)

        retrieved_ids = {r['id'] for r in retrieved_results}

        # Number of distinct documents considered by this diagnosis; used as the denominator below.
        # Derived from the local candidate pools because this helper does not touch the vector store.
        total_docs = len({doc['id'] for doc in all_candidates} | retrieved_ids)

        # Calculate the true average score of final retrieval results (fix avg_score always 0 bug causing suggestion to show "average 0.0%")
        retrieved_scores = [r.get('score', 0) for r in retrieved_results]
        avg_score = sum(retrieved_scores) / len(retrieved_scores) if retrieved_scores else 0.0

        for doc in all_candidates:
            if doc['id'] in retrieved_ids:
                continue

            score = doc.get('score', 0)
            bm25_score = doc.get('bm25_score', 0)
            vector_score = doc.get('vector_score', 0)

            if threshold - 0.1 <= score < threshold:
                near_threshold_count += 1
                near_threshold_docs.append({
                    'id': doc['id'],
                    'score': score,
                    'bm25_score': bm25_score,
                    'vector_score': vector_score,
                    'metadata': doc.get('metadata', {}),
                    'text_preview': doc.get('text', '')[:100],
                    'reason': f'Score is {score:.4f} below threshold of {threshold}'
                })

        near_threshold_docs.sort(key=lambda x: x['score'], reverse=True)

        return {
            'threshold': threshold,
            'near_threshold_count': near_threshold_count,
            'near_threshold_docs': near_threshold_docs[:10],
            'avg_score': round(avg_score, 4),
            'summary': f'Knowledge base has {total_docs} documents, of which {near_threshold_count} documents have score close to threshold (below threshold - 0.1 range) and may be missed ({(near_threshold_count / total_docs * 100) if total_docs else 0:.1f}%)'
        }

    def _analyze_mode_difference(
        self,
        bm25_results: Optional[List[Dict]],
        vector_results: Optional[List[Dict]],
        retrieved_results: List[Dict],
        query: str
    ) -> Dict:
        """
        Analyze result differences between different retrieval modes

        Args:
            bm25_results: BM25 retrieval results
            vector_results: Vector retrieval results
            retrieved_results: Final retrieval results
            query: User query

        Returns:
            Retrieval mode difference analysis result
        """
        bm25_ids = {r['id'] for r in (bm25_results or [])}
        vector_ids = {r['id'] for r in (vector_results or [])}
        retrieved_ids = {r['id'] for r in retrieved_results}

        bm25_only = bm25_ids - vector_ids - retrieved_ids
        vector_only = vector_ids - bm25_ids - retrieved_ids
        both_missed = (bm25_ids & vector_ids) - retrieved_ids

        bm25_only_docs = []
        vector_only_docs = []

        if bm25_results:
            for r in bm25_results:
                if r['id'] in bm25_only:
                    bm25_only_docs.append({
                        'id': r['id'],
                        'score': r.get('score', 0),
                        'type': 'bm25_only',
                        'metadata': r.get('metadata', {}),
                        'text_preview': r.get('text', '')[:100],
                        'reason': 'Only BM25 hit, vector retrieval misses'
                    })

        if vector_results:
            for r in vector_results:
                if r['id'] in vector_only:
                    vector_only_docs.append({
                        'id': r['id'],
                        'score': r.get('score', 0),
                        'type': 'vector_only',
                        'metadata': r.get('metadata', {}),
                        'text_preview': r.get('text', '')[:100],
                        'reason': 'Only vector retrieval hit, BM25 misses'
                    })

        high_value_misses = []
        query_tokens = set(self._tokenize(query))

        for doc in bm25_only_docs + vector_only_docs:
            doc_text = doc.get('text_preview', '')
            doc_tokens = set(self._tokenize(doc_text))
            overlap = len(query_tokens & doc_tokens)
            if overlap >= 2 and doc['score'] > 0.3:
                high_value_misses.append(doc)

        bm25_only_docs.sort(key=lambda x: x['score'], reverse=True)
        vector_only_docs.sort(key=lambda x: x['score'], reverse=True)

        return {
            'bm25_only_count': len(bm25_only),
            'vector_only_count': len(vector_only),
            'both_missed_count': len(both_missed),
            'bm25_only_docs': bm25_only_docs[:5],
            'vector_only_docs': vector_only_docs[:5],
            'high_value_misses': high_value_misses[:5],
            'summary': f'M25 has a hit of {len(bm25_only)} documents, vector retrieval has a hit of {len(vector_only)} documents, and both hit {len(both_missed)} documents'
        }

    def _analyze_keyword_gap(
        self,
        kb_id: str,
        vector_store,
        query: str,
        retrieved_results: List[Dict],
        params: Dict
    ) -> Dict:
        """
        Analyze keyword gap situation

        Args:
            kb_id: Knowledge base ID
            vector_store: Vector store instance
            query: User query
            retrieved_results: Final retrieval results
            params: Retrieval parameters

        Returns:
            Keyword gap analysis result
        """
        try:
            query_tokens = set(self._tokenize(query))
            if not query_tokens:
                return {'error': _('recall.cannot_extract_keywords')}

            collection = vector_store.get_collection(kb_id)
            if collection is None:
                return {'error': _('recall.kb_not_found')}

            result = collection.get(include=['documents', 'metadatas'])
            all_docs = []
            for i in range(len(result['ids'])):
                all_docs.append({
                    'id': result['ids'][i],
                    'text': result['documents'][i] if result.get('documents') else "",
                    'metadata': result['metadatas'][i] if result.get('metadatas') else {}
                })

            retrieved_texts = [r.get('text', '') for r in retrieved_results]
            retrieved_tokens = set()
            for text in retrieved_texts:
                retrieved_tokens.update(self._tokenize(text))

            all_tokens = set()
            for doc in all_docs:
                all_tokens.update(self._tokenize(doc['text']))

            domain_keywords = []
            keyword_patterns = [
                (r'退货|退款|换货|维修', '售后'),
                (r'订单|下单|购买|支付|发货|收货', '订单'),
                (r'登录|注册|账号|密码|账户', '账户'),
                (r'产品|功能|使用|操作|设置|配置', '产品'),
                (r'技术|开发|接口|API|部署|安装', '技术'),
                (r'发票|费用|价格|结算|报销', '财务'),
                (r'物流|快递|配送|运输|签收', '物流'),
            ]

            for pattern, domain in keyword_patterns:
                if re.search(pattern, query):
                    for doc in all_docs:
                        if re.search(pattern, doc['text']):
                            doc_tokens = set(self._tokenize(doc['text']))
                            matched_keywords = [t for t in doc_tokens if re.search(pattern, t)]
                            domain_keywords.extend(matched_keywords)

            domain_keywords = list(set(domain_keywords))
            missing_keywords = [kw for kw in domain_keywords if kw not in query_tokens]

            return {
                'query_tokens': list(query_tokens),
                'retrieved_tokens': list(retrieved_tokens),
                'domain_keywords': domain_keywords,
                'missing_keywords': missing_keywords[:10],
                'summary': f'Knowledge base has {len(all_tokens)} tokens, of which {len(missing_keywords)} domain keywords are missing. Suggested additions: {", ".join(missing_keywords[:5])}' if missing_keywords else 'No obvious keyword gaps found'
            }

        except Exception as e:
            logger.error(f"Keyword gap analysis failed: {e}")
            return {'error': str(e)}

    def _find_potential_misses(
        self,
        kb_id: str,
        vector_store,
        query: str,
        retrieved_results: List[Dict],
        params: Dict
    ) -> List[Dict]:
        """
        Find potential non-recalled relevant documents

        Args:
            kb_id: Knowledge base ID
            vector_store: Vector store instance
            query: User query
            retrieved_results: Final retrieval results
            params: Retrieval parameters

        Returns:
            Potential non-recalled document list
        """
        try:
            retrieved_ids = {r['id'] for r in retrieved_results}
            threshold = params.get('similarity_threshold', self._similarity_threshold)

            collection = vector_store.get_collection(kb_id)
            if collection is None:
                return []

            result = collection.get(include=['documents', 'metadatas', 'embeddings'])
            total_docs = len(result['ids'])
            if total_docs == 0:
                return []

            scan_limit = min(total_docs, self._full_scan_limit)

            query_embedding = self._get_embedding(query)
            if not query_embedding:
                return []

            potential_misses = []

            for i in range(scan_limit):
                doc_id = result['ids'][i]
                if doc_id in retrieved_ids:
                    continue

                doc_text = result['documents'][i] if result.get('documents') else ""
                metadata = result['metadatas'][i] if result.get('metadatas') else {}
                embedding = result['embeddings'][i] if result.get('embeddings') else []

                if embedding:
                    similarity = self._cosine_similarity(query_embedding, embedding)
                else:
                    continue

                if similarity >= threshold - 0.05 and similarity < threshold:
                    query_tokens = set(self._tokenize(query))
                    doc_tokens = set(self._tokenize(doc_text))
                    keyword_overlap = len(query_tokens & doc_tokens)

                    # Structured root cause label: for frontend categorized display and ops troubleshooting
                    if keyword_overlap == 0:
                        root_cause = 'embedding_mismatch'
                        root_cause_zh = 'Embedding mismatch'
                    elif similarity >= threshold - 0.02:
                        root_cause = 'threshold_edge'
                        root_cause_zh = 'Threshold edge'
                    elif keyword_overlap <= 1:
                        root_cause = 'keyword_missing'
                        root_cause_zh = ' keyword missing'
                    else:
                        root_cause = 'partial_match'
                        root_cause_zh = 'Partial match'

                    potential_misses.append({
                        'id': doc_id,
                        'similarity': round(similarity, 4),
                        'keyword_overlap': keyword_overlap,
                        'metadata': metadata,
                        'text_preview': doc_text[:150],
                        'root_cause': root_cause,
                        'root_cause_zh': root_cause_zh,
                        'reason': f'Similarity {similarity:.4f} is close to threshold {threshold}, keyword overlap is {keyword_overlap} tokens'
                    })

            potential_misses.sort(key=lambda x: x['similarity'], reverse=True)

            return potential_misses[:10]

        except Exception as e:
            logger.error(f"Potential non-recalled document analysis failed: {e}")
            return []

    def _generate_summary(self, diagnosis: Dict) -> Dict:
        """
        Generate diagnosis summary (business-oriented output)

        Args:
            diagnosis: Complete diagnosis result

        Returns:
            Diagnosis summary
        """
        issues = []
        warnings = []
        suggestions = []
        problem_description = ""
        root_cause = ""

        for item in diagnosis['diagnosis_items']:
            if item['severity'] == 'high':
                issues.append(item['title'])
            elif item['severity'] == 'medium':
                warnings.append(item['title'])

        low_similarity_items = []
        for item in diagnosis['diagnosis_items']:
            if 'similarity' in item.get('description', '') or 'score' in item.get('description', ''):
                low_similarity_items.append(item)

        if diagnosis['metadata_filter_analysis'].get('filtered_count', 0) > 0:
            filtered_count = diagnosis['metadata_filter_analysis']['filtered_count']
            suggestions.append(f'Filtered {filtered_count} documents by metadata filter, suggest to check if the filter rules are too strict')
            if not problem_description:
                problem_description = f'Part of the documents are filtered filtered by metadata filter, which may cause information loss'

        score_analysis = diagnosis['score_threshold_analysis']
        near_threshold_count = score_analysis.get('near_threshold_count', 0)
        avg_score = score_analysis.get('avg_score', 0)
        
        if near_threshold_count >= 3 or avg_score < 0.3:
            suggestions.append(f'Overall similarity of retrieval results is low (average {avg_score:.1%}), consider lowering similarity threshold or optimizing query terms')
            if not root_cause:
                root_cause = 'Query terms do not match document content well'

        mode_diff = diagnosis['mode_difference_analysis']
        if mode_diff.get('bm25_only_count', 0) > 0 or mode_diff.get('vector_only_count', 0) > 0:
            suggestions.append(f'BM25 and vector retrieval results have significant differences, suggest to adjust the hybrid retrieval weight configuration')

        keyword_gap = diagnosis['keyword_gap_analysis']
        if keyword_gap.get('missing_keywords', []):
            missing_kws = keyword_gap['missing_keywords'][:3]
            suggestions.append(f'Missing keywords detected: {", ".join(missing_kws)}, suggest to optimize query terms')

        if diagnosis['potential_misses']:
            suggestions.append(f'Potential non-recalled documents found: {len(diagnosis["potential_misses"])} documents, suggest to check')

        total_retrieved = diagnosis.get('total_retrieved', 0)
        if total_retrieved == 0:
            problem_description = 'No relevant documents found'
            root_cause = 'Query terms do not match document content well'
        elif total_retrieved == 1 and avg_score < 0.3:
            problem_description = 'Only 1 low similarity document found'
            root_cause = 'Document content may not match query intent well or document parsing quality is low'

        if not problem_description:
            if len(issues) > 0:
                problem_description = f'High priority issues detected: {len(issues)} issues'
            elif len(warnings) > 0:
                problem_description = f'Warnings detected: {len(warnings)} warnings'
            else:
                problem_description = 'Retrieval results are normal, no obvious problems found'

        if not root_cause:
            if issues:
                root_cause = f'{issues[0]}'
            else:
                root_cause = 'No clear root cause found'

        return {
            'total_issues': len(issues),
            'total_warnings': len(warnings),
            'issues': issues,
            'warnings': warnings,
            'suggestions': suggestions,
            'overall_status': 'critical' if len(issues) >= 2 else 'warning' if len(issues) > 0 or len(warnings) > 0 else 'healthy',
            'problem_description': problem_description,
            'root_cause': root_cause,
            'actionable_suggestions': suggestions[:3]
        }


recall_diagnostic = RecallDiagnostic()
