use std::sync::Arc;

use chrono::{Datelike, NaiveDate};
use datafusion::arrow::array::{ArrayRef, Date32Array, Int64Array, StringArray};
use datafusion::arrow::datatypes::{DataType, Field, Schema};
use datafusion::arrow::record_batch::RecordBatch;
use datafusion::prelude::SessionContext;
use volumeos_svc_query::query::{build_query, QueryRequest, QueryService};

fn request() -> QueryRequest {
    QueryRequest {
        table: "gt_campaign_perf".to_owned(),
        columns: vec!["tenant_id".to_owned(), "campaign_id".to_owned(), "delivered_count".to_owned()],
        start_date: Some("2026-10-01".to_owned()),
        end_date: Some("2026-10-31".to_owned()),
        limit: Some(100),
    }
}

fn day(value: &str) -> i32 {
    NaiveDate::parse_from_str(value, "%Y-%m-%d").unwrap().num_days_from_ce() - 719_163
}

#[test]
fn server_built_query_always_enforces_tenant_and_caps() {
    let sql = build_query(&request(), "tenant-a").unwrap();
    assert!(sql.contains("`tenant_id` = 'tenant-a'"));
    assert!(sql.contains("LIMIT 100"));
    assert!(sql.contains("DATE '2026-10-01'"));
    assert!(build_query(&request(), "tenant-a' OR true --").is_err());
    let mut raw_sql = request();
    raw_sql.table = "gt_campaign_perf WHERE true".to_owned();
    assert!(build_query(&raw_sql, "tenant-a").is_err());
    let mut excessive = request();
    excessive.end_date = Some("2027-10-31".to_owned());
    assert!(build_query(&excessive, "tenant-a").is_err());
    excessive = request();
    excessive.limit = Some(1_001);
    assert!(build_query(&excessive, "tenant-a").is_err());
    excessive = request();
    excessive.columns.push("tenant_id".to_owned());
    assert!(build_query(&excessive, "tenant-a").is_err());
}

#[tokio::test]
async fn datafusion_execution_cannot_return_another_tenants_gold_rows() {
    let schema = Arc::new(Schema::new(vec![
        Field::new("tenant_id", DataType::Utf8, false),
        Field::new("campaign_id", DataType::Utf8, false),
        Field::new("event_date", DataType::Date32, false),
        Field::new("sent_count", DataType::Int64, false),
        Field::new("delivered_count", DataType::Int64, false),
        Field::new("failed_count", DataType::Int64, false),
    ]));
    let batch = RecordBatch::try_new(schema.clone(), vec![
        Arc::new(StringArray::from(vec!["tenant-a", "tenant-b"])) as ArrayRef,
        Arc::new(StringArray::from(vec!["campaign-a", "campaign-b"])),
        Arc::new(Date32Array::from(vec![day("2026-10-28"), day("2026-10-28")])),
        Arc::new(Int64Array::from(vec![10, 20])),
        Arc::new(Int64Array::from(vec![9, 18])),
        Arc::new(Int64Array::from(vec![1, 2])),
    ]).unwrap();
    let context = SessionContext::new();
    context.register_batch("gt_campaign_perf", batch).unwrap();
    let result = QueryService::new(context).query(request(), "tenant-a").await.unwrap();
    assert_eq!(result.row_count, 1);
    assert_eq!(result.rows[0]["tenant_id"], "tenant-a");
    assert_eq!(result.rows[0]["campaign_id"], "campaign-a");
}
