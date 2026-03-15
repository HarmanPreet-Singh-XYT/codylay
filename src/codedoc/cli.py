"""
CodeDoc CLI — the main entry point with git-aware change tracking.

Usage:
    codedoc .
    codedoc /path/to/project
    codedoc . --provider openai --model gpt-4o
    codedoc status .
    codedoc clean .
"""

import sys
import os
import json
from datetime import datetime, timezone

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
)
from rich.table import Table
from rich import box

from codedoc.config import CodeDocConfig
from codedoc.scanner import Scanner
from codedoc.planner import Planner
from codedoc.processor import Processor
from codedoc.wire_manager import WireManager
from codedoc.docstore import DocStore
from codedoc.state import AgentState
from codedoc.llm_client import LLMClient
from codedoc.git_tracker import GitTracker, ChangeType
from codedoc.ui import UI

console = Console()


def common_options(fn):
    fn = click.option("--config", "-c", default=None, help="Path to codedoc.config.json")(fn)
    fn = click.option("--output", "-o", default=None, help="Output directory")(fn)
    fn = click.option("--model", "-m", default=None, help="LLM model override")(fn)
    fn = click.option(
        "--provider", "-p", default="anthropic",
        type=click.Choice(["anthropic", "openai"]), help="LLM provider",
    )(fn)
    fn = click.option("--verbose", "-v", is_flag=True, help="Verbose output")(fn)
    return fn


@click.group(invoke_without_command=True)
@click.argument("target", default=".", type=click.Path(exists=True))
@common_options
@click.pass_context
def cli(ctx, target, config, output, model, provider, verbose):
    """
    CodeDoc — AI Agent for Codebase Documentation.

    \b
    Examples:
        codedoc .                        Document current directory
        codedoc /path/to/project         Document a specific project
        codedoc . -p openai -m gpt-4o    Use OpenAI
        codedoc . -v                     Verbose mode
    """
    ctx.ensure_object(dict)
    ctx.obj["target"] = os.path.abspath(target)
    ctx.obj["config_path"] = config
    ctx.obj["output"] = output
    ctx.obj["model"] = model
    ctx.obj["provider"] = provider
    ctx.obj["verbose"] = verbose

    if ctx.invoked_subcommand is None:
        ctx.invoke(run)


@cli.command()
@click.pass_context
def run(ctx):
    """Run the documentation agent (default command)."""
    target = ctx.obj["target"]
    config_path = ctx.obj["config_path"]
    output_dir = ctx.obj["output"]
    model_override = ctx.obj["model"]
    provider = ctx.obj["provider"]
    verbose = ctx.obj["verbose"]

    ui = UI(console, verbose)
    ui.show_banner()

    # ── Load Config ──────────────────────────────────────────────
    cfg = CodeDocConfig.load(target, config_path)
    if model_override:
        cfg.llm_model = model_override
    cfg.llm_provider = provider
    ui.show_config(cfg)

    # ── Resolve paths ────────────────────────────────────────────
    if output_dir is None:
        output_dir = os.path.join(target, "output")

    state_path = os.path.join(output_dir, ".codedoc_state.json")
    codebase_md_path = os.path.join(output_dir, "CODEBASE.md")
    os.makedirs(output_dir, exist_ok=True)

    # ── Git setup ────────────────────────────────────────────────
    git = GitTracker(target)
    if git.is_git_repo:
        current_commit = git.get_current_commit()
        current_commit_short = git.get_current_commit_short()
        ui.info(f"Git repo detected — HEAD: [cyan]{current_commit_short}[/cyan]")
    else:
        current_commit = None
        current_commit_short = None
        ui.info("Not a git repo — using file-based change detection")

    # ── Check existing state ─────────────────────────────────────
    existing_state = None
    mode = "full"
    diff_result = None

    if os.path.exists(state_path) and os.path.exists(codebase_md_path):
        existing_state = AgentState.load(state_path)

        # Try git-based diff
        if git.is_git_repo and existing_state.last_commit:
            if git.is_commit_valid(existing_state.last_commit):
                diff_result = git.get_full_diff(existing_state.last_commit)

                if diff_result and diff_result.changes:
                    mode = ui.prompt_rerun_mode_git(diff_result)
                elif diff_result and not diff_result.changes:
                    ui.success(
                        "No changes since last documented commit "
                        f"([cyan]{existing_state.last_commit_short}[/cyan]). "
                        "Documentation is up to date!"
                    )
                    return
                else:
                    mode = ui.prompt_rerun_mode()
            else:
                ui.warn(
                    f"Last documented commit "
                    f"[cyan]{existing_state.last_commit_short}[/cyan] "
                    f"no longer exists (rebase/force push?)"
                )
                mode = ui.prompt_rerun_mode()
        else:
            mode = ui.prompt_rerun_mode()

        if mode == "quit":
            ui.info("Exiting.")
            return

        if mode == "full":
            bak = codebase_md_path + ".bak"
            if os.path.exists(codebase_md_path):
                os.rename(codebase_md_path, bak)
                ui.info(f"Archived existing doc → {bak}")
            existing_state = None

    # ── Init components ──────────────────────────────────────────
    llm = LLMClient(cfg)
    scanner = Scanner(target, cfg)
    wire_mgr = WireManager()
    docstore = DocStore()
    state = existing_state or AgentState(
        run_id=datetime.now(timezone.utc).isoformat()
    )

        # ── Phase 1: Bootstrap ───────────────────────────────────────
    ui.phase("Phase 1 · Bootstrap — Scanning codebase")

    with ui.spinner("Scanning files…"):
        file_tree_text = scanner.get_file_tree()
        all_files = scanner.get_all_files()
        md_contents = scanner.preload_md_files()

    ui.info(
        f"Found [bold]{len(all_files)}[/bold] files "
        f"({len(md_contents)} markdown preloaded)"
    )
    if verbose:
        ui.show_file_tree(file_tree_text)

    # ── Phase 1.5: Triage ────────────────────────────────────────
    # (only on full runs or when processing all files)

    files_to_process = all_files
    triage_result = None

    if mode == "full" and cfg.triage_mode != "none":
        ui.phase("Phase 1.5 · Triage — Classifying files to save tokens")

        from codedoc.triage import Triage

        triage = Triage(llm_client=llm, config=cfg)

        if cfg.triage_mode == "smart":
            with ui.spinner("LLM is classifying files (tree only, no content)…"):
                triage_result = triage.smart_triage(
                    file_tree_text, all_files, md_contents
                )
        else:
            with ui.spinner("Classifying files by pattern…"):
                triage_result = triage.fast_triage(all_files)

        # Apply force_include / force_skip from config
        if cfg.force_include:
            force_matched = []
            for pattern in cfg.force_include:
                force_matched.extend(
                    triage._expand_pattern(pattern, all_files)
                )
            if force_matched:
                triage_result.move_to_core(force_matched)
                ui.info(
                    f"Force-included {len(force_matched)} files from config"
                )

        if cfg.force_skip:
            force_matched = []
            for pattern in cfg.force_skip:
                force_matched.extend(
                    triage._expand_pattern(pattern, all_files)
                )
            if force_matched:
                triage_result.move_to_skip(force_matched)
                ui.info(
                    f"Force-skipped {len(force_matched)} files from config"
                )

        # Handle test files
        if not cfg.include_tests:
            test_files = [
                f for f in triage_result.core
                if any(
                    p in f.lower()
                    for p in [
                        "test", "spec", "__tests__", "_test.",
                        ".test.", ".spec.", "test_", "tests/",
                    ]
                )
            ]
            if test_files:
                triage_result.move_to_skip(test_files)
                ui.info(
                    f"Skipped {len(test_files)} test files "
                    f"(set triage.includeTests: true to include)"
                )

        # Estimate savings
        triage_result.token_estimate_saved = triage.estimate_tokens_saved(
            triage_result.skip, target
        )

        # Show results and get user confirmation
        ui.show_triage_result(triage_result, triage_result.project_type)

        # Show warnings from AI (borderline files)
        if triage_result.warnings:
            ui.show_triage_warnings(triage_result.warnings)

        review = ui.prompt_triage_review()

        if review == "quit":
            ui.info("Exiting.")
            return

        elif review == "edit":
            ui.prompt_triage_edit(triage_result)
            ui.show_triage_result(triage_result, triage_result.project_type)

        elif review == "skip_triage":
            ui.info("Skipping triage — all files will be processed")
            triage_result = None

        if triage_result:
            files_to_process = triage_result.files_to_process
            ui.success(
                f"Triage complete: [bold]{len(triage_result.core)}[/bold] core + "
                f"[bold]{len(triage_result.skim)}[/bold] skim = "
                f"[bold]{len(files_to_process)}[/bold] files to process "
                f"(skipping {len(triage_result.skip)})"
            )

    # ── Determine scope based on mode (for non-full runs) ────────

    if mode == "git_update" and diff_result:
        # Git-aware incremental — triage already happened on first run,
        # only process changed files
        ui.phase("Applying git changes to existing documentation")

        wire_mgr.load_state(state.open_wires, state.closed_wires)
        docstore.load_from_state(state.section_index, state.section_contents)

        rename_count = 0
        delete_count = 0

        for change in diff_result.renamed:
            old_path = change.old_path
            new_path = change.path
            ui.info(f"  Rename: {old_path} → [bold]{new_path}[/bold]")
            wire_mgr.handle_renamed_file(old_path, new_path)
            docstore.handle_renamed_file(old_path, new_path)
            if old_path in state.processed:
                state.processed.remove(old_path)
                state.processed.append(new_path)
            if old_path in state.parked:
                state.parked.remove(old_path)
                state.parked.append(new_path)
            rename_count += 1

        for change in diff_result.deleted:
            deleted_path = change.path
            ui.info(f"  Deleted: [red]{deleted_path}[/red]")
            wire_mgr.handle_deleted_file(deleted_path)
            docstore.handle_deleted_file(deleted_path)
            if deleted_path in state.processed:
                state.processed.remove(deleted_path)
            delete_count += 1

        files_to_process = diff_result.files_to_process
        valid_files = set(all_files)
        files_to_process = [f for f in files_to_process if f in valid_files]

        all_affected = diff_result.all_affected_paths
        wires_reopened = wire_mgr.reopen_wires_for_files(all_affected)
        invalidated = docstore.invalidate_sections_for_files(files_to_process)

        for parked_file in list(state.parked):
            wires_to_parked = wire_mgr.find_wires_to(parked_file)
            if any(w.get("from") in files_to_process for w in wires_to_parked):
                files_to_process.append(parked_file)
                state.parked.remove(parked_file)
                ui.info(f"  Unparked: {parked_file}")

        ui.show_git_changes_applied(
            renames=rename_count,
            deletes=delete_count,
            invalidated=len(invalidated),
            wires_reopened=wires_reopened,
        )

        if not files_to_process:
            ui.success("All changes were structural. Doc updated!")
            _finalize_and_write(
                state, wire_mgr, docstore, llm, cfg, ui,
                scanner, target, output_dir, codebase_md_path,
                state_path, git, current_commit, current_commit_short,
            )
            return

        ui.info(f"[bold]{len(files_to_process)}[/bold] files to re-process")

    elif mode == "update":
        wire_mgr.load_state(state.open_wires, state.closed_wires)
        docstore.load_from_state(state.section_index, state.section_contents)
        changed = scanner.get_changed_files(state.processed)
        if not changed:
            ui.success("No changed files. Documentation is up to date!")
            return
        files_to_process = changed
        wires_reopened = wire_mgr.reopen_wires_for_files(changed)
        ui.info(
            f"Detected {len(changed)} changed files, "
            f"re-opened {wires_reopened} wires"
        )

    elif mode == "specific":
        wire_mgr.load_state(state.open_wires, state.closed_wires)
        docstore.load_from_state(state.section_index, state.section_contents)
        specific = ui.prompt_specific_files(all_files)
        if not specific:
            ui.error("No valid files selected.")
            return
        files_to_process = specific
        wire_mgr.reopen_wires_for_files(specific)
        docstore.invalidate_sections_for_files(specific)

    # ── Phase 2: Planning ────────────────────────────────────────
    ui.phase("Phase 2 · Planning — Determining processing order")

    planner = Planner(llm, cfg)

    with ui.spinner("LLM is analysing the file structure…"):
        plan = planner.plan(file_tree_text, md_contents, files_to_process, state)

    state.queue = plan["order"]
    state.parked = plan.get("parked", []) if mode == "full" else state.parked
    state.park_reasons = plan.get("park_reasons", {}) if mode == "full" else state.park_reasons
    skeleton = plan["skeleton"]

    ui.show_plan(state.queue, state.parked, skeleton)

    if mode == "full":
        docstore.initialize_skeleton(
            skeleton.get("doc_title", "Codebase Reference"),
            skeleton.get("suggested_sections", []),
        )

    # ── Phase 3: Processing Loop ─────────────────────────────────
    ui.phase("Phase 3 · Processing — Reading files and building docs")

    processor = Processor(llm, cfg, wire_mgr, docstore, state, ui)

    total_files = len(state.queue)
    processed_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("({task.completed}/{task.total})"),
        console=console,
    ) as progress:
        task = progress.add_task("Processing files", total=total_files)

        while state.queue:
            file_path = state.queue.pop(0)
            full_path = os.path.join(target, file_path)

            if not os.path.exists(full_path):
                ui.warn(f"File not found, skipping: {file_path}")
                progress.advance(task)
                continue

            progress.update(task, description=f"Processing: {file_path}")

            try:
                content = scanner.read_file(full_path)
                if content is None:
                    ui.warn(f"Could not read (binary?): {file_path}")
                    progress.advance(task)
                    continue

                result = processor.process_file(file_path, content)

                # Track processed files (avoid duplicates on re-runs)
                if file_path not in state.processed:
                    state.processed.append(file_path)
                processed_count += 1

                # Store file hash for future diffing
                file_hash = scanner.get_file_hash(full_path)
                if file_hash:
                    state.file_hashes[file_path] = file_hash

                # Check for unparked files
                if result and result.get("unpark"):
                    for up in result["unpark"]:
                        if up in state.parked:
                            state.parked.remove(up)
                            state.queue.append(up)
                            total_files += 1
                            progress.update(task, total=total_files)
                            ui.info(f"  ↳ Unparked: {up}")

                # Save after every file (crash recovery)
                state.open_wires = wire_mgr.get_open_wires()
                state.closed_wires = wire_mgr.get_closed_wires()
                state.section_index = docstore.get_section_index()
                state.section_contents = docstore.get_section_contents()
                state.save(state_path)

            except Exception as e:
                ui.error(f"Error processing {file_path}: {e}")
                if verbose:
                    console.print_exception()

            progress.advance(task)

    # ── Phase 3b: Parked files ───────────────────────────────────
    if state.parked:
        ui.phase("Phase 3b · Processing parked files with available context")
        for parked_file in list(state.parked):
            full_path = os.path.join(target, parked_file)
            if not os.path.exists(full_path):
                continue
            content = scanner.read_file(full_path)
            if content is None:
                continue
            try:
                processor.process_file(parked_file, content)
                if parked_file not in state.processed:
                    state.processed.append(parked_file)
                state.parked.remove(parked_file)

                file_hash = scanner.get_file_hash(full_path)
                if file_hash:
                    state.file_hashes[parked_file] = file_hash

                state.open_wires = wire_mgr.get_open_wires()
                state.closed_wires = wire_mgr.get_closed_wires()
                state.section_index = docstore.get_section_index()
                state.section_contents = docstore.get_section_contents()
                state.save(state_path)
            except Exception as e:
                ui.warn(f"Could not process parked file {parked_file}: {e}")

    # ── Phase 4: Finalize ────────────────────────────────────────
    _finalize_and_write(
        state, wire_mgr, docstore, llm, cfg, ui,
        scanner, target, output_dir, codebase_md_path,
        state_path, git, current_commit, current_commit_short,
    )

    # Show LLM usage
    stats = llm.get_usage_stats()
    ui.info(
        f"LLM usage: {stats['total_calls']} calls, "
        f"{stats['total_input_tokens']:,} input tokens, "
        f"{stats['total_output_tokens']:,} output tokens"
    )


def _finalize_and_write(
    state, wire_mgr, docstore, llm, cfg, ui,
    scanner, target, output_dir, codebase_md_path,
    state_path, git, current_commit, current_commit_short,
):
    """Finalize documentation, write all output files, save state."""
    from codedoc.processor import Processor

    ui.phase("Phase 4 · Finalize — Assembling documentation")

    processor = Processor(llm, cfg, wire_mgr, docstore, state, ui)

    with ui.spinner("Running finalization pass…"):
        processor.finalize()

    open_wires = wire_mgr.get_open_wires()
    closed_wires = wire_mgr.get_closed_wires()

    # Remove stale dependency-graph / unresolved-references if they exist
    docstore.remove_section("dependency-graph")
    docstore.remove_section("unresolved-references")

    docstore.add_dependency_graph(closed_wires)
    docstore.add_unresolved_references(open_wires)

    # Write CODEBASE.md
    final_md = docstore.render_full_document()
    with open(codebase_md_path, "w", encoding="utf-8") as f:
        f.write(final_md)

    # Write links.json
    links_path = os.path.join(output_dir, "links.json")
    links_data = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "project": os.path.basename(target),
        "documented_commit": current_commit,
        "documented_commit_short": current_commit_short,
        "closed": closed_wires,
        "open": open_wires,
    }
    with open(links_path, "w", encoding="utf-8") as f:
        json.dump(links_data, f, indent=2)

    # ── Save final state with git info ───────────────────────────
    state.open_wires = open_wires
    state.closed_wires = closed_wires
    state.section_index = docstore.get_section_index()
    state.section_contents = docstore.get_section_contents()
    state.last_commit = current_commit
    state.last_commit_short = current_commit_short
    state.last_run = datetime.now(timezone.utc).isoformat()

    # Hash all currently processed files for fallback diffing
    for file_path in state.processed:
        full_path = os.path.join(target, file_path)
        file_hash = scanner.get_file_hash(full_path)
        if file_hash:
            state.file_hashes[file_path] = file_hash

    state.save(state_path)

    # ── Summary ──────────────────────────────────────────────────
    ui.show_summary(
        processed_count=len(state.processed),
        wires_closed=len(closed_wires),
        wires_open=len(open_wires),
        sections=len(docstore.get_section_index()),
        output_path=codebase_md_path,
        links_path=links_path,
    )

    if current_commit_short:
        ui.info(
            f"Documented at commit [cyan]{current_commit_short}[/cyan] — "
            f"next run will diff from here"
        )


# ─── Status command ───────────────────────────────────────────────────────────

@cli.command()
@click.argument("target", default=".", type=click.Path(exists=True))
def status(target):
    """Show current CodeDoc state for a project."""
    target = os.path.abspath(target)
    output_dir = os.path.join(target, "output")
    state_path = os.path.join(output_dir, ".codedoc_state.json")

    if not os.path.exists(state_path):
        console.print("[yellow]No CodeDoc state found for this project.[/yellow]")
        console.print(f"[dim]Looked in: {state_path}[/dim]")
        return

    state = AgentState.load(state_path)

    table = Table(title="CodeDoc Status", box=box.ROUNDED)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Run ID", state.run_id)
    table.add_row("Files processed", str(len(state.processed)))
    table.add_row("Open wires", str(len(state.open_wires)))
    table.add_row("Closed wires", str(len(state.closed_wires)))
    table.add_row("Documentation sections", str(len(state.section_index)))
    table.add_row("Queued (remaining)", str(len(state.queue)))
    table.add_row("Parked", str(len(state.parked)))

    # Git info
    if state.last_commit_short:
        table.add_row("Last documented commit", state.last_commit_short)
    if state.last_run:
        table.add_row("Last run", state.last_run)

    console.print(table)

    # Show git diff summary if available
    git = GitTracker(target)
    if git.is_git_repo and state.last_commit:
        if git.is_commit_valid(state.last_commit):
            diff_result = git.get_full_diff(state.last_commit)
            if diff_result and diff_result.changes:
                console.print(
                    f"\n[bold yellow]⚠ {len(diff_result.changes)} changes "
                    f"since last documented commit "
                    f"({diff_result.commits_behind} commits behind):[/bold yellow]"
                )
                for line in diff_result.summary_lines[:20]:
                    console.print(line)
                if len(diff_result.changes) > 20:
                    console.print(
                        f"  [dim]… +{len(diff_result.changes) - 20} more[/dim]"
                    )
                console.print(
                    "\n[dim]Run [bold]codedoc .[/bold] to update documentation.[/dim]"
                )
            elif diff_result:
                console.print(
                    "\n[green]✓ Documentation is up to date with HEAD.[/green]"
                )
        else:
            console.print(
                f"\n[yellow]⚠ Last documented commit "
                f"{state.last_commit_short} no longer exists "
                f"(rebase/force push?).[/yellow]"
            )

    if state.open_wires:
        console.print("\n[bold]Open wires:[/bold]")
        for w in state.open_wires[:15]:
            ctx = ""
            if "[DELETED]" in w.get("context", ""):
                ctx = " [red](file deleted)[/red]"
            console.print(
                f"  [yellow]→[/yellow] {w['from']} → {w['to']} "
                f"[dim]({w['type']})[/dim]{ctx}"
            )
        if len(state.open_wires) > 15:
            console.print(
                f"  [dim]  … +{len(state.open_wires) - 15} more[/dim]"
            )


# ─── Diff command (new — show what would change) ─────────────────────────────

@cli.command()
@click.argument("target", default=".", type=click.Path(exists=True))
def diff(target):
    """Show what has changed since the last CodeDoc run."""
    target = os.path.abspath(target)
    output_dir = os.path.join(target, "output")
    state_path = os.path.join(output_dir, ".codedoc_state.json")

    if not os.path.exists(state_path):
        console.print("[yellow]No previous CodeDoc run found.[/yellow]")
        console.print("[dim]Run [bold]codedoc .[/bold] first to create documentation.[/dim]")
        return

    state = AgentState.load(state_path)
    git = GitTracker(target)

    if not git.is_git_repo:
        console.print("[yellow]Not a git repository. Cannot compute diff.[/yellow]")
        return

    if not state.last_commit:
        console.print("[yellow]No commit recorded in state. Run a full documentation pass first.[/yellow]")
        return

    if not git.is_commit_valid(state.last_commit):
        console.print(
            f"[red]Last documented commit {state.last_commit_short} "
            f"no longer exists.[/red]"
        )
        console.print("[dim]This can happen after a rebase or force push.[/dim]")
        console.print("[dim]Run [bold]codedoc .[/bold] and choose 'Full re-run'.[/dim]")
        return

    diff_result = git.get_full_diff(state.last_commit)
    if not diff_result:
        console.print("[red]Could not compute diff.[/red]")
        return

    if not diff_result.changes:
        console.print(
            f"[green]✓ No changes since commit "
            f"{state.last_commit_short}. Documentation is current.[/green]"
        )
        return

    # Header
    console.print(
        Panel(
            f"[bold]Changes since last documentation run[/bold]\n"
            f"Base: [cyan]{diff_result.base_commit[:8]}[/cyan]  →  "
            f"HEAD: [cyan]{diff_result.head_commit[:8]}[/cyan]  "
            f"({diff_result.commits_behind} commits)",
            border_style="blue",
        )
    )

    # Changes table
    table = Table(box=box.SIMPLE, show_header=True)
    table.add_column("Status", style="bold", width=10)
    table.add_column("File")
    table.add_column("Impact", style="dim")

    for change in diff_result.changes:
        # Determine impact
        impact_parts = []
        was_processed = change.path in state.processed
        old_processed = change.old_path in state.processed if change.old_path else False

        if change.change_type == ChangeType.ADDED:
            status_str = "[green]added[/green]"
            impact_parts.append("new file — will be documented")

        elif change.change_type == ChangeType.MODIFIED:
            status_str = "[yellow]modified[/yellow]"
            if was_processed:
                # Count wires that would be affected
                affected_wires = [
                    w for w in state.closed_wires
                    if w.get("from") == change.path
                    or w.get("to") == change.path
                    or w.get("resolved_in") == change.path
                ]
                impact_parts.append(f"re-process, {len(affected_wires)} wires affected")
            else:
                impact_parts.append("not yet documented")

        elif change.change_type == ChangeType.DELETED:
            status_str = "[red]deleted[/red]"
            if was_processed:
                affected_wires = [
                    w for w in state.closed_wires
                    if w.get("resolved_in") == change.path
                ]
                impact_parts.append(f"section marked deleted, {len(affected_wires)} wires re-opened")
            else:
                impact_parts.append("was not documented")

        elif change.change_type == ChangeType.RENAMED:
            status_str = "[cyan]renamed[/cyan]"
            impact_parts.append(f"from {change.old_path}")
            if old_processed:
                impact_parts.append("paths updated + re-processed")

        else:
            status_str = change.change_type.value
            impact_parts.append("")

        file_display = change.path
        if change.change_type == ChangeType.RENAMED:
            file_display = f"{change.old_path} → {change.path}"

        table.add_row(status_str, file_display, ", ".join(impact_parts))

    console.print(table)

    # Summary
    console.print()
    console.print(
        f"  [bold]{len(diff_result.files_to_process)}[/bold] files to process, "
        f"[bold]{len(diff_result.deleted)}[/bold] deletions to handle"
    )
    console.print(
        "\n[dim]Run [bold]codedoc .[/bold] to update documentation.[/dim]"
    )

    # Show commits
    if diff_result.commit_messages:
        console.print(f"\n[bold]Commits ({diff_result.commits_behind}):[/bold]")
        for msg in diff_result.commit_messages[:15]:
            console.print(f"  [dim]{msg}[/dim]")
        if len(diff_result.commit_messages) > 15:
            extra = len(diff_result.commit_messages) - 15
            console.print(f"  [dim]… +{extra} more[/dim]")


# ─── Clean command ────────────────────────────────────────────────────────────

@cli.command()
@click.argument("target", default=".", type=click.Path(exists=True))
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def clean(target, yes):
    """Remove all CodeDoc generated files."""
    target = os.path.abspath(target)
    output_dir = os.path.join(target, "output")

    files_to_remove = []
    for fname in [
        ".codedoc_state.json",
        "CODEBASE.md",
        "CODEBASE.md.bak",
        "links.json",
    ]:
        path = os.path.join(output_dir, fname)
        if os.path.exists(path):
            files_to_remove.append((fname, path))

    if not files_to_remove:
        console.print("[yellow]Nothing to clean.[/yellow]")
        return

    console.print("[bold]Files to remove:[/bold]")
    for fname, path in files_to_remove:
        console.print(f"  [red]✗[/red] {path}")

    if not yes:
        confirm = click.confirm("\nProceed?", default=False)
        if not confirm:
            console.print("[dim]Cancelled.[/dim]")
            return

    for fname, path in files_to_remove:
        os.remove(path)

    console.print(f"[green]Removed {len(files_to_remove)} files.[/green]")


# ─── Init command ─────────────────────────────────────────────────────────────

@cli.command()
@click.argument("target", default=".", type=click.Path(exists=True))
def init(target):
    """Create a codedoc.config.json in the target directory."""
    target = os.path.abspath(target)
    config_path = os.path.join(target, "codedoc.config.json")

    if os.path.exists(config_path):
        console.print(f"[yellow]Config already exists: {config_path}[/yellow]")
        return

    default_config = {
        "ignore": [
            "**/*.test.*",
            "**/*.spec.*",
            "**/__tests__/**",
            "coverage/",
            "dist/",
            "build/",
        ],
        "notes": "",
        "instructions": "",
        "entryHint": "",
        "llm": {
            "model": "claude-sonnet-4-20250514",
            "maxTokensPerCall": 4096,
        },
    }

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(default_config, f, indent=2)

    console.print(f"[green]Created config:[/green] {config_path}")
    console.print("[dim]Edit it to customize CodeDoc behaviour for this project.[/dim]")