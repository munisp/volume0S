//! Independently confirmed collection processing.
//!
//! A signed webhook selects a payment to confirm; it never grants value by
//! itself. Only settled provider responses are posted, and ambiguous ownership
//! or amount/currency mismatches are isolated in provider suspense.

use std::fmt;

use volumeos_tb_adapter::ledger::{
    Currency, FundingReceipt, FundingRequest, FundingTarget, Ledger, MoneyError, Transport,
};

use crate::providers::{
    CollectionError, ConfirmationStatus, ConfirmedPayment, PaymentNotification, Provider,
    ProviderClient,
};

const MAX_PROVIDER_ID_BYTES: usize = 512;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CollectionExpectation {
    pub tenant_id: String,
    pub amount_minor: u128,
    pub currency: Currency,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SuspenseReason {
    Unmatched,
    ReferenceMismatch,
    AmountMismatch,
    CurrencyMismatch,
    TenantMismatch,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CollectionDisposition {
    Credited { tenant_id: String },
    Suspense { reason: SuspenseReason },
    Pending,
    Failed,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CollectionReceipt {
    pub event_id: String,
    pub provider: Provider,
    pub provider_payment_id: String,
    pub reference: String,
    pub amount_minor: u128,
    pub currency: Currency,
    pub disposition: CollectionDisposition,
    pub funding: Option<FundingReceipt>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ProcessingError {
    Confirmation(CollectionError),
    InvalidConfirmation(String),
    Ledger(MoneyError),
}

impl fmt::Display for ProcessingError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Confirmation(error) => write!(f, "{error}"),
            Self::InvalidConfirmation(message) => {
                write!(f, "invalid independent payment confirmation: {message}")
            }
            Self::Ledger(error) => write!(f, "{error}"),
        }
    }
}

impl std::error::Error for ProcessingError {}

impl From<CollectionError> for ProcessingError {
    fn from(value: CollectionError) -> Self {
        Self::Confirmation(value)
    }
}

impl From<MoneyError> for ProcessingError {
    fn from(value: MoneyError) -> Self {
        Self::Ledger(value)
    }
}

/// Process a webhook or reconciliation notification through the same
/// deterministic path. `None` means that no tenant expectation was found and a
/// settled payment must be routed to suspense rather than silently credited.
pub async fn process_collection<T: Transport>(
    notification: &PaymentNotification,
    expectation: Option<&CollectionExpectation>,
    provider: &dyn ProviderClient,
    ledger: &Ledger<T>,
) -> Result<CollectionReceipt, ProcessingError> {
    let confirmed = provider.confirm(notification).await?;
    validate_confirmation(notification, &confirmed)?;
    let event_id = collection_event_id(confirmed.provider, &confirmed.provider_payment_id);

    if confirmed.status != ConfirmationStatus::Settled {
        let disposition = if confirmed.status == ConfirmationStatus::Pending {
            CollectionDisposition::Pending
        } else {
            CollectionDisposition::Failed
        };
        return Ok(receipt(&event_id, &confirmed, disposition, None));
    }

    let routing = routing_decision(notification, expectation, &confirmed);
    let (target, disposition) = match routing {
        Routing::Credit { tenant_id } => (
            FundingTarget::TenantPrepaid {
                tenant_id: tenant_id.clone(),
            },
            CollectionDisposition::Credited { tenant_id },
        ),
        Routing::Suspense(reason) => (
            FundingTarget::Suspense,
            CollectionDisposition::Suspense { reason },
        ),
    };
    let funding = ledger
        .fund(&FundingRequest {
            event_id: event_id.clone(),
            provider: confirmed.provider.as_str().to_owned(),
            target,
            amount_minor: confirmed.amount_minor,
            currency: confirmed.currency,
        })
        .await?;
    Ok(receipt(&event_id, &confirmed, disposition, Some(funding)))
}

/// Stable across duplicate webhooks and reconciliation retries. Hashing keeps
/// arbitrary provider identifiers out of logs, transfer metadata, and ID fields.
pub fn collection_event_id(provider: Provider, provider_payment_id: &str) -> String {
    let digest = blake3::hash(
        format!(
            "volumeos:collection:v1:{}:{provider_payment_id}",
            provider.as_str()
        )
        .as_bytes(),
    );
    format!("collection:{digest}")
}

enum Routing {
    Credit { tenant_id: String },
    Suspense(SuspenseReason),
}

fn routing_decision(
    notification: &PaymentNotification,
    expectation: Option<&CollectionExpectation>,
    confirmed: &ConfirmedPayment,
) -> Routing {
    if confirmed.reference != notification.reference {
        return Routing::Suspense(SuspenseReason::ReferenceMismatch);
    }
    let Some(expected) = expectation else {
        return Routing::Suspense(SuspenseReason::Unmatched);
    };
    if confirmed.amount_minor != expected.amount_minor {
        return Routing::Suspense(SuspenseReason::AmountMismatch);
    }
    if confirmed.currency != expected.currency {
        return Routing::Suspense(SuspenseReason::CurrencyMismatch);
    }
    if confirmed.tenant_hint.as_deref() != Some(expected.tenant_id.as_str()) {
        return Routing::Suspense(SuspenseReason::TenantMismatch);
    }
    Routing::Credit {
        tenant_id: expected.tenant_id.clone(),
    }
}

fn validate_confirmation(
    notification: &PaymentNotification,
    confirmed: &ConfirmedPayment,
) -> Result<(), ProcessingError> {
    if confirmed.provider != notification.provider {
        return Err(ProcessingError::InvalidConfirmation(
            "provider changed during confirmation".to_owned(),
        ));
    }
    if confirmed.provider_payment_id.is_empty()
        || confirmed.provider_payment_id.len() > MAX_PROVIDER_ID_BYTES
    {
        return Err(ProcessingError::InvalidConfirmation(
            "provider payment ID is empty or too long".to_owned(),
        ));
    }
    if confirmed.reference.is_empty() || confirmed.reference.len() > MAX_PROVIDER_ID_BYTES {
        return Err(ProcessingError::InvalidConfirmation(
            "provider reference is empty or too long".to_owned(),
        ));
    }
    if confirmed.amount_minor == 0 {
        return Err(ProcessingError::InvalidConfirmation(
            "settled amount must be non-zero".to_owned(),
        ));
    }
    Ok(())
}

fn receipt(
    event_id: &str,
    confirmed: &ConfirmedPayment,
    disposition: CollectionDisposition,
    funding: Option<FundingReceipt>,
) -> CollectionReceipt {
    CollectionReceipt {
        event_id: event_id.to_owned(),
        provider: confirmed.provider,
        provider_payment_id: confirmed.provider_payment_id.clone(),
        reference: confirmed.reference.clone(),
        amount_minor: confirmed.amount_minor,
        currency: confirmed.currency,
        disposition,
        funding,
    }
}
