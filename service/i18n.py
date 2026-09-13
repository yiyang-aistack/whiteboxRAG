"""
whiteBoxRAG backend internationalization module
Supports Chinese (zh-CN) and English (en-US)

Usage:
1. from service.i18n import _
2. msg = _('kb.not_found', lang)  # Return translation based on language
3. msg = _('kb.not_found')        # Default Chinese

Language source priority:
1. Explicitly passed lang parameter
2. Accept-Language request header
3. Query parameter lang
4. Default zh-CN
"""
from typing import Optional

from fastapi import Request


# ==================== Translation Dictionary ====================
_TRANSLATIONS = {
    'zh-CN': {
        # ===== API Common =====
        'api.service_unavailable': 'LLM服务未初始化',
        'api.internal_error': '服务异常: {}',
        'api.not_found': '资源不存在',
        'api.bad_request': '请求参数错误',

        # ===== Document Optimization =====
        'docopt.analyze_failed': '文档分析失败',
        'docopt.optimize_failed': '文档优化失败',
        'docopt.issues_failed': '获取文档问题失败',
        'docopt.coverage_failed': '获取文档覆盖度失败',
        'docopt.download_failed': '下载文档失败',
        'docopt.file_not_found': '文件不存在',
        'docopt.invalid_file_name': '非法的文件名: {}',
        'docopt.full_analysis_failed': '完整分析失败',
        'docopt.analyze_success': '文档分析完成',
        'docopt.optimize_success': '成功优化了 {} 个文档',
        'docopt.full_analysis_done': '完整分析已完成，请查看日志获取详细结果',
        'docopt.suggestion_suffix': '_优化建议',
        'docopt.optimized_suffix': '_opt',

        # ===== Knowledge Base =====
        'kb.not_found': '知识库不存在',
        'kb.name_required': '知识库名称不能为空',
        'kb.create_success': '知识库创建成功',
        'kb.delete_success': '知识库删除成功',
        'kb.delete_failed': '知识库删除失败',
        'kb.already_exists': '知识库已存在',
        'kb.no_documents': '知识库中没有文档',
        'kb.upload_success': '文档上传成功，正在后台处理',
        'kb.upload_failed': '文档上传失败',
        'kb.parse_failed': '文档解析失败',
        'kb.delete_confirm': '确定删除此文档？',
        'kb.doc_not_found': '文档不存在',
        'kb.chunk_not_found': '分块不存在',
        'kb.raw_failed': '获取原始文档失败',
        'kb.max_limit': '知识库数量已达上限 ({}个)',
        'kb.invalid_scenario': '无效的场景ID: {}，可用场景: {}',
        'kb.invalid_chunk_size': 'chunk_size 不能小于 100',
        'kb.invalid_chunk_overlap': 'chunk_overlap 不能为负数',
        'kb.invalid_chunk_params': 'chunk_overlap 必须小于 chunk_size',
        'kb.unsupported_format': '不支持的文件格式，支持: {}',
        'kb.file_too_large': '文件过大，最大支持 {}MB',
        'kb.mime_mismatch': '文件类型校验失败：后缀 {} 与实际类型 {} 不匹配，疑似伪装文件',
        'kb.file_not_found': '文件不存在',
        'kb.doc_update_success': '文档更新成功，正在后台处理',
        'kb.delete_file_success': '文件 "{}" 已删除',
        'kb.synonym_empty': '标准词不能为空',
        'kb.synonym_array': '同义词必须是数组',
        'kb.synonym_add_success': '同义词添加成功',
        'kb.synonym_delete_success': '同义词 "{}" 已删除',
        'kb.synonym_group_delete': '同义词组 "{}" 已删除',
        'kb.typo_empty': '错误写法和正确写法都不能为空',
        'kb.typo_add_success': '错别字规则添加成功',
        'kb.typo_delete_success': '错别字规则 "{}" 已删除',
        'kb.parse_start': '开始解析文档',
        'kb.parse_done': '解析完成，共 {} 个分块，开始向量化',
        'kb.process_done': '处理完成，成功添加 {} 个向量',

        # ===== Chat =====
        'chat.query_empty': '查询不能为空',
        'chat.query_too_long': '查询过长，最大支持1000字符',
        'chat.trace_not_found': '溯源记录不存在',
        'chat.trace_id_required': '缺少trace_id参数',
        'chat.health_failed': '健康检查失败',
        'chat.simulate_failed': '模拟问答服务异常: {}',
        'chat.service_error': '问答服务异常: {}',
        'chat.trace_get_failed': '获取溯源信息失败: {}',
        'chat.feedback_failed': '处理用户反馈失败: {}',
        'chat.rules_get_failed': '获取规则列表失败: {}',
        'chat.rule_create_failed': '创建业务规则失败: {}',
        'chat.rule_delete_failed': '删除业务规则失败: {}',
        'chat.rule_effectiveness_failed': '获取规则生效率报告失败: {}',
        'chat.rule_logs_failed': '获取规则应用日志失败: {}',
        'chat.history_get_failed': '获取对话历史失败: {}',
        'chat.stats_get_failed': '获取对话统计失败: {}',
        'chat.conversation_not_found': '对话记录不存在',
        'chat.conversation_delete_failed': '删除对话记录失败: {}',

        # ===== A/B Test =====
        'abtest.min_variants': '至少需要2个变体',
        'abtest.max_variants': '最多支持5个变体',
        'abtest.failed': 'A/B测试服务异常: {}',
        'abtest.batch_failed': '批量A/B测试服务异常: {}',

        # ===== Evaluation =====
        'eval.failed': '评估失败',
        'eval.not_found': '评估记录不存在',
        'eval.batch_failed': '批量评估失败',
        'eval.report_generate_failed': '生成评估报告失败',
        'eval.report_not_found': '报告不存在: {}',
        'eval.report_get_failed': '获取评估报告失败',
        'eval.report_list_failed': '获取评估报告列表失败',
        'eval.metrics_get_failed': '获取指标定义失败',
        'eval.report_generate_success': '评估报告生成成功，报告ID: {}',

        # ===== Scenario =====
        'scenario.not_found': '场景不存在',
        'scenario.create_success': '场景创建成功',
        'scenario.update_success': '场景更新成功',
        'scenario.delete_success': '场景删除成功',
        'scenario.list_failed': '获取场景列表失败',
        'scenario.detail_failed': '获取场景详情失败',
        'scenario.params_failed': '获取场景参数失败',
        'scenario.invalid_id': '无效的场景ID',
        'scenario.config_valid': '场景配置有效',
        'scenario.config_warning': '场景配置存在警告',
        'scenario.validate_failed': '验证场景配置失败',

        # ===== Monitor =====
        'monitor.failed': '获取监控数据失败',
        'monitor.stats_failed': '获取性能统计失败',
        'monitor.logs_query_failed': '查询日志失败',
        'monitor.tasks_failed': '获取任务列表失败',
        'monitor.task_not_found': '任务不存在',
        'monitor.task_detail_failed': '获取任务详情失败',
        'monitor.stats_reset_done': '统计数据已重置',
        'monitor.stats_reset_failed': '重置统计失败',

        # ===== API Common Extended =====
        'api.server_error': '服务器内部错误',
        'api.rate_limit_exceeded': '请求过于频繁，每分钟限制 {} 次，请稍后重试',
        'api.app_description': '企业级轻量化私有化RAG系统',
        'api.app_starting': '启动 {} v{}',
        'api.app_env': '环境: {}',
        'api.llm_ready': 'LLM服务就绪，模型: {}',
        'api.llm_unavailable_detail': 'LLM服务不可用: {}',
        'api.pipeline_init_failed': '初始化LLM流水线失败: {}',
        'api.scheduler_init_failed': '初始化定时任务失败: {}',
        'api.app_started': '应用启动完成',
        'api.app_shutdown': '应用正在关闭...',
        'api.app_shutdown_done': '应用已关闭',
        'api.request_log': '请求: {} {}',
        'api.request_error': '请求异常: {} {} - {}',
        'api.uncaught_exception': '未捕获的异常: {}',
        'api.health_tag': '系统',

        # ===== Core - Document Parser =====
        'parser.file_not_found': '文件不存在',
        'parser.unsupported_format': '不支持的文件格式: {}',
        'parser.file_too_large': '文件过大: {}MB，最大支持 {}MB',
        'parser.dir_not_found': '目录不存在: {}',

        # ===== Core - LLM Adapter =====
        'llm.unsupported_provider': '不支持的LLM提供者: {}',

        # ===== Core - Retriever =====
        'retrieve.kb_not_found': '知识库不存在: {}',
        'retrieve.invalid_mode': '无效的检索模式: {}',

        # ===== Core - Rule Engine =====
        'rule.not_found': '规则不存在',

        # ===== Core - Vector Store =====
        'vector.kb_not_found': '知识库不存在: {}',

        # ===== Tool - Log Analyzer =====
        'log_analyzer.method_not_implemented': '子类必须实现 parse 方法',
        'log_analyzer.no_data': '暂无数据',

        # ===== Core - LLM Pipeline =====
        'pipeline.no_results': '抱歉，未找到相关信息。',
        'pipeline.kb_no_content': '当前知识库中没有这方面的信息。',
        'pipeline.llm_supplement': '以下内容由大模型补充生成，仅供参考：',
        'pipeline.quality_low': '检索质量低（平均分 {} ≤ 0.4），启用混合回答模式',
        'pipeline.llm_unavailable': '抱歉，AI模型服务暂时不可用，请稍后再试。',
        'pipeline.hybrid_disclaimer': '当前知识库中没有这方面的信息。\n\n以下内容来自大模型自身知识，仅供参考：\n\n',
        'pipeline.empty_response': '抱歉，未找到相关信息。',
        'pipeline.circuit_busy': '抱歉，AI服务暂时繁忙，请稍后再试。',
        'pipeline.llm_error': '抱歉，AI模型服务暂时不可用，请稍后再试。',
        'pipeline.status_retrieving': '正在检索相关文档...',
        'pipeline.status_generating': 'AI正在生成回答...',
        'pipeline.status_boundary_rejected': '该问题超出业务范围',
        'pipeline.boundary_rejected_msg': '抱歉，您的问题不在我的业务范围内，请咨询相关业务部门或尝试询问与业务相关的问题。',
        'pipeline.llm_exception': 'AI模型服务异常: {}',
        'pipeline.citation_missing': '答案中未检测到有效的引用标记，请检查是否按要求使用[文档编号]格式标注来源',
        'pipeline.citation_invalid': '发现 {} 个无效引用标记: {}，请确保引用编号与参考文档编号一致',
        'pipeline.citation_valid': '引用验证通过',
        'pipeline.unknown_doc': '未知文档',
        'pipeline.system_prompt_default': (
            '你是一个专业的企业知识助手。请根据提供的参考文档，回答用户的问题。\n\n'
            '【回答规则】\n'
            '1. 只基于参考文档中的信息回答，不要编造内容\n'
            '2. 如果参考文档中没有相关信息，请明确告知\n'
            '3. 回答要清晰、准确、有条理\n'
            '4. 【强制】必须在回答中使用引用标记，格式为 [文档编号]，例如：\n'
            '   - "根据[文档1]，退货期限为7天"\n'
            '   - "产品保修政策见[文档2]和[文档3]"\n'
            '5. 每个关键信息点都必须标注来源文档编号\n'
            '6. 如果信息来自多个文档，需要全部标注\n'
            '7. 不要直接大段复制文档内容，要进行适当总结和改写\n'
            '8. 【强制】对于文档中的数值（如尺寸、温度、转速、公差等），必须保持与原文完全一致的精度，不得进行四舍五入或近似处理'
        ),
        'pipeline.system_prompt_hybrid': (
            '你是一个专业的企业知识助手。经过检索，当前知识库中没有找到与用户问题直接相关的信息。\n\n'
            '【回答规则 - 必须严格遵守】\n'
            '1. 第一段必须以这句话开头："当前知识库中没有这方面的信息。"\n'
            '2. 第二段开头必须写："以下内容来自大模型自身知识，仅供参考："，然后再给出你基于自身知识的回答\n'
            '3. 绝对不要使用任何引用标记（如 [1]、[文档1] 等），因为知识库中没有相关文档\n'
            '4. 回答要清晰、准确、有条理\n'
            '5. 如果你的知识也无法回答，请只说"抱歉，我也无法回答这个问题。"'
        ),

        # ===== Core - Boundary =====
        'boundary.keyword_blacklist': '命中黑名单关键词',
        'boundary.keyword_whitelist': '命中白名单关键词',
        'boundary.retrieval_empty': '未检索到相关文档',
        'boundary.retrieval_low_score': '检索结果平均相似度过低（{}）',
        'boundary.disabled': '边界检测已禁用',
        'boundary.default_in_domain': '未命中任何边界规则，默认视为在业务范围内',
        'boundary.rejection_default': '抱歉，您的问题不在我的业务范围内，请咨询相关业务部门或尝试询问与业务相关的问题。',
        'boundary.rejection_empty': '抱歉，未在知识库中找到相关信息，请尝试换一种提问方式或上传相关文档。',

        # ===== Core - Sentence Tracing =====
        'trace.confidence_direct_quote': '直接引用',
        'trace.confidence_summary': '摘要改写',
        'trace.confidence_low': '低置信度',
        'trace.confidence_drift': '无依据推断',
        'trace.confidence_no_source': '无来源',

        # ===== Core - Intent =====
        'intent.other': '其他',
        'intent.cannot_identify': '无法识别具体业务意图',
        'intent.interpretation_fallback': '系统繁忙，使用基础意图识别',
        'intent.business_context_fallback': '无法使用LLM进行意图分析，降级为关键词匹配',
        'intent.business_context_prefix': '用户询问{}相关问题',

        # ===== Core - Evaluator =====
        'eval.boundary_low': '边界检测置信度低 ({})，综合分从 {} 调整至 {}',
        'eval.boundary_low_confidence': '边界置信度低({})，分数已调整',
        'eval.retrieval_quality_low': '检索质量低，分数已封顶',

        # ===== Core - Sentence Tracing =====
        'trace.drift_embedding_failed': '无法获取句子Embedding',
        'trace.drift_no_source': '没有找到任何相关来源',
        'trace.drift_low_similarity': '相似度 {} 较低',
        'trace.citation_direct_quote': '直接引用',
        'trace.citation_summary': '摘要改写',
        'trace.citation_low_confidence': '低置信度',
        'trace.citation_drift': '无依据推断',
        'trace.citation_no_source': '无来源',
        'trace.citation_unknown': '未知',

        # ===== Core - Recall Diagnostic =====
        'recall.meta_filter_title': '元数据过滤分析',
        'recall.score_threshold_title': '分数阈值分析',
        'recall.retrieval_mode_title': '检索模式差异分析',
        'recall.keyword_missing_title': '关键词缺失分析',
        'recall.potential_miss_title': '潜在相关文档',
        'recall.potential_miss_desc': '发现 {} 个可能相关但未被召回的文档',
        'recall.kb_not_found': '知识库不存在',
        'recall.cannot_extract_keywords': '无法提取查询关键词',
        'recall.filtered_docs_summary': '知识库共 {} 个文档，其中 {} 个文档因元数据不匹配可能被过滤（占比 {:.1f}%）',
        'recall.near_threshold_summary': '有 {} 个文档分数接近阈值（低于阈值0.1范围内），可能因阈值过严未被召回',
        'recall.bm25_only': '仅BM25命中，向量检索未命中',
        'recall.vector_only': '仅向量检索命中，BM25未命中',
        'recall.mode_diff_summary': 'BM25独有命中 {} 个，向量独有命中 {} 个，两者都命中但最终未召回 {} 个',
        'recall.missing_keywords_summary': '检测到 {} 个领域关键词可能缺失，建议补充：{}',
        'recall.no_issues': '未发现明显问题',
        'recall.root_cause_embedding': 'Embedding 不匹配',
        'recall.root_cause_threshold': '阈值卡边',
        'recall.root_cause_keyword': '关键词缺失',
        'recall.root_cause_partial': '部分匹配',
    },

    'en-US': {
        # ===== API Common =====
        'api.service_unavailable': 'LLM service not initialized',
        'api.internal_error': 'Service error: {}',
        'api.not_found': 'Resource not found',
        'api.bad_request': 'Bad request',

        # ===== Document Optimization =====
        'docopt.analyze_failed': 'Document analysis failed',
        'docopt.optimize_failed': 'Document optimization failed',
        'docopt.issues_failed': 'Failed to get document issues',
        'docopt.coverage_failed': 'Failed to get document coverage',
        'docopt.download_failed': 'Failed to download document',
        'docopt.file_not_found': 'File not found',
        'docopt.invalid_file_name': 'Invalid file name: {}',
        'docopt.full_analysis_failed': 'Full analysis failed',
        'docopt.analyze_success': 'Document analysis completed',
        'docopt.optimize_success': 'Successfully optimized {} document(s)',
        'docopt.full_analysis_done': 'Full analysis completed, check logs for details',
        'docopt.suggestion_suffix': '_suggestion',
        'docopt.optimized_suffix': '_opt',

        # ===== Knowledge Base =====
        'kb.not_found': 'Knowledge base not found',
        'kb.name_required': 'Knowledge base name cannot be empty',
        'kb.create_success': 'Knowledge base created successfully',
        'kb.delete_success': 'Knowledge base deleted successfully',
        'kb.delete_failed': 'Failed to delete knowledge base',
        'kb.already_exists': 'Knowledge base already exists',
        'kb.no_documents': 'No documents in knowledge base',
        'kb.upload_success': 'Document uploaded, processing in background',
        'kb.upload_failed': 'Document upload failed',
        'kb.parse_failed': 'Document parsing failed',
        'kb.delete_confirm': 'Are you sure you want to delete this document?',
        'kb.doc_not_found': 'Document not found',
        'kb.chunk_not_found': 'Chunk not found',
        'kb.raw_failed': 'Failed to get raw document',
        'kb.max_limit': 'Knowledge base limit reached ({} KB max)',
        'kb.invalid_scenario': 'Invalid scenario ID: {}, available: {}',
        'kb.invalid_chunk_size': 'chunk_size cannot be less than 100',
        'kb.invalid_chunk_overlap': 'chunk_overlap cannot be negative',
        'kb.invalid_chunk_params': 'chunk_overlap must be less than chunk_size',
        'kb.unsupported_format': 'Unsupported file format, supported: {}',
        'kb.file_too_large': 'File too large, max {}MB supported',
        'kb.mime_mismatch': 'File type validation failed: extension {} does not match detected type {}, possible disguised file',
        'kb.file_not_found': 'File not found',
        'kb.doc_update_success': 'Document updated, processing in background',
        'kb.delete_file_success': 'File "{}" deleted',
        'kb.synonym_empty': 'Standard term cannot be empty',
        'kb.synonym_array': 'Synonyms must be an array',
        'kb.synonym_add_success': 'Synonym added successfully',
        'kb.synonym_delete_success': 'Synonym "{}" deleted',
        'kb.synonym_group_delete': 'Synonym group "{}" deleted',
        'kb.typo_empty': 'Both typo and correction cannot be empty',
        'kb.typo_add_success': 'Typo rule added successfully',
        'kb.typo_delete_success': 'Typo rule "{}" deleted',
        'kb.parse_start': 'Starting document parsing',
        'kb.parse_done': 'Parsing done, {} chunks, starting vectorization',
        'kb.process_done': 'Processing done, {} vectors added',

        # ===== Chat =====
        'chat.query_empty': 'Query cannot be empty',
        'chat.query_too_long': 'Query too long, max 1000 characters',
        'chat.trace_not_found': 'Trace record not found',
        'chat.trace_id_required': 'Missing trace_id parameter',
        'chat.health_failed': 'Health check failed',
        'chat.simulate_failed': 'Simulated chat service error: {}',
        'chat.service_error': 'Chat service error: {}',
        'chat.trace_get_failed': 'Failed to get trace info: {}',
        'chat.feedback_failed': 'Failed to process feedback: {}',
        'chat.rules_get_failed': 'Failed to get rules list: {}',
        'chat.rule_create_failed': 'Failed to create rule: {}',
        'chat.rule_delete_failed': 'Failed to delete rule: {}',
        'chat.rule_effectiveness_failed': 'Failed to get rule effectiveness: {}',
        'chat.rule_logs_failed': 'Failed to get rule logs: {}',
        'chat.history_get_failed': 'Failed to get conversation history: {}',
        'chat.stats_get_failed': 'Failed to get conversation stats: {}',
        'chat.conversation_not_found': 'Conversation not found',
        'chat.conversation_delete_failed': 'Failed to delete conversation: {}',

        # ===== A/B Test =====
        'abtest.min_variants': 'At least 2 variants are required',
        'abtest.max_variants': 'Maximum 5 variants supported',
        'abtest.failed': 'A/B test service error: {}',
        'abtest.batch_failed': 'Batch A/B test service error: {}',

        # ===== Evaluation =====
        'eval.failed': 'Evaluation failed',
        'eval.not_found': 'Evaluation record not found',
        'eval.batch_failed': 'Batch evaluation failed',
        'eval.report_generate_failed': 'Failed to generate evaluation report',
        'eval.report_not_found': 'Report not found: {}',
        'eval.report_get_failed': 'Failed to get evaluation report',
        'eval.report_list_failed': 'Failed to get evaluation report list',
        'eval.metrics_get_failed': 'Failed to get metric definitions',
        'eval.report_generate_success': 'Evaluation report generated, ID: {}',

        # ===== Scenario =====
        'scenario.not_found': 'Scenario not found',
        'scenario.create_success': 'Scenario created successfully',
        'scenario.update_success': 'Scenario updated successfully',
        'scenario.delete_success': 'Scenario deleted successfully',
        'scenario.list_failed': 'Failed to get scenario list',
        'scenario.detail_failed': 'Failed to get scenario details',
        'scenario.params_failed': 'Failed to get scenario params',
        'scenario.invalid_id': 'Invalid scenario ID',
        'scenario.config_valid': 'Scenario configuration is valid',
        'scenario.config_warning': 'Scenario configuration has warnings',
        'scenario.validate_failed': 'Failed to validate scenario configuration',

        # ===== Monitor =====
        'monitor.failed': 'Failed to get monitoring data',
        'monitor.stats_failed': 'Failed to get performance stats',
        'monitor.logs_query_failed': 'Failed to query logs',
        'monitor.tasks_failed': 'Failed to get task list',
        'monitor.task_not_found': 'Task not found',
        'monitor.task_detail_failed': 'Failed to get task details',
        'monitor.stats_reset_done': 'Stats reset successfully',
        'monitor.stats_reset_failed': 'Failed to reset stats',

        # ===== API Common Extended =====
        'api.server_error': 'Internal server error',
        'api.rate_limit_exceeded': 'Too many requests, limit is {} per minute. Please try again later.',
        'api.app_description': 'Enterprise lightweight private RAG system',
        'api.app_starting': 'Starting {} v{}',
        'api.app_env': 'Environment: {}',
        'api.llm_ready': 'LLM service ready, model: {}',
        'api.llm_unavailable_detail': 'LLM service unavailable: {}',
        'api.pipeline_init_failed': 'Failed to initialize LLM pipeline: {}',
        'api.scheduler_init_failed': 'Failed to initialize scheduler: {}',
        'api.app_started': 'Application started',
        'api.app_shutdown': 'Application shutting down...',
        'api.app_shutdown_done': 'Application stopped',
        'api.request_log': 'Request: {} {}',
        'api.request_error': 'Request error: {} {} - {}',
        'api.uncaught_exception': 'Uncaught exception: {}',
        'api.health_tag': 'System',

        # ===== Core - Document Parser =====
        'parser.file_not_found': 'File not found',
        'parser.unsupported_format': 'Unsupported file format: {}',
        'parser.file_too_large': 'File too large: {}MB, max {}MB supported',
        'parser.dir_not_found': 'Directory not found: {}',

        # ===== Core - LLM Adapter =====
        'llm.unsupported_provider': 'Unsupported LLM provider: {}',

        # ===== Core - Retriever =====
        'retrieve.kb_not_found': 'Knowledge base not found: {}',
        'retrieve.invalid_mode': 'Invalid retrieval mode: {}',

        # ===== Core - Rule Engine =====
        'rule.not_found': 'Rule not found',

        # ===== Core - Vector Store =====
        'vector.kb_not_found': 'Knowledge base not found: {}',

        # ===== Tool - Log Analyzer =====
        'log_analyzer.method_not_implemented': 'Subclass must implement parse method',
        'log_analyzer.no_data': 'No data',

        # ===== Core - LLM Pipeline =====
        'pipeline.no_results': 'Sorry, no relevant information found.',
        'pipeline.kb_no_content': 'No relevant information found in the current knowledge base.',
        'pipeline.llm_supplement': 'The following content is generated by the LLM for reference only:',
        'pipeline.quality_low': 'Low retrieval quality (avg score {} ≤ 0.4), enabling hybrid answer mode',
        'pipeline.llm_unavailable': 'Sorry, the AI model service is temporarily unavailable. Please try again later.',
        'pipeline.hybrid_disclaimer': 'No relevant information found in the current knowledge base.\n\nThe following content is from the LLM\'s own knowledge and is for reference only:\n\n',
        'pipeline.empty_response': 'Sorry, no relevant information found.',
        'pipeline.circuit_busy': 'Sorry, AI service is temporarily busy, please try again later.',
        'pipeline.llm_error': 'Sorry, the AI model service is temporarily unavailable. Please try again later.',
        'pipeline.status_retrieving': 'Retrieving relevant documents...',
        'pipeline.status_generating': 'AI is generating answer...',
        'pipeline.status_boundary_rejected': 'This question is outside the business scope',
        'pipeline.boundary_rejected_msg': 'Sorry, your question is outside my business scope. Please consult the relevant department or ask a business-related question.',
        'pipeline.llm_exception': 'AI model service error: {}',
        'pipeline.citation_missing': 'No valid citation markers detected in the answer. Please ensure citation markers are used as [Document Number] format.',
        'pipeline.citation_invalid': '{} invalid citation marker(s) found: {}. Please ensure citation numbers match the reference document numbers.',
        'pipeline.citation_valid': 'Citation verification passed',
        'pipeline.unknown_doc': 'Unknown Document',
        'pipeline.system_prompt_default': (
            'You are a professional enterprise knowledge assistant. Please answer the user\'s question based on the provided reference documents.\n\n'
            '[Answer Rules]\n'
            '1. Answer only based on information in the reference documents, do not fabricate content\n'
            '2. If there is no relevant information in the reference documents, please state it clearly\n'
            '3. Answer should be clear, accurate, and well-organized\n'
            '4. [MANDATORY] You must use citation markers in the format [Document Number] in your answer, for example:\n'
            '   - "According to [Document 1], the return period is 7 days"\n'
            '   - "For product warranty policy, see [Document 2] and [Document 3]"\n'
            '5. Each key information point must be marked with the source document number\n'
            '6. If information comes from multiple documents, all must be marked\n'
            '7. Do not directly copy large sections of document content; summarize and paraphrase appropriately\n'
            '8. [MANDATORY] For numerical values in documents (such as dimensions, temperature, speed, tolerances, etc.), you must maintain the exact precision as the original text. Do not round or approximate.'
        ),
        'pipeline.system_prompt_hybrid': (
            'You are a professional enterprise knowledge assistant. After retrieval, no information directly relevant to the user\'s question was found in the current knowledge base.\n\n'
            '[Answer Rules - Must be strictly followed]\n'
            '1. The first paragraph must start with: "No relevant information found in the current knowledge base."\n'
            '2. The second paragraph must start with: "The following content is from the LLM\'s own knowledge and is for reference only:", then provide your answer based on your own knowledge\n'
            '3. Never use any citation markers (such as [1], [Document 1], etc.) because there are no relevant documents in the knowledge base\n'
            '4. Answer should be clear, accurate, and well-organized\n'
            '5. If your knowledge cannot answer either, please only say "Sorry, I cannot answer this question either."'
        ),

        # ===== Core - Boundary =====
        'boundary.keyword_blacklist': 'Hit blacklist keyword',
        'boundary.keyword_whitelist': 'Hit whitelist keyword',
        'boundary.retrieval_empty': 'No relevant documents retrieved',
        'boundary.retrieval_low_score': 'Low average retrieval similarity ({})',
        'boundary.disabled': 'Boundary detection disabled',
        'boundary.default_in_domain': 'No boundary rule matched, defaulting to in-domain',
        'boundary.rejection_default': 'Sorry, your question is outside my business scope. Please consult the relevant department or ask a business-related question.',
        'boundary.rejection_empty': 'Sorry, no relevant information found in the knowledge base. Please try a different question or upload relevant documents.',

        # ===== Core - Sentence Tracing =====
        'trace.confidence_direct_quote': 'Direct Quote',
        'trace.confidence_summary': 'Summary',
        'trace.confidence_low': 'Low Confidence',
        'trace.confidence_drift': 'Unsubstantiated',
        'trace.confidence_no_source': 'No Source',

        # ===== Core - Intent =====
        'intent.other': 'Other',
        'intent.cannot_identify': 'Unable to identify specific business intent',
        'intent.interpretation_fallback': 'System busy, using basic intent recognition',
        'intent.business_context_fallback': 'LLM intent analysis unavailable, falling back to keyword matching',
        'intent.business_context_prefix': 'User asking about {}',

        # ===== Core - Evaluator =====
        'eval.boundary_low': 'Low boundary detection confidence ({}), overall score adjusted from {} to {}',
        'eval.boundary_low_confidence': 'Low boundary confidence ({}), score adjusted',
        'eval.retrieval_quality_low': 'Low retrieval quality, score capped',

        # ===== Core - Sentence Tracing =====
        'trace.drift_embedding_failed': 'Failed to get sentence embedding',
        'trace.drift_no_source': 'No relevant source found',
        'trace.drift_low_similarity': 'Low similarity ({})',
        'trace.citation_direct_quote': 'Direct Quote',
        'trace.citation_summary': 'Summary Rewrite',
        'trace.citation_low_confidence': 'Low Confidence',
        'trace.citation_drift': 'Unsubstantiated Inference',
        'trace.citation_no_source': 'No Source',
        'trace.citation_unknown': 'Unknown',

        # ===== Core - Recall Diagnostic =====
        'recall.meta_filter_title': 'Metadata Filter Analysis',
        'recall.score_threshold_title': 'Score Threshold Analysis',
        'recall.retrieval_mode_title': 'Retrieval Mode Difference Analysis',
        'recall.keyword_missing_title': 'Missing Keyword Analysis',
        'recall.potential_miss_title': 'Potentially Relevant Documents',
        'recall.potential_miss_desc': 'Found {} potentially relevant but unretrieved documents',
        'recall.kb_not_found': 'Knowledge base not found',
        'recall.cannot_extract_keywords': 'Cannot extract query keywords',
        'recall.filtered_docs_summary': 'Knowledge base has {} documents, of which {} may be filtered out due to metadata mismatch ({:.1f}%)',
        'recall.near_threshold_summary': '{} documents have scores near the threshold (within 0.1 below), possibly not retrieved due to strict threshold',
        'recall.bm25_only': 'Only BM25 hit, vector retrieval missed',
        'recall.vector_only': 'Only vector retrieval hit, BM25 missed',
        'recall.mode_diff_summary': 'BM25 unique hits {}, vector unique hits {}, both hit but ultimately not retrieved {}',
        'recall.missing_keywords_summary': 'Detected {} potentially missing domain keywords, suggested additions: {}',
        'recall.no_issues': 'No obvious issues found',
        'recall.root_cause_embedding': 'Embedding mismatch',
        'recall.root_cause_threshold': 'Threshold boundary',
        'recall.root_cause_keyword': 'Missing keywords',
        'recall.root_cause_partial': 'Partial match',
    }
}


# Default language
_DEFAULT_LANG = 'zh-CN'


def get_lang_from_request(request: Optional[Request] = None, explicit_lang: Optional[str] = None) -> str:
    """
    Get language preference from request

    Priority:
    1. Explicitly passed lang parameter
    2. Query parameter lang
    3. Accept-Language request header
    4. Default zh-CN

    Args:
        request: FastAPI Request object
        explicit_lang: Explicitly specified language

    Returns:
        Language code 'zh-CN' or 'en-US'
    """
    if explicit_lang and explicit_lang in _TRANSLATIONS:
        return explicit_lang

    if request:
        # Query parameter
        lang_param = request.query_params.get('lang')
        if lang_param and lang_param in _TRANSLATIONS:
            return lang_param

        # Accept-Language header
        accept_lang = request.headers.get('accept-language', '')
        if accept_lang:
            # Simple parsing of Accept-Language header
            # Example: "zh-CN,zh;q=0.9,en;q=0.8" or "en-US,en;q=0.9"
            for part in accept_lang.split(','):
                lang_code = part.strip().split(';')[0].strip()
                # Exact match
                if lang_code in _TRANSLATIONS:
                    return lang_code
                # Prefix match (zh -> zh-CN, en -> en-US)
                if lang_code.lower().startswith('zh'):
                    return 'zh-CN'
                if lang_code.lower().startswith('en'):
                    return 'en-US'

    return _DEFAULT_LANG


def _(key: str, lang: Optional[str] = None, *args) -> str:
    """
    Translation function

    Args:
        key: Translation key
        lang: Language code, default zh-CN
        *args: Format parameters (for {} placeholders)

    Returns:
        Translated text

    Examples:
        _('kb.not_found')                          # Knowledge base not found
        _('kb.not_found', 'en-US')                 # Knowledge base not found
        _('api.internal_error', None, 'timeout')   # Service error: timeout
    """
    l = lang if lang and lang in _TRANSLATIONS else _DEFAULT_LANG
    text = _TRANSLATIONS[l].get(key) or _TRANSLATIONS[_DEFAULT_LANG].get(key) or key

    # Support format parameters
    if args:
        try:
            text = text.format(*args)
        except (IndexError, KeyError):
            pass

    return text


def get_available_langs() -> list:
    """Get list of supported languages"""
    return list(_TRANSLATIONS.keys())
