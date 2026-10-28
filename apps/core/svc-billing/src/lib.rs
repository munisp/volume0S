//! Tenant-scoped billing HTTP boundary.
//!
//! Financial balances exist only in TigerBeetle. This service exposes workflow
//! primitives; invoice persistence and Temporal orchestration live separately.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::sync::Arc;

use axum::extract::{DefaultBodyLimit, Path, State};
use axum::http::header::AUTHORIZATION;
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::{Deserialize, Serialize};
use serde_json::json;
use volumeos_tb_adapter::ledger::{
    AccountKey, AccountSpec, Balance, ChargeRequest, Currency, FundingReceipt, FxDirection,
    FxRevaluationRequest, Ledger, MoneyError, PostingState, ProviderCostRequest, Reservation,
    TransferKind, Transport,
};

const PAYMENT_PROVIDERS: [&str; 3] = ["paystack", "flutterwave", "moniepoint"];
const USAGE_CHANNELS: [&str; 7] = [
    "campaign", "sms", "whatsapp", "email", "social", "ai", "gpu",
];

pub struct AppState<T: Transport> {
    pub ledger: Ledger<T>,
    service_token: String,
}

impl<T: Transport> AppState<T> {
    pub fn new(ledger: Ledger<T>, service_token: String) -> Result<Self, ApiError> {
        if service_token.len() < 32 {
            return Err(ApiError::new(
                StatusCode::INTERNAL_SERVER_ERROR,
                "service_configuration",
                "VOLUMEOS_SERVICE_TOKEN must contain at least 32 bytes",
            ));
        }
        Ok(Self {
            ledger,
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
        .route("/v1/tenants/:tenant/accounts", post(provision_accounts))
        .route("/v1/tenants/:tenant/balance/:currency", get(balance))
        .route(
            "/v1/tenants/:tenant/reports/trial-balance/:currency",
            get(trial_balance),
        )
        .route("/v1/tenants/:tenant/charges/reserve", post(reserve))
        .route("/v1/tenants/:tenant/charges/:event/post", post(post_charge))
        .route("/v1/tenants/:tenant/charges/:event/void", post(void_charge))
        .route("/v1/tenants/:tenant/usage", post(post_usage))
        .route("/v1/tenants/:tenant/fx/revalue", post(revalue))
        .layer(DefaultBodyLimit::max(64 * 1024))
        .with_state(state)
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Ord, PartialOrd)]
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
pub struct ProvisionRequest {
    pub currencies: Vec<CurrencyInput>,
    pub offers: Vec<String>,
    pub providers: Option<Vec<String>>,
}

#[derive(Debug, Serialize)]
pub struct ProvisionResponse {
    pub accounts_provisioned: usize,
}

#[derive(Debug, Serialize)]
pub struct LedgerAccountReport {
    pub account_id: String,
    pub account_class: &'static str,
    pub code: u16,
    pub debits_pending: String,
    pub debits_posted: String,
    pub credits_pending: String,
    pub credits_posted: String,
}

#[derive(Debug, Serialize)]
pub struct OfferRevenueReport {
    pub offer: String,
    pub debits_posted: String,
    pub credits_posted: String,
    pub net_credit_minor: String,
}

#[derive(Debug, Serialize)]
pub struct TrialBalanceResponse {
    pub schema: &'static str,
    pub currency: String,
    pub account_count: usize,
    pub accounts: Vec<LedgerAccountReport>,
    pub total_debits_posted: String,
    pub total_credits_posted: String,
    pub balanced: bool,
    pub revenue_per_offer: Vec<OfferRevenueReport>,
}

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ChargeKind {
    Campaign,
    Retainer,
    Usage,
}

#[derive(Debug, Deserialize)]
pub struct ChargeHttpRequest {
    pub event_id: String,
    pub amount_minor: String,
    pub currency: CurrencyInput,
    pub kind: ChargeKind,
    pub offer: Option<String>,
    pub channel: Option<String>,
    pub corridor: Option<String>,
    pub timeout_seconds: u32,
}

#[derive(Debug, Deserialize)]
pub struct UsageHttpRequest {
    pub event_id: String,
    pub amount_minor: String,
    pub provider_cost_minor: String,
    pub currency: CurrencyInput,
    pub channel: String,
    pub provider: String,
    pub offer: String,
    pub corridor: String,
    pub timeout_seconds: u32,
}

#[derive(Debug, Serialize)]
pub struct UsagePostingResponse {
    pub event_id: String,
    pub tenant_id: String,
    pub transfer_id: String,
    pub amount_minor: String,
    pub state: String,
    pub provider_cost_minor: String,
    pub provider_cost_transfer_id: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct ReservationResponse {
    pub event_id: String,
    pub tenant_id: String,
    pub transfer_id: String,
    pub amount_minor: String,
    pub state: String,
}

impl From<Reservation> for ReservationResponse {
    fn from(value: Reservation) -> Self {
        Self {
            event_id: value.event_id,
            tenant_id: value.tenant_id,
            transfer_id: value.transfer_id.to_string(),
            amount_minor: value.amount_minor.to_string(),
            state: match value.state {
                PostingState::Pending => "pending",
                PostingState::Posted => "posted",
                PostingState::Voided => "voided",
            }
            .to_owned(),
        }
    }
}

#[derive(Debug, Serialize)]
pub struct BalanceResponse {
    pub account_id: String,
    pub debits_pending: String,
    pub debits_posted: String,
    pub credits_pending: String,
    pub credits_posted: String,
    pub available_minor: Option<String>,
}

impl From<Balance> for BalanceResponse {
    fn from(value: Balance) -> Self {
        Self {
            account_id: value.account_id.to_string(),
            debits_pending: value.debits_pending.to_string(),
            debits_posted: value.debits_posted.to_string(),
            credits_pending: value.credits_pending.to_string(),
            credits_posted: value.credits_posted.to_string(),
            available_minor: value.available_minor.map(|amount| amount.to_string()),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FxDirectionInput {
    Gain,
    Loss,
}

#[derive(Debug, Deserialize)]
pub struct FxHttpRequest {
    pub event_id: String,
    pub provider: String,
    pub amount_minor: String,
    pub direction: FxDirectionInput,
}

#[derive(Debug, Serialize)]
pub struct PostingResponse {
    pub transfer_id: String,
    pub amount_minor: String,
}

impl From<FundingReceipt> for PostingResponse {
    fn from(value: FundingReceipt) -> Self {
        Self {
            transfer_id: value.transfer_id.to_string(),
            amount_minor: value.amount_minor.to_string(),
        }
    }
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

impl From<MoneyError> for ApiError {
    fn from(value: MoneyError) -> Self {
        let (status, code) = match value {
            MoneyError::InvalidInput(_) => (StatusCode::BAD_REQUEST, "invalid_request"),
            MoneyError::IdempotencyConflict => (StatusCode::CONFLICT, "idempotency_conflict"),
            MoneyError::UnknownAccount => (StatusCode::NOT_FOUND, "unknown_account"),
            MoneyError::UnknownReservation => (StatusCode::NOT_FOUND, "unknown_reservation"),
            MoneyError::AlreadySettled => (StatusCode::CONFLICT, "already_settled"),
            MoneyError::ReservationExpired => (StatusCode::CONFLICT, "reservation_expired"),
            MoneyError::InsufficientFunds => (StatusCode::PAYMENT_REQUIRED, "insufficient_funds"),
            MoneyError::Transport(_) => (StatusCode::SERVICE_UNAVAILABLE, "ledger_unavailable"),
            MoneyError::InvariantViolation(_) => {
                (StatusCode::INTERNAL_SERVER_ERROR, "ledger_invariant")
            }
        };
        Self::new(status, code, value.to_string())
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
        let body = Json(json!({
            "error": {"code": self.code, "message": self.message}
        }));
        (self.status, body).into_response()
    }
}

async fn ready<T: Transport + 'static>(
    State(state): State<Arc<AppState<T>>>,
) -> Result<Json<serde_json::Value>, ApiError> {
    state
        .ledger
        .transport()
        .lookup_account(0)
        .await
        .map_err(|_| {
            ApiError::new(
                StatusCode::SERVICE_UNAVAILABLE,
                "ledger_unavailable",
                "TigerBeetle is not ready",
            )
        })?;
    Ok(Json(json!({"status":"ready"})))
}

async fn provision_accounts<T: Transport + 'static>(
    State(state): State<Arc<AppState<T>>>,
    Path(tenant): Path<String>,
    headers: HeaderMap,
    Json(request): Json<ProvisionRequest>,
) -> Result<Json<ProvisionResponse>, ApiError> {
    authorize(&headers, &state, &tenant)?;
    let currencies: BTreeSet<_> = request.currencies.into_iter().collect();
    if currencies.is_empty() {
        return Err(ApiError::invalid("at least one currency is required"));
    }
    let offers: BTreeSet<_> = request.offers.into_iter().collect();
    if offers.is_empty() {
        return Err(ApiError::invalid("at least one offer is required"));
    }
    let providers: BTreeSet<_> = request
        .providers
        .unwrap_or_else(|| PAYMENT_PROVIDERS.map(str::to_owned).to_vec())
        .into_iter()
        .collect();
    let mut count = 0;
    for currency in currencies {
        let currency = Currency::from(currency);
        let mut keys = vec![AccountKey::tenant_prepaid(&tenant)];
        keys.extend(offers.iter().cloned().map(AccountKey::revenue));
        keys.extend(USAGE_CHANNELS.iter().map(|channel| AccountKey::Usage {
            channel: (*channel).to_owned(),
        }));
        keys.extend(providers.iter().flat_map(|provider| {
            [
                AccountKey::ProviderSettlement {
                    provider: provider.clone(),
                },
                AccountKey::CollectionSuspense {
                    provider: provider.clone(),
                },
                AccountKey::ProviderCost {
                    provider: provider.clone(),
                },
                AccountKey::ProviderPayable {
                    provider: provider.clone(),
                },
            ]
        }));
        for key in keys {
            state
                .ledger
                .provision_account(&AccountSpec::new(key, currency)?)
                .await?;
            count += 1;
        }
    }
    Ok(Json(ProvisionResponse {
        accounts_provisioned: count,
    }))
}

async fn balance<T: Transport + 'static>(
    State(state): State<Arc<AppState<T>>>,
    Path((tenant, currency)): Path<(String, String)>,
    headers: HeaderMap,
) -> Result<Json<BalanceResponse>, ApiError> {
    authorize(&headers, &state, &tenant)?;
    let currency = parse_currency(&currency)?;
    let result = state
        .ledger
        .balance(&AccountSpec::new(
            AccountKey::tenant_prepaid(tenant),
            currency,
        )?)
        .await?;
    Ok(Json(result.into()))
}

async fn trial_balance<T: Transport + 'static>(
    State(state): State<Arc<AppState<T>>>,
    Path((tenant, currency)): Path<(String, String)>,
    headers: HeaderMap,
) -> Result<Json<TrialBalanceResponse>, ApiError> {
    authorize(&headers, &state, &tenant)?;
    if tenant != "platform" {
        return Err(ApiError::new(
            StatusCode::FORBIDDEN,
            "platform_scope_required",
            "trial balance is restricted to the platform auditor scope",
        ));
    }
    let currency = parse_currency(&currency)?;
    let snapshot = state.ledger.account_snapshot(currency).await?;
    let mut total_debits = 0_u128;
    let mut total_credits = 0_u128;
    let mut revenue: BTreeMap<u16, (u128, u128)> = BTreeMap::new();
    let mut accounts = Vec::with_capacity(snapshot.len());
    for account in snapshot {
        total_debits = total_debits
            .checked_add(account.debits_posted)
            .ok_or_else(|| {
                MoneyError::InvariantViolation("trial-balance debit total overflow".to_owned())
            })?;
        total_credits = total_credits
            .checked_add(account.credits_posted)
            .ok_or_else(|| {
                MoneyError::InvariantViolation("trial-balance credit total overflow".to_owned())
            })?;
        if (201..=208).contains(&account.code) {
            let totals = revenue.entry(account.code).or_default();
            totals.0 = totals.0.checked_add(account.debits_posted).ok_or_else(|| {
                MoneyError::InvariantViolation("offer debit total overflow".to_owned())
            })?;
            totals.1 = totals
                .1
                .checked_add(account.credits_posted)
                .ok_or_else(|| {
                    MoneyError::InvariantViolation("offer credit total overflow".to_owned())
                })?;
        }
        accounts.push(LedgerAccountReport {
            account_id: account.id.to_string(),
            account_class: account_class(account.code),
            code: account.code,
            debits_pending: account.debits_pending.to_string(),
            debits_posted: account.debits_posted.to_string(),
            credits_pending: account.credits_pending.to_string(),
            credits_posted: account.credits_posted.to_string(),
        });
    }
    if total_debits != total_credits {
        return Err(MoneyError::InvariantViolation(
            "TigerBeetle trial balance is not balanced".to_owned(),
        )
        .into());
    }
    let revenue_per_offer = revenue
        .into_iter()
        .map(|(code, (debits, credits))| OfferRevenueReport {
            offer: format!("{:02}", code - 200),
            debits_posted: debits.to_string(),
            credits_posted: credits.to_string(),
            net_credit_minor: net_credit(debits, credits),
        })
        .collect();
    Ok(Json(TrialBalanceResponse {
        schema: "volumeos.trial-balance.v1",
        currency: currency.as_str().to_owned(),
        account_count: accounts.len(),
        accounts,
        total_debits_posted: total_debits.to_string(),
        total_credits_posted: total_credits.to_string(),
        balanced: true,
        revenue_per_offer,
    }))
}

fn account_class(code: u16) -> &'static str {
    match code {
        100 => "liability:prepaid",
        201..=208 => "revenue:offer",
        300 => "usage:channel",
        400 => "cost:provider",
        410 => "liability:provider_payable",
        500 => "partner:share",
        601 => "fx:gain",
        602 => "fx:loss",
        700 => "provider:settlement",
        800 => "provider:suspense",
        _ => "unclassified",
    }
}

fn net_credit(debits: u128, credits: u128) -> String {
    if credits >= debits {
        (credits - debits).to_string()
    } else {
        format!("-{}", debits - credits)
    }
}

async fn reserve<T: Transport + 'static>(
    State(state): State<Arc<AppState<T>>>,
    Path(tenant): Path<String>,
    headers: HeaderMap,
    Json(request): Json<ChargeHttpRequest>,
) -> Result<Json<ReservationResponse>, ApiError> {
    authorize(&headers, &state, &tenant)?;
    let charge = build_charge(&tenant, request)?;
    let reservation = state.ledger.reserve(&charge).await?;
    Ok(Json(reservation.into()))
}

async fn post_charge<T: Transport + 'static>(
    State(state): State<Arc<AppState<T>>>,
    Path((tenant, event)): Path<(String, String)>,
    headers: HeaderMap,
) -> Result<Json<ReservationResponse>, ApiError> {
    authorize(&headers, &state, &tenant)?;
    Ok(Json(state.ledger.post(&event, &tenant).await?.into()))
}

async fn void_charge<T: Transport + 'static>(
    State(state): State<Arc<AppState<T>>>,
    Path((tenant, event)): Path<(String, String)>,
    headers: HeaderMap,
) -> Result<Json<ReservationResponse>, ApiError> {
    authorize(&headers, &state, &tenant)?;
    Ok(Json(state.ledger.void(&event, &tenant).await?.into()))
}

async fn post_usage<T: Transport + 'static>(
    State(state): State<Arc<AppState<T>>>,
    Path(tenant): Path<String>,
    headers: HeaderMap,
    Json(request): Json<UsageHttpRequest>,
) -> Result<Json<UsagePostingResponse>, ApiError> {
    authorize(&headers, &state, &tenant)?;
    let currency = Currency::from(request.currency);
    let charge = ChargeRequest::new(
        request.event_id.clone(),
        &tenant,
        AccountKey::tenant_prepaid(&tenant),
        AccountKey::Usage {
            channel: request.channel.clone(),
        },
        parse_amount(&request.amount_minor)?,
        currency,
        TransferKind::Usage,
        request.timeout_seconds,
    )
    .with_context(Some(request.offer.clone()), Some(request.corridor.clone()));
    let event_id = charge.event_id.clone();
    state.ledger.reserve(&charge).await?;
    let posted = state.ledger.post(&event_id, &tenant).await?;
    let provider_cost_minor = parse_amount(&request.provider_cost_minor)?;
    let provider_cost_transfer_id = if provider_cost_minor == 0 {
        None
    } else {
        let receipt = state
            .ledger
            .post_provider_cost(&ProviderCostRequest {
                event_id: request.event_id,
                tenant_id: tenant.clone(),
                provider: request.provider,
                amount_minor: provider_cost_minor,
                currency,
                offer: request.offer,
                corridor: request.corridor,
            })
            .await?;
        Some(receipt.transfer_id.to_string())
    };
    Ok(Json(UsagePostingResponse {
        event_id: posted.event_id,
        tenant_id: posted.tenant_id,
        transfer_id: posted.transfer_id.to_string(),
        amount_minor: posted.amount_minor.to_string(),
        state: "posted".to_owned(),
        provider_cost_minor: provider_cost_minor.to_string(),
        provider_cost_transfer_id,
    }))
}

async fn revalue<T: Transport + 'static>(
    State(state): State<Arc<AppState<T>>>,
    Path(tenant): Path<String>,
    headers: HeaderMap,
    Json(request): Json<FxHttpRequest>,
) -> Result<Json<PostingResponse>, ApiError> {
    authorize(&headers, &state, &tenant)?;
    if tenant != "platform" {
        return Err(ApiError::new(
            StatusCode::FORBIDDEN,
            "platform_scope_required",
            "FX close is restricted to the platform scope",
        ));
    }
    let provider = request.provider.clone();
    for key in [
        AccountKey::ProviderCost {
            provider: provider.clone(),
        },
        match request.direction {
            FxDirectionInput::Gain => AccountKey::FxGain,
            FxDirectionInput::Loss => AccountKey::FxLoss,
        },
    ] {
        state
            .ledger
            .provision_account(&AccountSpec::new(key, Currency::Ngn)?)
            .await?;
    }
    let receipt = state
        .ledger
        .revalue(&FxRevaluationRequest {
            event_id: request.event_id,
            provider,
            amount_minor: parse_amount(&request.amount_minor)?,
            direction: match request.direction {
                FxDirectionInput::Gain => FxDirection::Gain,
                FxDirectionInput::Loss => FxDirection::Loss,
            },
        })
        .await?;
    Ok(Json(receipt.into()))
}

fn build_charge(tenant: &str, request: ChargeHttpRequest) -> Result<ChargeRequest, ApiError> {
    let (credit, kind) = match request.kind {
        ChargeKind::Campaign => {
            let channel = request
                .channel
                .clone()
                .ok_or_else(|| ApiError::invalid("campaign charges require a channel"))?;
            (
                AccountKey::Usage {
                    channel: channel.clone(),
                },
                TransferKind::Campaign,
            )
        }
        ChargeKind::Retainer => {
            let offer = request
                .offer
                .clone()
                .ok_or_else(|| ApiError::invalid("retainer charges require an offer"))?;
            (AccountKey::revenue(offer), TransferKind::Retainer)
        }
        ChargeKind::Usage => {
            let channel = request
                .channel
                .ok_or_else(|| ApiError::invalid("usage charges require a channel"))?;
            (
                AccountKey::Usage {
                    channel: channel.clone(),
                },
                TransferKind::Usage,
            )
        }
    };
    Ok(ChargeRequest::new(
        request.event_id,
        tenant,
        AccountKey::tenant_prepaid(tenant),
        credit,
        parse_amount(&request.amount_minor)?,
        request.currency.into(),
        kind,
        request.timeout_seconds,
    )
    .with_context(request.offer, request.corridor))
}

fn parse_amount(value: &str) -> Result<u128, ApiError> {
    value
        .parse::<u128>()
        .map_err(|_| ApiError::invalid("amount_minor must be an unsigned 128-bit integer string"))
}

fn parse_currency(value: &str) -> Result<Currency, ApiError> {
    match value {
        "NGN" => Ok(Currency::Ngn),
        "USD" => Ok(Currency::Usd),
        _ => Err(ApiError::invalid("currency must be NGN or USD")),
    }
}

fn authorize<T: Transport>(
    headers: &HeaderMap,
    state: &AppState<T>,
    tenant: &str,
) -> Result<(), ApiError> {
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
        || !constant_time_eq::constant_time_eq(bearer.as_bytes(), state.service_token.as_bytes())
    {
        return Err(ApiError::new(
            StatusCode::UNAUTHORIZED,
            "unauthorized",
            "valid service authentication is required",
        ));
    }
    Ok(())
}
