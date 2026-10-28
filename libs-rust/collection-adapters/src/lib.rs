//! Verified collection ingress and confirmed-settlement posting.
//!
//! A valid webhook signature authenticates the delivery, not the payment. The
//! provider API must independently confirm status, reference, currency, and
//! amount before `CollectionProcessor` posts to TigerBeetle.

pub mod processor;
pub mod providers;
