//! Loopback-only provider contract tests. No external provider is contacted.

use std::io::{Read, Write};
use std::net::TcpListener;
use std::sync::mpsc::{self, Receiver};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use volumeos_collection_adapters::providers::{
    CheckoutRequest, ConfirmationStatus, FlutterwaveClient, MoniepointClient, PaymentNotification,
    PaystackClient, Provider, ProviderClient,
};
use volumeos_tb_adapter::ledger::Currency;

fn notification(provider: Provider) -> PaymentNotification {
    PaymentNotification {
        provider,
        notification_id: "notification-1".to_owned(),
        confirmation_id: match provider {
            Provider::Paystack => "order-1",
            Provider::Flutterwave => "charge-1",
            Provider::Moniepoint => "transaction-1",
        }
        .to_owned(),
        reference: "order-1".to_owned(),
    }
}

fn checkout(provider: Provider) -> CheckoutRequest {
    CheckoutRequest {
        provider,
        tenant_id: "tenant-a".to_owned(),
        reference: "order-1".to_owned(),
        amount_minor: 125_000,
        currency: Currency::Ngn,
        customer_email: "owner@example.com".to_owned(),
        redirect_url: "https://app.example/billing/callback".to_owned(),
    }
}

fn serve_json(bodies: Vec<&'static str>) -> (String, Receiver<String>, JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let (sender, receiver) = mpsc::channel();
    let handle = thread::spawn(move || {
        for body in bodies {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut request = Vec::new();
            let mut buffer = [0_u8; 4096];
            loop {
                let count = stream.read(&mut buffer).unwrap();
                assert!(count > 0, "client closed before sending a complete request");
                request.extend_from_slice(&buffer[..count]);
                if request_complete(&request) {
                    break;
                }
            }
            sender.send(String::from_utf8(request).unwrap()).unwrap();
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            )
            .unwrap();
        }
    });
    (format!("http://{address}"), receiver, handle)
}

fn request_complete(request: &[u8]) -> bool {
    let Some(header_end) = request.windows(4).position(|window| window == b"\r\n\r\n") else {
        return false;
    };
    let body_start = header_end + 4;
    let headers = String::from_utf8_lossy(&request[..header_end]);
    let content_length = headers
        .lines()
        .find_map(|line| {
            let (name, value) = line.split_once(':')?;
            name.eq_ignore_ascii_case("content-length")
                .then(|| value.trim().parse::<usize>().ok())
                .flatten()
        })
        .unwrap_or(0);
    request.len() >= body_start + content_length
}

#[tokio::test]
async fn paystack_confirmation_uses_verification_endpoint_and_minor_units() {
    let (base, requests, server) = serve_json(vec![
        r#"{"status":true,"data":{"id":12345,"reference":"order-1","status":"success","amount":125000,"currency":"NGN","metadata":{"tenant_id":"tenant-a"}}}"#,
    ]);
    let client = PaystackClient::new(&base, "test-secret".to_owned()).unwrap();
    let confirmed = client
        .confirm(&notification(Provider::Paystack))
        .await
        .unwrap();
    assert_eq!(confirmed.status, ConfirmationStatus::Settled);
    assert_eq!(confirmed.provider_payment_id, "12345");
    assert_eq!(confirmed.amount_minor, 125_000);
    assert_eq!(confirmed.currency, Currency::Ngn);
    assert_eq!(confirmed.tenant_hint.as_deref(), Some("tenant-a"));

    let request = requests.recv().unwrap().to_lowercase();
    assert!(request.starts_with("get /transaction/verify/order-1 "));
    assert!(request.contains("authorization: bearer test-secret\r\n"));
    server.join().unwrap();
}

#[tokio::test]
async fn flutterwave_confirmation_uses_oauth_and_v4_charge_retrieval() {
    let (base, requests, server) = serve_json(vec![
        r#"{"access_token":"test-token"}"#,
        r#"{"status":"success","data":{"id":"charge-1","reference":"order-1","status":"succeeded","amount":"1250.00","currency":"NGN","meta":{"tenant_id":"tenant-a"}}}"#,
    ]);
    let client = FlutterwaveClient::new(
        &base,
        &format!("{base}/oauth/token"),
        "test-client".to_owned(),
        "test-secret".to_owned(),
    )
    .unwrap();
    let confirmed = client
        .confirm(&notification(Provider::Flutterwave))
        .await
        .unwrap();
    assert_eq!(confirmed.status, ConfirmationStatus::Settled);
    assert_eq!(confirmed.provider_payment_id, "charge-1");
    assert_eq!(confirmed.amount_minor, 125_000);

    let token_request = requests.recv().unwrap().to_lowercase();
    assert!(token_request.starts_with("post /oauth/token "));
    assert!(token_request.contains("grant_type=client_credentials"));
    let charge_request = requests.recv().unwrap().to_lowercase();
    assert!(charge_request.starts_with("get /charges/charge-1 "));
    assert!(charge_request.contains("authorization: bearer test-token\r\n"));
    assert!(charge_request.contains("x-trace-id: "));
    server.join().unwrap();
}

#[tokio::test]
async fn moniepoint_confirmation_authenticates_and_normalizes_paid_status() {
    let (base, requests, server) = serve_json(vec![
        r#"{"requestSuccessful":true,"responseBody":{"accessToken":"test-token"}}"#,
        r#"{"requestSuccessful":true,"responseBody":{"transactionReference":"transaction-1","paymentReference":"order-1","paymentStatus":"PAID","amountPaid":"1250.00","currency":"NGN","metaData":{"tenant_id":"tenant-a"}}}"#,
    ]);
    let client = MoniepointClient::new(
        &base,
        "test-key".to_owned(),
        "test-secret".to_owned(),
        "test-contract".to_owned(),
    )
    .unwrap();
    let confirmed = client
        .confirm(&notification(Provider::Moniepoint))
        .await
        .unwrap();
    assert_eq!(confirmed.status, ConfirmationStatus::Settled);
    assert_eq!(confirmed.amount_minor, 125_000);
    assert_eq!(confirmed.currency, Currency::Ngn);

    let auth_request = requests.recv().unwrap().to_lowercase();
    assert!(auth_request.starts_with("post /api/v1/auth/login "));
    assert!(auth_request.contains("authorization: basic "));
    let transaction_request = requests.recv().unwrap().to_lowercase();
    assert!(transaction_request.starts_with("get /api/v2/transactions/transaction-1 "));
    assert!(transaction_request.contains("authorization: bearer test-token\r\n"));
    server.join().unwrap();
}

#[tokio::test]
async fn changed_paystack_reference_is_rejected() {
    let (base, requests, server) = serve_json(vec![
        r#"{"status":true,"data":{"id":12345,"reference":"different-order","status":"success","amount":125000,"currency":"NGN"}}"#,
    ]);
    let client = PaystackClient::new(&base, "test-secret".to_owned()).unwrap();
    let error = client
        .confirm(&notification(Provider::Paystack))
        .await
        .unwrap_err();
    assert!(error.to_string().contains("reference changed"));
    requests.recv().unwrap();
    server.join().unwrap();
}

#[tokio::test]
async fn paystack_checkout_is_initialized_server_side_with_exact_minor_units() {
    let (base, requests, server) = serve_json(vec![
        r#"{"status":true,"data":{"reference":"order-1","access_code":"access-1","authorization_url":"https://checkout.paystack.com/access-1"}}"#,
    ]);
    let client = PaystackClient::new(&base, "test-secret".to_owned()).unwrap();
    let session = client
        .initialize(&checkout(Provider::Paystack))
        .await
        .unwrap();
    assert_eq!(session.provider_session_id, "access-1");
    assert_eq!(session.reference, "order-1");
    let request = requests.recv().unwrap();
    assert!(request.starts_with("POST /transaction/initialize "));
    assert!(request.contains("\"amount\":\"125000\""));
    assert!(request.contains("\"tenant_id\":\"tenant-a\""));
    server.join().unwrap();
}

#[tokio::test]
async fn flutterwave_checkout_uses_v4_customer_method_and_charge_flow() {
    let (base, requests, server) = serve_json(vec![
        r#"{"access_token":"test-token"}"#,
        r#"{"status":"success","data":{"id":"customer-1"}}"#,
        r#"{"status":"success","data":{"id":"method-1"}}"#,
        r#"{"status":"success","data":{"id":"charge-1","reference":"order-1","next_action":{"redirect_url":{"url":"https://checkout.flutterwave.com/charge-1"}}}}"#,
    ]);
    let client = FlutterwaveClient::new(
        &base,
        &format!("{base}/oauth/token"),
        "test-client".to_owned(),
        "test-secret".to_owned(),
    )
    .unwrap();
    let session = client
        .initialize(&checkout(Provider::Flutterwave))
        .await
        .unwrap();
    assert_eq!(session.provider_session_id, "charge-1");
    assert_eq!(
        session.checkout_url,
        "https://checkout.flutterwave.com/charge-1"
    );
    assert!(requests.recv().unwrap().starts_with("POST /oauth/token "));
    let customer = requests.recv().unwrap();
    assert!(customer.starts_with("POST /customers "));
    assert!(customer.contains("x-idempotency-key: "));
    assert!(requests
        .recv()
        .unwrap()
        .starts_with("POST /payment-methods "));
    let charge = requests.recv().unwrap();
    assert!(charge.starts_with("POST /charges "));
    assert!(charge.contains("\"amount\":\"1250\""));
    server.join().unwrap();
}

#[tokio::test]
async fn moniepoint_checkout_returns_a_confirmed_reference_and_hosted_url() {
    let (base, requests, server) = serve_json(vec![
        r#"{"requestSuccessful":true,"responseBody":{"accessToken":"test-token"}}"#,
        r#"{"requestSuccessful":true,"responseBody":{"paymentReference":"order-1","transactionReference":"transaction-1","checkoutUrl":"https://sdk.monnify.com/checkout/transaction-1"}}"#,
    ]);
    let client = MoniepointClient::new(
        &base,
        "test-key".to_owned(),
        "test-secret".to_owned(),
        "test-contract".to_owned(),
    )
    .unwrap();
    let session = client
        .initialize(&checkout(Provider::Moniepoint))
        .await
        .unwrap();
    assert_eq!(session.provider_session_id, "transaction-1");
    assert_eq!(session.reference, "order-1");
    assert!(requests
        .recv()
        .unwrap()
        .starts_with("POST /api/v1/auth/login "));
    let request = requests.recv().unwrap();
    assert!(request.starts_with("POST /api/v1/merchant/transactions/init-transaction "));
    assert!(request.contains("\"contractCode\":\"test-contract\""));
    assert!(request.contains("\"amount\":\"1250\""));
    server.join().unwrap();
}
