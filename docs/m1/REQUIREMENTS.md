# M1 requirement matrix

Scope comes from the architecture roadmap: **Content Cadence, Shopfront Studio, and onboarding**, with ListMonk and Postiz embedded per tenant. Offer 01 is included in the commercial catalog and email adapter because it is the ListMonk service line. Other service lines remain outside this slice.

Status terms match `docs/REQUIREMENTS.md`: **tested**, **configured**, **blocked**, or **later**. No status here substitutes for staging acceptance.

| Requirement | M1 implementation | Status |
|---|---|---|
| Q1 offer/pricing model | Versioned NGN minor-unit catalog for offers 01/02/06 and the launch combo; prices remain operator-review-required | tested |
| MSG-3 ListMonk | Pinned HelmForge chart, external PostgreSQL, existing-secret credentials, no ingress; API adapter requires tenant-list binding | configured / adapter tested |
| MSG-4 Postiz | Pinned upstream image, external PostgreSQL/Redis, existing-secret credentials, no ingress; approval required before API scheduling | configured / adapter tested |
| MSG-5 usage events | Dispatch result creates a tenant/channel/campaign/cost usage record at the event boundary; Kafka publisher integration is configured separately | tested / configured |
| MSG-6 suppression | Channel suppression check before every provider call; fail closed | tested |
| MSG-7 kill switch | Tenant suspension check before every provider call; fail closed | tested |
| FLW-1 onboarding | Durable identity → tenancy → ListMonk → Postiz → welcome saga with reverse compensation; TigerBeetle account creation remains M2 | tested / configured |
| FLW-2 cadence | Existing approval signal is bound to provider dispatch; staging four-channel E2E remains | configured |
| AI-4 studio | Versioned ComfyUI graph, upload/submit/poll activities, tenant-prefixed asset keys, QA approve/reject signal, delivery only after approval | tested / configured |
| OBS-2 SLO dashboards | M1 dashboard definitions and live traces require cluster deployment | later in M1 acceptance |
| OBS-3 Wazuh | Official v4.14.9 source reviewed; upstream bootstrap credentials/certificates must be replaced by the secret pipeline before activation | blocked |
| M1 outcome | First paying clients onboarded end to end in staging | blocked on real cluster, providers, OIDC/MFA, and acceptance evidence |

## Commercial-data boundary

The Q1 launch kit says its founding rates are valid for 90 days. The catalog therefore records the supplied price book without asserting that those prices are currently active. An operator must review and activate a dated price book before using it for quotes or billing. No billing postings are created in M1; TigerBeetle remains M2.

## Acceptance still required

1. Render and sync pinned components in staging; verify their chart/image values against upstream.
2. Create per-tenant ListMonk lists and Postiz channels through admin workflows; never share API credentials across tenants.
3. Prove suppressed contacts and suspended tenants produce zero provider calls.
4. Run onboarding with injected failures and verify every compensation action and audit event.
5. Run Content Cadence across email, SMS, WhatsApp, and social; only approved content may publish.
6. Run a real GPU-backed ComfyUI pack, reject one asset with a reason, and verify rejected assets never reach delivery prefixes.
7. Produce 48-hour turnaround, usage/provider-cost reconciliation, and SLO dashboard evidence.
