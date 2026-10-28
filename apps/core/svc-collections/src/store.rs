use std::fmt;

use async_trait::async_trait;
use serde_json::Value;
use sqlx::{PgPool, Postgres, Row, Transaction};
use volumeos_collection_adapters::processor::CollectionExpectation;
use volumeos_collection_adapters::providers::Provider;
use volumeos_tb_adapter::ledger::Currency;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReceiptRecord {
    pub event_id: String,
    pub provider: Provider,
    pub reference: String,
    pub scope_tenant_id: String,
    pub disposition: String,
    pub provider_payment_id: String,
    pub amount_minor: u128,
    pub currency: Currency,
    pub ledger_transfer_id: Option<String>,
    pub payload: Value,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ResolutionRecord {
    pub resolution_id: String,
    pub event_id: String,
    pub provider: Provider,
    pub reference: String,
    pub credited_tenant_id: String,
    pub amount_minor: u128,
    pub currency: Currency,
    pub ledger_transfer_id: String,
    pub approved_by: String,
    pub reason: String,
    pub payload: Value,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum StoreError {
    Conflict,
    InvalidRecord,
    Transport(String),
}

impl fmt::Display for StoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Conflict => {
                f.write_str("collection reference or receipt already exists with different fields")
            }
            Self::InvalidRecord => f.write_str("stored collection record is invalid"),
            Self::Transport(message) => write!(f, "collection persistence failed: {message}"),
        }
    }
}

impl std::error::Error for StoreError {}

impl From<sqlx::Error> for StoreError {
    fn from(value: sqlx::Error) -> Self {
        Self::Transport(value.to_string())
    }
}

#[async_trait]
pub trait CollectionStore: Send + Sync {
    async fn put_expectation(
        &self,
        provider: Provider,
        reference: &str,
        expectation: &CollectionExpectation,
    ) -> Result<(), StoreError>;

    async fn expectation(
        &self,
        provider: Provider,
        reference: &str,
    ) -> Result<Option<CollectionExpectation>, StoreError>;

    async fn record_receipt(&self, receipt: &ReceiptRecord) -> Result<(), StoreError>;

    async fn receipt(
        &self,
        tenant_id: &str,
        provider: Provider,
        reference: &str,
    ) -> Result<Option<Value>, StoreError>;

    /// Platform-only reconciliation reads use the explicit platform RLS policy.
    async fn reconciliation_receipt(
        &self,
        provider: Provider,
        reference: &str,
    ) -> Result<Option<Value>, StoreError>;

    async fn list_open_suspense(&self, limit: u32) -> Result<Vec<Value>, StoreError>;

    async fn record_resolution(&self, resolution: &ResolutionRecord) -> Result<(), StoreError>;

    async fn ready(&self) -> Result<(), StoreError>;
}

pub struct PostgresCollectionStore {
    pool: PgPool,
}

impl PostgresCollectionStore {
    pub async fn connect(database_url: &str) -> Result<Self, StoreError> {
        if database_url.trim().is_empty() {
            return Err(StoreError::Transport(
                "DATABASE_URL is not configured".to_owned(),
            ));
        }
        let pool = PgPool::connect(database_url).await?;
        let store = Self { pool };
        store.migrate().await?;
        Ok(store)
    }

    async fn migrate(&self) -> Result<(), StoreError> {
        let mut connection = self.pool.acquire().await?;
        sqlx::query("SELECT pg_advisory_lock(hashtext('volumeos.collections.migrations'))")
            .execute(&mut *connection)
            .await?;
        sqlx::query("CREATE TABLE IF NOT EXISTS collection_schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
            .execute(&mut *connection)
            .await?;
        for (version, migration) in [
            (
                "001_collections",
                include_str!("../migrations/001_collections.sql"),
            ),
            (
                "002_reconciliation",
                include_str!("../migrations/002_reconciliation.sql"),
            ),
            (
                "003_platform_review",
                include_str!("../migrations/003_platform_review.sql"),
            ),
        ] {
            let applied =
                sqlx::query("SELECT version FROM collection_schema_migrations WHERE version=$1")
                    .bind(version)
                    .fetch_optional(&mut *connection)
                    .await?;
            if applied.is_none() {
                let mut tx = self.pool.begin().await?;
                sqlx::raw_sql(migration).execute(&mut *tx).await?;
                sqlx::query("INSERT INTO collection_schema_migrations(version) VALUES ($1)")
                    .bind(version)
                    .execute(&mut *tx)
                    .await?;
                tx.commit().await?;
            }
        }
        sqlx::query("SELECT pg_advisory_unlock(hashtext('volumeos.collections.migrations'))")
            .execute(&mut *connection)
            .await?;
        Ok(())
    }

    async fn tenant_tx<'a>(
        &'a self,
        tenant_id: &'a str,
    ) -> Result<Transaction<'a, Postgres>, StoreError> {
        let mut tx = self.pool.begin().await?;
        sqlx::query("SELECT set_config('volumeos.tenant_id', $1, true)")
            .bind(tenant_id)
            .execute(&mut *tx)
            .await?;
        Ok(tx)
    }
}

#[async_trait]
impl CollectionStore for PostgresCollectionStore {
    async fn put_expectation(
        &self,
        provider: Provider,
        reference: &str,
        expectation: &CollectionExpectation,
    ) -> Result<(), StoreError> {
        let mut tx = self.tenant_tx(&expectation.tenant_id).await?;
        let route = sqlx::query(
            "SELECT tenant_id FROM collection_routes WHERE provider=$1 AND reference=$2",
        )
        .bind(provider.as_str())
        .bind(reference)
        .fetch_optional(&mut *tx)
        .await?;
        if let Some(route) = &route {
            let tenant: String = route.try_get("tenant_id")?;
            if tenant != expectation.tenant_id {
                return Err(StoreError::Conflict);
            }
        }
        let existing = sqlx::query("SELECT amount_minor,currency FROM collection_expectations WHERE provider=$1 AND reference=$2 AND tenant_id=$3")
            .bind(provider.as_str())
            .bind(reference)
            .bind(&expectation.tenant_id)
            .fetch_optional(&mut *tx)
            .await?;
        if let Some(existing) = existing {
            let amount: String = existing.try_get("amount_minor")?;
            let currency: String = existing.try_get("currency")?;
            if amount != expectation.amount_minor.to_string()
                || currency != expectation.currency.as_str()
            {
                return Err(StoreError::Conflict);
            }
        } else {
            sqlx::query(
                "INSERT INTO collection_routes(provider,reference,tenant_id) VALUES ($1,$2,$3)",
            )
            .bind(provider.as_str())
            .bind(reference)
            .bind(&expectation.tenant_id)
            .execute(&mut *tx)
            .await?;
            sqlx::query("INSERT INTO collection_expectations(provider,reference,tenant_id,amount_minor,currency) VALUES ($1,$2,$3,$4,$5)")
                .bind(provider.as_str())
                .bind(reference)
                .bind(&expectation.tenant_id)
                .bind(expectation.amount_minor.to_string())
                .bind(expectation.currency.as_str())
                .execute(&mut *tx)
                .await?;
        }
        tx.commit().await?;
        Ok(())
    }

    async fn expectation(
        &self,
        provider: Provider,
        reference: &str,
    ) -> Result<Option<CollectionExpectation>, StoreError> {
        let route = sqlx::query(
            "SELECT tenant_id FROM collection_routes WHERE provider=$1 AND reference=$2",
        )
        .bind(provider.as_str())
        .bind(reference)
        .fetch_optional(&self.pool)
        .await?;
        let Some(route) = route else {
            return Ok(None);
        };
        let tenant_id: String = route.try_get("tenant_id")?;
        let mut tx = self.tenant_tx(&tenant_id).await?;
        let row = sqlx::query("SELECT amount_minor,currency FROM collection_expectations WHERE provider=$1 AND reference=$2 AND tenant_id=$3")
            .bind(provider.as_str())
            .bind(reference)
            .bind(&tenant_id)
            .fetch_optional(&mut *tx)
            .await?;
        let Some(row) = row else {
            return Err(StoreError::InvalidRecord);
        };
        let amount: String = row.try_get("amount_minor")?;
        let currency: String = row.try_get("currency")?;
        let amount_minor = amount
            .parse::<u128>()
            .map_err(|_| StoreError::InvalidRecord)?;
        let currency = match currency.as_str() {
            "NGN" => Currency::Ngn,
            "USD" => Currency::Usd,
            _ => return Err(StoreError::InvalidRecord),
        };
        tx.commit().await?;
        Ok(Some(CollectionExpectation {
            tenant_id,
            amount_minor,
            currency,
        }))
    }

    async fn record_receipt(&self, receipt: &ReceiptRecord) -> Result<(), StoreError> {
        let mut tx = self.tenant_tx(&receipt.scope_tenant_id).await?;
        let existing = sqlx::query("SELECT event_id,payload FROM collection_receipts WHERE provider=$1 AND reference=$2 AND tenant_id=$3")
            .bind(receipt.provider.as_str())
            .bind(&receipt.reference)
            .bind(&receipt.scope_tenant_id)
            .fetch_optional(&mut *tx)
            .await?;
        if let Some(existing) = existing {
            let event_id: String = existing.try_get("event_id")?;
            let payload: Value = existing.try_get("payload")?;
            if event_id != receipt.event_id || payload != receipt.payload {
                return Err(StoreError::Conflict);
            }
        } else {
            sqlx::query(
                "INSERT INTO collection_receipts(event_id,provider,reference,tenant_id,disposition,provider_payment_id,amount_minor,currency,ledger_transfer_id,payload) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
            )
            .bind(&receipt.event_id)
            .bind(receipt.provider.as_str())
            .bind(&receipt.reference)
            .bind(&receipt.scope_tenant_id)
            .bind(&receipt.disposition)
            .bind(&receipt.provider_payment_id)
            .bind(receipt.amount_minor.to_string())
            .bind(receipt.currency.as_str())
            .bind(&receipt.ledger_transfer_id)
            .bind(&receipt.payload)
            .execute(&mut *tx)
            .await?;
        }
        tx.commit().await?;
        Ok(())
    }

    async fn receipt(
        &self,
        tenant_id: &str,
        provider: Provider,
        reference: &str,
    ) -> Result<Option<Value>, StoreError> {
        let mut tx = self.tenant_tx(tenant_id).await?;
        let row = sqlx::query("SELECT payload FROM collection_receipts WHERE tenant_id=$1 AND provider=$2 AND reference=$3")
            .bind(tenant_id)
            .bind(provider.as_str())
            .bind(reference)
            .fetch_optional(&mut *tx)
            .await?;
        tx.commit().await?;
        row.map(|row| row.try_get("payload").map_err(StoreError::from))
            .transpose()
    }

    async fn reconciliation_receipt(
        &self,
        provider: Provider,
        reference: &str,
    ) -> Result<Option<Value>, StoreError> {
        let mut tx = self.tenant_tx("platform").await?;
        let row = sqlx::query(
            "SELECT payload FROM collection_receipts WHERE provider=$1 AND reference=$2",
        )
        .bind(provider.as_str())
        .bind(reference)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|row| row.try_get("payload").map_err(StoreError::from))
            .transpose()
    }

    async fn list_open_suspense(&self, limit: u32) -> Result<Vec<Value>, StoreError> {
        let mut tx = self.tenant_tx("platform").await?;
        let rows = sqlx::query(
            "SELECT r.payload FROM collection_receipts r \
             WHERE r.disposition='suspense' \
             AND NOT EXISTS (SELECT 1 FROM collection_resolutions x \
                             WHERE x.provider=r.provider AND x.reference=r.reference) \
             ORDER BY r.created_at,r.event_id LIMIT $1",
        )
        .bind(i64::from(limit))
        .fetch_all(&mut *tx)
        .await?;
        tx.commit().await?;
        rows.into_iter()
            .map(|row| row.try_get("payload").map_err(StoreError::from))
            .collect()
    }

    async fn record_resolution(&self, resolution: &ResolutionRecord) -> Result<(), StoreError> {
        let mut tx = self.tenant_tx("platform").await?;
        let existing = sqlx::query(
            "SELECT event_id,payload FROM collection_resolutions WHERE provider=$1 AND reference=$2",
        )
        .bind(resolution.provider.as_str())
        .bind(&resolution.reference)
        .fetch_optional(&mut *tx)
        .await?;
        if let Some(existing) = existing {
            let event_id: String = existing.try_get("event_id")?;
            let payload: Value = existing.try_get("payload")?;
            if event_id != resolution.event_id || payload != resolution.payload {
                return Err(StoreError::Conflict);
            }
        } else {
            sqlx::query(
                "INSERT INTO collection_resolutions(\
                 resolution_id,event_id,provider,reference,tenant_id,credited_tenant_id,\
                 amount_minor,currency,ledger_transfer_id,approved_by,reason,payload) \
                 VALUES ($1,$2,$3,$4,'platform',$5,$6,$7,$8,$9,$10,$11)",
            )
            .bind(&resolution.resolution_id)
            .bind(&resolution.event_id)
            .bind(resolution.provider.as_str())
            .bind(&resolution.reference)
            .bind(&resolution.credited_tenant_id)
            .bind(resolution.amount_minor.to_string())
            .bind(resolution.currency.as_str())
            .bind(&resolution.ledger_transfer_id)
            .bind(&resolution.approved_by)
            .bind(&resolution.reason)
            .bind(&resolution.payload)
            .execute(&mut *tx)
            .await?;
        }
        tx.commit().await?;
        Ok(())
    }

    async fn ready(&self) -> Result<(), StoreError> {
        sqlx::query("SELECT 1").execute(&self.pool).await?;
        Ok(())
    }
}
