//! Tenant-aware account taxonomy and idempotent money operations.
//!
//! The transport boundary exposes only TigerBeetle-style accounts and transfers.
//! No balance is read from, or written to, PostgreSQL. The deterministic IDs make
//! a retry safe even when the original request outcome was lost in transit.

use std::collections::HashSet;
use std::fmt;
use std::future::Future;
use std::pin::Pin;

pub const ACCOUNT_DEBITS_MUST_NOT_EXCEED_CREDITS: u16 = 1 << 1;
pub const ACCOUNT_CREDITS_MUST_NOT_EXCEED_DEBITS: u16 = 1 << 2;
pub const ACCOUNT_HISTORY: u16 = 1 << 3;
pub const TRANSFER_PENDING: u16 = 1 << 1;
pub const TRANSFER_POST_PENDING: u16 = 1 << 2;
pub const TRANSFER_VOID_PENDING: u16 = 1 << 3;

pub type TransportFuture<'a, T> = Pin<Box<dyn Future<Output = T> + Send + 'a>>;

/// Read-only TigerBeetle account query. Zero metadata/code values are wildcards;
/// timestamp_min supports deterministic forward pagination.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct AccountQuery {
    pub ledger: u32,
    pub code: u16,
    pub user_data_128: u128,
    pub timestamp_min: u64,
    pub limit: u32,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash)]
pub enum Currency {
    Ngn,
    Usd,
}

impl Currency {
    pub fn ledger(self) -> u32 {
        match self {
            // ISO 4217 numeric codes. Each currency is a separate TigerBeetle ledger.
            Self::Ngn => 566,
            Self::Usd => 840,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Ngn => "NGN",
            Self::Usd => "USD",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Hash)]
pub enum AccountKey {
    TenantPrepaid { tenant_id: String },
    Revenue { offer: String },
    Usage { channel: String },
    ProviderCost { provider: String },
    ProviderPayable { provider: String },
    PartnerShare { partner_id: String },
    FxGain,
    FxLoss,
    ProviderSettlement { provider: String },
    CollectionSuspense { provider: String },
}

impl AccountKey {
    pub fn tenant_prepaid(tenant_id: impl Into<String>) -> Self {
        Self::TenantPrepaid {
            tenant_id: tenant_id.into(),
        }
    }

    pub fn revenue(offer: impl Into<String>) -> Self {
        Self::Revenue {
            offer: offer.into(),
        }
    }

    pub fn validate(&self) -> Result<(), MoneyError> {
        match self {
            Self::TenantPrepaid { tenant_id } => validate_id("tenant_id", tenant_id),
            Self::Revenue { offer } => validate_offer(offer),
            Self::Usage { channel } => validate_id("channel", channel),
            Self::ProviderCost { provider }
            | Self::ProviderPayable { provider }
            | Self::ProviderSettlement { provider }
            | Self::CollectionSuspense { provider } => validate_id("provider", provider),
            Self::PartnerShare { partner_id } => validate_id("partner_id", partner_id),
            Self::FxGain | Self::FxLoss => Ok(()),
        }
    }

    pub fn canonical(&self) -> String {
        match self {
            Self::TenantPrepaid { tenant_id } => format!("liability:tenant#{tenant_id}:prepaid"),
            Self::Revenue { offer } => format!("revenue:offer{offer}"),
            Self::Usage { channel } => format!("recovery:usage:{channel}"),
            Self::ProviderCost { provider } => format!("cost:{provider}"),
            Self::ProviderPayable { provider } => format!("liability:provider#{provider}:payable"),
            Self::PartnerShare { partner_id } => format!("liability:partner#{partner_id}"),
            Self::FxGain => "fx:gain".to_owned(),
            Self::FxLoss => "fx:loss".to_owned(),
            Self::ProviderSettlement { provider } => format!("assets:{provider}_settlement"),
            Self::CollectionSuspense { provider } => format!("suspense:{provider}"),
        }
    }

    pub fn code(&self) -> u16 {
        match self {
            Self::TenantPrepaid { .. } => 100,
            Self::Revenue { offer } => 200 + offer.parse::<u16>().expect("validated offer"),
            Self::Usage { .. } => 300,
            Self::ProviderCost { .. } => 400,
            Self::ProviderPayable { .. } => 410,
            Self::PartnerShare { .. } => 500,
            Self::FxGain => 601,
            Self::FxLoss => 602,
            Self::ProviderSettlement { .. } => 700,
            Self::CollectionSuspense { .. } => 800,
        }
    }

    pub fn tenant_id(&self) -> Option<&str> {
        match self {
            Self::TenantPrepaid { tenant_id } => Some(tenant_id),
            _ => None,
        }
    }

    fn flags(&self) -> u16 {
        let constraint = match self {
            Self::TenantPrepaid { .. } | Self::CollectionSuspense { .. } => {
                ACCOUNT_DEBITS_MUST_NOT_EXCEED_CREDITS
            }
            Self::ProviderSettlement { .. } => ACCOUNT_CREDITS_MUST_NOT_EXCEED_DEBITS,
            _ => 0,
        };
        ACCOUNT_HISTORY | constraint
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountSpec {
    pub key: AccountKey,
    pub currency: Currency,
}

impl AccountSpec {
    pub fn new(key: AccountKey, currency: Currency) -> Result<Self, MoneyError> {
        key.validate()?;
        Ok(Self { key, currency })
    }

    pub fn id(&self) -> u128 {
        deterministic_id(&format!(
            "volumeos:account:v1:{}:{}",
            self.currency.as_str(),
            self.key.canonical()
        ))
    }

    pub fn creation(&self) -> Account {
        Account {
            id: self.id(),
            ledger: self.currency.ledger(),
            code: self.key.code(),
            user_data_128: self.key.tenant_id().map(tenant_fingerprint).unwrap_or(0),
            user_data_64: hash64(&format!("volumeos:account-key:v1:{}", self.key.canonical())),
            user_data_32: 0,
            flags: self.key.flags(),
            ..Account::default()
        }
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Account {
    pub id: u128,
    pub debits_pending: u128,
    pub debits_posted: u128,
    pub credits_pending: u128,
    pub credits_posted: u128,
    pub user_data_128: u128,
    pub user_data_64: u64,
    pub user_data_32: u32,
    pub ledger: u32,
    pub code: u16,
    pub flags: u16,
    pub timestamp: u64,
}

impl Account {
    fn same_creation(&self, other: &Self) -> bool {
        self.id == other.id
            && self.user_data_128 == other.user_data_128
            && self.user_data_64 == other.user_data_64
            && self.user_data_32 == other.user_data_32
            && self.ledger == other.ledger
            && self.code == other.code
            && self.flags == other.flags
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TransferKind {
    Campaign = 110,
    Retainer = 120,
    Usage = 130,
    ProviderCost = 140,
    PartnerShare = 150,
    FxRevaluation = 160,
    Funding = 170,
    Interop = 180,
    SuspenseResolution = 190,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ChargeRequest {
    pub event_id: String,
    pub tenant_id: String,
    pub debit: AccountKey,
    pub credit: AccountKey,
    pub amount_minor: u128,
    pub currency: Currency,
    pub kind: TransferKind,
    pub timeout_seconds: u32,
    pub offer: Option<String>,
    pub corridor: Option<String>,
}

impl ChargeRequest {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        event_id: impl Into<String>,
        tenant_id: impl Into<String>,
        debit: AccountKey,
        credit: AccountKey,
        amount_minor: u128,
        currency: Currency,
        kind: TransferKind,
        timeout_seconds: u32,
    ) -> Self {
        Self {
            event_id: event_id.into(),
            tenant_id: tenant_id.into(),
            debit,
            credit,
            amount_minor,
            currency,
            kind,
            timeout_seconds,
            offer: None,
            corridor: None,
        }
    }

    pub fn with_context(mut self, offer: Option<String>, corridor: Option<String>) -> Self {
        self.offer = offer;
        self.corridor = corridor;
        self
    }

    fn validate(&self) -> Result<(), MoneyError> {
        validate_id("event_id", &self.event_id)?;
        validate_id("tenant_id", &self.tenant_id)?;
        self.debit.validate()?;
        self.credit.validate()?;
        if self.debit == self.credit || self.amount_minor == 0 || self.timeout_seconds == 0 {
            return Err(MoneyError::InvalidInput(
                "charge accounts must differ, amount and reserve timeout must be non-zero"
                    .to_owned(),
            ));
        }
        if let Some(offer) = &self.offer {
            validate_offer(offer)?;
        }
        if let Some(corridor) = &self.corridor {
            validate_id("corridor", corridor)?;
        }
        Ok(())
    }

    fn metadata_64(&self) -> u64 {
        hash64(&format!(
            "volumeos:transfer-context:v1:offer={}:corridor={}",
            self.offer.as_deref().unwrap_or(""),
            self.corridor.as_deref().unwrap_or("")
        ))
    }

    fn pending_transfer(&self) -> Transfer {
        let debit = AccountSpec::new(self.debit.clone(), self.currency)
            .expect("validated charge")
            .id();
        let credit = AccountSpec::new(self.credit.clone(), self.currency)
            .expect("validated charge")
            .id();
        Transfer {
            id: reserve_id(&self.event_id),
            debit_account_id: debit,
            credit_account_id: credit,
            amount: self.amount_minor,
            pending_id: 0,
            user_data_128: tenant_fingerprint(&self.tenant_id),
            user_data_64: self.metadata_64(),
            user_data_32: 0,
            timeout: self.timeout_seconds,
            ledger: self.currency.ledger(),
            code: self.kind as u16,
            flags: TRANSFER_PENDING,
            timestamp: 0,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum FundingTarget {
    TenantPrepaid { tenant_id: String },
    Suspense,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct FundingRequest {
    pub event_id: String,
    pub provider: String,
    pub target: FundingTarget,
    pub amount_minor: u128,
    pub currency: Currency,
}

impl FundingRequest {
    fn validate(&self) -> Result<(), MoneyError> {
        validate_id("event_id", &self.event_id)?;
        validate_id("provider", &self.provider)?;
        if let FundingTarget::TenantPrepaid { tenant_id } = &self.target {
            validate_id("tenant_id", tenant_id)?;
        }
        if self.amount_minor == 0 {
            return Err(MoneyError::InvalidInput(
                "funding amount must be non-zero".to_owned(),
            ));
        }
        Ok(())
    }

    fn transfer(&self) -> Transfer {
        let tenant_id = match &self.target {
            FundingTarget::TenantPrepaid { tenant_id } => Some(tenant_id.as_str()),
            FundingTarget::Suspense => None,
        };
        let credit = match &self.target {
            FundingTarget::TenantPrepaid { tenant_id } => AccountKey::tenant_prepaid(tenant_id),
            FundingTarget::Suspense => AccountKey::CollectionSuspense {
                provider: self.provider.clone(),
            },
        };
        Transfer {
            id: funding_id(&self.event_id),
            debit_account_id: AccountSpec::new(
                AccountKey::ProviderSettlement {
                    provider: self.provider.clone(),
                },
                self.currency,
            )
            .expect("validated funding")
            .id(),
            credit_account_id: AccountSpec::new(credit, self.currency)
                .expect("validated funding")
                .id(),
            amount: self.amount_minor,
            user_data_128: tenant_id.map(tenant_fingerprint).unwrap_or(0),
            user_data_64: hash64(&format!(
                "volumeos:funding:v1:provider={}:target={}",
                self.provider,
                tenant_id.unwrap_or("suspense")
            )),
            ledger: self.currency.ledger(),
            code: TransferKind::Funding as u16,
            ..Transfer::default()
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct FundingReceipt {
    pub transfer_id: u128,
    pub amount_minor: u128,
}

/// Recognize provider cost independently from the tenant's usage recovery.
/// The tenant fingerprint and commercial context make the posting attributable
/// without storing a derived financial balance in PostgreSQL.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProviderCostRequest {
    pub event_id: String,
    pub tenant_id: String,
    pub provider: String,
    pub amount_minor: u128,
    pub currency: Currency,
    pub offer: String,
    pub corridor: String,
}

impl ProviderCostRequest {
    fn validate(&self) -> Result<(), MoneyError> {
        validate_id("event_id", &self.event_id)?;
        validate_id("tenant_id", &self.tenant_id)?;
        validate_id("provider", &self.provider)?;
        validate_offer(&self.offer)?;
        validate_id("corridor", &self.corridor)?;
        if self.amount_minor == 0 {
            return Err(MoneyError::InvalidInput(
                "provider cost amount must be non-zero".to_owned(),
            ));
        }
        Ok(())
    }

    fn transfer(&self) -> Transfer {
        Transfer {
            id: provider_cost_id(&self.event_id),
            debit_account_id: AccountSpec::new(
                AccountKey::ProviderCost {
                    provider: self.provider.clone(),
                },
                self.currency,
            )
            .expect("validated provider cost")
            .id(),
            credit_account_id: AccountSpec::new(
                AccountKey::ProviderPayable {
                    provider: self.provider.clone(),
                },
                self.currency,
            )
            .expect("validated provider payable")
            .id(),
            amount: self.amount_minor,
            user_data_128: tenant_fingerprint(&self.tenant_id),
            user_data_64: hash64(&format!(
                "volumeos:provider-cost:v1:provider={}:offer={}:corridor={}",
                self.provider, self.offer, self.corridor
            )),
            ledger: self.currency.ledger(),
            code: TransferKind::ProviderCost as u16,
            ..Transfer::default()
        }
    }
}

/// Move an already booked provider-suspense receipt to reviewed tenant prepaid.
/// The transfer ID depends only on the original collection event, so a second
/// operator resolution cannot credit the same settled payment twice.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SuspenseResolutionRequest {
    pub event_id: String,
    pub provider: String,
    pub tenant_id: String,
    pub amount_minor: u128,
    pub currency: Currency,
}

impl SuspenseResolutionRequest {
    fn validate(&self) -> Result<(), MoneyError> {
        validate_id("event_id", &self.event_id)?;
        validate_id("provider", &self.provider)?;
        validate_id("tenant_id", &self.tenant_id)?;
        if self.amount_minor == 0 {
            return Err(MoneyError::InvalidInput(
                "suspense resolution amount must be non-zero".to_owned(),
            ));
        }
        Ok(())
    }

    fn transfer(&self) -> Transfer {
        Transfer {
            id: deterministic_id(&format!(
                "volumeos:suspense-resolution:v1:{}",
                self.event_id
            )),
            debit_account_id: AccountSpec::new(
                AccountKey::CollectionSuspense {
                    provider: self.provider.clone(),
                },
                self.currency,
            )
            .expect("validated suspense resolution")
            .id(),
            credit_account_id: AccountSpec::new(
                AccountKey::tenant_prepaid(&self.tenant_id),
                self.currency,
            )
            .expect("validated suspense resolution")
            .id(),
            amount: self.amount_minor,
            user_data_128: tenant_fingerprint(&self.tenant_id),
            user_data_64: hash64(&format!(
                "volumeos:suspense-resolution-context:v1:{}",
                self.event_id
            )),
            ledger: self.currency.ledger(),
            code: TransferKind::SuspenseResolution as u16,
            ..Transfer::default()
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FxDirection {
    Gain,
    Loss,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct FxRevaluationRequest {
    pub event_id: String,
    pub provider: String,
    pub amount_minor: u128,
    pub direction: FxDirection,
}

impl FxRevaluationRequest {
    fn validate(&self) -> Result<(), MoneyError> {
        validate_id("event_id", &self.event_id)?;
        validate_id("provider", &self.provider)?;
        if self.amount_minor == 0 {
            return Err(MoneyError::InvalidInput(
                "FX revaluation amount must be non-zero".to_owned(),
            ));
        }
        Ok(())
    }

    fn transfer(&self) -> Transfer {
        let loss = matches!(self.direction, FxDirection::Loss);
        let fx = if loss {
            AccountKey::FxLoss
        } else {
            AccountKey::FxGain
        };
        let counter = AccountKey::ProviderCost {
            provider: self.provider.clone(),
        };
        let (debit, credit) = if loss { (fx, counter) } else { (counter, fx) };
        Transfer {
            id: funding_id(&self.event_id),
            debit_account_id: AccountSpec::new(debit, Currency::Ngn)
                .expect("validated FX account")
                .id(),
            credit_account_id: AccountSpec::new(credit, Currency::Ngn)
                .expect("validated FX account")
                .id(),
            amount: self.amount_minor,
            user_data_64: hash64(&format!(
                "volumeos:fx:v1:provider={}:direction={}",
                self.provider,
                if loss { "loss" } else { "gain" }
            )),
            ledger: Currency::Ngn.ledger(),
            code: TransferKind::FxRevaluation as u16,
            ..Transfer::default()
        }
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Transfer {
    pub id: u128,
    pub debit_account_id: u128,
    pub credit_account_id: u128,
    pub amount: u128,
    pub pending_id: u128,
    pub user_data_128: u128,
    pub user_data_64: u64,
    pub user_data_32: u32,
    pub timeout: u32,
    pub ledger: u32,
    pub code: u16,
    pub flags: u16,
    pub timestamp: u64,
}

impl Transfer {
    fn same_creation(&self, other: &Self) -> bool {
        self.id == other.id
            && self.debit_account_id == other.debit_account_id
            && self.credit_account_id == other.credit_account_id
            && self.amount == other.amount
            && self.pending_id == other.pending_id
            && self.user_data_128 == other.user_data_128
            && self.user_data_64 == other.user_data_64
            && self.user_data_32 == other.user_data_32
            && self.timeout == other.timeout
            && self.ledger == other.ledger
            && self.code == other.code
            && self.flags == other.flags
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PostingState {
    Pending,
    Posted,
    Voided,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Reservation {
    pub event_id: String,
    pub tenant_id: String,
    pub transfer_id: u128,
    pub amount_minor: u128,
    pub state: PostingState,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Balance {
    pub account_id: u128,
    pub debits_pending: u128,
    pub debits_posted: u128,
    pub credits_pending: u128,
    pub credits_posted: u128,
    /// Spendable prepaid funds: posted credits less posted and pending debits.
    /// `None` for non-prepaid account types, whose normal balance differs.
    pub available_minor: Option<u128>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum MoneyError {
    InvalidInput(String),
    IdempotencyConflict,
    UnknownAccount,
    UnknownReservation,
    AlreadySettled,
    ReservationExpired,
    InsufficientFunds,
    Transport(String),
    InvariantViolation(String),
}

impl fmt::Display for MoneyError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidInput(message) => write!(f, "invalid money input: {message}"),
            Self::IdempotencyConflict => {
                f.write_str("event ID was already used with different money fields")
            }
            Self::UnknownAccount => f.write_str("ledger account was not found"),
            Self::UnknownReservation => f.write_str("pending transfer was not found"),
            Self::AlreadySettled => f.write_str("pending transfer was already settled"),
            Self::ReservationExpired => f.write_str("pending transfer expired"),
            Self::InsufficientFunds => {
                f.write_str("account balance constraint rejected the transfer")
            }
            Self::Transport(message) => write!(f, "TigerBeetle transport failed: {message}"),
            Self::InvariantViolation(message) => write!(f, "ledger invariant failed: {message}"),
        }
    }
}

impl std::error::Error for MoneyError {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TransportError {
    pub message: String,
}

impl TransportError {
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl fmt::Display for TransportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for TransportError {}

impl From<TransportError> for MoneyError {
    fn from(value: TransportError) -> Self {
        Self::Transport(value.message)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CreateAccountStatus {
    Created,
    Exists,
    IdempotencyConflict,
    Rejected(String),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CreateTransferStatus {
    Created,
    Exists,
    IdempotencyConflict,
    UnknownAccount,
    UnknownReservation,
    AlreadySettled,
    Expired,
    InsufficientFunds,
    Rejected(String),
}

/// Minimal transport contract implemented by the official client and by tests.
/// Implementations must return point-lookup results from the financial store.
pub trait Transport: Send + Sync {
    fn create_account(
        &self,
        account: Account,
    ) -> TransportFuture<'_, Result<CreateAccountStatus, TransportError>>;
    fn lookup_account(
        &self,
        id: u128,
    ) -> TransportFuture<'_, Result<Option<Account>, TransportError>>;
    fn query_accounts(
        &self,
        _query: AccountQuery,
    ) -> TransportFuture<'_, Result<Vec<Account>, TransportError>> {
        Box::pin(async {
            Err(TransportError::new(
                "account queries are not supported by this transport",
            ))
        })
    }
    fn create_transfer(
        &self,
        transfer: Transfer,
    ) -> TransportFuture<'_, Result<CreateTransferStatus, TransportError>>;
    fn lookup_transfer(
        &self,
        id: u128,
    ) -> TransportFuture<'_, Result<Option<Transfer>, TransportError>>;
}

pub struct Ledger<T: Transport> {
    transport: T,
}

impl<T: Transport> Ledger<T> {
    pub fn new(transport: T) -> Self {
        Self { transport }
    }

    pub fn transport(&self) -> &T {
        &self.transport
    }

    /// Return every account in one currency ledger without copying balances into
    /// PostgreSQL. Pagination is mandatory once a full TigerBeetle page is seen.
    pub async fn account_snapshot(&self, currency: Currency) -> Result<Vec<Account>, MoneyError> {
        const PAGE_LIMIT: u32 = 8189;
        let mut timestamp_min = 0_u64;
        let mut snapshot = Vec::new();
        let mut seen = HashSet::new();
        loop {
            let page = self
                .transport
                .query_accounts(AccountQuery {
                    ledger: currency.ledger(),
                    code: 0,
                    user_data_128: 0,
                    timestamp_min,
                    limit: PAGE_LIMIT,
                })
                .await?;
            if page.len() > PAGE_LIMIT as usize {
                return Err(MoneyError::InvariantViolation(
                    "TigerBeetle exceeded the requested account page limit".to_owned(),
                ));
            }
            let full_page = page.len() == PAGE_LIMIT as usize;
            let last_timestamp = page.last().map(|account| account.timestamp);
            for account in page {
                if account.ledger != currency.ledger()
                    || account.timestamp < timestamp_min
                    || !seen.insert(account.id)
                {
                    return Err(MoneyError::InvariantViolation(
                        "account snapshot crossed its ledger or repeated an account".to_owned(),
                    ));
                }
                snapshot.push(account);
            }
            if !full_page {
                break;
            }
            let next = last_timestamp
                .and_then(|value| value.checked_add(1))
                .filter(|value| *value > timestamp_min)
                .ok_or_else(|| {
                    MoneyError::InvariantViolation(
                        "account snapshot pagination did not advance".to_owned(),
                    )
                })?;
            timestamp_min = next;
        }
        Ok(snapshot)
    }

    pub async fn provision_account(&self, spec: &AccountSpec) -> Result<u128, MoneyError> {
        let expected = spec.creation();
        match self.transport.create_account(expected).await? {
            CreateAccountStatus::Created => Ok(expected.id),
            CreateAccountStatus::Exists => {
                self.ensure_account(spec).await?;
                Ok(expected.id)
            }
            CreateAccountStatus::IdempotencyConflict => Err(MoneyError::IdempotencyConflict),
            CreateAccountStatus::Rejected(message) => Err(MoneyError::Transport(message)),
        }
    }

    pub async fn balance(&self, spec: &AccountSpec) -> Result<Balance, MoneyError> {
        let account = self.ensure_account(spec).await?;
        let available_minor = if matches!(spec.key, AccountKey::TenantPrepaid { .. }) {
            Some(
                account
                    .credits_posted
                    .checked_sub(account.debits_posted)
                    .and_then(|value| value.checked_sub(account.debits_pending))
                    .ok_or_else(|| {
                        MoneyError::InvariantViolation(
                            "prepaid available balance underflow".to_owned(),
                        )
                    })?,
            )
        } else {
            None
        };
        Ok(Balance {
            account_id: account.id,
            debits_pending: account.debits_pending,
            debits_posted: account.debits_posted,
            credits_pending: account.credits_pending,
            credits_posted: account.credits_posted,
            available_minor,
        })
    }

    /// Post a confirmed collection. Unlike service charges, funding is an
    /// immediate settlement credit; its deterministic event ID makes provider
    /// webhook and reconciliation retries credit once only.
    pub async fn fund(&self, request: &FundingRequest) -> Result<FundingReceipt, MoneyError> {
        request.validate()?;
        let expected = request.transfer();
        if let Some(existing) = self.transport.lookup_transfer(expected.id).await? {
            ensure_same_transfer(&existing, &expected)?;
            return Ok(FundingReceipt {
                transfer_id: existing.id,
                amount_minor: existing.amount,
            });
        }
        let debit = AccountSpec::new(
            AccountKey::ProviderSettlement {
                provider: request.provider.clone(),
            },
            request.currency,
        )?;
        let credit_key = match &request.target {
            FundingTarget::TenantPrepaid { tenant_id } => AccountKey::tenant_prepaid(tenant_id),
            FundingTarget::Suspense => AccountKey::CollectionSuspense {
                provider: request.provider.clone(),
            },
        };
        let credit = AccountSpec::new(credit_key, request.currency)?;
        self.ensure_account(&debit).await?;
        self.ensure_account(&credit).await?;
        match self.transport.create_transfer(expected).await? {
            CreateTransferStatus::Created => Ok(FundingReceipt {
                transfer_id: expected.id,
                amount_minor: expected.amount,
            }),
            CreateTransferStatus::Exists => {
                let existing = self.lookup_transfer(expected.id).await?;
                ensure_same_transfer(&existing, &expected)?;
                Ok(FundingReceipt {
                    transfer_id: existing.id,
                    amount_minor: existing.amount,
                })
            }
            status => Err(map_transfer_status(status)),
        }
    }

    /// Resolve provider suspense without touching provider settlement again.
    /// The suspense account's debit constraint prevents crediting more than was
    /// originally isolated, including across concurrent operator retries.
    pub async fn resolve_suspense(
        &self,
        request: &SuspenseResolutionRequest,
    ) -> Result<FundingReceipt, MoneyError> {
        request.validate()?;
        let expected = request.transfer();
        if let Some(existing) = self.transport.lookup_transfer(expected.id).await? {
            ensure_same_transfer(&existing, &expected)?;
            return Ok(FundingReceipt {
                transfer_id: existing.id,
                amount_minor: existing.amount,
            });
        }
        let suspense = AccountSpec::new(
            AccountKey::CollectionSuspense {
                provider: request.provider.clone(),
            },
            request.currency,
        )?;
        let tenant = AccountSpec::new(
            AccountKey::tenant_prepaid(&request.tenant_id),
            request.currency,
        )?;
        self.ensure_account(&suspense).await?;
        self.ensure_account(&tenant).await?;
        match self.transport.create_transfer(expected).await? {
            CreateTransferStatus::Created => Ok(FundingReceipt {
                transfer_id: expected.id,
                amount_minor: expected.amount,
            }),
            CreateTransferStatus::Exists => {
                let existing = self.lookup_transfer(expected.id).await?;
                ensure_same_transfer(&existing, &expected)?;
                Ok(FundingReceipt {
                    transfer_id: existing.id,
                    amount_minor: existing.amount,
                })
            }
            status => Err(map_transfer_status(status)),
        }
    }

    /// Post provider cost and its payable once, separately from tenant recovery.
    /// Deterministic identity lets an activity retry finish a partially completed
    /// charge/cost pair without duplicating either journal entry.
    pub async fn post_provider_cost(
        &self,
        request: &ProviderCostRequest,
    ) -> Result<FundingReceipt, MoneyError> {
        request.validate()?;
        let expected = request.transfer();
        if let Some(existing) = self.transport.lookup_transfer(expected.id).await? {
            ensure_same_transfer(&existing, &expected)?;
            return Ok(FundingReceipt {
                transfer_id: existing.id,
                amount_minor: existing.amount,
            });
        }
        let debit = AccountSpec::new(
            AccountKey::ProviderCost {
                provider: request.provider.clone(),
            },
            request.currency,
        )?;
        let credit = AccountSpec::new(
            AccountKey::ProviderPayable {
                provider: request.provider.clone(),
            },
            request.currency,
        )?;
        self.ensure_account(&debit).await?;
        self.ensure_account(&credit).await?;
        match self.transport.create_transfer(expected).await? {
            CreateTransferStatus::Created => Ok(FundingReceipt {
                transfer_id: expected.id,
                amount_minor: expected.amount,
            }),
            CreateTransferStatus::Exists => {
                let existing = self.lookup_transfer(expected.id).await?;
                ensure_same_transfer(&existing, &expected)?;
                Ok(FundingReceipt {
                    transfer_id: existing.id,
                    amount_minor: existing.amount,
                })
            }
            status => Err(map_transfer_status(status)),
        }
    }

    /// Post a monthly NGN revaluation of a USD-denominated provider input.
    /// The provider cost account is the counter-account; no exchange-rate or
    /// derived balance is persisted in PostgreSQL.
    pub async fn revalue(
        &self,
        request: &FxRevaluationRequest,
    ) -> Result<FundingReceipt, MoneyError> {
        request.validate()?;
        let expected = request.transfer();
        if let Some(existing) = self.transport.lookup_transfer(expected.id).await? {
            ensure_same_transfer(&existing, &expected)?;
            return Ok(FundingReceipt {
                transfer_id: existing.id,
                amount_minor: existing.amount,
            });
        }
        let counter = AccountSpec::new(
            AccountKey::ProviderCost {
                provider: request.provider.clone(),
            },
            Currency::Ngn,
        )?;
        let fx = AccountSpec::new(
            if matches!(request.direction, FxDirection::Loss) {
                AccountKey::FxLoss
            } else {
                AccountKey::FxGain
            },
            Currency::Ngn,
        )?;
        let (debit, credit) = if matches!(request.direction, FxDirection::Loss) {
            (fx, counter)
        } else {
            (counter, fx)
        };
        self.ensure_account(&debit).await?;
        self.ensure_account(&credit).await?;
        match self.transport.create_transfer(expected).await? {
            CreateTransferStatus::Created => Ok(FundingReceipt {
                transfer_id: expected.id,
                amount_minor: expected.amount,
            }),
            CreateTransferStatus::Exists => {
                let existing = self.lookup_transfer(expected.id).await?;
                ensure_same_transfer(&existing, &expected)?;
                Ok(FundingReceipt {
                    transfer_id: existing.id,
                    amount_minor: existing.amount,
                })
            }
            status => Err(map_transfer_status(status)),
        }
    }

    pub async fn reserve(&self, request: &ChargeRequest) -> Result<Reservation, MoneyError> {
        request.validate()?;
        let expected = request.pending_transfer();
        if let Some(existing) = self.transport.lookup_transfer(expected.id).await? {
            ensure_same_transfer(&existing, &expected)?;
            return self
                .reservation(&request.event_id, &request.tenant_id)
                .await;
        }
        self.ensure_account(&AccountSpec::new(request.debit.clone(), request.currency)?)
            .await?;
        self.ensure_account(&AccountSpec::new(request.credit.clone(), request.currency)?)
            .await?;
        match self.transport.create_transfer(expected).await? {
            CreateTransferStatus::Created => Ok(reservation_from_transfer(
                request,
                expected,
                PostingState::Pending,
            )),
            CreateTransferStatus::Exists => {
                let existing = self.lookup_transfer(expected.id).await?;
                ensure_same_transfer(&existing, &expected)?;
                self.reservation(&request.event_id, &request.tenant_id)
                    .await
            }
            status => Err(map_transfer_status(status)),
        }
    }

    pub async fn post(&self, event_id: &str, tenant_id: &str) -> Result<Reservation, MoneyError> {
        self.settle(event_id, tenant_id, PostingState::Posted).await
    }

    pub async fn void(&self, event_id: &str, tenant_id: &str) -> Result<Reservation, MoneyError> {
        self.settle(event_id, tenant_id, PostingState::Voided).await
    }

    pub async fn reservation(
        &self,
        event_id: &str,
        tenant_id: &str,
    ) -> Result<Reservation, MoneyError> {
        validate_id("event_id", event_id)?;
        validate_id("tenant_id", tenant_id)?;
        let pending = self.lookup_transfer(reserve_id(event_id)).await?;
        ensure_pending_tenant(&pending, tenant_id)?;
        let state = self.current_state(&pending).await?;
        Ok(Reservation {
            event_id: event_id.to_owned(),
            tenant_id: tenant_id.to_owned(),
            transfer_id: pending.id,
            amount_minor: pending.amount,
            state,
        })
    }

    async fn settle(
        &self,
        event_id: &str,
        tenant_id: &str,
        target: PostingState,
    ) -> Result<Reservation, MoneyError> {
        validate_id("event_id", event_id)?;
        validate_id("tenant_id", tenant_id)?;
        if target == PostingState::Pending {
            return Err(MoneyError::InvalidInput(
                "settlement target cannot be pending".to_owned(),
            ));
        }
        let pending = self.lookup_transfer(reserve_id(event_id)).await?;
        ensure_pending_tenant(&pending, tenant_id)?;
        let expected = settlement_transfer(&pending, target);
        if let Some(existing) = self.transport.lookup_transfer(expected.id).await? {
            ensure_same_transfer(&existing, &expected)?;
            return Ok(reservation_from_pending(
                event_id, tenant_id, &pending, target,
            ));
        }
        match self.transport.create_transfer(expected).await? {
            CreateTransferStatus::Created => Ok(reservation_from_pending(
                event_id, tenant_id, &pending, target,
            )),
            CreateTransferStatus::Exists => {
                let existing = self.lookup_transfer(expected.id).await?;
                ensure_same_transfer(&existing, &expected)?;
                Ok(reservation_from_pending(
                    event_id, tenant_id, &pending, target,
                ))
            }
            status => Err(map_transfer_status(status)),
        }
    }

    async fn current_state(&self, pending: &Transfer) -> Result<PostingState, MoneyError> {
        let post = settlement_transfer(pending, PostingState::Posted);
        let void = settlement_transfer(pending, PostingState::Voided);
        let posted = self.transport.lookup_transfer(post.id).await?;
        let voided = self.transport.lookup_transfer(void.id).await?;
        match (posted, voided) {
            (Some(found), None) => {
                ensure_same_transfer(&found, &post)?;
                Ok(PostingState::Posted)
            }
            (None, Some(found)) => {
                ensure_same_transfer(&found, &void)?;
                Ok(PostingState::Voided)
            }
            (None, None) => Ok(PostingState::Pending),
            (Some(_), Some(_)) => Err(MoneyError::InvariantViolation(
                "both post and void settlements exist for one reserve".to_owned(),
            )),
        }
    }

    async fn ensure_account(&self, spec: &AccountSpec) -> Result<Account, MoneyError> {
        let expected = spec.creation();
        let found = self
            .transport
            .lookup_account(expected.id)
            .await?
            .ok_or(MoneyError::UnknownAccount)?;
        if !found.same_creation(&expected) {
            return Err(MoneyError::InvariantViolation(format!(
                "account {} does not match its canonical taxonomy",
                spec.key.canonical()
            )));
        }
        Ok(found)
    }

    async fn lookup_transfer(&self, id: u128) -> Result<Transfer, MoneyError> {
        self.transport
            .lookup_transfer(id)
            .await?
            .ok_or(MoneyError::UnknownReservation)
    }
}

fn settlement_transfer(pending: &Transfer, target: PostingState) -> Transfer {
    let (id, amount, flags) = match target {
        PostingState::Posted => (
            settlement_id_from_reserve(pending.id, "post"),
            u128::MAX,
            TRANSFER_POST_PENDING,
        ),
        PostingState::Voided => (
            settlement_id_from_reserve(pending.id, "void"),
            0,
            TRANSFER_VOID_PENDING,
        ),
        PostingState::Pending => unreachable!("pending is not a settlement"),
    };
    Transfer {
        id,
        pending_id: pending.id,
        amount,
        user_data_128: pending.user_data_128,
        user_data_64: pending.user_data_64,
        user_data_32: pending.user_data_32,
        ledger: pending.ledger,
        code: pending.code,
        flags,
        ..Transfer::default()
    }
}

fn reservation_from_transfer(
    request: &ChargeRequest,
    transfer: Transfer,
    state: PostingState,
) -> Reservation {
    reservation_from_pending(&request.event_id, &request.tenant_id, &transfer, state)
}

fn reservation_from_pending(
    event_id: &str,
    tenant_id: &str,
    pending: &Transfer,
    state: PostingState,
) -> Reservation {
    Reservation {
        event_id: event_id.to_owned(),
        tenant_id: tenant_id.to_owned(),
        transfer_id: pending.id,
        amount_minor: pending.amount,
        state,
    }
}

fn ensure_pending_tenant(pending: &Transfer, tenant_id: &str) -> Result<(), MoneyError> {
    if pending.flags != TRANSFER_PENDING || pending.pending_id != 0 {
        return Err(MoneyError::InvariantViolation(
            "reserve ID does not reference a pending transfer".to_owned(),
        ));
    }
    if pending.user_data_128 != tenant_fingerprint(tenant_id) {
        return Err(MoneyError::IdempotencyConflict);
    }
    Ok(())
}

fn ensure_same_transfer(found: &Transfer, expected: &Transfer) -> Result<(), MoneyError> {
    if found.same_creation(expected) {
        Ok(())
    } else {
        Err(MoneyError::IdempotencyConflict)
    }
}

fn map_transfer_status(status: CreateTransferStatus) -> MoneyError {
    match status {
        CreateTransferStatus::IdempotencyConflict => MoneyError::IdempotencyConflict,
        CreateTransferStatus::UnknownAccount => MoneyError::UnknownAccount,
        CreateTransferStatus::UnknownReservation => MoneyError::UnknownReservation,
        CreateTransferStatus::AlreadySettled => MoneyError::AlreadySettled,
        CreateTransferStatus::Expired => MoneyError::ReservationExpired,
        CreateTransferStatus::InsufficientFunds => MoneyError::InsufficientFunds,
        CreateTransferStatus::Rejected(message) => MoneyError::Transport(message),
        CreateTransferStatus::Created | CreateTransferStatus::Exists => {
            MoneyError::InvariantViolation(
                "successful create status reached the rejection mapper".to_owned(),
            )
        }
    }
}

pub fn reserve_id(event_id: &str) -> u128 {
    deterministic_id(&format!("volumeos:transfer:v1:reserve:{event_id}"))
}

pub fn funding_id(event_id: &str) -> u128 {
    deterministic_id(&format!("volumeos:transfer:v1:funding:{event_id}"))
}

pub fn provider_cost_id(event_id: &str) -> u128 {
    deterministic_id(&format!("volumeos:transfer:v1:provider-cost:{event_id}"))
}

fn settlement_id_from_reserve(reserve_id: u128, operation: &str) -> u128 {
    deterministic_id(&format!(
        "volumeos:transfer:v1:{operation}:{reserve_id:032x}"
    ))
}

pub fn tenant_fingerprint(tenant_id: &str) -> u128 {
    deterministic_id(&format!("volumeos:tenant:v1:{tenant_id}"))
}

fn hash64(value: &str) -> u64 {
    let hash = blake3::hash(value.as_bytes());
    u64::from_le_bytes(hash.as_bytes()[..8].try_into().expect("eight hash bytes"))
}

fn deterministic_id(value: &str) -> u128 {
    let hash = blake3::hash(value.as_bytes());
    let id = u128::from_le_bytes(
        hash.as_bytes()[..16]
            .try_into()
            .expect("sixteen hash bytes"),
    );
    match id {
        0 | u128::MAX => 1,
        _ => id,
    }
}

fn validate_offer(offer: &str) -> Result<(), MoneyError> {
    if matches!(offer, "01" | "02" | "03" | "04" | "05" | "06" | "07" | "08") {
        Ok(())
    } else {
        Err(MoneyError::InvalidInput(
            "offer must be one of 01 through 08".to_owned(),
        ))
    }
}

fn validate_id(name: &str, value: &str) -> Result<(), MoneyError> {
    let mut chars = value.chars();
    let valid_first = chars.next().is_some_and(|c| c.is_ascii_alphanumeric());
    let valid_rest = chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | ':' | '-'));
    if valid_first && valid_rest && value.len() <= 128 {
        Ok(())
    } else {
        Err(MoneyError::InvalidInput(format!(
            "{name} must be 1-128 safe ASCII identifier characters"
        )))
    }
}
