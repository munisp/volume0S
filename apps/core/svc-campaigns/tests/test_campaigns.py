"""svc-campaigns behaviour with in-memory fakes: drafts, contacts, sending rules, retries and unsubscribe."""
import json
import os
import uuid

import pytest

os.environ["VOLUMEOS_SERVICE_TOKEN"] = "t" * 40

from fastapi.testclient import TestClient  # noqa: E402

from channels import ChannelError, read_unsubscribe_token, unsubscribe_token  # noqa: E402
from drafting import DraftingError, clean_draft, personalise  # noqa: E402
from main import create_app  # noqa: E402

AUTH = {"Authorization": "Bearer " + "t" * 40}


def biz(tenant="acme"):
    return {**AUTH, "X-VolumeOS-Tenant": tenant}


class FakeStore:
    def __init__(self):
        self.contacts, self.drafts, self.dispatches, self.sends, self.nudges = {}, {}, {}, {}, set()

    async def ready(self): ...

    async def import_contacts(self, tenant, rows):
        added = updated = 0
        for row in rows:
            match = next((c for c in self.contacts.values() if c["tenant"] == tenant and (
                (row.get("email") and c["email"] == row["email"]) or (row.get("phone") and c["phone"] == row["phone"]))), None)
            if match:
                match.update({k: v for k, v in row.items() if v not in (None, "")} | {
                    "email_consent": row["email_consent"], "whatsapp_consent": row["whatsapp_consent"]})
                updated += 1
            else:
                cid = str(uuid.uuid4())
                self.contacts[cid] = {"id": cid, "tenant": tenant, **row}
                added += 1
        return {"added": added, "updated": updated}

    async def list_contacts(self, tenant, limit, offset):
        mine = [c for c in self.contacts.values() if c["tenant"] == tenant]
        return {"contacts": mine[offset:offset + limit], "total": len(mine), "reachable": {}}

    async def delete_contact(self, tenant, cid):
        if cid in self.contacts and self.contacts[cid]["tenant"] == tenant:
            del self.contacts[cid]
            return True
        return False

    async def audience(self, tenant):
        return [c for c in self.contacts.values() if c["tenant"] == tenant and (
            (c.get("email") and c["email_consent"]) or (c.get("phone") and c["whatsapp_consent"]))]

    async def get_draft(self, tenant, draft_id):
        return next((d for (t, _), d in self.drafts.items() if t == tenant and d["draft_id"] == draft_id), None)

    async def draft_for_campaign(self, tenant, campaign_id):
        return self.drafts.get((tenant, campaign_id))

    async def save_draft(self, tenant, draft):
        return self.drafts.setdefault((tenant, draft["campaign_id"]), draft)

    async def begin_dispatch(self, tenant, value, dispatch_id):
        return self.dispatches.setdefault((tenant, dispatch_id), {"result": None})["result"]

    async def sent_before(self, tenant, dispatch_id):
        return {(c, ch): s for (t, d, c, ch), s in self.sends.items() if t == tenant and d == dispatch_id}

    async def record_send(self, tenant, dispatch_id, contact_id, channel, status, provider_id="", detail=""):
        key = (tenant, dispatch_id, contact_id, channel)
        if self.sends.get(key) in (None, "failed"):
            self.sends[key] = status

    async def finish_dispatch(self, tenant, dispatch_id, result):
        self.dispatches[(tenant, dispatch_id)]["result"] = result

    async def dispatch_summary(self, tenant, campaign_id):
        return []

    async def save_social_key(self, tenant, sealed):
        self.social = getattr(self, "social", {}); self.social[tenant] = sealed

    async def social_key(self, tenant):
        return getattr(self, "social", {}).get(tenant)

    async def delete_social_key(self, tenant):
        getattr(self, "social", {}).pop(tenant, None)

    async def social_posted(self, tenant, dispatch_id):
        return {i: st for (t, d, i), st in getattr(self, "posts", {}).items() if t == tenant and d == dispatch_id}

    async def record_social_post(self, tenant, dispatch_id, integration_id, network, status, provider_id="", detail=""):
        self.posts = getattr(self, "posts", {}); key = (tenant, dispatch_id, integration_id)
        if self.posts.get(key) in (None, "failed"):
            self.posts[key] = status

    async def nudge_sent(self, tenant, nudge_id):
        return (tenant, nudge_id) in self.nudges

    async def record_nudge(self, tenant, nudge_id, channel, recipients):
        self.nudges.add((tenant, nudge_id))
        return True


class FakeDrafter:
    model = "fake-model"

    def __init__(self):
        self.calls = 0

    async def draft(self, business, brief):
        self.calls += 1
        if "fail" in brief:
            raise DraftingError("the drafting model is unavailable")
        return {"subject": f"We miss you at {business}", "email_body": "Hi {{first_name}}, come back!", "whatsapp_text": "Hi {{first_name}}!",
                "social_text": "Jollof Fridays are back #Lagos"}


class FakeEmail:
    def __init__(self, fail_for=()):
        self.sent, self.fail_for = [], set(fail_for)

    async def send(self, message):
        if message.to in self.fail_for:
            raise ChannelError("Resend returned 500")
        self.sent.append(message)
        return f"email-{len(self.sent)}"


class FakeWhatsApp:
    def __init__(self):
        self.sent = []

    async def send(self, to, text):
        self.sent.append((to, text))
        return f"wamid.{len(self.sent)}"


class FakeServices:
    def __init__(self, suppressed=(), owners=(), state="active", suspended=False):
        self.suppressed, self.owners, self.suppress_calls = set(suppressed), list(owners), []
        self.state, self.suspended = state, suspended

    async def business(self, tenant):
        return {"name": "Acme Foods", "state": self.state, "outbound_suspended": self.suspended}

    async def business_name(self, tenant):
        return "Acme Foods"

    async def may_send(self, tenant, channel, recipient):
        return (recipient not in self.suppressed, "suppressed" if recipient in self.suppressed else "")

    async def suppress(self, tenant, channel, recipient, reason):
        self.suppress_calls.append((tenant, channel, recipient))

    async def billing_contacts(self, tenant):
        return self.owners


class FakePostiz:
    base = "https://postiz.example"

    def __init__(self, fail_for=()):
        self.posts, self.fail_for = [], set(fail_for)

    async def integrations(self, api_key):
        if api_key != "good-postiz-key-123":
            raise ChannelError("Postiz didn't accept that API key")
        return [{"id": "bsky-1", "name": "Acme", "network": "bluesky", "picture": "", "disabled": False},
                {"id": "tg-1", "name": "Acme TG", "network": "telegram", "picture": "", "disabled": False},
                {"id": "x-old", "name": "Old", "network": "x", "picture": "", "disabled": True}]

    async def post_now(self, api_key, integration, content, when):
        if integration["id"] in self.fail_for:
            raise ChannelError("Postiz returned 500")
        self.posts.append((integration["id"], content)); return f"post-{len(self.posts)}"


def app(**kw):
    parts = {"store": FakeStore(), "drafter": FakeDrafter(), "email": FakeEmail(), "whatsapp": FakeWhatsApp(), "services": FakeServices(),
             "postiz": FakePostiz()}
    parts.update(kw)
    return TestClient(create_app(**parts)), parts


def test_service_token_and_a_business_are_required():
    client, _ = app()
    with client:
        assert client.get("/v1/contacts").status_code == 401
        assert client.get("/v1/contacts", headers={**AUTH}).status_code == 400
        assert client.get("/v1/contacts", headers={**AUTH, "X-VolumeOS-Tenant": "platform"}).status_code == 400
        assert client.get("/v1/contacts", headers=biz()).status_code == 200


def test_one_campaign_gets_one_draft_even_when_the_worker_retries():
    client, parts = app()
    with client:
        first = client.post("/v1/drafts", headers=biz(), json={"campaign_id": "c1", "brief": "come back"}).json()
        again = client.post("/v1/drafts", headers=biz(), json={"campaign_id": "c1", "brief": "come back"}).json()
        assert first["draft_id"] == again["draft_id"] and first["subject"] == "We miss you at Acme Foods"
        assert parts["drafter"].calls == 1
        assert client.get(f"/v1/drafts/{first['draft_id']}", headers=biz("other")).status_code == 404
        failed = client.post("/v1/drafts", headers=biz(), json={"campaign_id": "c2", "brief": "please fail"})
        assert failed.status_code == 503 and failed.json()["error"]["code"] == "drafting_failed"


def test_contacts_are_normalised_and_bad_ones_refused():
    client, parts = app()
    with client:
        ok = client.post("/v1/contacts/import", headers=biz(), json={"contacts": [
            {"first_name": "Ada", "email": "ADA@Example.ng", "email_consent": True},
            {"first_name": "Bo", "phone": "0803 123 4567", "whatsapp_consent": True}]})
        assert ok.json() == {"added": 2, "updated": 0}
        phones = [c["phone"] for c in parts["store"].contacts.values() if c.get("phone")]
        emails = [c["email"] for c in parts["store"].contacts.values() if c.get("email")]
        assert phones == ["+2348031234567"] and emails == ["ada@example.ng"]
        for bad in ({"email": "not-an-email"}, {"phone": "12345"}, {"first_name": "{{evil}}", "email": "x@y.ng"}):
            assert client.post("/v1/contacts/import", headers=biz(), json={"contacts": [bad]}).status_code == 422
        assert client.post("/v1/contacts/import", headers=biz(), json={"contacts": [{"first_name": "Nobody"}]}).status_code == 400


def start_campaign(client):
    draft = client.post("/v1/drafts", headers=biz(), json={"campaign_id": "c1", "brief": "come back"}).json()
    return {"campaign_id": "c1", "cycle_id": "cy1", "draft_id": draft["draft_id"], "approval_id": "ap1"}


def test_dispatch_sends_only_with_consent_skips_suppressed_and_test_addresses_and_counts_per_channel():
    client, parts = app(services=FakeServices(suppressed={"blocked@acme.ng"}))
    with client:
        client.post("/v1/contacts/import", headers=biz(), json={"contacts": [
            {"first_name": "Ada", "email": "ada@acme.ng", "email_consent": True, "phone": "+2348031111111", "whatsapp_consent": True},
            {"first_name": "", "email": "noconsent@acme.ng", "email_consent": False},
            {"first_name": "Bo", "email": "blocked@acme.ng", "email_consent": True},
            {"first_name": "Qa", "email": "qa@example.com", "email_consent": True}]})
        result = client.post("/v1/dispatches", headers=biz(), json=start_campaign(client)).json()
        assert result["channels"] == [{"channel": "email", "success": 2, "failure": 0}, {"channel": "whatsapp", "success": 1, "failure": 0}]
        sent = parts["email"].sent
        assert [m.to for m in sent] == ["ada@acme.ng"]  # no consent, suppressed and test address are not emailed
        assert sent[0].body == "Hi Ada, come back!" and "/v1/unsubscribe?t=" in sent[0].unsubscribe_url
        assert parts["whatsapp"].sent == [("+2348031111111", "Hi Ada!")]


def test_a_retried_dispatch_never_sends_twice():
    client, parts = app()
    with client:
        client.post("/v1/contacts/import", headers=biz(), json={"contacts": [{"email": "ada@acme.ng", "email_consent": True}]})
        body = start_campaign(client)
        first = client.post("/v1/dispatches", headers=biz(), json=body).json()
        again = client.post("/v1/dispatches", headers=biz(), json=body).json()
        assert first == again and len(parts["email"].sent) == 1


def test_a_partly_failed_dispatch_retries_only_the_failures():
    email = FakeEmail(fail_for={"bo@acme.ng"})
    client, parts = app(email=email)
    with client:
        client.post("/v1/contacts/import", headers=biz(), json={"contacts": [
            {"email": "ada@acme.ng", "email_consent": True}, {"email": "bo@acme.ng", "email_consent": True}]})
        body = start_campaign(client)
        first = client.post("/v1/dispatches", headers=biz(), json=body).json()
        assert first["channels"] == [{"channel": "email", "success": 1, "failure": 1}]


def test_no_audience_is_refused_so_the_budget_is_released():
    client, _ = app()
    with client:
        r = client.post("/v1/dispatches", headers=biz(), json=start_campaign(client))
        assert r.status_code == 409 and r.json()["error"]["code"] == "no_audience"


def test_another_business_cannot_send_this_draft():
    client, _ = app()
    with client:
        body = start_campaign(client)
        client.post("/v1/contacts/import", headers=biz("other"), json={"contacts": [{"email": "x@other.ng", "email_consent": True}]})
        assert client.post("/v1/dispatches", headers=biz("other"), json=body).status_code == 404


def test_unsubscribe_links_are_signed_and_suppress_the_address():
    services = FakeServices()
    client, _ = app(services=services)
    token = unsubscribe_token("acme", "Ada@Acme.ng")
    assert read_unsubscribe_token(token) == ("acme", "ada@acme.ng")
    assert read_unsubscribe_token(token[:-2] + "AA") is None
    with client:
        page = client.get("/v1/unsubscribe", params={"t": token})  # a mail scanner opening the link changes nothing
        assert page.status_code == 200 and 'method="post"' in page.text and services.suppress_calls == []
        assert client.post("/v1/unsubscribe", params={"t": token}).status_code == 200
        assert client.get("/v1/unsubscribe", params={"t": "forged"}).status_code == 400
        assert client.post("/v1/unsubscribe", params={"t": "forged"}).status_code == 400
    assert services.suppress_calls == [("acme", "email", "ada@acme.ng")]


def test_unsubscribe_links_refuse_without_a_signing_secret(monkeypatch):
    token = unsubscribe_token("acme", "ada@acme.ng")
    monkeypatch.setenv("VOLUMEOS_SERVICE_TOKEN", "")
    assert read_unsubscribe_token(token) is None  # nothing verifies, so nothing can be forged with an empty key
    with pytest.raises(ChannelError):
        unsubscribe_token("acme", "ada@acme.ng")


def test_payment_reminders_go_to_the_owners_once():
    owners = [{"email": "owner@acme.ng", "first_name": "Ada", "enabled": True, "roles": ["client-owner"]},
              {"email": "qa@example.com", "first_name": "Qa", "enabled": True, "roles": ["client-owner"]}]
    client, parts = app(services=FakeServices(owners=owners))
    nudge = {"channel": "email", "subscription_id": "s1", "cycle": "2026-11", "attempt": 1, "amount_minor": "150000", "currency": "NGN"}
    with client:
        assert client.post("/v1/dunning-nudges", headers={**biz(), "Idempotency-Key": "n1"}, json=nudge).json() == {"status": "sent", "recipients": 1}
        assert client.post("/v1/dunning-nudges", headers={**biz(), "Idempotency-Key": "n1"}, json=nudge).json()["status"] == "already_sent"
        assert client.post("/v1/dunning-nudges", headers=biz(), json={**nudge, "channel": "whatsapp"}).json()["status"] == "skipped"
    assert len(parts["email"].sent) == 1 and "₦1,500.00" in parts["email"].sent[0].body


def test_draft_cleaning_and_personalising():
    draft = clean_draft({"subject": "Hi {{ first_name }}, we miss you!", "email_body": "Hi {{first_name}},\nCome back", "whatsapp_text": "Hey {{first_name}}"})
    assert draft["subject"] == "Hi, we miss you"
    assert personalise(draft["email_body"], "Ada") == "Hi Ada,\nCome back" and personalise("Hi {{first_name}},", "") == "Hi,"
    try:
        clean_draft({"subject": "x"})
        assert False, "a draft without a body was accepted"
    except DraftingError:
        pass


def test_a_business_that_isnt_active_or_is_paused_sends_nothing_and_says_why():
    for services, code in ((FakeServices(state="onboarding"), "business_not_active"), (FakeServices(suspended=True), "outbound_suspended")):
        client, parts = app(services=services)
        with client:
            client.post("/v1/contacts/import", headers=biz(), json={"contacts": [{"email": "ada@acme.ng", "email_consent": True}]})
            r = client.post("/v1/dispatches", headers=biz(), json=start_campaign(client))
            assert r.status_code == 409 and r.json()["error"]["code"] == code, r.text
            assert parts["email"].sent == []


def test_redaction_placeholders_never_reach_customers():
    draft = clean_draft({"subject": "Jollof deal", "email_body": "Hi {{first_name}}, 10% off this weekend! Order now: [redacted-phone].\nSee you soon.",
                         "whatsapp_text": "Call [redacted-phone] or reply here. Code BACK10 works all weekend."})
    assert "[redacted" not in json.dumps(draft)
    assert draft["email_body"] == "Hi {{first_name}}, 10% off this weekend!\nSee you soon."
    assert draft["whatsapp_text"] == "Code BACK10 works all weekend."


def test_social_key_is_checked_sealed_and_never_returned():
    client, parts = app()
    with client:
        assert client.put("/v1/social", headers=biz(), json={"api_key": "wrong-key-0000"}).status_code == 400
        ok = client.put("/v1/social", headers=biz(), json={"api_key": "good-postiz-key-123"})
        assert ok.status_code == 200 and [a["network"] for a in ok.json()["accounts"]] == ["bluesky", "telegram", "x"]
        assert b"good-postiz-key-123" not in parts["store"].social["acme"]  # stored encrypted
        listed = client.get("/v1/social", headers=biz()).json()
        assert listed["connected"] and "good-postiz-key-123" not in json.dumps(listed)
        assert client.get("/v1/social", headers=biz("other")).json()["connected"] is False


def test_a_social_only_business_can_run_a_campaign_and_posts_once_per_live_account():
    client, parts = app()
    with client:
        client.put("/v1/social", headers=biz(), json={"api_key": "good-postiz-key-123"})
        body = start_campaign(client)
        first = client.post("/v1/dispatches", headers=biz(), json=body).json()
        again = client.post("/v1/dispatches", headers=biz(), json=body).json()
        assert first["channels"] == [{"channel": "social", "success": 2, "failure": 0}] and first == again
        assert parts["postiz"].posts == [("bsky-1", "Jollof Fridays are back #Lagos"), ("tg-1", "Jollof Fridays are back #Lagos")]


def test_a_failed_social_post_is_counted_and_retried_alone():
    postiz = FakePostiz(fail_for={"tg-1"})
    client, parts = app(postiz=postiz)
    with client:
        client.put("/v1/social", headers=biz(), json={"api_key": "good-postiz-key-123"})
        body = start_campaign(client)
        result = client.post("/v1/dispatches", headers=biz(), json=body).json()
        assert result["channels"] == [{"channel": "social", "success": 1, "failure": 1}]
