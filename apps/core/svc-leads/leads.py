"""Tenant-safe CRM ingestion, reviewed gold scoring, cadence, and conversion."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field, replace
import hashlib
import hmac
import json
import math
import re
import time
import unicodedata
from typing import Any, Protocol

SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
H3_R9 = re.compile(r"^[0-9a-f]{15}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SOURCES = {"census", "referral", "import", "web", "partner"}
STAGES = {"new", "contacted", "qualified", "opportunity", "converted", "recycled", "suppressed"}
CADENCE = (("D0", 0), ("D3", 3), ("D7", 7), ("D14", 14))
TRANSITIONS = {"new": {"contacted", "suppressed"}, "contacted": {"qualified", "recycled", "suppressed"},
               "qualified": {"opportunity", "recycled", "suppressed"}, "opportunity": {"converted", "recycled", "suppressed"},
               "recycled": {"contacted", "suppressed"}, "converted": set(), "suppressed": set()}


class LeadError(ValueError):
    pass


def identifier(value: str, label: str = "identifier") -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise LeadError(f"invalid {label}")
    return value


def normalize_name(value: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    if not 2 <= len(normalized) <= 200:
        raise LeadError("business/contact name is required and bounded")
    return normalized


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if digits.startswith("0") and len(digits) == 11:
        digits = "234" + digits[1:]
    if not re.fullmatch(r"234[789][01]\d{8}", digits):
        raise LeadError("unsupported Nigerian phone number")
    return "+" + digits


def canonical(document: Any) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(document: Any) -> str:
    raw = document if isinstance(document, str) else canonical(document)
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class LeadInput:
    source: str
    source_record_id: str
    business_name: str
    phone: str | None = None
    contact_name: str | None = None
    address: str | None = None
    niche: str | None = None
    h3_r9: str | None = None
    consent_reference: str | None = None
    lineage_id: str | None = None

    def normalized(self, tenant_id: str) -> "LeadInput":
        identifier(tenant_id, "tenant")
        identifier(self.source_record_id, "source record")
        if self.source not in SOURCES:
            raise LeadError("unsupported lead source")
        if self.h3_r9 is not None and not H3_R9.fullmatch(self.h3_r9):
            raise LeadError("territory must be an H3 resolution-9 cell")
        if self.consent_reference is not None:
            identifier(self.consent_reference, "consent reference")
        if self.lineage_id is not None:
            identifier(self.lineage_id, "lineage")
        for value in (self.address, self.niche):
            if value is not None and len(value) > 1000:
                raise LeadError("lead attribute exceeds its bound")
        return replace(self, business_name=normalize_name(self.business_name),
                       contact_name=normalize_name(self.contact_name) if self.contact_name else None,
                       phone=normalize_phone(self.phone) if self.phone else None,
                       lineage_id=self.lineage_id or f"{self.source}:{self.source_record_id}")

    def fingerprint(self, tenant_id: str) -> str:
        return digest(asdict(self.normalized(tenant_id)))


@dataclass(frozen=True)
class Lead:
    tenant_id: str
    lead_id: str
    dedupe_key: str
    business_name: str
    phone: str | None
    contact_name: str | None
    address: str | None
    niche: str | None
    h3_r9: str | None
    consent_reference: str | None
    stage: str
    created_at_unix: int
    updated_at_unix: int
    version: int = 1


@dataclass(frozen=True)
class ModelReview:
    model_id: str
    model_version: str
    weight_version: str
    review_state: str
    reviewer: str
    gold_lineage_id: str
    weights: dict[str, float]

    def approved(self) -> "ModelReview":
        for value in (self.model_id, self.model_version, self.weight_version, self.reviewer, self.gold_lineage_id):
            identifier(value, "model review field")
        if self.review_state != "approved" or set(self.weights) != {"territory", "engagement", "freshness", "completeness"}:
            raise LeadError("exact model/weight versions require approved human review")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > 100
               for value in self.weights.values()):
            raise LeadError("model weights are not finite reviewed values")
        return self


@dataclass(frozen=True)
class Score:
    tenant_id: str
    lead_id: str
    model_version: str
    weight_version: str
    gold_lineage_id: str
    score: float
    reasons: dict[str, float]
    created_at_unix: int


@dataclass(frozen=True)
class CadenceAction:
    tenant_id: str
    lead_id: str
    step: str
    status: str
    approval_id: str | None
    created_at_unix: int


@dataclass(frozen=True)
class TerritoryAssignment:
    tenant_id: str
    lead_id: str
    h3_r9: str
    owner_id: str
    model_version: str
    weight_version: str
    gold_lineage_id: str
    assigned_at_unix: int


@dataclass(frozen=True)
class Conversion:
    tenant_id: str
    conversion_id: str
    lead_id: str
    opportunity_id: str
    consent_reference: str
    actor: str
    created_at_unix: int


class LeadStore(Protocol):
    async def ingest(self, tenant_id: str, value: LeadInput, lead: Lead, fingerprint: str,
                     lineage: dict[str, Any]) -> Lead: ...
    async def get(self, tenant_id: str, lead_id: str) -> Lead | None: ...
    async def save(self, lead: Lead) -> None: ...
    async def add_lineage(self, tenant_id: str, lead_id: str, event: dict[str, Any]) -> None: ...
    async def add_score(self, score: Score) -> None: ...
    async def actions(self, tenant_id: str, lead_id: str) -> list[CadenceAction]: ...
    async def add_action(self, action: CadenceAction) -> None: ...
    async def conversion(self, tenant_id: str, lead_id: str) -> Conversion | None: ...
    async def add_conversion(self, conversion: Conversion) -> None: ...
    async def assignment(self, tenant_id: str, lead_id: str) -> TerritoryAssignment | None: ...
    async def add_assignment(self, assignment: TerritoryAssignment) -> None: ...


class ConsentRegistry(Protocol):
    async def granted(self, tenant_id: str, reference: str, purpose: str) -> bool: ...


class GoldGateway(Protocol):
    async def review(self, tenant_id: str, model_version: str, weight_version: str) -> ModelReview: ...
    async def features(self, tenant_id: str, lead_id: str, h3_r9: str, model_version: str, weight_version: str) -> dict[str, float | str]: ...


class LeadService:
    def __init__(self, store: LeadStore, consent: ConsentRegistry | None = None, clock=time.time, gold: GoldGateway | None = None):
        self.store, self.consent, self.clock, self.gold = store, consent, clock, gold

    @staticmethod
    def _identity(tenant_id: str, value: LeadInput) -> tuple[str, str]:
        identity = f"phone:{value.phone}" if value.phone else f"name:{value.business_name}|address:{(value.address or '').strip().casefold()}"
        key = hmac.new(tenant_id.encode(), identity.encode(), hashlib.sha256).hexdigest()
        return "lead_" + key[:24], key

    async def ingest(self, tenant_id: str, raw: LeadInput, actor: str) -> Lead:
        identifier(actor, "actor")
        value = raw.normalized(tenant_id)
        lead_id, dedupe_key = self._identity(tenant_id, value)
        now = int(self.clock())
        lead = Lead(tenant_id, lead_id, dedupe_key, value.business_name, value.phone, value.contact_name,
                    value.address, value.niche, value.h3_r9, value.consent_reference, "new", now, now)
        lineage = {"source": value.source, "source_record_id": value.source_record_id, "lineage_id": value.lineage_id,
                   "fingerprint": value.fingerprint(tenant_id), "actor": actor, "created_at_unix": now}
        return await self.store.ingest(tenant_id, value, lead, lineage["fingerprint"], lineage)

    async def transition(self, tenant_id: str, lead_id: str, target: str, actor: str) -> Lead:
        identifier(actor, "actor")
        lead = await self._lead(tenant_id, lead_id)
        if target not in STAGES or target not in TRANSITIONS[lead.stage]:
            raise LeadError(f"invalid lead transition {lead.stage} -> {target}")
        updated = replace(lead, stage=target, updated_at_unix=int(self.clock()), version=lead.version + 1)
        await self.store.save(updated)
        await self.store.add_lineage(tenant_id, lead_id, {"type": "transition", "from": lead.stage, "to": target, "actor": actor})
        return updated

    async def score_reviewed(self, tenant_id: str, lead_id: str, model_version: str, weight_version: str) -> Score:
        if self.gold is None:
            raise LeadError("reviewed gold query gateway is not configured")
        lead = await self._lead(tenant_id, lead_id)
        if not lead.h3_r9:
            raise LeadError("lead requires an H3 territory before scoring")
        review = await self.gold.review(tenant_id, model_version, weight_version)
        features = await self.gold.features(tenant_id, lead_id, lead.h3_r9, model_version, weight_version)
        return await self.score(tenant_id, lead_id, review, features)

    async def score(self, tenant_id: str, lead_id: str, review: ModelReview, gold: dict[str, float | str]) -> Score:
        model = review.approved()
        lead = await self._lead(tenant_id, lead_id)
        if not lead.h3_r9 or gold.get("h3_r9") != lead.h3_r9 or not str(gold.get("lineage_id", "")):
            raise LeadError("gold features must match the lead's exact H3 territory and lineage")
        features = {name: float(gold.get(name, 0.0)) for name in model.weights}
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in features.values()):
            raise LeadError("gold features must be normalized finite values")
        reasons = {name: round(model.weights[name] * features[name], 6) for name in features}
        raw = sum(reasons.values())
        value = round(100 / (1 + math.exp(-max(-30.0, min(30.0, raw)))), 4)
        score = Score(tenant_id, lead_id, model.model_version, model.weight_version, str(gold["lineage_id"]),
                      value, reasons, int(self.clock()))
        await self.store.add_score(score)
        await self.store.add_lineage(tenant_id, lead_id, {"type": "score", "model_version": model.model_version,
                                                           "weight_version": model.weight_version, "gold_lineage_id": score.gold_lineage_id})
        return score

    async def due_cadence(self, tenant_id: str, lead_id: str) -> CadenceAction | None:
        lead = await self._lead(tenant_id, lead_id)
        if lead.stage not in {"new", "contacted", "qualified"}:
            return None
        actions = await self.store.actions(tenant_id, lead_id)
        completed = {action.step for action in actions}
        elapsed = max(0, int(self.clock()) - lead.created_at_unix) // 86400
        for step, day in CADENCE:
            if step not in completed:
                return CadenceAction(tenant_id, lead_id, step, "requires-human-approval", None, int(self.clock())) if elapsed >= day else None
        if elapsed >= 30 and lead.stage != "recycled":
            return CadenceAction(tenant_id, lead_id, "RECYCLE", "requires-human-approval", None, int(self.clock()))
        return None

    async def record_cadence(self, tenant_id: str, lead_id: str, step: str, approval_id: str, actor: str) -> CadenceAction:
        identifier(approval_id, "approval"); identifier(actor, "actor")
        due = await self.due_cadence(tenant_id, lead_id)
        if due is None or due.step != step:
            raise LeadError("cadence action is not currently due")
        action = replace(due, status="approved-draft", approval_id=approval_id)
        await self.store.add_action(action)
        if step == "RECYCLE":
            lead = await self._lead(tenant_id, lead_id)
            await self.store.save(replace(lead, stage="recycled", updated_at_unix=int(self.clock()), version=lead.version + 1))
        await self.store.add_lineage(tenant_id, lead_id, {"type": "cadence", "step": step, "approval_id": approval_id, "actor": actor})
        return action

    async def assign_territory(self, tenant_id: str, lead_id: str, model_version: str, weight_version: str) -> TerritoryAssignment:
        existing = await self.store.assignment(tenant_id, lead_id)
        if existing:
            if (existing.model_version, existing.weight_version) != (model_version, weight_version):
                raise LeadError("territory assignment already uses a different reviewed version")
            return existing
        if self.gold is None:
            raise LeadError("reviewed gold query gateway is not configured")
        lead = await self._lead(tenant_id, lead_id)
        if not lead.h3_r9:
            raise LeadError("lead requires an H3 territory")
        review = (await self.gold.review(tenant_id, model_version, weight_version)).approved()
        features = await self.gold.features(tenant_id, lead_id, lead.h3_r9, model_version, weight_version)
        owner = str(features.get("owner_id", ""))
        identifier(owner, "territory owner")
        if features.get("h3_r9") != lead.h3_r9 or not features.get("lineage_id"):
            raise LeadError("territory assignment lacks matching gold lineage")
        assignment = TerritoryAssignment(tenant_id, lead_id, lead.h3_r9, owner, model_version, weight_version,
                                         str(features["lineage_id"]), int(self.clock()))
        await self.store.add_assignment(assignment)
        await self.store.add_lineage(tenant_id, lead_id, {"type": "territory-assignment", "owner_id": owner,
                                                           "model_version": model_version, "weight_version": weight_version})
        return assignment

    async def convert(self, tenant_id: str, lead_id: str, actor: str) -> Conversion:
        identifier(actor, "actor")
        existing = await self.store.conversion(tenant_id, lead_id)
        if existing:
            return existing
        lead = await self._lead(tenant_id, lead_id)
        if lead.stage != "opportunity" or not lead.consent_reference or self.consent is None:
            raise LeadError("conversion requires an opportunity and consent registry")
        if not await self.consent.granted(tenant_id, lead.consent_reference, "lead-conversion"):
            raise LeadError("granted lead-conversion consent is required")
        conversion = Conversion(tenant_id, "conversion_" + digest(f"{tenant_id}|{lead_id}")[:24], lead_id,
                                "opportunity_" + lead.lead_id.removeprefix("lead_"), lead.consent_reference,
                                actor, int(self.clock()))
        await self.store.add_conversion(conversion)
        current = await self._lead(tenant_id, lead_id)
        await self.store.save(replace(current, stage="converted", updated_at_unix=int(self.clock()), version=current.version + 1))
        await self.store.add_lineage(tenant_id, lead_id, {"type": "conversion", "conversion_id": conversion.conversion_id, "actor": actor})
        return conversion

    async def _lead(self, tenant_id: str, lead_id: str) -> Lead:
        identifier(tenant_id, "tenant"); identifier(lead_id, "lead")
        lead = await self.store.get(tenant_id, lead_id)
        if lead is None:
            raise LeadError("lead not found")
        return lead


class MemoryLeadStore:
    """Test-only store. Production uses PostgreSQL transactions and RLS."""
    def __init__(self):
        self.leads: dict[tuple[str, str], Lead] = {}
        self.sources: dict[tuple[str, str, str], tuple[str, str]] = {}
        self.dedupe: dict[tuple[str, str], str] = {}
        self.lineage: list[dict[str, Any]] = []
        self.scores: list[Score] = []
        self.cadence: list[CadenceAction] = []
        self.conversions: dict[tuple[str, str], Conversion] = {}
        self.assignments: dict[tuple[str, str], TerritoryAssignment] = {}
        self.lock = asyncio.Lock()

    async def ingest(self, tenant_id, value, lead, fingerprint, lineage):
        async with self.lock:
            source_key = (tenant_id, value.source, value.source_record_id)
            existing_source = self.sources.get(source_key)
            if existing_source:
                if existing_source[1] != fingerprint:
                    raise LeadError("source replay conflicts with original content")
                return self.leads[(tenant_id, existing_source[0])]
            existing_id = self.dedupe.get((tenant_id, lead.dedupe_key))
            if existing_id:
                current = self.leads[(tenant_id, existing_id)]
                self.sources[source_key] = (existing_id, fingerprint)
                self.lineage.append({"tenant_id": tenant_id, "lead_id": existing_id, "type": "duplicate-source", **lineage})
                return current
            self.leads[(tenant_id, lead.lead_id)] = lead
            self.dedupe[(tenant_id, lead.dedupe_key)] = lead.lead_id
            self.sources[source_key] = (lead.lead_id, fingerprint)
            self.lineage.append({"tenant_id": tenant_id, "lead_id": lead.lead_id, "type": "ingest", **lineage})
            return lead

    async def get(self, tenant_id, lead_id): return self.leads.get((tenant_id, lead_id))
    async def save(self, lead): self.leads[(lead.tenant_id, lead.lead_id)] = lead
    async def add_lineage(self, tenant_id, lead_id, event): self.lineage.append({"tenant_id": tenant_id, "lead_id": lead_id, **event})
    async def add_score(self, score): self.scores.append(score)
    async def actions(self, tenant_id, lead_id): return [a for a in self.cadence if (a.tenant_id, a.lead_id) == (tenant_id, lead_id)]
    async def add_action(self, action): self.cadence.append(action)
    async def conversion(self, tenant_id, lead_id): return self.conversions.get((tenant_id, lead_id))
    async def add_conversion(self, conversion): self.conversions[(conversion.tenant_id, conversion.lead_id)] = conversion
    async def assignment(self, tenant_id, lead_id): return self.assignments.get((tenant_id, lead_id))
    async def add_assignment(self, assignment): self.assignments[(assignment.tenant_id, assignment.lead_id)] = assignment
