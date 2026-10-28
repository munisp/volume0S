use std::fmt;

use chrono::NaiveDate;
use datafusion::arrow::json::writer::LineDelimitedWriter;
use datafusion::prelude::SessionContext;
use serde::{Deserialize, Serialize};

const MAX_ROWS: u32 = 1_000;
const MAX_DAYS: i64 = 92;

#[derive(Clone, Debug, Deserialize)]
pub struct QueryRequest {
    pub table: String,
    pub columns: Vec<String>,
    pub start_date: Option<String>,
    pub end_date: Option<String>,
    pub limit: Option<u32>,
}

#[derive(Clone, Debug, Serialize)]
pub struct QueryResult {
    pub rows: Vec<serde_json::Value>,
    pub row_count: usize,
    pub sql_guard: String,
}

#[derive(Debug)]
pub enum QueryError {
    Invalid(String),
    Execution(String),
}

impl fmt::Display for QueryError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Invalid(message) => write!(f, "invalid gold query: {message}"),
            Self::Execution(message) => write!(f, "gold query failed: {message}"),
        }
    }
}

impl std::error::Error for QueryError {}

struct TablePolicy {
    columns: &'static [&'static str],
    date_column: Option<&'static str>,
}

fn policy(table: &str) -> Option<TablePolicy> {
    let campaign = &["tenant_id", "campaign_id", "event_date", "sent_count", "delivered_count", "failed_count", "source", "lineage_id"][..];
    let territory = &["tenant_id", "h3_r9", "h3_r7", "geometry", "density", "digital_silence", "value", "competition", "score", "model_version", "weight_version", "source", "lineage_id"][..];
    let density = &["tenant_id", "lga", "ward", "niche", "h3_r9", "poi_count", "geometry", "source", "lineage_id"][..];
    let health = &["tenant_id", "event_date", "health_score", "source", "lineage_id"][..];
    let cost = &["tenant_id", "event_date", "infra_cost_minor", "provider_cost_minor", "currency", "source", "lineage_id"][..];
    let revenue = &["tenant_id", "offer", "event_date", "revenue_minor", "currency", "source", "lineage_id"][..];
    match table {
        "gt_campaign_perf" => Some(TablePolicy { columns: campaign, date_column: Some("event_date") }),
        "gt_territory_scores" => Some(TablePolicy { columns: territory, date_column: None }),
        "gt_lga_density" => Some(TablePolicy { columns: density, date_column: None }),
        "gt_client_health" => Some(TablePolicy { columns: health, date_column: Some("event_date") }),
        "gt_cost_per_client" => Some(TablePolicy { columns: cost, date_column: Some("event_date") }),
        "gt_revenue_by_offer" => Some(TablePolicy { columns: revenue, date_column: Some("event_date") }),
        _ => None,
    }
}

fn valid_tenant(value: &str) -> bool {
    let mut chars = value.chars();
    chars.next().is_some_and(|c| c.is_ascii_alphanumeric())
        && chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | ':' | '-'))
        && value.len() <= 128
}

fn parse_date(value: &Option<String>, name: &str) -> Result<NaiveDate, QueryError> {
    let value = value.as_deref().ok_or_else(|| QueryError::Invalid(format!("{name} is required for this table")))?;
    NaiveDate::parse_from_str(value, "%Y-%m-%d").map_err(|_| QueryError::Invalid(format!("{name} must be ISO YYYY-MM-DD")))
}

pub fn build_query(request: &QueryRequest, tenant_id: &str) -> Result<String, QueryError> {
    if !valid_tenant(tenant_id) {
        return Err(QueryError::Invalid("tenant claim is malformed".to_owned()));
    }
    let table_policy = policy(&request.table).ok_or_else(|| QueryError::Invalid("table is not an approved client-facing gold mart".to_owned()))?;
    if request.columns.is_empty() || request.columns.len() > table_policy.columns.len() {
        return Err(QueryError::Invalid("select at least one approved column".to_owned()));
    }
    let mut selected = request.columns.clone();
    selected.sort();
    selected.dedup();
    if selected.len() != request.columns.len() || selected.iter().any(|column| !table_policy.columns.contains(&column.as_str())) {
        return Err(QueryError::Invalid("columns are duplicated or outside the mart contract".to_owned()));
    }
    let limit = request.limit.unwrap_or(100);
    if limit == 0 || limit > MAX_ROWS {
        return Err(QueryError::Invalid(format!("row limit must be between 1 and {MAX_ROWS}")));
    }
    let columns = request.columns.iter().map(|column| format!("`{column}`")).collect::<Vec<_>>().join(", ");
    let mut predicates = vec![format!("`tenant_id` = '{tenant_id}'")];
    if let Some(date_column) = table_policy.date_column {
        let start = parse_date(&request.start_date, "start_date")?;
        let end = parse_date(&request.end_date, "end_date")?;
        let days = (end - start).num_days();
        if !(0..=MAX_DAYS).contains(&days) {
            return Err(QueryError::Invalid(format!("date range must be between 0 and {MAX_DAYS} days")));
        }
        predicates.push(format!("`{date_column}` >= DATE '{start}' AND `{date_column}` <= DATE '{end}'"));
    } else if request.start_date.is_some() || request.end_date.is_some() {
        return Err(QueryError::Invalid("this spatial mart does not expose an event-date filter".to_owned()));
    }
    Ok(format!("SELECT {columns} FROM `{}` WHERE {} LIMIT {limit}", request.table, predicates.join(" AND ")))
}

#[derive(Clone)]
pub struct QueryService {
    context: SessionContext,
}

impl QueryService {
    pub fn new(context: SessionContext) -> Self {
        Self { context }
    }

    pub fn context(&self) -> &SessionContext {
        &self.context
    }

    pub async fn query(&self, request: QueryRequest, tenant_id: &str) -> Result<QueryResult, QueryError> {
        let sql = build_query(&request, tenant_id)?;
        let batches = self.context.sql(&sql).await.map_err(|error| QueryError::Execution(error.to_string()))?
            .collect().await.map_err(|error| QueryError::Execution(error.to_string()))?;
        let mut writer = LineDelimitedWriter::new(Vec::new());
        for batch in &batches {
            writer.write(batch).map_err(|error| QueryError::Execution(error.to_string()))?;
        }
        writer.finish().map_err(|error| QueryError::Execution(error.to_string()))?;
        let bytes = writer.into_inner();
        let rows = bytes.split(|byte| *byte == b'\n').filter(|line| !line.is_empty())
            .map(|line| serde_json::from_slice(line).map_err(|error| QueryError::Execution(error.to_string())))
            .collect::<Result<Vec<serde_json::Value>, _>>()?;
        Ok(QueryResult { row_count: rows.len(), rows, sql_guard: format!("tenant={tenant_id}; max_rows={MAX_ROWS}; max_days={MAX_DAYS}") })
    }
}
