use serde_json::{json, Value};
use std::time::Duration;
use std::{
    io::{BufRead, BufReader, Read, Write},
    net::{TcpListener, TcpStream},
    sync::{
        atomic::{AtomicBool, AtomicU16, Ordering},
        Arc, Mutex,
    },
    thread,
};
use trybench_sdk::{Bench, Options, SpanInput};

#[tokio::test]
async fn shared_privacy_contract_in_outgoing_payload() {
    let fixture: Value = serde_json::from_str(include_str!("fixtures/privacy.json")).unwrap();
    let server = Server::new();
    let mut options = server.options();
    options.capture_content = true;
    let bench = Bench::new(options).unwrap();
    let mut span = bench.start_span(
        None,
        SpanInput::new("privacy-contract").input(fixture["input"].clone()),
    );
    span.set_output(json!({"ok":true}));
    span.end();
    bench.shutdown().await;
    let requests = server.requests.lock().unwrap();
    let body: Value = serde_json::from_str(&requests[0].1).unwrap();
    let raw = body["traces"][0]["spans"][0]["input_value"]
        .as_str()
        .unwrap();
    for value in fixture["forbidden"].as_array().unwrap() {
        assert!(!raw.contains(value.as_str().unwrap()), "leaked {value}")
    }
    for value in fixture["preserved"].as_array().unwrap() {
        assert!(raw.contains(value.as_str().unwrap()), "lost {value}")
    }
    assert_eq!(serde_json::from_str::<Value>(raw).unwrap()["count"], 42);
}

struct Server {
    url: String,
    requests: Arc<Mutex<Vec<(String, String)>>>,
    status: Arc<AtomicU16>,
    stop: Arc<AtomicBool>,
    thread: Option<thread::JoinHandle<()>>,
}
impl Server {
    fn new() -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let requests = Arc::new(Mutex::new(Vec::new()));
        let seen = requests.clone();
        let status = Arc::new(AtomicU16::new(201));
        let code = status.clone();
        let stop = Arc::new(AtomicBool::new(false));
        let stopped = stop.clone();
        let thread = thread::spawn(move || {
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
                    if reader.read_line(&mut line).unwrap() == 0 {
                        break;
                    }
                    if line == "\r\n" {
                        break;
                    }
                    if let Some(n) = line.to_lowercase().strip_prefix("content-length:") {
                        length = n.trim().parse::<usize>().unwrap()
                    };
                    headers.push_str(&line)
                }
                let mut body = vec![0; length];
                reader.read_exact(&mut body).unwrap();
                seen.lock()
                    .unwrap()
                    .push((headers, String::from_utf8(body).unwrap()));
                write!(stream,"HTTP/1.1 {} Test\r\nContent-Length: 0\r\nConnection: close\r\nLocation: /never-follow\r\n\r\n",code.load(Ordering::SeqCst)).unwrap();
            }
        });
        Self {
            url: format!("http://{address}"),
            requests,
            status,
            stop,
            thread: Some(thread),
        }
    }
    fn options(&self) -> Options {
        let mut options = Options::new("bench_sk_synthetic_test_key", "fixture/rust", "main");
        options.endpoint = self.url.clone();
        options.environment = Some("staging".into());
        options
    }
}
impl Drop for Server {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        let _ = TcpStream::connect(self.url.trim_start_matches("http://"));
        let _ = self.thread.take().unwrap().join();
    }
}

#[tokio::test]
async fn http_nested_tasks_redaction_and_application_error() {
    let server = Server::new();
    let mut options = server.options();
    options.capture_content = true;
    let bench = Bench::new(options).unwrap();
    let mut root=bench.start_span(None,SpanInput::new("agent").kind("AGENT").input(json!({"card_number":4242424242424242_u64,"nested":"{\"full_name\":\"Private Person\"}","note":"person@example.test +49 151 12345678 192.168.1.2 4242-4242-4242-4242","count":3})));
    let mut tasks = tokio::task::JoinSet::new();
    for _ in 0..5 {
        let client = bench.clone();
        let context = root.context();
        tasks.spawn(async move {
            client
                .trace(
                    Some(&context),
                    SpanInput::new("tool").kind("TOOL"),
                    |_| async { Ok::<_, String>(42) },
                )
                .await
                .unwrap()
        });
    }
    while let Some(result) = tasks.join_next().await {
        assert_eq!(result.unwrap(), 42)
    }
    root.set_output(json!("done"));
    root.end();
    let result = bench
        .trace(None, SpanInput::new("failed"), |_| async {
            Err::<String, _>("original app error")
        })
        .await;
    assert_eq!(result.unwrap_err(), "original app error");
    bench.shutdown().await;
    let requests = server.requests.lock().unwrap();
    assert_eq!(requests.len(), 1);
    assert!(requests[0].0.contains("POST /api/traces"));
    assert!(requests[0]
        .0
        .to_lowercase()
        .contains("authorization: bearer bench_sk_synthetic_test_key"));
    for private in [
        "4242",
        "Private Person",
        "person@example.test",
        "12345678",
        "192.168.1.2",
        "original app error",
    ] {
        assert!(!requests[0].1.contains(private), "leaked {private}")
    }
    let body: Value = serde_json::from_str(&requests[0].1).unwrap();
    let traces = body["traces"].as_array().unwrap();
    let agent = traces
        .iter()
        .find(|t| t["spans"][0]["name"] == "agent")
        .unwrap();
    let tools: Vec<_> = traces
        .iter()
        .filter(|t| t["spans"][0]["name"] == "tool")
        .collect();
    assert_eq!(tools.len(), 5);
    for tool in tools {
        assert_eq!(tool["trace_id"], agent["trace_id"]);
        assert_eq!(
            tool["spans"][0]["parent_span_id"],
            agent["spans"][0]["span_id"]
        )
    }
    let failed = traces
        .iter()
        .find(|t| t["spans"][0]["name"] == "failed")
        .unwrap();
    assert_eq!(failed["spans"][0]["status"], "error");
}

#[tokio::test]
async fn metadata_retry_queue_and_redirect_contract() {
    let server = Server::new();
    server.status.store(503, Ordering::SeqCst);
    let mut options = server.options();
    options.max_queue_size = 1;
    let bench = Bench::new(options).unwrap();
    for _ in 0..2 {
        let mut input = SpanInput::new("request").input(json!("do-not-capture"));
        input.attributes.insert("private".into(), json!("no"));
        input
            .attributes
            .insert("gen_ai.usage.input_tokens".into(), json!(42));
        input
            .attributes
            .insert("gen_ai.tool.name".into(), json!("search"));
        input
            .attributes
            .insert("bench.cost.usd".into(), json!(0.002));
        input
            .attributes
            .insert("bench.cost.source".into(), json!("reported"));
        input
            .attributes
            .insert("bench.duration_ms".into(), json!(-1));
        let mut span = bench.start_span(None, input);
        tokio::time::sleep(Duration::from_millis(5)).await;
        span.set_output(json!("no-output"));
        span.end();
    }
    assert_eq!(bench.stats().dropped, 1);
    bench.flush().await;
    {
        let requests = server.requests.lock().unwrap();
        assert_eq!(requests.len(), 2);
        assert_eq!(requests[0].1, requests[1].1);
        assert!(!requests[0].1.contains("do-not-capture"));
        assert!(!requests[0].1.contains("private"));
        assert!(requests[0].1.contains("\"gen_ai.usage.input_tokens\":42"));
        let batch: Value = serde_json::from_str(&requests[0].1).unwrap();
        let attrs = &batch["traces"][0]["spans"][0]["attributes"];
        assert_eq!(attrs["bench.cost.usd"], json!(0.002));
        assert_eq!(attrs["bench.cost.source"], "reported");
        assert_eq!(attrs["gen_ai.tool.name"], "search");
        assert!(attrs["bench.duration_ms"].as_f64().unwrap() >= 1.0);
    }
    server.status.store(302, Ordering::SeqCst);
    let mut span = bench.start_span(None, SpanInput::new("redirect"));
    span.set_output(Value::Null);
    span.end();
    bench.flush().await;
    assert_eq!(server.requests.lock().unwrap().len(), 3);
    assert_eq!(bench.stats().dropped, 3);
}

#[tokio::test]
async fn sampling_and_redactor_failure_do_not_change_app_result() {
    let mut options = Options::new("bench_sk_test", "fixture/rust", "main");
    options.sample_rate = 0.0;
    let bench = Bench::new(options).unwrap();
    assert_eq!(
        bench
            .trace(None, SpanInput::new("off"), |_| async {
                Ok::<_, String>(42)
            })
            .await
            .unwrap(),
        42
    );
    assert_eq!(bench.stats().queued, 0);
    let mut options = Options::new("bench_sk_test", "fixture/rust", "main");
    options.redact = Some(Arc::new(|_| Err("redactor failed".into())));
    let bench = Bench::new(options).unwrap();
    assert_eq!(
        bench
            .trace(None, SpanInput::new("safe"), |_| async {
                Ok::<_, String>(42)
            })
            .await
            .unwrap(),
        42
    );
    assert_eq!(bench.stats().dropped, 1);
}
