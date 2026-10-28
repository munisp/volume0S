pub mod auth;
pub mod query;

use std::sync::Arc;

use auth::OidcValidator;
use axum::{extract::State, http::{HeaderMap, StatusCode}, response::Json, routing::{get, post}, Router};
use query::{QueryError, QueryRequest, QueryResult, QueryService};
use tonic::{Request, Response, Status};

pub mod proto {
    tonic::include_proto!("volumeos.query.v1");
}

#[derive(Clone)]
pub struct AppState {
    pub query: QueryService,
    pub auth: OidcValidator,
}

pub fn router(state: Arc<AppState>) -> Router {
    Router::new().route("/v1/query", post(rest_query))
        .route("/health/live", get(health)).route("/health/ready", get(health)).with_state(state)
}

async fn health() -> &'static str {
    "ok"
}

fn bearer(headers: &HeaderMap) -> Result<&str, StatusCode> {
    let value = headers.get(axum::http::header::AUTHORIZATION).and_then(|value| value.to_str().ok())
        .ok_or(StatusCode::UNAUTHORIZED)?;
    value.strip_prefix("Bearer ").filter(|token| !token.is_empty()).ok_or(StatusCode::UNAUTHORIZED)
}

async fn rest_query(State(state): State<Arc<AppState>>, headers: HeaderMap, Json(request): Json<QueryRequest>)
    -> Result<Json<QueryResult>, (StatusCode, String)> {
    let claims = state.auth.validate(bearer(&headers).map_err(|status| (status, "authentication required".to_owned()))?).await
        .map_err(|_| (StatusCode::UNAUTHORIZED, "authentication failed".to_owned()))?;
    state.query.query(request, &claims.tenant_id).await.map(Json).map_err(|error| match error {
        QueryError::Invalid(message) => (StatusCode::BAD_REQUEST, message),
        QueryError::Execution(_) => (StatusCode::INTERNAL_SERVER_ERROR, "gold query failed".to_owned()),
    })
}

pub struct GrpcService {
    pub state: Arc<AppState>,
}

#[tonic::async_trait]
impl proto::gold_query_server::GoldQuery for GrpcService {
    async fn query(&self, request: Request<proto::QueryRequest>) -> Result<Response<proto::QueryResponse>, Status> {
        let token = request.metadata().get("authorization").and_then(|value| value.to_str().ok())
            .and_then(|value| value.strip_prefix("Bearer ")).filter(|value| !value.is_empty())
            .ok_or_else(|| Status::unauthenticated("authentication required"))?;
        let claims = self.state.auth.validate(token).await.map_err(|_| Status::unauthenticated("authentication failed"))?;
        let input = request.into_inner();
        let request = QueryRequest {
            table: input.table,
            columns: input.columns,
            start_date: (!input.start_date.is_empty()).then_some(input.start_date),
            end_date: (!input.end_date.is_empty()).then_some(input.end_date),
            limit: Some(input.limit),
        };
        let result = self.state.query.query(request, &claims.tenant_id).await.map_err(|error| match error {
            QueryError::Invalid(message) => Status::invalid_argument(message),
            QueryError::Execution(_) => Status::internal("gold query failed"),
        })?;
        Ok(Response::new(proto::QueryResponse {
            rows_json: serde_json::to_string(&result.rows).map_err(|_| Status::internal("result encoding failed"))?,
            row_count: result.row_count as u32,
        }))
    }
}
