"""Tests for codilay.settings — persistent global configuration."""

import json
import os
import tempfile
from unittest.mock import patch

from codilay.settings import DEFAULT_MODELS, PROVIDER_META, PROVIDER_MODELS, Settings

# ── Helpers ───────────────────────────────────────────────────────────────────


def _temp_settings_file(tmp_path):
    """Return (settings_dir, settings_file) paths inside a temp directory."""
    settings_dir = os.path.join(tmp_path, ".codilay")
    settings_file = os.path.join(settings_dir, "settings.json")
    return settings_dir, settings_file


# ── Default values ────────────────────────────────────────────────────────────


def test_settings_defaults():
    s = Settings()
    assert s.default_provider == "anthropic"
    assert s.default_model is None
    assert s.verbose is False
    assert s.triage_mode == "smart"
    assert s.include_tests is False
    assert s.max_tokens_per_call == 4096
    assert s.parallel is True
    assert s.max_workers == 4


def test_settings_new_field_defaults():
    s = Settings()
    # Doc output location
    assert s.doc_output_location == "codilay"
    # Documentation style
    assert s.response_style == "balanced"
    assert s.detail_level == "standard"
    assert s.include_examples is True
    # Watch mode
    assert s.watch_debounce_seconds == 2.0
    assert s.watch_auto_open_ui is False
    assert s.watch_extensions == []
    # Export
    assert s.export_default_format == "compact"
    assert s.export_max_tokens == 100000
    # Web UI
    assert s.web_ui_port == 8765
    assert s.web_ui_auto_open_browser is True
    # Large file
    assert s.large_file_threshold is None


# ── Save / load round-trip ────────────────────────────────────────────────────


def test_save_load_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        settings_dir = os.path.join(tmp, ".codilay")
        settings_file = os.path.join(settings_dir, "settings.json")

        with (
            patch("codilay.settings.SETTINGS_DIR", __import__("pathlib").Path(settings_dir)),
            patch("codilay.settings.SETTINGS_FILE", __import__("pathlib").Path(settings_file)),
        ):
            s = Settings()
            s.default_provider = "openai"
            s.verbose = True
            s.api_keys = {"openai": "sk-test-key"}
            s.save()

            loaded = Settings.load()

        assert loaded.default_provider == "openai"
        assert loaded.verbose is True
        assert loaded.api_keys.get("openai") == "sk-test-key"


def test_save_load_new_fields_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        settings_dir = os.path.join(tmp, ".codilay")
        settings_file = os.path.join(settings_dir, "settings.json")

        with (
            patch("codilay.settings.SETTINGS_DIR", __import__("pathlib").Path(settings_dir)),
            patch("codilay.settings.SETTINGS_FILE", __import__("pathlib").Path(settings_file)),
        ):
            s = Settings()
            s.doc_output_location = "docs"
            s.response_style = "code-first"
            s.detail_level = "detailed"
            s.include_examples = False
            s.watch_debounce_seconds = 5.0
            s.watch_auto_open_ui = True
            s.watch_extensions = [".rb", ".erb"]
            s.export_default_format = "structured"
            s.export_max_tokens = 50000
            s.web_ui_port = 9000
            s.web_ui_auto_open_browser = False
            s.large_file_threshold = 8000
            s.save()

            loaded = Settings.load()

        assert loaded.doc_output_location == "docs"
        assert loaded.response_style == "code-first"
        assert loaded.detail_level == "detailed"
        assert loaded.include_examples is False
        assert loaded.watch_debounce_seconds == 5.0
        assert loaded.watch_auto_open_ui is True
        assert loaded.watch_extensions == [".rb", ".erb"]
        assert loaded.export_default_format == "structured"
        assert loaded.export_max_tokens == 50000
        assert loaded.web_ui_port == 9000
        assert loaded.web_ui_auto_open_browser is False
        assert loaded.large_file_threshold == 8000


def test_load_returns_defaults_when_file_missing():
    with tempfile.TemporaryDirectory() as tmp:
        settings_file = os.path.join(tmp, ".codilay", "settings.json")

        with patch("codilay.settings.SETTINGS_FILE", __import__("pathlib").Path(settings_file)):
            loaded = Settings.load()

    assert loaded.default_provider == "anthropic"
    assert loaded.verbose is False


def test_load_handles_corrupt_json_gracefully():
    with tempfile.TemporaryDirectory() as tmp:
        settings_dir = os.path.join(tmp, ".codilay")
        settings_file = os.path.join(settings_dir, "settings.json")
        os.makedirs(settings_dir)
        with open(settings_file, "w") as f:
            f.write("NOT VALID JSON {{{")

        with patch("codilay.settings.SETTINGS_FILE", __import__("pathlib").Path(settings_file)):
            loaded = Settings.load()

    assert loaded.default_provider == "anthropic"  # Falls back to defaults


def test_load_ignores_unknown_keys():
    """Extra keys in settings.json (from a future version) are silently ignored."""
    with tempfile.TemporaryDirectory() as tmp:
        settings_dir = os.path.join(tmp, ".codilay")
        settings_file = os.path.join(settings_dir, "settings.json")
        os.makedirs(settings_dir)
        with open(settings_file, "w") as f:
            json.dump({"default_provider": "gemini", "future_unknown_key": 42}, f)

        with patch("codilay.settings.SETTINGS_FILE", __import__("pathlib").Path(settings_file)):
            loaded = Settings.load()

    assert loaded.default_provider == "gemini"


# ── inject_env_vars ───────────────────────────────────────────────────────────


def test_inject_env_vars_sets_env():
    s = Settings()
    s.api_keys = {"anthropic": "sk-ant-test", "openai": "sk-oai-test"}

    with patch.dict(os.environ, {}, clear=False):
        s.inject_env_vars()
        assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-test"
        assert os.environ.get("OPENAI_API_KEY") == "sk-oai-test"


def test_inject_env_vars_custom_base_url():
    s = Settings()
    s.custom_base_url = "http://localhost:11434"

    with patch.dict(os.environ, {}, clear=False):
        s.inject_env_vars()
        assert os.environ.get("CUSTOM_LLM_BASE_URL") == "http://localhost:11434"


# ── get_effective_model ───────────────────────────────────────────────────────


def test_get_effective_model_provider_default():
    s = Settings()
    s.default_provider = "openai"
    s.default_model = None
    assert s.get_effective_model() == DEFAULT_MODELS["openai"]


def test_get_effective_model_override():
    s = Settings()
    s.default_model = "gpt-4-turbo"
    assert s.get_effective_model() == "gpt-4-turbo"


def test_get_effective_model_explicit_provider():
    s = Settings()
    s.default_model = None
    assert s.get_effective_model("gemini") == DEFAULT_MODELS["gemini"]


# ── has_provider_configured ───────────────────────────────────────────────────


def test_has_provider_configured_true_when_key_stored():
    s = Settings()
    s.api_keys = {"anthropic": "sk-ant-test"}
    assert s.has_provider_configured("anthropic") is True


def test_has_provider_configured_true_from_env():
    s = Settings()
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env-key"}):
        assert s.has_provider_configured("openai") is True


def test_has_provider_configured_false_no_key():
    s = Settings()
    s.api_keys = {}
    with patch.dict(os.environ, {}, clear=False):
        # Ensure env var absent
        os.environ.pop("ANTHROPIC_API_KEY", None)
        assert s.has_provider_configured("anthropic") is False


def test_has_provider_configured_ollama_no_key_needed():
    """Ollama requires no API key."""
    s = Settings()
    assert s.has_provider_configured("ollama") is True


# ── mask_key ──────────────────────────────────────────────────────────────────


def test_mask_key_normal():
    masked = Settings.mask_key("sk-ant-abcdefghijklmnop")
    assert masked.startswith("sk-a")
    assert masked.endswith("mnop")
    assert "•" in masked


def test_mask_key_short():
    assert Settings.mask_key("short") == "****"


def test_mask_key_empty():
    assert Settings.mask_key("") == "****"


# ── is_first_run ──────────────────────────────────────────────────────────────


def test_is_first_run_true_when_no_file():
    with tempfile.TemporaryDirectory() as tmp:
        settings_file = os.path.join(tmp, "nonexistent", "settings.json")
        with patch("codilay.settings.SETTINGS_FILE", __import__("pathlib").Path(settings_file)):
            s = Settings()
            assert s.is_first_run() is True


def test_is_first_run_false_when_file_exists():
    with tempfile.TemporaryDirectory() as tmp:
        settings_dir = os.path.join(tmp, ".codilay")
        settings_file = os.path.join(settings_dir, "settings.json")
        os.makedirs(settings_dir)
        with open(settings_file, "w") as f:
            json.dump({}, f)

        with patch("codilay.settings.SETTINGS_FILE", __import__("pathlib").Path(settings_file)):
            s = Settings()
            assert s.is_first_run() is False


# ── PROVIDER_MODELS ───────────────────────────────────────────────────────────


def test_provider_models_covers_all_providers():
    """Every provider in PROVIDER_META should have an entry in PROVIDER_MODELS."""
    for provider in PROVIDER_META:
        assert provider in PROVIDER_MODELS, f"Missing PROVIDER_MODELS entry for '{provider}'"


def test_provider_models_structure():
    """Each non-empty preset list must contain dicts with id, desc, and reasoning keys."""
    for provider, models in PROVIDER_MODELS.items():
        for m in models:
            assert "id" in m, f"{provider}: model entry missing 'id'"
            assert "desc" in m, f"{provider}: model entry missing 'desc'"
            assert "reasoning" in m, f"{provider}: model entry missing 'reasoning'"
            assert isinstance(m["reasoning"], bool), f"{provider}: 'reasoning' must be bool"
            assert isinstance(m["id"], str) and m["id"], f"{provider}: 'id' must be non-empty string"


def test_provider_models_default_in_list():
    """The DEFAULT_MODELS entry for each provider should appear in its preset list (where presets exist)."""
    for provider, models in PROVIDER_MODELS.items():
        if not models:
            continue  # ollama/custom have empty lists — fine
        default = DEFAULT_MODELS.get(provider)
        if default is None:
            continue
        ids = [m["id"] for m in models]
        assert default in ids, f"{provider}: default model '{default}' not found in PROVIDER_MODELS presets"


def test_reasoning_models_marked():
    """At least one model per major provider should be marked as supporting reasoning."""
    reasoning_providers = {"anthropic", "openai", "gemini", "deepseek", "xai"}
    for provider in reasoning_providers:
        models = PROVIDER_MODELS.get(provider, [])
        has_reasoning = any(m["reasoning"] for m in models)
        assert has_reasoning, f"{provider}: expected at least one reasoning-capable model"


# ── Reasoning settings defaults ───────────────────────────────────────────────


def test_reasoning_settings_defaults():
    s = Settings()
    assert s.reasoning_enabled is False
    assert s.reasoning_budget_tokens == 10000
    assert s.reasoning_effort == "medium"
    assert s.reasoning_apply_to == ["processing", "planning"]


def test_reasoning_settings_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        settings_dir = os.path.join(tmp, ".codilay")
        settings_file = os.path.join(settings_dir, "settings.json")

        with (
            patch("codilay.settings.SETTINGS_DIR", __import__("pathlib").Path(settings_dir)),
            patch("codilay.settings.SETTINGS_FILE", __import__("pathlib").Path(settings_file)),
        ):
            s = Settings()
            s.reasoning_enabled = True
            s.reasoning_budget_tokens = 20000
            s.reasoning_effort = "high"
            s.reasoning_apply_to = ["processing"]
            s.save()

            loaded = Settings.load()

        assert loaded.reasoning_enabled is True
        assert loaded.reasoning_budget_tokens == 20000
        assert loaded.reasoning_effort == "high"
        assert loaded.reasoning_apply_to == ["processing"]


# ── Annotation settings defaults ──────────────────────────────────────────────


def test_annotation_settings_defaults():
    s = Settings()
    assert s.annotate_require_git_clean is True
    assert s.annotate_require_dry_run_first is True
    assert s.annotate_auto_commit is False
    assert s.annotate_commit_message == "docs: add CodiLay annotations"
    assert s.annotate_level == "docstrings"
    assert s.annotate_skip_existing is True
    assert s.annotate_skip_tests is True
    assert s.annotate_skip_short_functions is True
    assert s.annotate_short_function_threshold == 5
    assert s.annotate_confidence_threshold == 0.7
    assert s.annotate_review_mode is False
    assert s.annotate_syntax_validation is True


def test_annotation_settings_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        settings_dir = os.path.join(tmp, ".codilay")
        settings_file = os.path.join(settings_dir, "settings.json")

        with (
            patch("codilay.settings.SETTINGS_DIR", __import__("pathlib").Path(settings_dir)),
            patch("codilay.settings.SETTINGS_FILE", __import__("pathlib").Path(settings_file)),
        ):
            s = Settings()
            s.annotate_require_git_clean = False
            s.annotate_auto_commit = True
            s.annotate_level = "full"
            s.annotate_confidence_threshold = 0.5
            s.annotate_short_function_threshold = 10
            s.save()

            loaded = Settings.load()

        assert loaded.annotate_require_git_clean is False
        assert loaded.annotate_auto_commit is True
        assert loaded.annotate_level == "full"
        assert loaded.annotate_confidence_threshold == 0.5
        assert loaded.annotate_short_function_threshold == 10
