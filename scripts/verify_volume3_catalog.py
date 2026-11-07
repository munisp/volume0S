#!/usr/bin/env python3
"""Static contracts for video-derived Volume III Offers 27–34."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []
offers_path = ROOT / "apps/core/svc-tenancy/offers.go"
offers = offers_path.read_text() if offers_path.is_file() else ""

entries = {
    "27": ("Website Makeover Studio", "web.makeover"),
    "28": ("Video Ad Packs", "media.ads"),
    "29": ("Course Tutor", "education.tutor"),
    "30": ("AI Team Install", "agents.teams"),
    "31": ("Replacement Part Design", "cad.parts"),
    "32": ("Industry Audio Briefings", "research.briefings"),
    "33": ("Bid Radar", "procurement.radar"),
    "34": ("Website Guard", "monitoring.website"),
}
volume3 = offers.split("// Volume III", 1)[1].split("var q1Bundles", 1)[0] if "// Volume III" in offers else ""
if not volume3:
    errors.append("missing Volume III source-boundary comment and entries")
for offer_id, (name, module) in entries.items():
    marker = f'{{ID: "{offer_id}", Name: "{name}", Module: "{module}", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{{}}}}'
    if offers.count(marker) != 1:
        errors.append(f"Offer {offer_id} must map once to {name}/{module} with no pricing")
for forbidden in ("SetupMinor", "MonthlyMinor", "PackMinor"):
    if forbidden in volume3:
        errors.append(f"Volume III must not invent commercial terms: {forbidden}")

for relative, markers in {
    "apps/core/svc-tenancy/offers_test.go": (
        "TestVolumeIIIVideoCatalogIsUnpricedAndNotSubscribable",
        "len(offers) != 34",
        "ValidateSubscription(offer.ID",
        "catalog-only Volume III offer",
    ),
    "apps/console/src/lib/demo.ts": tuple(
        f"id: '{offer_id}', name: '{name}', module: '{module}', commercial_status: 'source-only-unpriced', tiers: []"
        for offer_id, (name, module) in entries.items()
    ),
    "apps/console/src/App.tsx": (
        "offer.id >= '19'",
        "Catalog only",
        "Volume II entries are catalog-only",
        "Volume III entries follow the same rule",
        "video-derived Volume III Offers 27–34 remain unpriced",
    ),
    "docs/VOLUME_III_CATALOG.md": (
        "https://youtu.be/eHljw6IG0sE",
        "https://github.com/pbakaus/impeccable",
        "https://github.com/calesthio/OpenMontage",
        "https://github.com/HKUDS/DeepTutor",
        "https://github.com/milind-soni/OpenMausBot",
        "https://github.com/earthtojake/text-to-cad",
        "https://github.com/lfnovo/open-notebook",
        "https://github.com/dgtlmoon/changedetection.io",
        "https://github.com/louislam/uptime-kuma",
        "Treg is discussed in the video as a lead-research service",
        "source-only-unpriced",
        "Customer value and engagement plays",
        "Minimum net price",
        "qualified engineering approval",
        "read-only pilot",
        "authoritative deadline/addendum verification",
        "not eight new production service implementations",
        "34 offers",
    ),
    "docs/VOLUME_II_CATALOG.md": ("docs/VOLUME_III_CATALOG.md",),
}.items():
    path = ROOT / relative
    if not path.is_file():
        errors.append(f"missing Volume III contract: {relative}")
        continue
    text = path.read_text()
    for marker in markers:
        if marker not in text:
            errors.append(f"{relative}: missing contract {marker}")

if errors:
    print("Volume III catalog static gate FAILED:", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    raise SystemExit(1)
print("Volume III catalog static gate passed: 8 unpriced offers, 34 total, console mirror/gating, engagement and safety boundaries")
