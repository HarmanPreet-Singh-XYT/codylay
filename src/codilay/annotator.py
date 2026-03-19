"""
CodiLay Annotator — writes documentation comments back into source files.

Supports:
  codilay annotate ./my-project                   Full codebase
  codilay annotate ./my-project --scope src/auth/ Specific folder
  codilay annotate ./my-project --dry-run         Preview only
  codilay annotate ./my-project --level full      Docstrings + inline
"""

from __future__ import annotations

import ast
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# ── Language detection ────────────────────────────────────────────────────────

EXTENSION_TO_LANGUAGE: Dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".dart": "dart",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".ex": "elixir",
    ".exs": "elixir",
    ".sh": "bash",
    ".bash": "bash",
}

# Comment style descriptors per language (used in the prompt)
COMMENT_STYLES: Dict[str, str] = {
    "python": 'triple-quoted docstrings ("""...""") for functions/classes; # for inline',
    "javascript": "JSDoc (/** ... */) for functions; // for inline",
    "typescript": "JSDoc (/** ... */) for functions; // for inline",
    "go": "// GoDoc comments above functions; // for inline",
    "rust": "/// triple-slash doc comments above items; // for inline",
    "java": "Javadoc (/** ... */) for methods/classes; // for inline",
    "kotlin": "/** KDoc */ for functions/classes; // for inline",
    "swift": "/// triple-slash doc comments; // for inline",
    "dart": "/// triple-slash DartDoc comments; // for inline",
    "ruby": "# for all comments",
    "php": "PHPDoc (/** ... */) for functions/classes; // for inline",
    "c": "/** ... */ for function headers; // for inline",
    "cpp": "/** ... */ for function headers; // for inline",
    "csharp": "/// XML doc comments for public members; // for inline",
    "elixir": "@doc string for functions; # for inline",
    "bash": "# for all comments",
}

# File extensions that are never annotated (non-code)
NEVER_ANNOTATE_EXTENSIONS = {
    ".md",
    ".txt",
    ".rst",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".env",
    ".lock",
    ".log",
    ".csv",
    ".xml",
    ".html",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".map",
    ".pyc",
    ".pyo",
    ".class",
    ".o",
    ".so",
    ".dll",
    ".exe",
}

# Patterns suggesting generated / migration / seed files
GENERATED_FILE_PATTERNS = [
    r"\.g\.dart$",
    r"\.generated\.",
    r"_generated\.",
    r"\.pb\.go$",
    r"(?:^|/)migrations?/",
    r"(?:^|/)migration/",
    r"_migration\.",
    r"(?:^|/)seeds?/",
    r"_seed\.",
    r"\.min\.(js|css)$",
    r"(?:^|/)generated/",
    r"(?:^|/)__generated__/",
]

TEST_FILE_PATTERNS = [
    r"_test\.(py|go|rs|js|ts)$",
    r"\.test\.(js|ts|tsx|jsx)$",
    r"\.spec\.(js|ts|tsx|jsx)$",
    r"/tests?/",
    r"/spec/",
    r"test_.*\.py$",
]


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class Annotation:
    type: str  # "docstring" or "inline"
    target: str  # function/class name (for docstrings)
    line: int  # 1-based line number to insert at
    comment: str  # comment text (without delimiters)
    confidence: float  # 0.0–1.0


@dataclass
class FileAnnotationResult:
    file_path: str
    language: str
    annotations: List[Annotation] = field(default_factory=list)
    skip_reason: Optional[str] = None
    error: Optional[str] = None
    low_confidence: List[Annotation] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return bool(self.annotations) and not self.skip_reason and not self.error


@dataclass
class AnnotationRun:
    run_id: str
    target: str
    files_annotated: List[str] = field(default_factory=list)
    files_skipped: List[str] = field(default_factory=list)
    backup_dir: Optional[str] = None


# ── Syntax validation ─────────────────────────────────────────────────────────


def _validate_python_syntax(source: str) -> Optional[str]:
    """Return error message if source has a syntax error, else None."""
    try:
        ast.parse(source)
        return None
    except SyntaxError as e:
        return f"SyntaxError on line {e.lineno}: {e.msg}"


def validate_syntax(file_path: str, source: str) -> Optional[str]:
    """Validate the annotated source. Returns error message or None."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".py":
        return _validate_python_syntax(source)
    # For other languages we skip validation (no bundled tools)
    return None


# ── Comment insertion ─────────────────────────────────────────────────────────


def _python_insert_docstring(lines: List[str], line_idx: int, comment: str, target: str) -> List[str]:
    """Insert a Python docstring after the def/class line at line_idx (0-based)."""
    def_line = lines[line_idx]
    # Detect indentation of the def line
    indent = len(def_line) - len(def_line.lstrip())
    body_indent = " " * (indent + 4)

    # Find the colon + body start
    # Insert docstring as first statement in the body
    insert_after = line_idx + 1
    # Skip any decorator lines or blank lines between def and body
    while insert_after < len(lines) and lines[insert_after].strip() == "":
        insert_after += 1

    # Check if a docstring already exists at this position
    if insert_after < len(lines):
        stripped = lines[insert_after].strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            return lines  # already has a docstring — skip

    # Format the docstring
    doc_lines_raw = comment.strip().splitlines()
    if len(doc_lines_raw) == 1:
        docstring_lines = [f'{body_indent}"""{doc_lines_raw[0]}"""\n']
    else:
        docstring_lines = [f'{body_indent}"""\n']
        for dl in doc_lines_raw:
            docstring_lines.append(f"{body_indent}{dl}\n" if dl.strip() else f"\n")
        docstring_lines.append(f'{body_indent}"""\n')

    return lines[:insert_after] + docstring_lines + lines[insert_after:]


def _python_insert_inline(lines: List[str], line_idx: int, comment: str) -> List[str]:
    """Append an inline comment to the line at line_idx (0-based)."""
    line = lines[line_idx]
    # Don't add if comment already exists on this line
    if "#" in line:
        return lines
    stripped_line = line.rstrip("\n")
    lines[line_idx] = f"{stripped_line}  # {comment}\n"
    return lines


def _generic_insert_block_comment(lines: List[str], line_idx: int, comment: str, style: str) -> List[str]:
    """Insert a block comment above line_idx using the given style."""
    def_line = lines[line_idx]
    indent = " " * (len(def_line) - len(def_line.lstrip()))

    comment_lines_raw = comment.strip().splitlines()

    if style in ("javascript", "typescript", "java", "kotlin", "php", "c", "cpp", "csharp"):
        block = [f"{indent}/**\n"]
        for cl in comment_lines_raw:
            block.append(f"{indent} * {cl}\n" if cl.strip() else f"{indent} *\n")
        block.append(f"{indent} */\n")
    elif style in ("go", "rust", "swift", "dart"):
        block = []
        for cl in comment_lines_raw:
            block.append(f"{indent}// {cl}\n" if cl.strip() else f"{indent}//\n")
    elif style in ("ruby", "bash", "elixir"):
        block = []
        for cl in comment_lines_raw:
            block.append(f"{indent}# {cl}\n" if cl.strip() else f"{indent}#\n")
    else:
        # Fallback: use // style
        block = [f"{indent}// {cl}\n" for cl in comment_lines_raw]

    return lines[:line_idx] + block + lines[line_idx:]


def _generic_insert_inline(lines: List[str], line_idx: int, comment: str, style: str) -> List[str]:
    """Append an inline comment to a line."""
    line = lines[line_idx]
    stripped_line = line.rstrip("\n")

    if style in ("javascript", "typescript", "go", "rust", "java", "kotlin", "swift", "dart", "c", "cpp", "csharp"):
        prefix = "//"
    elif style in ("python", "ruby", "bash", "elixir"):
        prefix = "#"
    else:
        prefix = "//"

    # Don't double-annotate
    if prefix in stripped_line:
        return lines

    lines[line_idx] = f"{stripped_line}  {prefix} {comment}\n"
    return lines


def apply_annotations(
    file_path: str,
    original_content: str,
    annotations: List[Annotation],
    language: str,
    confidence_threshold: float = 0.7,
) -> Tuple[str, List[Annotation]]:
    """
    Apply annotations to file content. Returns (new_content, low_confidence_list).
    Annotations below confidence_threshold are collected but not applied.
    """
    lines = original_content.splitlines(keepends=True)
    if not lines:
        return original_content, []

    # Ensure all lines end with newline
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    low_confidence: List[Annotation] = []

    # Sort annotations by line descending so insertions don't shift later line numbers
    sorted_annotations = sorted(annotations, key=lambda a: a.line, reverse=True)

    for ann in sorted_annotations:
        if ann.confidence < confidence_threshold:
            low_confidence.append(ann)
            continue

        # Convert 1-based to 0-based
        line_idx = max(0, ann.line - 1)
        if line_idx >= len(lines):
            continue

        if language == "python":
            if ann.type == "docstring":
                lines = _python_insert_docstring(lines, line_idx, ann.comment, ann.target)
            else:
                lines = _python_insert_inline(lines, line_idx, ann.comment)
        else:
            style = language
            if ann.type == "docstring":
                lines = _generic_insert_block_comment(lines, line_idx, ann.comment, style)
            else:
                lines = _generic_insert_inline(lines, line_idx, ann.comment, style)

    return "".join(lines), low_confidence


# ── Main annotator class ──────────────────────────────────────────────────────


class Annotator:
    """Core annotation engine — classifies, annotates, validates, and writes."""

    def __init__(self, llm_client, settings, ui, target: str, output_dir: str):
        self.llm = llm_client
        self.settings = settings
        self.ui = ui
        self.target = target
        self.output_dir = output_dir
        self._backup_dir: Optional[str] = None

    # ── Public entry points ────────────────────────────────────────

    def run(
        self,
        files: List[str],
        level: str = "docstrings",
        dry_run: bool = False,
        scope: List[str] = None,
        exclude: List[str] = None,
        wire_data: dict = None,
        section_contents: dict = None,
    ) -> AnnotationRun:
        """
        Annotate the given file list.

        Args:
            files: relative file paths (already scanned)
            level: "docstrings" | "inline" | "full"
            dry_run: if True, preview only — no writes
            scope: restrict to these path prefixes (None = all)
            exclude: exclude these path patterns
            wire_data: closed wires from state (for wire connection blocks)
            section_contents: section docs from state (for context)
        """
        from codilay.prompts import annotation_prompt, annotation_triage_prompt

        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        run = AnnotationRun(run_id=run_id, target=self.target)

        # ── Filter files ──────────────────────────────────────────
        eligible = self._filter_files(files, scope, exclude)

        if not eligible:
            self.ui.warn("No eligible files found for annotation.")
            return run

        self.ui.info(f"Annotation candidates: {len(eligible)} files")

        # ── LLM triage pass ───────────────────────────────────────
        self.ui.phase("Annotation Triage — classifying files")
        lang_hints = {f: self._detect_language(f) for f in eligible}
        triage_result = self._triage_files(eligible, lang_hints)

        to_annotate = [f for f, verdict in triage_result.items() if verdict == "ANNOTATE"]
        self.ui.info(f"Files to annotate: {len(to_annotate)}")

        if not to_annotate:
            self.ui.warn("All files were classified as SKIP or IGNORE by triage.")
            return run

        # ── Setup backup dir (for rollback) ───────────────────────
        if not dry_run:
            self._backup_dir = self._create_backup_dir(run_id)
            run.backup_dir = self._backup_dir

        # ── Annotate each file ────────────────────────────────────
        self.ui.phase(f"Annotating {len(to_annotate)} files  ({'dry run — no writes' if dry_run else 'will write'})")

        for file_path in to_annotate:
            full_path = os.path.join(self.target, file_path)
            if not os.path.exists(full_path):
                self.ui.warn(f"  File not found: {file_path}")
                run.files_skipped.append(file_path)
                continue

            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    original_content = f.read()
            except Exception as e:
                self.ui.warn(f"  Could not read {file_path}: {e}")
                run.files_skipped.append(file_path)
                continue

            language = lang_hints.get(file_path, "unknown")
            if language == "unknown":
                run.files_skipped.append(file_path)
                continue

            # Build wire connections for this file
            wires = self._extract_wires_for_file(file_path, wire_data or [])

            # Get existing doc context
            existing_doc = self._find_doc_context(file_path, section_contents or {})

            # Call LLM to generate annotations
            result = self._annotate_file(file_path, original_content, language, level, wires, existing_doc)

            if result.error:
                self.ui.warn(f"  ✗ {file_path}: {result.error}")
                run.files_skipped.append(file_path)
                continue

            if result.skip_reason:
                self.ui.info(f"  — {file_path}: skipped ({result.skip_reason})")
                run.files_skipped.append(file_path)
                continue

            if not result.has_content:
                self.ui.info(f"  — {file_path}: no annotations generated")
                run.files_skipped.append(file_path)
                continue

            # Apply annotations to produce new content
            new_content, low_conf = apply_annotations(
                file_path,
                original_content,
                result.annotations,
                language,
                confidence_threshold=self.settings.annotate_confidence_threshold,
            )

            if low_conf:
                self.ui.warn(f"  ⚠ {file_path}: {len(low_conf)} low-confidence annotation(s) held back")

            if new_content == original_content:
                self.ui.info(f"  — {file_path}: no changes (all annotations already present)")
                run.files_skipped.append(file_path)
                continue

            # Syntax validation
            if self.settings.annotate_syntax_validation:
                err = validate_syntax(file_path, new_content)
                if err:
                    self.ui.warn(f"  ✗ {file_path}: syntax validation failed — {err}")
                    run.files_skipped.append(file_path)
                    continue

            if dry_run:
                self._show_diff(file_path, original_content, new_content, len(result.annotations))
            else:
                # Backup original
                self._backup_file(full_path, file_path)
                # Write annotated version
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                self.ui.info(f"  ✓ {file_path}  ({len(result.annotations)} annotation(s))")

            run.files_annotated.append(file_path)

        # ── Auto commit ───────────────────────────────────────────
        if not dry_run and run.files_annotated and self.settings.annotate_auto_commit:
            self._git_commit(run.files_annotated)

        if dry_run:
            self.ui.info(
                f"\n[dry run] Would annotate {len(run.files_annotated)} file(s). Pass without --dry-run to apply."
            )
        else:
            self.ui.success(f"Annotated {len(run.files_annotated)} file(s). Skipped {len(run.files_skipped)}.")
            if self._backup_dir:
                self.ui.info(
                    f"Backup saved to: {self._backup_dir}  (use 'codilay annotate --rollback {run_id}' to undo)"
                )

        return run

    def rollback(self, run_id: str) -> bool:
        """Restore files from a prior annotation run backup."""
        backup_base = os.path.join(self.output_dir, "annotation_history")
        backup_run = os.path.join(backup_base, run_id)
        if not os.path.isdir(backup_run):
            self.ui.error(f"No backup found for run {run_id}")
            return False

        restored = 0
        for dirpath, _, filenames in os.walk(backup_run):
            for filename in filenames:
                backup_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(backup_path, backup_run)
                original_path = os.path.join(self.target, rel_path)
                os.makedirs(os.path.dirname(original_path), exist_ok=True)
                shutil.copy2(backup_path, original_path)
                restored += 1

        self.ui.success(f"Restored {restored} file(s) from run {run_id}.")
        return True

    # ── Internal helpers ───────────────────────────────────────────

    def _detect_language(self, file_path: str) -> str:
        ext = os.path.splitext(file_path)[1].lower()
        return EXTENSION_TO_LANGUAGE.get(ext, "unknown")

    def _filter_files(self, files: List[str], scope: Optional[List[str]], exclude: Optional[List[str]]) -> List[str]:
        """Filter to files eligible for annotation."""
        result = []
        for f in files:
            ext = os.path.splitext(f)[1].lower()

            # Skip non-code extensions immediately
            if ext in NEVER_ANNOTATE_EXTENSIONS or ext == "":
                continue

            # Skip unknown languages
            if self._detect_language(f) == "unknown":
                continue

            # Skip generated files
            if any(re.search(p, f) for p in GENERATED_FILE_PATTERNS):
                continue

            # Skip test files if configured
            if self.settings.annotate_skip_tests and any(re.search(p, f) for p in TEST_FILE_PATTERNS):
                continue

            # Apply scope filter
            if scope:
                if not any(f.startswith(s.rstrip("/")) or f == s for s in scope):
                    continue

            # Apply exclude filter
            if exclude:
                if any(re.search(re.escape(e).replace(r"\*", ".*"), f) for e in exclude):
                    continue

            result.append(f)

        return result

    def _triage_files(self, files: List[str], lang_hints: dict) -> Dict[str, str]:
        """LLM triage pass — classify each file as ANNOTATE/SKIP/IGNORE."""
        from codilay.prompts import annotation_triage_prompt

        if not files:
            return {}

        prompt = annotation_triage_prompt(files, lang_hints)
        try:
            result = self.llm.call(
                "You are a code annotation triage assistant. Respond with ONLY valid JSON.",
                prompt,
                json_mode=True,
            )
            classifications = result.get("classifications", {})
            # Default unknown files to ANNOTATE (safe fallback)
            return {f: classifications.get(f, "ANNOTATE") for f in files}
        except Exception as e:
            self.ui.warn(f"Triage LLM call failed ({e}) — defaulting all files to ANNOTATE")
            return {f: "ANNOTATE" for f in files}

    def _annotate_file(
        self,
        file_path: str,
        content: str,
        language: str,
        level: str,
        wire_connections: dict,
        existing_doc: str,
    ) -> FileAnnotationResult:
        """Call LLM to generate annotations for a single file."""
        from codilay.prompts import annotation_prompt

        comment_style = COMMENT_STYLES.get(language, "// inline comments")

        system = (
            f"You are a code annotation assistant for {language}. "
            "You add documentation comments to source files without modifying any logic. "
            "Respond with ONLY valid JSON."
        )

        prompt = annotation_prompt(
            file_path=file_path,
            file_content=content,
            language=language,
            comment_style=comment_style,
            level=level,
            wire_connections=wire_connections,
            existing_doc=existing_doc,
        )

        try:
            result = self.llm.call(system, prompt, json_mode=True)
        except Exception as e:
            return FileAnnotationResult(file_path=file_path, language=language, error=str(e))

        if "error" in result:
            return FileAnnotationResult(file_path=file_path, language=language, error=result["error"])

        skip_reason = result.get("skip_reason")
        raw_annotations = result.get("annotations", [])

        annotations = []
        for a in raw_annotations:
            if not isinstance(a, dict):
                continue
            ann = Annotation(
                type=a.get("type", "inline"),
                target=a.get("target", ""),
                line=int(a.get("line", 1)),
                comment=a.get("comment", ""),
                confidence=float(a.get("confidence", 0.8)),
            )
            if ann.comment:
                annotations.append(ann)

        return FileAnnotationResult(
            file_path=file_path,
            language=language,
            annotations=annotations,
            skip_reason=skip_reason,
        )

    def _extract_wires_for_file(self, file_path: str, closed_wires: list) -> dict:
        """Build wire connection summary for a file from closed wires."""
        called_by = []
        calls = []

        for wire in closed_wires:
            if not isinstance(wire, dict):
                continue
            wire_from = wire.get("from", "")
            wire_to = wire.get("to", "")

            # This file is the target — someone calls it
            if wire_to and (wire_to in file_path or file_path.endswith(wire_to)):
                caller = os.path.basename(wire_from)
                if caller and caller not in called_by:
                    called_by.append(caller)

            # This file is the source — it calls something
            if wire_from and (wire_from in file_path or file_path.endswith(wire_from)):
                callee = os.path.basename(wire_to)
                if callee and callee not in calls:
                    calls.append(callee)

        return {"called_by": called_by[:8], "calls": calls[:8]}

    def _find_doc_context(self, file_path: str, section_contents: dict) -> str:
        """Find relevant documentation section for a file."""
        basename = os.path.basename(file_path).replace(".", "_").replace("/", "_")
        for sec_id, content in section_contents.items():
            if basename.lower() in sec_id.lower() or sec_id.lower() in file_path.lower():
                return content[:1500]
        return ""

    def _show_diff(self, file_path: str, original: str, new: str, annotation_count: int):
        """Show a simplified diff preview (first 20 new lines)."""
        from rich.console import Console
        from rich.syntax import Syntax

        console = Console()
        orig_lines = original.splitlines()
        new_lines = new.splitlines()

        added = []
        for i, line in enumerate(new_lines):
            if i >= len(orig_lines) or line != orig_lines[i]:
                if line.strip().startswith(("#", "//", "/*", "*", '"""', "'''")):
                    added.append(f"+ {line}")

        console.print(f"\n  [bold cyan]{file_path}[/bold cyan]  ({annotation_count} annotation(s))")
        preview = "\n".join(added[:20])
        if preview:
            console.print(f"[green]{preview}[/green]")
        if len(added) > 20:
            console.print(f"  [dim]... {len(added) - 20} more annotation lines[/dim]")

    def _create_backup_dir(self, run_id: str) -> str:
        backup_dir = os.path.join(self.output_dir, "annotation_history", run_id)
        os.makedirs(backup_dir, exist_ok=True)
        return backup_dir

    def _backup_file(self, full_path: str, relative_path: str):
        if not self._backup_dir:
            return
        backup_path = os.path.join(self._backup_dir, relative_path)
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
        shutil.copy2(full_path, backup_path)

    def _git_commit(self, files: List[str]):
        """Create a git commit for the annotated files."""
        import subprocess

        try:
            for f in files:
                subprocess.run(["git", "add", os.path.join(self.target, f)], check=True, capture_output=True)
            msg = self.settings.annotate_commit_message
            subprocess.run(["git", "commit", "-m", msg], check=True, capture_output=True, cwd=self.target)
            self.ui.info(f"Git commit created: {msg}")
        except Exception as e:
            self.ui.warn(f"Auto-commit failed: {e}")


def check_git_clean(target: str) -> Tuple[bool, str]:
    """
    Check if the git working tree is clean.
    Returns (is_clean, status_message).
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=target,
        )
        if result.returncode != 0:
            return True, "not a git repo"
        if result.stdout.strip():
            return False, "working tree has uncommitted changes"
        return True, "clean"
    except FileNotFoundError:
        return True, "git not available"
