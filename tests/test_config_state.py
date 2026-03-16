import json
import os
import tempfile

from codilay.config import CodiLayConfig
from codilay.settings import Settings
from codilay.state import AgentState


def test_config_load_default():
    # No config file
    with tempfile.TemporaryDirectory() as tmpdir:
        config = CodiLayConfig.load(tmpdir)
        assert config.target_path == tmpdir
        assert config.llm_provider == "anthropic"
        assert config.llm_model is None


def test_config_load_from_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_data = {
            "llm": {"provider": "openai", "model": "gpt-4o"},
            "ignore": ["data/", "*.log"],
            "triage": "fast",
        }
        config_path = os.path.join(tmpdir, "codilay.config.json")
        with open(config_path, "w") as f:
            json.dump(config_data, f)

        config = CodiLayConfig.load(tmpdir)
        assert config.llm_provider == "openai"
        assert config.llm_model == "gpt-4o"
        assert "data/" in config.ignore_patterns
        assert config.triage_mode == "fast"


def test_agent_state_save_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = os.path.join(tmpdir, "state.json")
        state = AgentState(run_id="test-run")
        state.queue = ["a.py", "b.py"]
        state.processed = ["README.md"]
        state.open_wires = [{"id": "w1", "from": "a.py", "to": "b.py"}]

        state.save(state_path)
        assert os.path.exists(state_path)

        loaded = AgentState.load(state_path)
        assert loaded.run_id == "test-run"
        assert loaded.queue == ["a.py", "b.py"]
        assert loaded.processed == ["README.md"]
        assert len(loaded.open_wires) == 1
        assert loaded.open_wires[0]["id"] == "w1"


# ── Parallel config ─────────────────────────────────────────────────────────


def test_config_parallel_defaults():
    """Config should have parallel=True and max_workers=4 by default."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = CodiLayConfig.load(tmpdir)
        assert config.parallel is True
        assert config.max_workers == 4


def test_config_parallel_bool_from_file():
    """A simple boolean 'parallel' key should set parallel on/off."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_data = {"parallel": False}
        config_path = os.path.join(tmpdir, "codilay.config.json")
        with open(config_path, "w") as f:
            json.dump(config_data, f)

        config = CodiLayConfig.load(tmpdir)
        assert config.parallel is False
        assert config.max_workers == 4  # unchanged


def test_config_parallel_dict_from_file():
    """A dict 'parallel' key should set both enabled and maxWorkers."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_data = {
            "parallel": {
                "enabled": True,
                "maxWorkers": 8,
            }
        }
        config_path = os.path.join(tmpdir, "codilay.config.json")
        with open(config_path, "w") as f:
            json.dump(config_data, f)

        config = CodiLayConfig.load(tmpdir)
        assert config.parallel is True
        assert config.max_workers == 8


def test_config_parallel_dict_disabled():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_data = {
            "parallel": {
                "enabled": False,
                "maxWorkers": 2,
            }
        }
        config_path = os.path.join(tmpdir, "codilay.config.json")
        with open(config_path, "w") as f:
            json.dump(config_data, f)

        config = CodiLayConfig.load(tmpdir)
        assert config.parallel is False
        assert config.max_workers == 2


def test_config_parallel_dict_defaults():
    """Dict with no keys should use defaults."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_data = {"parallel": {}}
        config_path = os.path.join(tmpdir, "codilay.config.json")
        with open(config_path, "w") as f:
            json.dump(config_data, f)

        config = CodiLayConfig.load(tmpdir)
        assert config.parallel is True
        assert config.max_workers == 4


# ── Settings parallel fields ────────────────────────────────────────────────


def test_settings_parallel_defaults():
    """Settings should default to parallel=True, max_workers=4."""
    settings = Settings()
    assert settings.parallel is True
    assert settings.max_workers == 4


def test_settings_parallel_save_load():
    """Parallel settings should persist through save/load."""
    with tempfile.TemporaryDirectory() as tmpdir:
        settings_file = os.path.join(tmpdir, "settings.json")

        settings = Settings()
        settings.parallel = False
        settings.max_workers = 8

        # Manually save to a specific path (not ~/.codilay)
        data = {
            "parallel": settings.parallel,
            "max_workers": settings.max_workers,
            "api_keys": {},
            "default_provider": "anthropic",
        }
        with open(settings_file, "w") as f:
            json.dump(data, f)

        # Load it back via constructor
        with open(settings_file, "r") as f:
            loaded_data = json.load(f)

        loaded = Settings(**{k: v for k, v in loaded_data.items() if k in Settings.__dataclass_fields__})
        assert loaded.parallel is False
        assert loaded.max_workers == 8


def test_settings_parallel_toggle():
    """Toggling parallel should flip the value."""
    settings = Settings()
    assert settings.parallel is True

    settings.parallel = not settings.parallel
    assert settings.parallel is False

    settings.parallel = not settings.parallel
    assert settings.parallel is True


def test_settings_max_workers_bounds():
    """max_workers should accept reasonable values."""
    settings = Settings()
    settings.max_workers = 1
    assert settings.max_workers == 1

    settings.max_workers = 16
    assert settings.max_workers == 16
