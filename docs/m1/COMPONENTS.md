# M1 embedded-component review

| Component | Upstream and license | Selected package | Review decision |
|---|---|---|---|
| ListMonk | `knadh/listmonk`, AGPL-3.0 | HelmForge `listmonk` chart 1.2.3, app 6.2.0; chart Apache-2.0 | Community chart is linked by ListMonk's installation documentation. External DB and existing secrets are supported. Deploy unmodified; retain upstream license and source offer obligations. |
| Postiz | `gitroomhq/postiz-app`, AGPL-3.0 | Upstream image `ghcr.io/gitroomhq/postiz-app:v1.3.0` with local Kubernetes packaging | Official chart 1.0.5 packages this app version but lacks existing-secret env support and its defaults are unsafe for shared environments. Local packaging supplies no application code and injects secrets through `envFrom`. Upgrade requires a new chart/runtime compatibility review, including Temporal requirements in newer releases. |
| Wazuh | `wazuh/wazuh-kubernetes`, GPL-2.0 | Official `v4.14.9` Kustomize source | Do not auto-sync upstream directly: its base generates bootstrap secrets and includes credential manifests. Activation waits for a reviewed SOPS/private overlay with fresh certificates and credentials. |
| ComfyUI | `Comfy-Org/ComfyUI`, GPL-3.0 | Studio worker calls an operator-supplied ComfyUI API; workflow graph versioned in Git | No unreviewed third-party container is introduced. GPU runtime/model selection and model licenses remain deployment acceptance items. |

Embedded copyleft components stay separate upstream processes. Their licenses are not relicensed by VolumeOS's Apache-2.0 license. Any modified distribution or network-use source obligations must be reviewed before an open-core or hosted release.
