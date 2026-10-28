use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::Mutex;

use futures::executor::block_on;
use volumeos_tb_adapter::ledger::{
    Account, AccountKey, AccountQuery, AccountSpec, ChargeRequest, CreateAccountStatus,
    CreateTransferStatus, Currency, FundingRequest, FundingTarget, FxDirection,
    FxRevaluationRequest, Ledger, MoneyError, PostingState, ProviderCostRequest,
    SuspenseResolutionRequest, Transfer, TransferKind, Transport, TransportError, TransportFuture,
    ACCOUNT_DEBITS_MUST_NOT_EXCEED_CREDITS, TRANSFER_PENDING, TRANSFER_POST_PENDING,
    TRANSFER_VOID_PENDING,
};

#[derive(Default)]
struct MockState {
    accounts: HashMap<u128, Account>,
    transfers: HashMap<u128, Transfer>,
    states: HashMap<u128, PostingState>,
    expired: HashSet<u128>,
}

/// Test-only transport seam. Production uses `TigerBeetleTransport`; this mock
/// verifies adapter semantics without claiming that a real cluster was contacted.
#[derive(Default)]
struct MockTransport {
    state: Mutex<MockState>,
}

impl MockTransport {
    fn fund(&self, settlement: &AccountSpec, prepaid: &AccountSpec, amount: u128) {
        let mut state = self.state.lock().unwrap();
        let debit_id = settlement.id();
        let credit_id = prepaid.id();
        let debit = state.accounts.get_mut(&debit_id).unwrap();
        debit.debits_posted = debit.debits_posted.checked_add(amount).unwrap();
        let credit = state.accounts.get_mut(&credit_id).unwrap();
        credit.credits_posted = credit.credits_posted.checked_add(amount).unwrap();
        let transfer = Transfer {
            id: volumeos_tb_adapter::ledger::reserve_id("test-initial-funding"),
            debit_account_id: debit_id,
            credit_account_id: credit_id,
            amount,
            ledger: prepaid.currency.ledger(),
            code: TransferKind::Funding as u16,
            ..Transfer::default()
        };
        state.transfers.insert(transfer.id, transfer);
    }

    fn expire(&self, reserve_id: u128) {
        self.state.lock().unwrap().expired.insert(reserve_id);
    }
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
            if transfer.flags == 0 {
                if !state.accounts.contains_key(&transfer.debit_account_id)
                    || !state.accounts.contains_key(&transfer.credit_account_id)
                {
                    return Ok(CreateTransferStatus::UnknownAccount);
                }
                let debit = state.accounts.get(&transfer.debit_account_id).unwrap();
                if debit.flags & ACCOUNT_DEBITS_MUST_NOT_EXCEED_CREDITS != 0
                    && debit
                        .debits_posted
                        .checked_add(transfer.amount)
                        .is_none_or(|value| value > debit.credits_posted)
                {
                    return Ok(CreateTransferStatus::InsufficientFunds);
                }
                let debit = state.accounts.get_mut(&transfer.debit_account_id).unwrap();
                debit.debits_posted = debit.debits_posted.checked_add(transfer.amount).unwrap();
                let credit = state.accounts.get_mut(&transfer.credit_account_id).unwrap();
                credit.credits_posted = credit.credits_posted.checked_add(transfer.amount).unwrap();
                state.transfers.insert(transfer.id, transfer);
                return Ok(CreateTransferStatus::Created);
            }
            if transfer.flags == TRANSFER_PENDING {
                if !state.accounts.contains_key(&transfer.debit_account_id)
                    || !state.accounts.contains_key(&transfer.credit_account_id)
                {
                    return Ok(CreateTransferStatus::UnknownAccount);
                }
                let debit = state.accounts.get(&transfer.debit_account_id).unwrap();
                if debit.flags & ACCOUNT_DEBITS_MUST_NOT_EXCEED_CREDITS != 0 {
                    let committed = debit
                        .debits_posted
                        .checked_add(debit.debits_pending)
                        .and_then(|value| value.checked_add(transfer.amount));
                    if committed.is_none_or(|value| value > debit.credits_posted) {
                        return Ok(CreateTransferStatus::InsufficientFunds);
                    }
                }
                let debit = state.accounts.get_mut(&transfer.debit_account_id).unwrap();
                debit.debits_pending = debit.debits_pending.checked_add(transfer.amount).unwrap();
                let credit = state.accounts.get_mut(&transfer.credit_account_id).unwrap();
                credit.credits_pending =
                    credit.credits_pending.checked_add(transfer.amount).unwrap();
                state.states.insert(transfer.id, PostingState::Pending);
                state.transfers.insert(transfer.id, transfer);
                return Ok(CreateTransferStatus::Created);
            }
            if transfer.flags == TRANSFER_POST_PENDING || transfer.flags == TRANSFER_VOID_PENDING {
                let Some(pending) = state.transfers.get(&transfer.pending_id).copied() else {
                    return Ok(CreateTransferStatus::UnknownReservation);
                };
                if state.expired.contains(&pending.id) {
                    return Ok(CreateTransferStatus::Expired);
                }
                if state.states.get(&pending.id) != Some(&PostingState::Pending) {
                    return Ok(CreateTransferStatus::AlreadySettled);
                }
                let target = if transfer.flags == TRANSFER_POST_PENDING {
                    PostingState::Posted
                } else {
                    PostingState::Voided
                };
                let debit = state.accounts.get_mut(&pending.debit_account_id).unwrap();
                debit.debits_pending = debit.debits_pending.checked_sub(pending.amount).unwrap();
                if target == PostingState::Posted {
                    debit.debits_posted = debit.debits_posted.checked_add(pending.amount).unwrap();
                }
                let credit = state.accounts.get_mut(&pending.credit_account_id).unwrap();
                credit.credits_pending =
                    credit.credits_pending.checked_sub(pending.amount).unwrap();
                if target == PostingState::Posted {
                    credit.credits_posted =
                        credit.credits_posted.checked_add(pending.amount).unwrap();
                }
                state.states.insert(pending.id, target);
                state.transfers.insert(transfer.id, transfer);
                return Ok(CreateTransferStatus::Created);
            }
            Ok(CreateTransferStatus::Rejected(
                "test mock only accepts two-phase transfers".to_owned(),
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

struct World {
    prepaid: AccountSpec,
    revenue: AccountSpec,
    settlement: AccountSpec,
}

fn world(tenant: &str) -> World {
    World {
        prepaid: AccountSpec::new(AccountKey::tenant_prepaid(tenant), Currency::Ngn).unwrap(),
        revenue: AccountSpec::new(AccountKey::revenue("02"), Currency::Ngn).unwrap(),
        settlement: AccountSpec::new(
            AccountKey::ProviderSettlement {
                provider: "paystack".to_owned(),
            },
            Currency::Ngn,
        )
        .unwrap(),
    }
}

fn charge(world: &World, event: &str, tenant: &str, amount: u128) -> ChargeRequest {
    ChargeRequest::new(
        event,
        tenant,
        world.prepaid.key.clone(),
        world.revenue.key.clone(),
        amount,
        Currency::Ngn,
        TransferKind::Retainer,
        900,
    )
    .with_context(Some("02".to_owned()), Some("lagos-yaba".to_owned()))
}

async fn funded_ledger(amount: u128) -> (Ledger<MockTransport>, World) {
    let ledger = Ledger::new(MockTransport::default());
    let world = world("tenant-a");
    for spec in [&world.prepaid, &world.revenue, &world.settlement] {
        ledger.provision_account(spec).await.unwrap();
    }
    ledger
        .transport()
        .fund(&world.settlement, &world.prepaid, amount);
    (ledger, world)
}

#[test]
fn taxonomy_is_deterministic_and_currency_scoped() {
    let ngn = world("tenant-a");
    let usd = AccountSpec::new(AccountKey::tenant_prepaid("tenant-a"), Currency::Usd).unwrap();
    assert_eq!(ngn.prepaid.id(), ngn.prepaid.creation().id);
    assert_ne!(ngn.prepaid.id(), usd.id());
    assert_eq!(ngn.prepaid.creation().ledger, 566);
    assert_eq!(usd.creation().ledger, 840);
    assert_eq!(ngn.revenue.creation().code, 202);
    assert!(ngn
        .prepaid
        .key
        .canonical()
        .contains("liability:tenant#tenant-a:prepaid"));
}

#[test]
fn account_provisioning_is_idempotent() {
    block_on(async {
        let ledger = Ledger::new(MockTransport::default());
        let spec = world("tenant-a").prepaid;
        let first = ledger.provision_account(&spec).await.unwrap();
        let second = ledger.provision_account(&spec).await.unwrap();
        assert_eq!(first, second);
        assert_eq!(ledger.transport().state.lock().unwrap().accounts.len(), 1);
    });
}

#[test]
fn reserve_and_post_are_idempotent_and_balances_come_from_transport() {
    block_on(async {
        let (ledger, world) = funded_ledger(10_000).await;
        let request = charge(&world, "evt-post", "tenant-a", 2_500);
        let reserved = ledger.reserve(&request).await.unwrap();
        assert_eq!(reserved.state, PostingState::Pending);
        assert_eq!(ledger.reserve(&request).await.unwrap(), reserved);
        let pending = ledger.balance(&world.prepaid).await.unwrap();
        assert_eq!(pending.available_minor, Some(7_500));
        assert_eq!(pending.debits_pending, 2_500);
        assert_eq!(pending.debits_posted, 0);

        let posted = ledger.post("evt-post", "tenant-a").await.unwrap();
        assert_eq!(posted.state, PostingState::Posted);
        assert_eq!(ledger.post("evt-post", "tenant-a").await.unwrap(), posted);
        assert_eq!(
            ledger.reserve(&request).await.unwrap().state,
            PostingState::Posted
        );
        let balance = ledger.balance(&world.prepaid).await.unwrap();
        assert_eq!(balance.available_minor, Some(7_500));
        assert_eq!(balance.debits_pending, 0);
        assert_eq!(balance.debits_posted, 2_500);
        assert_eq!(balance.credits_posted, 10_000);
    });
}

#[test]
fn void_releases_reserve_without_posted_money_movement() {
    block_on(async {
        let (ledger, world) = funded_ledger(10_000).await;
        ledger
            .reserve(&charge(&world, "evt-void", "tenant-a", 4_000))
            .await
            .unwrap();
        let voided = ledger.void("evt-void", "tenant-a").await.unwrap();
        assert_eq!(voided.state, PostingState::Voided);
        assert_eq!(ledger.void("evt-void", "tenant-a").await.unwrap(), voided);
        let balance = ledger.balance(&world.prepaid).await.unwrap();
        assert_eq!(balance.debits_pending, 0);
        assert_eq!(balance.debits_posted, 0);
        assert_eq!(balance.available_minor, Some(10_000));
        assert_eq!(
            ledger.post("evt-void", "tenant-a").await,
            Err(MoneyError::AlreadySettled)
        );
    });
}

#[test]
fn replay_cannot_change_any_money_or_tenant_field() {
    block_on(async {
        let (ledger, world) = funded_ledger(10_000).await;
        let original = charge(&world, "evt-replay", "tenant-a", 1_000);
        ledger.reserve(&original).await.unwrap();
        let mut changed = original.clone();
        changed.amount_minor = 1_001;
        assert_eq!(
            ledger.reserve(&changed).await,
            Err(MoneyError::IdempotencyConflict)
        );
        let mut changed = original.clone();
        changed.tenant_id = "tenant-b".to_owned();
        assert_eq!(
            ledger.reserve(&changed).await,
            Err(MoneyError::IdempotencyConflict)
        );
        let mut changed = original.clone();
        changed.timeout_seconds = 901;
        assert_eq!(
            ledger.reserve(&changed).await,
            Err(MoneyError::IdempotencyConflict)
        );
        let mut changed = original.clone();
        changed.offer = Some("01".to_owned());
        assert_eq!(
            ledger.reserve(&changed).await,
            Err(MoneyError::IdempotencyConflict)
        );
        let mut changed = original;
        changed.corridor = Some("abuja-wuse".to_owned());
        assert_eq!(
            ledger.reserve(&changed).await,
            Err(MoneyError::IdempotencyConflict)
        );
        assert_eq!(
            ledger.post("evt-replay", "tenant-b").await,
            Err(MoneyError::IdempotencyConflict)
        );
    });
}

#[test]
fn prepaid_reserves_cannot_overdraw_or_stack_beyond_funds() {
    block_on(async {
        let (ledger, world) = funded_ledger(100).await;
        assert_eq!(
            ledger
                .reserve(&charge(&world, "evt-too-much", "tenant-a", 101))
                .await,
            Err(MoneyError::InsufficientFunds)
        );
        ledger
            .reserve(&charge(&world, "evt-80", "tenant-a", 80))
            .await
            .unwrap();
        assert_eq!(
            ledger
                .reserve(&charge(&world, "evt-30", "tenant-a", 30))
                .await,
            Err(MoneyError::InsufficientFunds)
        );
        assert_eq!(
            ledger
                .balance(&world.prepaid)
                .await
                .unwrap()
                .available_minor,
            Some(20)
        );
    });
}

#[test]
fn expired_reserve_fails_closed_for_post_and_void() {
    block_on(async {
        let (ledger, world) = funded_ledger(1_000).await;
        let request = charge(&world, "evt-expired", "tenant-a", 100);
        let reserved = ledger.reserve(&request).await.unwrap();
        ledger.transport().expire(reserved.transfer_id);
        assert_eq!(
            ledger.post("evt-expired", "tenant-a").await,
            Err(MoneyError::ReservationExpired)
        );
        assert_eq!(
            ledger.void("evt-expired", "tenant-a").await,
            Err(MoneyError::ReservationExpired)
        );
    });
}

#[test]
fn zero_amount_zero_timeout_and_unknown_accounts_are_rejected() {
    block_on(async {
        let (ledger, world) = funded_ledger(1_000).await;
        let mut invalid = charge(&world, "evt-zero", "tenant-a", 0);
        assert!(matches!(
            ledger.reserve(&invalid).await,
            Err(MoneyError::InvalidInput(_))
        ));
        invalid.amount_minor = 1;
        invalid.timeout_seconds = 0;
        assert!(matches!(
            ledger.reserve(&invalid).await,
            Err(MoneyError::InvalidInput(_))
        ));
        assert_eq!(
            ledger.post("evt-missing", "tenant-a").await,
            Err(MoneyError::UnknownReservation)
        );

        let empty = Ledger::new(MockTransport::default());
        assert_eq!(
            empty
                .reserve(&charge(&world, "evt-no-accounts", "tenant-a", 1))
                .await,
            Err(MoneyError::UnknownAccount)
        );
    });
}

#[test]
fn full_u128_minor_unit_amounts_do_not_narrow_to_u64() {
    block_on(async {
        let funding = u128::from(u64::MAX) + 50_000;
        let (ledger, world) = funded_ledger(funding).await;
        let amount = funding - 10_000;
        ledger
            .reserve(&charge(&world, "evt-u128", "tenant-a", amount))
            .await
            .unwrap();
        ledger.post("evt-u128", "tenant-a").await.unwrap();
        let balance = ledger.balance(&world.prepaid).await.unwrap();
        assert_eq!(balance.debits_posted, amount);
        assert_eq!(balance.available_minor, Some(10_000));
    });
}

#[test]
fn tenant_and_currency_accounts_remain_separate() {
    block_on(async {
        let ledger = Ledger::new(MockTransport::default());
        let tenant_a =
            AccountSpec::new(AccountKey::tenant_prepaid("tenant-a"), Currency::Ngn).unwrap();
        let tenant_b =
            AccountSpec::new(AccountKey::tenant_prepaid("tenant-b"), Currency::Ngn).unwrap();
        let usd = AccountSpec::new(AccountKey::tenant_prepaid("tenant-a"), Currency::Usd).unwrap();
        for spec in [&tenant_a, &tenant_b, &usd] {
            ledger.provision_account(spec).await.unwrap();
        }
        assert_ne!(tenant_a.id(), tenant_b.id());
        assert_ne!(tenant_a.id(), usd.id());
        assert_eq!(ledger.transport().state.lock().unwrap().accounts.len(), 3);
    });
}

#[test]
fn confirmed_funding_replay_credits_tenant_once() {
    block_on(async {
        let (ledger, world) = funded_ledger(1_000).await;
        let request = FundingRequest {
            event_id: "collection:paystack:ref-1".to_owned(),
            provider: "paystack".to_owned(),
            target: FundingTarget::TenantPrepaid {
                tenant_id: "tenant-a".to_owned(),
            },
            amount_minor: 5_000,
            currency: Currency::Ngn,
        };
        let first = ledger.fund(&request).await.unwrap();
        assert_eq!(ledger.fund(&request).await.unwrap(), first);
        assert_eq!(
            ledger.balance(&world.prepaid).await.unwrap().credits_posted,
            6_000
        );
        let mut changed = request;
        changed.amount_minor = 5_001;
        assert_eq!(
            ledger.fund(&changed).await,
            Err(MoneyError::IdempotencyConflict)
        );
        assert_eq!(
            ledger.balance(&world.prepaid).await.unwrap().credits_posted,
            6_000
        );
    });
}

#[test]
fn unmatched_funding_can_only_reach_suspense() {
    block_on(async {
        let ledger = Ledger::new(MockTransport::default());
        let settlement = AccountSpec::new(
            AccountKey::ProviderSettlement {
                provider: "moniepoint".to_owned(),
            },
            Currency::Ngn,
        )
        .unwrap();
        let suspense = AccountSpec::new(
            AccountKey::CollectionSuspense {
                provider: "moniepoint".to_owned(),
            },
            Currency::Ngn,
        )
        .unwrap();
        ledger.provision_account(&settlement).await.unwrap();
        ledger.provision_account(&suspense).await.unwrap();
        let receipt = ledger
            .fund(&FundingRequest {
                event_id: "collection:moniepoint:unknown-1".to_owned(),
                provider: "moniepoint".to_owned(),
                target: FundingTarget::Suspense,
                amount_minor: 750,
                currency: Currency::Ngn,
            })
            .await
            .unwrap();
        assert!(receipt.transfer_id != 0);
        assert_eq!(ledger.balance(&suspense).await.unwrap().credits_posted, 750);
        assert_eq!(
            ledger.balance(&suspense).await.unwrap().available_minor,
            None
        );
    });
}

#[test]
fn fx_revaluation_is_balanced_deterministic_and_replay_safe() {
    block_on(async {
        let ledger = Ledger::new(MockTransport::default());
        let loss = AccountSpec::new(AccountKey::FxLoss, Currency::Ngn).unwrap();
        let gain = AccountSpec::new(AccountKey::FxGain, Currency::Ngn).unwrap();
        let cost = AccountSpec::new(
            AccountKey::ProviderCost {
                provider: "claude".to_owned(),
            },
            Currency::Ngn,
        )
        .unwrap();
        for spec in [&loss, &gain, &cost] {
            ledger.provision_account(spec).await.unwrap();
        }
        let loss_request = FxRevaluationRequest {
            event_id: "fx-close:2026-10:claude:loss".to_owned(),
            provider: "claude".to_owned(),
            amount_minor: 1250,
            direction: FxDirection::Loss,
        };
        let first = ledger.revalue(&loss_request).await.unwrap();
        let replay = ledger.revalue(&loss_request).await.unwrap();
        assert_eq!(first, replay);
        assert_eq!(ledger.balance(&loss).await.unwrap().debits_posted, 1250);
        assert_eq!(ledger.balance(&cost).await.unwrap().credits_posted, 1250);

        ledger
            .revalue(&FxRevaluationRequest {
                event_id: "fx-close:2026-10:claude:gain".to_owned(),
                provider: "claude".to_owned(),
                amount_minor: 500,
                direction: FxDirection::Gain,
            })
            .await
            .unwrap();
        assert_eq!(ledger.balance(&gain).await.unwrap().credits_posted, 500);
        assert_eq!(ledger.balance(&cost).await.unwrap().debits_posted, 500);

        let mut changed = loss_request;
        changed.amount_minor = 1251;
        assert_eq!(
            ledger.revalue(&changed).await,
            Err(MoneyError::IdempotencyConflict)
        );
        assert_eq!(ledger.transport().state.lock().unwrap().transfers.len(), 2);
    });
}

#[test]
fn zero_fx_revaluation_is_rejected_without_posting() {
    block_on(async {
        let ledger = Ledger::new(MockTransport::default());
        let result = ledger
            .revalue(&FxRevaluationRequest {
                event_id: "fx-close:2026-10:claude".to_owned(),
                provider: "claude".to_owned(),
                amount_minor: 0,
                direction: FxDirection::Loss,
            })
            .await;
        assert!(matches!(result, Err(MoneyError::InvalidInput(_))));
        assert!(ledger
            .transport()
            .state
            .lock()
            .unwrap()
            .transfers
            .is_empty());
    });
}

#[test]
fn suspense_resolution_moves_existing_value_once_without_reusing_settlement() {
    block_on(async {
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
        let original = FundingRequest {
            event_id: "collection:original".to_owned(),
            provider: "paystack".to_owned(),
            target: FundingTarget::Suspense,
            amount_minor: 12_500,
            currency: Currency::Ngn,
        };
        ledger.fund(&original).await.unwrap();
        let resolution = SuspenseResolutionRequest {
            event_id: original.event_id.clone(),
            provider: original.provider.clone(),
            tenant_id: "tenant-a".to_owned(),
            amount_minor: 12_500,
            currency: Currency::Ngn,
        };
        let first = ledger.resolve_suspense(&resolution).await.unwrap();
        let replay = ledger.resolve_suspense(&resolution).await.unwrap();
        assert_eq!(first, replay);
        let suspense = AccountSpec::new(
            AccountKey::CollectionSuspense {
                provider: "paystack".to_owned(),
            },
            Currency::Ngn,
        )
        .unwrap();
        let prepaid =
            AccountSpec::new(AccountKey::tenant_prepaid("tenant-a"), Currency::Ngn).unwrap();
        let settlement = AccountSpec::new(
            AccountKey::ProviderSettlement {
                provider: "paystack".to_owned(),
            },
            Currency::Ngn,
        )
        .unwrap();
        assert_eq!(
            ledger.balance(&suspense).await.unwrap().debits_posted,
            12_500
        );
        assert_eq!(
            ledger.balance(&prepaid).await.unwrap().credits_posted,
            12_500
        );
        assert_eq!(
            ledger.balance(&settlement).await.unwrap().debits_posted,
            12_500
        );
        let mut changed = resolution.clone();
        changed.tenant_id = "tenant-b".to_owned();
        assert_eq!(
            ledger.resolve_suspense(&changed).await,
            Err(MoneyError::IdempotencyConflict)
        );
        let excessive = SuspenseResolutionRequest {
            event_id: "collection:another".to_owned(),
            amount_minor: 12_501,
            ..resolution
        };
        assert_eq!(
            ledger.resolve_suspense(&excessive).await,
            Err(MoneyError::InsufficientFunds)
        );
        assert_eq!(
            ledger.balance(&prepaid).await.unwrap().credits_posted,
            12_500
        );
    });
}

#[test]
fn provider_cost_is_separate_attributable_and_replay_safe() {
    block_on(async {
        let ledger = Ledger::new(MockTransport::default());
        let cost = AccountSpec::new(
            AccountKey::ProviderCost {
                provider: "termii".to_owned(),
            },
            Currency::Ngn,
        )
        .unwrap();
        let payable = AccountSpec::new(
            AccountKey::ProviderPayable {
                provider: "termii".to_owned(),
            },
            Currency::Ngn,
        )
        .unwrap();
        for spec in [&cost, &payable] {
            ledger.provision_account(spec).await.unwrap();
        }
        let request = ProviderCostRequest {
            event_id: "usage:2026-W43:tenant-a".to_owned(),
            tenant_id: "tenant-a".to_owned(),
            provider: "termii".to_owned(),
            amount_minor: 1_250,
            currency: Currency::Ngn,
            offer: "01".to_owned(),
            corridor: "campaign-7".to_owned(),
        };
        let first = ledger.post_provider_cost(&request).await.unwrap();
        assert_eq!(first.amount_minor, 1_250);
        assert_eq!(ledger.post_provider_cost(&request).await.unwrap(), first);
        assert_eq!(ledger.balance(&cost).await.unwrap().debits_posted, 1_250);
        assert_eq!(
            ledger.balance(&payable).await.unwrap().credits_posted,
            1_250
        );
        let transfer = ledger
            .transport()
            .state
            .lock()
            .unwrap()
            .transfers
            .get(&first.transfer_id)
            .copied()
            .unwrap();
        assert_eq!(transfer.code, TransferKind::ProviderCost as u16);
        assert_eq!(
            transfer.user_data_128,
            world("tenant-a").prepaid.creation().user_data_128
        );

        let mut changed = request.clone();
        changed.amount_minor = 1_251;
        assert_eq!(
            ledger.post_provider_cost(&changed).await,
            Err(MoneyError::IdempotencyConflict)
        );
        let mut zero = request;
        zero.amount_minor = 0;
        assert!(matches!(
            ledger.post_provider_cost(&zero).await,
            Err(MoneyError::InvalidInput(_))
        ));
        assert_eq!(ledger.transport().state.lock().unwrap().transfers.len(), 1);
    });
}

#[test]
fn corrupt_prepaid_balances_and_taxonomy_fail_closed() {
    block_on(async {
        let (ledger, world) = funded_ledger(100).await;
        ledger
            .transport()
            .state
            .lock()
            .unwrap()
            .accounts
            .get_mut(&world.prepaid.id())
            .unwrap()
            .debits_posted = 101;
        assert!(matches!(
            ledger.balance(&world.prepaid).await,
            Err(MoneyError::InvariantViolation(_))
        ));

        let (ledger, world) = funded_ledger(100).await;
        ledger
            .transport()
            .state
            .lock()
            .unwrap()
            .accounts
            .get_mut(&world.prepaid.id())
            .unwrap()
            .code = 999;
        assert!(matches!(
            ledger.balance(&world.prepaid).await,
            Err(MoneyError::InvariantViolation(_))
        ));
    });
}

#[derive(Default)]
struct SnapshotTransport {
    pages: Mutex<VecDeque<Vec<Account>>>,
    queries: Mutex<Vec<AccountQuery>>,
}

impl SnapshotTransport {
    fn with_pages(pages: Vec<Vec<Account>>) -> Self {
        Self {
            pages: Mutex::new(pages.into()),
            queries: Mutex::new(Vec::new()),
        }
    }
}

impl Transport for SnapshotTransport {
    fn create_account(
        &self,
        _account: Account,
    ) -> TransportFuture<'_, Result<CreateAccountStatus, TransportError>> {
        Box::pin(async { Err(TransportError::new("not used by snapshot tests")) })
    }

    fn lookup_account(
        &self,
        _id: u128,
    ) -> TransportFuture<'_, Result<Option<Account>, TransportError>> {
        Box::pin(async { Err(TransportError::new("not used by snapshot tests")) })
    }

    fn query_accounts(
        &self,
        query: AccountQuery,
    ) -> TransportFuture<'_, Result<Vec<Account>, TransportError>> {
        Box::pin(async move {
            self.queries.lock().unwrap().push(query);
            Ok(self.pages.lock().unwrap().pop_front().unwrap_or_default())
        })
    }

    fn create_transfer(
        &self,
        _transfer: Transfer,
    ) -> TransportFuture<'_, Result<CreateTransferStatus, TransportError>> {
        Box::pin(async { Err(TransportError::new("not used by snapshot tests")) })
    }

    fn lookup_transfer(
        &self,
        _id: u128,
    ) -> TransportFuture<'_, Result<Option<Transfer>, TransportError>> {
        Box::pin(async { Err(TransportError::new("not used by snapshot tests")) })
    }
}

fn snapshot_account(id: u128, timestamp: u64) -> Account {
    Account {
        id,
        ledger: Currency::Ngn.ledger(),
        timestamp,
        ..Account::default()
    }
}

fn full_snapshot_page(timestamp: u64) -> Vec<Account> {
    (1..=8189)
        .map(|id| snapshot_account(id, timestamp))
        .collect()
}

#[test]
fn account_snapshot_paginates_without_copying_balances_to_a_database() {
    block_on(async {
        let transport = SnapshotTransport::with_pages(vec![
            full_snapshot_page(7),
            vec![snapshot_account(9_000, 8)],
        ]);
        let ledger = Ledger::new(transport);
        let snapshot = ledger.account_snapshot(Currency::Ngn).await.unwrap();
        assert_eq!(snapshot.len(), 8_190);
        let queries = ledger.transport().queries.lock().unwrap();
        assert_eq!(queries.len(), 2);
        assert_eq!(queries[0].timestamp_min, 0);
        assert_eq!(queries[1].timestamp_min, 8);
        assert!(queries.iter().all(|query| query.limit == 8_189));
        assert!(queries.iter().all(|query| query.ledger == 566));
    });
}

#[test]
fn account_snapshot_rejects_oversized_nonadvancing_and_cross_ledger_pages() {
    block_on(async {
        let oversized = Ledger::new(SnapshotTransport::with_pages(vec![(1..=8_190)
            .map(|id| snapshot_account(id, 1))
            .collect()]));
        assert!(matches!(
            oversized.account_snapshot(Currency::Ngn).await,
            Err(MoneyError::InvariantViolation(_))
        ));

        let stalled = Ledger::new(SnapshotTransport::with_pages(vec![full_snapshot_page(
            u64::MAX,
        )]));
        assert!(matches!(
            stalled.account_snapshot(Currency::Ngn).await,
            Err(MoneyError::InvariantViolation(_))
        ));

        let mut wrong_ledger = snapshot_account(1, 1);
        wrong_ledger.ledger = Currency::Usd.ledger();
        let crossed = Ledger::new(SnapshotTransport::with_pages(vec![vec![wrong_ledger]]));
        assert!(matches!(
            crossed.account_snapshot(Currency::Ngn).await,
            Err(MoneyError::InvariantViolation(_))
        ));

        let repeated = Ledger::new(SnapshotTransport::with_pages(vec![
            full_snapshot_page(1),
            vec![snapshot_account(1, 2)],
        ]));
        assert!(matches!(
            repeated.account_snapshot(Currency::Ngn).await,
            Err(MoneyError::InvariantViolation(_))
        ));
    });
}
