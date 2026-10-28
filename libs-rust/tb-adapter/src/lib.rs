//! VolumeOS money semantics (BIL-1/BIL-2/P2/P7).
//!
//! `ledger` contains the production account taxonomy and idempotent two-phase
//! operations. `tigerbeetle` connects those operations to the official live
//! client. `ReservationBook` remains only as the original deterministic contract
//! model for compatibility tests; it is not a production financial store.

pub mod ledger;
pub mod tigerbeetle;

use std::collections::HashMap;
use std::fmt;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReservationState {
    Pending,
    Posted,
    Voided,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Reservation {
    pub event_id: String,
    pub tenant_id: String,
    pub amount_minor: u128,
    pub state: ReservationState,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LedgerError {
    InvalidInput,
    IdempotencyConflict,
    UnknownReservation,
    AlreadySettled,
}

impl fmt::Display for LedgerError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{self:?}")
    }
}
impl std::error::Error for LedgerError {}

#[derive(Default)]
pub struct ReservationBook {
    reservations: HashMap<String, Reservation>,
}

impl ReservationBook {
    pub fn reserve(
        &mut self,
        event_id: &str,
        tenant_id: &str,
        amount_minor: u128,
    ) -> Result<Reservation, LedgerError> {
        if event_id.trim().is_empty() || tenant_id.trim().is_empty() || amount_minor == 0 {
            return Err(LedgerError::InvalidInput);
        }
        if let Some(existing) = self.reservations.get(event_id) {
            if existing.tenant_id == tenant_id && existing.amount_minor == amount_minor {
                return Ok(existing.clone());
            }
            return Err(LedgerError::IdempotencyConflict);
        }
        let reservation = Reservation {
            event_id: event_id.to_owned(),
            tenant_id: tenant_id.to_owned(),
            amount_minor,
            state: ReservationState::Pending,
        };
        self.reservations
            .insert(event_id.to_owned(), reservation.clone());
        Ok(reservation)
    }

    pub fn post(&mut self, event_id: &str) -> Result<Reservation, LedgerError> {
        self.settle(event_id, ReservationState::Posted)
    }
    pub fn void(&mut self, event_id: &str) -> Result<Reservation, LedgerError> {
        self.settle(event_id, ReservationState::Voided)
    }

    fn settle(
        &mut self,
        event_id: &str,
        target: ReservationState,
    ) -> Result<Reservation, LedgerError> {
        let reservation = self
            .reservations
            .get_mut(event_id)
            .ok_or(LedgerError::UnknownReservation)?;
        if reservation.state == target {
            return Ok(reservation.clone());
        }
        if reservation.state != ReservationState::Pending {
            return Err(LedgerError::AlreadySettled);
        }
        reservation.state = target;
        Ok(reservation.clone())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reserve_post_is_idempotent_under_replay() {
        let mut book = ReservationBook::default();
        let first = book.reserve("evt-1", "tenant-a", 5_500_000).unwrap();
        assert_eq!(book.reserve("evt-1", "tenant-a", 5_500_000).unwrap(), first);
        assert_eq!(book.post("evt-1").unwrap().state, ReservationState::Posted);
        assert_eq!(book.post("evt-1").unwrap().state, ReservationState::Posted);
    }

    #[test]
    fn failed_campaign_voids_without_posting() {
        let mut book = ReservationBook::default();
        book.reserve("evt-2", "tenant-a", 100).unwrap();
        assert_eq!(book.void("evt-2").unwrap().state, ReservationState::Voided);
        assert_eq!(book.post("evt-2"), Err(LedgerError::AlreadySettled));
    }

    #[test]
    fn replay_cannot_change_amount_or_tenant() {
        let mut book = ReservationBook::default();
        book.reserve("evt-3", "tenant-a", 100).unwrap();
        assert_eq!(
            book.reserve("evt-3", "tenant-a", 101),
            Err(LedgerError::IdempotencyConflict)
        );
        assert_eq!(
            book.reserve("evt-3", "tenant-b", 100),
            Err(LedgerError::IdempotencyConflict)
        );
    }

    #[test]
    fn zero_value_money_is_rejected() {
        assert_eq!(
            ReservationBook::default().reserve("evt", "tenant", 0),
            Err(LedgerError::InvalidInput)
        );
    }
}
