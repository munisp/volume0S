use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use serde_json::{json, Value};
use tower::ServiceExt;
use volumeos_svc_billing::{router, AppState};
use volumeos_tb_adapter::ledger::{
    Account, AccountKey, AccountQuery, AccountSpec, CreateAccountStatus, CreateTransferStatus,
    Currency, FundingRequest, FundingTarget, Ledger, PostingState, Transfer, Transport,
    TransportError, TransportFuture, ACCOUNT_DEBITS_MUST_NOT_EXCEED_CREDITS, TRANSFER_PENDING,
    TRANSFER_POST_PENDING, TRANSFER_VOID_PENDING,
};

const TOKEN: &str = "test-only-service-token-with-32-bytes";

#[derive(Default)]
struct MockState {
    accounts: HashMap<u128, Account>,
    transfers: HashMap<u128, Transfer>,
    states: HashMap<u128, PostingState>,
}

/// API transport-seam double; these tests never contact a TigerBeetle cluster.
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

    fn query_accounts(
        &self,
        query: AccountQuery,
    ) -> TransportFuture<'_, Result<Vec<Account>, TransportError>> {
        Box::pin(async move {
            let state = self.state.lock().unwrap();
            let mut accounts: Vec<_> = state
                .accounts
                .values()
                .filter(|account| {
                    account.ledger == query.ledger
                        && (query.code == 0 || account.code == query.code)
                        && (query.user_data_128 == 0
                            || account.user_data_128 == query.user_data_128)
                        && account.timestamp >= query.timestamp_min
                })
                .copied()
                .collect();
            accounts.sort_by_key(|account| (account.timestamp, account.id));
            accounts.truncate(query.limit as usize);
            Ok(accounts)
        })
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
            if transfer.flags == 0 {
                if !state.accounts.contains_key(&transfer.debit_account_id)
                    || !state.accounts.contains_key(&transfer.credit_account_id)
                {
                    return Ok(CreateTransferStatus::UnknownAccount);
                }
                let debit = state.accounts.get_mut(&transfer.debit_account_id).unwrap();
                debit.debits_posted += transfer.amount;
                let credit = state.accounts.get_mut(&transfer.credit_account_id).unwrap();
                credit.credits_posted += transfer.amount;
                state.transfers.insert(transfer.id, transfer);
                return Ok(CreateTransferStatus::Created);
            }
            if transfer.flags == TRANSFER_PENDING {
                let Some(debit) = state.accounts.get(&transfer.debit_account_id) else {
                    return Ok(CreateTransferStatus::UnknownAccount);
                };
                if !state.accounts.contains_key(&transfer.credit_account_id) {
                    return Ok(CreateTransferStatus::UnknownAccount);
                }
                if debit.flags & ACCOUNT_DEBITS_MUST_NOT_EXCEED_CREDITS != 0
                    && debit.debits_posted + debit.debits_pending + transfer.amount
                        > debit.credits_posted
                {
                    return Ok(CreateTransferStatus::InsufficientFunds);
                }
                let debit = state.accounts.get_mut(&transfer.debit_account_id).unwrap();
                debit.debits_pending += transfer.amount;
                let credit = state.accounts.get_mut(&transfer.credit_account_id).unwrap();
                credit.credits_pending += transfer.amount;
                state.states.insert(transfer.id, PostingState::Pending);
                state.transfers.insert(transfer.id, transfer);
                return Ok(CreateTransferStatus::Created);
            }
            if transfer.flags == TRANSFER_POST_PENDING || transfer.flags == TRANSFER_VOID_PENDING {
                let Some(pending) = state.transfers.get(&transfer.pending_id).copied() else {
                    return Ok(CreateTransferStatus::UnknownReservation);
                };
                if state.states.get(&pending.id) != Some(&PostingState::Pending) {
                    return Ok(CreateTransferStatus::AlreadySettled);
                }
                let posted = transfer.flags == TRANSFER_POST_PENDING;
                let debit = state.accounts.get_mut(&pending.debit_account_id).unwrap();
                debit.debits_pending -= pending.amount;
                if posted {
                    debit.debits_posted += pending.amount;
                }
                let credit = state.accounts.get_mut(&pending.credit_account_id).unwrap();
                credit.credits_pending -= pending.amount;
                if posted {
                    credit.credits_posted += pending.amount;
                }
                state.states.insert(
                    pending.id,
                    if posted {
                        PostingState::Posted
                    } else {
                        PostingState::Voided
                    },
                );
                state.transfers.insert(transfer.id, transfer);
                return Ok(CreateTransferStatus::Created);
            }
            Ok(CreateTransferStatus::Rejected(
                "unsupported test transfer".to_owned(),
            ))
        })
    }

    fn lookup_transfer(
        &self,
        id: u128,
    ) -> TransportFuture<'_, Result<Option<Transfer>, TransportError>> {
        Box::pin(async move { Ok(self.state.lock().unwrap().transfers.get(&id).copied()) })
    }
}

fn app() -> (axum::Router, Arc<AppState<MockTransport>>) {
    let state =
        Arc::new(AppState::new(Ledger::new(MockTransport::default()), TOKEN.to_owned()).unwrap());
    (router(state.clone()), state)
}

fn call(method: &str, uri: &str, tenant: &str, token: &str, body: Option<Value>) -> Request<Body> {
    let builder = Request::builder()
        .method(method)
        .uri(uri)
        .header("x-volumeos-tenant", tenant)
        .header("authorization", format!("Bearer {token}"));
    if let Some(body) = body {
        builder
            .header("content-type", "application/json")
            .body(Body::from(body.to_string()))
            .unwrap()
    } else {
        builder.body(Body::empty()).unwrap()
    }
}

async fn response(app: &axum::Router, request: Request<Body>) -> (StatusCode, Value) {
    let response = app.clone().oneshot(request).await.unwrap();
    let status = response.status();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    let body = if bytes.is_empty() {
        Value::Null
    } else {
        serde_json::from_slice(&bytes).unwrap()
    };
    (status, body)
}

async fn funded_app() -> (axum::Router, Arc<AppState<MockTransport>>) {
    let (app, state) = app();
    for key in [
        AccountKey::tenant_prepaid("tenant-a"),
        AccountKey::revenue("02"),
        AccountKey::Usage {
            channel: "sms".to_owned(),
        },
        AccountKey::Usage {
            channel: "campaign".to_owned(),
        },
        AccountKey::ProviderSettlement {
            provider: "paystack".to_owned(),
        },
        AccountKey::ProviderCost {
            provider: "termii".to_owned(),
        },
        AccountKey::ProviderPayable {
            provider: "termii".to_owned(),
        },
    ] {
        state
            .ledger
            .provision_account(&AccountSpec::new(key, Currency::Ngn).unwrap())
            .await
            .unwrap();
    }
    state
        .ledger
        .fund(&FundingRequest {
            event_id: "test-funding".to_owned(),
            provider: "paystack".to_owned(),
            target: FundingTarget::TenantPrepaid {
                tenant_id: "tenant-a".to_owned(),
            },
            amount_minor: 200_000,
            currency: Currency::Ngn,
        })
        .await
        .unwrap();
    (app, state)
}

#[tokio::test]
async fn service_auth_and_tenant_scope_are_mandatory() {
    let (app, _) = app();
    let (status, _) = response(
        &app,
        call(
            "GET",
            "/v1/tenants/tenant-a/balance/NGN",
            "tenant-a",
            "wrong",
            None,
        ),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    let (status, _) = response(
        &app,
        call(
            "GET",
            "/v1/tenants/tenant-a/balance/NGN",
            "tenant-b",
            TOKEN,
            None,
        ),
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn onboarding_provisions_tenant_offer_and_provider_accounts() {
    let (app, _) = app();
    let (status, body) = response(
        &app,
        call(
            "POST",
            "/v1/tenants/tenant-a/accounts",
            "tenant-a",
            TOKEN,
            Some(json!({"currencies":["NGN"],"offers":["01","02"]})),
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["accounts_provisioned"], 22);
}

#[tokio::test]
async fn retainer_and_usage_charges_settle_without_double_spending() {
    let (app, _) = funded_app().await;
    let retainer = json!({
        "event_id":"retainer:tenant-a:2026-10",
        "amount_minor":"125000",
        "currency":"NGN",
        "kind":"retainer",
        "offer":"02",
        "timeout_seconds":900
    });
    let (status, body) = response(
        &app,
        call(
            "POST",
            "/v1/tenants/tenant-a/charges/reserve",
            "tenant-a",
            TOKEN,
            Some(retainer),
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["state"], "pending");
    let (status, body) = response(
        &app,
        call(
            "POST",
            "/v1/tenants/tenant-a/charges/retainer:tenant-a:2026-10/post",
            "tenant-a",
            TOKEN,
            None,
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["state"], "posted");

    let usage = json!({
        "event_id":"usage:tenant-a:sms:2026-W43",
        "amount_minor":"75000",
        "provider_cost_minor":"9000",
        "currency":"NGN",
        "channel":"sms",
        "provider":"termii",
        "offer":"02",
        "corridor":"campaign-1",
        "timeout_seconds":900
    });
    let (status, body) = response(
        &app,
        call(
            "POST",
            "/v1/tenants/tenant-a/usage",
            "tenant-a",
            TOKEN,
            Some(usage.clone()),
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["state"], "posted");
    assert_eq!(body["provider_cost_minor"], "9000");
    assert!(body["provider_cost_transfer_id"].is_string());
    let (replay_status, replay) = response(
        &app,
        call(
            "POST",
            "/v1/tenants/tenant-a/usage",
            "tenant-a",
            TOKEN,
            Some(usage),
        ),
    )
    .await;
    assert_eq!(replay_status, StatusCode::OK);
    assert_eq!(replay, body);
    let (_, balance) = response(
        &app,
        call(
            "GET",
            "/v1/tenants/tenant-a/balance/NGN",
            "tenant-a",
            TOKEN,
            None,
        ),
    )
    .await;
    assert_eq!(balance["available_minor"], "0");

    let overdraw = json!({
        "event_id":"usage:tenant-a:sms:2026-W44",
        "amount_minor":"1",
        "provider_cost_minor":"0",
        "currency":"NGN",
        "channel":"sms",
        "provider":"termii",
        "offer":"02",
        "corridor":"campaign-1",
        "timeout_seconds":900
    });
    let (status, body) = response(
        &app,
        call(
            "POST",
            "/v1/tenants/tenant-a/usage",
            "tenant-a",
            TOKEN,
            Some(overdraw),
        ),
    )
    .await;
    assert_eq!(status, StatusCode::PAYMENT_REQUIRED);
    assert_eq!(body["error"]["code"], "insufficient_funds");
}

#[tokio::test]
async fn fx_endpoint_posts_balanced_monthly_revaluation() {
    let (app, _) = app();
    let request = json!({
        "event_id":"fx-close:2026-10:claude",
        "provider":"claude",
        "amount_minor":"1250",
        "direction":"loss"
    });
    let (status, first) = response(
        &app,
        call(
            "POST",
            "/v1/tenants/platform/fx/revalue",
            "platform",
            TOKEN,
            Some(request.clone()),
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let (status, replay) = response(
        &app,
        call(
            "POST",
            "/v1/tenants/platform/fx/revalue",
            "platform",
            TOKEN,
            Some(request),
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(first, replay);
}

#[tokio::test]
async fn campaign_reservation_uses_campaign_transfer_semantics() {
    let (app, _) = funded_app().await;
    let request = json!({
        "event_id":"campaign:abc123",
        "amount_minor":"25000",
        "currency":"NGN",
        "kind":"campaign",
        "offer":"01",
        "channel":"campaign",
        "timeout_seconds":900
    });
    let (status, reserved) = response(
        &app,
        call(
            "POST",
            "/v1/tenants/tenant-a/charges/reserve",
            "tenant-a",
            TOKEN,
            Some(request),
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(reserved["state"], "pending");
    let (status, posted) = response(
        &app,
        call(
            "POST",
            "/v1/tenants/tenant-a/charges/campaign:abc123/post",
            "tenant-a",
            TOKEN,
            None,
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(posted["state"], "posted");
}

#[tokio::test]
async fn platform_trial_balance_is_balanced_and_reports_offer_revenue() {
    let (app, _) = funded_app().await;
    let charge = json!({
        "event_id":"retainer:tenant-a:2026-10",
        "amount_minor":"125000",
        "currency":"NGN",
        "kind":"retainer",
        "offer":"02",
        "timeout_seconds":900
    });
    let (status, _) = response(
        &app,
        call(
            "POST",
            "/v1/tenants/tenant-a/charges/reserve",
            "tenant-a",
            TOKEN,
            Some(charge),
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let (status, _) = response(
        &app,
        call(
            "POST",
            "/v1/tenants/tenant-a/charges/retainer:tenant-a:2026-10/post",
            "tenant-a",
            TOKEN,
            None,
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK);

    let (status, report) = response(
        &app,
        call(
            "GET",
            "/v1/tenants/platform/reports/trial-balance/NGN",
            "platform",
            TOKEN,
            None,
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(report["schema"], "volumeos.trial-balance.v1");
    assert_eq!(report["balanced"], true);
    assert_eq!(
        report["total_debits_posted"],
        report["total_credits_posted"]
    );
    assert_eq!(report["revenue_per_offer"][0]["offer"], "02");
    assert_eq!(report["revenue_per_offer"][0]["net_credit_minor"], "125000");

    let (status, _) = response(
        &app,
        call(
            "GET",
            "/v1/tenants/tenant-a/reports/trial-balance/NGN",
            "tenant-a",
            TOKEN,
            None,
        ),
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn tenant_scope_cannot_post_platform_fx_close() {
    let (app, _) = app();
    let (status, body) = response(
        &app,
        call(
            "POST",
            "/v1/tenants/tenant-a/fx/revalue",
            "tenant-a",
            TOKEN,
            Some(json!({
                "event_id":"fx-close:unauthorized",
                "provider":"openai",
                "amount_minor":"100",
                "direction":"loss"
            })),
        ),
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(body["error"]["code"], "platform_scope_required");
}
