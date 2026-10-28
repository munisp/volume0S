"""Production PostgreSQL/RLS conversation persistence; no memory fallback."""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
from typing import AsyncIterator

import asyncpg

from conversations import Approval, Conversation, ConversationError, Draft, Message


class PostgresConversationStore:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @classmethod
    async def connect(cls, database_url: str) -> "PostgresConversationStore":
        if not database_url:
            raise RuntimeError("DATABASE_URL is required")
        return cls(await asyncpg.create_pool(database_url, min_size=1, max_size=10, command_timeout=30))

    async def close(self) -> None:
        await self.pool.close()

    async def ready(self) -> None:
        async with self.pool.acquire() as connection:
            await connection.fetchval("SELECT 1")

    @asynccontextmanager
    async def tenant_tx(self, tenant_id: str) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SELECT set_config('volumeos.tenant_id',$1,true)", tenant_id)
                yield connection

    async def channel_matches(self, tenant_id, phone_number_id):
        async with self.tenant_tx(tenant_id) as connection:
            return await connection.fetchval(
                "SELECT EXISTS(SELECT 1 FROM conversation_channels WHERE tenant_id=$1 AND phone_number_id=$2)",
                tenant_id, phone_number_id,
            )

    async def queue(self, tenant_id):
        from dataclasses import asdict
        async with self.tenant_tx(tenant_id) as connection:
            conversations = await connection.fetch(
                "SELECT * FROM conversations WHERE tenant_id=$1 ORDER BY created_at_unix DESC LIMIT 100", tenant_id,
            )
            drafts = await connection.fetch(
                "SELECT * FROM conversation_drafts WHERE tenant_id=$1 ORDER BY created_at_unix DESC LIMIT 500", tenant_id,
            )
        grouped: dict[str, list[dict]] = {}
        for row in drafts:
            draft = self._draft(row)
            grouped.setdefault(draft.conversation_id, []).append({**asdict(draft), "draft_sha256": draft.digest})
        return [{"conversation": asdict(self._conversation(row)),
                 "drafts": grouped.get(row["conversation_id"], [])} for row in conversations]

    async def record_inbound(self, conversation: Conversation, message: Message):
        async with self.tenant_tx(conversation.tenant_id) as connection:
            existing = await connection.fetchrow(
                "SELECT conversation_id,fingerprint FROM conversation_messages WHERE tenant_id=$1 AND message_id=$2 FOR UPDATE",
                conversation.tenant_id, message.message_id,
            )
            if existing:
                if existing["fingerprint"] != message.fingerprint or existing["conversation_id"] != message.conversation_id:
                    raise ConversationError("webhook replay conflicts with original content")
                return False
            await connection.execute(
                "INSERT INTO conversations(tenant_id,conversation_id,phone_number_id,recipient,contact_name,subject_digest,status,created_at_unix) "
                "VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT(tenant_id,conversation_id) DO NOTHING",
                conversation.tenant_id, conversation.conversation_id, conversation.phone_number_id, conversation.recipient,
                conversation.contact_name, conversation.subject_digest, conversation.status, conversation.created_at_unix,
            )
            await connection.execute(
                "INSERT INTO conversation_messages(tenant_id,conversation_id,message_id,direction,recipient,body,fingerprint,created_at_unix) "
                "VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
                message.tenant_id, message.conversation_id, message.message_id, message.direction, message.recipient,
                message.body, message.fingerprint, message.created_at_unix,
            )
            return True

    async def set_status(self, tenant_id, conversation_id, status):
        async with self.tenant_tx(tenant_id) as connection:
            result = await connection.execute(
                "UPDATE conversations SET status=$3 WHERE tenant_id=$1 AND conversation_id=$2",
                tenant_id, conversation_id, status,
            )
            if result != "UPDATE 1":
                raise ConversationError("conversation not found")

    async def allowed(self, tenant_id, recipient, subject_digest):
        async with self.tenant_tx(tenant_id) as connection:
            return await connection.fetchval(
                "SELECT NOT EXISTS(SELECT 1 FROM conversation_suppressions WHERE tenant_id=$1 AND recipient=$2) "
                "AND (SELECT count(DISTINCT purpose) FROM consents WHERE tenant_id=$1 AND subject_digest=$3 "
                "AND status='granted' AND purpose IN ('service-delivery','ai-processing'))=2",
                tenant_id, recipient, subject_digest,
            ) is True

    async def conversation(self, tenant_id, conversation_id):
        async with self.tenant_tx(tenant_id) as connection:
            row = await connection.fetchrow(
                "SELECT * FROM conversations WHERE tenant_id=$1 AND conversation_id=$2", tenant_id, conversation_id,
            )
        return self._conversation(row) if row else None

    async def save_draft(self, draft: Draft):
        async with self.tenant_tx(draft.tenant_id) as connection:
            try:
                await connection.execute(
                    "INSERT INTO conversation_drafts(tenant_id,conversation_id,draft_sha256,inbound_message_id,response_text,confidence,"
                    "model_version,escalation_reason,status,created_at_unix) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                    draft.tenant_id, draft.conversation_id, draft.digest, draft.inbound_message_id, draft.response_text,
                    draft.confidence, draft.model_version, draft.escalation_reason, draft.status, draft.created_at_unix,
                )
            except asyncpg.UniqueViolationError as exc:
                raise ConversationError("draft already exists for this inbound message") from exc

    async def draft_for_message(self, tenant_id, conversation_id, message_id):
        async with self.tenant_tx(tenant_id) as connection:
            row = await connection.fetchrow(
                "SELECT * FROM conversation_drafts WHERE tenant_id=$1 AND conversation_id=$2 AND inbound_message_id=$3",
                tenant_id, conversation_id, message_id,
            )
        return self._draft(row) if row else None

    async def draft(self, tenant_id, conversation_id, digest):
        async with self.tenant_tx(tenant_id) as connection:
            row = await connection.fetchrow(
                "SELECT * FROM conversation_drafts WHERE tenant_id=$1 AND conversation_id=$2 AND draft_sha256=$3",
                tenant_id, conversation_id, digest,
            )
        return self._draft(row) if row else None

    async def review(self, draft: Draft, approval: Approval):
        async with self.tenant_tx(draft.tenant_id) as connection:
            current = await connection.fetchrow(
                "SELECT status FROM conversation_drafts WHERE tenant_id=$1 AND conversation_id=$2 AND draft_sha256=$3 FOR UPDATE",
                draft.tenant_id, draft.conversation_id, draft.digest,
            )
            if current is None or current["status"] in {"sent", "rejected"}:
                raise ConversationError("sent or rejected draft cannot be reviewed again")
            try:
                await connection.execute(
                    "INSERT INTO conversation_approvals(tenant_id,conversation_id,draft_sha256,reviewer,decision,reason,reviewed_at_unix) "
                    "VALUES($1,$2,$3,$4,$5,$6,$7)",
                    approval.tenant_id, approval.conversation_id, approval.draft_sha256, approval.reviewer,
                    approval.decision, approval.reason, approval.reviewed_at_unix,
                )
            except asyncpg.UniqueViolationError as exc:
                raise ConversationError("reviewer already decided on this draft") from exc
            await connection.execute(
                "UPDATE conversation_drafts SET status=$4 WHERE tenant_id=$1 AND conversation_id=$2 AND draft_sha256=$3",
                draft.tenant_id, draft.conversation_id, draft.digest, approval.decision,
            )

    async def approvals(self, tenant_id, conversation_id, digest):
        async with self.tenant_tx(tenant_id) as connection:
            rows = await connection.fetch(
                "SELECT * FROM conversation_approvals WHERE tenant_id=$1 AND conversation_id=$2 AND draft_sha256=$3",
                tenant_id, conversation_id, digest,
            )
        return [Approval(*(row[key] for key in ("tenant_id", "conversation_id", "draft_sha256", "reviewer",
                                                "decision", "reason", "reviewed_at_unix"))) for row in rows]

    async def outbound_for_draft(self, tenant_id, conversation_id, digest):
        async with self.tenant_tx(tenant_id) as connection:
            row = await connection.fetchrow(
                "SELECT * FROM conversation_messages WHERE tenant_id=$1 AND conversation_id=$2 AND draft_sha256=$3 AND direction='outbound'",
                tenant_id, conversation_id, digest,
            )
        return self._message(row) if row else None

    async def record_outbound(self, message: Message):
        async with self.tenant_tx(message.tenant_id) as connection:
            await connection.execute(
                "INSERT INTO conversation_messages(tenant_id,conversation_id,message_id,direction,recipient,body,fingerprint,"
                "created_at_unix,provider_message_id,draft_sha256) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                message.tenant_id, message.conversation_id, message.message_id, message.direction, message.recipient,
                message.body, message.fingerprint, message.created_at_unix, message.provider_message_id, message.draft_sha256,
            )
            result = await connection.execute(
                "UPDATE conversation_drafts SET status='sent' WHERE tenant_id=$1 AND conversation_id=$2 AND draft_sha256=$3 AND status='approved'",
                message.tenant_id, message.conversation_id, message.draft_sha256,
            )
            if result != "UPDATE 1":
                raise ConversationError("approved draft state changed before send completion")

    async def booking(self, tenant_id, conversation_id, slot_id):
        async with self.tenant_tx(tenant_id) as connection:
            row = await connection.fetchval(
                "SELECT result FROM conversation_bookings WHERE tenant_id=$1 AND conversation_id=$2 AND slot_id=$3",
                tenant_id, conversation_id, slot_id,
            )
        return self._document(row) if row is not None else None

    async def save_booking(self, tenant_id, conversation_id, slot_id, document):
        async with self.tenant_tx(tenant_id) as connection:
            await connection.execute(
                "INSERT INTO conversation_bookings(tenant_id,conversation_id,slot_id,hold_id,result,created_at_unix) "
                "VALUES($1,$2,$3,$4,$5::jsonb,$6) ON CONFLICT(tenant_id,conversation_id,slot_id) DO NOTHING",
                tenant_id, conversation_id, slot_id, document["hold_id"], json.dumps(document, sort_keys=True),
                int(document.get("confirmed_at_unix", document.get("created_at_unix", 1))),
            )

    async def suppress(self, conversation: Conversation, actor, reason, created_at_unix):
        async with self.tenant_tx(conversation.tenant_id) as connection:
            await connection.execute(
                "INSERT INTO conversation_suppressions(tenant_id,recipient,reason,actor,created_at_unix) VALUES($1,$2,$3,$4,$5) "
                "ON CONFLICT(tenant_id,recipient) DO NOTHING",
                conversation.tenant_id, conversation.recipient, reason, actor, created_at_unix,
            )
            await connection.execute(
                "UPDATE conversations SET status='suppressed' WHERE tenant_id=$1 AND conversation_id=$2",
                conversation.tenant_id, conversation.conversation_id,
            )

    @staticmethod
    def _conversation(row):
        return Conversation(*(row[key] for key in ("tenant_id", "conversation_id", "phone_number_id", "recipient",
                                                    "contact_name", "subject_digest", "status", "created_at_unix")))

    @staticmethod
    def _draft(row):
        return Draft(*(row[key] for key in ("tenant_id", "conversation_id", "inbound_message_id", "response_text",
                                             "confidence", "model_version", "escalation_reason", "status", "created_at_unix")))

    @staticmethod
    def _message(row):
        return Message(*(row[key] for key in ("tenant_id", "conversation_id", "message_id", "direction", "recipient",
                                               "body", "fingerprint", "created_at_unix", "provider_message_id", "draft_sha256")))

    @staticmethod
    def _document(value):
        return json.loads(value) if isinstance(value, str) else value
