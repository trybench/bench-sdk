use regex::Regex;
use serde_json::Value;
use std::{net::Ipv4Addr, sync::LazyLock};

static SENSITIVE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)authorization|cookie|password|secret|token|api.?key|email|phone|address|user.?id|(?:first|last|full).?name|card.?number").unwrap()
});
static METADATA: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(code\.(filepath|lineno)|gen_ai\.(system|provider\.name|operation\.name|request\.model|response\.model|tool\.(name|type|call\.id)|usage\.(input_tokens|output_tokens))|bench\.(component_id|environment|prompt_version|duration_ms|cost\.(usd|source|pricing_version)))$").unwrap()
});
static SECRET: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)(?:bench_sk_|apikey_|sk-)[a-zA-Z0-9_-]{8,}|Bearer\s+[a-zA-Z0-9._~+/-]+")
        .unwrap()
});
static EMAIL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+").unwrap()
});
static PHONE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\+\d[\d ()-]{8,}\d").unwrap());
static IP: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b").unwrap());
static CARD: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\b(?:[0-9]{4}(?:[ -][0-9]{4}){3}[ -][0-9]{3}|[0-9]{4}(?:[ -][0-9]{4}){3}|[0-9]{4}[ -][0-9]{6}[ -][0-9]{5}|[0-9]{13,19})\b").unwrap()
});

pub fn metadata(key: &str) -> bool {
    METADATA.is_match(key)
}
pub fn text(value: &str) -> String {
    if value.len() > 16000 {
        return "[CONTENT_LIMIT]".into();
    }
    let value = SECRET.replace_all(value, "[REDACTED_SECRET]");
    let value = EMAIL.replace_all(&value, "[REDACTED_EMAIL]");
    let value = IP.replace_all(&value, |c: &regex::Captures| {
        if c[0].parse::<Ipv4Addr>().is_ok() {
            "[REDACTED_IP]".to_owned()
        } else {
            c[0].to_owned()
        }
    });
    let value = CARD.replace_all(&value, |c: &regex::Captures| {
        let digits: Vec<u32> = c[0].chars().filter_map(|c| c.to_digit(10)).collect();
        if card_checksum(&digits)
            || (digits.len() == 19 && c[0].len() > 19 && card_checksum(&digits[..16]))
        {
            "[REDACTED_PAYMENT_NUMBER]".to_owned()
        } else {
            c[0].to_owned()
        }
    });
    PHONE
        .replace_all(&value, "[REDACTED_PHONE]")
        .chars()
        .take(16000)
        .collect()
}

fn card_checksum(digits: &[u32]) -> bool {
    let sum: u32 = digits
        .iter()
        .rev()
        .enumerate()
        .map(|(i, &n)| {
            if i % 2 == 0 {
                n
            } else {
                let d = n * 2;
                if d > 9 {
                    d - 9
                } else {
                    d
                }
            }
        })
        .sum();
    sum > 0 && sum.is_multiple_of(10)
}
pub fn redact(value: Value, depth: usize) -> Value {
    redact_limit(value, depth, 100)
}
pub(crate) fn redact_limit(value: Value, depth: usize, max_items: usize) -> Value {
    if depth > 12 {
        return Value::String("[DEPTH_LIMIT]".into());
    }
    match value {
        Value::Number(ref n) => {
            let raw = if let Some(n) = n.as_u64() {
                n.to_string()
            } else if let Some(n) = n
                .as_f64()
                .filter(|n| n.is_finite() && *n >= 0.0 && n.fract() == 0.0)
            {
                format!("{n:.0}")
            } else {
                return value;
            };
            if (13..=19).contains(&raw.len())
                && card_checksum(
                    &raw.chars()
                        .filter_map(|c| c.to_digit(10))
                        .collect::<Vec<_>>(),
                )
            {
                Value::String("[REDACTED_PAYMENT_NUMBER]".into())
            } else {
                value
            }
        }
        Value::String(s) => {
            if s.len() > 16000 {
                return Value::String("[CONTENT_LIMIT]".into());
            }
            if s.trim_start().starts_with(['{', '[']) {
                if let Ok(v) = serde_json::from_str::<Value>(&s) {
                    return Value::String(
                        redact_limit(v, depth + 1, max_items)
                            .to_string()
                            .chars()
                            .take(16000)
                            .collect(),
                    );
                }
            }
            Value::String(text(&s))
        }
        Value::Array(items) => Value::Array(
            items
                .into_iter()
                .take(max_items)
                .map(|v| redact_limit(v, depth + 1, max_items))
                .collect(),
        ),
        Value::Object(items) => Value::Object(
            items
                .into_iter()
                .take(max_items)
                .map(|(key, v)| {
                    let filtered = if SENSITIVE.is_match(&key)
                        && !(key.starts_with("gen_ai.usage.") && metadata(&key) && v.is_number())
                    {
                        Value::String("[REDACTED]".into())
                    } else {
                        redact_limit(v, depth + 1, max_items)
                    };
                    (text(&key), filtered)
                })
                .collect(),
        ),
        other => other,
    }
}
