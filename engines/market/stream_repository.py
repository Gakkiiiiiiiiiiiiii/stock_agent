"""Redis Streams adapter kept separate from feature calculation logic."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from engines.market.streaming import MarketEvent


class RedisMarketEventStream:
    def __init__(self, client, stream_key: str = "market:events:v1", retention_seconds: int | None = None) -> None:
        self.client = client
        self.stream_key = stream_key
        self.retention_seconds = retention_seconds

    def append(self, event: MarketEvent) -> str:
        payload = event.model_dump(mode="json")
        message_id = self.client.xadd(self.stream_key, {"event": json.dumps(payload, ensure_ascii=False)}, id="*")
        if self.retention_seconds:
            start = datetime.now(UTC) - timedelta(seconds=self.retention_seconds)
            try:
                self.client.xtrim(self.stream_key, minid=f"{int(start.timestamp() * 1000)}-0", approximate=True)
            except TypeError:  # Older redis-py uses positional arguments only.
                self.client.xtrim(self.stream_key, f"{int(start.timestamp() * 1000)}-0", approximate=True)
        return message_id

    def ensure_group(self, group: str) -> None:
        try:
            self.client.xgroup_create(self.stream_key, group, id="0", mkstream=True)
        except Exception as exc:  # BUSYGROUP is safe and expected
            if "BUSYGROUP" not in str(exc):
                raise

    def consume(self, group: str, consumer: str, count: int = 100) -> list[tuple[str, MarketEvent]]:
        records = self.client.xreadgroup(group, consumer, {self.stream_key: ">"}, count=count, block=1)
        output: list[tuple[str, MarketEvent]] = []
        for _, messages in records:
            for message_id, fields in messages:
                output.append((message_id, MarketEvent.model_validate_json(fields[b"event"] if b"event" in fields else fields["event"])))
        return output

    def recent(self, count: int = 2_000) -> list[MarketEvent]:
        """Read a bounded log for deterministic worker recovery before consume."""
        records = self.client.xrevrange(self.stream_key, count=count)
        output: list[MarketEvent] = []
        for _, fields in reversed(records):
            payload = fields[b"event"] if b"event" in fields else fields["event"]
            output.append(MarketEvent.model_validate_json(payload))
        return output

    def recent_since(self, start_time: datetime) -> list[MarketEvent]:
        start = start_time.astimezone(UTC) if start_time.tzinfo else start_time.replace(tzinfo=UTC)
        records = self.client.xrange(self.stream_key, min=f"{int(start.timestamp() * 1000)}-0", max="+")
        return [MarketEvent.model_validate_json(fields[b"event"] if b"event" in fields else fields["event"]) for _, fields in records]

    def claim_pending(self, group: str, consumer: str, min_idle_ms: int = 30_000, count: int = 100) -> list[tuple[str, MarketEvent]]:
        """Recover messages read by a crashed consumer before reading new ones."""
        try:
            result = self.client.xautoclaim(self.stream_key, group, consumer, min_idle_ms, "0-0", count=count)
        except (AttributeError, TypeError):
            return []
        messages = result[1] if isinstance(result, (tuple, list)) and len(result) > 1 else []
        return [(message_id, MarketEvent.model_validate_json(fields[b"event"] if b"event" in fields else fields["event"])) for message_id, fields in messages]

    def acknowledge(self, group: str, message_id: str) -> int:
        return self.client.xack(self.stream_key, group, message_id)
