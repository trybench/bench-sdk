// Run from rust/: cargo run --example refund. No upload or model call.
use serde_json::json;
use std::sync::{Arc, Mutex};
use trybench_sdk::{Bench, EvaluationOptions, Options, SimulationSession, SpanInput, SystemCase};

#[tokio::main]
async fn main() -> Result<(), String> {
    let bench = Bench::new(Options::new(
        "bench_sk_local_example",
        "example/refunds",
        "test",
    ))?;
    let mut case = SystemCase::new(
        "refund-retry",
        json!({"initialState":{"refunds":0},"turns":["Refund order A","Retry that refund"]}),
    );
    case.expected_output = Some(json!("Refunded"));
    case.expected_state = Some(json!({"refunds":1}));
    let options = EvaluationOptions::new("a".repeat(40), "refund-policy-v1", vec![case]);
    for fixed in [false, true] {
        let client = bench.clone();
        let report = bench
            .simulate_system(options.clone(), move |_, _| {
                let writes = Arc::new(Mutex::new(0));
                let reads = writes.clone();
                let resets = writes.clone();
                let client = client.clone();
                async move {
                    Ok(SimulationSession::new(
                        move |_, ctx| {
                            let writes = writes.clone();
                            let client = client.clone();
                            async move {
                                client
                                    .trace(
                                        Some(&ctx.trace),
                                        SpanInput::new("refund").kind("TOOL"),
                                        move |_| async move {
                                            let mut count = writes.lock().unwrap();
                                            if !fixed || *count == 0 {
                                                *count += 1
                                            };
                                            Ok::<_, String>("Refunded")
                                        },
                                    )
                                    .await?;
                                Ok(json!("Refunded"))
                            }
                        },
                        move || {
                            let reads = reads.clone();
                            async move { Ok(json!({"refunds":*reads.lock().unwrap()})) }
                        },
                        move || {
                            let resets = resets.clone();
                            async move {
                                *resets.lock().unwrap() = 0;
                                Ok(())
                            }
                        },
                    ))
                }
            })
            .await?;
        assert_eq!(report.passed(), fixed);
        println!("fixed={fixed} passed={}", report.passed());
    }
    bench.shutdown().await;
    Ok(())
}
