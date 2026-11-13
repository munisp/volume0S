"""Campaign drafts written by a local model on the shared Ollama (no customer data leaves the cluster).

The brief has contact details redacted by the campaign worker before it gets here, and nothing about individual
customers is sent: the model only sees the business's own brief. One draft at a time per pod, so drafting can't
crowd out the other apps sharing the CPU-only Ollama.
"""
from __future__ import annotations

import asyncio
import json
import os
import re

import httpx

SYSTEM = (
    "You write short win-back and follow-up marketing messages that a small business in Nigeria sends to its own "
    "customers. The business is the sender: never greet or address the business by name. Greet the customer only "
    "with {{first_name}} (e.g. 'Hi {{first_name}},'). "
    "Answer only with JSON of this shape: {\"subject\": string, \"email_body\": string, \"whatsapp_text\": string, "
    "\"social_post\": string}. "
    "subject: at most 70 characters, never addresses the customer by name. "
    "email_body: plain text, at most 120 words, may use {{first_name}} for the customer's first name, no links. "
    "whatsapp_text: at most 300 characters, may use {{first_name}}. "
    "social_post: a public post for the business's social media, at most 260 characters, never uses {{first_name}}, "
    "may end with up to two relevant hashtags. "
    "Use only facts, prices, discounts and codes that are in the brief; never invent offers. Warm, clear, not pushy. "
    "The brief may contain placeholders like [redacted-phone] or [redacted-email]: never copy them into the messages."
)
PLACEHOLDER = re.compile(r"\{\{\s*first_name\s*\}\}")
REDACTED = re.compile(r"\[redacted-[a-z]+\]")
_one_at_a_time = asyncio.Semaphore(1)


class DraftingError(RuntimeError):
    pass


class OllamaDrafter:
    def __init__(self, base_url: str, model: str, timeout_seconds: float = 300.0):
        if not base_url:
            raise ValueError("OLLAMA_URL is required for campaign drafting")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout_seconds

    @classmethod
    def from_env(cls) -> "OllamaDrafter | None":
        url = os.environ.get("OLLAMA_URL", "")
        return cls(url, os.environ.get("DRAFT_MODEL", "qwen2.5:7b-instruct")) if url else None

    async def draft(self, business_name: str, brief: str) -> dict[str, str]:
        payload = {
            "model": self.model, "stream": False, "format": "json",
            "options": {"temperature": 0.5, "num_predict": 600},
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Business: {business_name or 'our business'}.\nBrief: {brief}"},
            ],
        }
        async with _one_at_a_time:
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(self.base_url + "/api/chat", json=payload)
                    response.raise_for_status()
            except httpx.HTTPError as exc:
                raise DraftingError(f"the drafting model is unavailable: {exc}") from exc
        try:
            content = json.loads(response.json()["message"]["content"])
        except (KeyError, ValueError, TypeError) as exc:
            raise DraftingError("the drafting model returned something that isn't a draft") from exc
        return clean_draft(content)


def clean_draft(content: object) -> dict[str, str]:
    if not isinstance(content, dict):
        raise DraftingError("the drafting model returned something that isn't a draft")
    out: dict[str, str] = {}
    for field, limit in (("subject", 120), ("email_body", 2000), ("whatsapp_text", 600)):
        value = content.get(field)
        if not isinstance(value, str) or not value.strip():
            raise DraftingError(f"the draft is missing its {field.replace('_', ' ')}")
        value = drop_redacted(PLACEHOLDER.sub("{{first_name}}", value.strip()))
        out[field] = value[:limit]
    social = content.get("social_post")
    out["social_text"] = personalise(drop_redacted(social.strip()), "")[:280] if isinstance(social, str) and social.strip() else ""
    out["subject"] = personalise(out["subject"], "").strip(" ,!-") or "A note from us"
    return out


def drop_redacted(text: str) -> str:
    """Removes any sentence still carrying a redaction placeholder: customers must never see '[redacted-phone]'."""
    if not REDACTED.search(text):
        return text
    lines = []
    for line in text.split("\n"):
        sentences = re.split(r"(?<=[.!?])\s+", line)
        lines.append(" ".join(s for s in sentences if not REDACTED.search(s)).strip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def personalise(text: str, first_name: str) -> str:
    """Fills {{first_name}}; with no name, 'Hi {{first_name}},' becomes 'Hi,' rather than 'Hi ,'."""
    name = (first_name or "").strip()
    if name:
        return PLACEHOLDER.sub(name, text)
    return re.sub(r"\s*\{\{first_name\}\}", "", text)
