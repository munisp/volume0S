"""Acknowledged, keyed Fluvio publication for canonical census events."""
from __future__ import annotations

import os
import re
from typing import Any


class FluvioCensusSink:
    def __init__(self, client: Any, topic: str):
        self.client = client
        self.topic = topic
        self.producer = client.topic_producer(topic)

    @classmethod
    def from_env(cls, topic: str | None = None) -> "FluvioCensusSink":
        from fluvio import Fluvio, FluvioConfig

        topic = topic or census_topic()
        endpoint = os.environ.get("FLUVIO_ENDPOINT", "")
        if not endpoint:
            raise RuntimeError("FLUVIO_ENDPOINT is required")
        config = FluvioConfig.new(endpoint)
        # In-cluster clients must use the SPU's local address: on a cluster without a LoadBalancer the SPU's public
        # endpoint has no host, and with False every send waits forever for an acknowledgement.
        config.set_use_spu_local_address(os.environ.get("FLUVIO_USE_SPU_LOCAL_ADDRESS", "true").lower() == "true")
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


DEFAULT_CENSUS_TOPIC = "census-pings"
FLUVIO_TOPIC = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def census_topic() -> str:
    """The Fluvio topic census documents go to. Fluvio names are lowercase letters, digits and hyphens only, so
    the Kafka topic census.pings can't be used here; the census Kafka sink connector copies this topic into it."""
    topic = os.environ.get("CENSUS_FLUVIO_TOPIC", DEFAULT_CENSUS_TOPIC)
    if not FLUVIO_TOPIC.fullmatch(topic):
        raise ValueError(f"invalid Fluvio topic name {topic!r}: lowercase letters, digits and hyphens only")
    return topic

