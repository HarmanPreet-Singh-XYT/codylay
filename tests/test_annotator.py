"""Tests for codilay.annotator — code annotation engine."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from codilay.annotator import (
    EXTENSION_TO_LANGUAGE,
    NEVER_ANNOTATE_EXTENSIONS,
    Annotation,
    Annotator,
    FileAnnotationResult,
    apply_annotations,
    check_git_clean,
    validate_syntax,
)
from codilay.settings import Settings

# ── Language detection ────────────────────────────────────────────────────────


def test_extension_to_language_python():
    assert EXTENSION_TO_LANGUAGE[".py"] == "python"


def test_extension_to_language_typescript():
    assert EXTENSION_TO_LANGUAGE[".ts"] == "typescript"
    assert EXTENSION_TO_LANGUAGE[".tsx"] == "typescript"


def test_extension_to_language_go():
    assert EXTENSION_TO_LANGUAGE[".go"] == "go"


def test_never_annotate_extensions_contains_expected():
    for ext in [".md", ".json", ".yaml", ".lock", ".pyc", ".png"]:
        assert ext in NEVER_ANNOTATE_EXTENSIONS, f"{ext} should be in NEVER_ANNOTATE_EXTENSIONS"


# ── validate_syntax ───────────────────────────────────────────────────────────


def test_validate_syntax_valid_python():
    src = "def foo():\n    return 42\n"
    assert validate_syntax("foo.py", src) is None


def test_validate_syntax_invalid_python():
    src = "def foo(\n    return 42\n"
    result = validate_syntax("foo.py", src)
    assert result is not None
    assert "SyntaxError" in result


def test_validate_syntax_non_python_always_passes():
    """For non-Python files, validation is skipped (returns None)."""
    assert validate_syntax("foo.go", "not valid go {{{") is None
    assert validate_syntax("foo.ts", "const x = !!!") is None


# ── apply_annotations — Python docstrings ────────────────────────────────────


def test_apply_python_docstring_basic():
    source = "def greet(name):\n    return f'hello {name}'\n"
    annotations = [
        Annotation(type="docstring", target="greet", line=1, comment="Return a greeting string.", confidence=0.9)
    ]
    result, low_conf = apply_annotations("foo.py", source, annotations, "python")
    assert '"""Return a greeting string."""' in result
    assert low_conf == []


def test_apply_python_docstring_skips_existing():
    source = 'def greet(name):\n    """Already documented."""\n    return name\n'
    annotations = [Annotation(type="docstring", target="greet", line=1, comment="New doc.", confidence=0.9)]
    result, _ = apply_annotations("foo.py", source, annotations, "python")
    # Existing docstring preserved, no second docstring added
    assert result.count('"""') == 2  # opening and closing of the original only


def test_apply_python_inline_comment():
    source = "x = 42\n"
    annotations = [Annotation(type="inline", target="", line=1, comment="the answer", confidence=0.9)]
    result, _ = apply_annotations("foo.py", source, annotations, "python")
    assert "# the answer" in result


def test_apply_python_inline_skips_line_with_existing_comment():
    source = "x = 42  # existing\n"
    annotations = [Annotation(type="inline", target="", line=1, comment="new comment", confidence=0.9)]
    result, _ = apply_annotations("foo.py", source, annotations, "python")
    # No second comment appended
    assert result.count("#") == 1


def test_apply_annotations_low_confidence_held_back():
    source = "def foo():\n    pass\n"
    annotations = [Annotation(type="docstring", target="foo", line=1, comment="Not sure.", confidence=0.5)]
    result, low_conf = apply_annotations("foo.py", source, annotations, "python", confidence_threshold=0.7)
    assert result == source  # nothing written
    assert len(low_conf) == 1
    assert low_conf[0].comment == "Not sure."


def test_apply_annotations_multiple_sorted_by_line_descending():
    """Insertions at higher line numbers first to avoid shifting earlier line refs."""
    source = "def a():\n    pass\n\ndef b():\n    pass\n"
    annotations = [
        Annotation(type="docstring", target="a", line=1, comment="Func a.", confidence=0.9),
        Annotation(type="docstring", target="b", line=4, comment="Func b.", confidence=0.9),
    ]
    result, _ = apply_annotations("foo.py", source, annotations, "python")
    assert "Func a." in result
    assert "Func b." in result
    # Both annotations present — order preserved in output
    assert result.index("Func a.") < result.index("Func b.")


# ── apply_annotations — non-Python ───────────────────────────────────────────


def test_apply_go_block_comment():
    source = "func Add(a, b int) int {\n\treturn a + b\n}\n"
    annotations = [
        Annotation(type="docstring", target="Add", line=1, comment="Add returns the sum of a and b.", confidence=0.9)
    ]
    result, _ = apply_annotations("math.go", source, annotations, "go")
    assert "// Add returns the sum of a and b." in result


def test_apply_js_jsdoc_block():
    source = "function greet(name) {\n  return `hello ${name}`;\n}\n"
    annotations = [Annotation(type="docstring", target="greet", line=1, comment="Return a greeting.", confidence=0.9)]
    result, _ = apply_annotations("greet.js", source, annotations, "javascript")
    assert "/**" in result
    assert "Return a greeting." in result


def test_apply_js_inline_comment():
    source = "const x = 42;\n"
    annotations = [Annotation(type="inline", target="", line=1, comment="the answer", confidence=0.9)]
    result, _ = apply_annotations("foo.js", source, annotations, "javascript")
    assert "// the answer" in result


# ── Annotator._filter_files ───────────────────────────────────────────────────


def _make_annotator():
    settings = Settings()
    ui = MagicMock()
    return Annotator(
        llm_client=MagicMock(), settings=settings, ui=ui, target="/tmp/proj", output_dir="/tmp/proj/codilay"
    )


def test_filter_files_excludes_non_code():
    annotator = _make_annotator()
    files = ["src/main.py", "README.md", "package.json", "src/app.ts"]
    result = annotator._filter_files(files, scope=None, exclude=None)
    assert "src/main.py" in result
    assert "src/app.ts" in result
    assert "README.md" not in result
    assert "package.json" not in result


def test_filter_files_excludes_generated():
    annotator = _make_annotator()
    files = ["lib/app.dart", "lib/app.g.dart", "migrations/0001_init.py"]
    result = annotator._filter_files(files, scope=None, exclude=None)
    assert "lib/app.dart" in result
    assert "lib/app.g.dart" not in result
    assert "migrations/0001_init.py" not in result


def test_filter_files_excludes_tests_by_default():
    annotator = _make_annotator()
    annotator.settings.annotate_skip_tests = True
    files = ["src/main.py", "tests/test_main.py", "src/utils.ts", "src/utils.test.ts"]
    result = annotator._filter_files(files, scope=None, exclude=None)
    assert "src/main.py" in result
    assert "src/utils.ts" in result
    assert "tests/test_main.py" not in result
    assert "src/utils.test.ts" not in result


def test_filter_files_includes_tests_when_disabled():
    annotator = _make_annotator()
    annotator.settings.annotate_skip_tests = False
    files = ["src/main.py", "tests/test_main.py"]
    result = annotator._filter_files(files, scope=None, exclude=None)
    assert "tests/test_main.py" in result


def test_filter_files_respects_scope():
    annotator = _make_annotator()
    files = ["src/auth/login.py", "src/payments/charge.py", "lib/utils.py"]
    result = annotator._filter_files(files, scope=["src/auth/"], exclude=None)
    assert "src/auth/login.py" in result
    assert "src/payments/charge.py" not in result
    assert "lib/utils.py" not in result


def test_filter_files_respects_exclude():
    annotator = _make_annotator()
    files = ["src/main.py", "src/generated/models.py", "src/utils.py"]
    result = annotator._filter_files(files, scope=None, exclude=["src/generated/"])
    assert "src/main.py" in result
    assert "src/utils.py" in result
    assert "src/generated/models.py" not in result


def test_filter_files_unknown_extension_excluded():
    annotator = _make_annotator()
    files = ["src/main.py", "assets/logo.svg", "data/export.csv"]
    result = annotator._filter_files(files, scope=None, exclude=None)
    assert "src/main.py" in result
    assert "assets/logo.svg" not in result
    assert "data/export.csv" not in result


# ── Annotator._detect_language ────────────────────────────────────────────────


def test_detect_language_python():
    a = _make_annotator()
    assert a._detect_language("src/main.py") == "python"


def test_detect_language_typescript():
    a = _make_annotator()
    assert a._detect_language("src/App.tsx") == "typescript"


def test_detect_language_unknown():
    a = _make_annotator()
    assert a._detect_language("data/file.xyz") == "unknown"


# ── Annotator._extract_wires_for_file ────────────────────────────────────────


def test_extract_wires_called_by():
    a = _make_annotator()
    wires = [
        {"from": "routes/orders.py", "to": "services/payment.py", "type": "import"},
        {"from": "scheduler/jobs.py", "to": "services/payment.py", "type": "call"},
    ]
    result = a._extract_wires_for_file("services/payment.py", wires)
    assert "orders.py" in result["called_by"]
    assert "jobs.py" in result["called_by"]
    assert result["calls"] == []


def test_extract_wires_calls():
    a = _make_annotator()
    wires = [
        {"from": "services/payment.py", "to": "models/order.py", "type": "import"},
        {"from": "services/payment.py", "to": "clients/stripe.py", "type": "call"},
    ]
    result = a._extract_wires_for_file("services/payment.py", wires)
    assert "order.py" in result["calls"]
    assert "stripe.py" in result["calls"]
    assert result["called_by"] == []


def test_extract_wires_empty():
    a = _make_annotator()
    result = a._extract_wires_for_file("src/main.py", [])
    assert result == {"called_by": [], "calls": []}


# ── check_git_clean ───────────────────────────────────────────────────────────


def test_check_git_clean_clean_repo():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        is_clean, msg = check_git_clean("/some/repo")
    assert is_clean is True


def test_check_git_clean_dirty_repo():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=" M src/main.py\n")
        is_clean, msg = check_git_clean("/some/repo")
    assert is_clean is False
    assert "uncommitted" in msg


def test_check_git_clean_not_a_repo():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=128, stdout="")
        is_clean, msg = check_git_clean("/some/dir")
    # Non-zero return code treated as "not a git repo" — no block
    assert is_clean is True


def test_check_git_clean_git_not_found():
    with patch("subprocess.run", side_effect=FileNotFoundError):
        is_clean, msg = check_git_clean("/some/dir")
    assert is_clean is True  # git absent — don't block


# ── Annotator.run — dry run end-to-end ───────────────────────────────────────


def test_annotator_run_dry_run_no_writes(tmp_path):
    """In dry-run mode no files should be written."""
    src_file = tmp_path / "main.py"
    src_file.write_text("def hello():\n    pass\n")

    settings = Settings()
    settings.annotate_skip_tests = False
    settings.annotate_confidence_threshold = 0.0

    ui = MagicMock()

    # LLM triage returns ANNOTATE
    # LLM annotation returns one docstring
    mock_llm = MagicMock()
    mock_llm.call.side_effect = [
        # First call: triage
        {"classifications": {"main.py": "ANNOTATE"}},
        # Second call: annotation
        {
            "annotations": [
                {"type": "docstring", "target": "hello", "line": 1, "comment": "Say hello.", "confidence": 0.9}
            ],
            "skip_reason": None,
        },
    ]

    annotator = Annotator(mock_llm, settings, ui, str(tmp_path), str(tmp_path / "codilay"))
    run = annotator.run(
        files=["main.py"],
        level="docstrings",
        dry_run=True,
    )

    # File content unchanged in dry-run
    assert src_file.read_text() == "def hello():\n    pass\n"
    assert "main.py" in run.files_annotated


def test_annotator_run_writes_on_real_run(tmp_path):
    """In real mode, the annotated content should be written to disk."""
    src_file = tmp_path / "service.py"
    src_file.write_text("def process():\n    return True\n")

    settings = Settings()
    settings.annotate_skip_tests = False
    settings.annotate_confidence_threshold = 0.0
    settings.annotate_syntax_validation = True
    settings.annotate_auto_commit = False

    ui = MagicMock()

    mock_llm = MagicMock()
    mock_llm.call.side_effect = [
        {"classifications": {"service.py": "ANNOTATE"}},
        {
            "annotations": [
                {
                    "type": "docstring",
                    "target": "process",
                    "line": 1,
                    "comment": "Process something.",
                    "confidence": 0.95,
                }
            ],
            "skip_reason": None,
        },
    ]

    annotator = Annotator(mock_llm, settings, ui, str(tmp_path), str(tmp_path / "codilay"))
    run = annotator.run(files=["service.py"], level="docstrings", dry_run=False)

    new_content = src_file.read_text()
    assert "Process something." in new_content
    assert "service.py" in run.files_annotated
    # Backup created
    assert run.backup_dir is not None
    assert os.path.isfile(os.path.join(run.backup_dir, "service.py"))


def test_annotator_run_skips_triage_ignore(tmp_path):
    """Files classified as IGNORE by triage should not be annotated."""
    readme = tmp_path / "README.md"
    readme.write_text("# Project\n")

    settings = Settings()
    ui = MagicMock()

    mock_llm = MagicMock()
    # Triage classifies as IGNORE
    mock_llm.call.return_value = {"classifications": {"README.md": "IGNORE"}}

    annotator = Annotator(mock_llm, settings, ui, str(tmp_path), str(tmp_path / "codilay"))

    # README.md is filtered out by NEVER_ANNOTATE_EXTENSIONS before even reaching triage
    run = annotator.run(files=["README.md"], dry_run=True)
    assert run.files_annotated == []


def test_annotator_run_skips_llm_annotated_skip(tmp_path):
    """Files the LLM decides should be skipped result in no output."""
    src = tmp_path / "config.py"
    src.write_text("DEBUG = True\n")

    settings = Settings()
    settings.annotate_skip_tests = False
    settings.annotate_confidence_threshold = 0.0
    ui = MagicMock()

    mock_llm = MagicMock()
    mock_llm.call.side_effect = [
        {"classifications": {"config.py": "ANNOTATE"}},
        {"annotations": [], "skip_reason": "File is just configuration constants"},
    ]

    annotator = Annotator(mock_llm, settings, ui, str(tmp_path), str(tmp_path / "codilay"))
    run = annotator.run(files=["config.py"], dry_run=True)

    assert run.files_annotated == []
    assert "config.py" in run.files_skipped


# ── Annotator.rollback ────────────────────────────────────────────────────────


def test_annotator_rollback_restores_files(tmp_path):
    original_content = "def foo():\n    pass\n"
    annotated_content = 'def foo():\n    """Annotated."""\n    pass\n'

    src = tmp_path / "foo.py"
    src.write_text(annotated_content)

    # Create a fake backup
    run_id = "20240101_120000"
    backup_dir = tmp_path / "codilay" / "annotation_history" / run_id
    backup_dir.mkdir(parents=True)
    (backup_dir / "foo.py").write_text(original_content)

    settings = Settings()
    ui = MagicMock()
    annotator = Annotator(MagicMock(), settings, ui, str(tmp_path), str(tmp_path / "codilay"))
    success = annotator.rollback(run_id)

    assert success is True
    assert src.read_text() == original_content


def test_annotator_rollback_missing_run_returns_false(tmp_path):
    settings = Settings()
    ui = MagicMock()
    annotator = Annotator(MagicMock(), settings, ui, str(tmp_path), str(tmp_path / "codilay"))
    success = annotator.rollback("nonexistent_run_id")
    assert success is False
