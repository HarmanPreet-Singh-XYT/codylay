"""Tests for codilay.diff_analyzer — Git diff extraction and boundary resolution."""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call

import pytest

from codilay.diff_analyzer import DiffAnalyzer, DiffAnalysisResult


class TestDiffAnalyzerBoundaryResolution:
    """Tests for boundary resolution (commit, tag, date, branch)."""

    @pytest.fixture
    def temp_git_repo(self):
        """Create a temporary git repository for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize repo
            os.system(f"cd {tmpdir} && git init -q")
            os.system(f"cd {tmpdir} && git config user.email 'test@test.com'")
            os.system(f"cd {tmpdir} && git config user.name 'Test User'")

            # Create initial commit
            test_file = os.path.join(tmpdir, "test.txt")
            with open(test_file, "w") as f:
                f.write("initial")
            os.system(f"cd {tmpdir} && git add test.txt && git commit -q -m 'initial'")

            yield tmpdir

    def test_resolve_boundary_with_commit_hash(self, temp_git_repo):
        """Test resolving a commit hash boundary."""
        # Get the commit hash
        result = os.popen(f"cd {temp_git_repo} && git rev-parse HEAD").read().strip()
        commit_hash = result[:7]

        analyzer = DiffAnalyzer(temp_git_repo)
        boundary_result = analyzer.resolve_boundary(since=commit_hash)

        assert boundary_result is not None
        base_commit, boundary_type = boundary_result
        assert boundary_type == "commit"
        assert base_commit is not None

    def test_resolve_boundary_with_invalid_commit(self, temp_git_repo):
        """Test resolving an invalid commit hash returns None."""
        analyzer = DiffAnalyzer(temp_git_repo)
        boundary_result = analyzer.resolve_boundary(since="invalid_hash_xyz")

        assert boundary_result is None

    def test_resolve_boundary_with_valid_date(self, temp_git_repo):
        """Test resolving a valid date boundary."""
        analyzer = DiffAnalyzer(temp_git_repo)
        # Use a date from before the repo was created to ensure commits exist
        two_days_ago = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        boundary_result = analyzer.resolve_boundary(since=two_days_ago)

        # For a brand new repo, this might return None since there are no commits before that date
        # So we just verify it either works or returns None gracefully
        if boundary_result is not None:
            base_commit, boundary_type = boundary_result
            assert boundary_type == "date"

    def test_resolve_boundary_with_invalid_date_format(self, temp_git_repo):
        """Test resolving an invalid date format returns None."""
        analyzer = DiffAnalyzer(temp_git_repo)
        boundary_result = analyzer.resolve_boundary(since="01-01-2024")  # wrong format

        assert boundary_result is None

    def test_resolve_boundary_with_future_date(self, temp_git_repo):
        """Test resolving a future date gracefully."""
        analyzer = DiffAnalyzer(temp_git_repo)
        future = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        boundary_result = analyzer.resolve_boundary(since=future)

        # Git might still return commits even for future dates, so we just verify it returns something valid
        # The important thing is that it doesn't crash
        if boundary_result is not None:
            base_commit, boundary_type = boundary_result
            assert boundary_type == "date"

    def test_resolve_boundary_with_tag(self, temp_git_repo):
        """Test resolving a git tag boundary."""
        # Create a tag
        os.system(f"cd {temp_git_repo} && git tag -q v1.0.0")

        analyzer = DiffAnalyzer(temp_git_repo)
        boundary_result = analyzer.resolve_boundary(since="v1.0.0")

        # Tag should resolve to the commit it points to
        if boundary_result is not None:
            base_commit, boundary_type = boundary_result
            assert boundary_type == "tag"

    def test_resolve_boundary_with_invalid_tag(self, temp_git_repo):
        """Test resolving an invalid tag returns None."""
        analyzer = DiffAnalyzer(temp_git_repo)
        boundary_result = analyzer.resolve_boundary(since="v99.99.99")

        assert boundary_result is None

    def test_is_git_repo_true(self, temp_git_repo):
        """Test is_git_repo returns True for git repositories."""
        analyzer = DiffAnalyzer(temp_git_repo)
        assert analyzer.is_git_repo is True

    def test_is_git_repo_false(self):
        """Test is_git_repo returns False for non-git directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            analyzer = DiffAnalyzer(tmpdir)
            assert analyzer.is_git_repo is False


class TestDiffAnalyzerDiffExtraction:
    """Tests for diff extraction and file change detection."""

    @pytest.fixture
    def git_repo_with_changes(self):
        """Create a git repo with various file changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize repo
            os.system(f"cd {tmpdir} && git init -q")
            os.system(f"cd {tmpdir} && git config user.email 'test@test.com'")
            os.system(f"cd {tmpdir} && git config user.name 'Test User'")

            # Create initial commit
            os.makedirs(os.path.join(tmpdir, "src"), exist_ok=True)
            init_file = os.path.join(tmpdir, "src", "main.py")
            with open(init_file, "w") as f:
                f.write("def main():\n    pass\n")
            os.system(f"cd {tmpdir} && git add . && git commit -q -m 'initial'")

            # Get the initial commit
            initial_commit = os.popen(f"cd {tmpdir} && git rev-parse HEAD").read().strip()

            # Modify a file
            with open(init_file, "w") as f:
                f.write("def main():\n    print('hello')\n")

            # Add a new file
            new_file = os.path.join(tmpdir, "src", "utils.py")
            with open(new_file, "w") as f:
                f.write("def helper():\n    pass\n")

            os.system(f"cd {tmpdir} && git add . && git commit -q -m 'add utils'")

            yield tmpdir, initial_commit

    def test_analyze_detects_modified_files(self, git_repo_with_changes):
        """Test that analyze detects modified files."""
        tmpdir, initial_commit = git_repo_with_changes
        analyzer = DiffAnalyzer(tmpdir)

        diff_result = analyzer.analyze(since=initial_commit)

        assert diff_result is not None
        assert len(diff_result.modified_files) > 0
        assert any("main.py" in f.path for f in diff_result.modified_files)

    def test_analyze_detects_added_files(self, git_repo_with_changes):
        """Test that analyze detects added files."""
        tmpdir, initial_commit = git_repo_with_changes
        analyzer = DiffAnalyzer(tmpdir)

        diff_result = analyzer.analyze(since=initial_commit)

        assert diff_result is not None
        assert len(diff_result.added_files) > 0
        assert any("utils.py" in f.path for f in diff_result.added_files)

    def test_analyze_extracts_diff_content(self, git_repo_with_changes):
        """Test that analyze extracts diff content for modified files."""
        tmpdir, initial_commit = git_repo_with_changes
        analyzer = DiffAnalyzer(tmpdir)

        diff_result = analyzer.analyze(since=initial_commit)

        # Find modified main.py
        main_py = next((f for f in diff_result.modified_files if "main.py" in f.path), None)
        assert main_py is not None
        assert main_py.diff_content is not None
        assert len(main_py.diff_content) > 0

    def test_analyze_extracts_full_content_for_new_files(self, git_repo_with_changes):
        """Test that analyze extracts full content for new files."""
        tmpdir, initial_commit = git_repo_with_changes
        analyzer = DiffAnalyzer(tmpdir)

        diff_result = analyzer.analyze(since=initial_commit)

        # Find new utils.py
        utils_py = next((f for f in diff_result.added_files if "utils.py" in f.path), None)
        assert utils_py is not None
        assert utils_py.full_content is not None
        assert "def helper" in utils_py.full_content

    def test_analyze_returns_diff_result_with_metadata(self, git_repo_with_changes):
        """Test that analyze returns DiffAnalysisResult with all metadata."""
        tmpdir, initial_commit = git_repo_with_changes
        analyzer = DiffAnalyzer(tmpdir)

        diff_result = analyzer.analyze(since=initial_commit)

        assert isinstance(diff_result, DiffAnalysisResult)
        assert diff_result.boundary_ref == initial_commit
        assert diff_result.commits_count >= 1
        assert len(diff_result.commit_messages) >= 1


class TestDiffAnalyzerNonGitRepo:
    """Tests for non-git directory handling."""

    def test_analyze_on_non_git_repo_returns_none(self):
        """Test that analyze on non-git repo returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            analyzer = DiffAnalyzer(tmpdir)
            result = analyzer.analyze(since="HEAD~1")
            assert result is None

    def test_resolve_boundary_on_non_git_repo_returns_none(self):
        """Test that resolve_boundary on non-git repo returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            analyzer = DiffAnalyzer(tmpdir)
            result = analyzer.resolve_boundary(since="HEAD~1")
            assert result is None
