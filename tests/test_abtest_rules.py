"""
A/B test and rule effectiveness validation mock test script
Used to validate fixed parameter passthrough and rule persistence logic
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.retriever import HybridRetriever
from core.rule_engine import RuleEngine, BusinessRule
from core.sentence_tracing import SentenceTracer


class MockVectorStore:
    """Mock vector store"""
    
    def __init__(self):
        self._collections = {}
    
    def get_collection(self, kb_id):
        if kb_id in self._collections:
            return self._collections[kb_id]
        return None
    
    def search(self, kb_id, query, top_k):
        mock_results = [
            {
                'id': f'chunk_{i}',
                'text': f'Content of document {i}, containing keywords: {query} refund policy customer service 7-day no reason refund',
                'metadata': {
                    'file_name': f'document_{i}.txt',
                    'chunk_index': i,
                    'total_chunks': 5
                },
                'score': 0.8 - i * 0.1,
                'type': 'vector'
            }
            for i in range(1, min(top_k + 1, 4))
        ]
        return mock_results
    
    def collection_exists(self, kb_id):
        return kb_id in self._collections
    
    def create_collection(self, kb_id, name):
        self._collections[kb_id] = {'name': name}
        return True


def test_retriever_params_override():
    """Test retrieval parameter override scenario parameters"""
    print("\n=== Test 1: Retrieval parameter override scenario parameters ===")
    
    mock_vs = MockVectorStore()
    mock_vs.create_collection('test_kb', 'Test Knowledge Base')
    
    retriever = HybridRetriever(mock_vs)
    
    # Retrieval under default config
    result1 = retriever.retrieve('test_kb', 'refund policy')
    print(f"Default mode: {result1['mode']}")
    print(f"Default BM25 weight: {result1['debug_info']['params']['bm25_weight']}")
    print(f"Default similarity threshold: {result1['debug_info']['params']['similarity_threshold']}")
    
    # Retrieval after overriding parameters
    result2 = retriever.retrieve(
        'test_kb', 'refund policy',
        mode='vector',
        bm25_weight=0.2,
        similarity_threshold=0.5,
        top_k=3
    )
    print(f"\nOverride mode: {result2['mode']}")
    print(f"Override BM25 weight: {result2['debug_info']['params']['bm25_weight']}")
    print(f"Override vector weight: {result2['debug_info']['params']['vector_weight']}")
    print(f"Override similarity threshold: {result2['debug_info']['params']['similarity_threshold']}")
    print(f"Override top_k: {result2['debug_info']['params']['top_k']}")
    
    assert result2['mode'] == 'vector', "Mode override failed"
    assert result2['debug_info']['params']['bm25_weight'] == 0.2, "BM25 weight override failed"
    assert result2['debug_info']['params']['vector_weight'] == 0.8, "Vector weight calculation failed"
    assert result2['debug_info']['params']['similarity_threshold'] == 0.5, "Similarity threshold override failed"
    assert result2['debug_info']['params']['top_k'] == 3, "top_k override failed"
    print("✅ Retrieval parameter override test passed")


def test_hit_reasons_use_params():
    """Test hit attribution uses weights from params"""
    print("\n=== Test 2: Hit attribution uses weights from params ===")
    
    mock_vs = MockVectorStore()
    mock_vs.create_collection('test_kb', 'Test Knowledge Base')
    
    retriever = HybridRetriever(mock_vs)
    
    # Retrieve using custom weights
    result = retriever.retrieve(
        'test_kb', '退款政策',
        mode='hybrid',
        bm25_weight=0.7,
        vector_weight=0.3
    )
    
    if result['results']:
        hit_reasons = result['results'][0].get('hit_reasons', [])
        for reason in hit_reasons:
            if reason['type'] == 'score_contribution':
                print(f"Hit attribution BM25 weight: {reason['value']['bm25_weight']}")
                print(f"Hit attribution vector weight: {reason['value']['vector_weight']}")
                assert reason['value']['bm25_weight'] == 0.7, "BM25 weight attribution error"
                assert reason['value']['vector_weight'] == 0.3, "Vector weight attribution error"
                print("✅ Hit attribution test passed with custom weights from params")
                return
    
    print("⚠️ No hit attribution data found")


def test_rule_effectiveness_persistence():
    """Test rule effectiveness rate persistence"""
    print("\n=== Test 3: Rule effectiveness rate persistence ===")
    
    import tempfile
    import os
    
    # Create temporary rule file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_file = f.name
    
    # Modify config to point to temporary file
    from config import config
    original_rule_file = config._config.get('rule_engine', {}).get('rule_file', './storage/rules.json')
    if 'rule_engine' not in config._config:
        config._config['rule_engine'] = {}
    config._config['rule_engine']['rule_file'] = temp_file
    
    # Create rule engine instance
    rule_engine = RuleEngine()
    
    # Add test rule
    test_rule = BusinessRule(
        rule_id='test_rule_001',
        rule_type='intent_routing',
        pattern={'keywords': ['退款', '退货'], 'match_type': 'any'},
        action={'scenario_id': 'customer_service'},
        description='测试规则'
    )
    rule_engine.add_rule(test_rule)
    
    # Simulate rule application
    rule_engine.log_rule_application(
        rule_id='test_rule_001',
        query='如何申请退款',
        action_taken={'scenario_id': 'customer_service'},
        result_summary={'trace_id': 'test_trace', 'context_count': 3, 'evaluation_score': 0.85}
    )
    
    # Record effectiveness
    rule_engine.evaluate_rule_effectiveness(
        rule_id='test_rule_001',
        feedback={'effective': True, 'evaluation_score': 0.85}
    )
    rule_engine.evaluate_rule_effectiveness(
        rule_id='test_rule_001',
        feedback={'effective': True, 'evaluation_score': 0.92}
    )
    rule_engine.evaluate_rule_effectiveness(
        rule_id='test_rule_001',
        feedback={'effective': False, 'evaluation_score': 0.6}
    )
    
    # Get current state
    report_before = rule_engine.get_rule_effectiveness_report('test_rule_001')
    print(f"持久化前有效次数: {report_before['effective_count']}")
    print(f"持久化前无效次数: {report_before['ineffective_count']}")
    print(f"持久化前平均评估分数: {report_before['avg_evaluation_score']}")
    
    # Force persistence before simulating a restart: the rule engine batches writes
    # (flush every 10s or 50 changes), so the counters would still be in memory otherwise.
    rule_engine.flush()

    # Create new rule engine instance (simulate restart)
    rule_engine2 = RuleEngine()
    
    # Get restored state
    report_after = rule_engine2.get_rule_effectiveness_report('test_rule_001')
    print(f"\n持久化后有效次数: {report_after['effective_count']}")
    print(f"\n持久化后无效次数: {report_after['ineffective_count']}")
    print(f"\n持久化后平均评估分数: {report_after['avg_evaluation_score']}")
    
    assert report_after['effective_count'] == 2, "有效次数持久化失败"
    assert report_after['ineffective_count'] == 1, "无效次数持久化失败"
    assert abs(report_after['avg_evaluation_score'] - 0.79) < 0.01, "平均评估分数持久化失败"
    assert report_after['effectiveness_rate'] == 2/3, "生效率计算失败"
    
    # Clean up temporary file
    os.unlink(temp_file)
    
    # Restore original config
    config._config['rule_engine']['rule_file'] = original_rule_file
    
    print("✅ 规则生效率持久化测试通过")


def test_sentence_tracing_contradiction():
    """Test semantic contradiction detection"""
    print("\n=== 测试4: 语义矛盾检测 ===")
    
    tracer = SentenceTracer()
    
    # Construct test data containing contradictions
    answer = "根据文档，退货期限为30天，可以全额退款，不支持部分退款"
    
    context_results = [
        {
            'id': 'chunk_1',
            'text': '退货期限为7天，超过7天将不予处理。退款方式为原路径返回，仅支持部分退款，不支持全额退款。',
            'metadata': {'file_name': '退货政策.txt'}
        },
        {
            'id': 'chunk_2',
            'text': '售后服务时间为工作日9:00-18:00，支持电话和在线客服两种方式。',
            'metadata': {'file_name': '服务指南.txt'}
        }
    ]
    
    contradictions = tracer.detect_contradictions(answer, context_results)
    
    print(f"检测到 {len(contradictions)} 个矛盾")
    for i, contradiction in enumerate(contradictions):
        print(f"\n矛盾{i+1}:")
        print(f"  句子: {contradiction['sentence']}")
        print(f"  类型: {contradiction['type']}")
        print(f"  严重程度: {contradiction['severity']}")
        print(f"  源文档值: {contradiction['source_value']}")
        print(f"  答案值: {contradiction['answer_value']}")
        print(f"  解释: {contradiction['explanation']}")
    
    # Verify detection results
    time_contradictions = [c for c in contradictions if c['type'] == 'time_duration']
    status_contradictions = [c for c in contradictions if c['type'] == 'status']
    
    assert len(time_contradictions) >= 1, "时间矛盾未检测到"
    assert len(status_contradictions) >= 1, "状态矛盾未检测到"
    
    print("✅ 语义矛盾检测测试通过")


def test_ab_test_variant_config():
    """Test A/B test variant config"""
    print("\n=== 测试5: A/B测试变体配置 ===")
    
    from api.routes.chat import ABTestRequest, ABTestVariant
    from pydantic import ValidationError
    
    # Test normal config
    try:
        request = ABTestRequest(
            kb_id='test_kb',
            query='如何申请退款',
            variants=[
                ABTestVariant(
                    name='变体A',
                    retrieval_mode='hybrid',
                    bm25_weight=0.4,
                    similarity_threshold=0.3,
                    top_k=5
                ),
                ABTestVariant(
                    name='变体B',
                    retrieval_mode='vector',
                    bm25_weight=0.2,
                    similarity_threshold=0.5,
                    top_k=3
                )
            ]
        )
        print(f"变体数量: {len(request.variants)}")
        for variant in request.variants:
            print(f"  {variant.name}: mode={variant.retrieval_mode}, bm25_weight={variant.bm25_weight}, threshold={variant.similarity_threshold}, top_k={variant.top_k}")
        print("✅ A/B测试配置验证通过")
    except ValidationError as e:
        print(f"❌ 配置验证失败: {e}")
        raise
    
    # Test boundary: fewer than 2 variants
    try:
        ABTestRequest(
            kb_id='test_kb',
            query='测试',
            variants=[ABTestVariant(name='变体A')]
        )
        print("❌ 应该拒绝少于2个变体的配置")
    except ValidationError:
        print("✅ 正确拒绝了少于2个变体的配置")


def test_ab_test_comparison_logic():
    """Test A/B test comparison logic"""
    print("\n=== 测试6: A/B测试对比逻辑 ===")
    
    from api.routes.chat import _compare_results
    
    # Construct mock results
    mock_results = [
        {
            'name': '变体A',
            'answer': '这是一个较长的回答内容，包含详细的解释和说明',
            'context': [{'text': 'doc1'}, {'text': 'doc2'}, {'text': 'doc3'}],
            'context_count': 3,
            'evaluation': {'overall_score': 0.85},
            'drift_analysis': {'drift_rate': 0.2},
            'contradiction_count': 0
        },
        {
            'name': '变体B',
            'answer': '简短回答',
            'context': [{'text': 'doc1'}],
            'context_count': 1,
            'evaluation': {'overall_score': 0.72},
            'drift_analysis': {'drift_rate': 0.4},
            'contradiction_count': 1
        },
        {
            'name': '变体C',
            'answer': '中等长度的回答',
            'context': [{'text': 'doc1'}, {'text': 'doc2'}],
            'context_count': 2,
            'evaluation': {'overall_score': 0.91},
            'drift_analysis': {'drift_rate': 0.15},
            'contradiction_count': 0
        }
    ]
    
    comparison = _compare_results(mock_results)
    
    print(f"最佳评估: {comparison['best_by_evaluation']['name']} ({comparison['best_by_evaluation']['score']})")
    print(f"最佳召回: {comparison['best_by_recall']['name']} ({comparison['best_by_recall']['count']})")
    print(f"最佳漂移率: {comparison['best_by_drift_rate']['name']} ({comparison['best_by_drift_rate']['drift_rate']})")
    
    print(f"\n统计摘要:")
    summary = comparison['summary']
    print(f"  平均答案长度: {summary['avg_answer_length']:.0f}")
    print(f"  平均召回数量: {summary['avg_context_count']:.1f}")
    print(f"  平均评估分数: {summary['avg_evaluation_score']:.3f}")
    print(f"  平均漂移率: {summary['avg_drift_rate']:.2f}")
    
    assert comparison['best_by_evaluation']['name'] == '变体C', "最佳评估判断错误"
    assert comparison['best_by_recall']['name'] == '变体A', "最佳召回判断错误"
    assert comparison['best_by_drift_rate']['name'] == '变体C', "最佳漂移率判断错误"
    
    print("✅ A/B测试对比逻辑测试通过")


if __name__ == '__main__':
    print("=" * 70)
    print("A/B测试和规则生效验证 Mock 测试")
    print("=" * 70)
    
    test_retriever_params_override()
    test_hit_reasons_use_params()
    test_rule_effectiveness_persistence()
    test_sentence_tracing_contradiction()
    test_ab_test_variant_config()
    test_ab_test_comparison_logic()
    
    print("\n" + "=" * 70)
    print("所有测试通过！")
    print("=" * 70)
