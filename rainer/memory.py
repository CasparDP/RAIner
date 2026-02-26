"""Conversation memory with session persistence."""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .config import get_config


class Message(BaseModel):
    """A single message in the conversation."""

    role: Literal["user", "assistant", "system", "tool"]
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)
    tool_name: str | None = None
    tool_call_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Session(BaseModel):
    """A conversation session with metadata."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    mode: str = "feedback"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    messages: list[Message] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)  # For storing draft info, etc.
    title: str | None = None

    def add_message(
        self,
        role: Literal["user", "assistant", "system", "tool"],
        content: str,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        **metadata: Any,
    ) -> Message:
        """Add a message to the session."""
        msg = Message(
            role=role,
            content=content,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            metadata=metadata,
        )
        self.messages.append(msg)
        self.updated_at = datetime.now()

        # Auto-generate title from first user message
        if self.title is None and role == "user":
            self.title = content[:50] + ("..." if len(content) > 50 else "")

        return msg

    def get_messages_for_llm(self) -> list[dict[str, Any]]:
        """Get messages formatted for LLM API."""
        result = []
        for msg in self.messages:
            if msg.role == "tool":
                result.append(
                    {
                        "role": "tool",
                        "content": msg.content,
                        "tool_call_id": msg.tool_call_id or msg.tool_name,
                    }
                )
            else:
                result.append({"role": msg.role, "content": msg.content})
        return result

    def get_context_summary(self) -> str:
        """Get a summary of the conversation context."""
        lines = []
        if self.context.get("draft_loaded"):
            lines.append(f"Draft: {self.context.get('draft_name', 'unnamed')}")
        if self.context.get("papers_referenced"):
            lines.append(f"Papers referenced: {len(self.context['papers_referenced'])}")
        return " | ".join(lines) if lines else "No context loaded"


class SessionManager:
    """Manages session persistence and retrieval."""

    def __init__(self, sessions_path: str | Path | None = None):
        if sessions_path is None:
            sessions_path = get_config().data.sessions_path
        self.sessions_path = Path(sessions_path).expanduser()
        self.sessions_path.mkdir(parents=True, exist_ok=True)

    def _session_file(self, session_id: str) -> Path:
        return self.sessions_path / f"{session_id}.json"

    def save(self, session: Session) -> None:
        """Save a session to disk."""
        path = self._session_file(session.id)
        with open(path, "w") as f:
            json.dump(session.model_dump(mode="json"), f, indent=2, default=str)

    def load(self, session_id: str) -> Session | None:
        """Load a session from disk."""
        path = self._session_file(session_id)
        if not path.exists():
            return None
        with open(path) as f:
            data = json.load(f)
        return Session(**data)

    def list_sessions(self, limit: int = 20) -> list[Session]:
        """List recent sessions."""
        sessions = []
        for path in sorted(self.sessions_path.glob("*.json"), reverse=True)[:limit]:
            try:
                with open(path) as f:
                    data = json.load(f)
                sessions.append(Session(**data))
            except Exception:
                continue
        return sorted(sessions, key=lambda s: s.updated_at, reverse=True)

    def delete(self, session_id: str) -> bool:
        """Delete a session."""
        path = self._session_file(session_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def create_session(self, mode: str = "feedback") -> Session:
        """Create a new session."""
        session = Session(mode=mode)
        self.save(session)
        return session


class ConversationMemory:
    """In-memory conversation state with persistence."""

    def __init__(self, session: Session | None = None):
        self.session = session or Session()
        self.manager = SessionManager()

    @classmethod
    def new(cls, mode: str = "feedback") -> "ConversationMemory":
        """Create a new conversation."""
        manager = SessionManager()
        session = manager.create_session(mode=mode)
        return cls(session=session)

    @classmethod
    def load(cls, session_id: str) -> "ConversationMemory | None":
        """Load an existing conversation."""
        manager = SessionManager()
        session = manager.load(session_id)
        if session is None:
            return None
        return cls(session=session)

    def add(
        self,
        role: Literal["user", "assistant", "system", "tool"],
        content: str,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        **metadata: Any,
    ) -> None:
        """Add a message and auto-save."""
        self.session.add_message(
            role, content, tool_name=tool_name, tool_call_id=tool_call_id, **metadata
        )
        self.manager.save(self.session)

    def get_messages(self) -> list[dict[str, Any]]:
        """Get messages for LLM."""
        return self.session.get_messages_for_llm()

    def set_context(self, key: str, value: Any) -> None:
        """Set context information."""
        self.session.context[key] = value
        self.manager.save(self.session)

    def get_context(self, key: str, default: Any = None) -> Any:
        """Get context information."""
        return self.session.context.get(key, default)

    @property
    def session_id(self) -> str:
        return self.session.id

    @property
    def mode(self) -> str:
        return self.session.mode
