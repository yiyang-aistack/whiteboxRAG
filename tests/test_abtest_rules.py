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
        # A route that returned nothing has its fusion weight redistributed for that query, so
        # the attribution reports the *effective* weights (what the fused score was computed
        # with) - `debug_info.effective_weights` is the same pair, and the configured 0.7 / 0.3
        # stay visible in `debug_info.params`.
        effective = result['debug_info']['effective_weights']
        assert result['debug_info']['params']['bm25_weight'] == 0.7, "BM25 weight override failed"
        assert result['debug_info']['params']['vector_weight'] == 0.3, "Vector weight override failed"
        for reason in hit_reasons:
            if reason['type'] == 'score_contribution':
                print(f"Hit attribution BM25 weight: {reason['value']['bm25_weight']}")
                print(f"Hit attribution vector weight: {reason['value']['vector_weight']}")
                assert reason['value']['bm25_weight'] == effective['bm25_weight'], \
                    "BM25 weight attribution error"
                assert reason['value']['vector_weight'] == effective['vector_weight'], \
                    "Vector weight attribution error"
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
    print(f"effective_count before persistence: {report_before['effective_count']}")
    print(f"ineffective_count before persistence: {report_before['ineffective_count']}")
    print(f"avg_evaluation_score before persistence: {report_before['avg_evaluation_score']}")
    
    # Force persistence before simulating a restart: the rule engine batches writes
    # (flush every 10s or 50 changes), so the counters would still be in memory otherwise.
    rule_engine.flush()

    # Create new rule engine instance (simulate restart)
    rule_engine2 = RuleEngine()
    
    # Get restored state
    report_after = rule_engine2.get_rule_effectiveness_report('test_rule_001')
    print(f"\neffective_count after persistence: {report_after['effective_count']}")
    print(f"ineffective_count after persistence: {report_after['ineffective_count']}")
    print(f"avg_evaluation_score after persistence: {report_after['avg_evaluation_score']}")
    
    assert report_after['effective_count'] == 2, "effective_count persistence failed"
    assert report_after['ineffective_count'] == 1, "ineffective_count persistence failed"
    assert abs(report_after['avg_evaluation_score'] - 0.79) < 0.01, "avg_evaluation_score persistence failed"
    assert report_after['effectiveness_rate'] == 2/3, "effectiveness_rate persistence failed"
    
    # Clean up temporary file
    os.unlink(temp_file)
    
    # Restore original config
    config._config['rule_engine']['rule_file'] = original_rule_file
    
    print("✅ Rule effectiveness rate persistence test passed")


def test_sentence_tracing_contradiction():
    """Test semantic contradiction detection"""
    print("\n=== Test 4: Semantic contradiction detection ===")
    
    tracer = SentenceTracer()
    
    # Construct test data containing contradictions
    answer = "Per document, return policy is 7 days to 30 days."
    
    context_results = [
        {
            'id': 'chunk_1',
            'text': 'Return policy is 7 days to 30 days. Partial refund is supported, not full refund.',
            'metadata': {'file_name': 'refund_policy.txt'}
        },
        {
            'id': 'chunk_2',
            'text': 'Customer service time is 9:00-18:00.',
            'metadata': {'file_name': 'service_guide.txt'}
        }
    ]
    
    contradictions = tracer.detect_contradictions(answer, context_results)
    
    print(f"Detected {len(contradictions)} contradictions:")
    for i, contradiction in enumerate(contradictions):
        print(f"\nConflict {i+1}:")
        print(f"  Sentence: {contradiction['sentence']}")
        print(f"  Type: {contradiction['type']}")
        print(f"  Severity: {contradiction['severity']}")
        print(f"  Source value: {contradiction['source_value']}")
        print(f"  Answer value: {contradiction['answer_value']}")
        print(f"  Explanation: {contradiction['explanation']}")
    
    # Verify detection results
    time_contradictions = [c for c in contradictions if c['type'] == 'time_duration']
    status_contradictions = [c for c in contradictions if c['type'] == 'status']
    
    assert len(time_contradictions) >= 1, "Time contradiction not detected"
    assert len(status_contradictions) >= 1, "Status contradiction not detected"

    print("✅ Semantic contradiction detection test case passed")


def test_ab_test_variant_config():
    """Test A/B test variant config"""
    print("\n=== Test 5: A/B test variant config ===")
    
    from api.routes.chat import ABTestRequest, ABTestVariant
    from pydantic import ValidationError
    
    # Test normal config
    try:
        request = ABTestRequest(
            kb_id='test_kb',
            query='How to apply for refund?',
            variants=[
                ABTestVariant(
                    name='Variant A',
                    retrieval_mode='hybrid',
                    bm25_weight=0.4,
                    similarity_threshold=0.3,
                    top_k=5
                ),
                ABTestVariant(
                    name='Variant B',
                    retrieval_mode='vector',
                    bm25_weight=0.2,
                    similarity_threshold=0.5,
                    top_k=3
                )
            ]
        )
        print(f"Variant count in request: {len(request.variants)}")
        for variant in request.variants:
            print(f"  {variant.name}: mode={variant.retrieval_mode}, bm25_weight={variant.bm25_weight}, threshold={variant.similarity_threshold}, top_k={variant.top_k}")
        print("✅ A/B test variant config validation passed")
    except ValidationError as e:
        print(f"❌ A/B test variant config validation failed: {e}")
        raise
    
    # Test boundary: fewer than 2 variants
    try:
        ABTestRequest(
            kb_id='test_kb',
            query='Test',
            variants=[ABTestVariant(name='Variant A')]
        )
        print("❌ Should reject fewer than 2 variants")
    except ValidationError:
        print("✅ Correctly rejected fewer than 2 variants")


def test_ab_test_comparison_logic():
    """Test A/B test comparison logic"""
    print("\n=== Test 6: A/B test comparison logic ===")
    
    from api.routes.chat import _compare_results
    
    # Construct mock results
    mock_results = [
        {
            'name': 'Variant A',
            'answer': 'Long answer with detailed explanation and details',
            'context': [{'text': 'doc1'}, {'text': 'doc2'}, {'text': 'doc3'}],
            'context_count': 3,
            'evaluation': {'overall_score': 0.85},
            'drift_analysis': {'drift_rate': 0.2},
            'contradiction_count': 0
        },
        {
            'name': 'Variant B',
            'answer': 'Short answer',
            'context': [{'text': 'doc1'}],
            'context_count': 1,
            'evaluation': {'overall_score': 0.72},
            'drift_analysis': {'drift_rate': 0.4},
            'contradiction_count': 1
        },
        {
            'name': 'Variant C',
            'answer': 'Medium length answer',
            'context': [{'text': 'doc1'}, {'text': 'doc2'}],
            'context_count': 2,
            'evaluation': {'overall_score': 0.91},
            'drift_analysis': {'drift_rate': 0.15},
            'contradiction_count': 0
        }
    ]
    
    comparison = _compare_results(mock_results)
    
    print(f"Best evaluation: {comparison['best_by_evaluation']['name']} ({comparison['best_by_evaluation']['score']})")
    print(f"Best recall: {comparison['best_by_recall']['name']} ({comparison['best_by_recall']['count']})")
    print(f"Best drift rate: {comparison['best_by_drift_rate']['name']} ({comparison['best_by_drift_rate']['drift_rate']})")
    
    print(f"\nStatistics summary:")
    summary = comparison['summary']
    print(f"  Average answer length: {summary['avg_answer_length']:.0f}")
    print(f"  Average recall count: {summary['avg_context_count']:.1f}")
    print(f"  Average evaluation score: {summary['avg_evaluation_score']:.3f}")
    print(f"  Average drift rate: {summary['avg_drift_rate']:.2f}")
    
    assert comparison['best_by_evaluation']['name'] == 'Variant C', "Best evaluation judgment error"
    assert comparison['best_by_recall']['name'] == 'Variant A', "Best recall judgment error"
    assert comparison['best_by_drift_rate']['name'] == 'Variant C', "Best drift rate judgment error"
    
    print("✅ A/B test comparison logic test passed")


if __name__ == '__main__':
    print("=" * 70)
    print("A/B test and rule effectiveness persistence Mock Test")
    print("=" * 70)
    
    test_retriever_params_override()
    test_hit_reasons_use_params()
    test_rule_effectiveness_persistence()
    test_sentence_tracing_contradiction()
    test_ab_test_variant_config()
    test_ab_test_comparison_logic()
    
    print("\n" + "=" * 70)
    print("✅ All tests passed")
    print("=" * 70)
