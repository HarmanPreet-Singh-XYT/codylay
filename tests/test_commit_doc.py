"""
Tests for CommitDocGenerator.

Covers: context loading, prompt building, single/range generation,
hook install/uninstall, and edge cases.
"""

import os
import stat
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from codilay.commit_doc import CommitDocGenerator

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def temp_output_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.call.return_value = {
        "answer": "# abc1234 — 2024-03-14\n\n> feat: add retry\n\n## What changed\n\nRetry logic added."
    }
    return llm


@pytest.fixture
def generator(mock_llm, temp_output_dir):
    return CommitDocGenerator(llm_client=mock_llm, output_dir=temp_output_dir)


@pytest.fixture
def git_repo(tmp_path):
    """Create a minimal real git repository with two commits."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), capture_output=True)

    # First commit
    (repo / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init: add a.py"], cwd=str(repo), capture_output=True)

    # Second commit
    (repo / "b.py").write_text("y = 2\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", "feat: add b.py"], cwd=str(repo), capture_output=True)

    return repo


# ── Init ──────────────────────────────────────────────────────────────────────


def test_init_creates_docs_dir(mock_llm, temp_output_dir):
    gen = CommitDocGenerator(llm_client=mock_llm, output_dir=temp_output_dir)
    assert os.path.isdir(gen.docs_dir)
    assert gen.docs_dir == os.path.join(temp_output_dir, "commit-docs")


# ── Git helpers ───────────────────────────────────────────────────────────────


def test_get_last_commit(generator, git_repo):
    commit = generator.get_last_commit(str(git_repo))
    assert len(commit) == 40  # full SHA


def test_get_commits_in_range(generator, git_repo):
    commits = generator.get_commits_in_range("HEAD~1..HEAD", str(git_repo))
    assert len(commits) == 1
    assert len(commits[0]) == 40


def test_get_commits_in_range_multiple(generator, git_repo):
    # HEAD~1..HEAD returns the last commit; full log returns both
    commits = generator.get_commits_in_range("HEAD", str(git_repo))
    # This returns only HEAD itself when called without a range operator — just verify it's non-empty
    assert len(commits) >= 1


def test_run_git_failure_raises(generator, tmp_path):
    with pytest.raises(RuntimeError, match="git rev-parse"):
        generator._run_git(["rev-parse", "HEAD"], str(tmp_path))


def test_get_commit_info_structure(generator, git_repo):
    full_hash = generator.get_last_commit(str(git_repo))
    info = generator._get_commit_info(full_hash, str(git_repo))

    assert info["full_hash"] == full_hash
    assert len(info["hash"]) == 7
    assert info["message"] == "feat: add b.py"
    assert info["date"] != ""
    assert "b.py" in info["changed_files"]
    assert info["patch"] != ""


# ── Context loading ───────────────────────────────────────────────────────────


def test_load_codebase_context_no_file(generator, tmp_path):
    result = generator._load_codebase_context(["src/payment.py"], str(tmp_path / "nonexistent.md"))
    assert result is None


def test_load_codebase_context_no_match(generator, tmp_path):
    codebase_md = tmp_path / "CODEBASE.md"
    codebase_md.write_text("## Auth Module\n\nHandles authentication.\n")
    result = generator._load_codebase_context(["payment.py"], str(codebase_md))
    assert result is None


def test_load_codebase_context_match_by_filename(generator, tmp_path):
    codebase_md = tmp_path / "CODEBASE.md"
    codebase_md.write_text("## Payment Module\n\nHandles payment.py retry logic.\n\n## Auth Module\n\nHandles auth.\n")
    result = generator._load_codebase_context(["services/payment.py"], str(codebase_md))
    assert result is not None
    assert "payment.py" in result
    assert "Auth Module" not in result


def test_load_codebase_context_caps_at_five_sections(generator, tmp_path):
    sections = "\n".join(f"## Module {i}\n\nfile{i}.py is here.\n" for i in range(10))
    codebase_md = tmp_path / "CODEBASE.md"
    codebase_md.write_text(sections)
    files = [f"file{i}.py" for i in range(10)]
    result = generator._load_codebase_context(files, str(codebase_md))
    assert result is not None
    # At most 5 sections returned
    assert result.count("## Module") <= 5


# ── Prompt building ───────────────────────────────────────────────────────────


def test_build_user_prompt_no_context(generator):
    commit_info = {"hash": "abc1234", "date": "2024-03-14", "message": "fix: bug", "patch": "diff --git ..."}
    prompt = generator._build_user_prompt(commit_info, context=None)
    assert "abc1234" in prompt
    assert "fix: bug" in prompt
    assert "diff --git" in prompt
    assert "context" not in prompt.lower()


def test_build_user_prompt_with_context(generator):
    commit_info = {"hash": "abc1234", "date": "2024-03-14", "message": "fix: bug", "patch": "diff --git ..."}
    prompt = generator._build_user_prompt(commit_info, context="## Payment\n\nRetry logic.")
    assert "Codebase context" in prompt
    assert "Retry logic" in prompt


def test_build_user_prompt_truncates_large_patch(generator):
    big_patch = "x" * 20000
    commit_info = {"hash": "abc1234", "date": "2024-03-14", "message": "chore", "patch": big_patch}
    prompt = generator._build_user_prompt(commit_info, context=None)
    assert "truncated" in prompt
    assert len(prompt) < 15000


# ── generate() ────────────────────────────────────────────────────────────────


def test_generate_creates_file(generator, git_repo):
    full_hash = generator.get_last_commit(str(git_repo))
    result = generator.generate(commit_hash=full_hash, repo_path=str(git_repo))

    assert os.path.exists(result["path"])
    assert result["hash"] == full_hash[:7]
    assert result["content"] != ""
    assert "b.py" in result["changed_files"]


def test_generate_file_content_matches_llm_response(generator, git_repo):
    full_hash = generator.get_last_commit(str(git_repo))
    result = generator.generate(commit_hash=full_hash, repo_path=str(git_repo))

    with open(result["path"], "r") as f:
        on_disk = f.read()
    assert on_disk == result["content"]


def test_generate_without_context_does_not_load_codebase(generator, git_repo, tmp_path):
    full_hash = generator.get_last_commit(str(git_repo))
    with patch.object(generator, "_load_codebase_context") as mock_ctx:
        generator.generate(commit_hash=full_hash, repo_path=str(git_repo), use_context=False)
        mock_ctx.assert_not_called()


def test_generate_with_context_missing_codebase_md_falls_back(generator, git_repo, tmp_path):
    """When CODEBASE.md doesn't exist, context should be None (no crash)."""
    full_hash = generator.get_last_commit(str(git_repo))
    # Pass a path that doesn't exist — should still complete without error
    result = generator.generate(
        commit_hash=full_hash,
        repo_path=str(git_repo),
        use_context=True,
        codebase_md_path=str(tmp_path / "nonexistent.md"),
    )
    assert result["content"] != ""


def test_generate_with_context_loads_relevant_sections(generator, git_repo, tmp_path):
    codebase_md = tmp_path / "CODEBASE.md"
    codebase_md.write_text("## b.py Module\n\nAdds variable y.\n")
    full_hash = generator.get_last_commit(str(git_repo))

    generator.generate(
        commit_hash=full_hash,
        repo_path=str(git_repo),
        use_context=True,
        codebase_md_path=str(codebase_md),
    )

    # LLM was called with context included
    call_kwargs = generator.llm.call.call_args
    user_prompt = call_kwargs[1]["user_prompt"] if call_kwargs[1] else call_kwargs[0][1]
    assert "Codebase context" in user_prompt


def test_generate_uses_json_mode_false(generator, git_repo):
    full_hash = generator.get_last_commit(str(git_repo))
    generator.generate(commit_hash=full_hash, repo_path=str(git_repo))
    call_kwargs = generator.llm.call.call_args
    assert call_kwargs[1].get("json_mode") is False


# ── generate_range() ──────────────────────────────────────────────────────────


def test_generate_range_returns_one_result_per_commit(generator, git_repo, temp_output_dir):
    results = generator.generate_range("HEAD~1..HEAD", str(git_repo))
    assert len(results) == 1
    assert os.path.exists(results[0]["path"])


def test_generate_range_empty_range(generator, git_repo):
    # A range with no commits should produce an empty list without error
    results = generator.generate_range("HEAD..HEAD", str(git_repo))
    assert results == []


def test_generate_range_creates_separate_files(generator, git_repo):
    results = generator.generate_range("HEAD~1..HEAD", str(git_repo))
    paths = [r["path"] for r in results]
    assert len(paths) == len(set(paths))  # all unique


# ── doc_path() ────────────────────────────────────────────────────────────────


def test_doc_path_format(generator):
    path = generator.doc_path("abc1234")
    assert path.endswith("commit-docs/abc1234.md")


# ── Hook management ───────────────────────────────────────────────────────────


def test_install_hook_creates_file(generator, git_repo):
    hook_path = generator.install_post_commit_hook(str(git_repo))
    assert os.path.exists(hook_path)
    with open(hook_path) as f:
        content = f.read()
    assert "codilay commit-doc" in content
    assert "#!/bin/bash" in content


def test_install_hook_is_executable(generator, git_repo):
    hook_path = generator.install_post_commit_hook(str(git_repo))
    mode = os.stat(hook_path).st_mode
    assert mode & stat.S_IXUSR


def test_install_hook_idempotent(generator, git_repo):
    """Installing twice should not duplicate the hook lines."""
    generator.install_post_commit_hook(str(git_repo))
    generator.install_post_commit_hook(str(git_repo))
    hook_path = os.path.join(str(git_repo), ".git", "hooks", "post-commit")
    with open(hook_path) as f:
        content = f.read()
    assert content.count("codilay commit-doc") == 1


def test_install_hook_appends_to_existing(generator, git_repo):
    hook_path = os.path.join(str(git_repo), ".git", "hooks", "post-commit")
    with open(hook_path, "w") as f:
        f.write("#!/bin/bash\necho 'existing hook'\n")
    os.chmod(hook_path, 0o755)

    generator.install_post_commit_hook(str(git_repo))

    with open(hook_path) as f:
        content = f.read()
    assert "existing hook" in content
    assert "codilay commit-doc" in content


def test_install_hook_no_git_repo_raises(generator, tmp_path):
    with pytest.raises(RuntimeError, match="No .git/hooks directory"):
        generator.install_post_commit_hook(str(tmp_path))


def test_uninstall_hook_removes_codilay_lines(generator, git_repo):
    generator.install_post_commit_hook(str(git_repo))
    removed = generator.uninstall_post_commit_hook(str(git_repo))
    assert removed is True
    hook_path = os.path.join(str(git_repo), ".git", "hooks", "post-commit")
    with open(hook_path) as f:
        content = f.read()
    assert "codilay commit-doc" not in content


def test_uninstall_hook_preserves_other_lines(generator, git_repo):
    hook_path = os.path.join(str(git_repo), ".git", "hooks", "post-commit")
    with open(hook_path, "w") as f:
        f.write("#!/bin/bash\necho 'keep me'\n")
    os.chmod(hook_path, 0o755)
    generator.install_post_commit_hook(str(git_repo))
    generator.uninstall_post_commit_hook(str(git_repo))
    with open(hook_path) as f:
        content = f.read()
    assert "keep me" in content


def test_uninstall_hook_no_hook_file_returns_false(generator, git_repo):
    result = generator.uninstall_post_commit_hook(str(git_repo))
    assert result is False


def test_uninstall_hook_no_codilay_lines_returns_false(generator, git_repo):
    hook_path = os.path.join(str(git_repo), ".git", "hooks", "post-commit")
    with open(hook_path, "w") as f:
        f.write("#!/bin/bash\necho 'unrelated'\n")
    os.chmod(hook_path, 0o755)
    result = generator.uninstall_post_commit_hook(str(git_repo))
    assert result is False


# ── Metrics: _analyze_metrics() ───────────────────────────────────────────────


SAMPLE_METRICS = {
    "metrics": [
        {"name": "Code Quality", "score": 7, "note": "readable"},
        {"name": "Test Coverage", "score": 8, "note": "good coverage"},
        {"name": "Security", "score": 9, "note": "no issues"},
        {"name": "Complexity", "score": 6, "note": "minor increase"},
        {"name": "Documentation", "score": 5, "note": "sparse"},
    ],
    "reviewer_notes": ["Consider extracting helper", "Check frontend filters"],
}


@pytest.fixture
def metrics_llm(mock_llm):
    """LLM that returns explanation for json_mode=False and metrics for json_mode=True."""
    explanation = {"answer": "# abc1234 — 2024-03-14\n\n> feat\n\n## What changed\n\nSomething."}

    def side_effect(system_prompt, user_prompt, json_mode=True, **kwargs):
        if json_mode:
            return SAMPLE_METRICS
        return explanation

    mock_llm.call.side_effect = side_effect
    return mock_llm


@pytest.fixture
def metrics_generator(metrics_llm, temp_output_dir):
    return CommitDocGenerator(llm_client=metrics_llm, output_dir=temp_output_dir)


def test_analyze_metrics_returns_structured_dict(metrics_generator, git_repo):
    full_hash = metrics_generator.get_last_commit(str(git_repo))
    commit_info = metrics_generator._get_commit_info(full_hash, str(git_repo))
    result = metrics_generator._analyze_metrics(commit_info)
    assert result is not None
    assert "metrics" in result
    assert len(result["metrics"]) == 5
    assert "reviewer_notes" in result


def test_analyze_metrics_returns_none_on_bad_response(generator, git_repo):
    """If the LLM returns something without 'metrics', _analyze_metrics should return None."""
    generator.llm.call.return_value = {"answer": "not json"}
    full_hash = generator.get_last_commit(str(git_repo))
    commit_info = generator._get_commit_info(full_hash, str(git_repo))
    result = generator._analyze_metrics(commit_info)
    assert result is None


def test_analyze_metrics_uses_json_mode_true(metrics_generator, git_repo):
    full_hash = metrics_generator.get_last_commit(str(git_repo))
    commit_info = metrics_generator._get_commit_info(full_hash, str(git_repo))
    metrics_generator._analyze_metrics(commit_info)
    # Last call should have json_mode=True (the metrics call)
    last_call = metrics_generator.llm.call.call_args
    assert last_call[1].get("json_mode") is True


# ── Metrics: _format_metrics_markdown() ──────────────────────────────────────


def test_format_metrics_markdown_contains_embedded_json(generator):
    import json as _json

    result = generator._format_metrics_markdown(SAMPLE_METRICS)
    assert "<!-- codilay-metrics:" in result
    # Extract and validate the embedded JSON
    import re

    match = re.search(r"<!-- codilay-metrics: (\{.*?\}) -->", result)
    assert match is not None
    parsed = _json.loads(match.group(1))
    assert parsed["metrics"][0]["name"] == "Code Quality"


def test_format_metrics_markdown_contains_table(generator):
    result = generator._format_metrics_markdown(SAMPLE_METRICS)
    assert "| Code Quality |" in result
    assert "| Security |" in result
    assert "7/10" in result


def test_format_metrics_markdown_contains_reviewer_notes(generator):
    result = generator._format_metrics_markdown(SAMPLE_METRICS)
    assert "Reviewer Notes" in result
    assert "Consider extracting helper" in result
    assert "Check frontend filters" in result


def test_format_metrics_markdown_na_score(generator):
    data = {
        "metrics": [{"name": "Test Coverage", "score": -1, "note": "config only"}],
        "reviewer_notes": [],
    }
    result = generator._format_metrics_markdown(data)
    assert "N/A" in result


def test_format_metrics_markdown_no_reviewer_notes(generator):
    data = {"metrics": [{"name": "Security", "score": 9, "note": "clean"}], "reviewer_notes": []}
    result = generator._format_metrics_markdown(data)
    assert "Reviewer Notes" not in result


# ── Metrics: end-to-end generate() with include_metrics ──────────────────────


def test_generate_with_metrics_calls_llm_twice(metrics_generator, git_repo):
    """With include_metrics=True, LLM should be called once for explanation + once for metrics."""
    full_hash = metrics_generator.get_last_commit(str(git_repo))
    metrics_generator.generate(commit_hash=full_hash, repo_path=str(git_repo), include_metrics=True)
    assert metrics_generator.llm.call.call_count == 2


def test_generate_without_metrics_calls_llm_once(metrics_generator, git_repo):
    full_hash = metrics_generator.get_last_commit(str(git_repo))
    metrics_generator.generate(commit_hash=full_hash, repo_path=str(git_repo), include_metrics=False)
    assert metrics_generator.llm.call.call_count == 1


def test_generate_with_metrics_embeds_json_in_file(metrics_generator, git_repo):
    full_hash = metrics_generator.get_last_commit(str(git_repo))
    result = metrics_generator.generate(commit_hash=full_hash, repo_path=str(git_repo), include_metrics=True)
    with open(result["path"]) as f:
        content = f.read()
    assert "<!-- codilay-metrics:" in content


def test_generate_with_metrics_returns_metrics_key(metrics_generator, git_repo):
    full_hash = metrics_generator.get_last_commit(str(git_repo))
    result = metrics_generator.generate(commit_hash=full_hash, repo_path=str(git_repo), include_metrics=True)
    assert "metrics" in result
    assert result["metrics"]["metrics"][0]["name"] == "Code Quality"


def test_generate_without_metrics_no_metrics_key(metrics_generator, git_repo):
    full_hash = metrics_generator.get_last_commit(str(git_repo))
    result = metrics_generator.generate(commit_hash=full_hash, repo_path=str(git_repo), include_metrics=False)
    assert "metrics" not in result


def test_generate_with_metrics_failed_analysis_still_saves_doc(generator, git_repo):
    """If metrics LLM call fails, the explanation doc should still be saved."""
    call_count = 0

    def side_effect(system_prompt, user_prompt, json_mode=True, **kwargs):
        nonlocal call_count
        call_count += 1
        if json_mode:
            raise RuntimeError("LLM unavailable")
        return {"answer": "# abc — 2024-01-01\n\n> fix\n\n## What changed\n\nSomething."}

    generator.llm.call.side_effect = side_effect
    full_hash = generator.get_last_commit(str(git_repo))
    result = generator.generate(commit_hash=full_hash, repo_path=str(git_repo), include_metrics=True)
    assert os.path.exists(result["path"])
    assert "metrics" not in result


def test_generate_range_with_metrics(metrics_generator, git_repo):
    results = metrics_generator.generate_range("HEAD~1..HEAD", str(git_repo), include_metrics=True)
    assert len(results) == 1
    assert "metrics" in results[0]


# ── Frontmatter: _write_frontmatter / _read_doc_metadata ──────────────────────


def test_generate_doc_has_frontmatter(generator, git_repo):
    full_hash = generator.get_last_commit(str(git_repo))
    result = generator.generate(commit_hash=full_hash, repo_path=str(git_repo))
    assert result["content"].startswith("<!-- codilay-doc:")


def test_generate_frontmatter_completed_true(generator, git_repo):
    import json as _json
    import re as _re

    full_hash = generator.get_last_commit(str(git_repo))
    result = generator.generate(commit_hash=full_hash, repo_path=str(git_repo))
    match = _re.match(r"<!-- codilay-doc: (\{.*?\}) -->", result["content"])
    assert match is not None
    meta = _json.loads(match.group(1))
    assert meta["completed"] is True
    assert meta["has_metrics"] is False


def test_generate_frontmatter_has_metrics_true(metrics_generator, git_repo):
    import json as _json
    import re as _re

    full_hash = metrics_generator.get_last_commit(str(git_repo))
    result = metrics_generator.generate(commit_hash=full_hash, repo_path=str(git_repo), include_metrics=True)
    match = _re.match(r"<!-- codilay-doc: (\{.*?\}) -->", result["content"])
    assert match is not None
    meta = _json.loads(match.group(1))
    assert meta["has_metrics"] is True


def test_read_doc_metadata_no_file(generator, tmp_path):
    result = generator._read_doc_metadata("nonexistent")
    assert result is None


def test_read_doc_metadata_with_frontmatter(generator, git_repo):
    full_hash = generator.get_last_commit(str(git_repo))
    generator.generate(commit_hash=full_hash, repo_path=str(git_repo))
    meta = generator._read_doc_metadata(full_hash[:7])
    assert meta is not None
    assert meta["completed"] is True


def test_read_doc_metadata_legacy_doc(generator, tmp_path):
    """A doc without frontmatter (legacy) should return default metadata."""
    short_hash = "abc1234"
    path = generator.doc_path(short_hash)
    with open(path, "w") as f:
        f.write("# abc1234 — 2024-01-01\n\n> fix\n")
    meta = generator._read_doc_metadata(short_hash)
    assert meta is not None
    assert meta["completed"] is True
    assert meta["has_metrics"] is False
    assert meta.get("_legacy") is True


# ── _get_history() ────────────────────────────────────────────────────────────


def test_get_history_returns_all_commits(generator, git_repo):
    hashes = generator._get_history(str(git_repo))
    assert len(hashes) >= 2


def test_get_history_oldest_first(generator, git_repo):
    hashes = generator._get_history(str(git_repo))
    # We know the first commit was "init: add a.py" and second was "feat: add b.py"
    # Get messages for first and last
    msg_first = generator._run_git(["log", "-1", "--format=%s", hashes[0]], str(git_repo))
    msg_last = generator._run_git(["log", "-1", "--format=%s", hashes[-1]], str(git_repo))
    assert msg_first == "init: add a.py"
    assert msg_last == "feat: add b.py"


def test_get_history_excludes_merges_by_default(generator, git_repo):
    # All commits in our test repo are non-merges — just verify no crash
    hashes = generator._get_history(str(git_repo), include_merges=False)
    assert len(hashes) >= 1


def test_get_history_last_n(generator, git_repo):
    hashes = generator._get_history(str(git_repo), last_n=1)
    assert len(hashes) == 1
    # last_n=1 should return the most recent commit
    head = generator.get_last_commit(str(git_repo))
    assert hashes[0] == head


def test_get_history_from_ref_includes_from(generator, git_repo):
    """--from <first_commit> should include that commit in the result."""
    all_hashes = generator._get_history(str(git_repo))
    first = all_hashes[0]
    hashes = generator._get_history(str(git_repo), from_ref=first)
    assert first in hashes


def test_get_history_from_date(generator, git_repo):
    # A far future date should return nothing
    hashes = generator._get_history(str(git_repo), from_ref="2099-01-01")
    assert hashes == []


# ── estimate_backfill() ───────────────────────────────────────────────────────


def test_estimate_backfill_all_new(generator, git_repo):
    estimate = generator.estimate_backfill(str(git_repo))
    assert estimate["total"] >= 2
    assert estimate["already_documented"] == 0
    assert estimate["will_process"] == estimate["total"]
    assert estimate["estimated_cost"] > 0


def test_estimate_backfill_some_done(generator, git_repo):
    all_hashes = generator._get_history(str(git_repo))
    # Generate doc for the first commit
    generator.generate(commit_hash=all_hashes[0], repo_path=str(git_repo))
    estimate = generator.estimate_backfill(str(git_repo))
    assert estimate["already_documented"] == 1
    assert estimate["will_process"] == estimate["total"] - 1


def test_estimate_backfill_force_counts_all(generator, git_repo):
    all_hashes = generator._get_history(str(git_repo))
    generator.generate(commit_hash=all_hashes[0], repo_path=str(git_repo))
    estimate = generator.estimate_backfill(str(git_repo), force=True)
    assert estimate["will_process"] == estimate["total"]


def test_estimate_backfill_last_n(generator, git_repo):
    estimate = generator.estimate_backfill(str(git_repo), last_n=1)
    assert estimate["total"] == 1


# ── backfill() ────────────────────────────────────────────────────────────────


def test_backfill_processes_all_new_commits(generator, git_repo, temp_output_dir):
    summary = generator.backfill(repo_path=str(git_repo))
    total_commits = len(generator._get_history(str(git_repo)))
    assert len(summary["processed"]) == total_commits
    assert summary["skipped"] == 0
    assert summary["errors"] == []


def test_backfill_skips_already_documented(generator, git_repo):
    all_hashes = generator._get_history(str(git_repo))
    # Pre-generate all docs
    for h in all_hashes:
        generator.generate(commit_hash=h, repo_path=str(git_repo))
    summary = generator.backfill(repo_path=str(git_repo))
    assert len(summary["processed"]) == 0
    assert summary["skipped"] == len(all_hashes)


def test_backfill_force_reprocesses_all(generator, git_repo):
    all_hashes = generator._get_history(str(git_repo))
    for h in all_hashes:
        generator.generate(commit_hash=h, repo_path=str(git_repo))
    summary = generator.backfill(repo_path=str(git_repo), force=True)
    assert len(summary["processed"]) == len(all_hashes)
    assert summary["skipped"] == 0


def test_backfill_reprocesses_incomplete_docs(generator, git_repo):
    """A doc with completed=False should be re-processed even without --force."""
    all_hashes = generator._get_history(str(git_repo))
    first = all_hashes[0]
    short = first[:7]
    # Write a doc marked as incomplete
    path = generator.doc_path(short)
    with open(path, "w") as f:
        f.write('<!-- codilay-doc: {"completed":false,"has_metrics":false} -->\n# incomplete\n')
    summary = generator.backfill(repo_path=str(git_repo), last_n=1)
    assert len(summary["processed"]) == 1


def test_backfill_progress_callback_called(generator, git_repo):
    calls = []
    generator.backfill(repo_path=str(git_repo), progress_callback=lambda d, t, h, s: calls.append(s))
    assert len(calls) == len(generator._get_history(str(git_repo)))
    assert all(s in ("processed", "error") for s in calls)


def test_backfill_generates_index(generator, git_repo):
    summary = generator.backfill(repo_path=str(git_repo))
    assert summary["index_path"] is not None
    assert os.path.exists(summary["index_path"])


def test_backfill_with_last_n(generator, git_repo):
    summary = generator.backfill(repo_path=str(git_repo), last_n=1)
    assert len(summary["processed"]) == 1


# ── _run_metrics_only() ───────────────────────────────────────────────────────


def test_run_metrics_only_adds_metrics_to_existing_doc(metrics_generator, git_repo):
    full_hash = metrics_generator.get_last_commit(str(git_repo))
    # Generate without metrics
    metrics_generator.generate(commit_hash=full_hash, repo_path=str(git_repo), include_metrics=False)
    result = metrics_generator._run_metrics_only(full_hash, str(git_repo))
    assert result["metrics_added"] is True
    with open(result["path"]) as f:
        content = f.read()
    assert "<!-- codilay-metrics:" in content


def test_run_metrics_only_updates_frontmatter(metrics_generator, git_repo):
    import json as _json
    import re as _re

    full_hash = metrics_generator.get_last_commit(str(git_repo))
    metrics_generator.generate(commit_hash=full_hash, repo_path=str(git_repo), include_metrics=False)
    metrics_generator._run_metrics_only(full_hash, str(git_repo))
    meta = metrics_generator._read_doc_metadata(full_hash[:7])
    assert meta["has_metrics"] is True


def test_run_metrics_only_no_doc_raises(generator, git_repo):
    with pytest.raises(RuntimeError, match="No doc found"):
        generator._run_metrics_only("aaaaaaa" * 6, str(git_repo))


# ── generate_index() ──────────────────────────────────────────────────────────


def test_generate_index_creates_file(generator, git_repo):
    all_hashes = generator._get_history(str(git_repo))
    for h in all_hashes:
        generator.generate(commit_hash=h, repo_path=str(git_repo))
    index_path = generator.generate_index()
    assert os.path.exists(index_path)
    assert index_path.endswith("index.md")


def test_generate_index_contains_all_hashes(generator, git_repo):
    all_hashes = generator._get_history(str(git_repo))
    for h in all_hashes:
        generator.generate(commit_hash=h, repo_path=str(git_repo))
    index_path = generator.generate_index()
    with open(index_path) as f:
        content = f.read()
    for h in all_hashes:
        assert h[:7] in content


def test_generate_index_empty_docs_dir(generator):
    index_path = generator.generate_index()
    assert os.path.exists(index_path)
    with open(index_path) as f:
        content = f.read()
    assert "0 commits" in content
