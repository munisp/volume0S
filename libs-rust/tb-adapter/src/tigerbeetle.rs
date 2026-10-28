//! Live transport backed by TigerBeetle's official Rust client.
//!
//! Construction fails closed when cluster configuration is absent. No local or
//! relational fallback is provided for financial balances.

use tigerbeetle as tb;

use crate::ledger::{
    Account, AccountQuery, CreateAccountStatus, CreateTransferStatus, Transfer, Transport,
    TransportError, TransportFuture,
};

pub struct TigerBeetleTransport {
    client: tb::Client,
}

impl TigerBeetleTransport {
    pub fn connect(cluster_id: u128, addresses: &str) -> Result<Self, TransportError> {
        if addresses.trim().is_empty() {
            return Err(TransportError::new(
                "at least one TigerBeetle replica address is required",
            ));
        }
        let client = tb::Client::new(cluster_id, addresses)
            .map_err(|error| TransportError::new(format!("client initialization: {error}")))?;
        Ok(Self { client })
    }

    pub fn from_env() -> Result<Self, TransportError> {
        let cluster = std::env::var("TIGERBEETLE_CLUSTER_ID")
            .map_err(|_| TransportError::new("TIGERBEETLE_CLUSTER_ID is not configured"))?;
        let cluster_id = cluster.parse::<u128>().map_err(|_| {
            TransportError::new("TIGERBEETLE_CLUSTER_ID must be an unsigned 128-bit integer")
        })?;
        let addresses = std::env::var("TIGERBEETLE_ADDRESSES")
            .map_err(|_| TransportError::new("TIGERBEETLE_ADDRESSES is not configured"))?;
        Self::connect(cluster_id, &addresses)
    }
}

impl Transport for TigerBeetleTransport {
    fn create_account(
        &self,
        account: Account,
    ) -> TransportFuture<'_, Result<CreateAccountStatus, TransportError>> {
        Box::pin(async move {
            let event = account_to_tb(account);
            let results = self
                .client
                .create_accounts(&[event])
                .await
                .map_err(|error| TransportError::new(error.to_string()))?;
            if results.len() != 1 {
                return Err(TransportError::new(
                    "create_accounts returned an invalid result count",
                ));
            }
            Ok(map_account_status(results[0].status))
        })
    }

    fn lookup_account(
        &self,
        id: u128,
    ) -> TransportFuture<'_, Result<Option<Account>, TransportError>> {
        Box::pin(async move {
            let mut results = self
                .client
                .lookup_accounts(&[id])
                .await
                .map_err(|error| TransportError::new(error.to_string()))?;
            if results.len() > 1 {
                return Err(TransportError::new(
                    "lookup_accounts returned more than one account",
                ));
            }
            Ok(results.pop().map(account_from_tb))
        })
    }

    fn query_accounts(
        &self,
        query: AccountQuery,
    ) -> TransportFuture<'_, Result<Vec<Account>, TransportError>> {
        Box::pin(async move {
            if query.limit == 0 || query.limit > 8189 {
                return Err(TransportError::new(
                    "account query limit must be between 1 and 8189",
                ));
            }
            let accounts = self
                .client
                .query_accounts(tb::QueryFilter {
                    user_data_128: query.user_data_128,
                    ledger: query.ledger,
                    code: query.code,
                    timestamp_min: query.timestamp_min,
                    limit: query.limit,
                    ..tb::QueryFilter::default()
                })
                .await
                .map_err(|error| TransportError::new(error.to_string()))?;
            Ok(accounts.into_iter().map(account_from_tb).collect())
        })
    }

    fn create_transfer(
        &self,
        transfer: Transfer,
    ) -> TransportFuture<'_, Result<CreateTransferStatus, TransportError>> {
        Box::pin(async move {
            let event = transfer_to_tb(transfer);
            let results = self
                .client
                .create_transfers(&[event])
                .await
                .map_err(|error| TransportError::new(error.to_string()))?;
            if results.len() != 1 {
                return Err(TransportError::new(
                    "create_transfers returned an invalid result count",
                ));
            }
            Ok(map_transfer_status(results[0].status))
        })
    }

    fn lookup_transfer(
        &self,
        id: u128,
    ) -> TransportFuture<'_, Result<Option<Transfer>, TransportError>> {
        Box::pin(async move {
            let mut results = self
                .client
                .lookup_transfers(&[id])
                .await
                .map_err(|error| TransportError::new(error.to_string()))?;
            if results.len() > 1 {
                return Err(TransportError::new(
                    "lookup_transfers returned more than one transfer",
                ));
            }
            Ok(results.pop().map(transfer_from_tb))
        })
    }
}

fn account_to_tb(account: Account) -> tb::Account {
    tb::Account {
        id: account.id,
        user_data_128: account.user_data_128,
        user_data_64: account.user_data_64,
        user_data_32: account.user_data_32,
        ledger: account.ledger,
        code: account.code,
        flags: tb::AccountFlags(account.flags),
        ..tb::Account::default()
    }
}

fn account_from_tb(account: tb::Account) -> Account {
    Account {
        id: account.id,
        debits_pending: account.debits_pending,
        debits_posted: account.debits_posted,
        credits_pending: account.credits_pending,
        credits_posted: account.credits_posted,
        user_data_128: account.user_data_128,
        user_data_64: account.user_data_64,
        user_data_32: account.user_data_32,
        ledger: account.ledger,
        code: account.code,
        flags: account.flags.0,
        timestamp: account.timestamp,
    }
}

fn transfer_to_tb(transfer: Transfer) -> tb::Transfer {
    tb::Transfer {
        id: transfer.id,
        debit_account_id: transfer.debit_account_id,
        credit_account_id: transfer.credit_account_id,
        amount: transfer.amount,
        pending_id: transfer.pending_id,
        user_data_128: transfer.user_data_128,
        user_data_64: transfer.user_data_64,
        user_data_32: transfer.user_data_32,
        timeout: transfer.timeout,
        ledger: transfer.ledger,
        code: transfer.code,
        flags: tb::TransferFlags(transfer.flags),
        ..tb::Transfer::default()
    }
}

fn transfer_from_tb(transfer: tb::Transfer) -> Transfer {
    Transfer {
        id: transfer.id,
        debit_account_id: transfer.debit_account_id,
        credit_account_id: transfer.credit_account_id,
        amount: transfer.amount,
        pending_id: transfer.pending_id,
        user_data_128: transfer.user_data_128,
        user_data_64: transfer.user_data_64,
        user_data_32: transfer.user_data_32,
        timeout: transfer.timeout,
        ledger: transfer.ledger,
        code: transfer.code,
        flags: transfer.flags.0,
        timestamp: transfer.timestamp,
    }
}

fn map_account_status(status: tb::CreateAccountStatus) -> CreateAccountStatus {
    use tb::CreateAccountStatus as S;
    match status {
        S::Created => CreateAccountStatus::Created,
        S::Exists => CreateAccountStatus::Exists,
        S::ExistsWithDifferentFlags
        | S::ExistsWithDifferentUserData128
        | S::ExistsWithDifferentUserData64
        | S::ExistsWithDifferentUserData32
        | S::ExistsWithDifferentLedger
        | S::ExistsWithDifferentCode => CreateAccountStatus::IdempotencyConflict,
        other => CreateAccountStatus::Rejected(format!("TigerBeetle rejected account: {other}")),
    }
}

fn map_transfer_status(status: tb::CreateTransferStatus) -> CreateTransferStatus {
    use tb::CreateTransferStatus as S;
    match status {
        S::Created => CreateTransferStatus::Created,
        S::Exists => CreateTransferStatus::Exists,
        S::ExistsWithDifferentFlags
        | S::ExistsWithDifferentPendingId
        | S::ExistsWithDifferentTimeout
        | S::ExistsWithDifferentDebitAccountId
        | S::ExistsWithDifferentCreditAccountId
        | S::ExistsWithDifferentAmount
        | S::ExistsWithDifferentUserData128
        | S::ExistsWithDifferentUserData64
        | S::ExistsWithDifferentUserData32
        | S::ExistsWithDifferentLedger
        | S::ExistsWithDifferentCode
        | S::IdAlreadyFailed => CreateTransferStatus::IdempotencyConflict,
        S::DebitAccountNotFound | S::CreditAccountNotFound => CreateTransferStatus::UnknownAccount,
        S::PendingTransferNotFound => CreateTransferStatus::UnknownReservation,
        S::PendingTransferNotPending
        | S::PendingTransferAlreadyPosted
        | S::PendingTransferAlreadyVoided => CreateTransferStatus::AlreadySettled,
        S::PendingTransferExpired => CreateTransferStatus::Expired,
        S::ExceedsCredits | S::ExceedsDebits => CreateTransferStatus::InsufficientFunds,
        other => CreateTransferStatus::Rejected(format!("TigerBeetle rejected transfer: {other}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_replica_configuration_fails_closed() {
        assert!(TigerBeetleTransport::connect(0, "  ").is_err());
    }

    #[test]
    fn financial_rejections_are_not_reported_as_success() {
        assert_eq!(
            map_transfer_status(tb::CreateTransferStatus::PendingTransferExpired),
            CreateTransferStatus::Expired
        );
        assert_eq!(
            map_transfer_status(tb::CreateTransferStatus::ExceedsCredits),
            CreateTransferStatus::InsufficientFunds
        );
        assert_eq!(
            map_transfer_status(tb::CreateTransferStatus::ExistsWithDifferentAmount),
            CreateTransferStatus::IdempotencyConflict
        );
    }

    #[test]
    fn official_types_round_trip_without_narrowing_money_fields() {
        let account = Account {
            id: u128::MAX - 1,
            debits_pending: u128::from(u64::MAX) + 1,
            debits_posted: 2,
            credits_pending: 3,
            credits_posted: 4,
            user_data_128: 5,
            user_data_64: 6,
            user_data_32: 7,
            ledger: 566,
            code: 100,
            flags: 8,
            timestamp: 9,
        };
        let stored_account = tb::Account {
            debits_pending: account.debits_pending,
            debits_posted: account.debits_posted,
            credits_pending: account.credits_pending,
            credits_posted: account.credits_posted,
            timestamp: account.timestamp,
            ..account_to_tb(account)
        };
        assert_eq!(account_from_tb(stored_account), account);
        let transfer = Transfer {
            id: u128::MAX - 2,
            debit_account_id: 1,
            credit_account_id: 2,
            amount: u128::from(u64::MAX) + 50,
            pending_id: 3,
            user_data_128: 4,
            user_data_64: 5,
            user_data_32: 6,
            timeout: 7,
            ledger: 566,
            code: 120,
            flags: 2,
            timestamp: 8,
        };
        let stored_transfer = tb::Transfer {
            timestamp: transfer.timestamp,
            ..transfer_to_tb(transfer)
        };
        assert_eq!(transfer_from_tb(stored_transfer), transfer);
    }

    #[test]
    fn configuration_and_all_financial_status_classes_are_explicit() {
        std::env::remove_var("TIGERBEETLE_CLUSTER_ID");
        std::env::remove_var("TIGERBEETLE_ADDRESSES");
        assert!(TigerBeetleTransport::from_env().is_err());
        std::env::set_var("TIGERBEETLE_CLUSTER_ID", "0");
        assert!(TigerBeetleTransport::from_env().is_err());
        std::env::set_var("TIGERBEETLE_ADDRESSES", "3000");
        assert!(TigerBeetleTransport::from_env().is_ok());
        std::env::set_var("TIGERBEETLE_CLUSTER_ID", "invalid");
        assert!(TigerBeetleTransport::from_env().is_err());
        std::env::remove_var("TIGERBEETLE_CLUSTER_ID");
        std::env::remove_var("TIGERBEETLE_ADDRESSES");

        for status in [
            tb::CreateAccountStatus::ExistsWithDifferentFlags,
            tb::CreateAccountStatus::ExistsWithDifferentUserData128,
            tb::CreateAccountStatus::ExistsWithDifferentUserData64,
            tb::CreateAccountStatus::ExistsWithDifferentUserData32,
            tb::CreateAccountStatus::ExistsWithDifferentLedger,
            tb::CreateAccountStatus::ExistsWithDifferentCode,
        ] {
            assert_eq!(
                map_account_status(status),
                CreateAccountStatus::IdempotencyConflict
            );
        }
        assert_eq!(
            map_account_status(tb::CreateAccountStatus::Created),
            CreateAccountStatus::Created
        );
        assert_eq!(
            map_account_status(tb::CreateAccountStatus::Exists),
            CreateAccountStatus::Exists
        );
        assert!(matches!(
            map_account_status(tb::CreateAccountStatus::ReservedField),
            CreateAccountStatus::Rejected(_)
        ));
        for status in [
            tb::CreateTransferStatus::ExistsWithDifferentFlags,
            tb::CreateTransferStatus::ExistsWithDifferentPendingId,
            tb::CreateTransferStatus::ExistsWithDifferentTimeout,
            tb::CreateTransferStatus::ExistsWithDifferentDebitAccountId,
            tb::CreateTransferStatus::ExistsWithDifferentCreditAccountId,
            tb::CreateTransferStatus::ExistsWithDifferentAmount,
            tb::CreateTransferStatus::ExistsWithDifferentUserData128,
            tb::CreateTransferStatus::ExistsWithDifferentUserData64,
            tb::CreateTransferStatus::ExistsWithDifferentUserData32,
            tb::CreateTransferStatus::ExistsWithDifferentLedger,
            tb::CreateTransferStatus::ExistsWithDifferentCode,
            tb::CreateTransferStatus::IdAlreadyFailed,
        ] {
            assert_eq!(
                map_transfer_status(status),
                CreateTransferStatus::IdempotencyConflict
            );
        }
        assert_eq!(
            map_transfer_status(tb::CreateTransferStatus::Created),
            CreateTransferStatus::Created
        );
        assert_eq!(
            map_transfer_status(tb::CreateTransferStatus::Exists),
            CreateTransferStatus::Exists
        );
        assert_eq!(
            map_transfer_status(tb::CreateTransferStatus::DebitAccountNotFound),
            CreateTransferStatus::UnknownAccount
        );
        assert_eq!(
            map_transfer_status(tb::CreateTransferStatus::CreditAccountNotFound),
            CreateTransferStatus::UnknownAccount
        );
        assert_eq!(
            map_transfer_status(tb::CreateTransferStatus::PendingTransferNotFound),
            CreateTransferStatus::UnknownReservation
        );
        assert_eq!(
            map_transfer_status(tb::CreateTransferStatus::PendingTransferNotPending),
            CreateTransferStatus::AlreadySettled
        );
        assert_eq!(
            map_transfer_status(tb::CreateTransferStatus::PendingTransferAlreadyPosted),
            CreateTransferStatus::AlreadySettled
        );
        assert_eq!(
            map_transfer_status(tb::CreateTransferStatus::PendingTransferAlreadyVoided),
            CreateTransferStatus::AlreadySettled
        );
        assert_eq!(
            map_transfer_status(tb::CreateTransferStatus::ExceedsDebits),
            CreateTransferStatus::InsufficientFunds
        );
        assert!(matches!(
            map_transfer_status(tb::CreateTransferStatus::ReservedFlag),
            CreateTransferStatus::Rejected(_)
        ));
    }
}
