"""
ChatStore — persistent conversation history, branching, pinning, and cross-session memory.

Storage layout under <output_dir>/chat/:
    conversations/
        <conv_id>.json        — one file per conversation (tree of messages + metadata)
    memory.json               — cross-session memory facts

Conversation format ("tree"):
    - nodes    : dict[msg_id -> node] — all messages ever written
    - branches : dict[branch_id -> branch] — named paths through the tree
    - active_branch_id : str — which branch the user is currently on

Each branch stores an ordered `path` (list of msg_ids) from root to tip. When the
user edits a message, a new branch is created from the fork point instead of
truncating the conversation. The original branch is fully preserved.

Privacy model:
    - visibility : "private" | "team"
    - owner      : str | None  (username who created the conversation)
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ── Data helpers ──────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_id() -> str:
    return uuid.uuid4().hex[:12]


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")[:60]


# ── Message schema ────────────────────────────────────────────────────────────


def make_message(
    role: str,
    content: str,
    *,
    msg_id: Optional[str] = None,
    sources: Optional[List[str]] = None,
    confidence: Optional[float] = None,
    escalated: bool = False,
    pinned: bool = False,
    parent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a normalised message dict."""
    return {
        "id": msg_id or _make_id(),
        "role": role,  # "user" | "assistant" | "system"
        "content": content,
        "sources": sources or [],
        "confidence": confidence,
        "escalated": escalated,
        "pinned": pinned,
        "parent_id": parent_id,
        "created_at": _now_iso(),
    }


def _make_node(message: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap a message dict as a tree node (adds children list)."""
    return {**message, "children": message.get("children", [])}


# ── Branch schema ─────────────────────────────────────────────────────────────


def _make_branch(
    branch_id: str,
    label: str,
    path: Optional[List[str]] = None,
    fork_msg_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": branch_id,
        "label": label,
        "created_at": _now_iso(),
        "fork_msg_id": fork_msg_id,  # msg after which this branch split off (None = original)
        "path": path or [],  # ordered list of msg_ids from root to tip
    }


# ── Conversation schema ──────────────────────────────────────────────────────


def make_conversation(
    title: str = "",
    conv_id: Optional[str] = None,
    parent_conv_id: Optional[str] = None,
    branch_point_msg_id: Optional[str] = None,
    visibility: str = "private",
    owner: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new conversation envelope (tree format)."""
    main_branch = _make_branch("main", "main")
    return {
        "id": conv_id or _make_id(),
        "title": title or "New conversation",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "format": "tree",
        "visibility": visibility,  # "private" | "team"
        "owner": owner,
        "nodes": {},
        "branches": {"main": main_branch},
        "active_branch_id": "main",
        # Legacy fields kept for external compatibility
        "parent_conv_id": parent_conv_id,
        "branch_point_msg_id": branch_point_msg_id,
    }


# ── ChatStore ─────────────────────────────────────────────────────────────────


class ChatStore:
    """File-backed store for conversations and cross-session memory."""

    def __init__(self, output_dir: str):
        self._base = os.path.join(output_dir, "chat")
        self._conv_dir = os.path.join(self._base, "conversations")
        self._memory_path = os.path.join(self._base, "memory.json")
        os.makedirs(self._conv_dir, exist_ok=True)

    # ── Conversation CRUD ─────────────────────────────────────────

    def list_conversations(
        self,
        user: Optional[str] = None,
        include_team: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Return summary list sorted by most-recently updated.

        Privacy filtering:
        - user=None  → return all conversations
        - user="alice" → return alice's private convs + team convs (if include_team)
        """
        summaries = []
        for fname in os.listdir(self._conv_dir):
            if not fname.endswith(".json"):
                continue
            conv = self._read_conv(fname[:-5])
            if conv is None:
                continue

            # Privacy filter
            if user is not None:
                visibility = conv.get("visibility", "private")
                owner = conv.get("owner")
                if visibility == "private" and owner != user:
                    continue
                if visibility == "team" and not include_team:
                    continue

            branch = self._active_branch(conv)
            path = branch.get("path", []) if branch else []
            msg_count = len(path)
            pinned_count = sum(1 for mid in path if conv["nodes"].get(mid, {}).get("pinned"))

            summaries.append(
                {
                    "id": conv["id"],
                    "title": conv["title"],
                    "created_at": conv["created_at"],
                    "updated_at": conv["updated_at"],
                    "visibility": conv.get("visibility", "private"),
                    "owner": conv.get("owner"),
                    "message_count": msg_count,
                    "pinned_count": pinned_count,
                    "branch_count": len(conv.get("branches", {})),
                    "active_branch_id": conv.get("active_branch_id", "main"),
                    "parent_conv_id": conv.get("parent_conv_id"),
                    "branch_point_msg_id": conv.get("branch_point_msg_id"),
                    "preview": self._preview(conv),
                }
            )
        summaries.sort(key=lambda c: c["updated_at"], reverse=True)
        return summaries

    def get_conversation(self, conv_id: str) -> Optional[Dict[str, Any]]:
        conv = self._read_conv(conv_id)
        if conv is None:
            return None
        return self._with_messages_view(conv)

    def create_conversation(
        self,
        title: str = "",
        parent_conv_id: Optional[str] = None,
        branch_point_msg_id: Optional[str] = None,
        visibility: str = "private",
        owner: Optional[str] = None,
    ) -> Dict[str, Any]:
        conv = make_conversation(
            title=title,
            parent_conv_id=parent_conv_id,
            branch_point_msg_id=branch_point_msg_id,
            visibility=visibility,
            owner=owner,
        )
        self._write_conv(conv)
        return self._with_messages_view(conv)

    def delete_conversation(self, conv_id: str) -> bool:
        path = self._conv_path(conv_id)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def update_title(self, conv_id: str, title: str) -> Optional[Dict[str, Any]]:
        conv = self._read_conv(conv_id)
        if conv is None:
            return None
        conv["title"] = title
        conv["updated_at"] = _now_iso()
        self._write_conv(conv)
        return self._with_messages_view(conv)

    def update_visibility(
        self,
        conv_id: str,
        visibility: str,
        owner: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Change a conversation's visibility (private/team) and optionally owner."""
        conv = self._read_conv(conv_id)
        if conv is None:
            return None
        conv["visibility"] = visibility
        if owner is not None:
            conv["owner"] = owner
        conv["updated_at"] = _now_iso()
        self._write_conv(conv)
        return self._with_messages_view(conv)

    # ── Message operations ────────────────────────────────────────

    def add_message(self, conv_id: str, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Append a message to the active branch. Returns updated conversation."""
        conv = self._read_conv(conv_id)
        if conv is None:
            return None

        branch = self._active_branch(conv)
        if branch is None:
            return None

        msg_id = message["id"]
        node = _make_node(message)
        conv["nodes"][msg_id] = node

        # Link: last node in branch gets this as a child
        if branch["path"]:
            parent_id = branch["path"][-1]
            conv["nodes"][parent_id]["children"].append(msg_id)

        branch["path"].append(msg_id)
        conv["updated_at"] = _now_iso()

        # Auto-title from first user message
        if conv["title"] == "New conversation":
            path_msgs = [conv["nodes"].get(mid) for mid in branch["path"]]
            first_user = next((m for m in path_msgs if m and m["role"] == "user"), None)
            if first_user:
                conv["title"] = self._auto_title(first_user["content"])

        self._write_conv(conv)
        return self._with_messages_view(conv)

    def edit_message(self, conv_id: str, msg_id: str, new_content: str) -> Optional[Dict[str, Any]]:
        """
        Edit a message by creating a new branch from its position.

        The original branch is fully preserved. A new branch is created containing
        all messages up to (but not including) the edited message, plus the new
        version. The new branch becomes active.

        Returns the updated conversation (with the new branch active).
        """
        conv = self._read_conv(conv_id)
        if conv is None:
            return None

        branch = self._active_branch(conv)
        if branch is None or msg_id not in branch["path"]:
            return None

        idx = branch["path"].index(msg_id)
        trunk = branch["path"][:idx]  # messages before the edited one

        # Build new message node
        original_node = conv["nodes"].get(msg_id, {})
        new_msg = make_message(original_node.get("role", "user"), new_content)
        new_msg_id = new_msg["id"]
        new_node = _make_node(new_msg)
        conv["nodes"][new_msg_id] = new_node

        # Link new node as a sibling of the original — child of its parent
        if trunk:
            parent_id = trunk[-1]
            if new_msg_id not in conv["nodes"][parent_id]["children"]:
                conv["nodes"][parent_id]["children"].append(new_msg_id)

        # Create the new branch
        branch_count = len(conv["branches"]) + 1
        new_branch_id = _make_id()
        conv["branches"][new_branch_id] = _make_branch(
            branch_id=new_branch_id,
            label=f"branch {branch_count}",
            path=trunk + [new_msg_id],
            fork_msg_id=trunk[-1] if trunk else None,
        )

        conv["active_branch_id"] = new_branch_id
        conv["updated_at"] = _now_iso()
        self._write_conv(conv)
        return self._with_messages_view(conv)

    def pin_message(self, conv_id: str, msg_id: str, pinned: bool = True) -> bool:
        conv = self._read_conv(conv_id)
        if conv is None:
            return False
        if msg_id not in conv.get("nodes", {}):
            return False
        conv["nodes"][msg_id]["pinned"] = pinned
        conv["updated_at"] = _now_iso()
        self._write_conv(conv)
        return True

    def get_pinned_messages(self, conv_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get pinned messages. If conv_id given, only from the active branch of that conversation.
        Otherwise, from ALL conversations (project-wide pinned knowledge).
        """
        pinned = []
        if conv_id:
            conv = self._read_conv(conv_id)
            if conv:
                branch = self._active_branch(conv)
                if branch:
                    for mid in branch["path"]:
                        node = conv["nodes"].get(mid)
                        if node and node.get("pinned"):
                            pinned.append(node)
        else:
            for fname in os.listdir(self._conv_dir):
                if not fname.endswith(".json"):
                    continue
                conv = self._read_conv(fname[:-5])
                if conv:
                    for mid, node in conv["nodes"].items():
                        if node.get("pinned"):
                            pinned.append(
                                {
                                    **node,
                                    "_conv_id": conv["id"],
                                    "_conv_title": conv["title"],
                                }
                            )
        return pinned

    # ── Branch operations ─────────────────────────────────────────

    def list_branches(self, conv_id: str) -> Optional[List[Dict[str, Any]]]:
        """List all branches for a conversation with metadata."""
        conv = self._read_conv(conv_id)
        if conv is None:
            return None
        active_id = conv.get("active_branch_id", "main")
        result = []
        for bid, branch in conv.get("branches", {}).items():
            result.append(
                {
                    **branch,
                    "is_active": bid == active_id,
                    "message_count": len(branch.get("path", [])),
                }
            )
        # Sort: main first, then by creation time
        result.sort(key=lambda b: (b["id"] != "main", b.get("created_at", "")))
        return result

    def switch_branch(self, conv_id: str, branch_id: str) -> Optional[Dict[str, Any]]:
        """Switch the active branch. Returns updated conversation."""
        conv = self._read_conv(conv_id)
        if conv is None:
            return None
        if branch_id not in conv.get("branches", {}):
            return None
        conv["active_branch_id"] = branch_id
        conv["updated_at"] = _now_iso()
        self._write_conv(conv)
        return self._with_messages_view(conv)

    def rename_branch(self, conv_id: str, branch_id: str, label: str) -> bool:
        """Rename a branch."""
        conv = self._read_conv(conv_id)
        if conv is None or branch_id not in conv.get("branches", {}):
            return False
        conv["branches"][branch_id]["label"] = label
        conv["updated_at"] = _now_iso()
        self._write_conv(conv)
        return True

    def branch_conversation(self, conv_id: str, from_msg_id: str) -> Optional[Dict[str, Any]]:
        """
        Create a new branch starting from (and including) from_msg_id.
        The new branch shares all messages up to from_msg_id with the current branch.
        Immediately switches to the new branch (which starts empty after from_msg_id).

        Returns the updated conversation with the new branch active.
        """
        conv = self._read_conv(conv_id)
        if conv is None:
            return None

        branch = self._active_branch(conv)
        if branch is None or from_msg_id not in branch["path"]:
            return None

        idx = branch["path"].index(from_msg_id)
        trunk = branch["path"][: idx + 1]  # up to and including from_msg_id

        branch_count = len(conv["branches"]) + 1
        new_branch_id = _make_id()
        conv["branches"][new_branch_id] = _make_branch(
            branch_id=new_branch_id,
            label=f"branch {branch_count}",
            path=list(trunk),
            fork_msg_id=from_msg_id,
        )

        conv["active_branch_id"] = new_branch_id
        conv["updated_at"] = _now_iso()
        self._write_conv(conv)
        return self._with_messages_view(conv)

    def get_branch_messages(self, conv_id: str, branch_id: str) -> Optional[List[Dict[str, Any]]]:
        """Get the message list for a specific branch."""
        conv = self._read_conv(conv_id)
        if conv is None or branch_id not in conv.get("branches", {}):
            return None
        branch = conv["branches"][branch_id]
        return [conv["nodes"][mid] for mid in branch["path"] if mid in conv["nodes"]]

    # ── Export ─────────────────────────────────────────────────────

    def export_markdown(self, conv_id: str) -> Optional[str]:
        """Export the active branch of a conversation to markdown format."""
        conv = self._read_conv(conv_id)
        if conv is None:
            return None

        messages = self._branch_messages(conv)
        lines = [
            f"# {conv['title']}",
            f"> Exported from CodiLay on {_now_iso()}",
            "",
        ]

        for msg in messages:
            role = msg["role"].capitalize()
            pin = " [PINNED]" if msg.get("pinned") else ""
            deep = " [Deep Agent]" if msg.get("escalated") else ""

            lines.append(f"### {role}{pin}{deep}")
            lines.append("")
            lines.append(msg["content"])
            lines.append("")

            if msg.get("sources"):
                lines.append(f"*Sources: {', '.join(msg['sources'])}*")
                lines.append("")

            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    # ── Context builder (for LLM calls) ───────────────────────────

    def build_chat_context(self, conv_id: str, max_messages: int = 20) -> List[Dict[str, str]]:
        """
        Build an LLM-ready message list from the active branch of a conversation.
        Includes pinned messages at the top for persistent context.
        """
        conv = self._read_conv(conv_id)
        if conv is None:
            return []

        # Collect project-wide pinned messages (from other conversations)
        project_pinned = []
        for fname in os.listdir(self._conv_dir):
            if not fname.endswith(".json"):
                continue
            cid = fname[:-5]
            if cid == conv_id:
                continue
            other = self._read_conv(cid)
            if other:
                for mid, node in other["nodes"].items():
                    if node.get("pinned") and node["role"] == "assistant":
                        project_pinned.append(node["content"])

        context = []

        if project_pinned:
            pinned_text = "\n\n---\n\n".join(project_pinned[:5])
            context.append(
                {
                    "role": "system",
                    "content": ("Previously established knowledge (pinned answers):\n\n" + pinned_text),
                }
            )

        branch = self._active_branch(conv)
        if branch is None:
            return context

        path = branch["path"]
        # Pinned messages in the active branch
        conv_pinned = [
            conv["nodes"][mid]
            for mid in path
            if conv["nodes"].get(mid, {}).get("pinned") and conv["nodes"][mid]["role"] == "assistant"
        ]
        recent_ids = path[-max_messages:]
        recent = [conv["nodes"][mid] for mid in recent_ids if mid in conv["nodes"]]

        pinned_ids = {m["id"] for m in conv_pinned}
        for m in conv_pinned:
            context.append({"role": m["role"], "content": m["content"]})
        for m in recent:
            if m["id"] not in pinned_ids:
                role = m.get("role", "user")
                if role not in ["user", "assistant", "system"]:
                    role = "user"
                context.append({"role": role, "content": m.get("content", "")})

        return context

    # ── Cross-session memory ──────────────────────────────────────

    def load_memory(self) -> Dict[str, Any]:
        """Load cross-session memory facts."""
        if not os.path.exists(self._memory_path):
            return {
                "facts": [],
                "preferences": {},
                "frequent_topics": {},
                "updated_at": None,
            }
        with open(self._memory_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_memory(self, memory: Dict[str, Any]):
        memory["updated_at"] = _now_iso()
        with open(self._memory_path, "w", encoding="utf-8") as f:
            json.dump(memory, f, indent=2)

    def add_memory_fact(self, fact: str, category: str = "general") -> Dict[str, Any]:
        """Add a fact to cross-session memory."""
        mem = self.load_memory()
        entry = {
            "id": _make_id(),
            "fact": fact,
            "category": category,
            "created_at": _now_iso(),
        }
        mem["facts"].append(entry)
        self.save_memory(mem)
        return entry

    def delete_memory_fact(self, fact_id: str) -> bool:
        mem = self.load_memory()
        before = len(mem["facts"])
        mem["facts"] = [f for f in mem["facts"] if f.get("id") != fact_id]
        if len(mem["facts"]) < before:
            self.save_memory(mem)
            return True
        return False

    def set_memory_preference(self, key: str, value: str) -> Dict[str, Any]:
        mem = self.load_memory()
        mem["preferences"][key] = value
        self.save_memory(mem)
        return mem

    def delete_memory_preference(self, key: str) -> bool:
        mem = self.load_memory()
        if key in mem["preferences"]:
            del mem["preferences"][key]
            self.save_memory(mem)
            return True
        return False

    def track_topic(self, topic: str):
        """Increment frequency counter for a topic."""
        mem = self.load_memory()
        topics = mem.get("frequent_topics", {})
        topics[topic] = topics.get(topic, 0) + 1
        mem["frequent_topics"] = topics
        self.save_memory(mem)

    def clear_memory(self):
        """Wipe all cross-session memory."""
        self.save_memory({"facts": [], "preferences": {}, "frequent_topics": {}, "updated_at": None})

    def build_memory_context(self) -> str:
        """Build a text summary of memory for injection into LLM context."""
        mem = self.load_memory()
        parts = []

        if mem.get("facts"):
            facts_text = "\n".join(f"- {f['fact']}" for f in mem["facts"][-20:])
            parts.append(f"Known facts about this user:\n{facts_text}")

        if mem.get("preferences"):
            prefs_text = "\n".join(f"- {k}: {v}" for k, v in mem["preferences"].items())
            parts.append(f"User preferences:\n{prefs_text}")

        if mem.get("frequent_topics"):
            sorted_topics = sorted(mem["frequent_topics"].items(), key=lambda x: x[1], reverse=True)[:5]
            topics_text = "\n".join(f"- {t} (asked {c} times)" for t, c in sorted_topics)
            parts.append(f"Frequently asked topics:\n{topics_text}")

        return "\n\n".join(parts) if parts else ""

    # ── Message retrieval ─────────────────────────────────────────

    def get_message(self, conv_id: str, msg_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific message node from a conversation."""
        conv = self._read_conv(conv_id)
        if conv is None:
            return None
        return conv["nodes"].get(msg_id)

    def get_preceding_question(self, conv_id: str, msg_id: str) -> Optional[str]:
        """Find the user question that preceded a given assistant message (in active branch)."""
        conv = self._read_conv(conv_id)
        if conv is None:
            return None
        branch = self._active_branch(conv)
        if branch is None or msg_id not in branch["path"]:
            return None
        idx = branch["path"].index(msg_id)
        for i in range(idx - 1, -1, -1):
            node = conv["nodes"].get(branch["path"][i])
            if node and node["role"] == "user":
                return node["content"]
        return None

    # ── Promote to doc ────────────────────────────────────────────

    def promote_to_doc(self, conv_id: str, msg_id: str, docstore, llm_client) -> Optional[str]:
        """
        Promote a chat answer to a documentation section.

        1. Gets the message and its preceding question
        2. Asks LLM to reformat as a doc section
        3. Adds the section to DocStore
        4. Marks the message as promoted

        Returns the section_id on success, None on failure.
        """
        from codilay.prompts import promote_to_doc_prompt

        msg = self.get_message(conv_id, msg_id)
        if msg is None or msg["role"] != "assistant":
            return None

        question = self.get_preceding_question(conv_id, msg_id) or "N/A"

        prompt = promote_to_doc_prompt(question, msg["content"])
        result = llm_client.call(
            "You reformat chat Q&A into documentation sections. Return only valid JSON.",
            prompt,
        )

        if "error" in result:
            return None

        section_id = result.get("id", _slugify(result.get("title", "chat-note")))
        title = result.get("title", "From Chat")
        content = result.get("content", msg["content"])
        tags = result.get("tags", ["from-chat"])

        if "from-chat" not in tags:
            tags.append("from-chat")

        docstore.add_section(
            section_id=section_id,
            title=title,
            content=content,
            tags=tags,
            file="",
        )

        # Mark the message as promoted
        conv = self._read_conv(conv_id)
        if conv and msg_id in conv["nodes"]:
            conv["nodes"][msg_id]["promoted_to"] = section_id
            conv["updated_at"] = _now_iso()
            self._write_conv(conv)

        return section_id

    # ── Memory auto-extraction ────────────────────────────────────

    def extract_and_store_memory(self, conv_id: str, llm_client) -> int:
        """
        Run LLM-powered memory extraction on the active branch of a conversation.
        Extracts facts, preferences, and topics, then stores them.

        Returns the number of new facts added.
        """
        from codilay.prompts import memory_extraction_prompt

        conv = self._read_conv(conv_id)
        if conv is None:
            return 0

        messages = self._branch_messages(conv)
        if len(messages) < 2:
            return 0

        prompt = memory_extraction_prompt(messages)
        result = llm_client.call(
            "You extract memorable facts from conversations. Return only valid JSON.",
            prompt,
        )

        if "error" in result:
            return 0

        added = 0

        facts = result.get("facts", [])
        for fact_data in facts:
            if isinstance(fact_data, dict) and fact_data.get("fact"):
                self.add_memory_fact(
                    fact=fact_data["fact"],
                    category=fact_data.get("category", "general"),
                )
                added += 1

        preferences = result.get("preferences", {})
        for key, value in preferences.items():
            if key and value:
                self.set_memory_preference(key, str(value))

        topics = result.get("topics", [])
        for topic in topics:
            if isinstance(topic, str) and topic:
                self.track_topic(topic)

        return added

    # ── Private helpers ───────────────────────────────────────────

    def _conv_path(self, conv_id: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "", conv_id)
        return os.path.join(self._conv_dir, f"{safe}.json")

    def _read_conv(self, conv_id: str) -> Optional[Dict[str, Any]]:
        path = self._conv_path(conv_id)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Migrate old flat-message format to tree format
        if data.get("format") != "tree" and "messages" in data:
            data = self._migrate_flat_to_tree(data)
            self._write_conv(data)  # persist migration
        return data

    def _write_conv(self, conv: Dict[str, Any]):
        path = self._conv_path(conv["id"])
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(conv, f, indent=2)
        os.replace(tmp, path)

    def _active_branch(self, conv: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        active_id = conv.get("active_branch_id", "main")
        return conv.get("branches", {}).get(active_id)

    def _branch_messages(self, conv: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return the ordered message list for the active branch."""
        branch = self._active_branch(conv)
        if branch is None:
            return []
        return [conv["nodes"][mid] for mid in branch["path"] if mid in conv["nodes"]]

    def _with_messages_view(self, conv: Dict[str, Any]) -> Dict[str, Any]:
        """Add a 'messages' list (active branch, in order) to the conversation dict."""
        messages = self._branch_messages(conv)
        return {**conv, "messages": messages}

    def _migrate_flat_to_tree(self, conv: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a legacy flat-message conversation to tree format."""
        messages = conv.get("messages", [])
        nodes: Dict[str, Any] = {}
        path: List[str] = []

        for i, msg in enumerate(messages):
            msg_id = msg["id"]
            children: List[str] = []
            if i < len(messages) - 1:
                children = [messages[i + 1]["id"]]
            nodes[msg_id] = {**msg, "children": children}
            path.append(msg_id)

        return {
            "id": conv["id"],
            "title": conv["title"],
            "created_at": conv["created_at"],
            "updated_at": conv["updated_at"],
            "format": "tree",
            "visibility": conv.get("visibility", "private"),
            "owner": conv.get("owner"),
            "nodes": nodes,
            "branches": {
                "main": _make_branch("main", "main", path=path, fork_msg_id=None),
            },
            "active_branch_id": "main",
            "parent_conv_id": conv.get("parent_conv_id"),
            "branch_point_msg_id": conv.get("branch_point_msg_id"),
        }

    def _auto_title(self, text: str) -> str:
        """Generate a short title from the first user message."""
        clean = text.strip().split("\n")[0][:80]
        if len(clean) > 60:
            clean = clean[:57] + "..."
        return clean or "New conversation"

    def _preview(self, conv: Dict[str, Any]) -> str:
        """Last user message in active branch as preview."""
        branch = self._active_branch(conv)
        if branch is None:
            return ""
        for mid in reversed(branch["path"]):
            node = conv["nodes"].get(mid)
            if node and node["role"] == "user":
                text = node["content"][:100]
                return text + "..." if len(node["content"]) > 100 else text
        return ""
