use crate::{Application, Bench, EvaluationContext, EvaluationOptions, EvaluationReport};
use serde_json::Value;
use std::{
    collections::HashMap,
    future::Future,
    pin::Pin,
    sync::{Arc, Mutex},
    time::Duration,
};

type ValueFuture = Pin<Box<dyn Future<Output = Result<Value, String>> + Send>>;
type CloseFuture = Pin<Box<dyn Future<Output = Result<(), String>> + Send>>;
type Turn = Arc<dyn Fn(Value, EvaluationContext) -> ValueFuture + Send + Sync>;
type Observe = Arc<dyn Fn() -> ValueFuture + Send + Sync>;
type Close = Arc<dyn Fn() -> CloseFuture + Send + Sync>;

pub struct SimulationSession {
    turn: Turn,
    observe: Observe,
    close: Option<Close>,
}
impl SimulationSession {
    pub fn new<T, TF, O, OF, C, CF>(turn: T, observe: O, close: C) -> Self
    where
        T: Fn(Value, EvaluationContext) -> TF + Send + Sync + 'static,
        TF: Future<Output = Result<Value, String>> + Send + 'static,
        O: Fn() -> OF + Send + Sync + 'static,
        OF: Future<Output = Result<Value, String>> + Send + 'static,
        C: Fn() -> CF + Send + Sync + 'static,
        CF: Future<Output = Result<(), String>> + Send + 'static,
    {
        Self {
            turn: Arc::new(move |value, ctx| Box::pin(turn(value, ctx))),
            observe: Arc::new(move || Box::pin(observe())),
            close: Some(Arc::new(move || Box::pin(close()))),
        }
    }
    async fn close(&mut self) -> Result<(), String> {
        if let Some(close) = self.close.take() {
            tokio::time::timeout(Duration::from_secs(5), close())
                .await
                .map_err(|_| "Session cleanup timed out.")??;
        }
        Ok(())
    }
}
impl Drop for SimulationSession {
    fn drop(&mut self) {
        if let Some(close) = self.close.take() {
            if let Ok(handle) = tokio::runtime::Handle::try_current() {
                handle.spawn(async move {
                    let _ = tokio::time::timeout(Duration::from_secs(5), close()).await;
                });
            }
        }
    }
}

impl Bench {
    /// Scripted turns keep the real app and inspect fixture state before cleanup.
    pub async fn simulate_system<F, Fut>(
        &self,
        options: EvaluationOptions,
        create_session: F,
    ) -> Result<EvaluationReport, String>
    where
        F: Fn(Value, EvaluationContext) -> Fut + Send + Sync + 'static,
        Fut: Future<Output = Result<SimulationSession, String>> + Send + 'static,
    {
        for case in &options.cases {
            let turns = case
                .input
                .get("turns")
                .and_then(Value::as_array)
                .ok_or("Provide scripted user turns.")?;
            if turns.is_empty()
                || turns.len() > 20
                || case.input.get("initialState").is_none()
                || case.expected_state.is_none()
            {
                return Err(
                    "Provide initialState, 1 to 20 turns and an expected business state.".into(),
                );
            }
        }
        let factory = Arc::new(create_session);
        let observations = Arc::new(Mutex::new(HashMap::new()));
        let observed = observations.clone();
        let app = Application::new(move |input, context| {
            let factory = factory.clone();
            let observations = observations.clone();
            async move {
                let mut session = factory(input["initialState"].clone(), context.clone()).await?;
                let outcome = async {
                    let mut reply = Value::Null;
                    for message in input["turns"].as_array().ok_or("Invalid turns.")? {
                        if context.is_cancelled() {
                            return Err("Simulation stopped.".into());
                        }
                        reply = (session.turn)(message.clone(), context.clone()).await?;
                    }
                    if context.is_cancelled() {
                        return Err("Simulation stopped.".into());
                    }
                    let state = (session.observe)().await?;
                    observations
                        .lock()
                        .unwrap_or_else(|e| e.into_inner())
                        .insert(context.case_id.clone(), state);
                    Ok::<_, String>(reply)
                }
                .await;
                let cleanup = session.close().await;
                let reply = outcome?;
                cleanup?;
                Ok(reply)
            }
        })
        .with_observer(move |context| {
            let observed = observed.clone();
            async move {
                observed
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .remove(&context.case_id)
                    .ok_or_else(|| "Missing observed state.".into())
            }
        });
        self.evaluate_system(options, app).await
    }
}
