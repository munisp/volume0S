# Flink connector artifacts

The reviewed image build must place the matching Flink 1.20 Kafka connector and Kafka client JARs in this directory. Pin each artifact by SHA-256 in the private image pipeline; do not download unverified JARs at container startup. Source-level tests do not assert that a Flink cluster or these runtime artifacts are present.
