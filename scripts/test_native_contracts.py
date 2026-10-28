import hashlib
import json
from pathlib import Path

VECTORS = json.loads((Path(__file__).resolve().parents[1] / "apps/native/field-app/shared/test-vectors.json").read_text())
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def deterministic_ulid(value: str) -> str:
    bits = "".join(f"{byte:08b}" for byte in hashlib.sha256(value.encode()).digest()[:16])
    return ALPHABET[int(bits[:3], 2)] + "".join(ALPHABET[int(bits[index:index + 5], 2)] for index in range(3, 128, 5))


def test_shared_event_id_vector():
    assert deterministic_ulid(VECTORS["event_id_input"]) == VECTORS["event_id"]
    assert len(VECTORS["event_id"]) == 26


def test_shared_chunk_digest_vector():
    assert hashlib.sha256(VECTORS["chunk_fixture"].encode()).hexdigest() == VECTORS["chunk_sha256"]
    assert VECTORS["chunk_size"] == 256 * 1024
