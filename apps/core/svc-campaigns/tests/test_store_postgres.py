"""The real store against Postgres, as a plain table owner (FORCE RLS must still apply). Runs only with
CAMPAIGNS_TEST_DATABASE_URL set, e.g. postgres://app:pw@localhost:55432/campaigns (app: LOGIN, not superuser)."""
import os

import asyncpg
import pytest

from store import PostgresCampaignStore

URL = os.environ.get("CAMPAIGNS_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="CAMPAIGNS_TEST_DATABASE_URL not set")


async def test_store_round_trip_and_isolation():
    store = await PostgresCampaignStore.connect(URL)
    await PostgresCampaignStore.connect(URL)  # migrating twice is harmless
    try:
        assert await store.import_contacts("a", [
            {"first_name": "Ada", "email": "ada@a.ng", "phone": "+2348031111111", "email_consent": True, "whatsapp_consent": False},
            {"first_name": "", "email": None, "phone": "+2348032222222", "email_consent": False, "whatsapp_consent": True}]) == {"added": 2, "updated": 0}
        assert await store.import_contacts("a", [{"first_name": "Ada O", "email": "ADA@a.ng", "phone": None,
                                                  "email_consent": True, "whatsapp_consent": True}]) == {"added": 0, "updated": 1}
        await store.import_contacts("b", [{"first_name": "Bo", "email": "bo@b.ng", "phone": None, "email_consent": True, "whatsapp_consent": False}])
        listed = await store.list_contacts("a", 50, 0)
        assert listed["total"] == 2 and listed["reachable"] == {"email": 1, "whatsapp": 2}
        assert len(await store.audience("a")) == 2 and len(await store.audience("b")) == 1

        draft = await store.save_draft("a", {"draft_id": "draft:x", "campaign_id": "c1", "subject": "s", "email_body": "b",
                                             "whatsapp_text": "w", "model": "m"})
        assert (await store.get_draft("a", "draft:x"))["subject"] == "s" and await store.get_draft("b", "draft:x") is None

        value = {"campaign_id": "c1", "cycle_id": "cy", "draft_id": "draft:x", "approval_id": "ap"}
        assert await store.begin_dispatch("a", value, "dispatch:x") is None
        contact = listed["contacts"][0]["id"]
        await store.record_send("a", "dispatch:x", contact, "email", "failed", detail="boom")
        await store.record_send("a", "dispatch:x", contact, "email", "sent", "p1")
        await store.record_send("a", "dispatch:x", contact, "email", "failed")  # a sent row is never downgraded
        assert await store.sent_before("a", "dispatch:x") == {(contact, "email"): "sent"}
        await store.finish_dispatch("a", "dispatch:x", {"dispatch_id": "dispatch:x", "channels": []})
        assert (await store.begin_dispatch("a", value, "dispatch:x"))["dispatch_id"] == "dispatch:x"
        assert await store.dispatch_summary("a", "c1") == [{"cycle_id": "cy", "channel": "email", "status": "sent", "count": 1}]
        assert await store.delete_contact("b", contact) is False and await store.delete_contact("a", contact) is True

        await store.save_social_key("a", b"\x01sealed")
        await store.save_social_key("a", b"\x02sealed")  # reconnecting replaces the key
        assert await store.social_key("a") == b"\x02sealed" and await store.social_key("b") is None
        await store.record_social_post("a", "dispatch:x", "bsky-1", "bluesky", "failed", detail="boom")
        await store.record_social_post("a", "dispatch:x", "bsky-1", "bluesky", "sent", "p1")
        assert await store.social_posted("a", "dispatch:x") == {"bsky-1": "sent"}
        assert {"cycle_id": "cy", "channel": "social", "status": "sent", "count": 1} in await store.dispatch_summary("a", "c1")
        await store.delete_social_key("a")
        assert await store.social_key("a") is None

        # The database itself refuses another business's rows, even to a query that forgets its tenant filter.
        connection = await asyncpg.connect(URL)
        try:
            async with connection.transaction():
                await connection.execute("SELECT set_config('volumeos.tenant_id', 'b', true)")
                assert await connection.fetchval("SELECT count(*) FROM campaign_contacts") == 1
                assert await connection.fetchval("SELECT count(*) FROM campaign_drafts") == 0
            assert await connection.fetchval("SELECT count(*) FROM campaign_contacts") == 0  # no business set
        finally:
            await connection.close()
    finally:
        await store.close()
