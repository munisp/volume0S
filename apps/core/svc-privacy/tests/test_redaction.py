import pytest

from redaction import RedactedAIGateway, RedactionError, Redactor


def test_redacts_nigerian_pii_before_egress():
    raw = "Call Mr Emeka Okafor on 0803 123 4567 or +234 803 123 4567; email emeka@example.ng. Plot 12 Admiralty Way, Lagos"
    safe = Redactor().redact(raw)
    for secret in ("Emeka", "Okafor", "0803", "+234", "emeka@example.ng", "Plot 12"):
        assert secret not in safe
    assert "[name]" in safe and "[phone]" in safe and "[email]" in safe and "[address]" in safe


class Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True}


class Transport:
    def __init__(self):
        self.calls = []

    async def post(self, url, json, headers):
        self.calls.append((url, json, headers))
        return Response()


@pytest.mark.asyncio
async def test_gateway_sends_only_redacted_prompt():
    transport = Transport()
    gateway = RedactedAIGateway(transport, "https://ai.example/v1", "secret")
    assert await gateway.complete("tenant-a", "Aisha: 07031234567", "model-1") == {"ok": True}
    _, body, _ = transport.calls[0]
    assert body["input"] == "[name]: [phone]"
    assert "Aisha" not in str(body) and "07031234567" not in str(body)


def test_gateway_fails_closed_without_tls_and_credentials():
    transport = Transport()
    with pytest.raises(ValueError):
        RedactedAIGateway(transport, "http://ai.example", "secret")
    with pytest.raises(ValueError):
        RedactedAIGateway(transport, "https://ai.example", "")


def test_prompt_is_bounded():
    with pytest.raises(RedactionError):
        Redactor().redact("x" * 200_001)
