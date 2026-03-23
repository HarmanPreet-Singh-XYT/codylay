import shutil
import tempfile

import pytest

from codilay.chatstore import ChatStore, make_conversation, make_message


@pytest.fixture
def chat_store():
    tmpdir = tempfile.mkdtemp()
    store = ChatStore(tmpdir)
    yield store
    shutil.rmtree(tmpdir)


def test_chatstore_create_list(chat_store):
    conv = chat_store.create_conversation("Test Convo")
    assert conv["title"] == "Test Convo"
    assert "id" in conv

    convs = chat_store.list_conversations()
    assert len(convs) == 1
    assert convs[0]["id"] == conv["id"]


def test_chatstore_add_message(chat_store):
    conv = chat_store.create_conversation("")
    msg = make_message("user", "Hello world")
    chat_store.add_message(conv["id"], msg)

    updated = chat_store.get_conversation(conv["id"])
    assert len(updated["messages"]) == 1
    assert updated["messages"][0]["content"] == "Hello world"
    # Auto-title check
    assert updated["title"] == "Hello world"


def test_chatstore_edit_message(chat_store):
    conv = chat_store.create_conversation("Title")
    chat_store.add_message(conv["id"], make_message("user", "m1"))
    m2 = make_message("assistant", "m2")
    chat_store.add_message(conv["id"], m2)
    chat_store.add_message(conv["id"], make_message("user", "m3"))

    # Edit m2 — creates a new branch from m1→new_m2; original m1→m2→m3 is preserved
    chat_store.edit_message(conv["id"], m2["id"], "new m2")

    # Active branch now has m1 + new_m2 (2 messages; m3 is on the original branch)
    updated = chat_store.get_conversation(conv["id"])
    assert len(updated["messages"]) == 2
    assert updated["messages"][1]["content"] == "new m2"

    # Two branches should exist: original "main" and the new branch
    branches = chat_store.list_branches(conv["id"])
    assert branches is not None
    assert len(branches) == 2

    # Switch back to main branch and verify m3 is still there
    main_branch = next(b for b in branches if b["id"] == "main")
    chat_store.switch_branch(conv["id"], main_branch["id"])
    original = chat_store.get_conversation(conv["id"])
    assert len(original["messages"]) == 3
    assert original["messages"][2]["content"] == "m3"


def test_chatstore_memory(chat_store):
    chat_store.add_memory_fact("The user likes Python")
    mem = chat_store.load_memory()
    assert len(mem["facts"]) == 1
    assert mem["facts"][0]["fact"] == "The user likes Python"

    ctx = chat_store.build_memory_context()
    assert "The user likes Python" in ctx


def test_chatstore_visibility_filtering(chat_store):
    chat_store.create_conversation("Alice private", visibility="private", owner="alice")
    chat_store.create_conversation("Bob private", visibility="private", owner="bob")
    chat_store.create_conversation("Team convo", visibility="team", owner="alice")

    # No filter — all 3
    assert len(chat_store.list_conversations()) == 3

    # Alice sees her private + team
    alice_convs = chat_store.list_conversations(user="alice", include_team=True)
    titles = {c["title"] for c in alice_convs}
    assert "Alice private" in titles
    assert "Team convo" in titles
    assert "Bob private" not in titles

    # Bob sees his private + team
    bob_convs = chat_store.list_conversations(user="bob", include_team=True)
    titles = {c["title"] for c in bob_convs}
    assert "Bob private" in titles
    assert "Team convo" in titles
    assert "Alice private" not in titles

    # Alice, no team
    alice_only = chat_store.list_conversations(user="alice", include_team=False)
    titles = {c["title"] for c in alice_only}
    assert "Alice private" in titles
    assert "Team convo" not in titles


def test_chatstore_branch_operations(chat_store):
    """Branch creation, listing, switching, renaming, and per-branch message access."""
    conv = chat_store.create_conversation("Branching test")
    m1 = make_message("user", "hello")
    chat_store.add_message(conv["id"], m1)
    m2 = make_message("assistant", "hi there")
    chat_store.add_message(conv["id"], m2)
    m3 = make_message("user", "what is auth?")
    chat_store.add_message(conv["id"], m3)

    # Initially one branch
    branches = chat_store.list_branches(conv["id"])
    assert len(branches) == 1
    assert branches[0]["id"] == "main"
    assert branches[0]["is_active"] is True

    # Branch from m2 — new empty branch after m2
    result = chat_store.branch_conversation(conv["id"], m2["id"])
    assert result is not None
    branches = chat_store.list_branches(conv["id"])
    assert len(branches) == 2

    # Active branch has 2 messages (m1, m2); m3 is only on main
    updated = chat_store.get_conversation(conv["id"])
    assert len(updated["messages"]) == 2
    assert updated["messages"][-1]["content"] == "hi there"

    # Switch back to main
    new_branch_id = next(b["id"] for b in branches if b["id"] != "main")
    chat_store.switch_branch(conv["id"], "main")
    on_main = chat_store.get_conversation(conv["id"])
    assert len(on_main["messages"]) == 3

    # Get messages for the other branch directly
    other_msgs = chat_store.get_branch_messages(conv["id"], new_branch_id)
    assert len(other_msgs) == 2
    assert other_msgs[-1]["content"] == "hi there"

    # Rename the new branch
    assert chat_store.rename_branch(conv["id"], new_branch_id, "auth investigation")
    branches = chat_store.list_branches(conv["id"])
    renamed = next(b for b in branches if b["id"] == new_branch_id)
    assert renamed["label"] == "auth investigation"

    # Switch to bad branch → None
    assert chat_store.switch_branch(conv["id"], "nonexistent") is None


def test_chatstore_migration(chat_store):
    """Old flat-message conversations are auto-migrated to tree format on read."""
    import json
    import os

    conv_id = "flattest123456"
    flat_conv = {
        "id": conv_id,
        "title": "Old flat conversation",
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "messages": [
            {
                "id": "a1b2c3d4e5f6",
                "role": "user",
                "content": "old question",
                "sources": [],
                "confidence": None,
                "escalated": False,
                "pinned": False,
                "parent_id": None,
                "created_at": "2024-01-01T00:00:00+00:00",
            },
            {
                "id": "f6e5d4c3b2a1",
                "role": "assistant",
                "content": "old answer",
                "sources": [],
                "confidence": 0.9,
                "escalated": False,
                "pinned": False,
                "parent_id": None,
                "created_at": "2024-01-01T00:00:01+00:00",
            },
        ],
        "parent_conv_id": None,
        "branch_point_msg_id": None,
    }
    # Write directly as old format
    path = os.path.join(chat_store._conv_dir, f"{conv_id}.json")
    with open(path, "w") as f:
        json.dump(flat_conv, f)

    # Reading should auto-migrate
    conv = chat_store.get_conversation(conv_id)
    assert conv is not None
    assert conv.get("format") == "tree"
    assert "nodes" in conv
    assert "branches" in conv
    assert "main" in conv["branches"]
    assert len(conv["messages"]) == 2
    assert conv["messages"][0]["content"] == "old question"
    assert conv["messages"][1]["content"] == "old answer"

    # Branches and nodes should be consistent
    branches = chat_store.list_branches(conv_id)
    assert len(branches) == 1
    assert branches[0]["message_count"] == 2

    # File should now be written in tree format
    with open(path) as f:
        saved = json.load(f)
    assert saved.get("format") == "tree"


def test_chatstore_update_visibility(chat_store):
    conv = chat_store.create_conversation("Test", visibility="private", owner="alice")
    assert conv["visibility"] == "private"

    updated = chat_store.update_visibility(conv["id"], "team")
    assert updated["visibility"] == "team"
    assert updated["owner"] == "alice"  # owner unchanged

    updated2 = chat_store.update_visibility(conv["id"], "private", owner="bob")
    assert updated2["owner"] == "bob"

    # Unknown id returns None
    assert chat_store.update_visibility("doesnotexist", "team") is None


def test_chatstore_pinning(chat_store):
    conv = chat_store.create_conversation("Convo")
    m1 = make_message("assistant", "answer")
    chat_store.add_message(conv["id"], m1)

    chat_store.pin_message(conv["id"], m1["id"], True)
    pinned = chat_store.get_pinned_messages(conv["id"])
    assert len(pinned) == 1
    assert pinned[0]["content"] == "answer"


def test_chatstore_build_chat_context(chat_store):
    """build_chat_context only returns messages on the active branch path."""
    conv = chat_store.create_conversation("Context test")
    mu = make_message("user", "trunk question")
    chat_store.add_message(conv["id"], mu)
    ma = make_message("assistant", "trunk answer")
    chat_store.add_message(conv["id"], ma)
    m3 = make_message("user", "branch A question")
    chat_store.add_message(conv["id"], m3)

    # Create a branch from ma (trunk answer); original m3 stays on main
    chat_store.branch_conversation(conv["id"], ma["id"])
    m4 = make_message("user", "branch B question")
    chat_store.add_message(conv["id"], m4)

    # Active branch is the new branch → should have trunk + branch B only
    ctx = chat_store.build_chat_context(conv["id"])
    contents = [m["content"] for m in ctx if m["role"] in ("user", "assistant")]
    assert "trunk question" in contents
    assert "trunk answer" in contents
    assert "branch B question" in contents
    assert "branch A question" not in contents  # on main, not active branch

    # Switch to main and re-check
    chat_store.switch_branch(conv["id"], "main")
    ctx_main = chat_store.build_chat_context(conv["id"])
    contents_main = [m["content"] for m in ctx_main if m["role"] in ("user", "assistant")]
    assert "branch A question" in contents_main
    assert "branch B question" not in contents_main


def test_chatstore_get_preceding_question(chat_store):
    """get_preceding_question walks the active branch path."""
    conv = chat_store.create_conversation("Preceding Q test")
    mu = make_message("user", "what is X?")
    chat_store.add_message(conv["id"], mu)
    ma = make_message("assistant", "X is Y")
    chat_store.add_message(conv["id"], ma)

    q = chat_store.get_preceding_question(conv["id"], ma["id"])
    assert q == "what is X?"

    # No preceding question if message is first
    q2 = chat_store.get_preceding_question(conv["id"], mu["id"])
    assert q2 is None

    # Unknown message → None
    assert chat_store.get_preceding_question(conv["id"], "bad_id") is None


def test_chatstore_multiple_sibling_branches(chat_store):
    """Two edits of the same message from main create two independent sibling branches."""
    conv = chat_store.create_conversation("Siblings")
    m1 = make_message("user", "hello")
    chat_store.add_message(conv["id"], m1)
    m2 = make_message("assistant", "hi")
    chat_store.add_message(conv["id"], m2)
    m3 = make_message("user", "original follow-up")
    chat_store.add_message(conv["id"], m3)

    # First edit of m3 → branch A
    chat_store.edit_message(conv["id"], m3["id"], "edit A")
    branch_a_id = chat_store.get_conversation(conv["id"])["active_branch_id"]

    # Switch back to main, make second edit of m3 → branch B
    chat_store.switch_branch(conv["id"], "main")
    chat_store.edit_message(conv["id"], m3["id"], "edit B")
    branch_b_id = chat_store.get_conversation(conv["id"])["active_branch_id"]

    assert branch_a_id != branch_b_id  # distinct branches
    branches = chat_store.list_branches(conv["id"])
    assert len(branches) == 3  # main + A + B

    msgs_a = chat_store.get_branch_messages(conv["id"], branch_a_id)
    msgs_b = chat_store.get_branch_messages(conv["id"], branch_b_id)
    assert msgs_a[-1]["content"] == "edit A"
    assert msgs_b[-1]["content"] == "edit B"

    # Both share the same trunk (m1, m2)
    assert msgs_a[0]["content"] == "hello"
    assert msgs_b[0]["content"] == "hello"


def test_chatstore_export_markdown(chat_store):
    """export_markdown uses the active branch messages."""
    conv = chat_store.create_conversation("Export test")
    chat_store.add_message(conv["id"], make_message("user", "user msg"))
    chat_store.add_message(conv["id"], make_message("assistant", "bot msg"))

    md = chat_store.export_markdown(conv["id"])
    assert md is not None
    assert "user msg" in md
    assert "bot msg" in md
    assert "Export test" in md

    # After branching, export only shows active branch
    m2 = chat_store.get_conversation(conv["id"])["messages"][1]
    chat_store.branch_conversation(conv["id"], m2["id"])
    chat_store.add_message(conv["id"], make_message("user", "branch only msg"))

    md_branch = chat_store.export_markdown(conv["id"])
    assert "branch only msg" in md_branch

    # Main branch export should not have branch-only content
    chat_store.switch_branch(conv["id"], "main")
    md_main = chat_store.export_markdown(conv["id"])
    assert "branch only msg" not in md_main
