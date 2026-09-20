use crate::{Bench, SpanInput, TraceContext};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    future::Future,
    pin::Pin,
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::sync::watch;

type CallbackFuture = Pin<Box<dyn Future<Output = Result<Value, String>> + Send>>;
type Run = Arc<dyn Fn(Value, EvaluationContext) -> CallbackFuture + Send + Sync>;
type Observe = Arc<dyn Fn(EvaluationContext) -> CallbackFuture + Send + Sync>;

#[derive(Clone)]
pub struct EvaluationContext {
    pub case_id: String,
    pub trace: TraceContext,
    cancellation: watch::Receiver<bool>,
}
impl EvaluationContext {
    pub fn is_cancelled(&self) -> bool {
        *self.cancellation.borrow() || self.cancellation.has_changed().is_err()
    }
    pub async fn cancelled(&self) {
        let mut signal = self.cancellation.clone();
        if !*signal.borrow() {
            let _ = signal.changed().await;
        }
    }
}

#[derive(Clone)]
pub struct Application {
    run: Run,
    observe: Option<Observe>,
}
impl Application {
    pub fn new<F, Fut>(run: F) -> Self
    where
        F: Fn(Value, EvaluationContext) -> Fut + Send + Sync + 'static,
        Fut: Future<Output = Result<Value, String>> + Send + 'static,
    {
        Self {
            run: Arc::new(move |input, ctx| Box::pin(run(input, ctx))),
            observe: None,
        }
    }
    pub fn with_observer<F, Fut>(mut self, observe: F) -> Self
    where
        F: Fn(EvaluationContext) -> Fut + Send + Sync + 'static,
        Fut: Future<Output = Result<Value, String>> + Send + 'static,
    {
        self.observe = Some(Arc::new(move |ctx| Box::pin(observe(ctx))));
        self
    }
}

fn present<'de, D: serde::Deserializer<'de>>(d: D) -> Result<Option<Value>, D::Error> {
    Value::deserialize(d).map(Some)
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SystemCase {
    pub id: String,
    pub input: Value,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub split: String,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "present"
    )]
    pub expected_output: Option<Value>,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "present"
    )]
    pub expected_state: Option<Value>,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub business_outcome: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub required_tools: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub forbidden_tools: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_tool_calls: Option<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_model_calls: Option<usize>,
}
impl SystemCase {
    pub fn new(id: impl Into<String>, input: Value) -> Self {
        Self {
            id: id.into(),
            input,
            split: String::new(),
            expected_output: None,
            expected_state: None,
            business_outcome: String::new(),
            required_tools: vec![],
            forbidden_tools: vec![],
            max_tool_calls: None,
            max_model_calls: None,
        }
    }
}
#[derive(Clone)]
pub struct EvaluationOptions {
    pub source_revision: String,
    pub context_revision: String,
    pub cases: Vec<SystemCase>,
    pub timeout: Duration,
}
impl EvaluationOptions {
    pub fn new(
        source: impl Into<String>,
        context: impl Into<String>,
        cases: Vec<SystemCase>,
    ) -> Self {
        Self {
            source_revision: source.into(),
            context_revision: context.into(),
            cases,
            timeout: Duration::from_secs(30),
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EvaluationSummary {
    pub status: String,
    pub score: Option<f64>,
    pub passed: usize,
    pub failed: usize,
    pub errors: usize,
    pub unscored: usize,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EvaluationReport {
    pub schema_version: u8,
    pub execution_mode: String,
    pub evidence_origin: String,
    pub environment: String,
    pub source_revision: String,
    pub context_revision: String,
    pub suite_hash: String,
    pub planned_case_count: usize,
    pub coverage: Value,
    pub cases: Vec<Value>,
    pub summary: EvaluationSummary,
}
impl EvaluationReport {
    pub fn passed(&self) -> bool {
        self.summary.status == "completed"
            && self.planned_case_count > 0
            && self.cases.len() == self.planned_case_count
            && self.cases.iter().all(|c| c["status"] == "passed")
    }
}

pub(crate) struct Capture {
    active: bool,
    limited: bool,
    pending: usize,
    spans: Vec<Value>,
}
impl Capture {
    fn new() -> Self {
        Self {
            active: true,
            limited: false,
            pending: 0,
            spans: vec![],
        }
    }
    pub(crate) fn start(&mut self) {
        self.pending += 1;
    }
    pub(crate) fn finish(&mut self, row: Option<Value>) {
        self.pending -= 1;
        if self.active {
            match row {
                Some(row) if self.spans.len() < 100 => self.spans.push(row),
                _ => self.limited = true,
            }
        }
    }
    fn freeze(&mut self) -> (Vec<Value>, bool, usize) {
        self.active = false;
        (self.spans.clone(), self.limited, self.pending)
    }
}

fn validate(options: &EvaluationOptions) -> Result<(), String> {
    if options.source_revision.len() != 40
        || !options
            .source_revision
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        || options.context_revision.is_empty()
        || options.context_revision.len() > 200
    {
        return Err("Pin source and context revisions.".into());
    }
    if options.cases.is_empty()
        || options.cases.len() > 100
        || options.timeout.is_zero()
        || options.timeout > Duration::from_secs(300)
    {
        return Err("Use 1 to 100 cases and a timeout up to 300 seconds.".into());
    }
    let raw = serde_json::to_vec(&options.cases).map_err(|_| "Invalid case suite.")?;
    if raw.len() > 500000 {
        return Err("Case suite exceeds 500 KB.".into());
    }
    let mut ids = HashSet::new();
    for c in &options.cases {
        if c.id.is_empty() || c.id.len() > 100 || !ids.insert(&c.id) {
            return Err("Case IDs must be unique and nonempty.".into());
        }
        if !matches!(
            c.split.as_str(),
            "" | "capability" | "regression" | "incident" | "holdout"
        ) {
            return Err("Invalid case split.".into());
        }
        if c.business_outcome.len() > 2000
            || c.required_tools.len() + c.forbidden_tools.len() > 90
            || c.required_tools
                .iter()
                .chain(&c.forbidden_tools)
                .any(|n| n.is_empty() || n.len() > 200)
            || c.max_tool_calls.is_some_and(|n| n > 10000)
            || c.max_model_calls.is_some_and(|n| n > 10000)
        {
            return Err("Case assertions exceed limits.".into());
        }
    }
    Ok(())
}

fn score(
    c: &SystemCase,
    output: &Value,
    state: Option<&Value>,
    spans: Vec<Value>,
    mut error: Option<String>,
) -> Value {
    if !spans
        .iter()
        .any(|s| s.get("parent_span_id").is_none() && s["kind"] == "AGENT" && s["status"] == "ok")
    {
        error.get_or_insert("Application root was not recorded. No complete score.".into());
    }
    if c.expected_state.is_some() && state.is_none() {
        error.get_or_insert("Application state was not observed. No complete score.".into());
    }
    let tools: Vec<_> = spans.iter().filter(|s| s["kind"] == "TOOL").collect();
    let mut checks = vec![];
    let mut add = |id: String, passed: bool, reason: &str| {
        checks.push(json!({"id":id,"passed":passed,"reason":reason}))
    };
    if let Some(expected) = &c.expected_output {
        add(
            "expected-output".into(),
            output == expected,
            "Compare actual output with the expected outcome.",
        );
    }
    if let (Some(expected), Some(state)) = (&c.expected_state, state) {
        add(
            "expected-state".into(),
            state == expected,
            "Compare observed tool effects with the expected business state.",
        );
    }
    for name in &c.required_tools {
        add(
            format!("required-tool:{name}"),
            tools
                .iter()
                .any(|s| s["name"] == *name && s["status"] == "ok"),
            "A successful recorded tool call is required.",
        );
    }
    for name in &c.forbidden_tools {
        add(
            format!("forbidden-tool:{name}"),
            !tools.iter().any(|s| s["name"] == *name),
            "This tool must not be invoked.",
        );
    }
    if let Some(n) = c.max_tool_calls {
        add(
            "tool-call-limit".into(),
            tools.len() <= n,
            "Recorded tool calls must stay within the limit.",
        );
    }
    if let Some(n) = c.max_model_calls {
        add(
            "model-call-limit".into(),
            spans.iter().filter(|s| s["kind"] == "LLM").count() <= n,
            "Recorded model calls must stay within the limit.",
        );
    }
    let ids: Vec<_> = spans.iter().map(|s| s["span_id"].clone()).collect();
    let mut findings = vec![];
    for tool in &tools {
        if tool["status"] == "error" {
            findings.push(json!({"category":"tool","title":format!("{} failed",tool["name"].as_str().unwrap_or("Tool")),"confidence":"observed_failure","evidence_span_ids":[tool["span_id"]],"fix_brief":"Inspect this tool's contract and dependencies. Reproduce the failure and rerun incident and regression cases."}));
        }
    }
    for check in &checks {
        if check["passed"] == false {
            let category = if matches!(
                check["id"].as_str(),
                Some("expected-output" | "expected-state")
            ) {
                "quality"
            } else {
                "harness"
            };
            findings.push(json!({"category":category,"title":check["id"],"confidence":"hypothesis","evidence_span_ids":ids,"fix_brief":"Inspect the real application path, tools, state, routing and retries. Rerun unchanged incident, regression and holdout cases."}));
        }
    }
    let status = if error.is_some() {
        "error"
    } else if checks.is_empty() {
        "unscored"
    } else if checks.iter().all(|c| c["passed"] == true) {
        "passed"
    } else {
        "failed"
    };
    let mut row = json!({"id":c.id,"split":if c.split.is_empty(){"regression"}else{&c.split},"status":status,"spans":spans,"checks":checks,"findings":findings});
    if let Some(error) = error {
        row["error"] = json!(error)
    }
    row
}

impl Bench {
    /// Execute the real app against pinned cases. No report is uploaded implicitly.
    /// Async callbacks must yield and await all child work; this is not a process sandbox.
    pub async fn evaluate_system(
        &self,
        options: EvaluationOptions,
        application: Application,
    ) -> Result<EvaluationReport, String> {
        validate(&options)?;
        if self
            .inner
            .queue
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .closed
        {
            return Err("Bench is shut down.".into());
        }
        let raw =
            serde_json::to_vec(&json!({"context":options.context_revision,"cases":options.cases}))
                .map_err(|_| "Invalid suite.")?;
        let hash = format!("{:x}", Sha256::digest(&raw));
        let mut results = vec![];
        for case in &options.cases {
            let capture = Arc::new(Mutex::new(Capture::new()));
            let (cancel, signal) = watch::channel(false);
            let client = self.clone();
            let app = application.clone();
            let input = case.input.clone();
            let id = case.id.clone();
            let recorded = capture.clone();
            let mut tasks = tokio::task::JoinSet::new();
            tasks.spawn(async move {
                let seed = TraceContext {
                    client: client.inner.id.clone(),
                    trace_id: uuid::Uuid::new_v4().simple().to_string(),
                    span_id: String::new(),
                    sampled: true,
                    evaluation: Some(recorded),
                };
                let mut root = client.start_span(
                    Some(&seed),
                    SpanInput::new("system-entrypoint")
                        .kind("AGENT")
                        .input(input.clone()),
                );
                let context = EvaluationContext {
                    case_id: id,
                    trace: root.context(),
                    cancellation: signal,
                };
                let output = (app.run)(input, context.clone()).await?;
                let state = if let Some(observe) = &app.observe {
                    Some(observe(context).await?)
                } else {
                    None
                };
                root.set_output(output.clone());
                root.end();
                Ok::<_, String>((output, state))
            });
            let (mut failure, output, state, mut stopped) =
                match tokio::time::timeout(options.timeout, tasks.join_next()).await {
                    Ok(Some(Ok(Ok((output, state))))) => (None, output, state, false),
                    Ok(_) => (
                        Some("Application execution failed. Inspect recorded spans.".to_string()),
                        Value::Null,
                        None,
                        false,
                    ),
                    Err(_) => {
                        let _ = cancel.send(true);
                        tasks.abort_all();
                        (
                            Some("Application timed out or was stopped. No complete score.".into()),
                            Value::Null,
                            None,
                            true,
                        )
                    }
                };
            let _ = cancel.send(true);
            let (spans, limited, pending) =
                capture.lock().unwrap_or_else(|e| e.into_inner()).freeze();
            if limited || pending > 0 {
                failure=Some("Application evidence is incomplete. Await all tools and stay within capture limits.".into());
                stopped = stopped || pending > 0;
            }
            let mut row = score(case, &output, state.as_ref(), spans, failure);
            let filtered =
                std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| -> Result<(), String> {
                    row["case_definition"] =
                        self.safe(serde_json::to_value(case).map_err(|_| "Invalid case.")?)?;
                    row["output"] = self.safe(output)?;
                    if let Some(state) = state {
                        row["observedState"] = self.safe(state)?;
                    }
                    Ok(())
                }));
            if !matches!(filtered, Ok(Ok(()))) {
                row["status"] = json!("error");
                row["error"] = json!("Application evidence could not be redacted.");
            }
            results.push(row);
            if stopped {
                break;
            }
        }
        let mut summary = EvaluationSummary {
            status: "incomplete".into(),
            score: None,
            passed: 0,
            failed: 0,
            errors: 0,
            unscored: 0,
        };
        for row in &results {
            match row["status"].as_str() {
                Some("passed") => summary.passed += 1,
                Some("failed") => summary.failed += 1,
                Some("error") => summary.errors += 1,
                _ => summary.unscored += 1,
            }
        }
        if results.len() == options.cases.len() && summary.errors == 0 && summary.unscored == 0 {
            summary.status = "completed".into();
            summary.score = Some(100.0 * summary.passed as f64 / results.len() as f64)
        }
        Ok(EvaluationReport {
            schema_version: 1,
            execution_mode: "application_runtime".into(),
            evidence_origin: "sdk_client_reported".into(),
            environment: self
                .inner
                .options
                .environment
                .clone()
                .unwrap_or_else(|| "unspecified".into()),
            source_revision: options.source_revision,
            context_revision: options.context_revision,
            suite_hash: hash,
            planned_case_count: options.cases.len(),
            coverage: json!({"instrumentation":"explicit_spans","tool_dependencies":"application_configured","hosted_validation":false}),
            cases: results,
            summary,
        })
    }
}

impl Bench {
    /// Save client-reported evidence explicitly. This does not run a paid check.
    pub async fn publish_system_evaluation(
        &self,
        system_id: u64,
        report: &EvaluationReport,
    ) -> Result<(), String> {
        if system_id == 0 || system_id > 9007199254740991 {
            return Err("Select a system.".into());
        }
        let filtered =
            std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| -> Result<Value, String> {
                let value =
                    serde_json::to_value(report).map_err(|_| "Invalid application report.")?;
                let value = if let Some(redact) = &self.inner.options.redact {
                    redact(value)?
                } else {
                    value
                };
                Ok(crate::privacy::redact_limit(value, 0, 300))
            }))
            .map_err(|_| "Application report could not be redacted.")??;
        let body = serde_json::to_vec(&filtered).map_err(|_| "Invalid application report.")?;
        if body.len() > 500000 {
            return Err(
                "Runtime report exceeds 500 KB. Retain it locally or split the suite.".into(),
            );
        }
        let response = self
            .inner
            .http
            .post(format!(
                "{}/api/ai-systems/{system_id}/runtime-evaluations",
                self.inner.options.endpoint.trim_end_matches('/')
            ))
            .bearer_auth(&self.inner.options.api_key)
            .header("Content-Type", "application/json")
            .body(body)
            .send()
            .await
            .map_err(|_| "Could not save application results. Local results remain available.")?;
        if !response.status().is_success() {
            return Err(format!(
                "Could not save application results (HTTP {}). Local results remain available.",
                response.status().as_u16()
            ));
        }
        Ok(())
    }
}
