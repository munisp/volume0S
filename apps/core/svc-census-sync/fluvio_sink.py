"""Acknowledged, keyed Fluvio publication for canonical census events."""
from __future__ import annotations

from typing import Any


class FluvioCensusSink:
    def __init__(self, client: Any, topic: str):
        self.client = client
        self.topic = topic
        self.producer = client.topic_producer(topic)

    @classmethod
    def from_env(cls, topic: str = "census.pings") -> "FluvioCensusSink":
        import os
        from fluvio import Fluvio, FluvioConfig

        endpoint = os.environ.get("FLUVIO_ENDPOINT", "")
        if not endpoint:
            raise RuntimeError("FLUVIO_ENDPOINT is required")
        config = FluvioConfig.new(endpoint)
        config.set_use_spu_local_address(False)
        config.disable_tls()
        return cls(Fluvio.connect_with_config(config), topic)

    async def publish(self, topic: str, key: str, document_json: str) -> None:
        if topic != self.topic:
            raise ValueError("census sink topic does not match its producer")
        output = await self.producer.async_send(list(key.encode()), list(document_json.encode()))
        metadata = await output.async_wait()
        if metadata is None:
            raise TimeoutError("Fluvio did not acknowledge census publication")
        await self.producer.async_flush()
