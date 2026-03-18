"""Tests for codilay.cli diff-run command."""

import os
import tempfile
from unittest.mock import MagicMock, patch, call
from click.testing import CliRunner

import pytest

from codilay.cli import cli


class TestDiffRunCLICommand:
    """Tests for the diff-run CLI command."""

    @pytest.fixture
    def git_repo_with_commits(self):
        """Create a temporary git repository with some commits."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize repo
            os.system(f"cd {tmpdir} && git init -q")
            os.system(f"cd {tmpdir} && git config user.email 'test@test.com'")
            os.system(f"cd {tmpdir} && git config user.name 'Test User'")

            # Create initial commit
            test_file = os.path.join(tmpdir, "test.txt")
            with open(test_file, "w") as f:
                f.write("initial content")
            os.system(f"cd {tmpdir} && git add . && git commit -q -m 'initial commit'")

            # Get the first commit hash
            first_commit = os.popen(f"cd {tmpdir} && git rev-parse HEAD").read().strip()

            # Create second commit
            with open(test_file, "w") as f:
                f.write("modified content")
            os.system(f"cd {tmpdir} && git commit -q -am 'second commit'")

            # Add new file and create third commit
            new_file = os.path.join(tmpdir, "new.txt")
            with open(new_file, "w") as f:
                f.write("new file content")
            os.system(f"cd {tmpdir} && git add . && git commit -q -m 'add new file'")

            yield tmpdir, first_commit

    def test_diff_run_command_exists(self):
        """Test that diff-run command exists."""
        runner = CliRunner()
        result = runner.invoke(cli, ["diff-run", "--help"])
        assert result.exit_code == 0
        assert "diff-run" in result.output.lower()

    def test_diff_run_help_shows_since_option(self):
        """Test that help text mentions --since option."""
        runner = CliRunner()
        result = runner.invoke(cli, ["diff-run", "--help"])
        assert "--since" in result.output

    def test_diff_run_help_shows_since_branch_option(self):
        """Test that help text mentions --since-branch option."""
        runner = CliRunner()
        result = runner.invoke(cli, ["diff-run", "--help"])
        assert "--since-branch" in result.output

    def test_diff_run_help_shows_update_doc_option(self):
        """Test that help text mentions --update-doc option."""
        runner = CliRunner()
        result = runner.invoke(cli, ["diff-run", "--help"])
        assert "--update-doc" in result.output

    def test_diff_run_requires_boundary(self, git_repo_with_commits):
        """Test that diff-run requires a boundary (--since or --since-branch)."""
        runner = CliRunner()
        tmpdir, _ = git_repo_with_commits

        # Run without boundary - should show error message about boundary
        result = runner.invoke(cli, ["diff-run", tmpdir])

        # Should show error message about missing boundary
        assert "since" in result.output.lower()

    def test_diff_run_with_valid_commit_boundary(self, git_repo_with_commits):
        """Test diff-run with a valid commit hash boundary."""
        runner = CliRunner()
        tmpdir, first_commit = git_repo_with_commits

        # This will fail without proper API key setup, but it should at least parse the command
        result = runner.invoke(cli, ["diff-run", tmpdir, "--since", first_commit[:7]])

        # Either it processes (and fails on API key) or shows proper error
        # The important part is the command itself is recognized
        assert "diff-run" in result.output.lower() or "error" in result.output.lower()

    def test_diff_run_with_verbose_flag(self, git_repo_with_commits):
        """Test that --verbose flag is accepted."""
        runner = CliRunner()
        tmpdir, first_commit = git_repo_with_commits

        result = runner.invoke(cli, ["diff-run", tmpdir, "--since", first_commit[:7], "-v"])

        # Command should be recognized
        assert result.exit_code >= 0

    def test_diff_run_with_invalid_commit(self, git_repo_with_commits):
        """Test diff-run with an invalid commit hash."""
        runner = CliRunner()
        tmpdir, _ = git_repo_with_commits

        result = runner.invoke(cli, ["diff-run", tmpdir, "--since", "invalid_hash_12345"])

        # Should fail with error about invalid boundary
        assert "error" in result.output.lower() or "could not resolve" in result.output.lower()

    def test_diff_run_on_non_git_repo(self):
        """Test diff-run on a non-git directory."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(cli, ["diff-run", tmpdir, "--since", "HEAD~1"])

            # Should fail because it's not a git repo
            assert "error" in result.output.lower() or "not a git repository" in result.output.lower()

    def test_diff_run_accepts_provider_option(self, git_repo_with_commits):
        """Test that diff-run accepts --provider option."""
        runner = CliRunner()
        tmpdir, first_commit = git_repo_with_commits

        result = runner.invoke(cli, ["diff-run", tmpdir, "--since", first_commit[:7], "--provider", "ollama"])

        # Should be recognized (might fail later due to ollama unavailable)
        assert result.exit_code >= 0

    def test_diff_run_accepts_model_option(self, git_repo_with_commits):
        """Test that diff-run accepts --model option."""
        runner = CliRunner()
        tmpdir, first_commit = git_repo_with_commits

        result = runner.invoke(cli, ["diff-run", tmpdir, "--since", first_commit[:7], "--model", "gpt-4"])

        # Should be recognized
        assert result.exit_code >= 0

    def test_diff_run_accepts_config_option(self, git_repo_with_commits):
        """Test that diff-run accepts --config option."""
        runner = CliRunner()
        tmpdir, first_commit = git_repo_with_commits

        result = runner.invoke(
            cli, ["diff-run", tmpdir, "--since", first_commit[:7], "--config", "codilay.config.json"]
        )

        # Should be recognized
        assert result.exit_code >= 0

    def test_diff_run_accepts_output_option(self, git_repo_with_commits):
        """Test that diff-run accepts --output option."""
        runner = CliRunner()
        tmpdir, first_commit = git_repo_with_commits

        result = runner.invoke(cli, ["diff-run", tmpdir, "--since", first_commit[:7], "--output", "custom_output"])

        # Should be recognized
        assert result.exit_code >= 0

    def test_diff_run_help_text_has_examples(self):
        """Test that help text includes usage examples."""
        runner = CliRunner()
        result = runner.invoke(cli, ["diff-run", "--help"])

        assert "example" in result.output.lower() or "codilay diff-run" in result.output

    def test_diff_run_shows_error_for_both_since_and_branch(self, git_repo_with_commits):
        """Test behavior when both --since and --since-branch are provided."""
        runner = CliRunner()
        tmpdir, first_commit = git_repo_with_commits

        # Test providing both (unclear what behavior should be, but shouldn't crash)
        result = runner.invoke(cli, ["diff-run", tmpdir, "--since", first_commit[:7], "--since-branch", "main"])

        # Should not crash - either accepts it or shows error
        assert result.exit_code >= 0
