use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;

use futures::executor::block_on;
use volumeos_collection_adapters::processor::{
    collection_event_id, process_collection, CollectionDisposition, CollectionExpectation,
    ProcessingError, SuspenseReason,
};
use volumeos_collection_adapters::providers::{
    CheckoutRequest, CheckoutSession, CollectionError, ConfirmationStatus, ConfirmedPayment,
    PaymentNotification, Provider, ProviderClient, ProviderFuture,
};
use volumeos_tb_adapter::ledger::{
    Account, AccountKey, AccountSpec, CreateAccountStatus, CreateTransferStatus, Currency, Ledger,
    MoneyError, Transfer, Transport, TransportError, TransportFuture,
};

#[derive(Default)]
struct MockState {
    accounts: HashMap<u128, Account>,
    transfers: HashMap<u128, Transfer>,
}

/// Transport-seam test double only; no real TigerBeetle cluster is contacted.
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
            debit.debits_posted = debit.debits_posted.checked_add(transfer.amount).unwrap();
            let credit = state.accounts.get_mut(&transfer.credit_account_id).unwrap();
            credit.credits_posted = credit.credits_posted.checked_add(transfer.amount).unwrap();
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
    calls: AtomicUsize,
}

impl MockProvider {
    fn new(payment: ConfirmedPayment) -> Self {
        Self {
            payment: Mutex::new(payment),
            calls: AtomicUsize::new(0),
        }
    }

    fn set(&self, payment: ConfirmedPayment) {
        *self.payment.lock().unwrap() = payment;
    }
}

impl ProviderClient for MockProvider {
    fn initialize(
        &self,
        _request: &CheckoutRequest,
    ) -> ProviderFuture<'_, Result<CheckoutSession, CollectionError>> {
        Box::pin(async {
            Err(CollectionError::Provider(
                "checkout is outside this test double's scope".to_owned(),
            ))
        })
    }

    fn confirm(
        &self,
        _notification: &PaymentNotification,
    ) -> ProviderFuture<'_, Result<ConfirmedPayment, CollectionError>> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        let payment = self.payment.lock().unwrap().clone();
        Box::pin(async move { Ok(payment) })
    }
}

fn notification(provider: Provider) -> PaymentNotification {
    PaymentNotification {
        provider,
        notification_id: "notification-1".to_owned(),
        confirmation_id: "charge-1".to_owned(),
        reference: "order-1".to_owned(),
    }
}

fn confirmed(provider: Provider) -> ConfirmedPayment {
    ConfirmedPayment {
        provider,
        provider_payment_id: "provider-payment-1".to_owned(),
        reference: "order-1".to_owned(),
        amount_minor: 125_000,
        currency: Currency::Ngn,
        status: ConfirmationStatus::Settled,
        tenant_hint: Some("tenant-a".to_owned()),
    }
}

fn expectation() -> CollectionExpectation {
    CollectionExpectation {
        tenant_id: "tenant-a".to_owned(),
        amount_minor: 125_000,
        currency: Currency::Ngn,
    }
}

async fn provisioned_ledger(provider: Provider) -> Ledger<MockTransport> {
    let ledger = Ledger::new(MockTransport::default());
    for currency in [Currency::Ngn, Currency::Usd] {
        for key in [
            AccountKey::ProviderSettlement {
                provider: provider.as_str().to_owned(),
            },
            AccountKey::CollectionSuspense {
                provider: provider.as_str().to_owned(),
            },
            AccountKey::tenant_prepaid("tenant-a"),
            AccountKey::tenant_prepaid("tenant-b"),
        ] {
            ledger
                .provision_account(&AccountSpec::new(key, currency).unwrap())
                .await
                .unwrap();
        }
    }
    ledger
}

async fn prepaid_balance<T: Transport>(ledger: &Ledger<T>, currency: Currency) -> u128 {
    ledger
        .balance(&AccountSpec::new(AccountKey::tenant_prepaid("tenant-a"), currency).unwrap())
        .await
        .unwrap()
        .available_minor
        .unwrap()
}

async fn suspense_credit<T: Transport>(
    ledger: &Ledger<T>,
    provider: Provider,
    currency: Currency,
) -> u128 {
    ledger
        .balance(
            &AccountSpec::new(
                AccountKey::CollectionSuspense {
                    provider: provider.as_str().to_owned(),
                },
                currency,
            )
            .unwrap(),
        )
        .await
        .unwrap()
        .credits_posted
}

#[test]
fn exact_confirmation_credits_tenant_prepaid_once() {
    block_on(async {
        let ledger = provisioned_ledger(Provider::Paystack).await;
        let provider = MockProvider::new(confirmed(Provider::Paystack));
        let first = process_collection(
            &notification(Provider::Paystack),
            Some(&expectation()),
            &provider,
            &ledger,
        )
        .await
        .unwrap();
        let second = process_collection(
            &notification(Provider::Paystack),
            Some(&expectation()),
            &provider,
            &ledger,
        )
        .await
        .unwrap();

        assert_eq!(first, second);
        assert_eq!(
            first.disposition,
            CollectionDisposition::Credited {
                tenant_id: "tenant-a".to_owned()
            }
        );
        assert!(first.funding.is_some());
        assert_eq!(prepaid_balance(&ledger, Currency::Ngn).await, 125_000);
        assert_eq!(ledger.transport().state.lock().unwrap().transfers.len(), 1);
        assert_eq!(provider.calls.load(Ordering::SeqCst), 2);
    });
}

#[test]
fn unmatched_settlement_is_routed_to_suspense() {
    block_on(async {
        let ledger = provisioned_ledger(Provider::Paystack).await;
        let provider = MockProvider::new(confirmed(Provider::Paystack));
        let receipt =
            process_collection(&notification(Provider::Paystack), None, &provider, &ledger)
                .await
                .unwrap();

        assert_eq!(
            receipt.disposition,
            CollectionDisposition::Suspense {
                reason: SuspenseReason::Unmatched
            }
        );
        assert_eq!(
            suspense_credit(&ledger, Provider::Paystack, Currency::Ngn).await,
            125_000
        );
        assert_eq!(prepaid_balance(&ledger, Currency::Ngn).await, 0);
    });
}

#[test]
fn amount_mismatch_does_not_credit_tenant() {
    block_on(async {
        let ledger = provisioned_ledger(Provider::Flutterwave).await;
        let provider = MockProvider::new(confirmed(Provider::Flutterwave));
        let mut expected = expectation();
        expected.amount_minor = 124_999;
        let receipt = process_collection(
            &notification(Provider::Flutterwave),
            Some(&expected),
            &provider,
            &ledger,
        )
        .await
        .unwrap();

        assert_eq!(
            receipt.disposition,
            CollectionDisposition::Suspense {
                reason: SuspenseReason::AmountMismatch
            }
        );
        assert_eq!(prepaid_balance(&ledger, Currency::Ngn).await, 0);
        assert_eq!(
            suspense_credit(&ledger, Provider::Flutterwave, Currency::Ngn).await,
            125_000
        );
    });
}

#[test]
fn currency_mismatch_uses_actual_currency_suspense() {
    block_on(async {
        let ledger = provisioned_ledger(Provider::Moniepoint).await;
        let mut payment = confirmed(Provider::Moniepoint);
        payment.currency = Currency::Usd;
        let provider = MockProvider::new(payment);
        let receipt = process_collection(
            &notification(Provider::Moniepoint),
            Some(&expectation()),
            &provider,
            &ledger,
        )
        .await
        .unwrap();

        assert_eq!(
            receipt.disposition,
            CollectionDisposition::Suspense {
                reason: SuspenseReason::CurrencyMismatch
            }
        );
        assert_eq!(prepaid_balance(&ledger, Currency::Ngn).await, 0);
        assert_eq!(prepaid_balance(&ledger, Currency::Usd).await, 0);
        assert_eq!(
            suspense_credit(&ledger, Provider::Moniepoint, Currency::Usd).await,
            125_000
        );
    });
}

#[test]
fn missing_or_different_tenant_hint_goes_to_suspense() {
    block_on(async {
        for hint in [None, Some("tenant-b".to_owned())] {
            let ledger = provisioned_ledger(Provider::Paystack).await;
            let mut payment = confirmed(Provider::Paystack);
            payment.tenant_hint = hint;
            let provider = MockProvider::new(payment);
            let receipt = process_collection(
                &notification(Provider::Paystack),
                Some(&expectation()),
                &provider,
                &ledger,
            )
            .await
            .unwrap();
            assert_eq!(
                receipt.disposition,
                CollectionDisposition::Suspense {
                    reason: SuspenseReason::TenantMismatch
                }
            );
            assert_eq!(prepaid_balance(&ledger, Currency::Ngn).await, 0);
        }
    });
}

#[test]
fn changed_provider_reference_goes_to_suspense() {
    block_on(async {
        let ledger = provisioned_ledger(Provider::Paystack).await;
        let mut payment = confirmed(Provider::Paystack);
        payment.reference = "different-order".to_owned();
        let provider = MockProvider::new(payment);
        let receipt = process_collection(
            &notification(Provider::Paystack),
            Some(&expectation()),
            &provider,
            &ledger,
        )
        .await
        .unwrap();

        assert_eq!(
            receipt.disposition,
            CollectionDisposition::Suspense {
                reason: SuspenseReason::ReferenceMismatch
            }
        );
        assert_eq!(prepaid_balance(&ledger, Currency::Ngn).await, 0);
    });
}

#[test]
fn pending_and_failed_confirmations_never_post_value() {
    block_on(async {
        for status in [ConfirmationStatus::Pending, ConfirmationStatus::Failed] {
            let ledger = provisioned_ledger(Provider::Paystack).await;
            let mut payment = confirmed(Provider::Paystack);
            payment.status = status;
            let provider = MockProvider::new(payment);
            let receipt = process_collection(
                &notification(Provider::Paystack),
                Some(&expectation()),
                &provider,
                &ledger,
            )
            .await
            .unwrap();
            let expected = if status == ConfirmationStatus::Pending {
                CollectionDisposition::Pending
            } else {
                CollectionDisposition::Failed
            };
            assert_eq!(receipt.disposition, expected);
            assert!(receipt.funding.is_none());
            assert!(ledger
                .transport()
                .state
                .lock()
                .unwrap()
                .transfers
                .is_empty());
        }
    });
}

#[test]
fn changed_amount_on_replay_preserves_idempotency_conflict() {
    block_on(async {
        let ledger = provisioned_ledger(Provider::Paystack).await;
        let provider = MockProvider::new(confirmed(Provider::Paystack));
        process_collection(
            &notification(Provider::Paystack),
            Some(&expectation()),
            &provider,
            &ledger,
        )
        .await
        .unwrap();
        let mut changed = confirmed(Provider::Paystack);
        changed.amount_minor = 125_001;
        provider.set(changed);
        let error = process_collection(
            &notification(Provider::Paystack),
            Some(&expectation()),
            &provider,
            &ledger,
        )
        .await
        .unwrap_err();

        assert_eq!(
            error,
            ProcessingError::Ledger(MoneyError::IdempotencyConflict)
        );
        assert_eq!(prepaid_balance(&ledger, Currency::Ngn).await, 125_000);
        assert_eq!(ledger.transport().state.lock().unwrap().transfers.len(), 1);
    });
}

#[test]
fn changed_replay_target_cannot_move_payment_out_of_prepaid() {
    block_on(async {
        let ledger = provisioned_ledger(Provider::Paystack).await;
        let provider = MockProvider::new(confirmed(Provider::Paystack));
        process_collection(
            &notification(Provider::Paystack),
            Some(&expectation()),
            &provider,
            &ledger,
        )
        .await
        .unwrap();
        let mut changed = confirmed(Provider::Paystack);
        changed.tenant_hint = Some("tenant-b".to_owned());
        provider.set(changed);
        let error = process_collection(
            &notification(Provider::Paystack),
            Some(&expectation()),
            &provider,
            &ledger,
        )
        .await
        .unwrap_err();

        assert_eq!(
            error,
            ProcessingError::Ledger(MoneyError::IdempotencyConflict)
        );
        assert_eq!(prepaid_balance(&ledger, Currency::Ngn).await, 125_000);
        assert_eq!(
            suspense_credit(&ledger, Provider::Paystack, Currency::Ngn).await,
            0
        );
    });
}

#[test]
fn provider_identity_change_fails_closed_without_posting() {
    block_on(async {
        let ledger = provisioned_ledger(Provider::Paystack).await;
        let provider = MockProvider::new(confirmed(Provider::Flutterwave));
        let error = process_collection(
            &notification(Provider::Paystack),
            Some(&expectation()),
            &provider,
            &ledger,
        )
        .await
        .unwrap_err();
        assert!(matches!(error, ProcessingError::InvalidConfirmation(_)));
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
fn collection_event_ids_are_bounded_deterministic_and_provider_scoped() {
    let first = collection_event_id(Provider::Paystack, "payment-1");
    assert_eq!(first, collection_event_id(Provider::Paystack, "payment-1"));
    assert_ne!(
        first,
        collection_event_id(Provider::Flutterwave, "payment-1")
    );
    assert_ne!(first, collection_event_id(Provider::Paystack, "payment-2"));
    assert!(first.len() <= 128);
    assert!(first.starts_with("collection:"));
}
