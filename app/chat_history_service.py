from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from financial_agent.utils import project_root


class ChatHistoryService:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or project_root()).resolve()
        self.sessions_dir = self.root / "storage" / "chat_sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def create_session(self, title: str | None = None) -> dict:
        session_id = uuid4().hex
        now = self._now()
        payload = {
            "session_id": session_id,
            "title": (title or "New Conversation").strip() or "New Conversation",
            "created_at": now,
            "updated_at": now,
            "messages": [],
            "last_response": None,
        }
        self._write_session(payload)
        return self._summary(payload)

    def list_sessions(self) -> list[dict]:
        items = []
        for path in self.sessions_dir.glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            items.append(self._summary(payload))
        items.sort(key=lambda item: item["updated_at"], reverse=True)
        return items

    def get_session(self, session_id: str) -> dict:
        return self._load_session(session_id)

    def delete_session(self, session_id: str) -> None:
        path = self._session_path(session_id)
        if not path.exists():
            raise FileNotFoundError(session_id)
        path.unlink()

    def save_turn(self, session_id: str, user_query: str, assistant_content: str, response: dict | None = None) -> dict:
        payload = self._load_session(session_id)
        now = self._now()
        if not payload["messages"] and payload.get("title") in {"", "New Conversation"}:
            payload["title"] = self._derive_title(user_query)
        payload["messages"].append({"role": "user", "content": user_query, "created_at": now})
        payload["messages"].append({"role": "assistant", "content": assistant_content, "created_at": now})
        payload["updated_at"] = now
        payload["last_response"] = response
        self._write_session(payload)
        return payload

    def ensure_session(self, session_id: str | None, title_hint: str | None = None) -> dict:
        if session_id:
            return self._load_session(session_id)
        return self.create_session(title_hint)

    def _load_session(self, session_id: str) -> dict:
        path = self._session_path(session_id)
        if not path.exists():
            raise FileNotFoundError(session_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_session(self, payload: dict) -> None:
        self._session_path(payload["session_id"]).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _session_path(self, session_id: str) -> Path:
        safe = session_id.strip()
        if not safe:
            raise ValueError("session_id is required")
        if any(char in safe for char in "\\/:*?\"<>|"):
            raise ValueError("invalid session_id")
        return self.sessions_dir / f"{safe}.json"

    @staticmethod
    def _summary(payload: dict) -> dict:
        messages = payload.get("messages") or []
        last_message = messages[-1]["content"] if messages else ""
        return {
            "session_id": payload["session_id"],
            "title": payload.get("title") or "New Conversation",
            "created_at": payload.get("created_at"),
            "updated_at": payload.get("updated_at"),
            "message_count": len(messages),
            "last_message_preview": last_message[:120],
        }

    @staticmethod
    def _derive_title(query: str) -> str:
        compact = " ".join(query.strip().split())
        return compact[:40] or "New Conversation"

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")
