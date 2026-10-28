"""Fail-closed PII redaction boundary for every external AI request."""
from __future__ import annotations

import re
from typing import Any, Protocol

# Local and E.164 Nigerian forms, including common separators.
PHONE = re.compile(r"(?<!\d)(?:\+?234|0)[\s.-]?[789][01](?:[\s.-]?\d){8}(?!\d)")
EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
ADDRESS = re.compile(
    r"\b(?:plot|house|no\.?|number)\s+\d+[a-z]?(?:\s+[A-Za-z0-9.'-]+){0,5}"
    r"(?:\s+(?:street|st\.?|road|rd\.?|avenue|ave\.?|close|lane|way|drive|crescent|estate))?"
    r"(?:\s*,?\s*(?:lagos|abuja|port harcourt|kano|ibadan))?\b",
    re.I,
)
HONORIFIC_NAME = re.compile(r"\b(?:mr|mrs|ms|miss|dr|chief|alhaji|alhaja|pastor|engr)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b")
NIGERIAN_NAMES = (
    "adaeze", "adeniyi", "adeola", "adesina", "adewale", "aisha", "amara", "aminu", "bola", "bukola",
    "chiamaka", "chidinma", "chinedu", "chinwe", "damilola", "ebuka", "efe", "emeka", "funke", "ibrahim",
    "ifeanyi", "ikechukwu", "iyabo", "kehinde", "kemi", "kunle", "musa", "ngozi", "nkechi", "nnamdi",
    "obinna", "olaitan", "olamide", "olu", "oluwaseun", "oluwatobi", "opeyemi", "osagie", "sade", "segun",
    "taiwo", "temitope", "titilayo", "tobi", "uche", "yemi", "yetunde", "zainab", "abubakar", "okonkwo",
    "okafor", "okoro", "adebayo", "adeyemi", "balogun", "danladi", "eze", "ibironke", "lawal", "obi",
    "ogunleye", "ojo", "oladipo", "olanrewaju", "onwuka", "otunba", "suleiman", "usman", "yakubu",
)
NAME = re.compile(r"\b(?:" + "|".join(NIGERIAN_NAMES) + r")\b(?:\s+(?:" + "|".join(NIGERIAN_NAMES) + r")\b)?", re.I)


class RedactionError(ValueError):
    pass


class AIHTTPTransport(Protocol):
    async def post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> Any: ...


class Redactor:
    def redact(self, text: str) -> str:
        if not isinstance(text, str) or len(text) > 200_000:
            raise RedactionError("AI prompt must be a bounded string")
        for pattern, token in (
            (EMAIL, "[email]"), (PHONE, "[phone]"), (ADDRESS, "[address]"),
            (HONORIFIC_NAME, "[name]"), (NAME, "[name]"),
        ):
            text = pattern.sub(token, text)
        return text


class RedactedAIGateway:
    """The only permitted provider egress wrapper; raw prompts are never logged."""
    def __init__(self, transport: AIHTTPTransport, endpoint: str, api_key: str,
                 redactor: Redactor | None = None):
        if not endpoint.startswith("https://") or not api_key:
            raise ValueError("an HTTPS AI endpoint and credential are required")
        self.transport = transport
        self.endpoint = endpoint
        self.api_key = api_key
        self.redactor = redactor or Redactor()

    async def complete(self, tenant_id: str, prompt: str, model: str) -> dict[str, Any]:
        if not tenant_id or not model or model.startswith("http"):
            raise RedactionError("tenant and bounded model identifier are required")
        safe_prompt = self.redactor.redact(prompt)
        response = await self.transport.post(
            self.endpoint,
            json={"model": model, "input": safe_prompt, "metadata": {"tenant_id": tenant_id}},
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        response.raise_for_status()
        document = response.json()
        if not isinstance(document, dict):
            raise RedactionError("AI provider returned a non-object response")
        return document
