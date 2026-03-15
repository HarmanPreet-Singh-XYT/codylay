"""Docstore — section-based markdown document management with git awareness."""

import re
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional


class DocStore:
    def __init__(self):
        self._sections: Dict[str, Dict[str, Any]] = {}
        self._order_counter = 0
        self._doc_title = "Codebase Reference"

    def initialize_skeleton(self, title: str, suggested_sections: List[str]):
        self._doc_title = title
        self.add_section(
            section_id="overview",
            title="Overview",
            content="*Documentation being generated…*",
            tags=["overview", "summary"],
            file="",
        )
        for section_name in suggested_sections:
            sid = self._slugify(section_name)
            if sid != "overview":
                self.add_section(
                    section_id=sid, title=section_name, content="",
                    tags=[sid], file="",
                )

    def add_section(
        self,
        section_id: str,
        title: str,
        content: str,
        tags: List[str] = None,
        file: str = "",
        deps: List[str] = None,
        insert_after: str = None,
    ):
        if insert_after and insert_after in self._sections:
            order = self._sections[insert_after]["order"] + 0.5
        else:
            order = self._order_counter
            self._order_counter += 1

        self._sections[section_id] = {
            "title": title,
            "content": content,
            "tags": tags or [],
            "file": file,
            "deps": deps or [],
            "wires_closed": [],
            "order": order,
        }

    def patch_section(self, section_id: str, update_type: str, content: str):
        if section_id not in self._sections:
            return
        sec = self._sections[section_id]
        if update_type == "replace":
            sec["content"] = content
        elif update_type == "append":
            sec["content"] = (
                sec["content"] + "\n\n" + content if sec["content"] else content
            )
        elif update_type == "insert_link":
            sec["content"] = (
                sec["content"] + "\n" + content if sec["content"] else content
            )

    def get_relevant_sections(
        self,
        file_path: str,
        file_imports: List[str] = None,
        open_wires: List[Dict] = None,
    ) -> Dict[str, Dict[str, Any]]:
        relevant = {}

        if "overview" in self._sections and self._sections["overview"]["content"]:
            relevant["overview"] = self._sections["overview"]

        for sid, sec in self._sections.items():
            if sid == "overview":
                continue
            if file_path in sec.get("deps", []):
                relevant[sid] = sec
                continue
            if sec.get("file") == file_path:
                relevant[sid] = sec
                continue
            if file_imports:
                sec_tags = set(t.lower() for t in sec.get("tags", []))
                import_terms = set()
                for imp in file_imports:
                    parts = (
                        imp.replace("./", "")
                        .replace("../", "")
                        .replace(".", "/")
                        .split("/")
                    )
                    import_terms.update(p.lower() for p in parts if p)
                if sec_tags & import_terms:
                    relevant[sid] = sec
                    continue
            if open_wires:
                for w in open_wires:
                    if w.get("to") == file_path or w.get("from") == sec.get(
                        "file", ""
                    ):
                        relevant[sid] = sec
                        break

        if len(relevant) > 10:
            sorted_items = sorted(
                relevant.items(),
                key=lambda x: (x[0] != "overview", len(x[1].get("content", ""))),
            )
            relevant = dict(sorted_items[:10])

        return relevant

    def get_section_index(self) -> Dict[str, Dict[str, Any]]:
        index = {}
        for sid, sec in self._sections.items():
            if not sec.get("content"):
                continue
            index[sid] = {
                "title": sec["title"],
                "file": sec.get("file", ""),
                "tags": sec.get("tags", []),
                "deps": sec.get("deps", []),
                "wires_closed": sec.get("wires_closed", []),
            }
        return index

    def get_section_contents(self) -> Dict[str, str]:
        return {sid: sec.get("content", "") for sid, sec in self._sections.items()}

    def load_from_state(self, section_index: Dict, section_contents: Dict):
        for sid, meta in section_index.items():
            self._sections[sid] = {
                "title": meta.get("title", sid),
                "content": section_contents.get(sid, ""),
                "tags": meta.get("tags", []),
                "file": meta.get("file", ""),
                "deps": meta.get("deps", []),
                "wires_closed": meta.get("wires_closed", []),
                "order": self._order_counter,
            }
            self._order_counter += 1

    # ── Git-aware section operations ─────────────────────────────

    def invalidate_sections_for_files(self, file_paths: List[str]) -> List[str]:
        """
        Mark sections as needing re-documentation because their source
        files changed. Returns list of invalidated section IDs.
        """
        invalidated = []
        file_set = set(file_paths)

        for sid, sec in self._sections.items():
            if sec.get("file") in file_set:
                # Don't delete — mark as stale so the processor can update it
                old_content = sec["content"]
                sec["content"] = (
                    f"> ⚠️ *This section is being updated — source file changed.*\n\n"
                    f"{old_content}"
                )
                invalidated.append(sid)

            # Also check deps
            if set(sec.get("deps", [])) & file_set:
                invalidated.append(sid)

        return list(set(invalidated))

    def handle_renamed_file(self, old_path: str, new_path: str) -> List[str]:
        """
        Update all sections that reference old_path to point to new_path.
        Returns list of updated section IDs.
        """
        updated = []

        for sid, sec in self._sections.items():
            changed = False

            # Update file reference
            if sec.get("file") == old_path:
                sec["file"] = new_path
                changed = True

            # Update deps
            if old_path in sec.get("deps", []):
                sec["deps"] = [
                    new_path if d == old_path else d
                    for d in sec["deps"]
                ]
                changed = True

            # Update content references
            if old_path in sec.get("content", ""):
                sec["content"] = sec["content"].replace(
                    old_path, new_path
                )
                changed = True

            if changed:
                updated.append(sid)

        return updated

    def handle_deleted_file(self, deleted_path: str) -> List[str]:
        """
        Handle a deleted file's sections.
        Don't remove the section — mark it as covering a deleted file.
        Returns list of affected section IDs.
        """
        affected = []

        for sid, sec in self._sections.items():
            if sec.get("file") == deleted_path:
                sec["content"] = (
                    f"> ⚠️ **File Deleted** — `{deleted_path}` has been removed "
                    f"from the codebase. This section documents its last known state.\n\n"
                    f"{sec['content']}"
                )
                sec["tags"] = list(set(sec.get("tags", []) + ["deleted"]))
                affected.append(sid)

        return affected

    def remove_section(self, section_id: str) -> bool:
        """Remove a section entirely. Returns True if found and removed."""
        if section_id in self._sections:
            del self._sections[section_id]
            return True
        return False

    # ── Standard output sections ─────────────────────────────────

    def add_dependency_graph(self, closed_wires: List[Dict]):
        if not closed_wires:
            content = "*No internal dependencies were traced.*"
        else:
            content = (
                "| From | To | Type | Summary |\n"
                "|------|----|------|--------|\n"
            )
            for w in closed_wires:
                s = w.get("summary", w.get("context", "")).replace("|", "\\|")
                content += (
                    f"| `{w.get('from', '?')}` | `{w.get('to', '?')}` "
                    f"| {w.get('type', '?')} | {s} |\n"
                )
        self.add_section(
            section_id="dependency-graph",
            title="Dependency Graph",
            content=content,
            tags=["dependencies", "graph", "wires"],
        )

    def add_unresolved_references(self, open_wires: List[Dict]):
        if not open_wires:
            content = "*All references were resolved within the codebase.* ✓"
        else:
            content = (
                "| From | To | Type | Notes |\n"
                "|------|----|------|-------|\n"
            )
            for w in open_wires:
                notes = w.get(
                    "context", w.get("classification_note", "")
                ).replace("|", "\\|")
                content += (
                    f"| `{w.get('from', '?')}` | `{w.get('to', '?')}` "
                    f"| {w.get('type', '?')} | {notes} |\n"
                )
        self.add_section(
            section_id="unresolved-references",
            title="Unresolved References",
            content=content,
            tags=["unresolved", "open-wires"],
        )

    def render_full_document(self) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            f"# {self._doc_title}",
            f"> Generated by CodeDoc on {now}",
            "",
        ]

        ordered = sorted(
            [
                (sid, sec)
                for sid, sec in self._sections.items()
                if sec.get("content")
            ],
            key=lambda x: x[1].get("order", 999),
        )

        # Table of contents
        lines.append("## Table of Contents")
        lines.append("")
        for sid, sec in ordered:
            title = sec["title"]
            anchor = self._slugify(title)
            lines.append(f"- [{title}](#{anchor})")
        lines.append("")
        lines.append("---")
        lines.append("")

        # Render each section
        for sid, sec in ordered:
            title = sec["title"]
            content = sec["content"]
            file_ref = sec.get("file", "")

            lines.append(f"## {title}")
            if file_ref:
                lines.append(f"> File: `{file_ref}`")
                lines.append("")
            lines.append(content)
            lines.append("")
            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def _slugify(self, text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"[^\w\s-]", "", text)
        text = re.sub(r"[\s_]+", "-", text)
        text = re.sub(r"-+", "-", text)
        return text.strip("-")