"""Wire Manager — tracks wire lifecycle with rename/delete awareness."""

from typing import List, Dict, Any, Optional


class WireManager:
    def __init__(self):
        self._open: List[Dict[str, Any]] = []
        self._closed: List[Dict[str, Any]] = []
        self._wire_counter = 0

    def load_state(self, open_wires: List[Dict], closed_wires: List[Dict]):
        self._open = list(open_wires)
        self._closed = list(closed_wires)
        all_ids = [w.get("id", "") for w in self._open + self._closed]
        max_num = 0
        for wid in all_ids:
            try:
                max_num = max(max_num, int(wid.replace("wire_", "")))
            except (ValueError, AttributeError):
                pass
        self._wire_counter = max_num + 1

    def _next_id(self) -> str:
        wid = f"wire_{self._wire_counter:03d}"
        self._wire_counter += 1
        return wid

    def open_wire(
        self, from_file: str, to_target: str, wire_type: str, context: str = ""
    ) -> Dict[str, Any]:
        for w in self._open:
            if w["from"] == from_file and w["to"] == to_target:
                if len(context) > len(w.get("context", "")):
                    w["context"] = context
                return w

        wire = {
            "id": self._next_id(),
            "from": from_file,
            "to": to_target,
            "type": wire_type,
            "context": context,
            "opened_at": from_file,
        }
        self._open.append(wire)
        return wire

    def close_wire(
        self, wire_id: str, resolved_in: str, summary: str = ""
    ) -> Optional[Dict]:
        wire = None
        for w in self._open:
            if w["id"] == wire_id:
                wire = w
                break
        if wire is None:
            return None

        self._open.remove(wire)
        wire["resolved_in"] = resolved_in
        wire["summary"] = summary
        self._closed.append(wire)
        return wire

    def close_wires_by_ids(
        self, wire_ids: List[str], resolved_in: str
    ) -> List[Dict]:
        closed = []
        for wid in wire_ids:
            result = self.close_wire(wid, resolved_in)
            if result:
                closed.append(result)
        return closed

    def find_wires_to(self, target: str) -> List[Dict]:
        matches = []
        for w in self._open:
            to = w["to"]
            if to == target:
                matches.append(w)
            elif target.endswith("/" + to) or target.endswith(
                "/" + to.split("/")[-1]
            ):
                matches.append(w)
            elif to.startswith("./") or to.startswith("../"):
                clean = to.lstrip("./").replace("../", "")
                for ext in ("", ".js", ".ts", ".py", ".go", ".rs", ".rb"):
                    if target.endswith(clean + ext):
                        matches.append(w)
                        break
            elif "/" not in to and "." not in to:
                basename = target.split("/")[-1].rsplit(".", 1)[0]
                if to.lower() == basename.lower():
                    matches.append(w)
        return matches

    def find_wires_from(self, source: str) -> List[Dict]:
        return [w for w in self._open if w["from"] == source]

    def get_open_wires(self) -> List[Dict]:
        return list(self._open)

    def get_closed_wires(self) -> List[Dict]:
        return list(self._closed)

    # ── Git-aware wire operations ────────────────────────────────

    def reopen_wires_for_files(self, files: List[str]) -> int:
        """Re-open closed wires related to changed files."""
        reopened = 0
        to_reopen = []
        file_set = set(files)

        for wire in self._closed:
            if (
                wire.get("from") in file_set
                or wire.get("to") in file_set
                or wire.get("resolved_in") in file_set
            ):
                to_reopen.append(wire)

        for wire in to_reopen:
            self._closed.remove(wire)
            wire.pop("resolved_in", None)
            wire.pop("summary", None)
            self._open.append(wire)
            reopened += 1
        return reopened

    def handle_renamed_file(self, old_path: str, new_path: str) -> int:
        """
        Update all wires referencing old_path to point to new_path.
        Returns count of updated wires.
        """
        updated = 0

        for wire in self._open + self._closed:
            if wire.get("from") == old_path:
                wire["from"] = new_path
                updated += 1
            if wire.get("to") == old_path:
                wire["to"] = new_path
                updated += 1
            if wire.get("opened_at") == old_path:
                wire["opened_at"] = new_path
                updated += 1
            if wire.get("resolved_in") == old_path:
                wire["resolved_in"] = new_path
                updated += 1

        return updated

    def handle_deleted_file(self, deleted_path: str) -> Dict[str, List]:
        """
        Handle a deleted file:
        - Wires FROM the deleted file: close them with a deletion note
        - Wires TO the deleted file: mark them with deletion context
        - Closed wires that referenced the file: re-open with deletion note

        Returns dict with 'orphaned_from', 'orphaned_to', 'reopened' counts.
        """
        result = {"orphaned_from": [], "orphaned_to": [], "reopened": []}

        # Wires originating from deleted file — close with note
        from_wires = [w for w in self._open if w["from"] == deleted_path]
        for w in from_wires:
            w["context"] = (
                f"[SOURCE DELETED] {w.get('context', '')} "
                f"— Origin file {deleted_path} was removed"
            )
            result["orphaned_from"].append(w["id"])

        # Wires pointing to deleted file — mark as permanently unresolvable
        to_wires = [w for w in self._open if w["to"] == deleted_path]
        for w in to_wires:
            w["context"] = (
                f"[TARGET DELETED] {w.get('context', '')} "
                f"— Target file {deleted_path} was removed"
            )
            result["orphaned_to"].append(w["id"])

        # Closed wires that referenced the deleted file — re-open
        to_reopen = []
        for wire in self._closed:
            if wire.get("resolved_in") == deleted_path:
                to_reopen.append(wire)
            elif wire.get("to") == deleted_path:
                to_reopen.append(wire)

        for wire in to_reopen:
            self._closed.remove(wire)
            wire.pop("resolved_in", None)
            wire.pop("summary", None)
            wire["context"] = (
                f"[RE-OPENED: file deleted] {wire.get('context', '')} "
                f"— {deleted_path} was removed from codebase"
            )
            self._open.append(wire)
            result["reopened"].append(wire["id"])

        return result

    def reprioritize_queue(self, queue: List[str]) -> List[str]:
        if not self._open or not queue:
            return queue
        scores = {}
        for f in queue:
            scores[f] = len(self.find_wires_to(f))
        return sorted(queue, key=lambda f: scores.get(f, 0), reverse=True)