"""
CodiLay Settings — persistent, cross-session configuration.

Settings are stored in ~/.codilay/settings.json and survive terminal restarts,
new sessions, etc.  They hold:
    • API keys for each provider
    • The user's preferred (default) provider & model
    • Global preferences (verbose, triage mode, …)
"""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Optional

# ── Location ──────────────────────────────────────────────────────────────────

SETTINGS_DIR = Path.home() / ".codilay"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"

# ── Provider metadata (label + env var name, for display only) ────────────────

PROVIDER_META = {
    "anthropic": {"label": "Anthropic (Claude)", "env_key": "ANTHROPIC_API_KEY"},
    "openai": {"label": "OpenAI", "env_key": "OPENAI_API_KEY"},
    "gemini": {"label": "Google Gemini", "env_key": "GEMINI_API_KEY"},
    "deepseek": {"label": "DeepSeek", "env_key": "DEEPSEEK_API_KEY"},
    "mistral": {"label": "Mistral AI", "env_key": "MISTRAL_API_KEY"},
    "groq": {"label": "Groq", "env_key": "GROQ_API_KEY"},
    "xai": {"label": "xAI (Grok)", "env_key": "XAI_API_KEY"},
    "llama": {"label": "Llama Cloud (Meta)", "env_key": "LLAMA_API_KEY"},
    "ollama": {"label": "Ollama (local)", "env_key": None},
    "custom": {"label": "Custom endpoint", "env_key": "CUSTOM_LLM_API_KEY"},
}

# Default models per provider
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "gemini": "gemini-2.0-flash",
    "deepseek": "deepseek-chat",
    "mistral": "mistral-large-latest",
    "groq": "llama-3.3-70b-versatile",
    "xai": "grok-2-latest",
    "llama": "Llama-4-Maverick-17B-128E",
    "ollama": "llama3.2",
    "custom": None,
}


# ── Data class ────────────────────────────────────────────────────────────────


@dataclass
class Settings:
    """Global, persistent settings for CodiLay."""

    # API keys — keyed by provider name
    api_keys: Dict[str, str] = field(default_factory=dict)

    # Preferred defaults
    default_provider: str = "anthropic"
    default_model: Optional[str] = None  # None → provider default
    custom_base_url: Optional[str] = None  # for 'custom' provider

    # Behaviour
    verbose: bool = False
    triage_mode: str = "smart"  # smart | fast | none
    include_tests: bool = False
    max_tokens_per_call: int = 4096
    parallel: bool = True  # tier-based parallel processing
    max_workers: int = 4  # max concurrent workers per tier

    # ── persistence ───────────────────────────────────────────────

    @classmethod
    def load(cls) -> "Settings":
        """Load settings from disk, or return fresh defaults."""
        if not SETTINGS_FILE.exists():
            return cls()
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (json.JSONDecodeError, TypeError):
            return cls()

    def save(self) -> None:
        """Persist settings to disk."""
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)

    # ── helpers ───────────────────────────────────────────────────

    def get_api_key(self, provider: str) -> Optional[str]:
        """Return stored key, falling back to the environment variable."""
        key = self.api_keys.get(provider)
        if key:
            return key
        env_key = PROVIDER_META.get(provider, {}).get("env_key")
        if env_key:
            return os.environ.get(env_key)
        return None

    def set_api_key(self, provider: str, key: str) -> None:
        """Store an API key persistently."""
        self.api_keys[provider] = key
        self.save()

    def remove_api_key(self, provider: str) -> None:
        """Remove a stored API key."""
        self.api_keys.pop(provider, None)
        self.save()

    def get_effective_model(self, provider: Optional[str] = None) -> Optional[str]:
        """Return the model in use for the given provider."""
        prov = provider or self.default_provider
        return self.default_model or DEFAULT_MODELS.get(prov)

    def has_provider_configured(self, provider: str) -> bool:
        """Check if a provider has the credentials it needs."""
        meta = PROVIDER_META.get(provider, {})
        if meta.get("env_key") is None:
            # No key required (e.g. Ollama local)
            return True
        return bool(self.get_api_key(provider))

    def inject_env_vars(self) -> None:
        """
        Push stored API keys into environment variables so that
        LLMClient (and the underlying SDKs) can find them without
        the user having to `export` anything.
        """
        for provider, key in self.api_keys.items():
            env_key = PROVIDER_META.get(provider, {}).get("env_key")
            if env_key and key:
                os.environ[env_key] = key
        # Custom base URL
        if self.custom_base_url:
            os.environ["CUSTOM_LLM_BASE_URL"] = self.custom_base_url

    @staticmethod
    def mask_key(key: str) -> str:
        """Return a masked version of an API key for display."""
        if not key or len(key) < 10:
            return "****"
        return key[:4] + "•" * (len(key) - 8) + key[-4:]

    def is_first_run(self) -> bool:
        """True when no settings file exists yet."""
        return not SETTINGS_FILE.exists()
