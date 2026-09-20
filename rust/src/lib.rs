//! Bounded server tracing for Bench. Capture never runs paid evaluations.
//! Call `flush().await` at a request or shutdown boundary to deliver events.
mod privacy;

use serde::Serialize;
use serde_json::{json, Map, Value};
use std::{
    collections::VecDeque,
    future::Future,
    sync::{Arc, Mutex},
    time::Duration,
};
use uuid::Uuid;

pub type Redactor = Arc<dyn Fn(Value) -> Result<Value, String> + Send + Sync>;
pub struct Options {
    pub api_key: String,
    pub repository: String,
    pub branch: String,
    pub system_name: Option<String>,
    pub environment: Option<String>,
    pub endpoint: String,
    pub capture_content: bool,
    pub sample_rate: f64,
    pub max_queue_size: usize,
    pub timeout: Duration,
    pub redact: Option<Redactor>,
}
impl Options {
    pub fn new(
        api_key: impl Into<String>,
        repository: impl Into<String>,
        branch: impl Into<String>,
    ) -> Self {
        Self {
            api_key: api_key.into(),
            repository: repository.into(),
            branch: branch.into(),
            system_name: None,
            environment: None,
            endpoint: "https://api.trybench.ai".into(),
            capture_content: false,
            sample_rate: 1.0,
            max_queue_size: 200,
            timeout: Duration::from_secs(5),
            redact: None,
        }
    }
}
#[derive(Clone)]
pub struct TraceContext {
    client: String,
    trace_id: String,
    span_id: String,
    sampled: bool,
}
#[derive(Clone, Default)]
pub struct SpanInput {
    pub name: String,
    pub kind: String,
    pub model: Option<String>,
    pub component_id: Option<i64>,
    pub input: Option<Value>,
    pub attributes: Map<String, Value>,
}
impl SpanInput {
    pub fn new(name: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            kind: "LLM".into(),
            ..Self::default()
        }
    }
    pub fn kind(mut self, kind: impl Into<String>) -> Self {
        self.kind = kind.into();
        self
    }
    pub fn input(mut self, input: Value) -> Self {
        self.input = Some(input);
        self
    }
}
#[derive(Debug, Clone, Copy, Default)]
pub struct Stats {
    pub queued: usize,
    pub dropped: usize,
}
struct Queue {
    items: VecDeque<Value>,
    dropped: usize,
    closed: bool,
}
struct Inner {
    id: String,
    options: Options,
    http: reqwest::Client,
    queue: Mutex<Queue>,
    sending: tokio::sync::Mutex<()>,
}
#[derive(Clone)]
pub struct Bench {
    inner: Arc<Inner>,
}

impl Bench {
    pub fn new(options: Options) -> Result<Self, String> {
        if !options.api_key.starts_with("bench_sk_")
            || options.repository.is_empty()
            || options.branch.is_empty()
        {
            return Err("A Bench key, repository and branch are required.".into());
        }
        let url = reqwest::Url::parse(&options.endpoint).map_err(|_| "Invalid endpoint.")?;
        if url.host_str().is_none()
            || !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
            || !(url.scheme() == "https"
                || (url.scheme() == "http"
                    && matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "[::1]"))))
        {
            return Err("Use HTTPS or a loopback HTTP endpoint.".into());
        }
        if let Some(env) = &options.environment {
            if env.is_empty()
                || env.len() > 64
                || !env
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
            {
                return Err("Invalid environment.".into());
            }
        }
        if !options.sample_rate.is_finite()
            || !(0.0..=1.0).contains(&options.sample_rate)
            || !(1..=2000).contains(&options.max_queue_size)
            || options.timeout < Duration::from_millis(100)
            || options.timeout > Duration::from_secs(30)
        {
            return Err("Sampling, queue size or timeout is out of range.".into());
        }
        let http = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(options.timeout)
            .build()
            .map_err(|_| "Could not create HTTP client.")?;
        Ok(Self {
            inner: Arc::new(Inner {
                id: Uuid::new_v4().simple().to_string(),
                options,
                http,
                queue: Mutex::new(Queue {
                    items: VecDeque::new(),
                    dropped: 0,
                    closed: false,
                }),
                sending: tokio::sync::Mutex::new(()),
            }),
        })
    }
    pub fn start_span(&self, parent: Option<&TraceContext>, input: SpanInput) -> Span {
        let parent = parent.filter(|p| p.client == self.inner.id);
        let random = Uuid::new_v4();
        let bits = u64::from_be_bytes(random.as_bytes()[..8].try_into().unwrap());
        let context = TraceContext {
            client: self.inner.id.clone(),
            trace_id: parent
                .map(|p| p.trace_id.clone())
                .unwrap_or_else(|| random.simple().to_string()),
            span_id: Uuid::new_v4().simple().to_string()[..16].to_owned(),
            sampled: parent.map(|p| p.sampled).unwrap_or(
                self.inner.options.sample_rate >= 1.0
                    || (bits as f64) / (u64::MAX as f64) < self.inner.options.sample_rate,
            ),
        };
        Span {
            bench: self.clone(),
            input,
            context,
            parent: parent.map(|p| p.span_id.clone()),
            started: timestamp(),
            output: None,
            status: "error",
            ended: false,
        }
    }
    pub async fn trace<T, E, F, Fut>(
        &self,
        parent: Option<&TraceContext>,
        input: SpanInput,
        run: F,
    ) -> Result<T, E>
    where
        T: Serialize,
        F: FnOnce(TraceContext) -> Fut,
        Fut: Future<Output = Result<T, E>>,
    {
        let mut span = self.start_span(parent, input);
        let result = run(span.context());
        match result.await {
            Ok(value) => {
                match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    serde_json::to_value(&value)
                })) {
                    Ok(Ok(output)) => span.set_output(output),
                    _ => {
                        span.ended = true;
                        self.drop_events(1)
                    }
                };
                Ok(value)
            }
            Err(error) => Err(error),
        }
    }
    fn safe(&self, value: Value) -> Result<Value, String> {
        let value = if let Some(redact) = &self.inner.options.redact {
            redact(value)?
        } else {
            value
        };
        Ok(privacy::redact(value, 0))
    }
    fn drop_events(&self, n: usize) {
        self.inner
            .queue
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .dropped += n
    }
    fn capture(&self, span: &Span) -> Result<(), String> {
        if !span.context.sampled {
            return Ok(());
        }
        if !matches!(
            span.input.kind.as_str(),
            "LLM" | "TOOL" | "CHAIN" | "AGENT" | "RETRIEVER" | "EMBEDDING"
        ) {
            return Err("Invalid span kind.".into());
        }
        let mut attrs: Map<String, Value> = span
            .input
            .attributes
            .iter()
            .filter(|(k, _)| self.inner.options.capture_content || privacy::metadata(k))
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        if let Some(env) = &self.inner.options.environment {
            attrs.insert("bench.environment".into(), json!(env));
        }
        if let Some(id) = span.input.component_id {
            attrs.insert("bench.component_id".into(), json!(id));
        }
        let name = self.safe(json!(span.input.name))?;
        let name = name
            .as_str()
            .filter(|s| !s.is_empty())
            .ok_or("Invalid name.")?;
        let mut row = json!({"span_id":span.context.span_id,"name":name.chars().take(200).collect::<String>(),"kind":span.input.kind,"started_at":span.started,"ended_at":timestamp(),"status":span.status,"attributes":self.safe(Value::Object(attrs))?});
        if let Some(parent) = &span.parent {
            row["parent_span_id"] = json!(parent)
        }
        if let Some(model) = &span.input.model {
            row["model_name"] = self.safe(json!(model))?
        }
        if self.inner.options.capture_content {
            row["input_value"] = json!(self
                .safe(span.input.input.clone().unwrap_or(Value::Null))?
                .to_string());
            row["output_value"] = json!(self
                .safe(span.output.clone().unwrap_or(Value::Null))?
                .to_string())
        }
        let trace = json!({"trace_id":span.context.trace_id,"source":"bench_sdk","spans":[row]});
        if trace.to_string().len() > 200000 {
            return Err("Trace too large.".into());
        }
        let mut queue = self.inner.queue.lock().unwrap_or_else(|e| e.into_inner());
        if queue.closed || queue.items.len() >= self.inner.options.max_queue_size {
            queue.dropped += 1
        } else {
            queue.items.push_back(trace)
        };
        Ok(())
    }
    pub fn stats(&self) -> Stats {
        let q = self.inner.queue.lock().unwrap_or_else(|e| e.into_inner());
        Stats {
            queued: q.items.len(),
            dropped: q.dropped,
        }
    }
    pub async fn flush(&self) {
        let _sending = self.inner.sending.lock().await;
        let mut pending = {
            let mut q = self.inner.queue.lock().unwrap_or_else(|e| e.into_inner());
            std::mem::take(&mut q.items)
        };
        while !pending.is_empty() {
            let mut batch = Vec::new();
            let mut size = 0;
            while let Some(next) = pending.front() {
                let bytes = next.to_string().len();
                if batch.len() >= 20 || size + bytes > 800000 {
                    break;
                }
                size += bytes;
                batch.push(pending.pop_front().unwrap());
            }
            let body=json!({"repo_full_name":self.inner.options.repository,"branch":self.inner.options.branch,"system_name":self.inner.options.system_name.as_deref().unwrap_or_else(||self.inner.options.repository.rsplit('/').next().unwrap_or("app")),"capture_content":self.inner.options.capture_content,"traces":batch}).to_string();
            let mut delivered = false;
            for attempt in 0..2 {
                let result = self
                    .inner
                    .http
                    .post(format!(
                        "{}/api/traces",
                        self.inner.options.endpoint.trim_end_matches('/')
                    ))
                    .bearer_auth(&self.inner.options.api_key)
                    .header("Content-Type", "application/json")
                    .body(body.clone())
                    .send()
                    .await;
                if let Ok(response) = result {
                    let status = response.status();
                    if status.is_success() {
                        delivered = true;
                        break;
                    }
                    if status.as_u16() != 429 && !status.is_server_error() {
                        break;
                    }
                }
                if attempt == 0 {
                    tokio::time::sleep(Duration::from_millis(250)).await
                }
            }
            if !delivered {
                self.drop_events(batch.len())
            }
        }
    }
    pub async fn shutdown(&self) {
        self.inner
            .queue
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .closed = true;
        self.flush().await
    }
}

/// An unfinished or canceled span records an error when dropped. No error text is captured.
pub struct Span {
    bench: Bench,
    input: SpanInput,
    context: TraceContext,
    parent: Option<String>,
    started: String,
    output: Option<Value>,
    status: &'static str,
    ended: bool,
}
impl Span {
    pub fn context(&self) -> TraceContext {
        self.context.clone()
    }
    pub fn set_output(&mut self, value: Value) {
        self.output = Some(value);
        self.status = "ok"
    }
    pub fn set_error(&mut self) {
        self.status = "error"
    }
    pub fn end(&mut self) {
        if !self.ended {
            self.ended = true;
            if !matches!(
                std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| self.bench.capture(self))),
                Ok(Ok(()))
            ) {
                self.bench.drop_events(1)
            }
        }
    }
}
impl Drop for Span {
    fn drop(&mut self) {
        self.end()
    }
}

fn timestamp() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true)
}
