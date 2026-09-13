"""
Hybrid retrieval workflow demo test
Simulates the complete retrieval workflow, comparing BM25, vector retrieval and hybrid retrieval
"""
import sys
import tempfile
import os
from pathlib import Path

# Add project root to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.document_parser import DocumentParser, DocumentChunk
from core.vector_store import VectorStoreManager
from core.retriever import HybridRetriever
from config import config


def create_test_documents():
    """
    Create test document data (simulating document chunks)
    Contains keywords of different topics for testing retrieval effectiveness
    """
    test_chunks = [
        DocumentChunk(
            text="Python是一种广泛使用的编程语言，特别适合数据科学和机器学习领域。Python拥有丰富的第三方库，如NumPy、Pandas、TensorFlow等。",
            metadata={'file_name': 'python_intro.txt', 'chunk_index': 0}
        ),
        DocumentChunk(
            text="机器学习是人工智能的一个重要分支，通过算法让计算机从数据中学习模式。深度学习是机器学习的子领域，使用神经网络进行复杂任务。",
            metadata={'file_name': 'ml_intro.txt', 'chunk_index': 0}
        ),
        DocumentChunk(
            text="FastAPI是一个现代、高性能的Python Web框架，基于Starlette和Pydantic构建。它支持异步编程，自动生成API文档。",
            metadata={'file_name': 'fastapi_intro.txt', 'chunk_index': 0}
        ),
        DocumentChunk(
            text="向量数据库是存储和检索高维向量的专用数据库，如ChromaDB、Pinecone等。它们广泛应用于语义搜索和推荐系统。",
            metadata={'file_name': 'vectordb_intro.txt', 'chunk_index': 0}
        ),
        DocumentChunk(
            text="BM25是一种基于词频和文档长度的检索算法，属于概率检索模型。BM25在关键词匹配场景中表现优异，特别适合精确查询。",
            metadata={'file_name': 'bm25_intro.txt', 'chunk_index': 0}
        ),
        DocumentChunk(
            text="RAG（检索增强生成）是一种结合检索和生成的AI技术。它首先从知识库检索相关文档，然后使用LLM生成回答，提高准确性。",
            metadata={'file_name': 'rag_intro.txt', 'chunk_index': 0}
        ),
        DocumentChunk(
            text="自然语言处理（NLP）是AI的重要应用领域，涉及文本分析、情感分析、机器翻译等任务。GPT和BERT是著名的NLP模型。",
            metadata={'file_name': 'nlp_intro.txt', 'chunk_index': 0}
        ),
        DocumentChunk(
            text="数据库索引是提高查询性能的关键技术，包括B树索引、哈希索引、全文索引等。合理的索引设计可以大幅提升系统性能。",
            metadata={'file_name': 'database_index.txt', 'chunk_index': 0}
        ),
        DocumentChunk(
            text="ChromaDB是一个开源的向量数据库，支持本地部署和云端服务。它提供了简单的API接口，适合RAG应用开发。",
            metadata={'file_name': 'chromadb_intro.txt', 'chunk_index': 0}
        ),
        DocumentChunk(
            text="文本分词是中文文本处理的基础步骤，jieba是常用的中文分词工具。分词质量直接影响检索和NLP任务的准确性。",
            metadata={'file_name': 'tokenizer_intro.txt', 'chunk_index': 0}
        ),
    ]
    
    return test_chunks


def test_bm25_retrieval(retriever, kb_id):
    """
    Test BM25 keyword retrieval
    Suitable for exact keyword query scenarios
    """
    print("\n" + "="*80)
    print("【BM25关键词检索测试】")
    print("="*80)
    
    # Force BM25 mode
    retriever.set_retrieval_mode('bm25')
    
    # Test query 1: exact keyword query
    query1 = "BM25算法"
    print(f"\n查询1: '{query1}'")
    print("-" * 40)
    
    result1 = retriever.retrieve(kb_id, query1, top_k=3)
    print(f"检索模式: {result1['mode']}")
    print(f"结果数量: {result1['count']}")
    print(f"是否有结果: {result1['has_results']}")
    
    if result1['results']:
        print("\n检索结果:")
        for i, doc in enumerate(result1['results'], 1):
            print(f"\n  [{i}] 来源: {doc['metadata'].get('file_name', '未知')}")
            print(f"      分数: {doc['score']:.4f}")
            print(f"      类型: {doc['type']}")
            print(f"      内容: {doc['text'][:80]}...")
    
    # Test query 2: multiple keyword query
    query2 = "Python 数据科学"
    print(f"\n查询2: '{query2}'")
    print("-" * 40)
    
    result2 = retriever.retrieve(kb_id, query2, top_k=3)
    print(f"结果数量: {result2['count']}")
    
    if result2['results']:
        print("\n检索结果:")
        for i, doc in enumerate(result2['results'], 1):
            print(f"\n  [{i}] 来源: {doc['metadata'].get('file_name', '未知')}")
            print(f"      分数: {doc['score']:.4f}")
            print(f"      内容: {doc['text'][:80]}...")
    
    # Test query 3: rare keyword query
    query3 = "jieba分词"
    print(f"\n查询3: '{query3}'")
    print("-" * 40)
    
    result3 = retriever.retrieve(kb_id, query3, top_k=2)
    print(f"结果数量: {result3['count']}")
    
    if result3['results']:
        print("\n检索结果:")
        for i, doc in enumerate(result3['results'], 1):
            print(f"\n  [{i}] 来源: {doc['metadata'].get('file_name', '未知')}")
            print(f"      分数: {doc['score']:.4f}")
            print(f"      内容: {doc['text'][:80]}...")
    
    return retriever


def test_vector_retrieval(retriever, kb_id):
    """
    Test vector semantic retrieval
    Suitable for semantic understanding query scenarios
    """
    print("\n" + "="*80)
    print("【向量语义检索测试】")
    print("="*80)
    
    # Force vector mode
    retriever.set_retrieval_mode('vector')
    
    # Test query 1: semantic query (without exact keywords)
    query1 = "如何选择合适的编程语言进行数据分析"
    print(f"\n查询1: '{query1}'")
    print("-" * 40)
    
    result1 = retriever.retrieve(kb_id, query1, top_k=3)
    print(f"检索模式: {result1['mode']}")
    print(f"结果数量: {result1['count']}")
    
    if result1['results']:
        print("\n检索结果:")
        for i, doc in enumerate(result1['results'], 1):
            print(f"\n  [{i}] 来源: {doc['metadata'].get('file_name', '未知')}")
            print(f"      分数: {doc['score']:.4f}")
            print(f"      类型: {doc['type']}")
            print(f"      内容: {doc['text'][:80]}...")
    
    # Test query 2: similar semantic query
    query2 = "什么是检索增强生成技术"
    print(f"\n查询2: '{query2}'")
    print("-" * 40)
    
    result2 = retriever.retrieve(kb_id, query2, top_k=3)
    print(f"结果数量: {result2['count']}")
    
    if result2['results']:
        print("\n检索结果:")
        for i, doc in enumerate(result2['results'], 1):
            print(f"\n  [{i}] 来源: {doc['metadata'].get('file_name', '未知')}")
            print(f"      分数: {doc['score']:.4f}")
            print(f"      内容: {doc['text'][:80]}...")
    
    return retriever


def test_hybrid_retrieval(retriever, kb_id):
    """
    Test hybrid retrieval
    Combines advantages of BM25 and vector retrieval
    """
    print("\n" + "="*80)
    print("【混合检索测试】")
    print("="*80)
    
    # Use hybrid mode
    retriever.set_retrieval_mode('hybrid')
    
    # Test query 1: query with both keywords and semantics
    query1 = "向量数据库和语义检索"
    print(f"\n查询1: '{query1}'")
    print("-" * 40)
    
    result1 = retriever.retrieve(kb_id, query1, top_k=3)
    print(f"检索模式: {result1['mode']}")
    print(f"结果数量: {result1['count']}")
    
    if result1['results']:
        print("\n检索结果:")
        for i, doc in enumerate(result1['results'], 1):
            print(f"\n  [{i}] 来源: {doc['metadata'].get('file_name', '未知')}")
            print(f"      分数: {doc['score']:.4f}")
            print(f"      类型: {doc['type']}")
            if 'bm25_score' in doc:
                print(f"      BM25分数: {doc['bm25_score']:.4f}")
            if 'vector_score' in doc:
                print(f"      向量分数: {doc['vector_score']:.4f}")
            print(f"      内容: {doc['text'][:80]}...")
    
    # Test query 2: technical concept query
    query2 = "如何提高数据库查询性能"
    print(f"\n查询2: '{query2}'")
    print("-" * 40)
    
    result2 = retriever.retrieve(kb_id, query2, top_k=3)
    print(f"结果数量: {result2['count']}")
    
    if result2['results']:
        print("\n检索结果:")
        for i, doc in enumerate(result2['results'], 1):
            print(f"\n  [{i}] 来源: {doc['metadata'].get('file_name', '未知')}")
            print(f"      分数: {doc['score']:.4f}")
            print(f"      类型: {doc['type']}")
            print(f"      内容: {doc['text'][:80]}...")
    
    return retriever


def test_empty_query(retriever, kb_id):
    """
    Test empty retrieval fallback
    Handling when no results are retrieved
    """
    print("\n" + "="*80)
    print("【空检索降级测试】")
    print("="*80)
    
    retriever.set_retrieval_mode('hybrid')
    
    # Test irrelevant query
    query = "量子力学和薛定谔方程"
    print(f"\n查询: '{query}' (与知识库无关)")
    print("-" * 40)
    
    result = retriever.retrieve(kb_id, query, top_k=3)
    print(f"结果数量: {result['count']}")
    print(f"是否有结果: {result['has_results']}")
    
    if not result['has_results']:
        print("\n降级回复:")
        print(f"  {result['empty_response']}")
    
    return retriever


def run_full_test():
    """
    Run the complete retrieval workflow test
    """
    print("\n" + "="*80)
    print("  混合检索流程完整测试")
    print("="*80)
    print("\n【测试说明】")
    print("  本测试模拟完整的RAG检索流程，包含：")
    print("  1. 创建临时知识库")
    print("  2. 添加测试文档（10个不同主题的文档块）")
    print("  3. 分别测试BM25、向量、混合三种检索模式")
    print("  4. 对比不同检索模式的效果差异")
    print("\n【注意事项】")
    print("  - 需要Ollama服务运行并安装embedding模型")
    print("  - 测试结束后会自动清理临时数据")
    print("-" * 80)
    
    try:
        # 1. Initialize components
        print("\n[步骤1] 初始化组件...")
        vector_store = VectorStoreManager()
        retriever = HybridRetriever(vector_store)
        
        # 2. Create temporary knowledge base
        kb_id = "test_kb_demo"
        kb_name = "测试知识库"
        
        print(f"\n[步骤2] 创建临时知识库: {kb_id}")
        
        # Delete old test knowledge base (if exists)
        if vector_store.collection_exists(kb_id):
            print(f"  清理旧测试数据...")
            vector_store.delete_collection(kb_id)
        
        # Create new test knowledge base
        success = vector_store.create_collection(kb_id, kb_name)
        if not success:
            print("  [失败] 无法创建知识库")
            return
        
        print(f"  [成功] 知识库已创建")
        
        # 3. Add test documents
        print("\n[步骤3] 创建测试文档数据...")
        test_chunks = create_test_documents()
        print(f"  创建了 {len(test_chunks)} 个文档块")
        
        print("\n[步骤4] 向量化文档（需要Ollama embedding模型）...")
        print("  注意: 这可能需要一些时间，取决于Ollama服务状态")
        
        try:
            added_count = vector_store.add_documents(kb_id, test_chunks)
            print(f"  [成功] 向量化完成，添加了 {added_count} 个向量")
            
            # Refresh BM25 index
            print("\n[步骤5] 构建BM25索引...")
            retriever.refresh_bm25_index(kb_id)
            print("  [成功] BM25索引已构建")
            
        except Exception as e:
            print(f"  [警告] 向量化失败: {e}")
            print("  可能原因: Ollama服务未启动或embedding模型未安装")
            print("  继续使用纯BM25测试...")
            
            # Manually build BM25 index (no vectors needed)
            docs = [{'id': str(i), 'text': chunk.text, 'metadata': chunk.metadata} 
                    for i, chunk in enumerate(test_chunks)]
            retriever._ensure_bm25_index(kb_id, docs)
            print("  [成功] BM25索引已手动构建（纯文本模式）")
        
        # 4. Execute retrieval tests
        print("\n" + "="*80)
        print("  开始执行检索测试")
        print("="*80)
        
        # Test BM25 retrieval
        retriever = test_bm25_retrieval(retriever, kb_id)
        
        # Test vector retrieval
        retriever = test_vector_retrieval(retriever, kb_id)
        
        # Test hybrid retrieval
        retriever = test_hybrid_retrieval(retriever, kb_id)
        
        # Test empty retrieval fallback
        retriever = test_empty_query(retriever, kb_id)
        
        # 5. Clean up test data
        print("\n" + "="*80)
        print("  测试完成，清理临时数据")
        print("="*80)
        
        vector_store.delete_collection(kb_id)
        print(f"  [成功] 知识库 {kb_id} 已删除")
        
        # 6. Summary
        print("\n" + "="*80)
        print("  测试总结")
        print("="*80)
        print("\n【BM25检索特点】")
        print("  - 精确关键词匹配，适合特定术语查询")
        print("  - 基于词频和文档长度评分")
        print("  - 不依赖语义理解，无需embedding模型")
        
        print("\n【向量检索特点】")
        print("  - 语义理解能力强，适合自然语言查询")
        print("  - 可以找到语义相关但无关键词重叠的文档")
        print("  - 需要embedding模型，依赖向量相似度")
        
        print("\n【混合检索特点】")
        print("  - 结合关键词和语义优势")
        print("  - BM25权重0.4 + 向量权重0.6")
        print("  - 适合大多数RAG应用场景")
        
        print("\n" + "="*80)
        print("  测试成功完成！")
        print("="*80)
        
    except Exception as e:
        print(f"\n[错误] 测试执行失败: {e}")
        import traceback
        traceback.print_exc()
        
        # Clean up residual data
        try:
            vector_store = VectorStoreManager()
            if vector_store.collection_exists("test_kb_demo"):
                vector_store.delete_collection("test_kb_demo")
                print("  [清理] 残留数据已删除")
        except:
            pass


if __name__ == '__main__':
    run_full_test()
