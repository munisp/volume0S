use std::sync::Arc;

use datafusion::prelude::SessionContext;
use volumeos_svc_query::{auth::OidcValidator, proto::gold_query_server::GoldQueryServer, router, AppState, GrpcService};
use volumeos_svc_query::query::QueryService;

const TABLES: [&str; 6] = [
    "gt_campaign_perf", "gt_territory_scores", "gt_lga_density",
    "gt_client_health", "gt_cost_per_client", "gt_revenue_by_offer",
];

fn required(name: &str) -> Result<String, String> {
    std::env::var(name).map_err(|_| format!("{name} is required"))
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let gold_root = required("GOLD_DELTA_ROOT")?;
    if !gold_root.starts_with("s3://") && !gold_root.starts_with("s3a://") {
        return Err("GOLD_DELTA_ROOT must be an S3-compatible URI".into());
    }
    let context = SessionContext::new();
    for name in TABLES {
        let table = deltalake::open_table(format!("{}/{name}", gold_root.trim_end_matches('/'))).await?;
        context.register_table(name, Arc::new(table))?;
    }
    let auth = OidcValidator::from_jwks(
        &required("OIDC_JWKS_URL")?, &required("OIDC_ISSUER")?, &required("OIDC_AUDIENCE")?,
    ).await?;
    let state = Arc::new(AppState { query: QueryService::new(context), auth });
    let rest_address = std::env::var("REST_ADDR").unwrap_or_else(|_| "0.0.0.0:8080".to_owned());
    let grpc_address = std::env::var("GRPC_ADDR").unwrap_or_else(|_| "0.0.0.0:8081".to_owned()).parse()?;
    let listener = tokio::net::TcpListener::bind(rest_address).await?;
    let rest = axum::serve(listener, router(state.clone()));
    let grpc = tonic::transport::Server::builder()
        .add_service(GoldQueryServer::new(GrpcService { state })).serve(grpc_address);
    tokio::try_join!(async { rest.await.map_err(|error| error.into()) }, async { grpc.await.map_err(|error| error.into()) })?;
    Ok(())
}
