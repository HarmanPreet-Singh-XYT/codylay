import threading
import time

import pytest

from codilay.wire_bus import WireBus, WireEvent
from codilay.wire_manager import WireManager


@pytest.fixture
def bus():
    wm = WireManager()
    return WireBus(wm)


# ── Basic wire operations (thread-safe wrappers) ────────────────────────────


def test_open_wire(bus):
    wire = bus.open_wire("src/a.py", "src/b.py", "import", "Importing B")

    assert wire["id"] == "wire_000"
    assert wire["from"] == "src/a.py"
    assert wire["to"] == "src/b.py"
    assert wire["type"] == "import"


def test_close_wire(bus):
    bus.open_wire("src/a.py", "src/b.py", "import")
    closed = bus.close_wire("wire_000", "src/b.py", "Resolved")

    assert closed is not None
    assert closed["id"] == "wire_000"
    assert len(bus.get_open_wires()) == 0
    assert len(bus.get_closed_wires()) == 1


def test_close_wires_by_ids(bus):
    bus.open_wire("src/a.py", "src/b.py", "import")
    bus.open_wire("src/a.py", "src/c.py", "import")
    bus.open_wire("src/a.py", "src/d.py", "import")

    closed = bus.close_wires_by_ids(["wire_000", "wire_002"], "src/resolver.py")

    assert len(closed) == 2
    assert len(bus.get_open_wires()) == 1
    assert bus.get_open_wires()[0]["id"] == "wire_001"


def test_find_wires_to(bus):
    bus.open_wire("src/a.py", "src/b.py", "import")
    bus.open_wire("src/c.py", "src/b.py", "call")

    wires = bus.find_wires_to("src/b.py")
    assert len(wires) == 2


def test_find_wires_from(bus):
    bus.open_wire("src/a.py", "src/b.py", "import")
    bus.open_wire("src/a.py", "src/c.py", "import")

    wires = bus.find_wires_from("src/a.py")
    assert len(wires) == 2


# ── Snapshot isolation ───────────────────────────────────────────────────────


def test_snapshot_returns_copy(bus):
    bus.open_wire("src/a.py", "src/b.py", "import")

    snap = bus.get_snapshot()

    assert len(snap["open_wires"]) == 1
    assert len(snap["closed_wires"]) == 0
    assert isinstance(snap["in_flight"], set)
    assert isinstance(snap["pending"], dict)


def test_snapshot_is_frozen(bus):
    """Changes after snapshot should not affect the snapshot."""
    bus.open_wire("src/a.py", "src/b.py", "import")
    snap = bus.get_snapshot()

    # Open another wire after snapshot
    bus.open_wire("src/c.py", "src/d.py", "import")

    # Snapshot should still show only the original wire
    assert len(snap["open_wires"]) == 1
    assert len(bus.get_open_wires()) == 2


def test_snapshot_includes_in_flight(bus):
    bus.mark_in_flight("src/a.py")
    bus.mark_in_flight("src/b.py")

    snap = bus.get_snapshot()

    assert snap["in_flight"] == {"src/a.py", "src/b.py"}


def test_snapshot_includes_pending(bus):
    bus.mark_in_flight("src/b.py")
    bus.open_wire("src/a.py", "src/b.py", "import")  # target is in-flight → pending

    snap = bus.get_snapshot()

    assert "wire_000" in snap["pending"]
    assert snap["pending"]["wire_000"] == "src/b.py"


# ── In-flight tracking ──────────────────────────────────────────────────────


def test_mark_in_flight(bus):
    bus.mark_in_flight("src/a.py")

    assert "src/a.py" in bus.get_in_flight()


def test_mark_completed(bus):
    bus.mark_in_flight("src/a.py")
    bus.mark_in_flight("src/b.py")
    bus.mark_completed("src/a.py")

    in_flight = bus.get_in_flight()
    assert "src/a.py" not in in_flight
    assert "src/b.py" in in_flight


def test_mark_completed_resolves_pending(bus):
    """Completing a file should resolve pending wires targeting it."""
    bus.mark_in_flight("src/b.py")
    bus.open_wire("src/a.py", "src/b.py", "import")

    assert "wire_000" in bus.get_pending_wires()

    bus.mark_completed("src/b.py")

    assert len(bus.get_pending_wires()) == 0


def test_pending_wire_created_when_target_in_flight(bus):
    """Opening a wire to an in-flight file marks it as pending."""
    bus.mark_in_flight("src/target.py")
    wire = bus.open_wire("src/source.py", "src/target.py", "import")

    pending = bus.get_pending_wires()
    assert wire["id"] in pending
    assert pending[wire["id"]] == "src/target.py"


def test_no_pending_when_target_not_in_flight(bus):
    """Opening a wire to a non-in-flight file should not create pending."""
    wire = bus.open_wire("src/source.py", "src/target.py", "import")

    pending = bus.get_pending_wires()
    assert len(pending) == 0


# ── Event subscription ──────────────────────────────────────────────────────


def test_subscribe_receives_open_events(bus):
    events = []
    bus.subscribe(lambda e: events.append(e))

    bus.open_wire("src/a.py", "src/b.py", "import")

    assert len(events) == 1
    assert events[0].event_type == "opened"
    assert events[0].wire_id == "wire_000"


def test_subscribe_receives_close_events(bus):
    events = []
    bus.subscribe(lambda e: events.append(e))

    bus.open_wire("src/a.py", "src/b.py", "import")
    bus.close_wire("wire_000", "src/b.py")

    assert len(events) == 2
    assert events[1].event_type == "closed"
    assert events[1].wire_id == "wire_000"


def test_subscribe_receives_pending_events(bus):
    events = []
    bus.subscribe(lambda e: events.append(e))

    bus.mark_in_flight("src/b.py")
    bus.open_wire("src/a.py", "src/b.py", "import")

    assert len(events) == 1
    assert events[0].event_type == "pending"


def test_unsubscribe(bus):
    events = []
    callback = lambda e: events.append(e)
    bus.subscribe(callback)

    bus.open_wire("src/a.py", "src/b.py", "import")
    assert len(events) == 1

    bus.unsubscribe(callback)
    bus.open_wire("src/c.py", "src/d.py", "import")
    assert len(events) == 1  # no new events after unsubscribe


def test_subscriber_error_does_not_break_wire_ops(bus):
    """A failing subscriber should not prevent wire operations."""

    def bad_callback(event):
        raise RuntimeError("subscriber error")

    bus.subscribe(bad_callback)

    # Should not raise
    wire = bus.open_wire("src/a.py", "src/b.py", "import")
    assert wire is not None

    closed = bus.close_wire("wire_000", "src/b.py")
    assert closed is not None


# ── Delegated operations ────────────────────────────────────────────────────


def test_load_state(bus):
    open_wires = [{"id": "w1", "from": "a.py", "to": "b.py", "type": "import", "context": ""}]
    closed_wires = [{"id": "w2", "from": "c.py", "to": "d.py", "type": "call", "context": "", "resolved_in": "d.py"}]

    bus.load_state(open_wires, closed_wires)

    assert len(bus.get_open_wires()) == 1
    assert len(bus.get_closed_wires()) == 1


def test_reopen_wires_for_files(bus):
    bus.open_wire("src/a.py", "src/b.py", "import")
    bus.close_wire("wire_000", "src/b.py")

    assert len(bus.get_open_wires()) == 0

    reopened = bus.reopen_wires_for_files(["src/a.py"])
    assert reopened == 1
    assert len(bus.get_open_wires()) == 1


def test_handle_renamed_file(bus):
    bus.open_wire("src/old.py", "target.py", "import")
    bus.open_wire("other.py", "src/old.py", "call")

    updated = bus.handle_renamed_file("src/old.py", "src/new.py")
    assert updated == 2


def test_handle_deleted_file(bus):
    bus.open_wire("src/deleted.py", "target.py", "import")
    bus.open_wire("other.py", "src/deleted.py", "call")

    result = bus.handle_deleted_file("src/deleted.py")
    assert "wire_000" in result["orphaned_from"]
    assert "wire_001" in result["orphaned_to"]


def test_reprioritize_queue(bus):
    bus.open_wire("src/a.py", "src/b.py", "import")

    queue = ["src/a.py", "src/b.py", "src/c.py"]
    result = bus.reprioritize_queue(queue)

    # Should return a list (reprioritized based on wire targets)
    assert isinstance(result, list)
    assert set(result) == set(queue)


# ── Thread safety ────────────────────────────────────────────────────────────


def test_concurrent_wire_opens(bus):
    """Multiple threads opening wires concurrently should not corrupt state."""
    results = []
    errors = []

    def open_wires(thread_id, count):
        try:
            for i in range(count):
                wire = bus.open_wire(f"src/t{thread_id}_{i}.py", "target.py", "import")
                results.append(wire["id"])
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=open_wires, args=(t, 10)) for t in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert len(results) == 50
    # All wire IDs should be unique
    assert len(set(results)) == 50


def test_concurrent_open_and_close(bus):
    """Opening and closing wires concurrently should not corrupt state."""
    errors = []

    # Pre-open some wires
    wire_ids = []
    for i in range(20):
        w = bus.open_wire(f"src/f{i}.py", "target.py", "import")
        wire_ids.append(w["id"])

    def close_wires(ids):
        try:
            for wid in ids:
                bus.close_wire(wid, "resolver.py")
        except Exception as e:
            errors.append(e)

    def open_more():
        try:
            for i in range(20):
                bus.open_wire(f"src/new{i}.py", "target.py", "call")
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=close_wires, args=(wire_ids[:10],))
    t2 = threading.Thread(target=close_wires, args=(wire_ids[10:],))
    t3 = threading.Thread(target=open_more)

    t1.start()
    t2.start()
    t3.start()
    t1.join()
    t2.join()
    t3.join()

    assert len(errors) == 0
    # All original wires should be closed
    assert len(bus.get_closed_wires()) == 20
    # New wires should be open
    assert len(bus.get_open_wires()) == 20


def test_concurrent_snapshot_and_mutation(bus):
    """Taking snapshots while mutating should not corrupt."""
    snapshots = []
    errors = []

    def take_snapshots(count):
        try:
            for _ in range(count):
                snap = bus.get_snapshot()
                snapshots.append(snap)
        except Exception as e:
            errors.append(e)

    def mutate():
        try:
            for i in range(20):
                bus.open_wire(f"src/m{i}.py", "target.py", "import")
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=take_snapshots, args=(20,))
    t2 = threading.Thread(target=mutate)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(errors) == 0
    assert len(snapshots) == 20
    # Each snapshot should be a consistent view
    for snap in snapshots:
        assert "open_wires" in snap
        assert "closed_wires" in snap


def test_concurrent_in_flight_tracking(bus):
    """In-flight tracking should be thread-safe."""
    errors = []

    def mark_in_flight(start, count):
        try:
            for i in range(start, start + count):
                bus.mark_in_flight(f"file_{i}.py")
        except Exception as e:
            errors.append(e)

    def mark_completed(start, count):
        try:
            time.sleep(0.01)  # Let in-flight marking get ahead
            for i in range(start, start + count):
                bus.mark_completed(f"file_{i}.py")
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=mark_in_flight, args=(0, 20))
    t2 = threading.Thread(target=mark_in_flight, args=(20, 20))
    t3 = threading.Thread(target=mark_completed, args=(0, 10))

    t1.start()
    t2.start()
    t3.start()
    t1.join()
    t2.join()
    t3.join()

    assert len(errors) == 0
    in_flight = bus.get_in_flight()
    # Files 0-9 were marked completed, 10-39 should still be in-flight
    for i in range(10):
        assert f"file_{i}.py" not in in_flight
    for i in range(10, 40):
        assert f"file_{i}.py" in in_flight
