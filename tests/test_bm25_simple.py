"""
Pure BM25 retrieval test (simplified version)
No chromadb or Ollama needed, directly demonstrates keyword retrieval effect
"""
import sys
from pathlib import Path

# Add project root directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

import jieba
from rank_bm25 import BM25Okapi


def create_test_documents():
    """Create test documents"""
    docs = [
        {
            'id': '0',
            'text': "Python是一种广泛使用的编程语言，特别适合数据科学和机器学习领域。Python拥有丰富的第三方库，如NumPy、Pandas、TensorFlow等。",
            'metadata': {'file_name': 'python_intro.txt'}
        },
        {
            'id': '1',
            'text': "机器学习是人工智能的一个重要分支，通过算法让计算机从数据中学习模式。深度学习是机器学习的子领域，使用神经网络进行复杂任务。",
            'metadata': {'file_name': 'ml_intro.txt'}
        },
        {
            'id': '2',
            'text': "FastAPI是一个现代、高性能的Python Web框架，基于Starlette和Pydantic构建。它支持异步编程，自动生成API文档。",
            'metadata': {'file_name': 'fastapi_intro.txt'}
        },
        {
            'id': '3',
            'text': "向量数据库是存储和检索高维向量的专用数据库，如ChromaDB、Pinecone等。它们广泛应用于语义搜索和推荐系统。",
            'metadata': {'file_name': 'vectordb_intro.txt'}
        },
        {
            'id': '4',
            'text': "BM25是一种基于词频和文档长度的检索算法，属于概率检索模型。BM25在关键词匹配场景中表现优异，特别适合精确查询。",
            'metadata': {'file_name': 'bm25_intro.txt'}
        },
        {
            'id': '5',
            'text': "RAG（检索增强生成）是一种结合检索和生成的AI技术。它首先从知识库检索相关文档，然后使用LLM生成回答，提高准确性。",
            'metadata': {'file_name': 'rag_intro.txt'}
        },
        {
            'id': '6',
            'text': "自然语言处理（NLP）是AI的重要应用领域，涉及文本分析、情感分析、机器翻译等任务。GPT和BERT是著名的NLP模型。",
            'metadata': {'file_name': 'nlp_intro.txt'}
        },
        {
            'id': '7',
            'text': "数据库索引是提高查询性能的关键技术，包括B树索引、哈希索引、全文索引等。合理的索引设计可以大幅提升系统性能。",
            'metadata': {'file_name': 'database_index.txt'}
        },
        {
            'id': '8',
            'text': "ChromaDB是一个开源的向量数据库，支持本地部署和云端服务。它提供了简单的API接口，适合RAG应用开发。",
            'metadata': {'file_name': 'chromadb_intro.txt'}
        },
        {
            'id': '9',
            'text': "文本分词是中文文本处理的基础步骤，jieba是常用的中文分词工具。分词质量直接影响检索和NLP任务的准确性。",
            'metadata': {'file_name': 'tokenizer_intro.txt'}
        },
    ]
    return docs


def tokenize(text):
    """jieba tokenization"""
    return list(jieba.cut(text))


def test_bm25_search():
    """
    Test BM25 keyword retrieval
    """
    print("\n" + "="*80)
    print("  BM25关键词检索演示测试")
    print("="*80)
    
    # Create test documents
    print("\n[步骤1] 创建测试文档...")
    docs = create_test_documents()
    print(f"  创建了 {len(docs)} 个文档")
    
    # Build BM25 index
    print("\n[步骤2] 构建BM25索引（jieba分词）...")
    tokenized_docs = [tokenize(doc['text']) for doc in docs]
    bm25 = BM25Okapi(tokenized_docs)
    print(f"  索引构建完成")
    
    # Show tokenization example
    print("\n[分词示例]")
    sample_text = docs[4]['text'][:50]
    sample_tokens = tokenize(sample_text)
    print(f"  原文: {sample_text}")
    print(f"  分词: {' | '.join(sample_tokens)}")
    
    # Execute retrieval tests
    print("\n" + "="*80)
    print("  开始执行检索测试")
    print("="*80)
    
    # Test 1: Exact keyword query
    print("\n" + "-"*80)
    print("【测试1】精确关键词查询")
    print("-"*80)
    
    query1 = "BM25算法"
    print(f"\n查询: '{query1}'")
    print(f"分词: {' | '.join(tokenize(query1))}")
    
    scores = bm25.get_scores(tokenize(query1))
    scored_docs = [(docs[i], scores[i]) for i in range(len(docs)) if scores[i] > 0]
    scored_docs.sort(key=lambda x: x[1], reverse=True)
    
    print(f"\n检索结果（按BM25分数排序）:")
    for i, (doc, score) in enumerate(scored_docs[:3], 1):
        print(f"\n  [{i}] 来源: {doc['metadata']['file_name']}")
        print(f"      BM25分数: {score:.4f}")
        print(f"      内容: {doc['text'][:80]}...")
    
    # Test 2: Multiple keyword query
    print("\n" + "-"*80)
    print("【测试2】多个关键词查询")
    print("-"*80)
    
    query2 = "Python 数据科学"
    print(f"\n查询: '{query2}'")
    print(f"分词: {' | '.join(tokenize(query2))}")
    
    scores = bm25.get_scores(tokenize(query2))
    scored_docs = [(docs[i], scores[i]) for i in range(len(docs)) if scores[i] > 0]
    scored_docs.sort(key=lambda x: x[1], reverse=True)
    
    print(f"\n检索结果:")
    for i, (doc, score) in enumerate(scored_docs[:3], 1):
        print(f"\n  [{i}] 来源: {doc['metadata']['file_name']}")
        print(f"      BM25分数: {score:.4f}")
        print(f"      内容: {doc['text'][:80]}...")
    
    # Test 3: Rare keyword query
    print("\n" + "-"*80)
    print("【测试3】罕见关键词查询")
    print("-"*80)
    
    query3 = "jieba分词"
    print(f"\n查询: '{query3}'")
    print(f"分词: {' | '.join(tokenize(query3))}")
    
    scores = bm25.get_scores(tokenize(query3))
    scored_docs = [(docs[i], scores[i]) for i in range(len(docs)) if scores[i] > 0]
    scored_docs.sort(key=lambda x: x[1], reverse=True)
    
    print(f"\n检索结果:")
    for i, (doc, score) in enumerate(scored_docs[:2], 1):
        print(f"\n  [{i}] 来源: {doc['metadata']['file_name']}")
        print(f"      BM25分数: {score:.4f}")
        print(f"      内容: {doc['text'][:80]}...")
    
    # Test 4: Long query comparison
    print("\n" + "-"*80)
    print("【测试4】自然语言查询对比")
    print("-"*80)
    
    query4a = "如何选择合适的编程语言进行数据分析"
    print(f"\n查询A: '{query4a}'")
    print(f"分词: {' | '.join(tokenize(query4a))}")
    
    scores = bm25.get_scores(tokenize(query4a))
    scored_docs = [(docs[i], scores[i]) for i in range(len(docs)) if scores[i] > 0]
    scored_docs.sort(key=lambda x: x[1], reverse=True)
    
    print(f"\nBM25检索结果:")
    for i, (doc, score) in enumerate(scored_docs[:3], 1):
        print(f"\n  [{i}] 来源: {doc['metadata']['file_name']}")
        print(f"      BM25分数: {score:.4f}")
        print(f"      内容: {doc['text'][:80]}...")
    
    print("\n" + "-"*80)
    query4b = "编程语言 数据分析"
    print(f"\n查询B（简化关键词）: '{query4b}'")
    print(f"分词: {' | '.join(tokenize(query4b))}")
    
    scores = bm25.get_scores(tokenize(query4b))
    scored_docs = [(docs[i], scores[i]) for i in range(len(docs)) if scores[i] > 0]
    scored_docs.sort(key=lambda x: x[1], reverse=True)
    
    print(f"\nBM25检索结果:")
    for i, (doc, score) in enumerate(scored_docs[:3], 1):
        print(f"\n  [{i}] 来源: {doc['metadata']['file_name']}")
        print(f"      BM25分数: {score:.4f}")
        print(f"      内容: {doc['text'][:80]}...")
    
    # Test 5: Empty query
    print("\n" + "-"*80)
    print("【测试5】无关关键词查询（无结果）")
    print("-"*80)
    
    query5 = "量子力学 薛定谔方程"
    print(f"\n查询: '{query5}'")
    print(f"分词: {' | '.join(tokenize(query5))}")
    
    scores = bm25.get_scores(tokenize(query5))
    max_score = max(scores) if len(scores) > 0 else 0
    
    if max_score == 0:
        print("\n结果: 无匹配文档")
        print("\n空检索降级回复:")
        print("  抱歉，未在知识库中找到相关信息，请尝试换一种提问方式或上传相关文档。")
    else:
        print(f"\n最高分数: {max_score:.4f}")
    
    # Summary
    print("\n" + "="*80)
    print("  测试总结")
    print("="*80)
    
    print("\n【BM25检索特点】")
    print("  [+] 精确关键词匹配，适合特定术语查询（如'BM25算法'、'jieba分词'）")
    print("  [+] 基于词频(TF)和逆文档频率(IDF)评分")
    print("  [+] 对文档长度进行归一化，避免长文档优势")
    print("  [+] 不依赖语义理解，无需向量模型")
    print("  [+] 类似grep的文本检索，但有相关性排序")
    
    print("\n【BM25 vs grep对比】")
    print("  grep:")
    print("    - 精确字符串匹配")
    print("    - 无排序能力")
    print("    - 无分词支持")
    print("  BM25:")
    print("    - 关键词权重匹配")
    print("    - 按相关性分数排序")
    print("    - 支持中文分词（jieba）")
    print("    - 部分匹配也能返回结果")
    
    print("\n【BM25与向量检索的区别】")
    print("  BM25:")
    print("    - 查询'编程语言 数据分析' → 精确匹配关键词")
    print("    - 无法理解'如何选择合适的编程语言进行数据分析'的语义")
    print("  向量检索:")
    print("    - 可以理解语义，找到语义相关但无关键词重叠的文档")
    print("    - 查询'数据分析工具' → 可能匹配到Python文档（语义相似）")
    
    print("\n【混合检索优势】")
    print("  BM25权重(0.4) + 向量权重(0.6) = 最佳效果")
    print("  - 精确关键词查询 → BM25贡献更大")
    print("  - 自然语言查询 → 向量检索贡献更大")
    print("  - 结合两者优势，覆盖更多场景")
    
    print("\n" + "="*80)
    print("  测试完成！")
    print("="*80)


if __name__ == '__main__':
    test_bm25_search()
