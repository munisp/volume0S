"""Offer 03: WhisperX lineage, rubric scoring, accent QA, and reviewed reports."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Any

from common import Artifact, Approval, OfferError, canonical, identifier, release_artifact, sha256


@dataclass(frozen=True)
class Transcript:
    tenant_id: str
    job_id: str
    source_object_key: str
    source_sha256: str
    whisperx_version: str
    language: str
    segments: list[dict[str, Any]]
    wer: float | None = None

    def validated(self) -> "Transcript":
        identifier(self.tenant_id, "tenant"); identifier(self.job_id, "job"); identifier(self.whisperx_version, "WhisperX version")
        if not self.source_object_key.startswith(f"{self.tenant_id}/calls/{self.job_id}/") or ".." in self.source_object_key:
            raise OfferError("call audio is outside the tenant/job object boundary")
        if len(self.source_sha256) != 64 or not self.segments or not 2 <= len(self.language) <= 16:
            raise OfferError("source digest, language, and transcript segments are required")
        for segment in self.segments:
            if not isinstance(segment.get("start"), (int, float)) or not isinstance(segment.get("end"), (int, float)) \
                    or segment["start"] < 0 or segment["end"] <= segment["start"] or not str(segment.get("text", "")).strip():
                raise OfferError("invalid transcript segment")
        if self.wer is not None and (not math.isfinite(self.wer) or not 0 <= self.wer <= 1):
            raise OfferError("WER must be normalized")
        return self

    @property
    def digest(self) -> str:
        self.validated()
        return sha256(canonical({"source_sha256": self.source_sha256, "whisperx_version": self.whisperx_version,
                                 "language": self.language, "segments": self.segments, "wer": self.wer}))


def accent_qa_sample(transcript: Transcript) -> bool:
    """Stable 10% sample per tenant/job; replay never changes selection."""
    return int(hashlib.sha256(f"{transcript.tenant_id}|{transcript.job_id}".encode()).hexdigest()[:8], 16) % 10 == 0


def rubric_score(transcript: Transcript, rubric_version: str, rubric: dict[str, float], scores: dict[str, float]) -> dict[str, Any]:
    transcript.validated(); identifier(rubric_version, "rubric version")
    if not rubric or set(rubric) != set(scores) or not math.isclose(sum(rubric.values()), 1.0, abs_tol=1e-9):
        raise OfferError("rubric weights must cover every score and sum to one")
    if any(not math.isfinite(weight) or weight <= 0 for weight in rubric.values()):
        raise OfferError("rubric weights must be positive")
    if any(not math.isfinite(score) or not 0 <= score <= 5 for score in scores.values()):
        raise OfferError("rubric scores must be in the zero-to-five range")
    evidence = {category: [segment for segment in transcript.segments if category in segment.get("labels", [])] for category in rubric}
    if any(not segments for segments in evidence.values()):
        raise OfferError("every scored category requires timestamped transcript evidence")
    return {"rubric_version": rubric_version, "transcript_sha256": transcript.digest,
            "category_scores": scores, "weighted_score": round(sum(rubric[c] * scores[c] for c in rubric), 4),
            "evidence": evidence}


def build_report(transcript: Transcript, scorecard: dict[str, Any], *, qa_reviewer: str | None = None,
                 qa_reason: str | None = None, wer_threshold: float = 0.25) -> Artifact:
    if scorecard.get("transcript_sha256") != transcript.digest:
        raise OfferError("scorecard is not bound to the exact transcript")
    requires_qa = accent_qa_sample(transcript) or transcript.wer is None or transcript.wer > wer_threshold
    if requires_qa and (not qa_reviewer or not qa_reason):
        raise OfferError("accent/WER human QA is required before report creation")
    payload = report_payload(transcript, scorecard, qa_reviewer=qa_reviewer, qa_reason=qa_reason)
    return Artifact(transcript.tenant_id, "03", transcript.job_id, 1,
                    f"{transcript.tenant_id}/offers/03/{transcript.job_id}/scorecard-v1.json",
                    sha256(payload), "application/json").validated()


def report_payload(transcript: Transcript, scorecard: dict[str, Any], *, qa_reviewer: str | None = None,
                   qa_reason: str | None = None) -> bytes:
    document = {"tenant_id": transcript.tenant_id, "job_id": transcript.job_id, "offer": "03",
                "transcript_sha256": transcript.digest, "whisperx_version": transcript.whisperx_version,
                "wer": transcript.wer, "accent_qa_sample": accent_qa_sample(transcript),
                "qa_reviewer": qa_reviewer, "qa_reason": qa_reason, "scorecard": scorecard}
    return canonical(document).encode()


def release_report(artifact: Artifact, approvals: list[Approval]) -> Artifact:
    if artifact.offer != "03":
        raise OfferError("not a call-scorecard artifact")
    return release_artifact(artifact, approvals)


class WhisperXClient:
    """Real HTTP adapter for the internal GPU worker; no simulated transcription."""
    def __init__(self, transport, endpoint: str, service_token: str):
        if not endpoint.startswith("https://") or not service_token:
            raise OfferError("HTTPS WhisperX endpoint and service credential required")
        self.transport, self.endpoint, self.token = transport, endpoint.rstrip("/"), service_token

    async def transcribe(self, tenant_id: str, job_id: str, source_key: str, source_sha256: str,
                         version: str, language: str) -> Transcript:
        response = await self.transport.post(self.endpoint + "/v1/transcriptions",
            json={"source_object_key": source_key, "source_sha256": source_sha256, "model_version": version, "language": language},
            headers={"Authorization": f"Bearer {self.token}", "Idempotency-Key": f"whisperx:{tenant_id}:{job_id}", "X-VolumeOS-Tenant": tenant_id})
        response.raise_for_status(); document = response.json()
        return Transcript(tenant_id, job_id, source_key, source_sha256, version, language,
                          document["segments"], document.get("wer")).validated()
