/**
 * whiteBoxRAG 前端国际化模块
 * 支持中文(zh-CN)和英文(en-US)两种语言
 *
 * 使用方式：
 * 1. HTML 静态文本：添加 data-i18n="key" 属性，applyI18n() 自动替换
 * 2. JS 动态文本：使用 t('key') 获取翻译
 * 3. 语言切换：调用 setLang('en-US') 或 setLang('zh-CN')
 * 4. 持久化：语言选择保存在 localStorage['lang']
 */
(function (global) {
    'use strict';

    // ==================== 翻译字典 ====================
    const translations = {
        'zh-CN': {
            // ===== 通用 =====
            'common.loading': '加载中...',
            'common.confirm': '确认',
            'common.cancel': '取消',
            'common.delete': '删除',
            'common.save': '保存',
            'common.search': '搜索',
            'common.upload': '上传',
            'common.close': '关闭',
            'common.back': '返回',
            'common.refresh': '刷新',
            'common.success': '操作成功',
            'common.failed': '操作失败',
            'common.no_data': '暂无数据',
            'common.default': '默认',
            'common.yes': '是',
            'common.no': '否',
            'common.create': '创建',
            'common.toggle': '切换',
            'common.theme_toggle': '切换浅色/深色主题',
            'common.lang_toggle': '切换语言',
            'common.docs': '文档',
            'common.chunks': '分块',
            'common.page_first': '首页',
            'common.page_prev': '上一页',
            'common.page_next': '下一页',
            'common.page_last': '末页',

            // ===== ab_test 进度步骤与通用指标 =====
            'common.init_test_env': '初始化测试环境',
            'common.execute_variant': '执行变体',
            'common.default_retrieval_mode': '混合检索',
            'common.analyze_comparison_results': '分析对比结果',
            'common.generate_report': '生成报告',
            'common.test_completed': '测试完成！',
            'common.test_failed': '测试失败',
            'common.start_test': '开始测试',
            'common.select_knowledge_base': '请选择知识库',
            'common.enter_test_queries': '请输入至少一条测试问句',
            'common.at_least_two_variants': '至少需要2个变体',
            'common.batch_test_running': '批量测试进行中...',
            'common.initialize_batch_test_environment': '初始化批量测试环境',
            'common.execute_query': '执行问句',
            'common.aggregate_metrics_and_generate_report': '汇总指标均值并生成报告',
            'common.batch_test_completed': '批量测试完成！',
            'common.batch_test_failed': '批量测试失败',
            'common.test_type': '测试类型',
            'common.batch_test': '批量测试',
            'common.knowledge_id': '知识库ID',
            'common.question_count': '问句数',
            'common.variant_count': '变体数',
            'common.variant': '变体',
            'common.avg_eval_score': '平均评估分',
            'common.avg_recall_count': '平均召回数',
            'common.avg_drift_rate': '平均漂移率',
            'common.mark': '标记',
            'common.best_eval': '最佳评估',
            'common.best_recall': '最佳召回',
            'common.low_drift': '最低漂移',
            'common.eval': '评估',
            'common.recall': '召回',
            'common.drift': '漂移',
            'common.query': '查询',
            'common.kb_id': '知识库ID',
            'common.best_drift_rate': '最佳漂移率',
            'common.config': '配置',
            'common.duration': '耗时',
            'common.eval_score': '评估分',
            'common.recall_count': '召回数',
            'common.drift_rate': '漂移率',
            'common.config_details': '配置详情',
            'common.bm25_weight': 'BM25权重',
            'common.similarity_threshold': '相似度阈值',
            'common.top_k': '返回数量',
            'common.contradiction_count': '矛盾数',
            'common.answer': '回答',
            'common.retrieved_documents': '召回文档',
            'common.unknown_file_name': '未知文件',
            'common.avg_answer_length': '平均回答长度',
            'common.avg_evaluation_score': '平均评估分',

            // ===== index.html - 顶部导航 =====
            'nav.title': 'whiteBoxRAG - 白盒企业私有化RAG系统',
            'nav.subtitle': '白盒企业私有化RAG系统 · 内置 RAG 调试台',
            'nav.admin': '管理端',
            'nav.trace': '查看溯源',
            'nav.history': '历史记录',
            'nav.ab_test': 'A/B对比测试',

            // ===== index.html - 知识库选择 =====
            'kb.select_placeholder': '请选择知识库',
            'kb.no_selection': '未选择知识库',
            'kb.create': '创建知识库',
            'kb.name': '知识库名称',
            'kb.desc': '知识库描述',
            'kb.scenario': '应用场景',
            'kb.doc_count': '文档数',
            'kb.chunk_count': '分块数',
            'kb.created_at': '创建时间',
            'kb.actions': '操作',
            'kb.delete_confirm': '确定删除此知识库？所有文档和向量数据将被清除。',
            'kb.delete_success': '知识库删除成功',
            'kb.create_success': '知识库创建成功',
            'kb.name_required': '请输入知识库名称',
            'kb.list': '知识库列表',
            'kb.empty': '暂无知识库',
            'kb.empty_hint': '点击上方 + 创建知识库',
            'kb.dropzone': '拖拽文档或点击上传',
            'kb.supported_formats': '支持 PDF/Word/Excel/PPT/TXT',
            'kb.uploading': '上传中...',
            'kb.name_label': '知识库名称 *',
            'kb.name_placeholder': '例如：产品文档知识库',
            'kb.desc_label': '描述（可选）',
            'kb.desc_placeholder': '简要描述知识库的用途...',
            'kb.create_dialog_title': '创建知识库',

            // ===== index.html - 对话区域 =====
            'chat.placeholder': '输入您的问题，按Enter发送，Shift+Enter换行...',
            'chat.send': '发送',
            'chat.clear': '清空对话',
            'chat.welcome': '欢迎使用 whiteBoxRAG',
            'chat.welcome_desc': '基于本地Ollama大语言模型的企业级RAG系统，支持私有化部署',
            'chat.quick_start': '快速开始',
            'chat.step1': '在左侧创建或选择一个知识库',
            'chat.step2': '上传PDF/Word等格式的文档',
            'chat.step3': '在下方输入问题开始对话',
            'chat.thinking': '正在思考...',
            'chat.no_kb': '请先选择知识库',
            'chat.error': '请求失败，请重试',
            'chat.copy': '复制',
            'chat.copied': '已复制',
            'chat.regenerate': '重新生成',
            'chat.stop': '停止生成',

            // ===== Chunk Settings =====
            'chunk.advanced': '高级分块设置',
            'chunk.size': '分块大小（字符）',
            'chunk.overlap': '分块重叠（字符）',
            'chunk.hint': '留空则使用场景/默认配置；自定义参数仅对本次上传生效',

            // ===== System =====
            'system.llm_status': 'LLM状态',
            'system.checking': '检测中',
            'system.monitor': '查看系统监控',

            // ===== index.html - 检索溯源面板 =====
            'trace.title': '检索溯源',
            'trace.tab_business': '业务摘要',
            'trace.tab_technical': '技术详情',
            'trace.conclusion': '知识库匹配结论',
            'trace.query': '查询',
            'trace.match_status': '匹配状态',
            'trace.match_good': '匹配良好',
            'trace.match_low': '低置信度',
            'trace.match_none': '知识库无相关内容',
            'trace.process_method': '处理方式',
            'trace.process_kb': '基于知识库内容回答',
            'trace.process_llm': '已启用大模型补充回答，内容仅供参考',
            'trace.risk_level': 'AI回答风险',
            'trace.risk_normal': '正常',
            'trace.risk_low': '低置信',
            'trace.risk_high': '存在幻觉风险',
            'trace.intent': '识别业务意图',
            'trace.confidence': '置信度',
            'trace.overall_score': '综合评分',
            'trace.one_line_summary': '一句话总结',
            'trace.reference_docs': '参考资料召回汇总',
            'trace.sentence_trace': 'AI逐句可信度溯源',
            'trace.sentence_supported': '有文档依据',
            'trace.sentence_low_confidence': '低置信度',
            'trace.sentence_hallucination': '无依据推断（幻觉风险）',
            'trace.diagnosis': '智能诊断 & 优化建议',
            'trace.quality_metrics': '质量指标',
            'trace.recall_quality': '召回质量',
            'trace.answer_relevance': '答案相关性',
            'trace.faithfulness': '内容忠实度',
            'trace.hallucination_risk': '幻觉风险比例',
            'trace.technical_details': '技术详情',
            'trace.retrieval_params': '检索参数',
            'trace.query_rewrite': '查询改写',
            'trace.original_query': '原始查询',
            'trace.rewritten_query': '改写后查询',
            'trace.bm25_results': 'BM25原始召回',
            'trace.vector_results': '向量原始召回',
            'trace.merged_results': '融合后结果',
            'trace.evaluation_metrics': '评估指标',
            'trace.no_trace': '暂无溯源数据',
            'trace.collapse': '收起',
            'trace.expand': '展开',
            'trace.summary_overview': '本次问答总览',
            'trace.no_data_yet': '暂无数据，开始对话后展示',
            'trace.no_suggestions': '暂无建议',
            'trace.expand_metrics': '展开查看详细质量指标',
            'trace.retrieval_chain': '检索链路',
            'trace.retrieval_process': '检索过程',
            'trace.no_trace_hint': '开始对话后显示检索文档',
            'trace.generation_chain': '生成溯源链路',
            'trace.evaluation_and_diagnosis': '评估与诊断',
            'trace.query_label': '查询：',
            'trace.kb_label': '知识库：',
            'trace.evaluation': '评估与诊断',
            'trace.eval_metrics': '评估指标',
            'trace.start_conversation': '开始对话后显示检索文档',

            // ===== admin.html - 导航 =====
            'admin.title': 'whiteBoxRAG - 知识库管理后台',
            'admin.tab_files': '文件管理',
            'admin.tab_chunks': '文档分块',
            'admin.tab_vectors': '向量数据',
            'admin.tab_kb_settings': '知识库设置',
            'admin.tab_system': '系统状态',

            // ===== admin.html - 文件管理 =====
            'admin.upload': '上传文档',
            'admin.upload_area': '点击或拖拽文件到此处上传',
            'admin.select_file': '请选择文件',
            'admin.upload_hint': '支持 PDF, Word, TXT, Markdown 格式',
            'admin.file_name': '文件名',
            'admin.file_size': '文件大小',
            'admin.file_status': '状态',
            'admin.file_actions': '操作',
            'admin.file_preview': '预览',
            'admin.file_delete': '删除',
            'admin.file_reparse': '重新解析',
            'admin.advanced_chunking': '高级分块设置',
            'admin.chunk_size': '分块大小',
            'admin.chunk_overlap': '分块重叠',
            'admin.upload_success': '文档上传成功',
            'admin.upload_failed': '文档上传失败',
            'admin.delete_confirm': '确定删除此文档？',
            'admin.no_files': '暂无文档',
            'admin.processing': '处理中',
            'admin.ready': '就绪',
            'admin.failed': '失败',

            // ===== admin.html - 文档分块 =====
            'admin.no_chunks': '暂无分块数据',
            'admin.chunk_id': '分块ID',
            'admin.chunk_text': '分块内容',
            'admin.chunk_metadata': '元数据',
            'admin.view_vector': '查看向量',

            // ===== admin.html - 向量数据 =====
            'admin.no_vector': '选择一个分块查看向量详情',
            'admin.vector_dimension': '向量维度',
            'admin.vector_preview': '向量预览',
            'admin.vector_stats': '向量统计',
            'admin.vector_mean': '平均值',
            'admin.vector_min': '最小值',
            'admin.vector_max': '最大值',

            // ===== admin.html - 知识库设置 =====
            'admin.kb_settings': '知识库设置',
            'admin.kb_name': '知识库名称',
            'admin.kb_desc': '知识库描述',
            'admin.kb_scenario': '应用场景',
            'admin.kb_embedding_model': 'Embedding模型',
            'admin.save_settings': '保存设置',
            'admin.settings_saved': '设置已保存',

            // ===== admin.html - 系统状态 =====
            'admin.system_status': '系统状态',
            'admin.llm_status': 'LLM服务状态',
            'admin.embedding_status': 'Embedding服务状态',
            'admin.model_name': '模型名称',
            'admin.available': '可用',
            'admin.unavailable': '不可用',
            'admin.available_models': '可用模型列表',
            'admin.create_kb': '新建知识库',
            'admin.system_running': '🟢 运行正常',
            'admin.kb_subtitle': '查看知识库详情、文档和向量数据',
            'admin.raw_docs': '原始文档列表',
            'admin.chunk_list': '文档分块列表',
            'admin.vector_details': '向量数据详情',
            'admin.raw_doc_content': '原始文档内容',

            // ===== Monitor =====
            'monitor.title': '系统监控',
            'monitor.total_requests': '总请求数',
            'monitor.error_rate': '错误率',
            'monitor.avg_response': '平均响应',
            'monitor.running_tasks': '运行任务',
            'monitor.request_stats': '请求统计',
            'monitor.recent_logs': '最近日志',
            'monitor.total_requests_short': '总请求:',
            'monitor.error_rate_short': '错误率:',
            'monitor.avg_response_short': '平均响应:',
            'monitor.service_ok': '服务正常',

            // ===== Intent Feedback =====
            'intent_feedback.title': '纠正意图识别',
            'intent_feedback.current': '当前识别结果',
            'intent_feedback.correct': '正确的意图类型',
            'intent_feedback.placeholder': '例如：售后-退货流程',
            'intent_feedback.reason': '纠正原因（可选）',
            'intent_feedback.reason_placeholder': '请简要说明为什么识别错误...',
            'intent_feedback.submit': '提交纠正',
            'intent_feedback.current_result': '当前识别结果',
            'intent_feedback.correct_intent': '正确的意图类型',
            'intent_feedback.intent_placeholder': '例如：售后-退货流程',

            // ===== File Manager =====
            'file_manager.title': '📁 文件管理',
            'file_manager.current_kb': '当前知识库:',
            'file_manager.no_files': '暂无文件',
            'file_manager.upload_hint': '点击下方按钮上传文件',
            'file_manager.upload_file': '上传文件',
            'file_manager.total_files': '共 {count} 个文件',

            // ===== ab_test.html =====
            'abtest.title': 'A/B对比测试',
            'abtest.back_home': '← 返回主页',
            'abtest.config': '测试配置',
            'abtest.mode': '测试模式：',
            'abtest.mode_single': '单条测试',
            'abtest.mode_batch': '批量测试',
            'abtest.batch_hint': '支持多问句批量运行全部变体，输出指标均值',
            'abtest.kb': '知识库',
            'abtest.kb_placeholder': '请选择知识库',
            'abtest.query': '测试查询',
            'abtest.query_placeholder': '输入要测试的问题...',
            'abtest.batch_label': '测试问句集（每行一条，最多50条）',
            'abtest.batch_placeholder': '请输入测试问句，每行一条\n例如：\n产品有哪些功能？\n如何申请退款？\n联系方式是什么？',
            'abtest.batch_count': '当前问句数：',
            'abtest.file_import': '从文件导入',
            'abtest.file_hint': '点击上传 .txt / .csv 文件',
            'abtest.file_hint2': '每行一条问句',
            'abtest.variants': '变体配置（至少2个）',
            'abtest.add_variant': '添加变体',
            'abtest.start': '开始测试',
            'abtest.testing': '测试进行中...',
            'abtest.batch_testing': '批量测试进行中...',
            'abtest.progress': '测试进度',
            'abtest.results': '📊 测试结果',
            'abtest.print': '🖨️ 打印报告',
            'abtest.variant_name': '变体名称',
            'abtest.retrieval_mode': '检索模式',
            'abtest.bm25_weight': 'BM25权重',
            'abtest.similarity_threshold': '相似度阈值',
            'abtest.top_k': '返回数量',
            'abtest.delete_variant': '删除',
            'abtest.mode_hybrid': '混合检索',
            'abtest.mode_vector': '向量检索',
            'abtest.mode_bm25': 'BM25检索',
            'abtest.alert_min_variants': '至少需要2个变体',
            'abtest.alert_select_kb': '请选择知识库',
            'abtest.alert_enter_query': '请输入测试查询',
            'abtest.alert_enter_queries': '请输入至少一条测试问句',
            'abtest.alert_file_too_large': '文件过大，请确保小于 1MB',
            'abtest.alert_file_read_failed': '文件读取失败',
            'abtest.alert_test_failed': '测试失败',
            'abtest.alert_batch_failed': '批量测试失败',
            'abtest.step_init': '初始化测试环境',
            'abtest.step_analyze': '分析对比结果',
            'abtest.step_report': '生成报告',
            'abtest.step_batch_init': '初始化批量测试环境',
            'abtest.step_batch_summary': '汇总指标均值并生成报告',
            'abtest.done': '测试完成！',
            'abtest.batch_done': '批量测试完成！',
            'abtest.test_type': '测试类型',
            'abtest.batch_test': '批量测试',
            'abtest.single_test': '单条测试',
            'abtest.query_label': '查询：',
            'abtest.kb_label': '知识库：',
            'abtest.best_eval': '最佳评估：',
            'abtest.best_recall': '最佳召回：',
            'abtest.best_drift': '最佳漂移率：',
            'abtest.variant_config': '变体配置（至少2个）',
            'abtest.knowledge_base': '知识库',
            'abtest.test_query': '测试查询',
            'abtest.variant_a': '变体A',
            'abtest.variant_b': '变体B',
            'abtest.hybrid': '混合检索',
            'abtest.vector': '向量检索',
            'abtest.bm25': 'BM25检索',
            'abtest.result': '📊 A/B测试结果',
            'abtest.view_trace': '🔍 查看溯源',
            'abtest.stats_summary': '📈 统计摘要',
            'abtest.variant_metrics': '📊 变体指标均值对比',

            // ===== 历史记录 =====
            'history.title': '历史记录',
            'history.empty': '暂无历史记录',
            'history.time': '时间',
            'history.query': '问题',
            'history.kb': '知识库',
            'history.view_trace': '查看溯源',
            'history.click_hint': '点击任意记录可查看完整溯源详情',
        },

        'en-US': {
            // ===== Common =====
            'common.loading': 'Loading...',
            'common.confirm': 'Confirm',
            'common.cancel': 'Cancel',
            'common.delete': 'Delete',
            'common.save': 'Save',
            'common.search': 'Search',
            'common.upload': 'Upload',
            'common.close': 'Close',
            'common.back': 'Back',
            'common.refresh': 'Refresh',
            'common.success': 'Success',
            'common.failed': 'Failed',
            'common.no_data': 'No data',
            'common.default': 'Default',
            'common.yes': 'Yes',
            'common.no': 'No',
            'common.create': 'Create',
            'common.toggle': 'Toggle',
            'common.theme_toggle': 'Toggle light/dark theme',
            'common.lang_toggle': 'Switch language',
            'common.docs': 'Docs',
            'common.chunks': 'Chunks',
            'common.page_first': 'First',
            'common.page_prev': 'Prev',
            'common.page_next': 'Next',
            'common.page_last': 'Last',

            // ===== ab_test progress steps and common metrics =====
            'common.init_test_env': 'Initializing test environment',
            'common.execute_variant': 'Executing variant',
            'common.default_retrieval_mode': 'Hybrid',
            'common.analyze_comparison_results': 'Analyzing comparison results',
            'common.generate_report': 'Generating report',
            'common.test_completed': 'Test complete!',
            'common.test_failed': 'Test failed',
            'common.start_test': 'Start Test',
            'common.select_knowledge_base': 'Please select a knowledge base',
            'common.enter_test_queries': 'Please enter at least one test query',
            'common.at_least_two_variants': 'At least 2 variants are required',
            'common.batch_test_running': 'Batch testing...',
            'common.initialize_batch_test_environment': 'Initializing batch test environment',
            'common.execute_query': 'Executing query',
            'common.aggregate_metrics_and_generate_report': 'Summarizing average metrics and generating report',
            'common.batch_test_completed': 'Batch test complete!',
            'common.batch_test_failed': 'Batch test failed',
            'common.test_type': 'Test Type',
            'common.batch_test': 'Batch Test',
            'common.knowledge_id': 'Knowledge Base ID',
            'common.question_count': 'Query Count',
            'common.variant_count': 'Variant Count',
            'common.variant': 'Variant',
            'common.avg_eval_score': 'Avg Eval Score',
            'common.avg_recall_count': 'Avg Recall Count',
            'common.avg_drift_rate': 'Avg Drift Rate',
            'common.mark': 'Mark',
            'common.best_eval': 'Best Eval',
            'common.best_recall': 'Best Recall',
            'common.low_drift': 'Lowest Drift',
            'common.eval': 'Eval',
            'common.recall': 'Recall',
            'common.drift': 'Drift',
            'common.query': 'Query',
            'common.kb_id': 'KB ID',
            'common.best_drift_rate': 'Best Drift Rate',
            'common.config': 'Config',
            'common.duration': 'Duration',
            'common.eval_score': 'Eval Score',
            'common.recall_count': 'Recall Count',
            'common.drift_rate': 'Drift Rate',
            'common.config_details': 'Configuration Details',
            'common.bm25_weight': 'BM25 Weight',
            'common.similarity_threshold': 'Similarity Threshold',
            'common.top_k': 'Top K',
            'common.contradiction_count': 'Contradictions',
            'common.answer': 'Answer',
            'common.retrieved_documents': 'Retrieved Documents',
            'common.unknown_file_name': 'Unknown File',
            'common.avg_answer_length': 'Avg Answer Length',
            'common.avg_evaluation_score': 'Avg Evaluation Score',

            // ===== index.html - Navigation =====
            'nav.title': 'whiteBoxRAG - White-box enterprise RAG with a built-in RAG debugger',
            'nav.subtitle': 'White-box RAG system with a built-in RAG debugger',
            'nav.admin': 'Admin',
            'nav.trace': 'View Trace',
            'nav.history': 'History',
            'nav.ab_test': 'A/B Test',

            // ===== index.html - Knowledge Base =====
            'kb.select_placeholder': 'Select a knowledge base',
            'kb.no_selection': 'No knowledge base selected',
            'kb.create': 'Create Knowledge Base',
            'kb.name': 'Knowledge Base Name',
            'kb.desc': 'Description',
            'kb.scenario': 'Scenario',
            'kb.doc_count': 'Documents',
            'kb.chunk_count': 'Chunks',
            'kb.created_at': 'Created At',
            'kb.actions': 'Actions',
            'kb.delete_confirm': 'Are you sure you want to delete this knowledge base? All documents and vector data will be removed.',
            'kb.delete_success': 'Knowledge base deleted successfully',
            'kb.create_success': 'Knowledge base created successfully',
            'kb.name_required': 'Please enter a knowledge base name',
            'kb.list': 'Knowledge Base List',
            'kb.empty': 'No knowledge bases yet',
            'kb.empty_hint': 'Click + above to create one',
            'kb.dropzone': 'Drag documents or click to upload',
            'kb.supported_formats': 'Supports PDF/Word/Excel/PPT/TXT',
            'kb.uploading': 'Uploading...',
            'kb.name_label': 'KB Name *',
            'kb.name_placeholder': 'e.g.: Product Documentation KB',
            'kb.desc_label': 'Description (optional)',
            'kb.desc_placeholder': 'Briefly describe the KB purpose...',
            'kb.create_dialog_title': 'Create Knowledge Base',

            // ===== index.html - Chat =====
            'chat.placeholder': 'Enter your question, Enter to send, Shift+Enter for new line...',
            'chat.send': 'Send',
            'chat.clear': 'Clear chat',
            'chat.welcome': 'Welcome to whiteBoxRAG',
            'chat.welcome_desc': 'Enterprise-grade RAG system based on local Ollama LLM, supporting private deployment',
            'chat.quick_start': 'Quick Start',
            'chat.step1': 'Create or select a knowledge base on the left',
            'chat.step2': 'Upload PDF/Word documents',
            'chat.step3': 'Enter your question below to start chatting',
            'chat.thinking': 'Thinking...',
            'chat.no_kb': 'Please select a knowledge base first',
            'chat.error': 'Request failed, please retry',
            'chat.copy': 'Copy',
            'chat.copied': 'Copied',
            'chat.regenerate': 'Regenerate',
            'chat.stop': 'Stop generating',

            // ===== Chunk Settings =====
            'chunk.advanced': 'Advanced Chunking Settings',
            'chunk.size': 'Chunk Size (chars)',
            'chunk.overlap': 'Chunk Overlap (chars)',
            'chunk.hint': 'Leave empty to use scenario/default config; custom parameters only apply to this upload',

            // ===== System =====
            'system.llm_status': 'LLM Status',
            'system.checking': 'Checking',
            'system.monitor': 'View System Monitor',

            // ===== index.html - Trace Panel =====
            'trace.title': 'Retrieval Trace',
            'trace.tab_business': 'Business Summary',
            'trace.tab_technical': 'Technical Details',
            'trace.conclusion': 'Knowledge Base Match Conclusion',
            'trace.query': 'Query',
            'trace.match_status': 'Match Status',
            'trace.match_good': 'Good Match',
            'trace.match_low': 'Low Confidence',
            'trace.match_none': 'No Relevant Content in Knowledge Base',
            'trace.process_method': 'Processing Method',
            'trace.process_kb': 'Answered based on knowledge base content',
            'trace.process_llm': 'LLM supplementary answer enabled, content is for reference only',
            'trace.risk_level': 'AI Answer Risk',
            'trace.risk_normal': 'Normal',
            'trace.risk_low': 'Low Confidence',
            'trace.risk_high': 'Hallucination Risk',
            'trace.intent': 'Detected Business Intent',
            'trace.confidence': 'Confidence',
            'trace.overall_score': 'Overall Score',
            'trace.one_line_summary': 'Summary',
            'trace.reference_docs': 'Reference Documents',
            'trace.sentence_trace': 'AI Sentence-level Traceability',
            'trace.sentence_supported': 'Documented',
            'trace.sentence_low_confidence': 'Low Confidence',
            'trace.sentence_hallucination': 'Unsubstantiated (Hallucination Risk)',
            'trace.diagnosis': 'Diagnosis & Optimization Suggestions',
            'trace.quality_metrics': 'Quality Metrics',
            'trace.recall_quality': 'Recall Quality',
            'trace.answer_relevance': 'Answer Relevance',
            'trace.faithfulness': 'Faithfulness',
            'trace.hallucination_risk': 'Hallucination Risk',
            'trace.technical_details': 'Technical Details',
            'trace.retrieval_params': 'Retrieval Parameters',
            'trace.query_rewrite': 'Query Rewrite',
            'trace.original_query': 'Original Query',
            'trace.rewritten_query': 'Rewritten Query',
            'trace.bm25_results': 'BM25 Raw Results',
            'trace.vector_results': 'Vector Raw Results',
            'trace.merged_results': 'Merged Results',
            'trace.evaluation_metrics': 'Evaluation Metrics',
            'trace.no_trace': 'No trace data available',
            'trace.collapse': 'Collapse',
            'trace.expand': 'Expand',
            'trace.summary_overview': 'Session Overview',
            'trace.no_data_yet': 'No data yet. Start a conversation to see details',
            'trace.no_suggestions': 'No suggestions yet',
            'trace.expand_metrics': 'Expand to view detailed quality metrics',
            'trace.retrieval_chain': 'Retrieval Chain',
            'trace.retrieval_process': 'Retrieval Process',
            'trace.no_trace_hint': 'Start a conversation to see retrieved documents',
            'trace.generation_chain': 'Generation Trace',
            'trace.evaluation_and_diagnosis': 'Evaluation & Diagnosis',
            'trace.query_label': 'Query:',
            'trace.kb_label': 'Knowledge Base:',
            'trace.evaluation': 'Evaluation & Diagnosis',
            'trace.eval_metrics': 'Evaluation Metrics',
            'trace.start_conversation': 'Start a conversation to see retrieved documents',

            // ===== admin.html - Navigation =====
            'admin.title': 'whiteBoxRAG - Knowledge Base Admin',
            'admin.tab_files': 'Files',
            'admin.tab_chunks': 'Chunks',
            'admin.tab_vectors': 'Vectors',
            'admin.tab_kb_settings': 'KB Settings',
            'admin.tab_system': 'System',

            // ===== admin.html - File Management =====
            'admin.upload': 'Upload Document',
            'admin.upload_area': 'Click or drag file here to upload',
            'admin.select_file': 'Please select a file',
            'admin.upload_hint': 'Supports PDF, Word, TXT, Markdown',
            'admin.file_name': 'File Name',
            'admin.file_size': 'Size',
            'admin.file_status': 'Status',
            'admin.file_actions': 'Actions',
            'admin.file_preview': 'Preview',
            'admin.file_delete': 'Delete',
            'admin.file_reparse': 'Re-parse',
            'admin.advanced_chunking': 'Advanced Chunking Settings',
            'admin.chunk_size': 'Chunk Size',
            'admin.chunk_overlap': 'Chunk Overlap',
            'admin.upload_success': 'Document uploaded successfully',
            'admin.upload_failed': 'Document upload failed',
            'admin.delete_confirm': 'Are you sure you want to delete this document?',
            'admin.no_files': 'No documents',
            'admin.processing': 'Processing',
            'admin.ready': 'Ready',
            'admin.failed': 'Failed',

            // ===== admin.html - Chunks =====
            'admin.no_chunks': 'No chunk data',
            'admin.chunk_id': 'Chunk ID',
            'admin.chunk_text': 'Chunk Content',
            'admin.chunk_metadata': 'Metadata',
            'admin.view_vector': 'View Vector',

            // ===== admin.html - Vectors =====
            'admin.no_vector': 'Select a chunk to view vector details',
            'admin.vector_dimension': 'Vector Dimension',
            'admin.vector_preview': 'Vector Preview',
            'admin.vector_stats': 'Vector Statistics',
            'admin.vector_mean': 'Mean',
            'admin.vector_min': 'Min',
            'admin.vector_max': 'Max',

            // ===== admin.html - KB Settings =====
            'admin.kb_settings': 'Knowledge Base Settings',
            'admin.kb_name': 'Knowledge Base Name',
            'admin.kb_desc': 'Description',
            'admin.kb_scenario': 'Scenario',
            'admin.kb_embedding_model': 'Embedding Model',
            'admin.save_settings': 'Save Settings',
            'admin.settings_saved': 'Settings saved',

            // ===== admin.html - System Status =====
            'admin.system_status': 'System Status',
            'admin.llm_status': 'LLM Service Status',
            'admin.embedding_status': 'Embedding Service Status',
            'admin.model_name': 'Model Name',
            'admin.available': 'Available',
            'admin.unavailable': 'Unavailable',
            'admin.available_models': 'Available Models',
            'admin.create_kb': 'Create KB',
            'admin.system_running': '🟢 Running Normally',
            'admin.kb_subtitle': 'View KB details, docs and vector data',
            'admin.raw_docs': 'Original Documents',
            'admin.chunk_list': 'Chunk List',
            'admin.vector_details': 'Vector Details',
            'admin.raw_doc_content': 'Raw Document Content',

            // ===== Monitor =====
            'monitor.title': 'System Monitor',
            'monitor.total_requests': 'Total Requests',
            'monitor.error_rate': 'Error Rate',
            'monitor.avg_response': 'Avg Response',
            'monitor.running_tasks': 'Running Tasks',
            'monitor.request_stats': 'Request Statistics',
            'monitor.recent_logs': 'Recent Logs',
            'monitor.total_requests_short': 'Total:',
            'monitor.error_rate_short': 'Error:',
            'monitor.avg_response_short': 'Avg:',
            'monitor.service_ok': 'Service OK',

            // ===== Intent Feedback =====
            'intent_feedback.title': 'Correct Intent Recognition',
            'intent_feedback.current': 'Current Recognition',
            'intent_feedback.correct': 'Correct Intent Type',
            'intent_feedback.placeholder': 'e.g.: After-sales - Return Process',
            'intent_feedback.reason': 'Correction Reason (optional)',
            'intent_feedback.reason_placeholder': 'Briefly explain why it is wrong...',
            'intent_feedback.submit': 'Submit Correction',
            'intent_feedback.current_result': 'Current Recognition',
            'intent_feedback.correct_intent': 'Correct Intent Type',
            'intent_feedback.intent_placeholder': 'e.g.: After-sales - Return Process',

            // ===== File Manager =====
            'file_manager.title': '📁 File Manager',
            'file_manager.current_kb': 'Current KB:',
            'file_manager.no_files': 'No files yet',
            'file_manager.upload_hint': 'Click the button below to upload',
            'file_manager.upload_file': 'Upload File',
            'file_manager.total_files': 'Total {count} files',

            // ===== ab_test.html =====
            'abtest.title': 'A/B Test',
            'abtest.back_home': '← Back to Home',
            'abtest.config': 'Test Configuration',
            'abtest.mode': 'Mode: ',
            'abtest.mode_single': 'Single Test',
            'abtest.mode_batch': 'Batch Test',
            'abtest.batch_hint': 'Run all variants with multiple queries and output average metrics',
            'abtest.kb': 'Knowledge Base',
            'abtest.kb_placeholder': 'Select a knowledge base',
            'abtest.query': 'Test Query',
            'abtest.query_placeholder': 'Enter a question to test...',
            'abtest.batch_label': 'Test queries (one per line, max 50)',
            'abtest.batch_placeholder': 'Enter test queries, one per line\nExample:\nWhat features does the product have?\nHow to request a refund?\nWhat is the contact information?',
            'abtest.batch_count': 'Query count: ',
            'abtest.file_import': 'Import from file',
            'abtest.file_hint': 'Click to upload .txt / .csv file',
            'abtest.file_hint2': 'One query per line',
            'abtest.variants': 'Variants (at least 2)',
            'abtest.add_variant': 'Add Variant',
            'abtest.start': 'Start Test',
            'abtest.testing': 'Testing...',
            'abtest.batch_testing': 'Batch testing...',
            'abtest.progress': 'Progress',
            'abtest.results': '📊 Test Results',
            'abtest.print': '🖨️ Print Report',
            'abtest.variant_name': 'Variant Name',
            'abtest.retrieval_mode': 'Retrieval Mode',
            'abtest.bm25_weight': 'BM25 Weight',
            'abtest.similarity_threshold': 'Similarity Threshold',
            'abtest.top_k': 'Top K',
            'abtest.delete_variant': 'Delete',
            'abtest.mode_hybrid': 'Hybrid',
            'abtest.mode_vector': 'Vector',
            'abtest.mode_bm25': 'BM25',
            'abtest.alert_min_variants': 'At least 2 variants are required',
            'abtest.alert_select_kb': 'Please select a knowledge base',
            'abtest.alert_enter_query': 'Please enter a test query',
            'abtest.alert_enter_queries': 'Please enter at least one test query',
            'abtest.alert_file_too_large': 'File too large, please ensure it is under 1MB',
            'abtest.alert_file_read_failed': 'File read failed',
            'abtest.alert_test_failed': 'Test failed',
            'abtest.alert_batch_failed': 'Batch test failed',
            'abtest.step_init': 'Initializing test environment',
            'abtest.step_analyze': 'Analyzing comparison results',
            'abtest.step_report': 'Generating report',
            'abtest.step_batch_init': 'Initializing batch test environment',
            'abtest.step_batch_summary': 'Summarizing average metrics and generating report',
            'abtest.done': 'Test complete!',
            'abtest.batch_done': 'Batch test complete!',
            'abtest.test_type': 'Test Type',
            'abtest.batch_test': 'Batch Test',
            'abtest.single_test': 'Single Test',
            'abtest.query_label': 'Query:',
            'abtest.kb_label': 'Knowledge Base:',
            'abtest.best_eval': 'Best Eval:',
            'abtest.best_recall': 'Best Recall:',
            'abtest.best_drift': 'Best Drift:',
            'abtest.variant_config': 'Variants (at least 2)',
            'abtest.knowledge_base': 'Knowledge Base',
            'abtest.test_query': 'Test Query',
            'abtest.variant_a': 'Variant A',
            'abtest.variant_b': 'Variant B',
            'abtest.hybrid': 'Hybrid',
            'abtest.vector': 'Vector',
            'abtest.bm25': 'BM25',
            'abtest.result': '📊 A/B Test Results',
            'abtest.view_trace': '🔍 View Trace',
            'abtest.stats_summary': '📈 Statistical Summary',
            'abtest.variant_metrics': '📊 Variant Metrics Comparison',

            // ===== History =====
            'history.title': 'History',
            'history.empty': 'No history records',
            'history.time': 'Time',
            'history.query': 'Question',
            'history.kb': 'Knowledge Base',
            'history.view_trace': 'View Trace',
            'history.click_hint': 'Click any record to view full trace',
        }
    };

    // ==================== 状态管理 ====================
    let currentLang = localStorage.getItem('lang') || 'zh-CN';

    /**
     * 获取当前语言
     */
    function getLang() {
        return currentLang;
    }

    /**
     * 设置语言并持久化
     */
    function setLang(lang) {
        if (!translations[lang]) return;
        currentLang = lang;
        localStorage.setItem('lang', lang);
        applyI18n();
        // 更新语言切换按钮显示
        updateLangButton();
        // 触发语言变更事件，供页面自定义逻辑监听
        document.dispatchEvent(new CustomEvent('langChanged', { detail: { lang: lang } }));
    }

    /**
     * 切换语言（中英切换）
     */
    function toggleLang() {
        setLang(currentLang === 'zh-CN' ? 'en-US' : 'zh-CN');
    }

    /**
     * 翻译函数
     * @param {string} key - 翻译键
     * @param {string} [lang] - 可选语言，默认当前语言
     * @returns {string} 翻译文本，找不到则返回 key
     */
    function t(key, lang) {
        const l = lang || currentLang;
        const dict = translations[l] || translations['zh-CN'];
        return dict[key] || translations['zh-CN'][key] || key;
    }

    /**
     * 批量翻译函数 - 用于 JS 中构建 HTML
     * @param {string} key
     * @returns {string}
     */
    function tt(key) {
        return t(key);
    }

    /**
     * 获取包含 Accept-Language 头的 headers 对象
     * @returns {Object}
     */
    function getApiHeaders() {
        return {
            'Accept-Language': currentLang,
            'Content-Type': 'application/json'
        };
    }

    /**
     * 封装 fetch，自动添加语言头
     * @param {string} url - 请求 URL
     * @param {Object} [options] - fetch 选项
     * @returns {Promise<Response>}
     */
    async function apiFetch(url, options = {}) {
        const headers = {
            'Accept-Language': currentLang,
            ...(options.headers || {})
        };
        const response = await fetch(url, { ...options, headers });
        return response;
    }

    /**
     * 用于需要插入 HTML 的翻译
     * @param {string} key - 翻译键
     * @param {string} [lang] - 可选语言
     * @returns {string}
     */
    function html(key, lang) {
        const l = lang || currentLang;
        const dict = translations[l] || translations['zh-CN'];
        return dict[key] || translations['zh-CN'][key] || key;
    }

    /**
     * 应用国际化：扫描所有 [data-i18n] 元素并替换文本
     * 同时处理 [data-i18n-placeholder] 和 [data-i18n-title]
     */
    function applyI18n() {
        // 文本内容
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            const text = t(key);
            if (text) el.textContent = text;
        });

        // placeholder
        document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            const key = el.getAttribute('data-i18n-placeholder');
            const text = t(key);
            if (text) el.placeholder = text;
        });

        // title 属性
        document.querySelectorAll('[data-i18n-title]').forEach(el => {
            const key = el.getAttribute('data-i18n-title');
            const text = t(key);
            if (text) el.title = text;
        });

        // data-i18n-html (保留HTML内容)
        document.querySelectorAll('[data-i18n-html]').forEach(el => {
            const key = el.getAttribute('data-i18n-html');
            const text = t(key);
            if (text) el.innerHTML = text;
        });

        // <title> 标签
        const titleEl = document.querySelector('title[data-i18n]');
        if (titleEl) {
            document.title = t(titleEl.getAttribute('data-i18n'));
        }

        // 设置 html lang 属性
        document.documentElement.setAttribute('lang', currentLang);
    }

    /**
     * 更新语言切换按钮的显示
     */
    function updateLangButton() {
        const btn = document.getElementById('langToggle');
        if (btn) {
            // 中文时显示 "EN"，英文时显示 "中"
            btn.textContent = currentLang === 'zh-CN' ? 'EN' : '中';
            btn.title = currentLang === 'zh-CN' ? 'Switch to English' : '切换到中文';
        }
    }

    /**
     * 初始化语言（在 <head> 中尽早调用，避免闪白）
     * 通过内联脚本在 HTML 加载前设置 <html lang> 属性
     */
    function earlyInit() {
        const lang = localStorage.getItem('lang') || 'zh-CN';
        document.documentElement.setAttribute('lang', lang);
    }

    // ==================== 导出 API ====================
    global.i18n = {
        t: t,
        tt: tt,
        getLang: getLang,
        setLang: setLang,
        toggleLang: toggleLang,
        applyI18n: applyI18n,
        earlyInit: earlyInit,
        translations: translations,
        getApiHeaders: getApiHeaders,
        apiFetch: apiFetch,
        html: html
    };

    // 尽早设置 html lang 属性
    earlyInit();

    // DOM就绪后自动应用国际化
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
            applyI18n();
        });
    } else {
        applyI18n();
    }

})(window);
