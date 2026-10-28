# Open-core and external operators

The repository root is Apache-2.0 licensed. Third-party projects retain their own licenses; inclusion here does not relicense TigerBeetle, Temporal, Keycloak, ComfyUI, OpenSearch, Wazuh, OpenCTI, Fluvio, Kafka, Delta, Spark, Flink, Sedona, Ray, DataFusion, mobile libraries, or AI/provider services. Mojaloop remains disabled and requires its separate Phase-3 licensing review.

## Operator boundary

An external operator must supply its own domain, OIDC realm/keys, PostgreSQL, Temporal, object storage, Kafka/Fluvio, TigerBeetle, secret manager, container registry, DNS/TLS, provider accounts, Wazuh/OpenCTI secrets, and signed mobile release credentials. No VolumeOS bootstrap credential, production tenant, phone number, suppression secret, or provider token belongs in the repository.

`deploy/examples/` contains non-routable hosts and secret references only. Before an external deployment, run:

```sh
python3 scripts/verify_repo.py
python3 scripts/verify_m3.py
python3 scripts/verify_m4.py
python3 scripts/verify_native.py
python3 scripts/external_operator_preflight.py --config <operator-config.json>
```

Source verification does not create clusters, publish images, configure providers, sign mobile binaries, or certify an operator. Those remain separate evidence-backed gates.
