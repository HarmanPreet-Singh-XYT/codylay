"""
Triage — pre-flight file classification.

Two modes:
  fast  → static patterns only, no LLM, instant, free
  smart → LLM sees the tree (NO file content), decides what matters
          ~500-1000 tokens total. Pays for itself 100x over.
"""

import os
import fnmatch
from typing import Dict, List, Set, Any
from dataclasses import dataclass, field


class FileCategory:
    CORE = "core"
    SKIM = "skim"
    SKIP = "skip"


@dataclass
class TriageResult:
    """Result of the triage phase."""
    core: List[str] = field(default_factory=list)
    skim: List[str] = field(default_factory=list)
    skip: List[str] = field(default_factory=list)
    project_type: str = ""
    reasoning: str = ""
    warnings: List[str] = field(default_factory=list)
    token_estimate_saved: int = 0

    @property
    def total_files(self) -> int:
        return len(self.core) + len(self.skim) + len(self.skip)

    @property
    def files_to_process(self) -> List[str]:
        return self.core + self.skim

    def move_to_core(self, paths: List[str]):
        for p in paths:
            if p in self.skip:
                self.skip.remove(p)
                if p not in self.core:
                    self.core.append(p)
            elif p in self.skim:
                self.skim.remove(p)
                if p not in self.core:
                    self.core.append(p)

    def move_to_skip(self, paths: List[str]):
        for p in paths:
            if p in self.core:
                self.core.remove(p)
                if p not in self.skip:
                    self.skip.append(p)
            elif p in self.skim:
                self.skim.remove(p)
                if p not in self.skip:
                    self.skip.append(p)

    def move_to_skim(self, paths: List[str]):
        for p in paths:
            if p in self.core:
                self.core.remove(p)
                if p not in self.skim:
                    self.skim.append(p)
            elif p in self.skip:
                self.skip.remove(p)
                if p not in self.skim:
                    self.skim.append(p)


# ── Static patterns (used by fast mode AND as safety net) ────────────────────

ALWAYS_SKIP_DIRS: Set[str] = {
    ".git", "node_modules", "__pycache__", ".dart_tool",
    ".gradle", ".idea", ".vscode", ".vs",
    "build", "dist", "out", ".next", "target",
    "coverage", ".nyc_output", ".cache", ".tmp",
    "Pods", ".symlinks",
}

ALWAYS_SKIP_EXTENSIONS: Set[str] = {
    ".min.js", ".min.css", ".map",
    ".pyc", ".pyo", ".class",
    ".g.dart", ".freezed.dart",
    ".lock",
}

ALWAYS_SKIM_FILES: Set[str] = {
    "package.json", "pubspec.yaml", "pyproject.toml",
    "Cargo.toml", "go.mod", "build.gradle", "build.gradle.kts",
    "pom.xml", "Gemfile", "composer.json", "setup.py", "setup.cfg",
    "requirements.txt", "Pipfile",
    "tsconfig.json", "webpack.config.js", "vite.config.js",
    "vite.config.ts", "babel.config.js", "rollup.config.js",
    "jest.config.js", "jest.config.ts",
    "tailwind.config.js", "tailwind.config.ts", "postcss.config.js",
    "next.config.js", "next.config.mjs", "nuxt.config.ts",
    "angular.json", "analysis_options.yaml",
    "docker-compose.yml", "docker-compose.yaml",
    "Dockerfile", "Makefile", "CMakeLists.txt",
    ".env.example", "Procfile",
    ".gitignore", ".dockerignore", ".editorconfig",
    "LICENSE", "LICENCE",
}


class Triage:
    """Pre-flight file classifier."""

    def __init__(self, llm_client=None, config=None):
        self.llm = llm_client
        self.config = config

    # ── Fast mode: patterns only ─────────────────────────────────

    def fast_triage(self, all_files: List[str]) -> TriageResult:
        """Classify using static patterns. No LLM. Instant."""
        result = TriageResult()

        for file_path in all_files:
            basename = os.path.basename(file_path)
            _, ext = os.path.splitext(basename)
            parts = file_path.split("/")

            # Check directory-level skips
            if any(p in ALWAYS_SKIP_DIRS for p in parts[:-1]):
                result.skip.append(file_path)
                continue

            # Check extension skips
            if ext in ALWAYS_SKIP_EXTENSIONS:
                result.skip.append(file_path)
                continue
            # Handle compound extensions like .g.dart
            if any(file_path.endswith(skip_ext) for skip_ext in ALWAYS_SKIP_EXTENSIONS):
                result.skip.append(file_path)
                continue

            # Check skim files
            if basename in ALWAYS_SKIM_FILES:
                result.skim.append(file_path)
                continue

            # Everything else is core
            result.core.append(file_path)

        result.project_type = self._detect_project_type(all_files)
        result.reasoning = (
            f"Pattern-based classification for {result.project_type} project"
        )
        return result

    # ── Smart mode: AI decides ───────────────────────────────────

    def smart_triage(
        self,
        file_tree: str,
        all_files: List[str],
        md_contents: Dict[str, str] = None,
    ) -> TriageResult:
        """
        AI-driven triage. Sends ONLY the file tree to the LLM.
        The AI decides from scratch what to process.
        No file content is sent — just paths. Very cheap call.
        """
        if self.llm is None:
            return self.fast_triage(all_files)

        from codedoc.prompts import triage_prompt, system_prompt

        sys_prompt = system_prompt(self.config)
        user_prompt = triage_prompt(
            file_tree=file_tree,
            all_files=all_files,
            md_contents=md_contents,
            notes=self.config.notes if self.config else "",
            instructions=self.config.instructions if self.config else "",
        )

        response = self.llm.call(sys_prompt, user_prompt)

        if "error" in response:
            # Fallback to fast triage
            return self.fast_triage(all_files)

        result = self._parse_response(response, all_files)

        # Safety net: never let the AI process things we KNOW are garbage
        self._apply_safety_net(result)

        return result

    def _parse_response(
        self, response: Dict[str, Any], all_files: List[str]
    ) -> TriageResult:
        """Parse the LLM triage response into a TriageResult."""
        result = TriageResult()
        all_files_set = set(all_files)

        # The AI returns directory patterns and file paths.
        # We need to expand patterns to actual files.

        core_patterns = response.get("core", [])
        skim_patterns = response.get("skim", [])
        skip_patterns = response.get("skip", [])

        if not isinstance(core_patterns, list):
            core_patterns = []
        if not isinstance(skim_patterns, list):
            skim_patterns = []
        if not isinstance(skip_patterns, list):
            skip_patterns = []

        # Expand patterns to actual file paths
        core_expanded = self._expand_patterns(core_patterns, all_files)
        skim_expanded = self._expand_patterns(skim_patterns, all_files)
        skip_expanded = self._expand_patterns(skip_patterns, all_files)

        # Assign files — skip > skim > core priority for conflicts
        assigned = set()

        for f in skip_expanded:
            if f in all_files_set and f not in assigned:
                result.skip.append(f)
                assigned.add(f)

        for f in skim_expanded:
            if f in all_files_set and f not in assigned:
                result.skim.append(f)
                assigned.add(f)

        for f in core_expanded:
            if f in all_files_set and f not in assigned:
                result.core.append(f)
                assigned.add(f)

        # Any unclassified files default to CORE (safe default)
        for f in all_files:
            if f not in assigned:
                result.core.append(f)

        result.project_type = response.get("project_type", "unknown")
        result.reasoning = response.get("reasoning", "")
        result.warnings = response.get("warnings", [])

        return result

    def _expand_patterns(
        self, patterns: List[str], all_files: List[str]
    ) -> List[str]:
        """Expand a list of patterns/paths/directories to actual file paths."""
        expanded = []

        for pattern in patterns:
            if not isinstance(pattern, str):
                continue

            pattern = pattern.strip()
            if not pattern:
                continue

            # Exact file match
            if pattern in all_files:
                expanded.append(pattern)
                continue

            # Directory match — "lib/" or "lib"
            dir_pattern = pattern.rstrip("/") + "/"
            dir_matches = [f for f in all_files if f.startswith(dir_pattern)]
            if dir_matches:
                expanded.extend(dir_matches)
                continue

            # Also try without trailing slash
            bare_dir_matches = [
                f for f in all_files
                if f.startswith(pattern.rstrip("/") + "/")
            ]
            if bare_dir_matches:
                expanded.extend(bare_dir_matches)
                continue

            # Glob pattern
            if "*" in pattern or "?" in pattern:
                glob_matches = [
                    f for f in all_files if fnmatch.fnmatch(f, pattern)
                ]
                expanded.extend(glob_matches)
                continue

            # Basename match — "AppDelegate.swift" matches "ios/Runner/AppDelegate.swift"
            basename_matches = [
                f for f in all_files
                if os.path.basename(f) == pattern
            ]
            if basename_matches:
                expanded.extend(basename_matches)
                continue

            # Substring match as last resort — "services/" matches "lib/services/"
            sub_matches = [
                f for f in all_files
                if pattern.rstrip("/") in f
            ]
            if sub_matches:
                expanded.extend(sub_matches)

        return expanded

    def _apply_safety_net(self, result: TriageResult):
        """
        Move files we KNOW are garbage back to skip,
        even if the AI said otherwise.
        """
        to_force_skip = []

        for f in result.core + result.skim:
            parts = f.split("/")
            basename = os.path.basename(f)

            # Never process files inside node_modules, .git, etc.
            if any(p in ALWAYS_SKIP_DIRS for p in parts[:-1]):
                to_force_skip.append(f)
                continue

            # Never process compiled/minified files
            if any(f.endswith(ext) for ext in ALWAYS_SKIP_EXTENSIONS):
                to_force_skip.append(f)
                continue

        if to_force_skip:
            result.move_to_skip(to_force_skip)

    def _detect_project_type(self, all_files: List[str]) -> str:
        """Quick project type detection from file names."""
        file_set = set(all_files)

        checks = [
            ("flutter", lambda: "pubspec.yaml" in file_set and any(
                f.endswith(".dart") for f in all_files
            )),
            ("react_native", lambda: "app.json" in file_set and any(
                f.startswith("android/") for f in all_files
            ) and any(f.endswith((".jsx", ".tsx")) for f in all_files)),
            ("nextjs", lambda: any(
                f in file_set for f in [
                    "next.config.js", "next.config.mjs", "next.config.ts"
                ]
            )),
            ("nuxt", lambda: any(
                f in file_set for f in ["nuxt.config.ts", "nuxt.config.js"]
            )),
            ("angular", lambda: "angular.json" in file_set),
            ("vue", lambda: any(f.endswith(".vue") for f in all_files)),
            ("django", lambda: "manage.py" in file_set),
            ("rails", lambda: "Gemfile" in file_set and any(
                f.startswith("config/routes") for f in all_files
            )),
            ("spring", lambda: any(
                f.endswith(".java") for f in all_files
            ) and ("pom.xml" in file_set or "build.gradle" in file_set)),
            ("dotnet", lambda: any(
                f.endswith((".csproj", ".sln")) for f in all_files
            )),
            ("rust", lambda: "Cargo.toml" in file_set),
            ("go", lambda: "go.mod" in file_set),
            ("fastapi", lambda: any(
                f.endswith(".py") for f in all_files
            ) and "requirements.txt" in file_set),
            ("express", lambda: "package.json" in file_set and any(
                f.endswith((".js", ".ts")) for f in all_files
            )),
            ("react", lambda: "package.json" in file_set and any(
                f.endswith((".jsx", ".tsx")) for f in all_files
            )),
        ]

        for name, check in checks:
            try:
                if check():
                    return name
            except Exception:
                continue

        return "generic"

    def estimate_tokens_saved(
        self,
        skip_files: List[str],
        target_path: str,
        avg_tokens_per_file: int = 800,
    ) -> int:
        """Estimate tokens saved by skipping files."""
        return len(skip_files) * (avg_tokens_per_file + 200)