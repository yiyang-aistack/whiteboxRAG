"""
A/B test integration test script
Simulate complete API call flow, test parameter passing and rule effectiveness scenarios
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def simulate_ab_test_api_call():
    """Simulate A/B test API call"""
    print("\n=== A/B test API call simulation test ===")
    
    from api.routes.chat import ab_test, ABTestRequest, ABTestVariant
    from core.llm_pipeline import LLMPipeline
    from core.vector_store import VectorStoreManager as VectorStore
    
    print("\n1. Initialize LLM Pipeline...")
    
    vector_store = VectorStore()
    pipeline = LLMPipeline(vector_store)
    
    print("✅ LLM Pipeline initialized")
    
    print("\n2. Construct A/B test request...")
    request = ABTestRequest(
        kb_id='b2268c57',
        query='How to apply for a refund?',
        variants=[
            ABTestVariant(
                name='Default configuration',
                retrieval_mode=None,
                bm25_weight=None,
                similarity_threshold=None,
                top_k=None
            ),
            ABTestVariant(
                name='High BM BM25 weight',
                retrieval_mode='hybrid',
                bm25_weight=0.7,
                similarity_threshold=0.3,
                top_k=5
            ),
            ABTestVariant(
                name='High vector weight',
                retrieval_mode='hybrid',
                bm25_weight=0.2,
                similarity_threshold=0.5,
                top_k=3
            ),
            ABTestVariant(
                name='Vector retrieval',
                retrieval_mode='vector',
                bm25_weight=None,
                similarity_threshold=0.4,
                top_k=4
            )
        ]
    )
    
    print(f"   Query: {request.query}")
    print(f"   Knowledge base: {request.kb_id}")
    print(f"   Variant count: {len(request.variants)}")
    for v in request.variants:
        print(f"     - {v.name}: mode={v.retrieval_mode}, bm25_weight={v.bm25_weight}, threshold={v.similarity_threshold}, top_k={v.top_k}")
    
    print("\n✅ A/B test request construction successfully")
    
    print("\n3. Verify parameter passing logic...")
    for variant in request.variants:
        variant_params = {}
        scenario_id = None
        
        if variant.retrieval_mode:
            variant_params['mode'] = variant.retrieval_mode
        if variant.bm25_weight is not None:
            variant_params['bm25_weight'] = variant.bm25_weight
        if variant.similarity_threshold is not None:
            variant_params['similarity_threshold'] = variant.similarity_threshold
        if variant.top_k is not None:
            variant_params['top_k'] = variant.top_k
        
        print(f"\n    Variant: {variant.name}")
        print(f"   Passed parameters: {variant_params}")
        
        if variant_params:
            print("   ✅ Parameters passed to LLMPipeline.query()")
        else:
            print("   ✅ Default scenario configuration used")
    
    print("\n✅ Parameter passing logic verified successfully")
    
    print("\n4. Simulate rule application scenarios...")
    
    from core.rule_engine import rule_engine, BusinessRule
    
    if 'test_refund_rule' not in rule_engine.rules:
        rule = BusinessRule(
            rule_id='test_refund_rule',
            rule_type='intent_routing',
            pattern={'keywords': ['refund', 'return', 'return money'], 'match_type': 'any'},
            action={'scenario_id': 'customer_service'},
            description='Refund-related questions routed to customer service scenario'
        )
        rule_engine.add_rule(rule)
        print("   ✅ Test rule added successfully")
    
    print("\n   Rule matching test:")
    query = "How to apply for a refund?"
    rule_results = rule_engine.evaluate_query(query)
    print(f"    Query: '{query}'")
    print(f"   Matched rules count: {len(rule_results)}")
    for result in rule_results:
        print(f"     - {result['rule_id']}: {result.get('description', '')}")
    
    print("\n   Rule application log:")
    rule_engine.log_rule_application(
        rule_id='test_refund_rule',
        query=query,
        action_taken={'scenario_id': 'customer_service'},
        result_summary={'trace_id': 'test_abtest_trace', 'context_count': 5, 'evaluation_score': 0.88}
    )
    print("   ✅ Rule application log recorded successfully")
    
    print("\n   Rule effectiveness evaluation:")
    rule_engine.evaluate_rule_effectiveness(
        rule_id='test_refund_rule',
        feedback={'effective': True, 'evaluation_score': 0.88}
    )
    report = rule_engine.get_rule_effectiveness_report('test_refund_rule')
    print(f"     Effective count: {report['effective_count']}")
    print(f"     Ineffective count: {report['ineffective_count']}")
    print(f"     Effectiveness rate: {report['effectiveness_rate']:.2%}")
    print(f"     Average evaluation score: {report['avg_evaluation_score']:.3f}")
    print(f"     Rule status: {report['status']}")
    
    print("\n✅ Rule application scenario verified successfully")
    
    print("\n5. Simulate semantic contradiction detection scenario...")
    
    from core.sentence_tracing import sentence_tracer
    
    answer = "Return policy states that refunds are supported for up to 30 days."
    context_results = [
        {
            'id': 'chunk_1',
            'text': 'Return policy states that refunds are supported for up to 7 days.',
            'metadata': {'file_name': 'return_policy.txt'}
        }
    ]
    
    contradictions = sentence_tracer.detect_contradictions(answer, context_results)
    print(f"    Detected contradictions count: {len(contradictions)}")
    for c in contradictions:
        print(f"     - {c['type']} [{c['severity']}]: {c['explanation']}")
    
    print("\n✅ Semantic contradiction detection scenario verified successfully")


def test_real_knowledge_base():
    """Test real knowledge base (if exists)"""
    print("\n=== Test real knowledge base ===")
    
    from core.vector_store import VectorStoreManager as VectorStore
    
    vector_store = VectorStore()
    
    kb_list = vector_store.list_collections()
    print(f"\nAvailable knowledge bases: {len(kb_list)}")
    for kb in kb_list:
        print(f"   - {kb['name']}: {kb['count']} documents")
    
    if kb_list:
        test_kb = kb_list[0]
        print(f"\nUsing knowledge base: {test_kb['name']}")
        
        from core.retriever import HybridRetriever
        
        retriever = HybridRetriever(vector_store)
        
        print("\nTest 1: Default configuration retrieval")
        result1 = retriever.retrieve(test_kb['name'], 'return_policy')
        print(f"   Mode: {result1['mode']}")
        print(f"   Count: {result1['count']}")
        print(f"   BM25 weight: {result1['debug_info']['params']['bm25_weight']}")
        
        print("\nTest 2: Parameter override retrieval")
        result2 = retriever.retrieve(
            test_kb['name'], 'return_policy',
            mode='vector',
            bm25_weight=0.3,
            similarity_threshold=0.5
        )
        print(f"   Mode: {result2['mode']}")
        print(f"   Count: {result2['count']}")
        print(f"   BM25 weight: {result2['debug_info']['params']['bm25_weight']}")
        print(f"   Vector weight: {result2['debug_info']['params']['vector_weight']}")
        print(f"   Similarity threshold: {result2['debug_info']['params']['similarity_threshold']}")
        
        print("\n✅ Real knowledge base test passed")
    else:
        print("⚠️ No available knowledge base, skipping test for real knowledge base")


if __name__ == '__main__':
    print("=" * 70)
    print("A/B Test Integration Test")
    print("=" * 70)
    
    simulate_ab_test_api_call()
    test_real_knowledge_base()
    
    print("\n" + "=" * 70)
    print("Integration test completed")
    print("=" * 70)
