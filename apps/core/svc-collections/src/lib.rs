//! Signed payment collection ingress with independent provider confirmation.
//!
//! No webhook body field directly grants prepaid value. Only a settled provider
//! API response reaches TigerBeetle; mismatches are posted to provider suspense.

pub mod store;

use std::collections::BTreeMap;
use std::fmt;
use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::{DefaultBodyLimit, Path, Query, State};
use axum::http::header::AUTHORIZATION;
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post, put};
use axum::{Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};
use store::{CollectionStore, ReceiptRecord, ResolutionRecord, StoreError};
use volumeos_collection_adapters::processor::{
    process_collection, CollectionDisposition, CollectionExpectation, CollectionReceipt,
    ProcessingError,
};
use volumeos_collection_adapters::providers::{
    verify_and_parse, CheckoutRequest, CollectionError, FlutterwaveClient, MoniepointClient,
    PaystackClient, Provider, ProviderClient,
};
use volumeos_tb_adapter::ledger::{
    funding_id, Currency, Ledger, MoneyError, SuspenseResolutionRequest, Transport,
};

pub struct ProviderClients {
    clients: BTreeMap<&'static str, Arc<dyn ProviderClient>>,
    webhook_secrets: BTreeMap<&'static str, String>,
}

impl ProviderClients {
    pub fn new(
        clients: BTreeMap<&'static str, Arc<dyn ProviderClient>>,
        webhook_secrets: BTreeMap<&'static str, String>,
    ) -> Result<Self, ApiError> {
        for provider in [
            Provider::Paystack,
            Provider::Flutterwave,
            Provider::Moniepoint,
        ] {
            if !clients.contains_key(provider.as_str())
                || webhook_secrets
                    .get(provider.as_str())
                    .is_none_or(|secret| secret.is_empty())
            {
                return Err(ApiError::new(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "provider_configuration",
                    "all collection providers and webhook secrets must be configured",
                ));
            }
        }
        Ok(Self {
            clients,
            webhook_secrets,
        })
    }

    pub fn from_env() -> Result<Self, ApiError> {
        let mut clients: BTreeMap<&'static str, Arc<dyn ProviderClient>> = BTreeMap::new();
        clients.insert(
            "paystack",
            Arc::new(PaystackClient::new(
                &env_or("PAYSTACK_API_URL", "https://api.paystack.co"),
                required_env("PAYSTACK_API_SECRET")?,
            )?),
        );
        clients.insert(
            "flutterwave",
            Arc::new(FlutterwaveClient::new(
                &env_or("FLUTTERWAVE_API_URL", "https://api.flutterwave.cloud"),
                &env_or(
                    "FLUTTERWAVE_TOKEN_URL",
                    "https://idp.flutterwave.com/realms/flutterwave/protocol/openid-connect/token",
                ),
                required_env("FLUTTERWAVE_CLIENT_ID")?,
                required_env("FLUTTERWAVE_CLIENT_SECRET")?,
            )?),
        );
        clients.insert(
            "moniepoint",
            Arc::new(MoniepointClient::new(
                &env_or("MONIEPOINT_API_URL", "https://api.monnify.com"),
                required_env("MONIEPOINT_CLIENT_KEY")?,
                required_env("MONIEPOINT_CLIENT_SECRET")?,
                required_env("MONIEPOINT_CONTRACT_CODE")?,
            )?),
        );
        let secrets = BTreeMap::from([
            ("paystack", required_env("PAYSTACK_WEBHOOK_SECRET")?),
            ("flutterwave", required_env("FLUTTERWAVE_WEBHOOK_SECRET")?),
            ("moniepoint", required_env("MONIEPOINT_WEBHOOK_SECRET")?),
        ]);
        Self::new(clients, secrets)
    }

    fn client(&self, provider: Provider) -> &dyn ProviderClient {
        self.clients
            .get(provider.as_str())
            .expect("validated provider registry")
            .as_ref()
    }

    fn secret(&self, provider: Provider) -> &str {
        self.webhook_secrets
            .get(provider.as_str())
            .expect("validated webhook secrets")
    }
}

pub struct AppState<T: Transport> {
    pub ledger: Ledger<T>,
    pub store: Arc<dyn CollectionStore>,
    pub providers: ProviderClients,
    service_token: String,
}

impl<T: Transport> AppState<T> {
    pub fn new(
        ledger: Ledger<T>,
        store: Arc<dyn CollectionStore>,
        providers: ProviderClients,
        service_token: String,
    ) -> Result<Self, ApiError> {
        if service_token.len() < 32 {
            return Err(ApiError::new(
                StatusCode::INTERNAL_SERVER_ERROR,
                "service_configuration",
                "VOLUMEOS_SERVICE_TOKEN must contain at least 32 bytes",
            ));
        }
        Ok(Self {
            ledger,
            store,
            providers,
            service_token,
        })
    }
}

pub fn router<T: Transport + 'static>(state: Arc<AppState<T>>) -> Router {
    Router::new()
        .route(
            "/health/live",
            get(|| async { Json(json!({"status":"ok"})) }),
        )
        .route("/health/ready", get(ready::<T>))
        .route(
            "/v1/tenants/:tenant/checkouts/:provider/:reference",
            post(start_checkout::<T>),
        )
        .route(
            "/v1/tenants/:tenant/collection-expectations/:provider/:reference",
            put(put_expectation::<T>).get(get_receipt::<T>),
        )
        .route("/v1/collections/webhooks/:provider", post(webhook::<T>))
        .route(
            "/v1/tenants/platform/collection-suspense",
            get(list_open_suspense::<T>),
        )
        .route(
            "/v1/tenants/platform/collection-suspense/:provider/:reference/resolutions",
            post(resolve_suspense::<T>),
        )
        .layer(DefaultBodyLimit::max(1024 * 1024))
        .with_state(state)
}

#[derive(Clone, Copy, Debug, Deserialize)]
pub enum CurrencyInput {
    NGN,
    USD,
}

impl From<CurrencyInput> for Currency {
    fn from(value: CurrencyInput) -> Self {
        match value {
            CurrencyInput::NGN => Self::Ngn,
            CurrencyInput::USD => Self::Usd,
        }
    }
}

#[derive(Debug, Deserialize)]
pub struct ExpectationRequest {
    pub amount_minor: String,
    pub currency: CurrencyInput,
}

#[derive(Debug, Deserialize)]
pub struct CheckoutHttpRequest {
    pub amount_minor: String,
    pub currency: CurrencyInput,
    pub customer_email: String,
    pub redirect_url: String,
}

#[derive(Debug, Deserialize)]
pub struct SuspenseResolutionHttpRequest {
    pub tenant_id: String,
    pub approved_by: String,
    pub reason: String,
}

#[derive(Debug, Deserialize)]
pub struct ReviewQuery {
    #[serde(default = "default_review_limit")]
    pub limit: u32,
}

fn default_review_limit() -> u32 {
    100
}

#[derive(Debug)]
pub struct ApiError {
    status: StatusCode,
    code: &'static str,
    message: String,
}

impl ApiError {
    fn new(status: StatusCode, code: &'static str, message: impl Into<String>) -> Self {
        Self {
            status,
            code,
            message: message.into(),
        }
    }

    fn invalid(message: impl Into<String>) -> Self {
        Self::new(StatusCode::BAD_REQUEST, "invalid_request", message)
    }
}

impl fmt::Display for ApiError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for ApiError {}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (
            self.status,
            Json(json!({"error":{"code":self.code,"message":self.message}})),
        )
            .into_response()
    }
}

impl From<CollectionError> for ApiError {
    fn from(value: CollectionError) -> Self {
        match value {
            CollectionError::InvalidSignature => Self::new(
                StatusCode::UNAUTHORIZED,
                "invalid_signature",
                "webhook signature validation failed",
            ),
            CollectionError::InvalidPayload(message) => Self::invalid(message),
            CollectionError::Provider(message) => Self::new(
                StatusCode::BAD_GATEWAY,
                "provider_confirmation_failed",
                message,
            ),
        }
    }
}

impl From<ProcessingError> for ApiError {
    fn from(value: ProcessingError) -> Self {
        match value {
            ProcessingError::Confirmation(error) => error.into(),
            ProcessingError::InvalidConfirmation(message) => Self::new(
                StatusCode::BAD_GATEWAY,
                "invalid_provider_confirmation",
                message,
            ),
            ProcessingError::Ledger(error) => error.into(),
        }
    }
}

impl From<MoneyError> for ApiError {
    fn from(value: MoneyError) -> Self {
        let (status, code) = match value {
            MoneyError::InvalidInput(_) => (StatusCode::BAD_REQUEST, "invalid_request"),
            MoneyError::IdempotencyConflict | MoneyError::AlreadySettled => {
                (StatusCode::CONFLICT, "ledger_idempotency_conflict")
            }
            MoneyError::UnknownAccount | MoneyError::UnknownReservation => {
                (StatusCode::NOT_FOUND, "ledger_account_not_found")
            }
            MoneyError::ReservationExpired | MoneyError::InsufficientFunds => {
                (StatusCode::CONFLICT, "ledger_posting_conflict")
            }
            MoneyError::Transport(_) => (StatusCode::SERVICE_UNAVAILABLE, "ledger_unavailable"),
            MoneyError::InvariantViolation(_) => {
                (StatusCode::INTERNAL_SERVER_ERROR, "ledger_invariant")
            }
        };
        Self::new(status, code, value.to_string())
    }
}

impl From<StoreError> for ApiError {
    fn from(value: StoreError) -> Self {
        match value {
            StoreError::Conflict => Self::new(
                StatusCode::CONFLICT,
                "collection_conflict",
                value.to_string(),
            ),
            StoreError::InvalidRecord => Self::new(
                StatusCode::INTERNAL_SERVER_ERROR,
                "invalid_collection_record",
                value.to_string(),
            ),
            StoreError::Transport(message) => Self::new(
                StatusCode::SERVICE_UNAVAILABLE,
                "collection_store_unavailable",
                message,
            ),
        }
    }
}

async fn ready<T: Transport + 'static>(
    State(state): State<Arc<AppState<T>>>,
) -> Result<Json<Value>, ApiError> {
    state.store.ready().await?;
    Ok(Json(json!({"status":"ready"})))
}

async fn start_checkout<T: Transport + 'static>(
    State(state): State<Arc<AppState<T>>>,
    Path((tenant, provider, reference)): Path<(String, String, String)>,
    headers: HeaderMap,
    Json(request): Json<CheckoutHttpRequest>,
) -> Result<Json<Value>, ApiError> {
    authorize(&headers, &state.service_token, &tenant)?;
    let provider = parse_provider(&provider)?;
    validate_reference(&reference)?;
    let amount_minor = request.amount_minor.parse::<u128>().map_err(|_| {
        ApiError::invalid("amount_minor must be an unsigned 128-bit integer string")
    })?;
    if amount_minor == 0 {
        return Err(ApiError::invalid("collection amount must be non-zero"));
    }
    let currency = Currency::from(request.currency);
    let expectation = CollectionExpectation {
        tenant_id: tenant.clone(),
        amount_minor,
        currency,
    };
    state
        .store
        .put_expectation(provider, &reference, &expectation)
        .await?;
    let checkout = state
        .providers
        .client(provider)
        .initialize(&CheckoutRequest {
            provider,
            tenant_id: tenant,
            reference,
            amount_minor,
            currency,
            customer_email: request.customer_email,
            redirect_url: request.redirect_url,
        })
        .await?;
    Ok(Json(json!({
        "provider": checkout.provider.as_str(),
        "reference": checkout.reference,
        "provider_session_id": checkout.provider_session_id,
        "checkout_url": checkout.checkout_url,
        "amount_minor": amount_minor.to_string(),
        "currency": currency.as_str(),
        "status": "awaiting_confirmation"
    })))
}

async fn put_expectation<T: Transport + 'static>(
    State(state): State<Arc<AppState<T>>>,
    Path((tenant, provider, reference)): Path<(String, String, String)>,
    headers: HeaderMap,
    Json(request): Json<ExpectationRequest>,
) -> Result<Json<Value>, ApiError> {
    authorize(&headers, &state.service_token, &tenant)?;
    let provider = parse_provider(&provider)?;
    validate_reference(&reference)?;
    let amount_minor = request.amount_minor.parse::<u128>().map_err(|_| {
        ApiError::invalid("amount_minor must be an unsigned 128-bit integer string")
    })?;
    if amount_minor == 0 {
        return Err(ApiError::invalid("collection amount must be non-zero"));
    }
    let expectation = CollectionExpectation {
        tenant_id: tenant.clone(),
        amount_minor,
        currency: request.currency.into(),
    };
    state
        .store
        .put_expectation(provider, &reference, &expectation)
        .await?;
    Ok(Json(json!({
        "provider":provider.as_str(),
        "reference":reference,
        "tenant_id":tenant,
        "amount_minor":amount_minor.to_string(),
        "currency":expectation.currency.as_str(),
        "status":"awaiting_confirmation"
    })))
}

async fn get_receipt<T: Transport + 'static>(
    State(state): State<Arc<AppState<T>>>,
    Path((tenant, provider, reference)): Path<(String, String, String)>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    authorize(&headers, &state.service_token, &tenant)?;
    let provider = parse_provider(&provider)?;
    validate_reference(&reference)?;
    let receipt = state.store.receipt(&tenant, provider, &reference).await?;
    match receipt {
        Some(receipt) => Ok(Json(receipt)),
        None => Err(ApiError::new(
            StatusCode::NOT_FOUND,
            "collection_not_found",
            "no settled collection receipt exists in this tenant scope",
        )),
    }
}

async fn list_open_suspense<T: Transport + 'static>(
    State(state): State<Arc<AppState<T>>>,
    headers: HeaderMap,
    Query(query): Query<ReviewQuery>,
) -> Result<Json<Value>, ApiError> {
    authorize(&headers, &state.service_token, "platform")?;
    if !(1..=500).contains(&query.limit) {
        return Err(ApiError::invalid("limit must be between 1 and 500"));
    }
    let receipts = state.store.list_open_suspense(query.limit).await?;
    Ok(Json(json!({"receipts":receipts})))
}

async fn resolve_suspense<T: Transport + 'static>(
    State(state): State<Arc<AppState<T>>>,
    Path((provider, reference)): Path<(String, String)>,
    headers: HeaderMap,
    Json(request): Json<SuspenseResolutionHttpRequest>,
) -> Result<Json<Value>, ApiError> {
    authorize(&headers, &state.service_token, "platform")?;
    let provider = parse_provider(&provider)?;
    validate_reference(&reference)?;
    validate_reference(&request.tenant_id)?;
    validate_reference(&request.approved_by)?;
    if request.tenant_id == "platform" {
        return Err(ApiError::invalid(
            "suspense must be resolved to a client tenant, not platform",
        ));
    }
    let reason = request.reason.trim();
    if !(3..=1_000).contains(&reason.len()) || reason.chars().any(char::is_control) {
        return Err(ApiError::invalid(
            "resolution reason must contain 3-1000 printable characters",
        ));
    }
    let original = state
        .store
        .reconciliation_receipt(provider, &reference)
        .await?
        .ok_or_else(|| {
            ApiError::new(
                StatusCode::NOT_FOUND,
                "collection_not_found",
                "no collection receipt exists for reconciliation",
            )
        })?;
    if original.get("disposition").and_then(Value::as_str) != Some("suspense")
        || original.get("provider").and_then(Value::as_str) != Some(provider.as_str())
        || original.get("reference").and_then(Value::as_str) != Some(reference.as_str())
    {
        return Err(ApiError::new(
            StatusCode::CONFLICT,
            "not_open_suspense",
            "only an unmatched or mismatched suspense receipt can be resolved",
        ));
    }
    let event_id = required_json_string(&original, "event_id")?;
    validate_reference(&event_id)?;
    let amount_minor = required_json_string(&original, "amount_minor")?
        .parse::<u128>()
        .map_err(|_| ApiError::invalid("stored suspense amount is invalid"))?;
    let currency = parse_currency(required_json_string(&original, "currency")?.as_str())?;
    let original_transfer = required_json_string(&original, "ledger_transfer_id")?
        .parse::<u128>()
        .map_err(|_| ApiError::invalid("stored suspense transfer is invalid"))?;
    if original_transfer != funding_id(&event_id) {
        return Err(ApiError::new(
            StatusCode::INTERNAL_SERVER_ERROR,
            "invalid_collection_record",
            "stored suspense receipt does not match its deterministic funding transfer",
        ));
    }
    let resolution = state
        .ledger
        .resolve_suspense(&SuspenseResolutionRequest {
            event_id: event_id.clone(),
            provider: provider.as_str().to_owned(),
            tenant_id: request.tenant_id.clone(),
            amount_minor,
            currency,
        })
        .await?;
    let resolution_id = format!("resolution:{event_id}");
    let transfer_id = resolution.transfer_id.to_string();
    let payload = json!({
        "schema":"volumeos.collection-resolution.v1",
        "resolution_id":resolution_id,
        "event_id":event_id,
        "provider":provider.as_str(),
        "reference":reference,
        "tenant_id":request.tenant_id,
        "amount_minor":amount_minor.to_string(),
        "currency":currency.as_str(),
        "ledger_transfer_id":transfer_id,
        "approved_by":request.approved_by,
        "reason":reason,
        "status":"resolved"
    });
    state
        .store
        .record_resolution(&ResolutionRecord {
            resolution_id: required_json_string(&payload, "resolution_id")?,
            event_id: required_json_string(&payload, "event_id")?,
            provider,
            reference,
            credited_tenant_id: required_json_string(&payload, "tenant_id")?,
            amount_minor,
            currency,
            ledger_transfer_id: transfer_id,
            approved_by: required_json_string(&payload, "approved_by")?,
            reason: reason.to_owned(),
            payload: payload.clone(),
        })
        .await?;
    Ok(Json(payload))
}

fn required_json_string(value: &Value, field: &str) -> Result<String, ApiError> {
    value
        .get(field)
        .and_then(Value::as_str)
        .filter(|item| !item.is_empty())
        .map(str::to_owned)
        .ok_or_else(|| {
            ApiError::new(
                StatusCode::INTERNAL_SERVER_ERROR,
                "invalid_collection_record",
                format!("stored collection receipt is missing {field}"),
            )
        })
}

async fn webhook<T: Transport + 'static>(
    State(state): State<Arc<AppState<T>>>,
    Path(provider): Path<String>,
    headers: HeaderMap,
    body: Bytes,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let provider = parse_provider(&provider)?;
    let signature_name = match provider {
        Provider::Paystack => "x-paystack-signature",
        Provider::Flutterwave => "flutterwave-signature",
        Provider::Moniepoint => "moniepoint-signature",
    };
    let signature = headers
        .get(signature_name)
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default();
    let Some(notification) =
        verify_and_parse(provider, &body, signature, state.providers.secret(provider))?
    else {
        return Ok((StatusCode::ACCEPTED, Json(json!({"status":"ignored"}))));
    };
    let expectation = state
        .store
        .expectation(provider, &notification.reference)
        .await?;
    let receipt = process_collection(
        &notification,
        expectation.as_ref(),
        state.providers.client(provider),
        &state.ledger,
    )
    .await?;
    let pending = matches!(receipt.disposition, CollectionDisposition::Pending);
    if receipt.funding.is_some() {
        let record = receipt_record(&receipt, expectation.as_ref());
        state.store.record_receipt(&record).await?;
    }
    let status = if pending {
        StatusCode::ACCEPTED
    } else {
        StatusCode::OK
    };
    Ok((status, Json(receipt_json(&receipt))))
}

fn receipt_record(
    receipt: &CollectionReceipt,
    expectation: Option<&CollectionExpectation>,
) -> ReceiptRecord {
    let scope_tenant_id = match &receipt.disposition {
        CollectionDisposition::Credited { tenant_id } => tenant_id.clone(),
        _ => expectation
            .map(|value| value.tenant_id.clone())
            .unwrap_or_else(|| "platform".to_owned()),
    };
    ReceiptRecord {
        event_id: receipt.event_id.clone(),
        provider: receipt.provider,
        reference: receipt.reference.clone(),
        scope_tenant_id,
        disposition: disposition_name(&receipt.disposition).to_owned(),
        provider_payment_id: receipt.provider_payment_id.clone(),
        amount_minor: receipt.amount_minor,
        currency: receipt.currency,
        ledger_transfer_id: receipt
            .funding
            .map(|funding| funding.transfer_id.to_string()),
        payload: receipt_json(receipt),
    }
}

fn receipt_json(receipt: &CollectionReceipt) -> Value {
    json!({
        "event_id":receipt.event_id,
        "provider":receipt.provider.as_str(),
        "reference":receipt.reference,
        "amount_minor":receipt.amount_minor.to_string(),
        "currency":receipt.currency.as_str(),
        "disposition":disposition_name(&receipt.disposition),
        "tenant_id":match &receipt.disposition {
            CollectionDisposition::Credited { tenant_id } => Some(tenant_id),
            _ => None,
        },
        "ledger_transfer_id":receipt.funding.map(|funding| funding.transfer_id.to_string()),
    })
}

fn disposition_name(disposition: &CollectionDisposition) -> &'static str {
    match disposition {
        CollectionDisposition::Credited { .. } => "credited",
        CollectionDisposition::Suspense { .. } => "suspense",
        CollectionDisposition::Pending => "pending",
        CollectionDisposition::Failed => "failed",
    }
}

fn parse_currency(value: &str) -> Result<Currency, ApiError> {
    match value {
        "NGN" => Ok(Currency::Ngn),
        "USD" => Ok(Currency::Usd),
        _ => Err(ApiError::new(
            StatusCode::INTERNAL_SERVER_ERROR,
            "invalid_collection_record",
            "stored collection currency is invalid",
        )),
    }
}

fn parse_provider(value: &str) -> Result<Provider, ApiError> {
    match value {
        "paystack" => Ok(Provider::Paystack),
        "flutterwave" => Ok(Provider::Flutterwave),
        "moniepoint" => Ok(Provider::Moniepoint),
        _ => Err(ApiError::invalid("unsupported collection provider")),
    }
}

fn validate_reference(value: &str) -> Result<(), ApiError> {
    let valid = !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .next()
            .is_some_and(|byte| byte.is_ascii_alphanumeric())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'-'));
    if valid {
        Ok(())
    } else {
        Err(ApiError::invalid(
            "reference must contain 1-128 safe identifier characters",
        ))
    }
}

fn authorize(headers: &HeaderMap, service_token: &str, tenant: &str) -> Result<(), ApiError> {
    let scoped = headers
        .get("x-volumeos-tenant")
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| value == tenant);
    if !scoped {
        return Err(ApiError::new(
            StatusCode::FORBIDDEN,
            "tenant_scope_mismatch",
            "tenant scope does not match the request path",
        ));
    }
    let bearer = headers
        .get(AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
        .unwrap_or_default();
    if bearer.is_empty()
        || !constant_time_eq::constant_time_eq(bearer.as_bytes(), service_token.as_bytes())
    {
        return Err(ApiError::new(
            StatusCode::UNAUTHORIZED,
            "unauthorized",
            "valid service authentication is required",
        ));
    }
    Ok(())
}

fn required_env(name: &str) -> Result<String, ApiError> {
    std::env::var(name).map_err(|_| {
        ApiError::new(
            StatusCode::INTERNAL_SERVER_ERROR,
            "provider_configuration",
            format!("{name} is not configured"),
        )
    })
}

fn env_or(name: &str, default: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| default.to_owned())
}
