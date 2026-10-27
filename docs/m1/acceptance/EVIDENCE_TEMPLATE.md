# VolumeOS M1 acceptance evidence summary

Copy this template into an operator-reviewed artifact. Complete it from real staging output; do not use it to overwrite the generated `run.json`, `preflight.json`, or `verification.json`.

## Run identity

- Environment:
- Kubernetes context:
- Reviewed source revision (full SHA):
- Deployed image/chart revisions:
- Evidence directory / immutable-storage reference:
- Preflight started / completed (UTC):
- Acceptance window (UTC and Africa/Lagos):
- Operators and reviewers:

## Requirement results

| Check | Requirement(s) | Result | Evidence artifact(s) | Exceptions / corrective action | Reviewer / date |
|---|---|---|---|---|---|
| ONB-001 | BR-5, FR-FLW-1 | blocked | | | |
| ONB-002 | FR-FLW-1, NFR-12 | blocked | | | |
| MSG-001 | BR-10, BR-18, FR-FLW-2 | blocked | | | |
| MSG-002 | BR-10, P8 | blocked | | | |
| MSG-003 | FR-MSG-6, NDPR | blocked | | | |
| MSG-004 | BR-24, FR-MSG-7 | blocked | | | |
| MSG-005 | P5, P7, FR-MSG-5 | blocked | | | |
| STU-001 | FR-AI-4, BR-10 | blocked | | | |
| STU-002 | FR-AI-2, FR-AI-4, BR-10 | blocked | | | |
| STU-003 | BR-5, BR-9 | blocked | | | |
| SEC-001 | FR-IDN-3/4, FR-EDGE-2, P5 | blocked | | | |
| SEC-002 | FR-OBS-3, NFR-7 | blocked | | | |
| DR-001 | BR-23, NFR-3/4 | blocked | | | |
| OBS-001 | FR-OBS-2, NFR-2 | blocked | | | |
| OBS-002 | FR-OBS-6, BR-18 | blocked | | | |
| COST-001 | BR-4, FR-MSG-5, FR-OBS-5 | blocked | | | |

## Measurements

- Onboarding elapsed business time (target ≤1 business day):
- Signup to first approved report (target ≤48 hours):
- Kill-switch enforcement time (target ≤5 minutes):
- Restore RPO (target ≤15 minutes):
- Restore RTO (target ≤4 hours):
- Core API monthly successful/total requests and availability (target ≥99.5%):
- Campaign cycles below 99% dispatch success (target zero):
- Login/send synthetic interval and coverage (target every 5 minutes):
- Purple-team scenarios detected (target at least 3 of 4):
- Provider-cost variance and resolution:

## Security and privacy review

- [ ] No credentials, raw tokens, recipient addresses, suppression secrets, or unnecessary personal data are present.
- [ ] Customer-owned Postiz integrations were not modified or deleted by compensation.
- [ ] Provider/customer authorization covered all test recipients and channels.
- [ ] Recovery data was isolated and handled under the operator’s retention process.
- [ ] Every artifact was reviewed before recording and its SHA-256 verification passed.

## Final decision

- Generated verifier status:
- Open failures / blocked checks:
- Corrective actions and owners:
- Next quarterly restore drill:
- Operator sign-off / date:
- Independent reviewer sign-off / date:

A signature alone does not make acceptance pass. The generated verifier must also exit successfully with all required evidence.
