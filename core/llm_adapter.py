"""
LLM adapter module
Provide unified LLM call interface, supporting multiple backends (Ollama, OpenAI, Anthropic, etc.)
Switch backend by configuration, without modifying business code
"""

import json
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Generator, List, Optional

import requests

from config import config
from service.logger import get_logger
from service.i18n import _

logger = get_logger('llm_adapter')


class LLMAdapter(ABC):
    """LLM adapter abstract base class"""

    @abstractmethod
    def chat(self, model: str, messages: List[Dict], stream: bool = False, **kwargs) -> Any:
        """
        Call LLM for conversation

        Args:
            model: Model Name 
            messages: message list
            stream: Whether to stream the response
            **kwargs: Additional parameters

        Returns:
            Conversation result
        """
        pass

    @abstractmethod
    def embeddings(self, model: str, prompt: str) -> List[float]:
        """
        Get text embedding vector 
        It can use either local embedding model or remote one, 
        
        Args:
            model: Embedding model name
            prompt: Input text

        Returns:
            Embedding vector
        """
        pass

    @abstractmethod
    def list_models(self) -> List[str]:
        """
        Get available model list

        Returns:
            List of model names
        """
        pass

    @abstractmethod
    def check_health(self) -> Dict[str, Any]:
        """
        Check the health status of the service

        Returns:
            Health status dictionary
        """
        pass


class OllamaAdapter(LLMAdapter):
    """Ollama adapter
    
    Connects to a local (or remote) Ollama server. The base URL is resolved
    from config (which may be overridden by the OLLAMA_BASE_URL env var).
    """

    def __init__(self, base_url: str = None, timeout: int = 60):
        if base_url is None:
            base_url = config.get('ollama.llm_base_url', 'http://localhost:11434')
        self.base_url = base_url
        self.timeout = timeout
        self._client = None

    def _get_client(self):
        """Get Ollama client (lazy loading)"""
        if self._client is None:
            try:
                import ollama
                self._client = ollama.Client(host=self.base_url, timeout=self.timeout)
                logger.info(f"Ollama client initialized successfully: {self.base_url}")
            except Exception as e:
                logger.error(f"Failed to initialize Ollama client: {e}")
                raise
        return self._client

    def chat(self, model: str, messages: List[Dict], stream: bool = False, **kwargs) -> Any:
        client = self._get_client()
        response = client.chat(model=model, messages=messages, stream=stream, **kwargs)
        return response

    def embeddings(self, model: str, prompt: str) -> List[float]:
        client = self._get_client()
        response = client.embeddings(model=model, prompt=prompt)
        return response.get('embedding', [])

    def list_models(self) -> List[str]:
        client = self._get_client()
        models = client.list()

        if hasattr(models, 'models'):
            model_names = [m.model for m in models.models]
        else:
            models_dict = models.model_dump() if hasattr(models, 'model_dump') else dict(models)
            model_names = [m.get('model', '') for m in models_dict.get('models', [])]

        return model_names

    def check_health(self) -> Dict[str, Any]:
        try:
            model_names = self.list_models()
            return {
                'healthy': True,
                'provider': 'ollama',
                'base_url': self.base_url,
                'available_models': model_names
            }
        except Exception as e:
            return {
                'healthy': False,
                'provider': 'ollama',
                'base_url': self.base_url,
                'error': str(e)
            }


class OpenAIAdapter(LLMAdapter):
    """OpenAI adapter
    Connects to the OpenAI API (or a compatible endpoint). The base URL and
    API key are resolved from config (which may be overridden by env vars
    OPENAI_BASE_URL / OPENAI_API_KEY).
    """

    def __init__(self, api_key: str, base_url: str = None, timeout: int = 60):
        self.api_key = api_key
        self.base_url = base_url or config.get('openai.base_url', 'https://api.openai.com/v1')
        self.timeout = timeout

    def _make_request(self, endpoint: str, method: str = 'POST', **kwargs) -> Dict:
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
        url = f"{self.base_url}{endpoint}"

        try:
            response = requests.request(method, url, headers=headers, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"OpenAI API request failed for {endpoint}: {e}")
            raise

    def chat(self, model: str, messages: List[Dict], stream: bool = False, **kwargs) -> Any:
        data = {
            'model': model,
            'messages': messages,
            'stream': stream,
            **kwargs
        }

        if stream:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'},
                json=data,
                stream=True,
                timeout=self.timeout
            )
            response.raise_for_status()

            def generate_stream():
                for chunk in response.iter_lines():
                    if chunk:
                        chunk_str = chunk.decode('utf-8').replace('data: ', '')
                        if chunk_str == '[DONE]':
                            break
                        try:
                            yield json.loads(chunk_str)
                        except json.JSONDecodeError:
                            continue

            return generate_stream()
        else:
            result = self._make_request('/chat/completions', json=data)
            return {
                'message': {
                    'content': result['choices'][0]['message']['content']
                }
            }

    def embeddings(self, model: str, prompt: str) -> List[float]:
        data = {
            'model': model,
            'input': prompt
        }
        result = self._make_request('/embeddings', json=data)
        return result['data'][0]['embedding']

    def list_models(self) -> List[str]:
        try:
            result = self._make_request('/models', method='GET')
            return [m['id'] for m in result.get('data', [])]
        except Exception as e:
            logger.error(f" OpenAI model list failed to get: {e}")
            return []

    def check_health(self) -> Dict[str, Any]:
        try:
            model_names = self.list_models()
            return {
                'healthy': len(model_names) > 0,
                'provider': 'openai',
                'base_url': self.base_url,
                'available_models': model_names
            }
        except Exception as e:
            return {
                'healthy': False,
                'provider': 'openai',
                'base_url': self.base_url,
                'error': str(e)
            }


class LLMAdapterFactory:
    """LLM adapter factory"""

    _adapters: Dict[str, LLMAdapter] = {}

    @classmethod
    def get_adapter(cls, provider: Optional[str] = None) -> LLMAdapter:
        """
        Get LLM adapter instance

        Args:
            provider: Provider name (ollama/openai), defaults to provider in config

        Returns:
            LLM adapter instance
        """
        if provider is None:
            provider = config.get('llm.provider', 'ollama')

        if provider in cls._adapters:
            return cls._adapters[provider]

        if provider == 'ollama':
            adapter = OllamaAdapter(
                base_url=config.get_llm_base_url(),
                timeout=config.get('ollama.timeout', 60)
            )
        elif provider == 'openai':
            # API key is injected centrally by config/__init__.py from the
            # OPENAI_API_KEY env var (see .env.example), so we just read config.
            api_key = config.get('openai.api_key', '')
            adapter = OpenAIAdapter(
                api_key=api_key,
                base_url=config.get_llm_base_url(),
                timeout=config.get('openai.timeout', 60)
            )
        else:
            raise ValueError(_('llm.unsupported_provider', None, provider))

        cls._adapters[provider] = adapter
        logger.info(f"Created LLM adapter: {provider}")
        return adapter

    @classmethod
    def reset(cls):
        """Reset adapter cache"""
        cls._adapters.clear()
        logger.info("LLM cache reset successfully")


llm_adapter = LLMAdapterFactory.get_adapter()