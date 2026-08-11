"""Redis Streams consumer for realtime market feature overlays (P2-05)."""
from __future__ import annotations

import logging
import os
import socket
import time

import redis

from engines.market.stream_repository import RedisMarketEventStream
from engines.market.streaming import StreamingFeatureEngine

logger = logging.getLogger(__name__)


def build_worker() -> tuple[RedisMarketEventStream, StreamingFeatureEngine, str, str]:
    client = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    stream_key = os.getenv("MARKET_EVENT_STREAM_KEY", "market:events:v1")
    group = os.getenv("MARKET_EVENT_CONSUMER_GROUP", "market-feature-v1")
    consumer = os.getenv("MARKET_EVENT_CONSUMER", f"market-stream-{socket.gethostname()}")
    stream = RedisMarketEventStream(client, stream_key)
    stream.ensure_group(group)
    lateness = int(os.getenv("MARKET_EVENT_ALLOWED_LATENESS_SECONDS", "5"))
    return stream, StreamingFeatureEngine(lateness), group, consumer


def process_batch(stream: RedisMarketEventStream, engine: StreamingFeatureEngine, group: str, consumer: str, count: int = 100) -> int:
    processed = 0
    for message_id, event in stream.consume(group, consumer, count):
        result = engine.process(event)
        # Both normal, duplicate and late events are durable outcomes.  Acking
        # them avoids poison-message loops while retaining late event IDs in the
        # engine audit state.
        stream.acknowledge(group, message_id)
        processed += 1
        logger.debug("market event %s: %s", event.event_id, result.get("reason", "accepted"))
    return processed


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    stream, engine, group, consumer = build_worker()
    while True:
        try:
            process_batch(stream, engine, group, consumer)
        except Exception:  # noqa: BLE001
            logger.exception("market stream processing failed")
            time.sleep(2)


if __name__ == "__main__":
    main()
