# ADR-003: Embed reviewed M1 components without inheriting unsafe defaults

- Status: accepted
- Date: 2026-10-27

## Context

M1 requires ListMonk, Postiz, Wazuh, and a ComfyUI-shaped studio service. The original scaffold left their package sources unresolved. Review found that ListMonk's documented community chart supports external databases and existing secrets; Postiz's available chart does not provide the required secret-injection boundary; Wazuh's official base contains bootstrap secrets and generated certificate inputs.

## Decision

Use the pinned HelmForge ListMonk chart. Package the pinned upstream Postiz image with local Kubernetes manifests and existing-secret environment injection. Keep Wazuh inactive until its official manifests are wrapped in a reviewed private secret/certificate overlay. Call an operator-supplied ComfyUI API rather than selecting an unreviewed community GPU image. Record all licenses and do not relicense embedded components.

## Consequences

ListMonk and Postiz can proceed to staging after secrets and provider configuration arrive. Wazuh and the GPU runtime are not represented as live. Postiz upgrades require explicit runtime/chart compatibility review. Production acceptance still requires health, isolation, backup/restore, approval, suppression, and security drills.
