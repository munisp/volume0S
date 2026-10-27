SHELL := /bin/bash
K ?= kubectl
ARGOCD_CHART_VERSION := 10.11.6
PYTHON ?= python3

.PHONY: verify test test-staging-acceptance apply-namespaces bootstrap-argocd apply-apps configure-secrets configure-m1-secrets
verify:
	$(PYTHON) scripts/verify_repo.py

test-staging-acceptance:
	$(PYTHON) -m unittest discover -s scripts/tests -v

test: verify test-staging-acceptance
	cd apps/core/svc-tenancy && go test ./...
	cd apps/core/svc-provisioning && go test ./...
	cd apps/workers/campaign-worker && $(PYTHON) -m pytest
	cd apps/workers/studio-worker && $(PYTHON) -m pytest
	cd apps/workers/synthetic-probe && $(PYTHON) -m pytest
	cd libs-rust/tb-adapter && cargo test

apply-namespaces: verify
	$(K) apply -f bootstrap/00-namespaces.yaml

bootstrap-argocd:
	helm repo add argo https://argoproj.github.io/argo-helm --force-update
	helm repo update argo
	helm upgrade --install argocd argo/argo-cd --version $(ARGOCD_CHART_VERSION) \
		-n argocd --create-namespace --wait --timeout 10m
	@echo "Initial admin password (rotate immediately):"
	@$(K) -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo

apply-apps: verify
	$(K) apply -f bootstrap/argocd/project.yaml
	$(K) apply -f bootstrap/argocd/root.yaml

configure-secrets:
	@test -n "$(DATABASE_URL)" || (echo "Set DATABASE_URL"; exit 1)
	$(K) -n core create secret generic svc-tenancy --from-literal=database-url="$(DATABASE_URL)" \
		--dry-run=client -o yaml | $(K) apply -f -
	@test -n "$(MINIO_ROOT_USER)" -a -n "$(MINIO_ROOT_PASSWORD)" || (echo "Set MINIO_ROOT_USER and MINIO_ROOT_PASSWORD"; exit 1)
	$(K) -n data create secret generic minio-root \
		--from-literal=rootUser="$(MINIO_ROOT_USER)" --from-literal=rootPassword="$(MINIO_ROOT_PASSWORD)" \
		--dry-run=client -o yaml | $(K) apply -f -
	@test -n "$(GRAFANA_ADMIN_PASSWORD)" || (echo "Set GRAFANA_ADMIN_PASSWORD"; exit 1)
	$(K) -n ops create secret generic grafana-admin \
		--from-literal=admin-user=admin --from-literal=admin-password="$(GRAFANA_ADMIN_PASSWORD)" \
		--dry-run=client -o yaml | $(K) apply -f -

configure-m1-secrets:
	@test -n "$(DATABASE_URL)" || (echo "Set DATABASE_URL before configuring tenancy messaging secrets"; exit 1)
	@test -n "$(LISTMONK_ADMIN_PASSWORD)" -a -n "$(POSTIZ_DATABASE_URL)" -a -n "$(POSTIZ_REDIS_URL)" -a -n "$(POSTIZ_JWT_SECRET)" || (echo "Set ListMonk/Postiz secret variables"; exit 1)
	@test -n "$(VOLUMEOS_SERVICE_TOKEN)" -a -n "$(AWS_ACCESS_KEY_ID)" -a -n "$(AWS_SECRET_ACCESS_KEY)" || (echo "Set service-token and object-storage variables"; exit 1)
	@test "$${#VOLUMEOS_SERVICE_TOKEN}" -ge 32 -a "$${#SUPPRESSION_HMAC_SECRET}" -ge 32 || (echo "Service and suppression secrets must contain at least 32 bytes"; exit 1)
	@test -n "$(KEYCLOAK_ADMIN_CLIENT_ID)" -a "$${#KEYCLOAK_ADMIN_CLIENT_SECRET}" -ge 16 || (echo "Set Keycloak admin client credentials"; exit 1)
	$(K) -n core create secret generic svc-tenancy --from-literal=database-url="$(DATABASE_URL)" \
		--from-literal=service-token="$(VOLUMEOS_SERVICE_TOKEN)" --from-literal=suppression-hmac-secret="$(SUPPRESSION_HMAC_SECRET)" \
		--dry-run=client -o yaml | $(K) apply -f -
	$(K) -n core create secret generic svc-provisioning --from-literal=service-token="$(VOLUMEOS_SERVICE_TOKEN)" \
		--from-literal=keycloak-admin-client-id="$(KEYCLOAK_ADMIN_CLIENT_ID)" \
		--from-literal=keycloak-admin-client-secret="$(KEYCLOAK_ADMIN_CLIENT_SECRET)" \
		--from-literal=dapr-api-token="$(DAPR_API_TOKEN)" --dry-run=client -o yaml | $(K) apply -f -
	$(K) -n core create secret generic listmonk-admin --from-literal=admin-password="$(LISTMONK_ADMIN_PASSWORD)" \
		--dry-run=client -o yaml | $(K) apply -f -
	$(K) -n core create secret generic postiz --from-literal=DATABASE_URL="$(POSTIZ_DATABASE_URL)" \
		--from-literal=REDIS_URL="$(POSTIZ_REDIS_URL)" --from-literal=JWT_SECRET="$(POSTIZ_JWT_SECRET)" \
		--dry-run=client -o yaml | $(K) apply -f -
	$(K) -n workers create secret generic campaign-worker --from-literal=service-token="$(VOLUMEOS_SERVICE_TOKEN)" \
		--from-literal=listmonk-admin-user=admin --from-literal=listmonk-admin-password="$(LISTMONK_ADMIN_PASSWORD)" \
		--dry-run=client -o yaml | $(K) apply -f -
	$(K) -n workers create secret generic studio-worker --from-literal=AWS_ACCESS_KEY_ID="$(AWS_ACCESS_KEY_ID)" \
		--from-literal=AWS_SECRET_ACCESS_KEY="$(AWS_SECRET_ACCESS_KEY)" --from-literal=AWS_DEFAULT_REGION=us-east-1 \
		--dry-run=client -o yaml | $(K) apply -f -
