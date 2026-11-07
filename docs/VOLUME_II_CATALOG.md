# Volume II service catalog

Status date: 2026-10-30. Source: `nigeria-volume-playbook (2).pdf`, section **4b · Volume II — Eight More Opportunities, Converted**, pages 21–29. This increment adds the eight services to the commercial catalog and console reference data. It does not implement their delivery workflows or assert operational acceptance.

## Numbering and commercial status

The playbook numbers these opportunities 09–16, but VolumeOS already uses Offer IDs 09–18 for developing-market commerce capabilities. To preserve every existing ID, Volume II opportunities are appended as **Offers 19–26**.

All prices below are supplied by the playbook, converted to NGN minor units in source, and marked `operator-review-required`. They are proposed commercial terms, not approved or active market prices. The console marks these offers **Catalog only**, and subscription validation continues to reject them pending delivery implementation.

## Catalog mapping

| Playbook opportunity | Platform offer | Service/module | Supplied terms and included outcomes |
|---|---|---|---|
| 09 — Voice Studio | **19 — Language Multiplier** | `media.dubbing` | Starter: ₦40,000 setup + ₦45,000/month for 2 videos and 1 language. Growth: ₦60,000 + ₦75,000/month for 4 videos, 2 languages, and subtitles. Pro: ₦100,000 + ₦120,000/month for 8 videos, 3 languages, and series handling. |
| 10 — Cloudflare Security Audit Skill | **20 — Vibe-Code Audit** | `security.audit` | ₦220,000 per audit with documented findings, prioritized fix list, and evidence pack; ₦85,000/month for release-driven re-audit and release-note review. |
| 11 — Anthropic Knowledge-Work Plugins | **21 — Department Copilot** | `knowledge.copilot` | ₦120,000 setup + ₦65,000/month per department for call preparation, same-day follow-up, and Monday pipeline review. |
| 12 — Agent Reach | **22 — Intent Radar** | `leads.intent` | ₦30,000 setup + ₦55,000/month for five opportunities per day, competitor gripe log, and human voice review. |
| 13 — Metabase | **23 — Monday Dashboard** | `analytics.dashboard` | ₦120,000 setup + ₦70,000/month for three data-source classes, up to ten KPI metrics, and Monday digest. |
| 14 — Paperless-ngx | **24 — Paperless Office** | `documents.archive` | ₦100,000 setup + ₦50,000/month for searchable archive, archive Q&A, and retention configuration. Bulk scanning is pass-through at cost and is not included in the setup price. |
| 15 — Invoice Ninja | **25 — Collections Engine** | `collections.receivables` | ₦50,000 setup + ₦55,000/month for four reminder steps, weekly receivables report, and human escalation. Client messaging usage remains separate. |
| 16 — Dub | **26 — Attribution Engine** | `marketing.attribution` | ₦30,000 setup + ₦40,000/month for print-ready QR artwork, monthly attribution report, WhatsApp click-through, and keyword fallback. Dub hosting or cloud charges require operator review. |

## Business value and launch controls

| Offer | Nigerian business value | Mandatory delivery/acceptance controls |
|---|---|---|
| 19 — Language Multiplier | Reuse existing creator, sermon, course, and brand footage to reach additional language audiences. | Written voice-clone consent, voice-likeness rights, per-language pilot and human reviewer, AI-dubbing disclosure, and publication approval. Low-resource language quality cannot be assumed. |
| 20 — Vibe-Code Audit | Give founders, agencies, and SMEs a prioritized security review of AI-built applications and repeat checks after releases. | Written authorization before any live testing, scoped rules of engagement, evidence-packaged findings, secure report handling, and clear statement that an audit is not a security guarantee. |
| 21 — Department Copilot | Help relationship-driven sales and service teams prepare for calls, follow up consistently, and review pipelines. | Client-owned AI seats, least-privilege CRM access, DPA/data-residency review, redaction before external AI calls, and human review of customer-facing output. |
| 22 — Intent Radar | Turn relevant public recommendation requests into a daily, reviewable lead list and learn recurring competitor complaints. | Official/permitted source routes, platform terms review, public-source minimization, no automated posting, and human voice/claim approval before publication. |
| 23 — Monday Dashboard | Give multi-branch and mid-market operators a consistent weekly view of financial, CRM, and advertising KPIs. | Tenant-scoped connectors, source-data quality reconciliation, metric definitions, approved-publication data boundaries, access control, and digest delivery evidence. |
| 24 — Paperless Office | Make legal, accounting, school, property, HR, and clinical archives easier to retrieve and protect against physical loss. | NDPR/DPA review, role-based access, encryption, client-specific retention, backup/restore, in-region or on-premise hosting where required, and special handling for sensitive records. |
| 25 — Collections Engine | Help distributors, wholesalers, manufacturers, and service firms follow up receivables consistently without relying on manual calls. | Client-approved templates and tone, days 3/7/14/30 cadence configuration, human escalation after day 30, suppression/consent policy, independently confirmed payment reconciliation, and TigerBeetle as the only financial balance store. |
| 26 — Attribution Engine | Show which banners, buses, flyers, signage, radio placements, or other campaigns generate measurable responses. | Unique placement links/codes, WhatsApp and keyword/SMS fallback, consent-aware measurement, clear attribution methodology, and no claim that clicks alone prove revenue. |

## Platform boundaries

- This is a **catalog increment**, not eight new production service implementations.
- Existing Offers 01–18 and their modules/prices are unchanged.
- The Volume II increment brought the catalog API and console preview to 26 offers. Later video-derived additions are documented separately in `docs/VOLUME_III_CATALOG.md`.
- Volume II offers display proposed tiers but cannot be subscribed to from the console. The existing Core subscription validator also rejects Offer 19 and above.
- No repository stars, video revenue claims, client counts, FX conversions, margins, or market benchmarks are used as platform facts or entitlements.
- External repositories named in the playbook are source inspirations, not deployed or certified VolumeOS integrations.
- Live provider deployment, workflow implementation, durable persistence, security/privacy review, pricing approval, service-level definitions, and customer acceptance remain open.
