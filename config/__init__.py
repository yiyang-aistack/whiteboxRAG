"""
Config module
Responsible for loading and managing global YAML config and scenario config.

Configuration priority (highest to lowest):
  1. Environment variables (loaded from .env via python-dotenv, or set in the shell)
  2. config/settings.yaml
  3. Hardcoded fallback defaults in individual modules

All sensitive / environment-specific values (API keys, service URLs, model names)
should be declared in .env (see .env.example). They are injected into the config
dict centrally here, so downstream code simply calls config.get('ollama.llm_base_url')
without needing to know whether the value came from YAML or an env var.
"""
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml

# Try loading .env file from project root (optional dependency, silently skip if not installed)
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent.parent / '.env'
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Environment variable override mapping
# Maps ENV_VAR_NAME -> (config_section, config_key, optional_type_converter)
# If an env var is set (non-empty), its value overrides the YAML config.
# This is the single injection point — no other module should call os.getenv
# for these keys directly.
# ---------------------------------------------------------------------------
_ENV_OVERRIDES: Dict[str, tuple] = {
    # LLM provider selection
    'LLM_PROVIDER':           ('llm', 'provider'),
    # Ollama (local)
    'OLLAMA_BASE_URL':        ('ollama', 'llm_base_url'),
    'OLLAMA_LLM_MODEL':       ('ollama', 'llm_model'),
    'OLLAMA_EMBEDDING_MODEL': ('ollama', 'embedding_model'),
    'OLLAMA_EMBEDDING_DIM':   ('ollama', 'embedding_dim', int),
    'OLLAMA_TIMEOUT':         ('ollama', 'timeout', int),
    'OLLAMA_MAX_RETRIES':     ('ollama', 'max_retries', int),
    # OpenAI (cloud)
    'OPENAI_API_KEY':         ('openai', 'api_key'),
    'OPENAI_BASE_URL':        ('openai', 'base_url'),
    'OPENAI_LLM_MODEL':       ('openai', 'llm_model'),
    'OPENAI_EMBEDDING_MODEL': ('openai', 'embedding_model'),
    'OPENAI_EMBEDDING_DIM':   ('openai', 'embedding_dim', int),
    'OPENAI_TIMEOUT':         ('openai', 'timeout', int),
    # Application security
    'API_KEY':                ('api', 'api_key'),
    'API_RATE_LIMIT':         ('api', 'rate_limit', int),
}


class ConfigManager:
    """Config manager - Singleton pattern"""

    _instance = None
    _config = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize config"""
        if self._config is None:
            self.load_config()

    def load_config(self, config_path: str = None) -> Dict[str, Any]:
        """
        Load YAML config file

        Args:
            config_path: Config file path, defaults to settings.yaml in current directory

        Returns:
            Config dictionary
        """
        if config_path is None:
            config_path = Path(__file__).parent / "settings.yaml"
        else:
            config_path = Path(config_path)

        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found at: {config_path}")

        with open(config_path, 'r', encoding='utf-8') as f:
            self._config = yaml.safe_load(f)

        # Apply environment variable overrides (highest priority)
        self._apply_env_overrides()

        return self._config

    def _apply_env_overrides(self):
        """Apply environment variable overrides on top of the YAML config.

        For each entry in _ENV_OVERRIDES, if the environment variable is set
        and non-empty, its value replaces the corresponding YAML config key.
        Integer-valued keys are automatically type-converted.
        """
        for env_key, mapping in _ENV_OVERRIDES.items():
            value = os.getenv(env_key)
            if value is None or value == '':
                continue

            section, config_key = mapping[0], mapping[1]
            type_fn = mapping[2] if len(mapping) > 2 else None

            if type_fn is not None:
                try:
                    value = type_fn(value)
                except (ValueError, TypeError):
                    # Skip invalid value, keep YAML default
                    continue

            if section not in self._config:
                self._config[section] = {}
            self._config[section][config_key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get config item (supports dot-separated access to nested config)

        Args:
            key: Config key, e.g. 'system.port'
            default: Default value

        Returns:
            Config value
        """
        if self._config is None:
            self.load_config()

        keys = key.split('.')
        value = self._config

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default

        return value

    # ------------------------------------------------------------------
    # Provider-aware convenience accessors
    #
    # These helpers centralize the if/else branching on llm.provider that
    # was previously duplicated across 6+ modules. All modules should use
    # these instead of reading model names / URLs with inline conditionals.
    # ------------------------------------------------------------------

    def get_llm_model(self, purpose: str = None) -> str:
        """Resolve the LLM model name based on the configured provider.

        Args:
            purpose: Optional purpose key (e.g. 'intent'). When set, checks
                     for a purpose-specific Ollama model (ollama.<purpose>_model)
                     before falling back to the default LLM model.

        Returns:
            The resolved model name string.
        """
        provider = self.get('llm.provider', 'ollama')
        if provider == 'openai':
            return self.get('openai.llm_model', 'gpt-4o')
        # For Ollama, allow purpose-specific model override
        if purpose:
            purpose_model = self.get(f'ollama.{purpose}_model')
            if purpose_model:
                return purpose_model
        return self.get('ollama.llm_model', 'qwen2.5:7b')

    def get_embedding_model(self) -> str:
        """Resolve the embedding model name based on the configured provider.

        Returns:
            The resolved embedding model name string.
        """
        provider = self.get('llm.provider', 'ollama')
        if provider == 'openai':
            return self.get('openai.embedding_model', 'text-embedding-3-small')
        return self.get('ollama.embedding_model', 'nomic-embed-text:latest')

    def get_llm_base_url(self) -> str:
        """Resolve the LLM service base URL based on the configured provider.

        Returns:
            The resolved base URL string.
        """
        provider = self.get('llm.provider', 'ollama')
        if provider == 'openai':
            return self.get('openai.base_url', 'https://api.openai.com/v1')
        return self.get('ollama.llm_base_url', 'http://localhost:11434')

    def get_all(self) -> Dict[str, Any]:
        """Get all config"""
        if self._config is None:
            self.load_config()
        return self._config.copy()

    def __getattr__(self, name: str) -> Any:
        """
        Support accessing top-level config items via attribute.
        e.g. config.ollama returns the ollama config dictionary.
        Only applies to attributes not starting with underscore to avoid conflicts with internal attributes.

        Args:
            name: Config key name (top-level)

        Returns:
            Config value

        Raises:
            AttributeError: Raised when config item does not exist
        """
        # Avoid recursive access to internal attributes
        if name.startswith('_'):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        if self._config is None:
            self.load_config()
        if name in self._config:
            return self._config[name]
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def reload(self, config_path: str = None):
        """Reload config"""
        self._config = None
        self.load_config(config_path)


class ScenarioConfigManager:
    """Scenario config manager - Singleton pattern

    Responsible for loading and managing scenario config, supports deep merge of scenario config and global config.
    Scenario config files are stored in config/scenarios/ directory, one YAML file per scenario.
    """

    _instance = None
    _scenarios: Dict[str, Dict] = {}
    _base_config: Optional[Dict] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize scenario config manager"""
        if not self._scenarios:
            self._load_scenarios()
            self._load_base_config()

    def _load_base_config(self):
        """Load global base config"""
        base_config = ConfigManager()
        self._base_config = base_config.get_all()

    def _load_scenarios(self):
        """Load all scenario config files"""
        scenarios_dir = Path(__file__).parent / "scenarios"
        if not scenarios_dir.exists():
            scenarios_dir.mkdir(parents=True, exist_ok=True)
            return

        for yaml_file in sorted(scenarios_dir.glob("*.yaml")):
            try:
                with open(yaml_file, 'r', encoding='utf-8') as f:
                    scenario_data = yaml.safe_load(f)
                    scenario_id = scenario_data.get("scenario_id", yaml_file.stem)
                    self._scenarios[scenario_id] = scenario_data
            except Exception as e:
                print(f" Loading scenario config failed {yaml_file}: {e}")

    def _deep_merge(self, base: Dict, override: Dict) -> Dict:
        """
        Deep merge two dictionaries

        Args:
            base: Base config
            override: Override config (higher priority)

        Returns:
            Merged config dictionary
        """
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def get_scenario(self, scenario_id: str) -> Optional[Dict]:
        """
        Get scenario config (raw config, not including global config)

        Args:
            scenario_id: Scenario ID

        Returns:
            Scenario config dictionary, or None if scenario does not exist
        """
        return self._scenarios.get(scenario_id)

    def get_effective_config(self, scenario_id: str) -> Dict:
        """
        Get effective config for scenario (deep merge of global config and scenario config)

        Args:
            scenario_id: Scenario ID

        Returns:
            Merged effective config dictionary
        """
        scenario_config = self.get_scenario(scenario_id)
        if scenario_config is None:
            return self._base_config.copy() if self._base_config else {}

        if self._base_config is None:
            self._load_base_config()

        merged = self._deep_merge(self._base_config, scenario_config)
        merged['scenario'] = {
            'scenario_id': scenario_id,
            'scenario_name': scenario_config.get('scenario_name', scenario_id),
            'description': scenario_config.get('description', '')
        }
        return merged

    def list_scenarios(self) -> List[Dict]:
        """
        List all available scenarios

        Returns:
            Scenario list, each element contains scenario_id, scenario_name, description
        """
        scenarios = []
        for scenario_id, config_data in self._scenarios.items():
            scenarios.append({
                'scenario_id': scenario_id,
                'scenario_name': config_data.get('scenario_name', scenario_id),
                'description': config_data.get('description', '')
            })
        return sorted(scenarios, key=lambda x: x['scenario_id'])

    def reload(self):
        """Reload all scenario configs"""
        self._scenarios = {}
        self._base_config = None
        self._load_scenarios()
        self._load_base_config()


# Global config instance
config = ConfigManager()

# Global scenario config instance
scenario_config = ScenarioConfigManager()