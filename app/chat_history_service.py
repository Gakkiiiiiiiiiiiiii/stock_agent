from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, func, select

from financial_agent.utils import project_root
from storage.db import get_engine, session_scope
from storage.models.chat import ChatMessageRecord, ChatSessionRecord


class ChatHistoryService:
    _schema_ready = False

    def __init__(self, root: Path | None = None, use_database: bool | None = None) -> None:
        self.root = (root or project_root()).resolve()
        self.sessions_dir = self.root / "storage" / "chat_sessions"
        self.use_database = (root is None) if use_database is None else use_database
        if not self.use_database:
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
        if self.use_database:
            self._ensure_database_schema()
            with session_scope() as db:
                record = ChatSessionRecord(
                    session_id=session_id,
                    title=payload["title"],
                    created_at=self._parse_time(now),
                    updated_at=self._parse_time(now),
                )
                db.add(record)
            return self._summary(payload)
        self._write_session(payload)
        return self._summary(payload)

    def list_sessions(self) -> list[dict]:
        if self.use_database:
            self._ensure_database_schema()
            with session_scope() as db:
                sessions = db.execute(select(ChatSessionRecord).order_by(ChatSessionRecord.updated_at.desc())).scalars().all()
                items = []
                for record in sessions:
                    count = db.scalar(select(func.count()).select_from(ChatMessageRecord).where(ChatMessageRecord.session_id == record.session_id)) or 0
                    last_message = db.execute(
                        select(ChatMessageRecord.content)
                        .where(ChatMessageRecord.session_id == record.session_id)
                        .order_by(ChatMessageRecord.ordinal.desc(), ChatMessageRecord.id.desc())
                        .limit(1)
                    ).scalar_one_or_none()
                    items.append(
                        {
                            "session_id": record.session_id,
                            "title": record.title or "New Conversation",
                            "created_at": self._format_time(record.created_at),
                            "updated_at": self._format_time(record.updated_at),
                            "message_count": count,
                            "last_message_preview": (last_message or "")[:120],
                        }
                    )
                return items
        items = []
        for path in self.sessions_dir.glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            items.append(self._summary(payload))
        items.sort(key=lambda item: item["updated_at"], reverse=True)
        return items

    def get_session(self, session_id: str) -> dict:
        return self._load_session(session_id)

    def delete_session(self, session_id: str) -> None:
        if self.use_database:
            self._ensure_database_schema()
            with session_scope() as db:
                record = db.get(ChatSessionRecord, session_id)
                if record is None:
                    raise FileNotFoundError(session_id)
                db.execute(delete(ChatMessageRecord).where(ChatMessageRecord.session_id == session_id))
                db.delete(record)
            return
        path = self._session_path(session_id)
        if not path.exists():
            raise FileNotFoundError(session_id)
        path.unlink()

    def save_turn(self, session_id: str, user_query: str, assistant_content: str, response: dict | None = None) -> dict:
        if self.use_database:
            self._ensure_database_schema()
            with session_scope() as db:
                record = db.get(ChatSessionRecord, session_id)
                if record is None:
                    raise FileNotFoundError(session_id)
                message_count = db.scalar(select(func.count()).select_from(ChatMessageRecord).where(ChatMessageRecord.session_id == session_id)) or 0
                if message_count == 0 and record.title in {"", "New Conversation"}:
                    record.title = self._derive_title(user_query)
                now_dt = datetime.now()
                db.add_all(
                    [
                        ChatMessageRecord(session_id=session_id, role="user", content=user_query, ordinal=message_count, created_at=now_dt),
                        ChatMessageRecord(session_id=session_id, role="assistant", content=assistant_content, ordinal=message_count + 1, created_at=now_dt),
                    ]
                )
                record.updated_at = now_dt
                record.last_response_json = json.dumps(response, ensure_ascii=False) if response is not None else None
            return self.get_session(session_id)
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
        if self.use_database:
            self._ensure_database_schema()
            with session_scope() as db:
                record = db.get(ChatSessionRecord, session_id)
                if record is None:
                    raise FileNotFoundError(session_id)
                messages = db.execute(
                    select(ChatMessageRecord)
                    .where(ChatMessageRecord.session_id == session_id)
                    .order_by(ChatMessageRecord.ordinal.asc(), ChatMessageRecord.id.asc())
                ).scalars().all()
                return {
                    "session_id": record.session_id,
                    "title": record.title or "New Conversation",
                    "created_at": self._format_time(record.created_at),
                    "updated_at": self._format_time(record.updated_at),
                    "messages": [
                        {"role": message.role, "content": message.content, "created_at": self._format_time(message.created_at)}
                        for message in messages
                    ],
                    "last_response": json.loads(record.last_response_json) if record.last_response_json else None,
                }
        path = self._session_path(session_id)
        if not path.exists():
            raise FileNotFoundError(session_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_session(self, payload: dict) -> None:
        self._session_path(payload["session_id"]).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def _ensure_database_schema(cls) -> None:
        if cls._schema_ready:
            return
        engine = get_engine()
        ChatSessionRecord.__table__.create(bind=engine, checkfirst=True)
        ChatMessageRecord.__table__.create(bind=engine, checkfirst=True)
        cls._schema_ready = True

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

    @staticmethod
    def _parse_time(value: str) -> datetime:
        return datetime.fromisoformat(value)

    @staticmethod
    def _format_time(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat(timespec="seconds")
