//! Explicit headless management of Bench, independent of tracing and local tests.
use serde_json::{json, Value};
use std::{collections::HashMap, fmt, time::Duration};

const CATALOG: &str = include_str!("operations.json");
#[derive(Debug)]
pub struct PlatformError {
    pub status: u16,
    pub code: String,
    pub message: String,
    pub reference: Option<String>,
    pub details: Value,
}
impl PlatformError {
    fn local(code: &str, message: &str) -> Self {
        Self {
            status: 0,
            code: code.into(),
            message: message.into(),
            reference: None,
            details: json!({}),
        }
    }
    pub fn retryable(&self) -> bool {
        self.status == 429 || self.status >= 500
    }
}
impl fmt::Display for PlatformError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}
impl std::error::Error for PlatformError {}

pub struct PlatformFile {
    pub name: String,
    pub content: Vec<u8>,
}
#[derive(Default)]
pub struct PlatformRequest {
    pub path: HashMap<String, String>,
    pub query: HashMap<String, String>,
    pub body: Option<Value>,
    pub form: serde_json::Map<String, Value>,
    pub files: Vec<PlatformFile>,
}

pub struct BenchPlatform {
    endpoint: String,
    credential: String,
    http: reqwest::Client,
    operations: HashMap<String, Value>,
}
impl BenchPlatform {
    /// Supply a Bench API key or short-lived Bench OAuth token. An empty
    /// credential permits public login/catalog operations only.
    pub fn new(credential: impl Into<String>, endpoint: &str) -> Result<Self, PlatformError> {
        let url = reqwest::Url::parse(endpoint)
            .map_err(|_| PlatformError::local("invalid_endpoint", "Invalid API origin."))?;
        if !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
            || url.path() != "/"
            || !(url.scheme() == "https"
                || (url.scheme() == "http"
                    && matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "[::1]"))))
        {
            return Err(PlatformError::local(
                "invalid_endpoint",
                "Use an HTTPS API origin or loopback HTTP origin.",
            ));
        }
        let catalog: Value = serde_json::from_str(CATALOG)
            .map_err(|_| PlatformError::local("catalog_error", "Invalid operation catalog."))?;
        let operations = catalog["operations"]
            .as_array()
            .ok_or_else(|| PlatformError::local("catalog_error", "Missing operations."))?
            .iter()
            .map(|op| {
                (
                    op["id"].as_str().unwrap_or_default().to_string(),
                    op.clone(),
                )
            })
            .collect();
        let http = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(120))
            .build()
            .map_err(|_| PlatformError::local("client_error", "Cannot create HTTP client."))?;
        Ok(Self {
            endpoint: endpoint.trim_end_matches('/').into(),
            credential: credential.into(),
            http,
            operations,
        })
    }
    /// Replace an expired OAuth token; never put credentials in source or logs.
    pub fn set_credential(&mut self, credential: impl Into<String>) {
        self.credential = credential.into();
    }
    pub fn operations(&self) -> Value {
        serde_json::from_str(CATALOG).expect("validated catalog")
    }
    /// One operation, one request. No retries or implicit evaluation spending.
    /// NDJSON results are returned as an event array; stream errors are errors.
    pub async fn call(
        &self,
        operation: &str,
        input: PlatformRequest,
    ) -> Result<Value, PlatformError> {
        let op = self
            .operations
            .get(operation)
            .ok_or_else(|| PlatformError::local("unknown_operation", "Unknown Bench operation."))?;
        let mut route = op["path"].as_str().unwrap_or_default().to_string();
        for key in op["path_parameters"]
            .as_object()
            .into_iter()
            .flat_map(|v| v.keys())
        {
            let value = input
                .path
                .get(key)
                .ok_or_else(|| PlatformError::local("invalid_path", "Missing path parameter."))?;
            if value.is_empty()
                || [".", ".."].contains(&value.as_str())
                || value
                    .chars()
                    .any(|c| c.is_whitespace() || "/?#\\%".contains(c))
            {
                return Err(PlatformError::local(
                    "invalid_path",
                    "Invalid path parameter.",
                ));
            }
            let mut segment = reqwest::Url::parse("https://path.invalid/").expect("static URL");
            segment.path_segments_mut().expect("base URL").push(value);
            route = route.replace(
                &format!("{{{key}}}"),
                segment.path().trim_start_matches('/'),
            );
        }
        let mut url = reqwest::Url::parse(&(self.endpoint.clone() + &route))
            .map_err(|_| PlatformError::local("invalid_path", "Invalid path."))?;
        if !input.query.is_empty() {
            url.query_pairs_mut().extend_pairs(input.query.iter());
        }
        let method =
            reqwest::Method::from_bytes(op["method"].as_str().unwrap_or_default().as_bytes())
                .map_err(|_| PlatformError::local("catalog_error", "Invalid operation method."))?;
        let mut request = self
            .http
            .request(method, url)
            .header("Accept", "application/json, application/x-ndjson");
        if op["auth"] != "public" {
            if self.credential.trim().is_empty() {
                return Err(PlatformError {
                    status: 401,
                    code: "unauthorized".into(),
                    message: "Provide a Bench credential or authenticate through MCP OAuth.".into(),
                    reference: None,
                    details: json!({}),
                });
            }
            request = request.bearer_auth(&self.credential);
        }
        if op["multipart"].as_bool().unwrap_or(false) {
            if input.body.is_some() {
                return Err(PlatformError::local(
                    "invalid_input",
                    "Use form and files for multipart operations.",
                ));
            }
            let boundary = format!("bench-{}", uuid::Uuid::new_v4());
            let mut data = Vec::new();
            for (key, value) in input.form {
                if key.contains(['\r', '\n', '"']) {
                    return Err(PlatformError::local(
                        "invalid_input",
                        "Invalid form field name.",
                    ));
                }
                let text = value
                    .as_str()
                    .map(str::to_owned)
                    .unwrap_or_else(|| value.to_string());
                data.extend_from_slice(format!("--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{text}\r\n").as_bytes());
            }
            let mut total = 0;
            let limit = op["max_file_bytes"].as_u64().unwrap_or(4 * 1024 * 1024) as usize;
            let field = op["file_field"].as_str().unwrap_or("file");
            for file in input.files {
                if file.name.is_empty() || file.name.contains(['\r', '\n', '/', '\\', '"']) {
                    return Err(PlatformError::local(
                        "invalid_input",
                        "Use a file name without directories or line breaks.",
                    ));
                }
                total += file.content.len();
                if total > limit {
                    return Err(PlatformError::local(
                        "upload_too_large",
                        "Upload exceeds operation file-size limit.",
                    ));
                }
                data.extend_from_slice(format!("--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{}\"\r\nContent-Type: application/octet-stream\r\n\r\n", file.name).as_bytes());
                data.extend_from_slice(&file.content);
                data.extend_from_slice(b"\r\n");
            }
            data.extend_from_slice(format!("--{boundary}--\r\n").as_bytes());
            request = request
                .header(
                    "Content-Type",
                    format!("multipart/form-data; boundary={boundary}"),
                )
                .body(data);
        } else {
            if !input.files.is_empty() || !input.form.is_empty() {
                return Err(PlatformError::local(
                    "invalid_input",
                    "Operation does not accept multipart uploads.",
                ));
            }
            if let Some(body) = input.body {
                request = request.json(&body);
            }
        }
        let mut response = request.send().await.map_err(|_| {
            PlatformError::local(
                "network_error",
                "Could not reach Bench; check connection and timeout.",
            )
        })?;
        let status = response.status().as_u16();
        let ndjson = response
            .headers()
            .get("Content-Type")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .contains("ndjson");
        let mut data = Vec::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| PlatformError::local("network_error", "Could not read Bench response."))?
        {
            if data.len() + chunk.len() > 16 * 1024 * 1024 {
                return Err(PlatformError::local(
                    "response_too_large",
                    "Response exceeds 16 MB; use pagination.",
                ));
            }
            data.extend_from_slice(&chunk);
        }
        if !(200..300).contains(&status) {
            let value: Value = serde_json::from_slice(&data).unwrap_or(json!({}));
            return Err(PlatformError {
                status,
                code: value["error"]["code"]
                    .as_str()
                    .map(str::to_owned)
                    .unwrap_or_else(|| format!("http_{status}")),
                message: value["error"]["message"]
                    .as_str()
                    .unwrap_or("Bench request failed.")
                    .into(),
                reference: value["error"]["reference"].as_str().map(str::to_owned),
                details: value["error"].clone(),
            });
        }
        if status == 204 || data.is_empty() {
            return Ok(Value::Null);
        }
        if ndjson {
            let mut events = Vec::new();
            for line in data
                .split(|c| *c == b'\n')
                .filter(|line| !line.iter().all(u8::is_ascii_whitespace))
            {
                let event: Value = serde_json::from_slice(line).map_err(|_| {
                    PlatformError::local("invalid_response", "Invalid Bench stream event.")
                })?;
                if event["type"] == "error" {
                    return Err(PlatformError {
                        status,
                        code: event["code"].as_str().unwrap_or("stream_error").into(),
                        message: event["message"]
                            .as_str()
                            .or_else(|| event["error"].as_str())
                            .unwrap_or("Operation failed.")
                            .into(),
                        reference: None,
                        details: json!({}),
                    });
                }
                events.push(event);
            }
            return Ok(Value::Array(events));
        }
        serde_json::from_slice(&data)
            .map_err(|_| PlatformError::local("invalid_response", "Invalid Bench JSON response."))
    }
}
