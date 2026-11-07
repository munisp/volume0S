#!/usr/bin/env python3
"""Static contracts for playbook Volume II Offers 19–26."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []
offers_path = ROOT / "apps/core/svc-tenancy/offers.go"
offers = offers_path.read_text() if offers_path.is_file() else ""

entries = {
    "19": ("Language Multiplier", "media.dubbing"),
    "20": ("Vibe-Code Audit", "security.audit"),
    "21": ("Department Copilot", "knowledge.copilot"),
    "22": ("Intent Radar", "leads.intent"),
    "23": ("Monday Dashboard", "analytics.dashboard"),
    "24": ("Paperless Office", "documents.archive"),
    "25": ("Collections Engine", "collections.receivables"),
    "26": ("Attribution Engine", "marketing.attribution"),
}
for offer_id, (name, module) in entries.items():
    marker = f'{{ID: "{offer_id}", Name: "{name}", Module: "{module}", CommercialStatus: "operator-review-required", Tiers: []OfferTier{{'
    if offers.count(marker) != 1:
        errors.append(f"Offer {offer_id} must map once to {name}/{module} with review-required pricing")

terms = (
    '{ID: "starter", SetupMinor: 4000000, MonthlyMinor: 4500000',
    '{ID: "growth", SetupMinor: 6000000, MonthlyMinor: 7500000',
    '{ID: "pro", SetupMinor: 10000000, MonthlyMinor: 12000000',
    '{ID: "audit", PackMinor: 22000000',
    '{ID: "re-audit", MonthlyMinor: 8500000',
    '{ID: "department", SetupMinor: 12000000, MonthlyMinor: 6500000',
    '{ID: "daily-digest", SetupMinor: 3000000, MonthlyMinor: 5500000',
    '{ID: "monday", SetupMinor: 12000000, MonthlyMinor: 7000000',
    '{ID: "archive", SetupMinor: 10000000, MonthlyMinor: 5000000',
    '{ID: "collections", SetupMinor: 5000000, MonthlyMinor: 5500000',
    '{ID: "attribution", SetupMinor: 3000000, MonthlyMinor: 4000000',
)
for term in terms:
    if term not in offers:
        errors.append(f"missing Volume II commercial term: {term}")

for relative, markers in {
    "apps/core/svc-tenancy/offers_test.go": ("TestVolumeIICatalogPricingAndModuleMapping", "ValidateSubscription(\"19\"", "opportunities_per_day"),
    "apps/console/src/lib/demo.ts": ("id: '19'", "id: '20'", "id: '21'", "id: '22'", "id: '23'", "id: '24'", "id: '25'", "id: '26'"),
    "apps/console/src/App.tsx": ("offer.id >= '19'", "Catalog only", "Volume II entries are catalog-only"),
    "docs/VOLUME_II_CATALOG.md": ("pages 21–29", "Written voice-clone consent", "Written authorization", "no automated posting", "TigerBeetle as the only financial balance store", "catalog increment"),
}.items():
    path = ROOT / relative
    if not path.is_file():
        errors.append(f"missing Volume II contract: {relative}")
        continue
    text = path.read_text()
    for marker in markers:
        if marker not in text:
            errors.append(f"{relative}: missing contract {marker}")

if errors:
    print("Volume II catalog static gate FAILED:", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    raise SystemExit(1)
print("Volume II catalog static gate passed: 8 offers, 11 supplied price tiers, console and safety boundaries")
