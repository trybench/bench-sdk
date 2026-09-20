use serde_json::json;
use trybench_sdk::{Application, Bench, EvaluationOptions, Options, SpanInput, SystemCase};

#[tokio::test]
async fn actual_harness_failure_is_detected_despite_correct_output() {
    let mut options = Options::new("bench_sk_fixture", "test/refunds", "dev");
    options.sample_rate = 0.0;
    let bench = Bench::new(options).unwrap();
    let mut case = SystemCase::new("late-refund", json!({"days":45}));
    case.expected_output = Some(json!(false));
    case.forbidden_tools = vec!["issue-refund".into()];
    let options = EvaluationOptions::new("a".repeat(40), "v1", vec![case]);
    let client = bench.clone();
    let bad = bench
        .evaluate_system(
            options.clone(),
            Application::new(move |_, context| {
                let client = client.clone();
                async move {
                    client
                        .trace(
                            Some(&context.trace),
                            SpanInput::new("issue-refund").kind("TOOL"),
                            |_| async { Ok::<_, String>(true) },
                        )
                        .await?;
                    Ok(json!(false))
                }
            }),
        )
        .await
        .unwrap();
    let good = bench
        .evaluate_system(options, Application::new(|_, _| async { Ok(json!(false)) }))
        .await
        .unwrap();
    assert!(!bad.passed());
    assert!(good.passed());
    assert_eq!(bad.suite_hash, good.suite_hash);
    assert_eq!(bad.cases[0]["spans"].as_array().unwrap().len(), 2);
    assert_eq!(bad.cases[0]["findings"][0]["category"], "harness");
    assert_eq!(bench.stats().queued, 0);
}

#[tokio::test]
async fn repeated_customer_request_checks_state_before_session_reset() {
    use std::sync::{Arc, Mutex};
    use trybench_sdk::SimulationSession;
    let bench = Bench::new(Options::new("bench_sk_fixture", "test/refunds", "dev")).unwrap();
    let mut case = SystemCase::new(
        "retry",
        json!({"initialState":{"cents":0},"turns":["Refund","Try again"]}),
    );
    case.expected_output = Some(json!("Refunded"));
    case.expected_state = Some(json!({"cents":12000}));
    let options = EvaluationOptions::new("a".repeat(40), "v1", vec![case]);
    let closed = Arc::new(Mutex::new(0));
    for fixed in [false, true] {
        let client = bench.clone();
        let closed = closed.clone();
        let report = bench
            .simulate_system(options.clone(), move |_, _| {
                let state = Arc::new(Mutex::new(0));
                let writes = state.clone();
                let reads = state.clone();
                let closed = closed.clone();
                let client = client.clone();
                async move {
                    Ok(SimulationSession::new(
                        move |_, context| {
                            let writes = writes.clone();
                            let client = client.clone();
                            async move {
                                client
                                    .trace(
                                        Some(&context.trace),
                                        SpanInput::new("refund").kind("TOOL"),
                                        move |_| async move {
                                            let mut cents = writes.lock().unwrap();
                                            if !fixed || *cents == 0 {
                                                *cents += 12000
                                            }
                                            Ok::<_, String>("Refunded")
                                        },
                                    )
                                    .await?;
                                Ok(json!("Refunded"))
                            }
                        },
                        move || {
                            let reads = reads.clone();
                            async move { Ok(json!({"cents":*reads.lock().unwrap()})) }
                        },
                        move || {
                            let state = state.clone();
                            let closed = closed.clone();
                            async move {
                                *state.lock().unwrap() = 0;
                                *closed.lock().unwrap() += 1;
                                Ok(())
                            }
                        },
                    ))
                }
            })
            .await
            .unwrap();
        assert_eq!(report.passed(), fixed);
        assert_eq!(
            report.cases[0]["observedState"]["cents"],
            if fixed { 12000 } else { 24000 }
        );
    }
    assert_eq!(*closed.lock().unwrap(), 2);
}

#[tokio::test]
async fn report_publication_is_explicit_and_redacted() {
    use std::{
        io::{BufRead, BufReader, Read, Write},
        net::TcpListener,
        thread,
    };
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut reader = BufReader::new(stream.try_clone().unwrap());
        let mut size = 0;
        let mut headers = String::new();
        loop {
            let mut line = String::new();
            reader.read_line(&mut line).unwrap();
            if line == "\r\n" {
                break;
            }
            if let Some(length) = line.to_lowercase().strip_prefix("content-length:") {
                size = length.trim().parse().unwrap()
            }
            headers.push_str(&line)
        }
        let mut body = vec![0; size];
        reader.read_exact(&mut body).unwrap();
        write!(
            stream,
            "HTTP/1.1 201 Created\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        )
        .unwrap();
        (headers, String::from_utf8(body).unwrap())
    });
    let mut options = Options::new("bench_sk_fixture", "test/refunds", "dev");
    options.endpoint = format!("http://{address}");
    let bench = Bench::new(options).unwrap();
    let mut case = SystemCase::new("privacy", json!(1));
    case.expected_output = Some(json!("person@example.test"));
    let report = bench
        .evaluate_system(
            EvaluationOptions::new("a".repeat(40), "v1", vec![case]),
            Application::new(|_, _| async { Ok(json!("person@example.test")) }),
        )
        .await
        .unwrap();
    assert!(report.passed());
    assert_eq!(bench.stats().queued, 0);
    bench.publish_system_evaluation(3, &report).await.unwrap();
    let (headers, body) = server.join().unwrap();
    assert!(headers.contains("/api/ai-systems/3/runtime-evaluations"));
    assert!(!body.contains("person@example.test"));
    assert_eq!(
        serde_json::from_str::<serde_json::Value>(&body).unwrap()["planned_case_count"],
        1
    );
}

#[tokio::test]
async fn missing_assertions_and_state_are_incomplete_but_explicit_null_is_an_assertion() {
    let bench = Bench::new(Options::new("bench_sk_fixture", "test/app", "dev")).unwrap();
    for state in [false, true] {
        let mut case = SystemCase::new("one", json!(1));
        if state {
            case.expected_state = Some(json!(null));
        }
        let report = bench
            .evaluate_system(
                EvaluationOptions::new("a".repeat(40), "v1", vec![case]),
                Application::new(|_, _| async { Ok(json!(null)) }),
            )
            .await
            .unwrap();
        assert!(!report.passed());
        assert_eq!(report.summary.score, None);
    }
    let mut case = SystemCase::new("null", json!(null));
    case.expected_output = Some(json!(null));
    let report = bench
        .evaluate_system(
            EvaluationOptions::new("a".repeat(40), "v1", vec![case]),
            Application::new(|_, _| async { Ok(json!(null)) }),
        )
        .await
        .unwrap();
    assert!(report.passed());
}

#[tokio::test]
async fn timeout_stops_cases_and_cleans_simulation_session() {
    use std::sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    };
    use std::time::Duration;
    use trybench_sdk::SimulationSession;
    let bench = Bench::new(Options::new("bench_sk_fixture", "test/app", "dev")).unwrap();
    let calls = Arc::new(AtomicUsize::new(0));
    let closed = Arc::new(AtomicUsize::new(0));
    let cases = ["one", "two"]
        .into_iter()
        .map(|id| {
            let mut c = SystemCase::new(id, json!({"initialState":{},"turns":["go"]}));
            c.expected_state = Some(json!(1));
            c
        })
        .collect();
    let mut options = EvaluationOptions::new("a".repeat(40), "v1", cases);
    options.timeout = Duration::from_millis(20);
    let started = calls.clone();
    let cleanup = closed.clone();
    let report = bench
        .simulate_system(options, move |_, _| {
            let started = started.clone();
            let cleanup = cleanup.clone();
            async move {
                Ok(SimulationSession::new(
                    move |_, _| {
                        started.fetch_add(1, Ordering::SeqCst);
                        async { std::future::pending().await }
                    },
                    || async { Ok(json!(1)) },
                    move || {
                        cleanup.fetch_add(1, Ordering::SeqCst);
                        async { Ok(()) }
                    },
                ))
            }
        })
        .await
        .unwrap();
    tokio::time::timeout(Duration::from_secs(1), async {
        while closed.load(Ordering::SeqCst) == 0 {
            tokio::task::yield_now().await
        }
    })
    .await
    .unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    assert_eq!(closed.load(Ordering::SeqCst), 1);
    assert_eq!(report.planned_case_count, 2);
    assert_eq!(report.cases.len(), 1);
    assert_eq!(report.summary.score, None);
    assert!(!report.passed());
}

#[tokio::test]
async fn dropping_evaluation_signals_retained_contexts() {
    use std::sync::{Arc, Mutex};
    use std::time::Duration;
    let bench = Bench::new(Options::new("bench_sk_fixture", "test/app", "dev")).unwrap();
    let retained = Arc::new(Mutex::new(None));
    let saved = retained.clone();
    let mut case = SystemCase::new("one", json!(1));
    case.expected_output = Some(json!(1));
    let options = EvaluationOptions::new("a".repeat(40), "v1", vec![case]);
    let result = tokio::time::timeout(
        Duration::from_millis(20),
        bench.evaluate_system(
            options,
            Application::new(move |_, ctx| {
                *saved.lock().unwrap() = Some(ctx);
                async { std::future::pending().await }
            }),
        ),
    )
    .await;
    assert!(result.is_err());
    let ctx = retained.lock().unwrap().take().unwrap();
    assert!(ctx.is_cancelled());
    tokio::time::timeout(Duration::from_secs(1), ctx.cancelled())
        .await
        .unwrap();
}

#[tokio::test]
async fn capture_overflow_and_unfinished_tools_never_pass() {
    use std::sync::{Arc, Mutex};
    let bench = Bench::new(Options::new("bench_sk_fixture", "test/app", "dev")).unwrap();
    let mut case = SystemCase::new("one", json!(1));
    case.expected_output = Some(json!(1));
    let options = EvaluationOptions::new("a".repeat(40), "v1", vec![case]);
    let pending = Arc::new(Mutex::new(None));
    let retained = pending.clone();
    let client = bench.clone();
    let report = bench
        .evaluate_system(
            options.clone(),
            Application::new(move |_, ctx| {
                *retained.lock().unwrap() = Some(
                    client.start_span(Some(&ctx.trace), SpanInput::new("unfinished").kind("TOOL")),
                );
                async { Ok(json!(1)) }
            }),
        )
        .await
        .unwrap();
    drop(pending.lock().unwrap().take());
    assert!(!report.passed());
    assert_eq!(report.summary.score, None);
    let client = bench.clone();
    let report = bench
        .evaluate_system(
            options,
            Application::new(move |_, ctx| {
                let client = client.clone();
                async move {
                    for _ in 0..100 {
                        client
                            .trace(
                                Some(&ctx.trace),
                                SpanInput::new("tool").kind("TOOL"),
                                |_| async { Ok::<_, String>(1) },
                            )
                            .await?;
                    }
                    Ok(json!(1))
                }
            }),
        )
        .await
        .unwrap();
    assert!(!report.passed());
    assert_eq!(report.summary.score, None);
    assert_eq!(bench.stats().queued, 0);
}

#[tokio::test]
async fn unrecordable_successful_tool_cannot_produce_a_passing_evaluation() {
    struct Unrecordable;
    impl serde::Serialize for Unrecordable {
        fn serialize<S: serde::Serializer>(&self, _: S) -> Result<S::Ok, S::Error> {
            Err(serde::ser::Error::custom("unavailable"))
        }
    }
    let bench = Bench::new(Options::new("bench_sk_fixture", "test/app", "dev")).unwrap();
    let client = bench.clone();
    let mut case = SystemCase::new("one", json!(1));
    case.expected_output = Some(json!("ok"));
    let report = bench
        .evaluate_system(
            EvaluationOptions::new("a".repeat(40), "v1", vec![case]),
            Application::new(move |_, ctx| {
                let client = client.clone();
                async move {
                    client
                        .trace(
                            Some(&ctx.trace),
                            SpanInput::new("tool").kind("TOOL"),
                            |_| async { Ok::<_, String>(Unrecordable) },
                        )
                        .await?;
                    Ok(json!("ok"))
                }
            }),
        )
        .await
        .unwrap();
    assert!(!report.passed());
    assert_eq!(report.summary.score, None);
}
