"""Agent state management — now with git tracking."""

import json
import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class AgentState:
    run_id: str = ""
    queue: List[str] = field(default_factory=list)
    parked: List[str] = field(default_factory=list)
    park_reasons: Dict[str, str] = field(default_factory=dict)
    open_wires: List[Dict[str, Any]] = field(default_factory=list)
    closed_wires: List[Dict[str, Any]] = field(default_factory=list)
    section_index: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    section_contents: Dict[str, str] = field(default_factory=dict)
    processed: List[str] = field(default_factory=list)

    # ── Git tracking fields ──────────────────────────────────────
    last_commit: Optional[str] = None
    last_commit_short: Optional[str] = None
    last_run: Optional[str] = None
    file_hashes: Dict[str, str] = field(default_factory=dict)  # path → md5

    def save(self, path: str):
        data = {
            "run_id": self.run_id,
            "queue": self.queue,
            "parked": self.parked,
            "park_reasons": self.park_reasons,
            "open_wires": self.open_wires,
            "closed_wires": self.closed_wires,
            "section_index": self.section_index,
            "section_contents": self.section_contents,
            "processed": self.processed,
            "last_commit": self.last_commit,
            "last_commit_short": self.last_commit_short,
            "last_run": self.last_run,
            "file_hashes": self.file_hashes,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

        # Write atomically (write to tmp then rename)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)

    @classmethod
    def load(cls, path: str) -> "AgentState":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        state = cls()
        state.run_id = data.get("run_id", "")
        state.queue = data.get("queue", [])
        state.parked = data.get("parked", [])
        state.park_reasons = data.get("park_reasons", {})
        state.open_wires = data.get("open_wires", [])
        state.closed_wires = data.get("closed_wires", [])
        state.section_index = data.get("section_index", {})
        state.section_contents = data.get("section_contents", {})
        state.processed = data.get("processed", [])
        state.last_commit = data.get("last_commit")
        state.last_commit_short = data.get("last_commit_short")
        state.last_run = data.get("last_run")
        state.file_hashes = data.get("file_hashes", {})
        return state