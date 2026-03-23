"""
Commit documentation generator for CodiLay.

Reads git diffs and generates plain-language explanations of commits,
optionally enriched with CODEBASE.md context and diff-level quality metrics.

Usage (via CLI):
    codilay commit-doc                      # last commit, no context
    codilay commit-doc abc123f              # specific commit
    codilay commit-doc --range main..HEAD   # all commits on a branch
    codilay commit-doc --context            # include CODEBASE.md sections
    codilay commit-doc --metrics            # append quality metrics analysis
"""

import json
import os
import re
import stat
import subprocess
from typing import Optional

COMMIT_DOC_SYSTEM_PROMPT = """\
You are a developer documentation assistant. You receive a git commit diff and write a
plain-language explanation for it.

Write a clear, factual commit document. Explain what changed and what each file's change actually
does — don't just restate the diff line-by-line. Write for a developer who is reading this commit
for the first time, weeks or months after it was made.

Format as GitHub-flavored markdown:

# <short hash> — <date>

> <commit message>

## What changed

<2-5 sentence plain-language summary of the overall change>

## Files

- `<file>` — <one sentence: what this file's change does>
- ...

Keep it concise. No fluff. If the diff is small, keep the doc short. If codebase context is
provided, use it to explain downstream effects or caller impacts — but don't pad the doc.
"""

METRICS_SYSTEM_PROMPT = """\
You are a code review assistant. You receive a git commit diff and analyze it across five
quality dimensions, scoring each 0–10.

Return a JSON object with exactly this shape:
{
  "metrics": [
    {"name": "Code Quality",   "score": <0-10>, "note": "<one sentence>"},
    {"name": "Test Coverage",  "score": <0-10 or -1 if N/A>, "note": "<one sentence>"},
    {"name": "Security",       "score": <0-10>, "note": "<one sentence>"},
    {"name": "Complexity",     "score": <0-10>, "note": "<one sentence>"},
    {"name": "Documentation",  "score": <0-10>, "note": "<one sentence>"}
  ],
  "reviewer_notes": ["<short actionable note>", ...]
}

Scoring guidance:
- Code Quality: readability, naming, visible code smells in changed lines. 10 = clean.
- Test Coverage: are tests added proportional to new logic? 10 = strong coverage,
  -1 = N/A (config/docs/build only changed).
- Security: red flags in the diff — hardcoded secrets, injection vectors, unsafe ops,
  new external deps. 10 = no concerns.
- Complexity: delta only. 10 = simplified or neutral. Lower = more branching, deeper nesting,
  longer functions added.
- Documentation: inline comments/docstrings for new logic. 10 = well documented.
  Lower = significant logic with no explanation.

reviewer_notes: 0–3 short, actionable notes for a code reviewer. Omit if nothing worth flagging.
Focus on non-obvious risks or follow-up tasks, not general style.

Return only valid JSON. No markdown fences, no explanation outside the JSON.
"""


class CommitDocGenerator:
    def __init__(self, llm_client, output_dir: str):
        self.llm = llm_client
        self.docs_dir = os.path.join(output_dir, "commit-docs")
        os.makedirs(self.docs_dir, exist_ok=True)

    # ── Git helpers ────────────────────────────────────────────────────────────

    def _run_git(self, args: list, cwd: str) -> str:
        """Run a git command and return stdout. Raises RuntimeError on failure."""
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout.strip()

    def get_last_commit(self, repo_path: str) -> str:
        """Return the full HEAD commit hash."""
        return self._run_git(["rev-parse", "HEAD"], repo_path)

    def get_commits_in_range(self, commit_range: str, repo_path: str) -> list:
        """Return full commit hashes in range, oldest first."""
        output = self._run_git(["log", "--format=%H", "--reverse", commit_range], repo_path)
        return [h for h in output.splitlines() if h]

    def _get_commit_info(self, commit_hash: str, repo_path: str) -> dict:
        """Collect diff, message, date, and changed files for a commit."""
        message = self._run_git(["log", "-1", "--format=%s", commit_hash], repo_path)
        date_str = self._run_git(["log", "-1", "--format=%ci", commit_hash], repo_path)
        files_output = self._run_git(["diff-tree", "--no-commit-id", "-r", "--name-only", commit_hash], repo_path)
        changed_files = [f for f in files_output.splitlines() if f]
        patch = self._run_git(["show", commit_hash], repo_path)

        return {
            "hash": commit_hash[:7],
            "full_hash": commit_hash,
            "message": message,
            "date": date_str[:10] if date_str else "",
            "changed_files": changed_files,
            "patch": patch,
        }

    # ── Context loading ────────────────────────────────────────────────────────

    def _load_codebase_context(self, changed_files: list, codebase_md_path: str) -> Optional[str]:
        """Extract sections from CODEBASE.md that mention the changed files."""
        if not os.path.exists(codebase_md_path):
            return None

        with open(codebase_md_path, "r", encoding="utf-8") as f:
            content = f.read()

        sections = re.split(r"\n(?=#{1,3} )", content)
        relevant = []
        seen = set()
        for section in sections:
            for file_path in changed_files:
                filename = os.path.basename(file_path)
                if (filename in section or file_path in section) and section not in seen:
                    relevant.append(section)
                    seen.add(section)
                    break
            if len(relevant) >= 5:
                break

        return "\n\n".join(relevant) if relevant else None

    # ── Prompt building ────────────────────────────────────────────────────────

    def _build_user_prompt(self, commit_info: dict, context: Optional[str]) -> str:
        patch = commit_info["patch"]
        max_patch_chars = 12000
        if len(patch) > max_patch_chars:
            patch = patch[:max_patch_chars] + "\n\n[diff truncated — showing first 12 000 chars]"

        prompt = (
            f"Commit: {commit_info['hash']} — {commit_info['date']}\n"
            f"Message: {commit_info['message']}\n\n"
            f"Diff:\n```\n{patch}\n```"
        )

        if context:
            prompt += f"\n\nCodebase context (relevant CODEBASE.md sections):\n{context}"

        return prompt

    # ── Metrics analysis ───────────────────────────────────────────────────────

    def _analyze_metrics(self, commit_info: dict) -> Optional[dict]:
        """Run a separate LLM call to score the diff across quality dimensions.

        Returns a dict with keys 'metrics' and 'reviewer_notes', or None on failure.
        """
        user_prompt = self._build_user_prompt(commit_info, context=None)
        try:
            response = self.llm.call(
                system_prompt=METRICS_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                json_mode=True,
            )
        except Exception:
            return None
        if not isinstance(response, dict) or "metrics" not in response:
            return None
        return response

    def _format_metrics_markdown(self, metrics_data: dict) -> str:
        """Convert a metrics dict into a markdown section with an embedded JSON comment.

        The HTML comment carries the structured data for the web UI to render
        visually (score bars, color coding). The markdown table is the plain-text
        fallback.
        """
        metrics = metrics_data.get("metrics", [])
        notes = metrics_data.get("reviewer_notes", [])

        score_labels = {
            10: "excellent",
            9: "excellent",
            8: "good",
            7: "good",
            6: "fair",
            5: "fair",
            4: "needs attention",
            3: "needs attention",
            2: "poor",
            1: "poor",
            0: "poor",
        }

        lines = ["\n---\n", "## Commit Metrics\n"]
        # Embed raw JSON for the web UI — invisible in normal markdown renders
        lines.append(f"<!-- codilay-metrics: {json.dumps(metrics_data, separators=(',', ':'))} -->\n")

        lines.append("| Metric | Score | Assessment |")
        lines.append("|--------|-------|------------|")
        for m in metrics:
            score = m.get("score", -1)
            name = m.get("name", "")
            note = m.get("note", "")
            if score == -1:
                score_str = "N/A"
            else:
                label = score_labels.get(score, "")
                score_str = f"{score}/10 — {label}" if label else f"{score}/10"
            lines.append(f"| {name} | {score_str} | {note} |")

        if notes:
            lines.append("\n**Reviewer Notes**")
            for note in notes:
                lines.append(f"- {note}")

        return "\n".join(lines)

    # ── Doc metadata (frontmatter) ────────────────────────────────────────────

    _META_MARKER = "codilay-doc"
    COST_PER_COMMIT = 0.01  # rough average; doubles when include_metrics=True

    def _write_frontmatter(self, completed: bool, has_metrics: bool) -> str:
        data = {"completed": completed, "has_metrics": has_metrics}
        return f"<!-- {self._META_MARKER}: {json.dumps(data, separators=(',', ':'))} -->\n"

    def _read_doc_metadata(self, short_hash: str) -> Optional[dict]:
        """Read the frontmatter comment from an existing doc.

        Returns None if the doc doesn't exist.
        Returns a dict (possibly with _legacy=True) if the doc exists without frontmatter.
        """
        path = self.doc_path(short_hash)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                first_line = f.readline()
            match = re.match(r"<!-- codilay-doc: (\{.*?\}) -->", first_line.strip())
            if match:
                return json.loads(match.group(1))
        except Exception:
            pass
        # Doc exists but no frontmatter — treat as complete, no metrics
        return {"completed": True, "has_metrics": False, "_legacy": True}

    # ── Output path ────────────────────────────────────────────────────────────

    def doc_path(self, short_hash: str) -> str:
        return os.path.join(self.docs_dir, f"{short_hash}.md")

    # ── Main API ───────────────────────────────────────────────────────────────

    def generate(
        self,
        commit_hash: str,
        repo_path: str,
        use_context: bool = False,
        codebase_md_path: Optional[str] = None,
        include_metrics: bool = False,
    ) -> dict:
        """Generate a commit doc for a single commit.

        Returns a dict with keys: hash, path, content, changed_files, metrics (if requested).
        """
        commit_info = self._get_commit_info(commit_hash, repo_path)

        context = None
        if use_context and codebase_md_path:
            context = self._load_codebase_context(commit_info["changed_files"], codebase_md_path)

        user_prompt = self._build_user_prompt(commit_info, context)
        response = self.llm.call(
            system_prompt=COMMIT_DOC_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            json_mode=False,
        )

        body = response.get("answer", "") if isinstance(response, dict) else str(response)

        metrics_data = None
        if include_metrics:
            metrics_data = self._analyze_metrics(commit_info)
            if metrics_data:
                body += self._format_metrics_markdown(metrics_data)

        content = self._write_frontmatter(completed=True, has_metrics=metrics_data is not None) + body

        out_path = self.doc_path(commit_info["hash"])
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)

        result = {
            "hash": commit_info["hash"],
            "path": out_path,
            "content": content,
            "changed_files": commit_info["changed_files"],
        }
        if metrics_data is not None:
            result["metrics"] = metrics_data
        return result

    def generate_range(
        self,
        commit_range: str,
        repo_path: str,
        use_context: bool = False,
        codebase_md_path: Optional[str] = None,
        include_metrics: bool = False,
    ) -> list:
        """Generate commit docs for all commits in a range. Returns list of result dicts."""
        hashes = self.get_commits_in_range(commit_range, repo_path)
        results = []
        for h in hashes:
            result = self.generate(
                h,
                repo_path,
                use_context=use_context,
                codebase_md_path=codebase_md_path,
                include_metrics=include_metrics,
            )
            results.append(result)
        return results

    # ── History retrieval ──────────────────────────────────────────────────────

    def _get_history(
        self,
        repo_path: str,
        from_ref: Optional[str] = None,
        to_ref: str = "HEAD",
        author: Optional[str] = None,
        path_filter: Optional[str] = None,
        include_merges: bool = False,
        last_n: Optional[int] = None,
    ) -> list:
        """Return commit hashes matching filters, oldest-first.

        from_ref can be a commit hash/tag or a YYYY-MM-DD date string.
        For hash ranges, from_ref itself is included.
        """
        args = ["log", "--format=%H"]
        if not include_merges:
            args.append("--no-merges")
        if author:
            args.extend(["--author", author])

        if last_n:
            args.extend(["-n", str(last_n), to_ref])
        elif from_ref and re.match(r"\d{4}-\d{2}-\d{2}", from_ref):
            args.extend(["--after", from_ref, to_ref])
        elif from_ref:
            # git range A..B excludes A — we add A back below
            args.append(f"{from_ref}..{to_ref}")
        else:
            args.append(to_ref)

        if path_filter:
            args.extend(["--", path_filter])

        output = self._run_git(args, repo_path)
        hashes = [h for h in output.splitlines() if h]
        hashes.reverse()  # git log is newest-first; we want oldest-first

        # For hash-based --from, the A..B range excludes A itself — prepend it
        if from_ref and not last_n and not re.match(r"\d{4}-\d{2}-\d{2}", from_ref):
            try:
                from_full = self._run_git(["rev-parse", from_ref], repo_path)
                if from_full not in hashes:
                    hashes.insert(0, from_full)
            except RuntimeError:
                pass

        return hashes

    # ── Backfill estimation ────────────────────────────────────────────────────

    def estimate_backfill(
        self,
        repo_path: str,
        from_ref: Optional[str] = None,
        to_ref: str = "HEAD",
        author: Optional[str] = None,
        path_filter: Optional[str] = None,
        include_merges: bool = False,
        last_n: Optional[int] = None,
        include_metrics: bool = False,
        force: bool = False,
    ) -> dict:
        """Return a cost/count preview without processing anything."""
        hashes = self._get_history(
            repo_path,
            from_ref=from_ref,
            to_ref=to_ref,
            author=author,
            path_filter=path_filter,
            include_merges=include_merges,
            last_n=last_n,
        )
        already_done = 0
        incomplete = 0
        to_process = 0
        for h in hashes:
            meta = self._read_doc_metadata(h[:7])
            if force or meta is None:
                to_process += 1
            elif not meta.get("completed", False):
                incomplete += 1
            else:
                already_done += 1

        will_process = to_process + incomplete
        cost_each = self.COST_PER_COMMIT * (2 if include_metrics else 1)
        return {
            "total": len(hashes),
            "already_documented": already_done,
            "incomplete": incomplete,
            "to_process": to_process,
            "will_process": will_process,
            "estimated_cost": round(will_process * cost_each, 2),
        }

    # ── Metrics-only pass ──────────────────────────────────────────────────────

    def _run_metrics_only(self, commit_hash: str, repo_path: str) -> dict:
        """Add metrics to an existing complete doc that has none. Rewrites in-place."""
        short = commit_hash[:7]
        path = self.doc_path(short)
        if not os.path.exists(path):
            raise RuntimeError(f"No doc found for {short}")
        with open(path, "r", encoding="utf-8") as f:
            existing = f.read()
        commit_info = self._get_commit_info(commit_hash, repo_path)
        metrics_data = self._analyze_metrics(commit_info)
        if not metrics_data:
            return {"hash": short, "path": path, "metrics_added": False}
        # Strip old frontmatter line if present, then re-prepend updated one
        body = re.sub(r"^<!-- codilay-doc:.*?-->\n", "", existing, flags=re.DOTALL)
        new_content = (
            self._write_frontmatter(completed=True, has_metrics=True)
            + body.rstrip("\n")
            + self._format_metrics_markdown(metrics_data)
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return {"hash": short, "path": path, "metrics_added": True, "metrics": metrics_data}

    # ── Backfill ───────────────────────────────────────────────────────────────

    def backfill(
        self,
        repo_path: str,
        from_ref: Optional[str] = None,
        to_ref: str = "HEAD",
        author: Optional[str] = None,
        path_filter: Optional[str] = None,
        include_merges: bool = False,
        last_n: Optional[int] = None,
        use_context: bool = False,
        codebase_md_path: Optional[str] = None,
        include_metrics: bool = False,
        force: bool = False,
        force_metrics: bool = False,
        workers: int = 4,
        progress_callback=None,
    ) -> dict:
        """Process historical commits, skipping already-complete docs.

        Skip logic per commit:
          - No doc: process
          - Doc exists, completed=False: re-process (was interrupted)
          - Doc exists, completed=True, has_metrics=False, force_metrics: metrics-only pass
          - Doc exists, completed=True: skip (unless force=True)

        progress_callback(done, total, short_hash, status) is called after each commit.
        Returns a summary dict: processed, metrics_only, skipped, errors, index_path.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        hashes = self._get_history(
            repo_path,
            from_ref=from_ref,
            to_ref=to_ref,
            author=author,
            path_filter=path_filter,
            include_merges=include_merges,
            last_n=last_n,
        )

        to_generate: list = []
        to_metrics: list = []
        n_skipped = 0

        for h in hashes:
            meta = self._read_doc_metadata(h[:7])
            if force or meta is None:
                to_generate.append(h)
            elif not meta.get("completed", False):
                to_generate.append(h)
            elif include_metrics and force_metrics and not meta.get("has_metrics", False):
                to_metrics.append(h)
            else:
                n_skipped += 1

        results: dict = {
            "total": len(hashes),
            "processed": [],
            "metrics_only": [],
            "skipped": n_skipped,
            "errors": [],
            "index_path": None,
        }
        total_jobs = len(to_generate) + len(to_metrics)
        done_count = [0]

        def _tick(short_hash: str, status: str) -> None:
            done_count[0] += 1
            if progress_callback:
                progress_callback(done_count[0], total_jobs, short_hash, status)

        def _do_generate(h: str):
            try:
                r = self.generate(
                    h,
                    repo_path,
                    use_context=use_context,
                    codebase_md_path=codebase_md_path,
                    include_metrics=include_metrics,
                )
                _tick(h[:7], "processed")
                return "processed", r
            except Exception as exc:
                _tick(h[:7], "error")
                return "error", {"hash": h[:7], "error": str(exc)}

        def _do_metrics(h: str):
            try:
                r = self._run_metrics_only(h, repo_path)
                _tick(h[:7], "metrics_only")
                return "metrics_only", r
            except Exception as exc:
                _tick(h[:7], "error")
                return "error", {"hash": h[:7], "error": str(exc)}

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(_do_generate, h) for h in to_generate] + [
                pool.submit(_do_metrics, h) for h in to_metrics
            ]
            for future in as_completed(futures):
                status, data = future.result()
                results[status if status in results else "errors"].append(data)

        results["index_path"] = self.generate_index()
        return results

    # ── Index ──────────────────────────────────────────────────────────────────

    def generate_index(self) -> str:
        """Write/overwrite commit-docs/index.md — a dated changelog of all docs."""
        from collections import defaultdict

        entries = []
        for fname in os.listdir(self.docs_dir):
            if fname == "index.md" or not fname.endswith(".md"):
                continue
            short_hash = fname[:-3]
            fpath = os.path.join(self.docs_dir, fname)
            date = ""
            message = ""
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                for line in lines:
                    s = line.strip()
                    if s.startswith("# ") and " — " in s:
                        parts = s.lstrip("# ").split(" — ", 1)
                        if len(parts) == 2:
                            date = parts[1]
                        break
                for line in lines:
                    s = line.strip()
                    if s.startswith(">"):
                        message = s.lstrip("> ").strip()
                        break
            except Exception:
                pass
            entries.append({"hash": short_hash, "date": date, "message": message, "filename": fname})

        by_date: dict = defaultdict(list)
        for e in entries:
            by_date[e["date"] or "Unknown"].append(e)

        count = len(entries)
        lines = [
            "# Commit Documentation Index\n",
            f"_CodiLay — {count} commit{'s' if count != 1 else ''} documented_\n",
        ]
        for date in sorted(by_date.keys(), reverse=True):
            lines.append(f"\n## {date}\n")
            for e in by_date[date]:
                msg = e["message"] or "(no message)"
                lines.append(f"- [{e['hash']}]({e['filename']}) — {msg}")

        index_path = os.path.join(self.docs_dir, "index.md")
        with open(index_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return index_path

    # ── Git hook management ────────────────────────────────────────────────────

    def install_post_commit_hook(self, repo_path: str) -> str:
        """Install a post-commit hook that silently generates a commit doc after each commit.

        If a post-commit hook already exists, the CodiLay lines are appended.
        Returns the hook file path.
        """
        hooks_dir = os.path.join(repo_path, ".git", "hooks")
        if not os.path.isdir(hooks_dir):
            raise RuntimeError(f"No .git/hooks directory at {repo_path} — is this a git repository?")

        hook_path = os.path.join(hooks_dir, "post-commit")

        codilay_block = (
            "\n# CodiLay: auto-generate commit doc\n"
            "_codilay_commit=$(git rev-parse HEAD)\n"
            'codilay commit-doc "$_codilay_commit" --silent &\n'
        )

        if os.path.exists(hook_path):
            with open(hook_path, "r", encoding="utf-8") as f:
                existing = f.read()
            if "codilay commit-doc" in existing:
                return hook_path  # already installed
            new_content = existing.rstrip("\n") + "\n" + codilay_block
        else:
            new_content = "#!/bin/bash\n" + codilay_block

        with open(hook_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        # Ensure the hook is executable
        current_mode = os.stat(hook_path).st_mode
        os.chmod(hook_path, current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        return hook_path

    def uninstall_post_commit_hook(self, repo_path: str) -> bool:
        """Remove CodiLay's lines from the post-commit hook.

        Returns True if anything was removed, False if hook wasn't found or had no CodiLay lines.
        """
        hook_path = os.path.join(repo_path, ".git", "hooks", "post-commit")
        if not os.path.exists(hook_path):
            return False

        with open(hook_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        codilay_markers = {"codilay commit-doc", "CodiLay: auto-generate commit doc", "_codilay_commit"}
        filtered = [line for line in lines if not any(marker in line for marker in codilay_markers)]

        if len(filtered) == len(lines):
            return False  # nothing to remove

        with open(hook_path, "w", encoding="utf-8") as f:
            f.writelines(filtered)

        return True
