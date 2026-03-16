import os
import tempfile
import shutil
import threading

import pytest
from unittest.mock import MagicMock

from codilay.parallel_orchestrator import (
    ParallelOrchestrator,
    WorkerResult,
    TierResult,
    ParkEntry,
)
from codilay.state import AgentState
from codilay.wire_bus import WireBus
from codilay.wire_manager import WireManager


@pytest.fixture
def mock_deps():
    """Build mocked dependencies for the ParallelOrchestrator."""
    # Processor mock — returns a result dict for every file
    processor = MagicMock()
    processor.process_file.return_value = {
        "section_id": "test_section",
        "wires_opened": [],
        "wires_closed": [],
    }

    # Real WireBus wrapping a real WireManager
    wire_mgr = WireManager()
    wire_bus = WireBus(wire_mgr)

    # DocStore mock
    docstore = MagicMock()
    docstore.get_section_index.return_value = {}
    docstore.get_section_contents.return_value = {}

    # Real AgentState
    state = AgentState(run_id="test-parallel")

    # Scanner mock
    scanner = MagicMock()
    scanner.get_file_hash.return_value = "abc123"

    # UI mock
    ui = MagicMock()

    return processor, wire_bus, docstore, state, scanner, ui


def _make_orchestrator(mock_deps, tmpdir, max_workers=2):
    """Create an orchestrator with real temp files."""
    processor, wire_bus, docstore, state, scanner, ui = mock_deps
    return ParallelOrchestrator(
        processor=processor,
        wire_bus=wire_bus,
        docstore=docstore,
        state=state,
        scanner=scanner,
        target_path=tmpdir,
        ui=ui,
        max_workers=max_workers,
    )


# ── WorkerResult and TierResult dataclasses ──────────────────────────────────


def test_worker_result_defaults():
    wr = WorkerResult(file_path="a.py", success=True)

    assert wr.file_path == "a.py"
    assert wr.success is True
    assert wr.result is None
    assert wr.error is None
    assert wr.processing_time == 0.0
    assert wr.confidence == "parallel"
    assert wr.wires_opened == 0
    assert wr.wires_closed == 0


def test_tier_result_defaults():
    tr = TierResult(tier_index=0, files=["a.py", "b.py"])

    assert tr.tier_index == 0
    assert tr.files == ["a.py", "b.py"]
    assert tr.results == []
    assert tr.total_time == 0.0
    assert tr.parallelism_achieved == 0


def test_park_entry_defaults():
    pe = ParkEntry(file_path="a.py", reason="waiting on dependency")

    assert pe.file_path == "a.py"
    assert pe.reason == "waiting on dependency"
    assert pe.waiting_on is None
    assert pe.state == "parked"


# ── Orchestrator initialization ──────────────────────────────────────────────


def test_orchestrator_init(mock_deps):
    tmpdir = tempfile.mkdtemp()
    try:
        orch = _make_orchestrator(mock_deps, tmpdir, max_workers=3)

        assert orch.max_workers == 3
        assert orch._stats["total_files"] == 0
        assert orch._stats["parallel_files"] == 0
        assert orch._stats["sequential_files"] == 0
    finally:
        orch.cleanup()
        shutil.rmtree(tmpdir)


# ── Sequential tier processing ───────────────────────────────────────────────


def test_process_single_file_success(mock_deps):
    tmpdir = tempfile.mkdtemp()
    try:
        # Create a real file so the orchestrator doesn't fail on path check
        os.makedirs(os.path.join(tmpdir, "src"), exist_ok=True)
        with open(os.path.join(tmpdir, "src", "a.py"), "w") as f:
            f.write("print('hello')")

        orch = _make_orchestrator(mock_deps, tmpdir)
        result = orch._process_single_file("src/a.py", "print('hello')", "sequential")

        assert result.success is True
        assert result.file_path == "src/a.py"
        assert result.confidence == "sequential"
        assert result.processing_time > 0

        # File should be marked as processed in state
        processor, wire_bus, docstore, state, scanner, ui = mock_deps
        assert "src/a.py" in state.processed
    finally:
        orch.cleanup()
        shutil.rmtree(tmpdir)


def test_process_single_file_not_found(mock_deps):
    tmpdir = tempfile.mkdtemp()
    try:
        orch = _make_orchestrator(mock_deps, tmpdir)
        result = orch._process_single_file("src/nonexistent.py", "content", "sequential")

        assert result.success is False
        assert "not found" in result.error.lower()
    finally:
        orch.cleanup()
        shutil.rmtree(tmpdir)


def test_process_single_file_processor_returns_none(mock_deps):
    tmpdir = tempfile.mkdtemp()
    try:
        processor, wire_bus, docstore, state, scanner, ui = mock_deps
        processor.process_file.return_value = None

        os.makedirs(os.path.join(tmpdir, "src"), exist_ok=True)
        with open(os.path.join(tmpdir, "src", "a.py"), "w") as f:
            f.write("content")

        orch = _make_orchestrator(mock_deps, tmpdir)
        result = orch._process_single_file("src/a.py", "content", "parallel")

        assert result.success is False
        assert "None" in result.error
    finally:
        orch.cleanup()
        shutil.rmtree(tmpdir)


def test_process_single_file_processor_raises(mock_deps):
    tmpdir = tempfile.mkdtemp()
    try:
        processor, wire_bus, docstore, state, scanner, ui = mock_deps
        processor.process_file.side_effect = RuntimeError("LLM failure")

        os.makedirs(os.path.join(tmpdir, "src"), exist_ok=True)
        with open(os.path.join(tmpdir, "src", "a.py"), "w") as f:
            f.write("content")

        orch = _make_orchestrator(mock_deps, tmpdir)
        result = orch._process_single_file("src/a.py", "content", "parallel")

        assert result.success is False
        assert "LLM failure" in result.error
    finally:
        orch.cleanup()
        shutil.rmtree(tmpdir)


# ── Tier-based processing ───────────────────────────────────────────────────


def test_process_tier_sequential(mock_deps):
    tmpdir = tempfile.mkdtemp()
    try:
        processor, wire_bus, docstore, state, scanner, ui = mock_deps

        # Create real files
        for name in ["a.py", "b.py"]:
            with open(os.path.join(tmpdir, name), "w") as f:
                f.write(f"# {name}")

        orch = _make_orchestrator(mock_deps, tmpdir)
        file_contents = {"a.py": "# a.py", "b.py": "# b.py"}

        tier_result = orch._process_tier_sequential(0, ["a.py", "b.py"], file_contents)

        assert tier_result.tier_index == 0
        assert len(tier_result.results) == 2
        assert tier_result.total_time > 0
        assert all(r.success for r in tier_result.results)
    finally:
        orch.cleanup()
        shutil.rmtree(tmpdir)


def test_process_tier_parallel(mock_deps):
    tmpdir = tempfile.mkdtemp()
    try:
        processor, wire_bus, docstore, state, scanner, ui = mock_deps

        # Create real files
        for name in ["a.py", "b.py", "c.py"]:
            with open(os.path.join(tmpdir, name), "w") as f:
                f.write(f"# {name}")

        orch = _make_orchestrator(mock_deps, tmpdir, max_workers=3)
        file_contents = {"a.py": "# a.py", "b.py": "# b.py", "c.py": "# c.py"}

        tier_result = orch._process_tier_parallel(0, ["a.py", "b.py", "c.py"], file_contents, 3)

        assert tier_result.tier_index == 0
        assert len(tier_result.results) == 3
        assert tier_result.parallelism_achieved == 3
        assert tier_result.total_time > 0
    finally:
        orch.cleanup()
        shutil.rmtree(tmpdir)


def test_process_tier_parallel_marks_in_flight(mock_deps):
    """Parallel tier processing should mark files as in-flight then completed."""
    tmpdir = tempfile.mkdtemp()
    try:
        processor, wire_bus, docstore, state, scanner, ui = mock_deps

        for name in ["a.py", "b.py"]:
            with open(os.path.join(tmpdir, name), "w") as f:
                f.write(f"# {name}")

        orch = _make_orchestrator(mock_deps, tmpdir, max_workers=2)
        file_contents = {"a.py": "# a.py", "b.py": "# b.py"}

        orch._process_tier_parallel(0, ["a.py", "b.py"], file_contents, 2)

        # After processing completes, nothing should be in-flight
        assert len(wire_bus.get_in_flight()) == 0
    finally:
        orch.cleanup()
        shutil.rmtree(tmpdir)


# ── Full process_all flow ────────────────────────────────────────────────────


def test_process_all_independent_files(mock_deps):
    """Independent files should all end up in tier 0."""
    tmpdir = tempfile.mkdtemp()
    try:
        processor, wire_bus, docstore, state, scanner, ui = mock_deps

        files = ["a.py", "b.py", "c.py"]
        for name in files:
            with open(os.path.join(tmpdir, name), "w") as f:
                f.write(f"# {name}")

        file_contents = {name: f"# {name}" for name in files}

        orch = _make_orchestrator(mock_deps, tmpdir, max_workers=3)
        result = orch.process_all(files, file_contents)

        assert result["stats"]["total_files"] == 3
        assert result["dep_graph_stats"]["num_tiers"] == 1
        assert processor.process_file.call_count == 3
    finally:
        orch.cleanup()
        shutil.rmtree(tmpdir)


def test_process_all_with_deps(mock_deps):
    """Files with dependencies should be split across tiers."""
    tmpdir = tempfile.mkdtemp()
    try:
        processor, wire_bus, docstore, state, scanner, ui = mock_deps

        files = ["a.py", "b.py"]
        for name in files:
            with open(os.path.join(tmpdir, name), "w") as f:
                f.write(f"# {name}")

        # a.py depends on b.py
        file_contents = {
            "a.py": "from b import something",
            "b.py": "# base module",
        }

        orch = _make_orchestrator(mock_deps, tmpdir, max_workers=2)
        result = orch.process_all(files, file_contents)

        assert result["stats"]["total_files"] == 2
        # Should have 2 tiers: b.py first, then a.py
        assert result["dep_graph_stats"]["num_tiers"] == 2
    finally:
        orch.cleanup()
        shutil.rmtree(tmpdir)


def test_process_all_progress_callback(mock_deps):
    """Progress callback should be called for each file."""
    tmpdir = tempfile.mkdtemp()
    try:
        processor, wire_bus, docstore, state, scanner, ui = mock_deps

        files = ["a.py", "b.py"]
        for name in files:
            with open(os.path.join(tmpdir, name), "w") as f:
                f.write(f"# {name}")

        file_contents = {"a.py": "# a", "b.py": "# b"}
        progress_calls = []

        def progress_cb(file_path, completed, total):
            progress_calls.append((file_path, completed, total))

        orch = _make_orchestrator(mock_deps, tmpdir)
        orch.process_all(files, file_contents, progress_callback=progress_cb)

        assert len(progress_calls) == 2
        # Last call should show 2/2
        assert progress_calls[-1][1] == 2
        assert progress_calls[-1][2] == 2
    finally:
        orch.cleanup()
        shutil.rmtree(tmpdir)


# ── Tier boundary sync ──────────────────────────────────────────────────────


def test_sync_tier_boundary(mock_deps):
    """Tier boundary should reconcile wire state and clean queue."""
    tmpdir = tempfile.mkdtemp()
    try:
        processor, wire_bus, docstore, state, scanner, ui = mock_deps

        state.queue = ["a.py", "b.py", "c.py"]
        state.processed = ["a.py"]

        docstore.get_section_index.return_value = {"sec_a": {"file": "a.py"}}
        docstore.get_section_contents.return_value = {"sec_a": "content of a"}

        orch = _make_orchestrator(mock_deps, tmpdir)
        orch._sync_tier_boundary(0)

        # a.py should be removed from queue
        assert "a.py" not in state.queue
        assert "b.py" in state.queue
        assert "c.py" in state.queue

        # State should have synced section data
        assert state.section_index == {"sec_a": {"file": "a.py"}}
        assert state.section_contents == {"sec_a": "content of a"}
    finally:
        orch.cleanup()
        shutil.rmtree(tmpdir)


# ── Checkpoint saving ───────────────────────────────────────────────────────


def test_save_checkpoint(mock_deps):
    tmpdir = tempfile.mkdtemp()
    try:
        processor, wire_bus, docstore, state, scanner, ui = mock_deps

        state.processed = ["a.py"]
        docstore.get_section_index.return_value = {}
        docstore.get_section_contents.return_value = {}

        orch = _make_orchestrator(mock_deps, tmpdir)

        checkpoint_path = os.path.join(tmpdir, "checkpoint.json")
        orch.save_checkpoint(checkpoint_path)

        assert os.path.exists(checkpoint_path)

        # Load and verify
        loaded = AgentState.load(checkpoint_path)
        assert loaded.processed == ["a.py"]
    finally:
        orch.cleanup()
        shutil.rmtree(tmpdir)


# ── Park management ─────────────────────────────────────────────────────────


def test_handle_unpark(mock_deps):
    tmpdir = tempfile.mkdtemp()
    try:
        processor, wire_bus, docstore, state, scanner, ui = mock_deps
        state.parked = ["parked_file.py"]

        orch = _make_orchestrator(mock_deps, tmpdir)
        orch._handle_unpark("parked_file.py")

        assert "parked_file.py" not in state.parked
        assert orch._park_entries["parked_file.py"].state == "unparked"
        assert orch._stats["unparked_count"] == 1
    finally:
        orch.cleanup()
        shutil.rmtree(tmpdir)


def test_collect_unparked(mock_deps):
    tmpdir = tempfile.mkdtemp()
    try:
        processor, wire_bus, docstore, state, scanner, ui = mock_deps

        orch = _make_orchestrator(mock_deps, tmpdir)

        # Manually add some unparked entries
        orch._park_entries["a.py"] = ParkEntry(file_path="a.py", reason="test", state="unparked")
        orch._park_entries["b.py"] = ParkEntry(file_path="b.py", reason="test", state="parked")
        orch._park_entries["c.py"] = ParkEntry(file_path="c.py", reason="test", state="unparked")

        unparked = orch._collect_unparked()

        assert set(unparked) == {"a.py", "c.py"}
        # After collection, entries should be marked pending
        assert orch._park_entries["a.py"].state == "pending"
        assert orch._park_entries["c.py"].state == "pending"
        # b.py still parked
        assert orch._park_entries["b.py"].state == "parked"
    finally:
        orch.cleanup()
        shutil.rmtree(tmpdir)


def test_collect_unparked_empty(mock_deps):
    tmpdir = tempfile.mkdtemp()
    try:
        orch = _make_orchestrator(mock_deps, tmpdir)
        unparked = orch._collect_unparked()
        assert unparked == []
    finally:
        orch.cleanup()
        shutil.rmtree(tmpdir)


# ── Wire event auto-unparking ───────────────────────────────────────────────


def test_wire_close_event_unparks_waiting_file(mock_deps):
    tmpdir = tempfile.mkdtemp()
    try:
        processor, wire_bus, docstore, state, scanner, ui = mock_deps

        orch = _make_orchestrator(mock_deps, tmpdir)

        # Park a file waiting on "src/dep.py"
        orch._park_entries["waiting.py"] = ParkEntry(
            file_path="waiting.py",
            reason="waiting for dependency",
            waiting_on="src/dep.py",
            state="parked",
        )

        # Simulate a wire close event from src/dep.py
        wire_bus.open_wire("other.py", "src/dep.py", "import")
        wire_bus.close_wire("wire_000", "src/dep.py")

        # The wire close event handler should have unparked the waiting file
        assert orch._park_entries["waiting.py"].state == "unparked"
    finally:
        orch.cleanup()
        shutil.rmtree(tmpdir)


def test_wire_open_event_does_not_unpark(mock_deps):
    tmpdir = tempfile.mkdtemp()
    try:
        processor, wire_bus, docstore, state, scanner, ui = mock_deps

        orch = _make_orchestrator(mock_deps, tmpdir)

        orch._park_entries["waiting.py"] = ParkEntry(
            file_path="waiting.py",
            reason="waiting",
            waiting_on="src/dep.py",
            state="parked",
        )

        # Open event should not unpark
        wire_bus.open_wire("other.py", "src/dep.py", "import")

        assert orch._park_entries["waiting.py"].state == "parked"
    finally:
        orch.cleanup()
        shutil.rmtree(tmpdir)


# ── Statistics ───────────────────────────────────────────────────────────────


def test_get_stats(mock_deps):
    tmpdir = tempfile.mkdtemp()
    try:
        orch = _make_orchestrator(mock_deps, tmpdir)
        stats = orch.get_stats()

        assert "total_files" in stats
        assert "parallel_files" in stats
        assert "sequential_files" in stats
        assert "tier_count" in stats
        assert "max_parallelism" in stats
        assert "total_time" in stats
        assert "unparked_count" in stats
    finally:
        orch.cleanup()
        shutil.rmtree(tmpdir)


# ── Cleanup ──────────────────────────────────────────────────────────────────


def test_cleanup_unsubscribes(mock_deps):
    tmpdir = tempfile.mkdtemp()
    try:
        processor, wire_bus, docstore, state, scanner, ui = mock_deps

        orch = _make_orchestrator(mock_deps, tmpdir)

        # Before cleanup, the orchestrator's event handler is subscribed
        initial_sub_count = len(wire_bus._subscribers)
        assert initial_sub_count > 0

        orch.cleanup()

        assert len(wire_bus._subscribers) == initial_sub_count - 1
    finally:
        shutil.rmtree(tmpdir)


# ── Get parallel sections ───────────────────────────────────────────────────


def test_get_parallel_sections(mock_deps):
    tmpdir = tempfile.mkdtemp()
    try:
        processor, wire_bus, docstore, state, scanner, ui = mock_deps
        docstore.get_section_index.return_value = {
            "sec_a": {"file": "a.py"},
            "sec_b": {"file": "b.py"},
        }

        orch = _make_orchestrator(mock_deps, tmpdir)
        sections = orch.get_parallel_sections()

        assert set(sections) == {"sec_a", "sec_b"}
    finally:
        orch.cleanup()
        shutil.rmtree(tmpdir)
