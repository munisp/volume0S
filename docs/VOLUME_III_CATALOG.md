# Volume III video-derived service catalog

Status date: 2026-11-08. Source: Corey Ganim / Build With AI, [“I investigated 8 GitHub repos that print money”](https://youtu.be/eHljw6IG0sE). This increment translates the video's eight service concepts into VolumeOS Offers **27–34**. The catalog and console entries are complemented by a [plan-only engagement and approval workflow](VOLUME_III_ENGAGEMENTS.md); production delivery and operational acceptance remain unimplemented.

## Numbering, pricing, and source boundaries

Existing Offers 01–26 and their IDs, modules, tiers, and commercial terms are unchanged. All eight new entries are `source-only-unpriced` with empty tiers. They cannot be subscribed to or charged, and no NGN prices, FX conversions, margins, demand claims, earnings, or entitlements are inferred from the video.

The external projects below are **source inspirations, not deployed or certified VolumeOS integrations**. Exact versions, licenses, notices, dependencies, security, privacy, data flows, and delivery fitness must be reviewed before any implementation or commercial use. Example prices discussed by third parties are deliberately excluded; operators must approve VolumeOS terms separately.

Treg is discussed in the video as a lead-research service. It is not a ninth repository and is not added as an offer.

| Offer | Source inspiration | Proposed VolumeOS service boundary |
|---|---|---|
| **27 — Website Makeover Studio** (`web.makeover`) | [pbakaus/impeccable](https://github.com/pbakaus/impeccable) | Bounded website/UI improvement, design-system cleanup, responsive behavior, accessibility, and human-reviewed implementation. |
| **28 — Video Ad Packs** (`media.ads`) | [calesthio/OpenMontage](https://github.com/calesthio/OpenMontage) | Scripted, rights-cleared short-video advertisement packs with human creative review and publication approval. |
| **29 — Course Tutor** (`education.tutor`) | [HKUDS/DeepTutor](https://github.com/HKUDS/DeepTutor) | Source-grounded tutoring over approved course material, with educator review and learner-data controls. |
| **30 — AI Team Install** (`agents.teams`) | [milind-soni/OpenMausBot](https://github.com/milind-soni/OpenMausBot) | Permissioned business-assistant/agent-team setup, beginning read-only and escalating only through explicit approvals. |
| **31 — Replacement Part Design** (`cad.parts`) | [earthtojake/text-to-cad](https://github.com/earthtojake/text-to-cad) | Editable CAD concepts for non-safety-critical replacement parts, followed by measurement and qualified engineering validation. Fabrication is separate. |
| **32 — Industry Audio Briefings** (`research.briefings`) | [lfnovo/open-notebook](https://github.com/lfnovo/open-notebook) | Source-cited industry research packs and human-approved audio briefings for owners and teams. |
| **33 — Bid Radar** (`procurement.radar`) | [dgtlmoon/changedetection.io](https://github.com/dgtlmoon/changedetection.io) | Permitted public procurement-page monitoring, reviewed change alerts, and authoritative deadline verification. |
| **34 — Website Guard** (`monitoring.website`) | [louislam/uptime-kuma](https://github.com/louislam/uptime-kuma) | Configured website checks, alert routing, escalation ownership, and incident follow-up; not an uptime guarantee. |

## Customer value and engagement plays

The engagement motion is a paid discovery or fixed-scope pilot only after operator-approved pricing and a delivery workflow exist. These are sales plays, not claims that the corresponding services are already deployable.

| Offer | Value for Nigerian SMEs and institutions | Customer engagement play | Recommended pricing metric |
|---|---|---|---|
| **27 — Website Makeover Studio** | Improve trust, mobile usability, enquiries, and conversion for schools, clinics, professional firms, retailers, and hospitality businesses with dated websites. | Run a before/after review of one high-value page; agree audience, brand, content, accessibility, and conversion goals; pilot one bounded page before a wider redesign. | Setup per approved page/component scope; optional recurring optimization by review cycle. |
| **28 — Video Ad Packs** | Give SMEs reusable product, recruitment, event, and launch creative without building an internal production team. | Select one product and audience; approve script, storyboard, media rights, voice, captions, and channel formats before producing a small pack. | Setup per campaign plus per approved video pack; provider/media costs pass through with evidence. |
| **29 — Course Tutor** | Help schools, training providers, and businesses make approved material easier to study and support learners beyond classroom hours. | Choose one course and educator; test answers against a question set; review citations, misconceptions, safeguarding, consent, and escalation before a cohort pilot. | Setup per course plus recurring per active learner/cohort, with agreed included usage. |
| **30 — AI Team Install** | Reduce repetitive research, coordination, and administrative work while preserving accountable human decisions. | Map one department workflow and prohibited actions; begin read-only with synthetic or minimized data; require approval for messages, bookings, purchases, writes, and external actions. | Setup per workflow/department plus recurring per permissioned agent team and usage band. |
| **31 — Replacement Part Design** | Help workshops, manufacturers, farms, and maintenance teams prototype unavailable or obsolete non-critical parts and fixtures. | Start with a measured low-risk part; capture dimensions, fit, material, tolerances, and load; review editable CAD with a qualified engineer before any fabrication quote. | Per accepted design/revision; engineering, fabrication, materials, and shipping priced separately. |
| **32 — Industry Audio Briefings** | Turn fragmented regulations, market updates, competitor material, and internal documents into reviewable decision briefs for busy teams. | Agree one recurring decision question and approved source list; deliver a cited written brief and short audio pilot; log corrections and source coverage. | Setup per research notebook plus recurring per briefing cadence, source scope, and audio minutes. |
| **33 — Bid Radar** | Help suppliers, contractors, and consultants notice relevant tender updates without repeatedly checking many public portals. | Configure a small permitted portal/keyword set; review false positives and selector changes; verify every deadline and addendum at the authoritative source before bid decisions. | Setup per monitored source set plus recurring by sources, keywords, and reviewed-alert volume. |
| **34 — Website Guard** | Reduce unnoticed downtime for SME storefronts, school portals, booking sites, and campaign landing pages. | Define checks, locations, maintenance windows, recipients, and escalation ownership; run a controlled outage/alert drill; review incidents and false alarms monthly. | Setup per monitored estate plus recurring by endpoints, check frequency, alert channels, and escalation coverage. |

## Commercial formulas

No numeric rate is approved by this document. For each offer, the operator defines the scope, included allowance, unit, rate, tax treatment, and target margin before creating tiers.

- **Invoice:** `setup + recurring base + metered usage + pass-through + approved tax`.
- **Usage:** `ceil((quantity − included) ÷ unit) × rate`, with negative excess treated as zero.
- **Cost floor:** `direct labor + provider cost + support + infrastructure + allocated delivery overhead`.
- **Minimum net price:** `cost floor ÷ (1 − target margin)`.
- Provider, media, model, fabrication, shipping, and third-party charges require documented pass-through treatment; they are not silently included.

## Mandatory delivery and acceptance controls

| Offer | Controls required before customer delivery |
|---|---|
| **27 — Website Makeover Studio** | Written owner authorization; bounded repository/deployment access; asset and copy rights; brand approval; responsive and accessibility QA; backup/rollback; no unreviewed production deployment. |
| **28 — Video Ad Packs** | Footage, image, music, voice, and likeness rights; source/license evidence; claim review; human script/storyboard/edit approval; accessible captions where applicable; separate publication approval. |
| **29 — Course Tutor** | Curriculum/content rights; source-grounded answer tests; educator review; age-appropriate safeguarding; learner-data consent and minimization; model/data-flow review; escalation to a human educator. |
| **30 — AI Team Install** | Least privilege; credential isolation; tenant-scoped audit logs; read-only pilot; allowlisted tools/data; explicit approval for outgoing actions and writes; kill switch; no autonomous financial, legal, HR, or publication decisions. |
| **31 — Replacement Part Design** | Measured fit; editable source and export validation; material/tolerance/load review; qualified engineering approval; controlled fabrication handoff. Safety-critical, pressure, lifting, braking, medical, and regulated parts are excluded from casual pilots. |
| **32 — Industry Audio Briefings** | Approved sources and usage rights; citation-to-source checks; uncertainty and date labeling; model/data-flow review; human fact/script approval; voice/audio rights; correction and retraction process. |
| **33 — Bid Radar** | Portal terms and robots/access review; permitted collection only; selector maintenance; false-positive review; source-link evidence; authoritative deadline/addendum verification; no guarantee of tender completeness or award. |
| **34 — Website Guard** | Authorized endpoints; defined check logic and maintenance windows; monitored notification delivery; escalation ownership; controlled outage and recovery drill; incident review; no guarantee of uninterrupted availability. |

## Platform boundaries

- The catalog and plan-only engagement increments are **not eight new production service implementations**.
- The catalog API and console preview expose **34 offers** after this increment.
- Offers 27–34 have no prices, tiers, entitlements, subscription path, charge path, or production delivery path. Their separate engagement workflow only creates and reviews delivery plans.
- Existing Core subscription validation remains limited to Offers 01–08; Volume III subscription attempts are rejected.
- The console's existing Offer 19-and-above gate covers Volume II and Volume III. Unpriced rows also have no selectable tier or subscription action.
- Repository availability does not prove integration, license clearance, security, service quality, customer demand, or commercial readiness.
- Provider connectors, production persistence/RLS for delivered artifacts, observability, backup/restore, security/privacy review, service levels, operator-approved pricing, staging evidence, and customer acceptance remain open for all eight offers.
- Human approval remains mandatory before external publication, customer communication, production changes, fabrication handoff, or Studio delivery.
