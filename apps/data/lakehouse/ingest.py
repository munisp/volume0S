"""Kafka/Fluvio-to-bronze ingestion with event-ID replay suppression and DLQ evidence."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

from contracts import ContractError, EventEnvelope
from storage import BronzeStore


class DedupStore(Protocol):
    def contains(self, consumer: str, event_id: str) -> bool: ...
    def remember(self, consumer: str, event_id: str) -> None: ...


class DeadLetterSink(Protocol):
    def publish(self, topic: str, record: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class IngestResult:
    state: str
    event_id: str | None = None


class BronzeIngester:
    def __init__(self, consumer: str, store: BronzeStore, dedup: DedupStore, dlq: DeadLetterSink):
        self.consumer = consumer
        self.store = store
        self.dedup = dedup
        self.dlq = dlq

    def consume(self, table: str, raw: bytes) -> IngestResult:
        try:
            document = json.loads(raw)
            if not isinstance(document, dict):
                raise ContractError("event document must be an object")
            envelope = EventEnvelope.from_dict(document.get("envelope"))
            row = envelope.bronze_row(table, document.get("payload"))
        except (ContractError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            self.dlq.publish(f"{table}_dlq", {
                "consumer": self.consumer,
                "reason": str(exc)[:256],
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
            })
            return IngestResult("quarantined")
        if self.dedup.contains(self.consumer, envelope.event_id):
            return IngestResult("duplicate", envelope.event_id)
        # Transient storage failures propagate for broker retry. Silver conformance
        # also deduplicates, closing the append/commit crash window without mutating bronze.
        self.store.append(table, row)
        self.dedup.remember(self.consumer, envelope.event_id)
        return IngestResult("appended", envelope.event_id)
