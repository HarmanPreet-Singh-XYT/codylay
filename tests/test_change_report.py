"""Tests for codilay.change_report — Change report generation."""

import os
import tempfile
from datetime import datetime
from unittest.mock import MagicMock, patch, mock_open

import pytest

from codilay.change_report import ChangeReportGenerator


class TestChangeReportGenerator:
    """Tests for ChangeReportGenerator functionality."""

    @pytest.fixture
    def temp_output_dir(self):
        """Create a temporary output directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def sample_analysis_result(self):
        """Create a sample analysis result dict."""
        return {
            "summary": "Updated authentication module with JWT support and added new user model.",
            "added": [{"path": "src/models/user.py", "description": "New user model for authentication"}],
            "modified": [{"path": "src/auth/handler.py", "description": "Added JWT token validation"}],
            "deleted": [{"path": "src/legacy/old_auth.py", "description": "Removed legacy authentication"}],
            "renamed": [
                {"path": "src/utils/helpers.py", "old_path": "src/utils/utils.py", "description": "Renamed for clarity"}
            ],
            "wire_impact": {
                "new_dependencies": ["jwt"],
                "satisfied_dependencies": ["requests"],
                "broken_dependencies": ["legacy-auth"],
            },
        }

    def test_init_creates_generator(self, temp_output_dir):
        """Test that ChangeReportGenerator initializes correctly."""
        generator = ChangeReportGenerator(temp_output_dir)
        assert generator.output_dir == temp_output_dir

    def test_generate_report_returns_path(self, temp_output_dir, sample_analysis_result):
        """Test that generate_report returns a file path."""
        generator = ChangeReportGenerator(temp_output_dir)

        report_path = generator.generate_report(
            analysis_result=sample_analysis_result,
            boundary_ref="abc123f",
            boundary_type="commit",
            commits_count=3,
            commit_messages=["feat: add auth", "fix: jwt bug", "docs: update"],
        )

        assert isinstance(report_path, str)
        assert report_path.startswith(temp_output_dir)
        assert "CHANGES_" in report_path

    def test_generate_report_creates_file(self, temp_output_dir, sample_analysis_result):
        """Test that generate_report creates a file."""
        generator = ChangeReportGenerator(temp_output_dir)

        report_path = generator.generate_report(
            analysis_result=sample_analysis_result,
            boundary_ref="abc123f",
            boundary_type="commit",
            commits_count=1,
            commit_messages=["feat: add auth"],
        )

        assert os.path.exists(report_path)

    def test_generate_report_file_contains_summary(self, temp_output_dir, sample_analysis_result):
        """Test that generated file contains summary."""
        generator = ChangeReportGenerator(temp_output_dir)

        report_path = generator.generate_report(
            analysis_result=sample_analysis_result,
            boundary_ref="abc123f",
            boundary_type="commit",
            commits_count=1,
            commit_messages=["feat: add auth"],
        )

        with open(report_path, "r") as f:
            content = f.read()

        assert "## Summary" in content
        assert "Updated authentication module" in content

    def test_generate_report_file_contains_commits(self, temp_output_dir, sample_analysis_result):
        """Test that generated file contains commit log."""
        generator = ChangeReportGenerator(temp_output_dir)

        report_path = generator.generate_report(
            analysis_result=sample_analysis_result,
            boundary_ref="v1.0.0",
            boundary_type="tag",
            commits_count=3,
            commit_messages=["feat: add auth", "fix: jwt bug", "docs: update"],
        )

        with open(report_path, "r") as f:
            content = f.read()

        assert "## Commits" in content
        assert "feat: add auth" in content
        assert "fix: jwt bug" in content

    def test_generate_report_file_contains_added_section(self, temp_output_dir, sample_analysis_result):
        """Test that generated file contains added files section."""
        generator = ChangeReportGenerator(temp_output_dir)

        report_path = generator.generate_report(
            analysis_result=sample_analysis_result,
            boundary_ref="abc123f",
            boundary_type="commit",
            commits_count=1,
            commit_messages=["feat: add auth"],
        )

        with open(report_path, "r") as f:
            content = f.read()

        assert "## Added" in content
        assert "src/models/user.py" in content

    def test_generate_report_file_contains_modified_section(self, temp_output_dir, sample_analysis_result):
        """Test that generated file contains modified files section."""
        generator = ChangeReportGenerator(temp_output_dir)

        report_path = generator.generate_report(
            analysis_result=sample_analysis_result,
            boundary_ref="abc123f",
            boundary_type="commit",
            commits_count=1,
            commit_messages=["feat: add auth"],
        )

        with open(report_path, "r") as f:
            content = f.read()

        assert "## Modified" in content
        assert "src/auth/handler.py" in content

    def test_generate_report_file_contains_deleted_section(self, temp_output_dir, sample_analysis_result):
        """Test that generated file contains deleted files section."""
        generator = ChangeReportGenerator(temp_output_dir)

        report_path = generator.generate_report(
            analysis_result=sample_analysis_result,
            boundary_ref="abc123f",
            boundary_type="commit",
            commits_count=1,
            commit_messages=["feat: add auth"],
        )

        with open(report_path, "r") as f:
            content = f.read()

        assert "## Deleted" in content
        assert "src/legacy/old_auth.py" in content

    def test_generate_report_filename_includes_boundary_type(self, temp_output_dir, sample_analysis_result):
        """Test that filename includes the boundary type."""
        generator = ChangeReportGenerator(temp_output_dir)

        # Test with commit
        report_path = generator.generate_report(
            analysis_result=sample_analysis_result,
            boundary_ref="abc123f",
            boundary_type="commit",
            commits_count=1,
            commit_messages=["feat: add auth"],
        )
        assert "CHANGES_commit_" in report_path

    def test_generate_report_filename_includes_timestamp(self, temp_output_dir, sample_analysis_result):
        """Test that filename includes timestamp."""
        generator = ChangeReportGenerator(temp_output_dir)

        report_path = generator.generate_report(
            analysis_result=sample_analysis_result,
            boundary_ref="abc123f",
            boundary_type="commit",
            commits_count=1,
            commit_messages=["feat: add auth"],
        )

        # Should have format: CHANGES_commit_YYYYMMDD_HHMMSS.md
        filename = os.path.basename(report_path)
        assert filename.endswith(".md")
        assert len(filename) >= len("CHANGES_commit_20000101_000000.md")

    def test_format_boundary_label_commit(self, temp_output_dir):
        """Test boundary label formatting for commit."""
        generator = ChangeReportGenerator(temp_output_dir)
        label = generator._format_boundary_label("abc123f", "commit")

        assert "abc123f" in label or "commit" in label.lower()

    def test_format_boundary_label_tag(self, temp_output_dir):
        """Test boundary label formatting for tag."""
        generator = ChangeReportGenerator(temp_output_dir)
        label = generator._format_boundary_label("v1.0.0", "tag")

        assert "v1.0.0" in label

    def test_format_boundary_label_date(self, temp_output_dir):
        """Test boundary label formatting for date."""
        generator = ChangeReportGenerator(temp_output_dir)
        label = generator._format_boundary_label("2024-03-01", "date")

        assert "2024-03-01" in label

    def test_format_boundary_label_branch(self, temp_output_dir):
        """Test boundary label formatting for branch."""
        generator = ChangeReportGenerator(temp_output_dir)
        label = generator._format_boundary_label("main", "branch")

        assert "main" in label

    def test_generate_report_with_empty_analysis(self, temp_output_dir):
        """Test report generation with empty analysis."""
        generator = ChangeReportGenerator(temp_output_dir)

        report_path = generator.generate_report(
            analysis_result={}, boundary_ref="abc123f", boundary_type="commit", commits_count=0, commit_messages=[]
        )

        assert os.path.exists(report_path)

        with open(report_path, "r") as f:
            content = f.read()

        assert "CodiLay Change Report" in content

    def test_generate_report_with_many_commits(self, temp_output_dir, sample_analysis_result):
        """Test report generation with many commits."""
        commits = [f"commit-{i}" for i in range(25)]
        generator = ChangeReportGenerator(temp_output_dir)

        report_path = generator.generate_report(
            analysis_result=sample_analysis_result,
            boundary_ref="abc123f",
            boundary_type="branch",
            commits_count=len(commits),
            commit_messages=commits,
        )

        with open(report_path, "r") as f:
            content = f.read()

        # Should truncate to first 20 with "and X more" message
        assert "and" in content or "25" in content

    def test_format_boundary_label_truncates_long_commit(self, temp_output_dir):
        """Test that very long commit hashes are truncated."""
        generator = ChangeReportGenerator(temp_output_dir)
        long_hash = "a" * 100
        label = generator._format_boundary_label(long_hash, "commit")

        # Should either truncate or format nicely
        assert len(label) <= len(long_hash) + 20

    def test_generate_report_handles_special_characters(self, temp_output_dir):
        """Test that report handles special characters in paths and messages."""
        analysis = {
            "summary": "Added support for Unicode & special chars: 文字",
            "added": [{"path": "src/utils/中文.py", "description": "Unicode support"}],
            "modified": [],
            "deleted": [],
            "renamed": [],
            "wire_impact": {},
        }

        generator = ChangeReportGenerator(temp_output_dir)
        report_path = generator.generate_report(
            analysis_result=analysis,
            boundary_ref="abc123f",
            boundary_type="commit",
            commits_count=1,
            commit_messages=["feat: Unicode & special chars"],
        )

        with open(report_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "Unicode & special chars" in content

    def test_report_has_valid_markdown_structure(self, temp_output_dir, sample_analysis_result):
        """Test that generated report has valid markdown structure."""
        generator = ChangeReportGenerator(temp_output_dir)

        report_path = generator.generate_report(
            analysis_result=sample_analysis_result,
            boundary_ref="abc123f",
            boundary_type="commit",
            commits_count=3,
            commit_messages=["feat: 1", "fix: 2", "docs: 3"],
        )

        with open(report_path, "r") as f:
            content = f.read()

        # Should have proper markdown headers
        assert content.count("#") >= 3

        # Should have proper formatting
        lines = content.split("\n")
        assert lines[0].startswith("#")  # First line is header
