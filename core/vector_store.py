"""
Vector Store Management Module
Local vector storage based on ChromaDB, supports multi-knowledge base isolation
"""
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config import config
from service.logger import get_logger
from service.i18n import _

logger = get_logger('vector_store')


class VectorStoreManager:
    """Vector store manager"""

    def __init__(self):
        self.persist_directory = Path(config.get('vector_store.persist_directory', './storage/vectordb'))
        self.persist_directory.mkdir(parents=True, exist_ok=True)

        self.collection_prefix = config.get('vector_store.collection_prefix', 'kb_')
        self.top_k = config.get('vector_store.top_k', 5)
        self.similarity_threshold = config.get('vector_store.similarity_threshold', 0.3)
        # LLM infrastructure values come exclusively from .env via config.
        # The startup check in main.py validates these before the server launches.
        self.llm_provider = config.get('llm.provider', '')
        self.embedding_provider = config.get('embedding.provider', '')

        # Ollama embedding fields (used when embedding.provider=ollama, or as fallback)
        self.ollama_embedding_model = config.get('ollama.embedding_model', '')
        self.ollama_base_url = config.get('ollama.llm_base_url', '')

        # OpenAI embedding fields (used when embedding.provider=openai)
        self.openai_embedding_model = config.get('openai.embedding_model', '')
        self.openai_api_key = config.get('openai.api_key', '')
        self.openai_base_url = config.get('openai.base_url', '')

        # Local HuggingFace embedding (used when embedding.provider=local)
        self.local_embedding_path = config.get('embedding.local_model_path', '')

        # Dimension: pull from whichever provider is active
        provider_dim_map = {
            'ollama': config.get('ollama.embedding_dim', 768),
            'openai': config.get('openai.embedding_dim', 1536),
            'local': config.get('embedding.local_dim', 768),
        }
        self.embedding_dim = provider_dim_map.get(self.embedding_provider, 768)

        self._chroma_client = None
        self._collections = {}
        self._embedding_func = None

    def _get_chroma_client(self):
        """Get ChromaDB client (lazy loading)"""
        if self._chroma_client is None:
            import chromadb
            self._chroma_client = chromadb.PersistentClient(path=str(self.persist_directory))
            logger.info(f"ChromaDB client initialized, persist path set to: {self.persist_directory}")
        return self._chroma_client

    def _get_embedding_func(self):
        """Get Embedding function based on the configured embedding provider.

        Supports three backends:
          - 'ollama':  OllamaEmbeddingFunction (remote Ollama service)
          - 'openai':  OpenAIEmbeddingFunction (or OpenAI-compatible)
          - 'local':   SentenceTransformerEmbeddingFunction (local HF model)

        Raises instead of silently substituting another model: a fallback would change the
        embedding space behind the user's back (ChromaDB's default is a 384-dim MiniLM, and
        it is downloaded on first use), producing vectors that cannot be compared with the
        configured model's.
        """
        if self._embedding_func is not None:
            return self._embedding_func

        from chromadb.utils import embedding_functions

        provider = (self.embedding_provider or self.llm_provider).lower()
        try:
            if provider == 'openai':
                self._embedding_func = embedding_functions.OpenAIEmbeddingFunction(
                    api_key=self.openai_api_key,
                    model_name=self.openai_embedding_model,
                    api_base=self.openai_base_url or None,
                )
                logger.info(f"OpenAI Embedding function initialized: {self.openai_embedding_model}")
            elif provider == 'local':
                if not self.local_embedding_path:
                    raise ValueError("EMBEDDING_PROVIDER=local but LOCAL_EMBEDDING_MODEL_PATH is not set")
                # sentence-transformers is an optional dependency (it pulls in torch):
                # install it with `uv sync --extra local-embedding`.
                self._embedding_func = embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name=self.local_embedding_path
                )
                logger.info(f"Local HuggingFace Embedding function initialized: {self.local_embedding_path}")
            else:
                # Default / Ollama path
                self._embedding_func = embedding_functions.OllamaEmbeddingFunction(
                    url=f"{self.ollama_base_url}/api/embeddings",
                    model_name=self.ollama_embedding_model
                )
                logger.info(f"Ollama Embedding function initialized: {self.ollama_embedding_model}")
        except Exception as e:
            hint = ""
            if provider == 'local':
                hint = (" Install the optional dependency with "
                        "`uv sync --extra local-embedding`, or set EMBEDDING_PROVIDER to "
                        "ollama/openai in .env.")
            logger.error(f"Failed to initialize the embedding function for provider "
                         f"'{provider}': {e}.{hint}")
            raise RuntimeError(
                f"Embedding provider '{provider}' could not be initialized: {e}.{hint}"
            ) from e
        return self._embedding_func

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Embed texts with the very same function that produced the stored vectors.

        Sentence-level tracing and the semantic-consistency metric compare an answer against the
        retrieved context, so their similarity only means something when both sides are embedded
        in the same space as retrieval. Routing them through here also removes the need for them
        to know which provider is active — including 'local', which has no HTTP endpoint and so
        cannot be reached through an LLM adapter at all.

        Args:
            texts: Texts to embed.

        Returns:
            One vector per input text, in the same order.

        Raises:
            RuntimeError: If the configured embedding provider cannot be initialized.
        """
        if not texts:
            return []
        # ChromaDB's embedding functions take the batch as their first positional argument in
        # both the legacy (List[str]) and current (input) calling conventions.
        vectors = self._get_embedding_func()(texts)
        return [[float(x) for x in vector] for vector in vectors]

    def _get_collection_name(self, kb_id: str) -> str:
        """Generate collection name"""
        return f"{self.collection_prefix}{kb_id}"

    def create_collection(self, kb_id: str, kb_name: str = "") -> bool:
        """
        Create knowledge base collection

        Args:
            kb_id: Knowledge base ID
            kb_name: Knowledge base name

        Returns:
            Whether creation succeeded
        """
        try:
            client = self._get_chroma_client()
            collection_name = self._get_collection_name(kb_id)

            # Check if already exists
            existing = [c.name for c in client.list_collections()]
            if collection_name in existing:
                logger.warning(f"Collection already exists: {collection_name}")
                return False

            collection = client.create_collection(
                name=collection_name,
                embedding_function=self._get_embedding_func(),
                # hnsw:space defaults to squared L2. The score conversion in search() is
                # "1 - distance", which is only meaningful for cosine distance, so the space
                # must be set explicitly or every similarity score is wrong.
                metadata={"kb_id": kb_id, "kb_name": kb_name, "hnsw:space": "cosine"}
            )

            self._collections[kb_id] = collection
            logger.info(f"Collection created successfully: {kb_id} ({kb_name})")
            return True

        except Exception as e:
            logger.error(f"Collection creation failed: {e}")
            return False

    def delete_collection(self, kb_id: str) -> bool:
        """
        Delete knowledge base collection

        Args:
            kb_id: Knowledge base ID

        Returns:
            Whether deletion succeeded
        """
        try:
            client = self._get_chroma_client()
            collection_name = self._get_collection_name(kb_id)

            client.delete_collection(collection_name)

            if kb_id in self._collections:
                del self._collections[kb_id]

            logger.info(f"Collection deleted successfully: {kb_id}")
            return True

        except Exception as e:
            logger.error(f"Collection deletion failed: {e}")
            return False

    def get_collection(self, kb_id: str):
        """Get knowledge base collection"""
        if kb_id in self._collections:
            return self._collections[kb_id]

        try:
            client = self._get_chroma_client()
            collection_name = self._get_collection_name(kb_id)
            collection = client.get_collection(
                name=collection_name,
                embedding_function=self._get_embedding_func()
            )
            self._collections[kb_id] = collection
            return collection
        except Exception as e:
            logger.error(f"Failed to get collection {kb_id}: {e}")
            return None

    def get_documents(self, kb_id: str) -> List[Dict]:
        """Every stored chunk of a knowledge base, without initializing the embedding function.

        `get_collection()` attaches the configured embedding function, and constructing it loads
        the whole embedding model (seconds and hundreds of MB for a local model). The keyword index
        only needs the texts, and a BM25 rebuild happens on the first query after a restart - so
        paying the model load there delayed the index past every bounded wait while the vector
        route loaded the same model on its own path anyway.

        Args:
            kb_id: Knowledge base ID

        Returns:
            One dict per chunk (`id`, `text`, `metadata`); empty when the knowledge base does not
            exist or has no documents.
        """
        try:
            client = self._get_chroma_client()
            # No embedding_function: `collection.get()` never embeds, and omitting it keeps the
            # model out of this path (see the docstring above).
            collection = client.get_collection(name=self._get_collection_name(kb_id))
            result = collection.get(include=['documents', 'metadatas'])
        except Exception as e:
            logger.warning(f"Failed to read documents of knowledge base {kb_id}: {e}")
            return []

        ids = result.get('ids') or []
        documents = result.get('documents') or []
        metadatas = result.get('metadatas') or []
        return [
            {
                'id': ids[i],
                'text': documents[i] if i < len(documents) else "",
                'metadata': metadatas[i] if i < len(metadatas) else {}
            }
            for i in range(len(ids))
        ]

    def collection_exists(self, kb_id: str) -> bool:
        """Check if collection exists"""
        try:
            client = self._get_chroma_client()
            collection_name = self._get_collection_name(kb_id)
            existing = [c.name for c in client.list_collections()]
            return collection_name in existing
        except Exception:
            return False

    def add_documents(self, kb_id: str, chunks: List[Any], progress_callback: Optional[Callable] = None) -> int:
        """
        Add documents to vector store

        Args:
            kb_id: Knowledge base ID
            chunks: Document chunk list (DocumentChunk objects)
            progress_callback: Progress callback

        Returns:
            Number of documents added
        """
        collection = self.get_collection(kb_id)
        if collection is None:
            raise ValueError(_('vector.kb_not_found', None, kb_id))

        if not chunks:
            return 0

        total = len(chunks)
        logger.info(f"Start embedding {total} document chunks to knowledge base {kb_id}")

        ids = []
        documents = []
        metadatas = []

        for i, chunk in enumerate(chunks):
            chunk_id = str(uuid.uuid4())
            ids.append(chunk_id)
            documents.append(chunk.text)
            
            metadata = chunk.metadata if hasattr(chunk, 'metadata') else {}
            cleaned_metadata = {}
            for key, value in metadata.items():
                if value is not None:
                    if isinstance(value, (int, float, bool, str)):
                        cleaned_metadata[key] = value
                    else:
                        cleaned_metadata[key] = str(value)
            metadatas.append(cleaned_metadata)

            if progress_callback and (i + 1) % max(1, total // 20) == 0:
                progress = (i + 1) / total * 100
                progress_callback(progress, f"Embedding document chunk {i + 1}/{total}")

        # Batch add (process in batches to avoid memory overflow)
        batch_size = 50
        added_count = 0

        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i:i + batch_size]
            batch_docs = documents[i:i + batch_size]
            batch_metas = metadatas[i:i + batch_size]

            try:
                collection.add(
                    ids=batch_ids,
                    documents=batch_docs,
                    metadatas=batch_metas
                )
                added_count += len(batch_ids)
            except Exception as e:
                logger.error(f"Batch add failed (batch {i // batch_size}): {e}")
                # Try adding individually
                for j in range(len(batch_ids)):
                    try:
                        collection.add(
                            ids=[batch_ids[j]],
                            documents=[batch_docs[j]],
                            metadatas=[batch_metas[j]]
                        )
                        added_count += 1
                    except Exception as e2:
                        logger.error(f"Failed to add document chunk {batch_ids[j]}: {e2}")

        if progress_callback:
            progress_callback(100, f"Embedding completed, added {added_count} document chunks")

        logger.info(f"Embedding completed, added {added_count}/{total} document chunks to knowledge base {kb_id}")
        return added_count

    def search(self, kb_id: str, query: str, top_k: int = None) -> List[Dict]:
        """
        Vector similarity retrieval

        Args:
            kb_id: Knowledge base ID
            query: Query text
            top_k: Return count, default uses config value

        Returns:
            Retrieval result list, each result contains id, text, metadata, score
        """
        collection = self.get_collection(kb_id)
        if collection is None:
            raise ValueError(_('vector.kb_not_found', None, kb_id))

        if top_k is None:
            top_k = self.top_k

        try:
            results = collection.query(
                query_texts=[query],
                n_results=top_k
            )

            formatted_results = []
            if results and results.get('ids') and len(results['ids']) > 0:
                ids = results['ids'][0]
                documents = results.get('documents', [[]])[0]
                metadatas = results.get('metadatas', [[]])[0]
                distances = results.get('distances', [[]])[0]

                for i in range(len(ids)):
                    # ChromaDB distance is a distance metric, convert to similarity score
                    # Assuming cosine distance, smaller distance means higher similarity
                    distance = distances[i] if i < len(distances) else 1.0
                    score = max(0.0, 1.0 - distance)

                    # Similarity filter
                    if score >= self.similarity_threshold:
                        formatted_results.append({
                            'id': ids[i],
                            'text': documents[i] if i < len(documents) else "",
                            'metadata': metadatas[i] if i < len(metadatas) else {},
                            'score': score,
                            'type': 'vector'
                        })

            logger.debug(f"Vector retrieval completed, returned {len(formatted_results)} results")
            return formatted_results

        except Exception as e:
            logger.error(f"Vector retrieval failed: {e}")
            return []

    def get_document_count(self, kb_id: str) -> int:
        """Get knowledge base document count"""
        collection = self.get_collection(kb_id)
        if collection is None:
            return 0

        try:
            return collection.count()
        except Exception:
            return 0

    def delete_documents_by_file(self, kb_id: str, file_name: str) -> int:
        """
        Delete vector data by file name

        Args:
            kb_id: Knowledge base ID
            file_name: File name (original file name)

        Returns:
            Number of documents deleted
        """
        collection = self.get_collection(kb_id)
        if collection is None:
            logger.warning(f"Knowledge base not found, cannot delete documents by file: {kb_id}")
            return 0

        try:
            count_before = collection.count()
            collection.delete(where={"file_name": file_name})
            count_after = collection.count()
            deleted = count_before - count_after
            logger.info(f"Deleted documents from knowledge base {kb_id}, file: {file_name}, count: {deleted}")
            return deleted
        except Exception as e:
            logger.error(f"Delete documents from knowledge base {kb_id}: {e}")
            return 0

    def list_collections(self) -> List[Dict]:
        """List all knowledge base collections"""
        try:
            client = self._get_chroma_client()
            collections = client.list_collections()
            result = []
            for col in collections:
                result.append({
                    'name': col.name,
                    'metadata': col.metadata or {},
                    'count': col.count()
                })
            return result
        except Exception as e:
            logger.error(f"List collections failed: {e}")
            return []

    def get_all_documents(self, kb_id: str, limit: int = 100, offset: int = 0) -> List[Dict]:
        """
        Get all documents (chunks) in knowledge base

        Args:
            kb_id: Knowledge base ID
            limit: Return count limit
            offset: Offset

        Returns:
            Document list, each document contains id, text, metadata
        """
        collection = self.get_collection(kb_id)
        if collection is None:
            return []

        try:
            all_ids = collection.get()['ids']
            total = len(all_ids)
            
            start = offset
            end = offset + limit
            paginated_ids = all_ids[start:end]
            
            if not paginated_ids:
                return []

            results = collection.get(ids=paginated_ids)
            
            documents = []
            ids = results.get('ids', [])
            texts = results.get('documents', [])
            metadatas = results.get('metadatas', [])
            
            for i in range(len(ids)):
                documents.append({
                    'id': ids[i],
                    'text': texts[i] if i < len(texts) else '',
                    'metadata': metadatas[i] if i < len(metadatas) else {}
                })
            
            logger.debug(f"Retrieved documents from knowledge base {kb_id}, total {total}, limit {limit}, offset {offset}, returned {len(documents)} documents")
            return {
                'documents': documents,
                'total': total,
                'limit': limit,
                'offset': offset
            }

        except Exception as e:
            logger.error(f"Retrieved documents from knowledge base {kb_id}: {e}")
            return []

    def get_document_by_id(self, kb_id: str, doc_id: str) -> Optional[Dict]:
        """
        Get single document by ID

        Args:
            kb_id: Knowledge base ID
            doc_id: Document ID

        Returns:
            Document info, contains id, text, metadata, embedding
        """
        collection = self.get_collection(kb_id)
        if collection is None:
            return None

        try:
            results = collection.get(ids=[doc_id], include=['documents', 'metadatas', 'embeddings'])
            
            if results and results.get('ids') and len(results['ids']) > 0:
                return {
                    'id': results['ids'][0],
                    'text': results.get('documents', [None])[0],
                    'metadata': results.get('metadatas', [{}])[0],
                    'embedding': results.get('embeddings', [None])[0]
                }
            return None

        except Exception as e:
            logger.error(f"Retrieved document from knowledge base {kb_id}/{doc_id}: {e}")
            return None


# Shared instance. ChromaDB keeps one client per persist path, and every consumer must embed
# with the same function, so the process uses a single manager rather than one per module.
vector_store_manager = VectorStoreManager()
