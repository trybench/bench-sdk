use serde_json::{json, Value};
use std::{
    collections::HashMap,
    io::{BufRead, BufReader, Read, Write},
    net::{TcpListener, TcpStream},
    sync::{
        atomic::{AtomicBool, AtomicU16, Ordering},
        Arc, Mutex,
    },
    thread,
};
use trybench_sdk::{BenchPlatform, PlatformFile, PlatformRequest};

type Requests = Arc<Mutex<Vec<(String, Vec<u8>)>>>;
struct Server {
    url: String,
    requests: Requests,
    status: Arc<AtomicU16>,
    body: Arc<Mutex<(String, String)>>,
    stop: Arc<AtomicBool>,
    worker: Option<thread::JoinHandle<()>>,
}
impl Server {
    fn new() -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let requests: Requests = Arc::new(Mutex::new(Vec::new()));
        let seen = requests.clone();
        let status = Arc::new(AtomicU16::new(200));
        let code = status.clone();
        let body = Arc::new(Mutex::new((
            "application/json".to_string(),
            "{\"ok\":true}".to_string(),
        )));
        let response = body.clone();
        let stop = Arc::new(AtomicBool::new(false));
        let stopped = stop.clone();
        let worker = thread::spawn(move || {
            for connection in listener.incoming() {
                if stopped.load(Ordering::SeqCst) {
                    break;
                }
                let mut stream = connection.unwrap();
                stream
                    .set_read_timeout(Some(std::time::Duration::from_secs(3)))
                    .unwrap();
                let mut reader = BufReader::new(stream.try_clone().unwrap());
                let mut headers = String::new();
                let mut length = 0;
                loop {
                    let mut line = String::new();
                    reader.read_line(&mut line).unwrap();
                    if line == "\r\n" || line.is_empty() {
                        break;
                    }
                    if let Some(value) = line.to_lowercase().strip_prefix("content-length:") {
                        length = value.trim().parse::<usize>().unwrap();
                    }
                    headers.push_str(&line);
                }
                let mut payload = vec![0; length];
                reader.read_exact(&mut payload).unwrap();
                seen.lock().unwrap().push((headers, payload));
                let (kind, data) = response.lock().unwrap().clone();
                write!(stream, "HTTP/1.1 {} Test\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\nLocation: http://127.0.0.1:1/secret\r\n\r\n{}", code.load(Ordering::SeqCst), kind, data.len(), data).unwrap();
            }
        });
        Self {
            url,
            requests,
            status,
            body,
            stop,
            worker: Some(worker),
        }
    }
}
impl Drop for Server {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        let _ = TcpStream::connect(self.url.trim_start_matches("http://"));
        self.worker.take().unwrap().join().unwrap();
    }
}

#[tokio::test]
async fn complete_catalog_routes_and_scopes_credentials() {
    let server = Server::new();
    let platform = BenchPlatform::new("fixture", &server.url).unwrap();
    for operation in platform.operations()["operations"].as_array().unwrap() {
        let mut path = HashMap::new();
        let mut expected = operation["path"].as_str().unwrap().to_string();
        for key in operation["path_parameters"].as_object().unwrap().keys() {
            path.insert(key.clone(), "42".into());
            expected = expected.replace(&format!("{{{key}}}"), "42");
        }
        assert_eq!(
            platform
                .call(
                    operation["id"].as_str().unwrap(),
                    PlatformRequest {
                        path,
                        ..Default::default()
                    }
                )
                .await
                .unwrap(),
            json!({"ok":true})
        );
        let seen = server.requests.lock().unwrap();
        let (headers, _) = seen.last().unwrap();
        assert!(headers.starts_with(&format!(
            "{} {} HTTP/1.1",
            operation["method"].as_str().unwrap(),
            expected
        )));
        assert_eq!(
            headers
                .to_lowercase()
                .contains("authorization: bearer fixture"),
            operation["auth"] != "public"
        );
    }
}
#[tokio::test]
async fn multipart_preserves_binary_and_server_errors() {
    let server = Server::new();
    let platform = BenchPlatform::new("fixture", &server.url).unwrap();
    let input = PlatformRequest {
        path: HashMap::from([("id".into(), "1".into())]),
        form: json!({"content_consent":true,"mapping":{"input":"question"}})
            .as_object()
            .unwrap()
            .clone(),
        files: vec![PlatformFile {
            name: "cases.xlsx".into(),
            content: vec![0, 255, 128],
        }],
        ..Default::default()
    };
    platform.call("upload_dataset", input).await.unwrap();
    {
        let seen = server.requests.lock().unwrap();
        let payload = &seen[0].1;
        assert!(payload.windows(3).any(|bytes| bytes == [0, 255, 128]));
        assert!(String::from_utf8_lossy(payload).contains("{\"input\":\"question\"}"));
    }
    server.status.store(403, Ordering::SeqCst);
    *server.body.lock().unwrap() = ("application/json".into(), "{\"error\":{\"code\":\"upgrade_required\",\"message\":\"Growth required\",\"reference\":\"BENCH-TEST\",\"pricing_url\":\"https://stg.usebench.ai/plans\",\"upgrade_url\":\"https://stg.usebench.ai/plans\",\"payment_confirmation_required\":true}}".into());
    let error = platform
        .call("whoami", Default::default())
        .await
        .unwrap_err();
    assert_eq!(error.code, "upgrade_required");
    assert_eq!(error.reference.as_deref(), Some("BENCH-TEST"));
    assert_eq!(
        error.details["upgrade_url"],
        "https://stg.usebench.ai/plans"
    );
    assert_eq!(error.details["payment_confirmation_required"], true);
    assert!(!error.retryable());
    assert_eq!(server.requests.lock().unwrap().len(), 2);
}
#[tokio::test]
async fn rejects_traversal_and_redirects_and_stream_errors() {
    let server = Server::new();
    let platform = BenchPlatform::new("fixture", &server.url).unwrap();
    for value in ["..", "%2fadmin", "a/b", "a\\b", "a\n"] {
        assert!(platform
            .call(
                "get_system",
                PlatformRequest {
                    path: HashMap::from([("id".into(), value.into())]),
                    ..Default::default()
                }
            )
            .await
            .is_err());
    }
    assert!(server.requests.lock().unwrap().is_empty());
    server.status.store(302, Ordering::SeqCst);
    assert!(platform.call("whoami", Default::default()).await.is_err());
    assert_eq!(server.requests.lock().unwrap().len(), 1);
    server.status.store(200, Ordering::SeqCst);
    *server.body.lock().unwrap() = (
        "application/x-ndjson".into(),
        "{\"type\":\"progress\"}\n{\"type\":\"error\",\"message\":\"failed\"}\n".into(),
    );
    let error = platform
        .call("whoami", Default::default())
        .await
        .unwrap_err();
    assert_eq!(error.code, "stream_error");
}
#[test]
fn refuses_unsafe_origins() {
    for endpoint in [
        "http://external.invalid",
        "https://user:secret@example.com",
        "https://api.example.com/?key=secret",
    ] {
        assert!(BenchPlatform::new("fixture", endpoint).is_err());
    }
    let _: Value = json!({});
}
