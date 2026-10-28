import boto3
import pytest
from temporalio.exceptions import ApplicationError

from common import Artifact
from worker import OfferJob, S3ArtifactStore, validate_job


def job(offer="03"):
    return OfferJob("tenant-a", "job-1", offer, 1000, "NGN", {"cost_estimate_minor": 100, "job_ceiling_minor": 500})


def test_batch_offer_job_validation():
    validate_job(job())
    with pytest.raises(ApplicationError): validate_job(job("04"))
    with pytest.raises(ApplicationError): validate_job(OfferJob("tenant-a", "job-1", "03", 0, "NGN", {}))


def test_s3_artifact_store_checks_content_digest(monkeypatch):
    class S3:
        def __init__(self): self.objects = {}
        def put_object(self, **kwargs): self.objects[kwargs["Key"]] = kwargs
    client = S3(); store = S3ArtifactStore.__new__(S3ArtifactStore); store.bucket = "assets"; store.client = client
    artifact = Artifact("tenant-a", "03", "job-1", 1, "tenant-a/offers/03/job-1/report.json",
                        "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08", "application/json")
    with pytest.raises(Exception): store.put(artifact, b"wrong")
    store.put(artifact, b"test")
    assert client.objects[artifact.object_key]["Body"] == b"test"
    assert client.objects[artifact.object_key + ".manifest.json"]["Metadata"]["tenant-id"] == "tenant-a"
