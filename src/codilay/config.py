"""Configuration loader for CodiLay."""

import json
import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class CodiLayConfig:
    target_path: str = "."
    ignore_patterns: List[str] = field(default_factory=list)
    notes: str = ""
    instructions: str = ""
    entry_hint: Optional[str] = None
    llm_model: Optional[str] = None  # None = use provider default
    llm_provider: str = "anthropic"
    llm_base_url: Optional[str] = None  # Override provider's default base URL
    max_tokens_per_call: int = 4096
    max_file_size: int = 50000
    skip_binary: bool = True
    skip_generated: List[str] = field(
        default_factory=lambda: [
            "package-lock.json",
            "yarn.lock",
            "poetry.lock",
            "Pipfile.lock",
            "composer.lock",
            "Gemfile.lock",
            "Cargo.lock",
            "*.min.js",
            "*.min.css",
            "*.map",
            "*.pyc",
            "*.pyo",
            "__pycache__",
            ".DS_Store",
            "Thumbs.db",
        ]
    )

    # Triage options
    triage_mode: str = "smart"
    include_tests: bool = False
    force_include: List[str] = field(default_factory=list)
    force_skip: List[str] = field(default_factory=list)

    # Chunking options
    chunk_token_threshold: int = 6000  # Files above this get chunked
    max_chunk_tokens: int = 4000  # Max tokens per detail chunk
    chunk_overlap_ratio: float = 0.10  # 10% overlap between chunks

    # Parallel processing options
    parallel: bool = True  # Enable tier-based parallel processing
    max_workers: int = 4  # Max concurrent workers per tier

    @classmethod
    def load(cls, target_path: str, config_path: Optional[str] = None) -> "CodiLayConfig":
        config = cls(target_path=target_path)

        if config_path is None:
            config_path = os.path.join(target_path, "codilay.config.json")

        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            config.ignore_patterns = data.get("ignore", [])
            config.notes = data.get("notes", "")
            config.instructions = data.get("instructions", "")
            config.entry_hint = data.get("entryHint", None)

            llm = data.get("llm", {})
            if "model" in llm:
                config.llm_model = llm["model"]
            if "maxTokensPerCall" in llm:
                config.max_tokens_per_call = llm["maxTokensPerCall"]
            if "provider" in llm:
                config.llm_provider = llm["provider"]
            if "baseUrl" in llm:
                config.llm_base_url = llm["baseUrl"]
            if "skipGenerated" in data:
                config.skip_generated = data["skipGenerated"]

            # Triage
            triage = data.get("triage", {})
            if isinstance(triage, str):
                config.triage_mode = triage
            elif isinstance(triage, dict):
                config.triage_mode = triage.get("mode", "smart")
                config.include_tests = triage.get("includeTests", False)
                config.force_include = triage.get("forceInclude", [])
                config.force_skip = triage.get("forceSkip", [])

            # Chunking
            chunking = data.get("chunking", {})
            if isinstance(chunking, dict):
                config.chunk_token_threshold = chunking.get("tokenThreshold", 6000)
                config.max_chunk_tokens = chunking.get("maxChunkTokens", 4000)
                config.chunk_overlap_ratio = chunking.get("overlapRatio", 0.10)

            # Parallel processing
            parallel = data.get("parallel", {})
            if isinstance(parallel, bool):
                config.parallel = parallel
            elif isinstance(parallel, dict):
                config.parallel = parallel.get("enabled", True)
                config.max_workers = parallel.get("maxWorkers", 4)

        return config
