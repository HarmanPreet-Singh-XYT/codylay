"""
Wire Bus — Thread-safe central wire state manager for parallel processing.

This wraps the existing WireManager with:
- Read/write locks on all wire operations
- Wire event notifications (subscribe to wire close events)
- Snapshot isolation (workers get frozen context at job start)
- Pending wire state (for wires pointing to in-flight files)
"""

import threading
from typing import Any, Callable, Dict, List, Optional, Set

from codilay.wire_manager import WireManager


class WireEvent:
    """Represents a wire state change event."""

    __slots__ = ("event_type", "wire_id", "wire", "source_file")

    def __init__(self, event_type: str, wire_id: str, wire: Dict[str, Any], source_file: str):
        self.event_type = event_type  # "opened", "closed", "pending"
        self.wire_id = wire_id
        self.wire = wire
        self.source_file = source_file


class WireBus:
    """
    Thread-safe wire state manager.

    All wire operations go through the bus, which holds a lock to prevent
    concurrent mutation. Workers interact via:
    - get_snapshot(): get a frozen copy of wire state for their context
    - open_wire() / close_wire(): mutate through the bus
    - subscribe(): get notified when wires close (for unparking)
    - mark_pending(): mark a wire as pointing to an in-flight file
    """

    def __init__(self, wire_mgr: WireManager):
        self._mgr = wire_mgr
        self._lock = threading.RLock()  # Reentrant for nested calls

        # Subscribers: callbacks invoked on wire events
        self._subscribers: List[Callable[[WireEvent], None]] = []

        # Track which files are currently being processed
        self._in_flight: Set[str] = set()

        # Pending wires: wires opened to files currently being processed
        # Maps wire_id -> file being processed
        self._pending: Dict[str, str] = {}

    # ── Snapshot isolation ───────────────────────────────────────

    def get_snapshot(self) -> Dict[str, Any]:
        """
        Get a frozen snapshot of wire state.

        Workers receive this at job start and use it for their entire
        processing run. They cannot see new wires created mid-processing
        by other workers — this prevents partial reads.
        """
        with self._lock:
            return {
                "open_wires": self._mgr.get_open_wires(),  # deep copy from mgr
                "closed_wires": self._mgr.get_closed_wires(),  # deep copy from mgr
                "in_flight": set(self._in_flight),
                "pending": dict(self._pending),
            }

    # ── Wire operations (thread-safe) ────────────────────────────

    def open_wire(self, from_file: str, to_target: str, wire_type: str, context: str = "") -> Dict[str, Any]:
        """Thread-safe wire open. If target is in-flight, marks as pending."""
        with self._lock:
            wire = self._mgr.open_wire(from_file, to_target, wire_type, context)

            # Check if target is currently being processed by another worker
            if to_target in self._in_flight:
                self._pending[wire["id"]] = to_target
                self._emit(WireEvent("pending", wire["id"], wire, from_file))
            else:
                self._emit(WireEvent("opened", wire["id"], wire, from_file))

            return wire

    def close_wire(self, wire_id: str, resolved_in: str, summary: str = "") -> Optional[Dict]:
        """Thread-safe wire close. Notifies subscribers."""
        with self._lock:
            wire = self._mgr.close_wire(wire_id, resolved_in, summary)
            if wire:
                self._pending.pop(wire_id, None)
                self._emit(WireEvent("closed", wire_id, wire, resolved_in))
            return wire

    def close_wires_by_ids(self, wire_ids: List[str], resolved_in: str) -> List[Dict]:
        """Thread-safe batch close."""
        with self._lock:
            closed = self._mgr.close_wires_by_ids(wire_ids, resolved_in)
            for wire in closed:
                wid = wire.get("id", "")
                self._pending.pop(wid, None)
                self._emit(WireEvent("closed", wid, wire, resolved_in))
            return closed

    def find_wires_to(self, target: str) -> List[Dict]:
        """Thread-safe wire lookup."""
        with self._lock:
            return self._mgr.find_wires_to(target)

    def find_wires_from(self, source: str) -> List[Dict]:
        """Thread-safe wire lookup."""
        with self._lock:
            return self._mgr.find_wires_from(source)

    def get_open_wires(self) -> List[Dict]:
        """Thread-safe get all open wires."""
        with self._lock:
            return self._mgr.get_open_wires()

    def get_closed_wires(self) -> List[Dict]:
        """Thread-safe get all closed wires."""
        with self._lock:
            return self._mgr.get_closed_wires()

    def reprioritize_queue(self, queue: List[str]) -> List[str]:
        """Thread-safe queue reprioritization."""
        with self._lock:
            return self._mgr.reprioritize_queue(queue)

    # ── In-flight tracking ───────────────────────────────────────

    def mark_in_flight(self, file_path: str):
        """Mark a file as currently being processed by a worker."""
        with self._lock:
            self._in_flight.add(file_path)

    def mark_completed(self, file_path: str):
        """
        Mark a file as done processing. Resolves any pending wires
        that were waiting on this file.
        """
        with self._lock:
            self._in_flight.discard(file_path)

            # Resolve pending wires that targeted this file
            resolved_pending = [wid for wid, target in self._pending.items() if target == file_path]
            for wid in resolved_pending:
                del self._pending[wid]
                # These wires stay open — the finalizer will handle them
                # since the target is now processed

    def get_in_flight(self) -> Set[str]:
        """Get the set of currently in-flight files."""
        with self._lock:
            return set(self._in_flight)

    def get_pending_wires(self) -> Dict[str, str]:
        """Get wires that are pending (target in-flight)."""
        with self._lock:
            return dict(self._pending)

    # ── Event subscription ───────────────────────────────────────

    def subscribe(self, callback: Callable[[WireEvent], None]):
        """Subscribe to wire events. Called under the lock."""
        with self._lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[WireEvent], None]):
        """Remove a subscriber."""
        with self._lock:
            self._subscribers = [s for s in self._subscribers if s is not callback]

    # ── Delegated git-aware operations ───────────────────────────

    def load_state(self, open_wires: List[Dict], closed_wires: List[Dict]):
        with self._lock:
            self._mgr.load_state(open_wires, closed_wires)

    def reopen_wires_for_files(self, files: List[str]) -> int:
        with self._lock:
            return self._mgr.reopen_wires_for_files(files)

    def handle_renamed_file(self, old_path: str, new_path: str) -> int:
        with self._lock:
            return self._mgr.handle_renamed_file(old_path, new_path)

    def handle_deleted_file(self, deleted_path: str) -> Dict[str, List]:
        with self._lock:
            return self._mgr.handle_deleted_file(deleted_path)

    # ── Internal ─────────────────────────────────────────────────

    def _emit(self, event: WireEvent):
        """Emit event to all subscribers. Must be called under lock."""
        for callback in self._subscribers:
            try:
                callback(event)
            except Exception:
                pass  # Don't let subscriber errors break wire operations
