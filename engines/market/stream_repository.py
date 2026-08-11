"""Redis Streams adapter kept separate from feature calculation logic."""
from __future__ import annotations

import json

from engines.market.streaming import MarketEvent


class RedisMarketEventStream:
    def __init__(self, client, stream_key: str = "market:events:v1") -> None:
        self.client = client
        self.stream_key = stream_key

    def append(self, event: MarketEvent) -> str:
        payload = event.model_dump(mode="json")
        return self.client.xadd(self.stream_key, {"event": json.dumps(payload, ensure_ascii=False)}, id="*")

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

    def acknowledge(self, group: str, message_id: str) -> int:
        return self.client.xack(self.stream_key, group, message_id)
