"""
Query Rewrite test script
Test synonym rewrite and expansion logic
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.query_rewriter import QueryRewriter


def test_rewrite():
    """Test query rewrite"""
    rewriter = QueryRewriter()

    test_cases = [
        {
            'name': '客服场景-退款',
            'query': '我想退钱，请问怎么操作退款流程',
            'expected_terms': ['退款', '退钱']
        },
        {
            'name': '技术文档场景-API配置',
            'query': '如何配置API接口的参数和权限',
            'expected_terms': ['API', '接口', '参数', '权限']
        },
        {
            'name': '单字查询扩展',
            'query': '退',
            'expected_terms': ['退']
        },
        {
            'name': '多同义词组合',
            'query': '登录系统后如何修改密码并查看订单',
            'expected_terms': ['登录', '系统', '修改', '密码', '查看', '订单']
        },
        {
            'name': '口语化查询',
            'query': '咋弄啊，这个功能用不了',
            'expected_terms': ['功能']
        },
        {
            'name': '复杂查询-多场景',
            'query': '请问客服如何帮助我处理退货和申请发票',
            'expected_terms': ['客服', '退货', '申请', '发票']
        },
        {
            'name': '技术场景-部署调试',
            'query': '如何部署和调试这个系统，性能怎么样',
            'expected_terms': ['部署', '调试', '系统', '性能']
        },
        {
            'name': '缩写和全称',
            'query': 'API接口调用失败怎么办',
            'expected_terms': ['API', '接口', '调用', '失败']
        }
    ]

    print("=" * 80)
    print("Query Rewrite 测试")
    print("=" * 80)

    for i, case in enumerate(test_cases):
        print(f"\n{'='*60}")
        print(f"测试用例 {i+1}: {case['name']}")
        print(f"原始查询: {case['query']}")
        
        result = rewriter.rewrite(case['query'])
        
        print(f"\n改写结果:")
        print(f"  原始查询: {result['original_query']}")
        print(f"  改写后:   {result['rewritten_query']}")
        print(f"  关键词:   {result['key_terms']}")
        print(f"  扩展词:   {result['expanded_terms']}")
        print(f"  改写原因: {result['rewrite_reason']}")
        
        if result['term_mappings']:
            print(f"\n  术语映射:")
            for mapping in result['term_mappings']:
                print(f"    {mapping['original']} → {mapping['synonyms']}")
        
        # Validate key term extraction
        extracted_terms = set(result['key_terms'])
        expected_terms = set(case['expected_terms'])
        matched_terms = extracted_terms.intersection(expected_terms)
        
        print(f"\n  关键词验证:")
        print(f"    期望关键词: {case['expected_terms']}")
        print(f"    提取关键词: {result['key_terms']}")
        print(f"    匹配数量: {len(matched_terms)}/{len(expected_terms)}")
        
        if len(matched_terms) == len(expected_terms):
            print(f"    ✅ 关键词提取正确")
        else:
            missing = expected_terms - extracted_terms
            print(f"    ⚠️ 缺失关键词: {missing}")

    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80)


def test_batch_rewrite():
    """Test batch rewrite"""
    rewriter = QueryRewriter()
    
    queries = [
        '如何退款',
        '登录系统',
        '修改密码',
        '查看订单'
    ]
    
    results = rewriter.batch_rewrite(queries)
    
    print("\n\n批量改写测试:")
    print("-" * 40)
    for i, (query, result) in enumerate(zip(queries, results)):
        print(f"{i+1}. '{query}' → '{result['rewritten_query']}'")


def test_knowledge_base_terms():
    """Test knowledge base term extraction"""
    rewriter = QueryRewriter()
    
    docs = [
        '退款流程：用户可以在订单页面申请退款，退款将在3-5个工作日内到账',
        'API接口文档：提供用户认证、订单查询、支付接口',
        '系统部署指南：支持Docker部署和手动部署两种方式',
        '权限管理：管理员可以配置用户权限和角色'
    ]
    
    rewriter.fit_knowledge_base(docs)
    terms = rewriter.get_knowledge_terms()
    
    print("\n\n知识库术语提取测试:")
    print("-" * 40)
    print(f"从4篇文档中提取了 {len(terms)} 个术语")
    print(f"术语列表: {terms[:20]}...")
    
    result = rewriter.rewrite('如何申请退款', kb_summary=' '.join(docs))
    print(f"\n结合知识库改写:")
    print(f"  原始: {result['original_query']}")
    print(f"  改写后: {result['rewritten_query']}")
    print(f"  原因: {result['rewrite_reason']}")


if __name__ == '__main__':
    test_rewrite()
    test_batch_rewrite()
    test_knowledge_base_terms()
