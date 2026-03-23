"""
CodiLay Web UI Server — three-layer codebase intelligence interface.

Layer 1: Reader     — renders CODEBASE.md with sidebar nav + dependency graph
Layer 2: Chatbot    — answers questions from the doc context
Layer 3: Deep Agent — reads source files when the doc can't answer

Usage:
    codilay serve .
    codilay serve /path/to/project
    codilay serve . --port 8484
"""

import asyncio
import json
import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from codilay.chatstore import ChatStore, make_message
from codilay.config import CodiLayConfig
from codilay.retriever import Retriever
from codilay.settings import Settings
from codilay.state import AgentState

# ── Data models ───────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    question: str
    conversation_id: Optional[str] = None  # None = new conversation
    deep: bool = False  # Force deep agent mode


class ChatResponse(BaseModel):
    answer: str
    sources: List[str]
    confidence: float
    escalated: bool  # Whether the deep agent was used
    conversation_id: str = ""
    message_id: str = ""


# ── Server factory ────────────────────────────────────────────────────────────


def create_app(
    target_path: str,
    output_dir: Optional[str] = None,
) -> FastAPI:
    """
    Build a FastAPI app wired to a specific project's codilay output.
    """
    target_path = os.path.abspath(target_path)
    if output_dir is None:
        output_dir = os.path.join(target_path, "codilay")

    # Support both old (.codylay_state.json) and new (.codilay_state.json) names
    state_path = os.path.join(output_dir, ".codilay_state.json")
    if not os.path.exists(state_path):
        alt = os.path.join(output_dir, ".codylay_state.json")
        if os.path.exists(alt):
            state_path = alt
    codebase_md_path = os.path.join(output_dir, "CODEBASE.md")
    links_path = os.path.join(output_dir, "links.json")

    # ── Validate output exists ────────────────────────────────────
    if not os.path.exists(codebase_md_path):
        raise FileNotFoundError(
            f"No CODEBASE.md found at {codebase_md_path}. Run 'codilay {target_path}' first to generate documentation."
        )

    app = FastAPI(
        title="CodiLay",
        description="Codebase intelligence interface",
    )

    # ── Shared state (loaded lazily, cached) ──────────────────────

    _cache: Dict[str, Any] = {}
    chat_store = ChatStore(output_dir)

    def _load_state() -> AgentState:
        if "state" not in _cache or _file_changed(state_path, "_state_mtime"):
            _cache["state"] = AgentState.load(state_path)
        return _cache["state"]

    def _load_links() -> Dict:
        if "links" not in _cache or _file_changed(links_path, "_links_mtime"):
            with open(links_path, "r", encoding="utf-8") as f:
                _cache["links"] = json.load(f)
        return _cache["links"]

    def _load_codebase_md() -> str:
        if "codebase_md" not in _cache or _file_changed(codebase_md_path, "_md_mtime"):
            with open(codebase_md_path, "r", encoding="utf-8") as f:
                _cache["codebase_md"] = f.read()
        return _cache["codebase_md"]

    def _load_retriever() -> Retriever:
        """Build/rebuild the retriever when state changes."""
        if "retriever" not in _cache or _file_changed(state_path, "_retriever_mtime"):
            state = _load_state()
            _cache["retriever"] = Retriever(state.section_index, state.section_contents)
        return _cache["retriever"]

    def _file_changed(path: str, mtime_key: str) -> bool:
        try:
            mtime = os.path.getmtime(path)
            if _cache.get(mtime_key) != mtime:
                _cache[mtime_key] = mtime
                return True
            return False
        except OSError:
            return True

    # ── Layer 1: Reader endpoints ─────────────────────────────────

    # Static files (CSS, JS) from the web directory
    web_dir = os.path.join(os.path.dirname(__file__), "web")
    if os.path.exists(web_dir):
        app.mount("/static", StaticFiles(directory=web_dir), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        """Serve the single-page web UI."""
        return _get_frontend_html()

    @app.get("/api/sections")
    async def get_sections():
        """Return the section index for sidebar navigation."""
        state = _load_state()
        sections = []
        for sid, meta in state.section_index.items():
            content = state.section_contents.get(sid, "")
            sections.append(
                {
                    "id": sid,
                    "title": meta.get("title", sid),
                    "file": meta.get("file", ""),
                    "tags": meta.get("tags", []),
                    "content": content,
                }
            )
        return {"sections": sections, "project": os.path.basename(target_path)}

    @app.get("/api/document")
    async def get_document():
        """Return the full CODEBASE.md content."""
        md = _load_codebase_md()
        return {"markdown": md}

    @app.get("/api/links")
    async def get_links():
        """Return the dependency graph data."""
        links = _load_links()
        return links

    @app.get("/api/stats")
    async def get_stats():
        """Return project documentation stats."""
        state = _load_state()
        links = _load_links()
        return {
            "project": os.path.basename(target_path),
            "target_path": target_path,
            "files_processed": len(state.processed),
            "sections": len(state.section_index),
            "closed_wires": len(links.get("closed", [])),
            "open_wires": len(links.get("open", [])),
            "last_commit": state.last_commit_short,
            "last_run": state.last_run,
        }

    # ── Conversation management endpoints ──────────────────────────

    @app.get("/api/conversations")
    async def list_conversations():
        """List all conversations, most recent first."""
        return {"conversations": chat_store.list_conversations()}

    @app.post("/api/conversations")
    async def create_conversation(title: str = ""):
        """Create a new conversation."""
        conv = chat_store.create_conversation(title=title)
        return conv

    @app.get("/api/conversations/{conv_id}")
    async def get_conversation(conv_id: str):
        """Get a full conversation with all messages."""
        conv = chat_store.get_conversation(conv_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return conv

    @app.delete("/api/conversations/{conv_id}")
    async def delete_conversation(conv_id: str):
        """Delete a conversation."""
        if chat_store.delete_conversation(conv_id):
            return {"deleted": True}
        raise HTTPException(status_code=404, detail="Conversation not found")

    @app.patch("/api/conversations/{conv_id}/title")
    async def update_conv_title(conv_id: str, title: str):
        """Update a conversation title."""
        conv = chat_store.update_title(conv_id, title)
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return conv

    # ── Message control endpoints ─────────────────────────────────

    @app.post("/api/conversations/{conv_id}/messages/{msg_id}/pin")
    async def toggle_pin(conv_id: str, msg_id: str, pinned: bool = True):
        """Pin or unpin a message."""
        if chat_store.pin_message(conv_id, msg_id, pinned):
            return {"pinned": pinned}
        raise HTTPException(status_code=404, detail="Message not found")

    @app.post("/api/conversations/{conv_id}/messages/{msg_id}/edit")
    async def edit_message(conv_id: str, msg_id: str, content: str = ""):
        """Edit a message and truncate everything after it."""
        if not content:
            raise HTTPException(status_code=400, detail="Content is required")
        conv = chat_store.edit_message(conv_id, msg_id, content)
        if conv is None:
            raise HTTPException(status_code=404, detail="Message not found")
        return conv

    @app.post("/api/conversations/{conv_id}/branch/{msg_id}")
    async def branch_conversation(conv_id: str, msg_id: str):
        """Create a new conversation branching from a specific message."""
        branch = chat_store.branch_conversation(conv_id, msg_id)
        if branch is None:
            raise HTTPException(status_code=404, detail="Conversation or message not found")
        return branch

    @app.get("/api/conversations/{conv_id}/export")
    async def export_conversation(conv_id: str):
        """Export a conversation to markdown."""
        md = chat_store.export_markdown(conv_id)
        if md is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return {"markdown": md}

    @app.get("/api/conversations/{conv_id}/pinned")
    async def get_conv_pinned_messages(conv_id: str):
        """Get pinned messages for a conversation."""
        return {"pinned": chat_store.get_pinned_messages(conv_id)}

    @app.get("/api/pinned")
    async def get_all_pinned_messages():
        """Get all pinned messages across all conversations."""
        return {"pinned": chat_store.get_pinned_messages()}

    # ── Promote to doc endpoint ───────────────────────────────────

    @app.post("/api/conversations/{conv_id}/messages/{msg_id}/promote")
    async def promote_message(conv_id: str, msg_id: str):
        """Promote a chat answer to a documentation section."""
        try:
            settings = Settings.load()
            settings.inject_env_vars()
            cfg = CodiLayConfig(target_path=target_path)
            cfg.llm_provider = settings.default_provider
            cfg.llm_model = settings.default_model
            if settings.custom_base_url:
                cfg.llm_base_url = settings.custom_base_url

            from codilay.docstore import DocStore
            from codilay.llm_client import LLMClient

            llm = LLMClient(cfg)

            # Load docstore from state
            state = _load_state()
            docstore = DocStore()
            docstore.load_from_state(state.section_index, state.section_contents)

            section_id = await asyncio.to_thread(chat_store.promote_to_doc, conv_id, msg_id, docstore, llm)

            if section_id is None:
                raise HTTPException(
                    status_code=400,
                    detail="Could not promote message (must be an assistant message)",
                )

            # Re-render CODEBASE.md with the new section
            final_md = docstore.render_full_document()
            with open(codebase_md_path, "w", encoding="utf-8") as f:
                f.write(final_md)

            # Update state
            state.section_index = docstore.get_section_index()
            state.section_contents = docstore.get_section_contents()
            state.save(state_path)

            # Invalidate caches
            _cache.pop("codebase_md", None)
            _cache.pop("state", None)
            _cache.pop("retriever", None)

            return {"promoted": True, "section_id": section_id}

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── Memory management endpoints ───────────────────────────────

    @app.get("/api/memory")
    async def get_memory():
        """Get all cross-session memory."""
        return chat_store.load_memory()

    @app.delete("/api/memory")
    async def clear_all_memory():
        """Clear all cross-session memory."""
        chat_store.clear_memory()
        return {"cleared": True}

    @app.delete("/api/memory/facts/{fact_id}")
    async def delete_memory_fact(fact_id: str):
        """Delete a specific memory fact."""
        if chat_store.delete_memory_fact(fact_id):
            return {"deleted": True}
        raise HTTPException(status_code=404, detail="Fact not found")

    @app.delete("/api/memory/preferences/{key}")
    async def delete_memory_pref(key: str):
        """Delete a specific memory preference."""
        if chat_store.delete_memory_preference(key):
            return {"deleted": True}
        raise HTTPException(status_code=404, detail="Preference not found")

    # ── Layer 2: Chatbot endpoint (conversation-aware) ────────────

    @app.post("/api/chat")
    async def chat(req: ChatRequest):
        """
        Answer a question about the codebase with full conversation support.

        Flow:
        1. Create or resume a conversation
        2. Retrieve relevant doc sections via TF-IDF (token-efficient)
        3. Inject memory context and pinned messages
        4. Ask LLM with targeted context
        5. If confidence < threshold or req.deep, escalate to deep agent
        6. Persist both user and assistant messages
        7. Track topics for memory
        """
        state = _load_state()
        retriever = _load_retriever()
        question = req.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="Question is required")

        # ── Get or create conversation ────────────────────────────
        conv_id = req.conversation_id
        if conv_id:
            conv = chat_store.get_conversation(conv_id)
            if conv is None:
                raise HTTPException(status_code=404, detail="Conversation not found")
        else:
            conv = chat_store.create_conversation()
            conv_id = conv["id"]

        # ── Persist user message ──────────────────────────────────
        user_msg = make_message("user", question)
        chat_store.add_message(conv_id, user_msg)

        # ── Retrieve relevant sections (TF-IDF) ──────────────────
        relevant_sections = retriever.search(question, top_k=5)

        # ── Build memory + pinned context ─────────────────────────
        memory_ctx = chat_store.build_memory_context()
        pinned_msgs = chat_store.get_pinned_messages(conv_id)
        pinned_ctx = ""
        if pinned_msgs:
            pinned_ctx = "\n\n".join(f"- {m['content'][:200]}" for m in pinned_msgs[:5])

        # ── Build conversation history ────────────────────────────
        chat_context = chat_store.build_chat_context(conv_id, max_messages=10)
        history_text = ""
        if len(chat_context) > 1:  # more than just the current message
            history_lines = []
            for cm in chat_context[:-1]:  # exclude current message
                role = cm["role"].capitalize()
                content = cm["content"][:300]
                history_lines.append(f"{role}: {content}")
            history_text = "\n".join(history_lines[-6:])  # last 3 exchanges

        # Check for explicit intent to see code
        def _should_escalate_by_keyword(question: str) -> bool:
            q = question.lower()
            deep_patterns = [
                "show me the code",
                "show the code",
                "exactly how",
                "line by line",
                "implementation detail",
                "source code",
                "what does the code",
                "read the file",
                "look at the file",
                "open the file",
                "specific implementation",
                "actual code",
            ]
            return any(p in q for p in deep_patterns)

        force_deep = req.deep or _should_escalate_by_keyword(question)

        if not force_deep and relevant_sections:
            # Try answering from doc context first
            print(f"🤖 Layer 2: Chatbot answering from {len(relevant_sections)} doc sections...")
            answer, confidence, sources = await _chatbot_answer(
                question, relevant_sections, memory_ctx, pinned_ctx, history_text
            )
            print(f"✅ Chatbot confidence: {confidence:.2f}")

            if confidence >= 0.7 and answer.strip():
                # Persist assistant message
                asst_msg = make_message(
                    "assistant",
                    answer,
                    sources=sources,
                    confidence=confidence,
                )
                chat_store.add_message(conv_id, asst_msg)

                # Track topic
                chat_store.track_topic(relevant_sections[0].title if relevant_sections else question[:50])

                return ChatResponse(
                    answer=answer,
                    sources=sources,
                    confidence=confidence,
                    escalated=False,
                    conversation_id=conv_id,
                    message_id=asst_msg["id"],
                )
            else:
                print(f"⚠️ Layer 2 low confidence ({confidence:.2f}) or empty answer. Escalating...")

        # ── Escalate to deep agent ────────────────────────────────
        print(f"🔍 Layer 3: Escalating to Deep Agent (found {len(relevant_sections)} relevant sections)...")
        answer, sources = await _deep_agent_answer(question, relevant_sections, state, target_path, history_text)
        print(f"✅ Deep Agent generated answer from {len(sources)} files.")

        asst_msg = make_message(
            "assistant",
            answer,
            sources=sources,
            confidence=1.0,
            escalated=True,
        )
        chat_store.add_message(conv_id, asst_msg)

        return ChatResponse(
            answer=answer,
            sources=sources,
            confidence=1.0,
            escalated=True,
            conversation_id=conv_id,
            message_id=asst_msg["id"],
        )

    # ── Layer 2: Chatbot internals ────────────────────────────────

    def _should_escalate_by_keyword(question: str) -> bool:
        """Check if the question pattern demands source code reading."""
        q = question.lower()
        deep_patterns = [
            "show me the code",
            "show the code",
            "exactly how",
            "line by line",
            "implementation detail",
            "source code",
            "what does the code",
            "read the file",
            "look at the file",
            "open the file",
            "specific implementation",
            "actual code",
        ]
        return any(p in q for p in deep_patterns)

    async def _chatbot_answer(
        question: str,
        relevant_sections,
        memory_ctx: str,
        pinned_ctx: str,
        history_text: str,
    ) -> tuple:
        """
        Use LLM to answer from doc context.
        Returns (answer, confidence, sources).
        """
        try:
            settings = Settings.load()
            settings.inject_env_vars()
            cfg = CodiLayConfig(target_path=target_path)
            cfg.llm_provider = settings.default_provider
            cfg.llm_model = settings.default_model
            if settings.custom_base_url:
                cfg.llm_base_url = settings.custom_base_url

            from codilay.llm_client import LLMClient
            from codilay.prompts import chat_system_prompt, chat_user_prompt

            llm = LLMClient(cfg)

            # Build context from relevant sections (token-efficient)
            context_parts = []
            for sec in relevant_sections:
                context_parts.append(sec.formatted)
            doc_context = "\n\n---\n\n".join(context_parts)

            system = chat_system_prompt(
                memory_context=memory_ctx,
                pinned_context=pinned_ctx,
            )

            user = chat_user_prompt(
                question=question,
                doc_context=doc_context,
                conversation_history=history_text,
            )

            # Plain text call — we want free-form response, not JSON
            raw_text = await asyncio.to_thread(llm._raw_call_with_rate_limit, system, user, json_mode=False)

            # Extract confidence and clean answer
            confidence = 0.5
            lines = raw_text.strip().split("\n")
            answer_lines = []
            for line in lines:
                clean = line.strip()
                if clean.startswith("CONFIDENCE:"):
                    try:
                        confidence = float(clean.split("CONFIDENCE:")[1].strip())
                    except (ValueError, IndexError):
                        pass
                elif clean:  # Ignore empty lines
                    answer_lines.append(line)

            final_answer = "\n".join(answer_lines).strip()
            # Safety: if the answer is just the confidence or too short, return 0 confidence
            if len(final_answer) < 5:
                confidence = 0.0

            sources = [sec.section_id for sec in relevant_sections]
            return final_answer, confidence, sources

        except Exception as e:
            return f"I couldn't process that question: {str(e)}", 0.0, []

    # ── Layer 3: Deep Agent internals ─────────────────────────────

    async def _deep_agent_answer(
        question: str,
        relevant_sections,
        state: AgentState,
        project_path: str,
        history_text: str = "",
    ) -> tuple:
        """
        Deep agent: reads actual source files to answer precisely.
        Uses the Retriever to pick the most relevant files.
        Returns (answer, source_list).
        """
        try:
            settings = Settings.load()
            settings.inject_env_vars()
            cfg = CodiLayConfig(target_path=project_path)
            cfg.llm_provider = settings.default_provider
            cfg.llm_model = settings.default_model
            if settings.custom_base_url:
                cfg.llm_base_url = settings.custom_base_url

            from codilay.llm_client import LLMClient

            llm = LLMClient(cfg)
            retriever = _load_retriever()

            # Use retriever to find most relevant files
            file_candidates = set(retriever.get_source_files(question, top_k=5))

            # Also add files from the relevant sections
            for sec in relevant_sections:
                if sec.file:
                    file_candidates.add(sec.file)

            # Also scan for keyword matches as fallback
            q_lower = question.lower()
            for sid, meta in state.section_index.items():
                file_ref = meta.get("file", "")
                if file_ref:
                    parts = file_ref.replace("/", " ").replace(".", " ").lower().split()
                    if any(p in q_lower for p in parts if len(p) > 2):
                        file_candidates.add(file_ref)

            # Read the actual source files
            file_contents = {}
            for fpath in list(file_candidates)[:5]:  # Limit to 5 files
                full = os.path.join(project_path, fpath)
                if os.path.exists(full) and os.path.isfile(full):
                    try:
                        with open(full, "r", encoding="utf-8", errors="replace") as fh:
                            content = fh.read()
                        if len(content) > 10000:
                            content = content[:10000] + "\n\n... [truncated]"
                        file_contents[fpath] = content
                    except Exception:
                        pass

            if not file_contents:
                # Broader fallback search
                for fpath in state.processed[:10]:
                    parts = fpath.replace("/", " ").replace(".", " ").lower().split()
                    if any(p in q_lower for p in parts if len(p) > 2):
                        full = os.path.join(project_path, fpath)
                        if os.path.exists(full):
                            try:
                                with open(full, "r", encoding="utf-8", errors="replace") as fh:
                                    content = fh.read()
                                if len(content) > 10000:
                                    content = content[:10000] + "\n\n... [truncated]"
                                file_contents[fpath] = content
                            except Exception:
                                pass
                    if len(file_contents) >= 5:
                        break

            if not file_contents:
                return (
                    "I couldn't find relevant source files to answer this question. "
                    "Try rephrasing with specific file names or module names.",
                    [],
                )

            # Build source context
            source_parts = []
            for fpath, content in file_contents.items():
                source_parts.append(f"### File: {fpath}\n```\n{content}\n```")
            source_context = "\n\n".join(source_parts)

            # Doc context from retriever (token-efficient)
            doc_context = ""
            if relevant_sections:
                doc_parts = [sec.formatted for sec in relevant_sections[:3]]
                doc_context = "Existing documentation context:\n\n" + "\n---\n".join(doc_parts) + "\n\n---\n\n"

            # Conversation history context
            history_section = ""
            if history_text:
                history_section = f"\n\nRecent conversation:\n{history_text}\n\n---\n\n"

            system = (
                "You are a deep codebase analysis agent. You have access to actual "
                "source code files. Answer the user's question with precision, "
                "referencing specific functions, classes, line ranges, and logic. "
                "Be thorough but concise. Use markdown formatting.\n"
                "IMPORTANT: Respond with PLAIN TEXT markdown only. Do NOT wrap your "
                "entire response in a JSON object or any other format."
            )

            user = f"{doc_context}{history_section}Source code:\n\n{source_context}\n\n---\n\nQuestion: {question}"

            raw_text = await asyncio.to_thread(llm._raw_call_with_rate_limit, system, user, json_mode=False)

            return raw_text.strip(), list(file_contents.keys())

        except Exception as e:
            return f"Deep analysis failed: {str(e)}", []

    # ── SSE streaming chat (optional upgrade path) ────────────────

    @app.post("/api/chat/stream")
    async def chat_stream(req: ChatRequest):
        """
        Streaming version of chat — returns SSE events.
        Falls back to non-streaming if provider doesn't support it.
        """
        response = await chat(req)

        async def event_stream():
            yield f"data: {json.dumps(response.model_dump())}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
        )

    # ── Memory extraction endpoint ────────────────────────────────

    @app.post("/api/conversations/{conv_id}/extract-memory")
    async def extract_memory(conv_id: str):
        """Run memory extraction on a conversation."""
        try:
            settings = Settings.load()
            settings.inject_env_vars()
            cfg = CodiLayConfig(target_path=target_path)
            cfg.llm_provider = settings.default_provider
            cfg.llm_model = settings.default_model
            if settings.custom_base_url:
                cfg.llm_base_url = settings.custom_base_url

            from codilay.llm_client import LLMClient

            llm = LLMClient(cfg)
            facts_added = await asyncio.to_thread(chat_store.extract_and_store_memory, conv_id, llm)
            return {"facts_added": facts_added}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── Source file viewer ────────────────────────────────────────

    @app.get("/api/file/{file_path:path}")
    async def get_file(file_path: str):
        """Read a source file from the project."""
        full_path = os.path.join(target_path, file_path)
        # Security: ensure we stay within the project
        real_target = os.path.realpath(target_path)
        real_file = os.path.realpath(full_path)
        if not real_file.startswith(real_target):
            raise HTTPException(status_code=403, detail="Access denied")
        if not os.path.exists(full_path) or not os.path.isfile(full_path):
            raise HTTPException(status_code=404, detail="File not found")
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return {"path": file_path, "content": content}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── Feature 3: AI-friendly export ─────────────────────────────

    class ExportRequest(BaseModel):
        format: str = "markdown"  # markdown, xml, json
        max_tokens: Optional[int] = None
        include_graph: bool = True
        include_unresolved: bool = False

    @app.post("/api/export")
    async def export_for_ai(req: ExportRequest):
        """Export documentation in a compact, AI-friendly format."""
        try:
            from codilay.exporter import export_for_ai as _export_for_ai

            result = await asyncio.to_thread(
                _export_for_ai,
                output_dir=output_dir,
                fmt=req.format,
                max_tokens=req.max_tokens,
                include_graph=req.include_graph,
            )
            return {"content": result, "format": req.format, "chars": len(result)}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/export")
    async def export_for_ai_get(
        fmt: str = "markdown",
        max_tokens: Optional[int] = None,
        include_graph: bool = True,
    ):
        """GET version of export for easy curl/browser access."""
        try:
            from codilay.exporter import export_for_ai as _export_for_ai

            result = await asyncio.to_thread(
                _export_for_ai,
                output_dir=output_dir,
                fmt=fmt,
                max_tokens=max_tokens,
                include_graph=include_graph,
            )
            return {"content": result, "format": fmt, "chars": len(result)}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── Feature 4: Doc diff ───────────────────────────────────────

    @app.get("/api/doc-diff")
    async def get_doc_diff(snap1: Optional[str] = None, snap2: Optional[str] = None):
        """Get documentation changes between runs."""
        try:
            from codilay.doc_differ import DocVersionStore

            store = DocVersionStore(output_dir)

            if snap1 and snap2:
                result = store.diff_snapshots(snap1, snap2)
            else:
                snapshots = store.list_snapshots()
                if len(snapshots) < 2:
                    return {"has_changes": False, "message": "Need at least 2 snapshots", "snapshots": len(snapshots)}
                result = store.diff_latest()

            if result is None:
                raise HTTPException(status_code=500, detail="Could not compute diff")

            return result.to_dict()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/doc-diff/snapshots")
    async def list_snapshots():
        """List all documentation snapshots."""
        from codilay.doc_differ import DocVersionStore

        store = DocVersionStore(output_dir)
        return {"snapshots": store.list_snapshots()}

    # ── Diff-run endpoint ─────────────────────────────────────────

    @app.get("/api/diff-run")
    async def run_diff_analysis(
        since: Optional[str] = None,
        since_branch: Optional[str] = None,
        update_doc: bool = False,
    ):
        """
        Run diff-run analysis to document changes since a boundary.

        Parameters:
        - since: Commit hash, tag, or date (YYYY-MM-DD)
        - since_branch: Branch name (finds merge base)
        - update_doc: Whether to update CODEBASE.md with changes
        """
        from codilay.change_report import ChangeReportGenerator
        from codilay.diff_analyzer import DiffAnalyzer
        from codilay.llm_client import LLMClient
        from codilay.prompts import diff_run_analysis_prompt, diff_run_system_prompt

        if not since and not since_branch:
            raise HTTPException(
                status_code=400,
                detail="Must specify either 'since' or 'since_branch' parameter",
            )

        # Initialize analyzer
        analyzer = DiffAnalyzer(target_path)
        if not analyzer.is_git_repo:
            raise HTTPException(status_code=400, detail="Target directory is not a git repository")

        # Resolve boundary
        boundary_result = analyzer.resolve_boundary(since=since, since_branch=since_branch)
        if not boundary_result:
            raise HTTPException(
                status_code=400,
                detail=f"Could not resolve boundary: {since or since_branch}",
            )

        base_commit, boundary_type = boundary_result
        boundary_ref = since or since_branch or base_commit

        # Analyze changes
        diff_result = analyzer.analyze(since=since, since_branch=since_branch)
        if not diff_result:
            raise HTTPException(status_code=500, detail="Failed to analyze changes")

        if not diff_result.file_diffs:
            return {
                "summary": "No changes detected since the boundary.",
                "changes": {
                    "added": 0,
                    "modified": 0,
                    "deleted": 0,
                    "renamed": 0,
                    "commits": diff_result.commits_count,
                },
                "report_path": None,
            }

        # Load config and LLM
        try:
            cfg = CodiLayConfig.load(target_path)
        except Exception:
            cfg = CodiLayConfig.from_defaults()

        settings = Settings.load()
        settings.inject_env_vars()

        if not cfg.provider:
            cfg.provider = settings.default_provider
        if not cfg.model:
            cfg.model = settings.default_model

        llm = LLMClient(cfg)

        # Prepare LLM analysis
        added_files = []
        for f in diff_result.added_files:
            added_files.append({"path": f.path, "content": f.full_content or ""})

        modified_files = []
        for f in diff_result.modified_files:
            modified_files.append({"path": f.path, "diff": f.diff_content or ""})

        deleted_files = []
        for f in diff_result.deleted_files:
            deleted_files.append({"path": f.path})

        renamed_files = []
        for f in diff_result.renamed_files:
            renamed_files.append({"path": f.path, "old_path": f.old_path or "", "diff": f.diff_content or ""})

        # Build prompts
        sys_prompt = diff_run_system_prompt(cfg)
        user_prompt = diff_run_analysis_prompt(
            boundary_ref=boundary_ref,
            boundary_type=boundary_type,
            commits_count=diff_result.commits_count,
            commit_messages=diff_result.commit_messages,
            added_files=added_files,
            modified_files=modified_files,
            deleted_files=deleted_files,
            renamed_files=renamed_files,
            existing_sections={},
            section_index=[],
        )

        # Call LLM
        result = llm.call(sys_prompt, user_prompt)
        if "error" in result:
            raise HTTPException(status_code=500, detail=result.get("error"))

        # Generate report
        report_gen = ChangeReportGenerator(output_dir)
        report_path = report_gen.generate_report(
            analysis_result=result,
            boundary_ref=boundary_ref,
            boundary_type=boundary_type,
            commits_count=diff_result.commits_count,
            commit_messages=diff_result.commit_messages,
        )

        # Get LLM stats
        stats = llm.get_usage_stats()

        return {
            "summary": result.get("summary", ""),
            "changes": {
                "added": len(diff_result.added_files),
                "modified": len(diff_result.modified_files),
                "deleted": len(diff_result.deleted_files),
                "renamed": len(diff_result.renamed_files),
                "commits": diff_result.commits_count,
            },
            "report_path": report_path,
            "llm_usage": {
                "calls": stats["total_calls"],
                "input_tokens": stats["total_input_tokens"],
                "output_tokens": stats["total_output_tokens"],
            },
        }

    # ── Feature 5: Triage feedback ────────────────────────────────

    class TriageFeedbackRequest(BaseModel):
        file_path: str
        original_category: str
        corrected_category: str
        reason: str = ""
        is_pattern: bool = False

    @app.get("/api/triage-feedback")
    async def list_triage_feedback():
        """List all triage feedback entries."""
        from codilay.triage_feedback import TriageFeedbackStore

        store = TriageFeedbackStore(output_dir)
        entries = store.list_feedback()
        return {
            "entries": [e.to_dict() for e in entries],
            "hints": store.get_project_hints(),
        }

    @app.post("/api/triage-feedback")
    async def add_triage_feedback(req: TriageFeedbackRequest):
        """Record a triage correction."""
        from codilay.triage_feedback import TriageFeedbackStore

        store = TriageFeedbackStore(output_dir)
        entry = store.add_feedback(
            req.file_path,
            req.original_category,
            req.corrected_category,
            reason=req.reason,
            is_pattern=req.is_pattern,
        )
        return entry.to_dict()

    @app.delete("/api/triage-feedback/{file_path:path}")
    async def remove_triage_feedback(file_path: str):
        """Remove feedback for a specific file."""
        from codilay.triage_feedback import TriageFeedbackStore

        store = TriageFeedbackStore(output_dir)
        if store.remove_feedback(file_path):
            return {"deleted": True}
        raise HTTPException(status_code=404, detail="Feedback not found")

    # ── Feature 7: Graph filters ──────────────────────────────────

    class GraphFilterRequest(BaseModel):
        wire_types: Optional[List[str]] = None
        layers: Optional[List[str]] = None
        modules: Optional[List[str]] = None
        exclude_files: Optional[List[str]] = None
        direction: str = "both"
        min_connections: int = 0

    @app.get("/api/graph/filters")
    async def get_graph_filters():
        """Get available filter values for the dependency graph."""
        from codilay.graph_filter import GraphFilter

        links = _load_links()
        gf = GraphFilter(
            closed_wires=links.get("closed", []),
            open_wires=links.get("open", []),
        )
        return gf.get_available_filters()

    @app.post("/api/graph/filter")
    async def filter_graph(req: GraphFilterRequest):
        """Apply filters to the dependency graph."""
        from codilay.graph_filter import GraphFilter, GraphFilterOptions

        links = _load_links()
        gf = GraphFilter(
            closed_wires=links.get("closed", []),
            open_wires=links.get("open", []),
        )
        options = GraphFilterOptions(
            wire_types=req.wire_types,
            layers=req.layers,
            modules=req.modules,
            exclude_files=req.exclude_files,
            direction=req.direction,
            min_connections=req.min_connections,
        )
        result = gf.filter(options)
        return result.to_dict()

    # ── Feature 8: Team memory ────────────────────────────────────

    @app.get("/api/team/facts")
    async def get_team_facts(category: Optional[str] = None):
        """List team facts."""
        from codilay.team_memory import TeamMemory

        tm = TeamMemory(output_dir)
        return {"facts": tm.list_facts(category=category)}

    class TeamFactRequest(BaseModel):
        fact: str
        category: str = "general"
        author: str = ""
        tags: Optional[List[str]] = None

    @app.post("/api/team/facts")
    async def add_team_fact(req: TeamFactRequest):
        """Add a team fact."""
        from codilay.team_memory import TeamMemory

        tm = TeamMemory(output_dir)
        return tm.add_fact(req.fact, category=req.category, author=req.author, tags=req.tags)

    @app.delete("/api/team/facts/{fact_id}")
    async def remove_team_fact(fact_id: str):
        """Remove a team fact."""
        from codilay.team_memory import TeamMemory

        tm = TeamMemory(output_dir)
        if tm.remove_fact(fact_id):
            return {"deleted": True}
        raise HTTPException(status_code=404, detail="Fact not found")

    @app.post("/api/team/facts/{fact_id}/vote")
    async def vote_team_fact(fact_id: str, vote: str = "up"):
        """Vote on a team fact (up or down)."""
        from codilay.team_memory import TeamMemory

        tm = TeamMemory(output_dir)
        if tm.vote_fact(fact_id, vote):
            return {"voted": vote}
        raise HTTPException(status_code=404, detail="Fact not found")

    @app.get("/api/team/decisions")
    async def get_team_decisions(status: Optional[str] = None):
        """List team decisions."""
        from codilay.team_memory import TeamMemory

        tm = TeamMemory(output_dir)
        return {"decisions": tm.list_decisions(status=status)}

    class TeamDecisionRequest(BaseModel):
        title: str
        description: str
        author: str = ""
        related_files: Optional[List[str]] = None

    @app.post("/api/team/decisions")
    async def add_team_decision(req: TeamDecisionRequest):
        """Record a team decision."""
        from codilay.team_memory import TeamMemory

        tm = TeamMemory(output_dir)
        return tm.add_decision(req.title, req.description, author=req.author, related_files=req.related_files)

    @app.patch("/api/team/decisions/{decision_id}")
    async def update_team_decision_status(decision_id: str, status: str):
        """Update a decision's status."""
        from codilay.team_memory import TeamMemory

        tm = TeamMemory(output_dir)
        if tm.update_decision_status(decision_id, status):
            return {"updated": True}
        raise HTTPException(status_code=404, detail="Decision not found")

    @app.get("/api/team/conventions")
    async def get_team_conventions():
        """List team conventions."""
        from codilay.team_memory import TeamMemory

        tm = TeamMemory(output_dir)
        return {"conventions": tm.list_conventions()}

    class TeamConventionRequest(BaseModel):
        name: str
        description: str
        examples: Optional[List[str]] = None
        author: str = ""

    @app.post("/api/team/conventions")
    async def add_team_convention(req: TeamConventionRequest):
        """Add a coding convention."""
        from codilay.team_memory import TeamMemory

        tm = TeamMemory(output_dir)
        return tm.add_convention(req.name, req.description, examples=req.examples, author=req.author)

    class TeamAnnotationRequest(BaseModel):
        file_path: str
        note: str
        author: str = ""
        line_range: Optional[str] = None

    @app.get("/api/team/annotations")
    async def get_team_annotations(file_path: Optional[str] = None):
        """List file annotations."""
        from codilay.team_memory import TeamMemory

        tm = TeamMemory(output_dir)
        return {"annotations": tm.get_annotations(file_path=file_path)}

    @app.post("/api/team/annotations")
    async def add_team_annotation(req: TeamAnnotationRequest):
        """Add a file annotation."""
        from codilay.team_memory import TeamMemory

        tm = TeamMemory(output_dir)
        return tm.add_annotation(req.file_path, req.note, author=req.author, line_range=req.line_range)

    @app.delete("/api/team/annotations/{annotation_id}")
    async def remove_team_annotation(annotation_id: str):
        """Remove an annotation."""
        from codilay.team_memory import TeamMemory

        tm = TeamMemory(output_dir)
        if tm.remove_annotation(annotation_id):
            return {"deleted": True}
        raise HTTPException(status_code=404, detail="Annotation not found")

    @app.get("/api/team/users")
    async def get_team_users():
        """List team members."""
        from codilay.team_memory import TeamMemory

        tm = TeamMemory(output_dir)
        return {"users": tm.list_users()}

    class TeamUserRequest(BaseModel):
        username: str
        display_name: str = ""

    @app.post("/api/team/users")
    async def register_team_user(req: TeamUserRequest):
        """Register a team member."""
        from codilay.team_memory import TeamMemory

        tm = TeamMemory(output_dir)
        return tm.register_user(req.username, display_name=req.display_name)

    @app.get("/api/team/context")
    async def get_team_context():
        """Get team knowledge formatted for LLM context injection."""
        from codilay.team_memory import TeamMemory

        tm = TeamMemory(output_dir)
        return {"context": tm.build_context()}

    # ── Feature 9: Conversation search ────────────────────────────

    @app.get("/api/search")
    async def search_conversations(
        q: str,
        top_k: int = 20,
        role: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ):
        """Full-text search across all conversations."""
        from codilay.search import ConversationSearch

        searcher = ConversationSearch(output_dir)
        if not searcher.load_index():
            await asyncio.to_thread(searcher.build_index)

        results = searcher.search(
            query=q,
            top_k=top_k,
            role_filter=role,
            conv_id_filter=conversation_id,
        )
        return results.to_dict()

    @app.post("/api/search/rebuild")
    async def rebuild_search_index():
        """Rebuild the conversation search index."""
        from codilay.search import ConversationSearch

        searcher = ConversationSearch(output_dir)
        await asyncio.to_thread(searcher.build_index)
        return {"rebuilt": True}

    # ── Feature 10: Code Audits ───────────────────────────────────

    class AuditRequest(BaseModel):
        audit_type: str = "security"
        mode: str = "passive"

    @app.get("/api/audits")
    async def list_audits():
        from codilay.audit_manager import AuditManager

        am = AuditManager(None, output_dir)
        return am.get_index()

    @app.post("/api/audits")
    async def run_audit_endpoint(req: AuditRequest):
        try:
            settings = Settings.load()
            settings.inject_env_vars()
            cfg = CodiLayConfig(target_path=target_path)
            cfg.llm_provider = settings.default_provider
            cfg.llm_model = settings.default_model
            if settings.custom_base_url:
                cfg.llm_base_url = settings.custom_base_url

            # Increase limit for audits to avoid truncation
            cfg.max_tokens_per_call = 8192

            from codilay.audit_manager import AuditManager
            from codilay.llm_client import LLMClient

            llm = LLMClient(cfg)
            am = AuditManager(llm, output_dir)

            state = _load_state()
            links = _load_links()

            result = await asyncio.to_thread(
                am.run_audit,
                req.audit_type,
                req.mode,
                state.section_contents,
                links.get("open", []),
                links.get("closed", []),
                target_path,
                None,  # scanner fallback for now
            )
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/audits/{filename}")
    async def get_audit_report(filename: str):
        import os

        report_path = os.path.join(output_dir, "audits", filename)
        if not os.path.exists(report_path):
            raise HTTPException(status_code=404, detail="Audit not found")
        with open(report_path, "r", encoding="utf-8") as f:
            return {"content": f.read()}

    # ── Feature 11: Commit Docs ────────────────────────────────────

    class CommitDocRequest(BaseModel):
        commit_hash: Optional[str] = None
        commit_range: Optional[str] = None
        use_context: bool = False
        include_metrics: bool = False
        # Backfill fields
        backfill: bool = False
        from_ref: Optional[str] = None
        to_ref: str = "HEAD"
        last_n: Optional[int] = None
        author: Optional[str] = None
        path_filter: Optional[str] = None
        include_merges: bool = False
        force: bool = False
        force_metrics: bool = False
        workers: int = 4

    @app.get("/api/commit-docs")
    async def list_commit_docs():
        docs_dir = os.path.join(output_dir, "commit-docs")
        if not os.path.isdir(docs_dir):
            return {"docs": []}

        docs = []
        for fname in os.listdir(docs_dir):
            if not fname.endswith(".md"):
                continue
            short_hash = fname[:-3]
            fpath = os.path.join(docs_dir, fname)
            mtime = os.path.getmtime(fpath)
            date = ""
            message = ""
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                for line in lines[:5]:
                    s = line.strip()
                    if s.startswith("# ") and " — " in s:
                        parts = s.lstrip("# ").split(" — ", 1)
                        if len(parts) == 2:
                            date = parts[1]
                        break
                for line in lines[:10]:
                    s = line.strip()
                    if s.startswith(">"):
                        message = s.lstrip("> ").strip()
                        break
            except Exception:
                pass
            docs.append({"hash": short_hash, "filename": fname, "date": date, "message": message, "mtime": mtime})

        docs.sort(key=lambda d: d["mtime"], reverse=True)
        return {"docs": docs}

    @app.get("/api/commit-docs/index")
    async def get_commit_index():
        index_path = os.path.join(output_dir, "commit-docs", "index.md")
        if not os.path.exists(index_path):
            raise HTTPException(status_code=404, detail="No index found — generate some commit docs first")
        with open(index_path, "r", encoding="utf-8") as f:
            return {"content": f.read()}

    class BackfillEstimateRequest(BaseModel):
        from_ref: Optional[str] = None
        to_ref: str = "HEAD"
        last_n: Optional[int] = None
        author: Optional[str] = None
        path_filter: Optional[str] = None
        include_merges: bool = False
        include_metrics: bool = False
        force: bool = False

    @app.post("/api/commit-docs/estimate")
    async def estimate_backfill_endpoint(req: BackfillEstimateRequest):
        try:
            from codilay.commit_doc import CommitDocGenerator

            gen = CommitDocGenerator(None, output_dir)  # no LLM needed for estimate
            result = await asyncio.to_thread(
                gen.estimate_backfill,
                target_path,
                req.from_ref,
                req.to_ref,
                req.author,
                req.path_filter,
                req.include_merges,
                req.last_n,
                req.include_metrics,
                req.force,
            )
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/commit-docs/{short_hash}")
    async def get_commit_doc(short_hash: str):
        short_hash = os.path.basename(short_hash)
        fpath = os.path.join(output_dir, "commit-docs", f"{short_hash}.md")
        if not os.path.exists(fpath):
            raise HTTPException(status_code=404, detail="Commit doc not found")
        with open(fpath, "r", encoding="utf-8") as f:
            return {"hash": short_hash, "content": f.read()}

    @app.post("/api/commit-docs")
    async def generate_commit_doc_endpoint(req: CommitDocRequest):
        try:
            settings = Settings.load()
            settings.inject_env_vars()
            cfg = CodiLayConfig(target_path=target_path)
            cfg.llm_provider = settings.default_provider
            cfg.llm_model = settings.default_model
            if settings.custom_base_url:
                cfg.llm_base_url = settings.custom_base_url

            from codilay.commit_doc import CommitDocGenerator
            from codilay.llm_client import LLMClient

            llm = LLMClient(cfg)
            gen = CommitDocGenerator(llm, output_dir)

            codebase_md_path = None
            if req.use_context:
                candidate = os.path.join(output_dir, "CODEBASE.md")
                if os.path.exists(candidate):
                    codebase_md_path = candidate

            if req.backfill or req.from_ref is not None or req.last_n is not None:
                summary = await asyncio.to_thread(
                    gen.backfill,
                    target_path,
                    req.from_ref,
                    req.to_ref,
                    req.author,
                    req.path_filter,
                    req.include_merges,
                    req.last_n,
                    req.use_context,
                    codebase_md_path,
                    req.include_metrics,
                    req.force,
                    req.force_metrics,
                    min(req.workers, 4),  # cap at 4 for web-triggered requests
                )
                return summary
            elif req.commit_range:
                results = await asyncio.to_thread(
                    gen.generate_range,
                    req.commit_range,
                    target_path,
                    req.use_context,
                    codebase_md_path,
                    req.include_metrics,
                )
                return {"generated": results}
            else:
                commit_hash = req.commit_hash
                if not commit_hash:
                    commit_hash = await asyncio.to_thread(gen.get_last_commit, target_path)
                result = await asyncio.to_thread(
                    gen.generate,
                    commit_hash,
                    target_path,
                    req.use_context,
                    codebase_md_path,
                    req.include_metrics,
                )
                return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    return app


# ── Frontend HTML ─────────────────────────────────────────────────────────────


def _get_frontend_html() -> str:
    """Return the self-contained single-page HTML frontend."""
    html_path = os.path.join(os.path.dirname(__file__), "web", "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    # Fallback — shouldn't happen in production
    return "<html><body><h1>CodiLay UI</h1><p>Frontend not found.</p></body></html>"


# ── Server launcher ──────────────────────────────────────────────────────────


def run_server(
    target_path: str,
    output_dir: Optional[str] = None,
    host: str = "127.0.0.1",
    port: int = 8484,
):
    """Start the CodiLay web server."""
    import uvicorn

    app = create_app(target_path, output_dir)
    print(f"\n  CodiLay UI → http://{host}:{port}\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
