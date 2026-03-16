#!/usr/bin/env python3
"""
CodiLay — AI Agent for Codebase Documentation
CLI entry point.
"""

import json
import os
import sys
from datetime import datetime, timezone

import click
from rich import box
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)
from rich.table import Table

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from codilay.config import CodiLayConfig
from codilay.docstore import DocStore
from codilay.llm_client import LLMClient
from codilay.planner import Planner
from codilay.processor import Processor
from codilay.scanner import Scanner
from codilay.state import AgentState
from codilay.ui import UI
from codilay.wire_manager import WireManager

console = Console()


@click.group(invoke_without_command=True)
@click.option("--target", "-t", default=".", help="Path to the codebase to document")
@click.option("--config", "-c", default=None, help="Path to codilay.config.json")
@click.option("--output", "-o", default=None, help="Output directory")
@click.option("--model", "-m", default=None, help="LLM model override")
@click.option(
    "--provider",
    "-p",
    default="anthropic",
    type=click.Choice(["anthropic", "openai"]),
    help="LLM provider",
)
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
@click.pass_context
def cli(ctx, target, config, output, model, provider, verbose):
    """CodiLay — AI Agent for Codebase Documentation"""
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
    """Run CodiLay on a codebase (default command)"""
    target = ctx.obj["target"]
    config_path = ctx.obj["config_path"]
    output_dir = ctx.obj["output"]
    model_override = ctx.obj["model"]
    provider = ctx.obj["provider"]
    verbose = ctx.obj["verbose"]

    ui = UI(console, verbose)
    ui.show_banner()

    # ── Load Config ──────────────────────────────────────────────────
    config = CodiLayConfig.load(target, config_path)
    if model_override:
        config.llm_model = model_override
    config.llm_provider = provider

    ui.show_config(config)

    # ── Check for existing state ─────────────────────────────────────
    if output_dir is None:
        output_dir = os.path.join(target, "output")

    state_path = os.path.join(output_dir, ".codilay_state.json")
    codebase_md_path = os.path.join(output_dir, "CODEBASE.md")

    existing_state = None
    mode = "full"

    if os.path.exists(state_path) and os.path.exists(codebase_md_path):
        mode = ui.prompt_rerun_mode()
        if mode == "quit":
            ui.info("Exiting.")
            return
        if mode in ("update", "specific"):
            existing_state = AgentState.load(state_path)
        if mode == "full":
            # Archive existing
            bak_path = codebase_md_path + ".bak"
            if os.path.exists(codebase_md_path):
                os.rename(codebase_md_path, bak_path)
                ui.info(f"Archived existing doc to {bak_path}")

    os.makedirs(output_dir, exist_ok=True)

    # ── Initialize Components ────────────────────────────────────────
    llm = LLMClient(config)
    scanner = Scanner(target, config, output_dir=output_dir)
    wire_mgr = WireManager()
    docstore = DocStore()
    state = existing_state or AgentState(run_id=datetime.now(timezone.utc).isoformat())

    # ── Phase 1: Bootstrap ───────────────────────────────────────────
    ui.phase("Phase 1: Bootstrap — Scanning codebase")

    with ui.spinner("Scanning files..."):
        file_tree_text = scanner.get_file_tree()
        all_files = scanner.get_all_files()
        md_contents = scanner.preload_md_files()

    ui.info(f"Found {len(all_files)} files ({len(md_contents)} markdown files preloaded)")

    if verbose:
        ui.show_file_tree(file_tree_text)

    # ── Determine files to process ───────────────────────────────────
    files_to_process = all_files

    if mode == "update":
        changed = scanner.get_changed_files(state.processed)
        if not changed:
            ui.success("No changed files detected. Documentation is up to date!")
            return
        files_to_process = changed
        ui.info(f"Detected {len(changed)} changed files")
        # Re-open wires related to changed files
        wire_mgr.load_state(state.open_wires, state.closed_wires)
        reopened = wire_mgr.reopen_wires_for_files(changed)
        ui.info(f"Re-opened {reopened} wires for changed files")
        docstore.load_from_state(state.section_index, state.section_contents)

    elif mode == "specific":
        specific = ui.prompt_specific_files(all_files)
        if not specific:
            ui.error("No valid files selected.")
            return
        files_to_process = specific
        wire_mgr.load_state(state.open_wires, state.closed_wires)
        wire_mgr.reopen_wires_for_files(specific)
        docstore.load_from_state(state.section_index, state.section_contents)

    # ── Phase 2: Planning ────────────────────────────────────────────
    ui.phase("Phase 2: Planning — Determining processing order")

    planner = Planner(llm, config)

    with ui.spinner("LLM is analyzing file structure..."):
        plan = planner.plan(file_tree_text, md_contents, files_to_process, state)

    state.queue = plan.get("order", files_to_process)
    state.parked = plan.get("parked", [])
    state.park_reasons = plan.get("park_reasons", {})
    skeleton = plan.get("skeleton", {})

    ui.show_plan(state.queue, state.parked, skeleton)

    # Initialize docstore with skeleton
    if mode == "full":
        docstore.initialize_skeleton(
            skeleton.get("doc_title", "Codebase Reference"),
            skeleton.get("suggested_sections", []),
        )

    # ── Phase 3: Processing Loop ─────────────────────────────────────
    ui.phase("Phase 3: Processing — Reading files and building documentation")

    processor = Processor(llm, config, wire_mgr, docstore, state, ui)

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
                    ui.warn(f"Could not read (binary?), skipping: {file_path}")
                    progress.advance(task)
                    continue

                result = processor.process_file(file_path, content)

                state.processed.append(file_path)
                processed_count += 1

                # Check for unparked files
                if result and result.get("unpark"):
                    for up in result["unpark"]:
                        if up in state.parked:
                            state.parked.remove(up)
                            state.queue.append(up)
                            total_files += 1
                            progress.update(task, total=total_files)
                            ui.info(f"  ↳ Unparked: {up}")

                # Save state after each file
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

    # ── Process remaining parked files ───────────────────────────────
    if state.parked:
        ui.phase("Phase 3b: Processing parked files with available context")
        for parked_file in list(state.parked):
            full_path = os.path.join(target, parked_file)
            if not os.path.exists(full_path):
                continue
            content = scanner.read_file(full_path)
            if content is None:
                continue
            try:
                processor.process_file(parked_file, content)
                state.processed.append(parked_file)
                state.parked.remove(parked_file)

                state.open_wires = wire_mgr.get_open_wires()
                state.closed_wires = wire_mgr.get_closed_wires()
                state.section_index = docstore.get_section_index()
                state.section_contents = docstore.get_section_contents()
                state.save(state_path)
            except Exception as e:
                ui.warn(f"Could not process parked file {parked_file}: {e}")

    # ── Phase 4: Finalize ────────────────────────────────────────────
    ui.phase("Phase 4: Finalize — Assembling documentation")

    with ui.spinner("Running finalization pass..."):
        processor.finalize()

    # Build final outputs
    open_wires = wire_mgr.get_open_wires()
    closed_wires = wire_mgr.get_closed_wires()

    # Add dependency graph section
    docstore.add_dependency_graph(closed_wires)

    # Add unresolved references section
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
        "closed": closed_wires,
        "open": open_wires,
    }
    with open(links_path, "w", encoding="utf-8") as f:
        json.dump(links_data, f, indent=2)

    # Final state save
    state.open_wires = open_wires
    state.closed_wires = closed_wires
    state.section_index = docstore.get_section_index()
    state.section_contents = docstore.get_section_contents()
    state.save(state_path)

    # ── Summary ──────────────────────────────────────────────────────
    ui.show_summary(
        processed_count=len(state.processed),
        wires_closed=len(closed_wires),
        wires_open=len(open_wires),
        sections=len(docstore.get_section_index()),
        output_path=codebase_md_path,
        links_path=links_path,
    )


@cli.command()
@click.option("--target", "-t", default=".", help="Path to the codebase")
def status(target):
    """Show current CodiLay state for a project"""
    console = Console()
    output_dir = os.path.join(os.path.abspath(target), "output")
    state_path = os.path.join(output_dir, ".codilay_state.json")

    if not os.path.exists(state_path):
        console.print("[yellow]No CodiLay state found for this project.[/yellow]")
        return

    state = AgentState.load(state_path)

    table = Table(title="CodiLay Status", box=box.ROUNDED)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Run ID", state.run_id)
    table.add_row("Files processed", str(len(state.processed)))
    table.add_row("Open wires", str(len(state.open_wires)))
    table.add_row("Closed wires", str(len(state.closed_wires)))
    table.add_row("Sections", str(len(state.section_index)))
    table.add_row("Queued", str(len(state.queue)))
    table.add_row("Parked", str(len(state.parked)))

    console.print(table)


@cli.command()
@click.option("--target", "-t", default=".", help="Path to the codebase")
def clean(target):
    """Remove all CodiLay generated files"""
    output_dir = os.path.join(os.path.abspath(target), "output")
    removed = []

    for fname in [
        ".codilay_state.json",
        "CODEBASE.md",
        "CODEBASE.md.bak",
        "links.json",
    ]:
        path = os.path.join(output_dir, fname)
        if os.path.exists(path):
            os.remove(path)
            removed.append(fname)

    if removed:
        console.print(f"[green]Removed: {', '.join(removed)}[/green]")
    else:
        console.print("[yellow]Nothing to clean.[/yellow]")


if __name__ == "__main__":
    cli()
