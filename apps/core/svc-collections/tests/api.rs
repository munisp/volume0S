use std::collections::{BTreeMap, HashMap};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::Body;
use axum::http::{Request, StatusCode};
use hmac::{Hmac, Mac};
use http_body_util::BodyExt;
use serde_json::{json, Value};
use sha2::Sha512;
use tower::ServiceExt;
use volumeos_collection_adapters::processor::CollectionExpectation;
use volumeos_collection_adapters::providers::{
    CheckoutRequest, CheckoutSession, CollectionError, ConfirmationStatus, ConfirmedPayment,
    PaymentNotification, Provider, ProviderClient, ProviderFuture,
};
use volumeos_svc_collections::store::{
    CollectionStore, ReceiptRecord, ResolutionRecord, StoreError,
};
use volumeos_svc_collections::{router, AppState, ProviderClients};
use volumeos_tb_adapter::ledger::{
    Account, AccountKey, AccountSpec, CreateAccountStatus, CreateTransferStatus, Currency, Ledger,
    Transfer, Transport, TransportError, TransportFuture,
};

const TOKEN: &str = "test-service-token-with-at-least-32-bytes";
const WEBHOOK_SECRET: &str = "test-webhook-secret";

#[derive(Default)]
struct MemoryStore {
    expectations: Mutex<HashMap<(Provider, String), CollectionExpectation>>,
    receipts: Mutex<HashMap<(String, Provider, String), Value>>,
    resolutions: Mutex<HashMap<(Provider, String), Value>>,
}

#[async_trait]
impl CollectionStore for MemoryStore {
    async fn put_expectation(
        &self,
        provider: Provider,
        reference: &str,
        expectation: &CollectionExpectation,
    ) -> Result<(), StoreError> {
        let mut values = self.expectations.lock().unwrap();
        let key = (provider, reference.to_owned());
        if let Some(existing) = values.get(&key) {
            if existing != expectation {
                return Err(StoreError::Conflict);
            }
        } else {
            values.insert(key, expectation.clone());
        }
        Ok(())
    }

    async fn expectation(
        &self,
        provider: Provider,
        reference: &str,
    ) -> Result<Option<CollectionExpectation>, StoreError> {
        Ok(self
            .expectations
            .lock()
            .unwrap()
            .get(&(provider, reference.to_owned()))
            .cloned())
    }

    async fn record_receipt(&self, receipt: &ReceiptRecord) -> Result<(), StoreError> {
        let mut values = self.receipts.lock().unwrap();
        let key = (
            receipt.scope_tenant_id.clone(),
            receipt.provider,
            receipt.reference.clone(),
        );
        if let Some(existing) = values.get(&key) {
            if existing != &receipt.payload {
                return Err(StoreError::Conflict);
            }
        } else {
            values.insert(key, receipt.payload.clone());
        }
        Ok(())
    }

    async fn receipt(
        &self,
        tenant_id: &str,
        provider: Provider,
        reference: &str,
    ) -> Result<Option<Value>, StoreError> {
        Ok(self
            .receipts
            .lock()
            .unwrap()
            .get(&(tenant_id.to_owned(), provider, reference.to_owned()))
            .cloned())
    }

    async fn reconciliation_receipt(
        &self,
        provider: Provider,
        reference: &str,
    ) -> Result<Option<Value>, StoreError> {
        Ok(self
            .receipts
            .lock()
            .unwrap()
            .iter()
            .find(|((_, receipt_provider, receipt_reference), _)| {
                *receipt_provider == provider && receipt_reference == reference
            })
            .map(|(_, receipt)| receipt.clone()))
    }

    async fn list_open_suspense(&self, limit: u32) -> Result<Vec<Value>, StoreError> {
        let resolutions = self.resolutions.lock().unwrap();
        let mut receipts: Vec<_> = self
            .receipts
            .lock()
            .unwrap()
            .iter()
            .filter(|((_, provider, reference), receipt)| {
                receipt.get("disposition").and_then(Value::as_str) == Some("suspense")
                    && !resolutions.contains_key(&(*provider, reference.clone()))
            })
            .map(|(_, receipt)| receipt.clone())
            .collect();
        receipts.sort_by_key(|receipt| {
            receipt
                .get("event_id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned()
        });
        receipts.truncate(limit as usize);
        Ok(receipts)
    }

    async fn record_resolution(&self, resolution: &ResolutionRecord) -> Result<(), StoreError> {
        let key = (resolution.provider, resolution.reference.clone());
        let mut values = self.resolutions.lock().unwrap();
        if let Some(existing) = values.get(&key) {
            if existing != &resolution.payload {
                return Err(StoreError::Conflict);
            }
        } else {
            values.insert(key, resolution.payload.clone());
        }
        Ok(())
    }

    async fn ready(&self) -> Result<(), StoreError> {
        Ok(())
    }
}

#[derive(Default)]
struct MockState {
    accounts: HashMap<u128, Account>,
    transfers: HashMap<u128, Transfer>,
}

#[derive(Default)]
struct MockTransport {
    state: Mutex<MockState>,
}

impl Transport for MockTransport {
    fn create_account(
        &self,
        account: Account,
    ) -> TransportFuture<'_, Result<CreateAccountStatus, TransportError>> {
        Box::pin(async move {
            let mut state = self.state.lock().unwrap();
            if state.accounts.contains_key(&account.id) {
                return Ok(CreateAccountStatus::Exists);
            }
            state.accounts.insert(account.id, account);
            Ok(CreateAccountStatus::Created)
        })
    }

    fn lookup_account(
        &self,
        id: u128,
    ) -> TransportFuture<'_, Result<Option<Account>, TransportError>> {
        Box::pin(async move { Ok(self.state.lock().unwrap().accounts.get(&id).copied()) })
    }

    fn create_transfer(
        &self,
        transfer: Transfer,
    ) -> TransportFuture<'_, Result<CreateTransferStatus, TransportError>> {
        Box::pin(async move {
            let mut state = self.state.lock().unwrap();
            if state.transfers.contains_key(&transfer.id) {
                return Ok(CreateTransferStatus::Exists);
            }
            if transfer.flags != 0
                || !state.accounts.contains_key(&transfer.debit_account_id)
                || !state.accounts.contains_key(&transfer.credit_account_id)
            {
                return Ok(CreateTransferStatus::UnknownAccount);
            }
            let debit = state.accounts.get_mut(&transfer.debit_account_id).unwrap();
            debit.debits_posted += transfer.amount;
            let credit = state.accounts.get_mut(&transfer.credit_account_id).unwrap();
            credit.credits_posted += transfer.amount;
            state.transfers.insert(transfer.id, transfer);
            Ok(CreateTransferStatus::Created)
        })
    }

    fn lookup_transfer(
        &self,
        id: u128,
    ) -> TransportFuture<'_, Result<Option<Transfer>, TransportError>> {
        Box::pin(async move { Ok(self.state.lock().unwrap().transfers.get(&id).copied()) })
    }
}

struct MockProvider {
    payment: Mutex<ConfirmedPayment>,
}

impl ProviderClient for MockProvider {
    fn initialize<'a>(
        &'a self,
        request: &'a CheckoutRequest,
    ) -> ProviderFuture<'a, Result<CheckoutSession, CollectionError>> {
        let session = CheckoutSession {
            provider: request.provider,
            reference: request.reference.clone(),
            provider_session_id: "checkout-session-1".to_owned(),
            checkout_url: "https://checkout.example/session-1".to_owned(),
        };
        Box::pin(async move { Ok(session) })
    }

    fn confirm<'a>(
        &'a self,
        _notification: &'a PaymentNotification,
    ) -> ProviderFuture<'a, Result<ConfirmedPayment, CollectionError>> {
        let payment = self.payment.lock().unwrap().clone();
        Box::pin(async move { Ok(payment) })
    }
}

fn confirmed() -> ConfirmedPayment {
    ConfirmedPayment {
        provider: Provider::Paystack,
        provider_payment_id: "provider-payment-1".to_owned(),
        reference: "topup-1".to_owned(),
        amount_minor: 125_000,
        currency: Currency::Ngn,
        status: ConfirmationStatus::Settled,
        tenant_hint: Some("tenant-a".to_owned()),
    }
}

async fn app() -> (axum::Router, Arc<AppState<MockTransport>>) {
    let ledger = Ledger::new(MockTransport::default());
    for key in [
        AccountKey::ProviderSettlement {
            provider: "paystack".to_owned(),
        },
        AccountKey::CollectionSuspense {
            provider: "paystack".to_owned(),
        },
        AccountKey::tenant_prepaid("tenant-a"),
    ] {
        ledger
            .provision_account(&AccountSpec::new(key, Currency::Ngn).unwrap())
            .await
            .unwrap();
    }
    let provider: Arc<dyn ProviderClient> = Arc::new(MockProvider {
        payment: Mutex::new(confirmed()),
    });
    let providers = ProviderClients::new(
        BTreeMap::from([
            ("paystack", provider.clone()),
            ("flutterwave", provider.clone()),
            ("moniepoint", provider),
        ]),
        BTreeMap::from([
            ("paystack", WEBHOOK_SECRET.to_owned()),
            ("flutterwave", WEBHOOK_SECRET.to_owned()),
            ("moniepoint", WEBHOOK_SECRET.to_owned()),
        ]),
    )
    .unwrap();
    let state = Arc::new(
        AppState::new(
            ledger,
            Arc::new(MemoryStore::default()),
            providers,
            TOKEN.to_owned(),
        )
        .unwrap(),
    );
    (router(state.clone()), state)
}

async fn response(app: &axum::Router, request: Request<Body>) -> (StatusCode, Value) {
    let response = app.clone().oneshot(request).await.unwrap();
    let status = response.status();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    let body = if bytes.is_empty() {
        json!({})
    } else {
        serde_json::from_slice(&bytes).unwrap_or_else(|error| {
            panic!(
                "non-JSON response with status {status}: {error}; body={}",
                String::from_utf8_lossy(&bytes)
            )
        })
    };
    (status, body)
}

fn service_request(method: &str, path: &str, tenant: &str, body: Option<Value>) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(path)
        .header("content-type", "application/json")
        .header("authorization", format!("Bearer {TOKEN}"))
        .header("x-volumeos-tenant", tenant)
        .body(match body {
            Some(body) => Body::from(serde_json::to_vec(&body).unwrap()),
            None => Body::empty(),
        })
        .unwrap()
}

fn signature(body: &[u8]) -> String {
    let mut mac = <Hmac<Sha512> as Mac>::new_from_slice(WEBHOOK_SECRET.as_bytes()).unwrap();
    mac.update(body);
    hex::encode(mac.finalize().into_bytes())
}

async fn register_expectation(app: &axum::Router) {
    let (status, _) = response(
        app,
        service_request(
            "PUT",
            "/v1/tenants/tenant-a/collection-expectations/paystack/topup-1",
            "tenant-a",
            Some(json!({"amount_minor":"125000","currency":"NGN"})),
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
}

#[tokio::test]
async fn signed_confirmed_webhook_credits_once_and_persists_receipt() {
    let (app, state) = app().await;
    register_expectation(&app).await;
    let body = serde_json::to_vec(&json!({
        "event":"charge.success",
        "data":{"id":12345,"reference":"topup-1"}
    }))
    .unwrap();
    for _ in 0..2 {
        let request = Request::builder()
            .method("POST")
            .uri("/v1/collections/webhooks/paystack")
            .header("content-type", "application/json")
            .header("x-paystack-signature", signature(&body))
            .body(Body::from(body.clone()))
            .unwrap();
        let (status, receipt) = response(&app, request).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(receipt["disposition"], "credited");
        assert_eq!(receipt["tenant_id"], "tenant-a");
    }
    let balance = state
        .ledger
        .balance(&AccountSpec::new(AccountKey::tenant_prepaid("tenant-a"), Currency::Ngn).unwrap())
        .await
        .unwrap();
    assert_eq!(balance.available_minor, Some(125_000));
    let (status, receipt) = response(
        &app,
        service_request(
            "GET",
            "/v1/tenants/tenant-a/collection-expectations/paystack/topup-1",
            "tenant-a",
            None,
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(receipt["disposition"], "credited");
    assert_eq!(
        state
            .ledger
            .transport()
            .state
            .lock()
            .unwrap()
            .transfers
            .len(),
        1
    );
}

#[tokio::test]
async fn invalid_signature_and_cross_tenant_access_fail_closed() {
    let (app, _) = app().await;
    register_expectation(&app).await;
    let body = br#"{"event":"charge.success","data":{"id":12345,"reference":"topup-1"}}"#;
    let request = Request::builder()
        .method("POST")
        .uri("/v1/collections/webhooks/paystack")
        .header("x-paystack-signature", "00")
        .body(Body::from(body.as_slice()))
        .unwrap();
    let (status, _) = response(&app, request).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    let (status, _) = response(
        &app,
        service_request(
            "GET",
            "/v1/tenants/tenant-a/collection-expectations/paystack/topup-1",
            "tenant-b",
            None,
        ),
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn expectation_replay_cannot_change_money_fields() {
    let (app, _) = app().await;
    register_expectation(&app).await;
    let (status, body) = response(
        &app,
        service_request(
            "PUT",
            "/v1/tenants/tenant-a/collection-expectations/paystack/topup-1",
            "tenant-a",
            Some(json!({"amount_minor":"125001","currency":"NGN"})),
        ),
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["error"]["code"], "collection_conflict");
}

#[tokio::test]
async fn checkout_initialization_binds_provider_session_to_immutable_expectation() {
    let (app, _) = app().await;
    let path = "/v1/tenants/tenant-a/checkouts/paystack/topup-checkout-1";
    let checkout = json!({
        "amount_minor":"125000",
        "currency":"NGN",
        "customer_email":"owner@example.com",
        "redirect_url":"https://app.example/billing/callback"
    });
    let (status, body) = response(
        &app,
        service_request("POST", path, "tenant-a", Some(checkout.clone())),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["reference"], "topup-checkout-1");
    assert_eq!(body["provider_session_id"], "checkout-session-1");
    assert_eq!(body["checkout_url"], "https://checkout.example/session-1");
    assert_eq!(body["status"], "awaiting_confirmation");

    let mut changed = checkout;
    changed["amount_minor"] = json!("125001");
    let (status, _) = response(
        &app,
        service_request("POST", path, "tenant-a", Some(changed)),
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
}

#[tokio::test]
async fn platform_review_resolves_exact_suspense_once_with_an_immutable_audit() {
    let (app, state) = app().await;
    let body = serde_json::to_vec(&json!({
        "event":"charge.success",
        "data":{"id":12345,"reference":"topup-1"}
    }))
    .unwrap();
    let request = Request::builder()
        .method("POST")
        .uri("/v1/collections/webhooks/paystack")
        .header("x-paystack-signature", signature(&body))
        .body(Body::from(body))
        .unwrap();
    let (status, receipt) = response(&app, request).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(receipt["disposition"], "suspense");

    let (status, denied) = response(
        &app,
        service_request(
            "GET",
            "/v1/tenants/platform/collection-suspense",
            "tenant-a",
            None,
        ),
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(denied["error"]["code"], "tenant_scope_mismatch");

    let (status, queue) = response(
        &app,
        service_request(
            "GET",
            "/v1/tenants/platform/collection-suspense?limit=100",
            "platform",
            None,
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(queue["receipts"].as_array().unwrap().len(), 1);

    let path = "/v1/tenants/platform/collection-suspense/paystack/topup-1/resolutions";
    let resolution = json!({
        "tenant_id":"tenant-a",
        "approved_by":"finance-1",
        "reason":"Provider reference matched the signed client invoice"
    });
    for _ in 0..2 {
        let (status, resolved) = response(
            &app,
            service_request("POST", path, "platform", Some(resolution.clone())),
        )
        .await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(resolved["schema"], "volumeos.collection-resolution.v1");
        assert_eq!(resolved["tenant_id"], "tenant-a");
        assert_eq!(resolved["amount_minor"], "125000");
        assert_eq!(resolved["approved_by"], "finance-1");
    }
    let balance = state
        .ledger
        .balance(&AccountSpec::new(AccountKey::tenant_prepaid("tenant-a"), Currency::Ngn).unwrap())
        .await
        .unwrap();
    assert_eq!(balance.available_minor, Some(125_000));
    assert_eq!(
        state
            .ledger
            .transport()
            .state
            .lock()
            .unwrap()
            .transfers
            .len(),
        2
    );

    let mut changed = resolution;
    changed["tenant_id"] = json!("tenant-b");
    let (status, _) = response(
        &app,
        service_request("POST", path, "platform", Some(changed)),
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);

    let (status, queue) = response(
        &app,
        service_request(
            "GET",
            "/v1/tenants/platform/collection-suspense",
            "platform",
            None,
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(queue["receipts"].as_array().unwrap().len(), 0);
}
