use std::error::Error;
use std::sync::Arc;

use volumeos_svc_billing::{router, AppState};
use volumeos_tb_adapter::ledger::Ledger;
use volumeos_tb_adapter::tigerbeetle::TigerBeetleTransport;

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let transport = TigerBeetleTransport::from_env()?;
    let token = std::env::var("VOLUMEOS_SERVICE_TOKEN")
        .map_err(|_| "VOLUMEOS_SERVICE_TOKEN is not configured")?;
    let state = Arc::new(AppState::new(Ledger::new(transport), token)?);
    let port = std::env::var("PORT").unwrap_or_else(|_| "8080".to_owned());
    let listener = tokio::net::TcpListener::bind(format!("0.0.0.0:{port}")).await?;
    axum::serve(listener, router(state))
        .with_graceful_shutdown(shutdown_signal())
        .await?;
    Ok(())
}

async fn shutdown_signal() {
    let _ = tokio::signal::ctrl_c().await;
}
