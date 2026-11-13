//! Live transport backed by a TigerBeetle Rust client.
//!
//! Construction fails closed when cluster configuration is absent. No local or
//! relational fallback is provided for financial balances.
//!
//! The client is `tigerbeetle-unofficial` built for TigerBeetle 0.16.66, the release the
//! cluster runs: a TigerBeetle cluster refuses clients newer than itself, and the official
//! Rust client starts at 0.17. Replica addresses are IP:port (the 0.16 client does not
//! resolve host names). Accounts and transfers are built from zeroed raw records so that no
//! builder assertion can panic on an identifier.

use bytemuck::Zeroable;
use tigerbeetle_unofficial as tb;
use tb::error::{
    CreateAccountErrorKind, CreateAccountsError, CreateTransferErrorKind, CreateTransfersError,
};

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
        Self::connect(cluster_id, &resolve_addresses(&addresses)?)
    }
}

/// The client only takes IP:port, but the shared cluster is addressed by its StatefulSet hostnames
/// (`tigerbeetle-0.tigerbeetle-headless...:3000`), as every other app on it does. Resolve each name once at start-up,
/// keeping the replica order (it must match the cluster's replica numbering). IPs pass through unchanged.
pub fn resolve_addresses(addresses: &str) -> Result<String, TransportError> {
    use std::net::{IpAddr, ToSocketAddrs};
    let mut resolved = Vec::new();
    for entry in addresses.split(',').map(str::trim).filter(|entry| !entry.is_empty()) {
        // A bare port means this machine, which the client understands itself.
        if entry.parse::<u16>().is_ok() {
            resolved.push(entry.to_owned());
            continue;
        }
        let (host, port) = entry
            .rsplit_once(':')
            .ok_or_else(|| TransportError::new(format!("replica address {entry} needs a port")))?;
        if host.parse::<IpAddr>().is_ok() {
            resolved.push(entry.to_owned());
            continue;
        }
        let ip = (host, port.parse::<u16>().map_err(|_| TransportError::new(format!("bad port in {entry}")))?)
            .to_socket_addrs()
            .map_err(|error| TransportError::new(format!("cannot resolve {host}: {error}")))?
            .find(|address| address.is_ipv4())
            .ok_or_else(|| TransportError::new(format!("{host} has no IPv4 address")))?;
        resolved.push(ip.to_string());
    }
    Ok(resolved.join(","))
}

#[cfg(test)]
mod resolve_tests {
    use super::resolve_addresses;

    #[test]
    fn names_become_ips_in_order_and_ips_pass_through() {
        assert_eq!(resolve_addresses("10.0.0.1:3000, 10.0.0.2:3001").unwrap(), "10.0.0.1:3000,10.0.0.2:3001");
        assert_eq!(resolve_addresses("localhost:3000,10.0.0.9:3000").unwrap(), "127.0.0.1:3000,10.0.0.9:3000");
        assert_eq!(resolve_addresses("3000").unwrap(), "3000");
        assert!(resolve_addresses("no-port-here").is_err());
        assert!(resolve_addresses("name.invalid:3000").is_err());
    }
}

impl Transport for TigerBeetleTransport {
    fn create_account(
        &self,
        account: Account,
    ) -> TransportFuture<'_, Result<CreateAccountStatus, TransportError>> {
        Box::pin(async move {
            let event = account_to_tb(account);
            match self.client.create_accounts(vec![event]).await {
                Ok(()) => Ok(CreateAccountStatus::Created),
                Err(CreateAccountsError::Api(errors)) => match errors.as_slice() {
                    [only] => Ok(map_account_status(only.kind())),
                    _ => Err(TransportError::new(
                        "create_accounts returned an invalid result count",
                    )),
                },
                Err(error) => Err(TransportError::new(error.to_string())),
            }
        })
    }

    fn lookup_account(
        &self,
        id: u128,
    ) -> TransportFuture<'_, Result<Option<Account>, TransportError>> {
        Box::pin(async move {
            let mut results = self
                .client
                .lookup_accounts(vec![id])
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
            let mut filter = tb::QueryFilter::new(query.limit);
            let raw = filter.as_raw_mut();
            raw.user_data_128 = query.user_data_128;
            raw.ledger = query.ledger;
            raw.code = query.code;
            raw.timestamp_min = query.timestamp_min;
            let accounts = self
                .client
                .query_accounts(Box::new(filter))
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
            match self.client.create_transfers(vec![event]).await {
                Ok(()) => Ok(CreateTransferStatus::Created),
                Err(CreateTransfersError::Api(errors)) => match errors.as_slice() {
                    [only] => Ok(map_transfer_status(only.kind())),
                    _ => Err(TransportError::new(
                        "create_transfers returned an invalid result count",
                    )),
                },
                Err(error) => Err(TransportError::new(error.to_string())),
            }
        })
    }

    fn lookup_transfer(
        &self,
        id: u128,
    ) -> TransportFuture<'_, Result<Option<Transfer>, TransportError>> {
        Box::pin(async move {
            let mut results = self
                .client
                .lookup_transfers(vec![id])
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
    let mut raw = tb::account::Raw::zeroed();
    raw.id = account.id;
    raw.user_data_128 = account.user_data_128;
    raw.user_data_64 = account.user_data_64;
    raw.user_data_32 = account.user_data_32;
    raw.ledger = account.ledger;
    raw.code = account.code;
    raw.flags = account.flags;
    tb::Account::from_raw(raw)
}

fn account_from_tb(account: tb::Account) -> Account {
    let raw = account.as_raw();
    Account {
        id: raw.id,
        debits_pending: raw.debits_pending,
        debits_posted: raw.debits_posted,
        credits_pending: raw.credits_pending,
        credits_posted: raw.credits_posted,
        user_data_128: raw.user_data_128,
        user_data_64: raw.user_data_64,
        user_data_32: raw.user_data_32,
        ledger: raw.ledger,
        code: raw.code,
        flags: raw.flags,
        timestamp: raw.timestamp,
    }
}

fn transfer_to_tb(transfer: Transfer) -> tb::Transfer {
    let mut raw = tb::transfer::Raw::zeroed();
    raw.id = transfer.id;
    raw.debit_account_id = transfer.debit_account_id;
    raw.credit_account_id = transfer.credit_account_id;
    raw.amount = transfer.amount;
    raw.pending_id = transfer.pending_id;
    raw.user_data_128 = transfer.user_data_128;
    raw.user_data_64 = transfer.user_data_64;
    raw.user_data_32 = transfer.user_data_32;
    raw.timeout = transfer.timeout;
    raw.ledger = transfer.ledger;
    raw.code = transfer.code;
    raw.flags = transfer.flags;
    tb::Transfer::from_raw(raw)
}

fn transfer_from_tb(transfer: tb::Transfer) -> Transfer {
    let raw = transfer.as_raw();
    Transfer {
        id: raw.id,
        debit_account_id: raw.debit_account_id,
        credit_account_id: raw.credit_account_id,
        amount: raw.amount,
        pending_id: raw.pending_id,
        user_data_128: raw.user_data_128,
        user_data_64: raw.user_data_64,
        user_data_32: raw.user_data_32,
        timeout: raw.timeout,
        ledger: raw.ledger,
        code: raw.code,
        flags: raw.flags,
        timestamp: raw.timestamp,
    }
}

fn map_account_status(status: CreateAccountErrorKind) -> CreateAccountStatus {
    use CreateAccountErrorKind as S;
    match status {
        S::Exists => CreateAccountStatus::Exists,
        S::ExistsWithDifferentFlags
        | S::ExistsWithDifferentUserData128
        | S::ExistsWithDifferentUserData64
        | S::ExistsWithDifferentUserData32
        | S::ExistsWithDifferentLedger
        | S::ExistsWithDifferentCode => CreateAccountStatus::IdempotencyConflict,
        other => CreateAccountStatus::Rejected(format!("TigerBeetle rejected account: {other:?}")),
    }
}

fn map_transfer_status(status: CreateTransferErrorKind) -> CreateTransferStatus {
    use CreateTransferErrorKind as S;
    match status {
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
        | S::ExistsWithDifferentCode => CreateTransferStatus::IdempotencyConflict,
        S::IdAlreadyFailed => CreateTransferStatus::IdAlreadyFailed,
        S::DebitAccountNotFound | S::CreditAccountNotFound => CreateTransferStatus::UnknownAccount,
        S::PendingTransferNotFound => CreateTransferStatus::UnknownReservation,
        S::PendingTransferNotPending
        | S::PendingTransferAlreadyPosted
        | S::PendingTransferAlreadyVoided => CreateTransferStatus::AlreadySettled,
        S::PendingTransferExpired => CreateTransferStatus::Expired,
        S::ExceedsCredits | S::ExceedsDebits => CreateTransferStatus::InsufficientFunds,
        other => CreateTransferStatus::Rejected(format!("TigerBeetle rejected transfer: {other:?}")),
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
            map_transfer_status(CreateTransferErrorKind::PendingTransferExpired),
            CreateTransferStatus::Expired
        );
        assert_eq!(
            map_transfer_status(CreateTransferErrorKind::ExceedsCredits),
            CreateTransferStatus::InsufficientFunds
        );
        assert_eq!(
            map_transfer_status(CreateTransferErrorKind::ExistsWithDifferentAmount),
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
        let mut stored_account = account_to_tb(account.clone());
        let raw = stored_account.as_raw_mut();
        raw.debits_pending = account.debits_pending;
        raw.debits_posted = account.debits_posted;
        raw.credits_pending = account.credits_pending;
        raw.credits_posted = account.credits_posted;
        raw.timestamp = account.timestamp;
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
        let mut stored_transfer = transfer_to_tb(transfer.clone());
        stored_transfer.as_raw_mut().timestamp = transfer.timestamp;
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
            CreateAccountErrorKind::ExistsWithDifferentFlags,
            CreateAccountErrorKind::ExistsWithDifferentUserData128,
            CreateAccountErrorKind::ExistsWithDifferentUserData64,
            CreateAccountErrorKind::ExistsWithDifferentUserData32,
            CreateAccountErrorKind::ExistsWithDifferentLedger,
            CreateAccountErrorKind::ExistsWithDifferentCode,
        ] {
            assert_eq!(
                map_account_status(status),
                CreateAccountStatus::IdempotencyConflict
            );
        }
        assert_eq!(
            map_account_status(CreateAccountErrorKind::Exists),
            CreateAccountStatus::Exists
        );
        assert!(matches!(
            map_account_status(CreateAccountErrorKind::ReservedField),
            CreateAccountStatus::Rejected(_)
        ));
        for status in [
            CreateTransferErrorKind::ExistsWithDifferentFlags,
            CreateTransferErrorKind::ExistsWithDifferentPendingId,
            CreateTransferErrorKind::ExistsWithDifferentTimeout,
            CreateTransferErrorKind::ExistsWithDifferentDebitAccountId,
            CreateTransferErrorKind::ExistsWithDifferentCreditAccountId,
            CreateTransferErrorKind::ExistsWithDifferentAmount,
            CreateTransferErrorKind::ExistsWithDifferentUserData128,
            CreateTransferErrorKind::ExistsWithDifferentUserData64,
            CreateTransferErrorKind::ExistsWithDifferentUserData32,
            CreateTransferErrorKind::ExistsWithDifferentLedger,
            CreateTransferErrorKind::ExistsWithDifferentCode,
        ] {
            assert_eq!(
                map_transfer_status(status),
                CreateTransferStatus::IdempotencyConflict
            );
        }
        // A burnt id is its own status: the ledger retries a reservation under the next id.
        assert_eq!(
            map_transfer_status(CreateTransferErrorKind::IdAlreadyFailed),
            CreateTransferStatus::IdAlreadyFailed
        );
        assert_eq!(
            map_transfer_status(CreateTransferErrorKind::Exists),
            CreateTransferStatus::Exists
        );
        assert_eq!(
            map_transfer_status(CreateTransferErrorKind::DebitAccountNotFound),
            CreateTransferStatus::UnknownAccount
        );
        assert_eq!(
            map_transfer_status(CreateTransferErrorKind::CreditAccountNotFound),
            CreateTransferStatus::UnknownAccount
        );
        assert_eq!(
            map_transfer_status(CreateTransferErrorKind::PendingTransferNotFound),
            CreateTransferStatus::UnknownReservation
        );
        assert_eq!(
            map_transfer_status(CreateTransferErrorKind::PendingTransferNotPending),
            CreateTransferStatus::AlreadySettled
        );
        assert_eq!(
            map_transfer_status(CreateTransferErrorKind::PendingTransferAlreadyPosted),
            CreateTransferStatus::AlreadySettled
        );
        assert_eq!(
            map_transfer_status(CreateTransferErrorKind::PendingTransferAlreadyVoided),
            CreateTransferStatus::AlreadySettled
        );
        assert_eq!(
            map_transfer_status(CreateTransferErrorKind::ExceedsDebits),
            CreateTransferStatus::InsufficientFunds
        );
        assert!(matches!(
            map_transfer_status(CreateTransferErrorKind::ReservedFlag),
            CreateTransferStatus::Rejected(_)
        ));
    }
}
