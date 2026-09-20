"""Application checks share the versioned Bench report contract, without network work."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

_ALIASES = {"expected_output": "expectedOutput", "expected_state": "expectedState", "business_outcome": "businessOutcome",
            "required_tools": "requiredTools", "forbidden_tools": "forbiddenTools", "max_tool_calls": "maxToolCalls", "max_model_calls": "maxModelCalls"}


def snapshot(value: Any) -> Any:
    return json.loads(json.dumps(value, allow_nan=False))


def equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    return a == b


@dataclass(frozen=True)
class EvaluationContext:
    case_id: str
    signal: threading.Event
    deadline: float
    turn_index: int | None = None

    @property
    def cancelled(self) -> bool:
        return self.signal.is_set() or time.monotonic() >= self.deadline

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError()


class Capture:
    def __init__(self):
        self.lock = threading.Lock()
        self.active, self.limited, self.pending = True, False, 0
        self.spans: list[dict] = []

    def start(self):
        with self.lock:
            self.pending += 1

    def finish(self, row):
        with self.lock:
            self.pending -= 1
            if self.active:
                if row is None or len(self.spans) >= 100:
                    self.limited = True
                else:
                    self.spans.append(row)

    def freeze(self):
        with self.lock:
            self.active = False
            return list(self.spans), self.limited, self.pending


def validate(source_revision, context_revision, cases, timeout):
    if not isinstance(source_revision, str) or not re.fullmatch(r"[a-f0-9]{40}", source_revision) or not isinstance(context_revision, str) or not 1 <= len(context_revision) <= 200:
        raise ValueError("Pin a full source revision and a context revision.")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 100 or isinstance(timeout, bool) or not 0 < timeout <= 300:
        raise ValueError("Use 1 to 100 cases and a timeout up to 300 seconds.")
    pinned = snapshot(cases)
    if len(json.dumps(pinned).encode()) > 500000:
        raise ValueError("Case suite exceeds 500 KB.")
    ids = set()
    for case in pinned:
        if not isinstance(case, dict):
            raise ValueError("A case must be an object.")
        for key, alias in _ALIASES.items():
            if key in case:
                if alias in case:
                    raise ValueError("Use one spelling per case field.")
                case[alias] = case.pop(key)
        name = case.get("id")
        if not isinstance(name, str) or not 1 <= len(name) <= 100 or name in ids:
            raise ValueError("Case IDs must be unique and nonempty.")
        ids.add(name)
        if "input" not in case or case.get("split", "regression") not in ("capability", "incident", "regression", "holdout"):
            raise ValueError("Each case needs an input and valid split.")
        if "businessOutcome" in case and (not isinstance(case["businessOutcome"], str) or not 1 <= len(case["businessOutcome"]) <= 2000):
            raise ValueError("Provide a bounded business outcome.")
        count = 0
        for key in ("requiredTools", "forbiddenTools"):
            names = case.get(key, [])
            if not isinstance(names, list) or any(not isinstance(n, str) or not 1 <= len(n) <= 200 for n in names):
                raise ValueError("Invalid tool assertions.")
            count += len(names)
        if count > 90:
            raise ValueError("Use at most 90 tool assertions per case.")
        for key in ("maxToolCalls", "maxModelCalls"):
            if key in case and (type(case[key]) is not int or not 0 <= case[key] <= 10000):
                raise ValueError("Call limits must be nonnegative integers up to 10000.")
    return pinned


async def invoke(fn: Callable, *args):
    # Sync callbacks run off-loop. Cancellation cannot kill a Python thread.
    result = fn(*args) if inspect.iscoroutinefunction(fn) else await asyncio.to_thread(fn, *args)
    return await result if inspect.isawaitable(result) else result


def score(case, output, state, observed, spans, error):
    if not any(not s.get("parent_span_id") and s["kind"] == "AGENT" and s["status"] == "ok" for s in spans):
        error = error or "Application root was not recorded. No complete score."
    if "expectedState" in case and not observed:
        error = error or "Application state was not observed. No complete score."
    checks, findings = [], []
    def check(id, passed, reason):
        checks.append(dict(id=id, passed=passed, reason=reason))
    if "expectedOutput" in case:
        check("expected-output", equal(output, case["expectedOutput"]), "Compare actual output with the expected outcome.")
    if "expectedState" in case and observed:
        check("expected-state", equal(state, case["expectedState"]), "Compare observed tool effects with the expected business state.")
    tools = [s for s in spans if s["kind"] == "TOOL"]
    for name in case.get("requiredTools", []):
        check("required-tool:" + name, any(s["name"] == name and s["status"] == "ok" for s in tools), "A successful recorded tool call is required.")
    for name in case.get("forbiddenTools", []):
        check("forbidden-tool:" + name, not any(s["name"] == name for s in tools), "This tool must not be invoked.")
    for field, kind, label in (("maxToolCalls", "TOOL", "tool"), ("maxModelCalls", "LLM", "model")):
        if field in case:
            count = sum(s["kind"] == kind for s in spans)
            check(label + "-call-limit", count <= case[field], f"{count} recorded calls; limit {case[field]}.")
    for tool in tools:
        if tool["status"] == "error":
            findings.append(dict(category="tool", title=tool["name"] + " failed", evidence_span_ids=[tool["span_id"]], confidence="observed_failure", fix_brief="Inspect this tool's contract and dependencies. Reproduce the failure, fix it, and rerun incident and regression cases."))
    for item in checks:
        if not item["passed"]:
            findings.append(dict(category="quality" if item["id"] in ("expected-output", "expected-state") else "harness", title=item["id"], evidence_span_ids=[s["span_id"] for s in spans], confidence="hypothesis", fix_brief="Inspect the real application path, tools, state, routing and retries. Change one suspected cause and rerun unchanged incident, regression and holdout cases."))
    status = "error" if error else "unscored" if not checks else "passed" if all(c["passed"] for c in checks) else "failed"
    return dict(id=case["id"], split=case.get("split", "regression"), status=status, checks=checks, findings=findings, spans=spans, **({"error": error} if error else {}))


async def evaluate(bench, *, source_revision: str, context_revision: str, cases: list[dict], run: Callable,
                   observe: Callable | None = None, timeout: float = 30, cancel_event: threading.Event | None = None) -> dict:
    pinned = validate(source_revision, context_revision, cases, timeout)
    if not callable(run) or (observe is not None and not callable(observe)):
        raise ValueError("A real application entry point is required.")
    if bench._closed or bench._evaluation.get() is not None:
        raise ValueError("Use an open client outside another application evaluation.")
    results = []
    for case in pinned:
        if cancel_event and cancel_event.is_set():
            break
        capture = Capture()
        context = EvaluationContext(case["id"], threading.Event(), time.monotonic() + timeout)
        async def execute():
            own = bench._evaluation.set(capture)
            parent = bench._context.set(None)
            try:
                with bench.trace("system-entrypoint", kind="AGENT", input=snapshot(case["input"])) as root:
                    output = snapshot(await invoke(run, snapshot(case["input"]), context))
                    state = snapshot(await invoke(observe, context)) if observe else None
                    root.set_output(output)
                return output, state
            finally:
                bench._context.reset(parent)
                bench._evaluation.reset(own)
        async def cancellation():
            while not (cancel_event and cancel_event.is_set()):
                await asyncio.sleep(0.01)
        task, cancel = asyncio.create_task(execute()), asyncio.create_task(cancellation())
        error, output, state, observed, stopped = None, None, None, False, False
        try:
            done, _ = await asyncio.wait([task, cancel], timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if task not in done or context.cancelled or (cancel_event and cancel_event.is_set()):
                stopped = True
                error = "Application timed out or was stopped. No complete score."
            else:
                try:
                    output, state = task.result()
                    observed = observe is not None
                except (Exception, asyncio.CancelledError):
                    error = "Application execution failed. Inspect recorded spans."
        finally:
            context.signal.set()
            cancel.cancel()
            if not task.done():
                task.cancel()
            # Consume eventual exceptions without waiting forever for uncooperative code.
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            spans, limited, pending = capture.freeze()
        if limited or pending:
            error = "Application evidence is incomplete. Await all tools and stay within capture limits."
            stopped = stopped or bool(pending)
        result = score(case, output, state, observed, spans, error)
        try:
            result["case_definition"] = bench._safe(case)
            result["output"] = bench._safe(output)
            if observed:
                result["observedState"] = bench._safe(state)
        except Exception:
            result.update(status="error", error="Application evidence could not be redacted.")
        results.append(result)
        if stopped:
            break
    counts = {"passed": 0, "failed": 0, "errors": 0, "unscored": 0}
    for case in results:
        counts["errors" if case["status"] == "error" else case["status"]] += 1
    complete = len(results) == len(pinned) and not counts["errors"] and not counts["unscored"]
    digest = hashlib.sha256(json.dumps(dict(context=context_revision, cases=pinned), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return dict(schema_version=1, execution_mode="application_runtime", evidence_origin="sdk_client_reported",
                environment=bench._environment or "unspecified", source_revision=source_revision, context_revision=context_revision,
                suite_hash=digest, planned_case_count=len(pinned), coverage=dict(instrumentation="explicit_spans", tool_dependencies="application_configured", hosted_validation=False),
                cases=results, summary=dict(status="completed" if complete else "incomplete", score=100 * counts["passed"] / len(pinned) if complete else None, **counts))


async def simulate(bench, *, create_session: Callable, **options) -> dict:
    if not callable(create_session):
        raise ValueError("An application session factory is required.")
    cases = snapshot(options["cases"])
    for case in cases:
        data = case.get("input", {})
        if not isinstance(data, dict) or not isinstance(data.get("turns"), list) or not 1 <= len(data["turns"]) <= 20:
            raise ValueError("A simulation requires 1 to 20 scripted user turns.")
        if "expected_state" not in case and "expectedState" not in case:
            raise ValueError("A simulation requires the expected business state.")
        if "initial_state" in data:
            if "initialState" in data:
                raise ValueError("Use one spelling for initial state.")
            data["initialState"] = data.pop("initial_state")
        if "initialState" not in data:
            raise ValueError("Provide initial_state for a fresh simulation session.")
    observations = {}
    async def run(data, context):
        session = await invoke(create_session, data["initialState"], context)
        try:
            if not all(callable(getattr(session, method, None)) for method in ("turn", "observe", "close")):
                raise ValueError("A session must provide turn, observe and close.")
            reply = None
            for index, message in enumerate(data["turns"]):
                context.raise_if_cancelled()
                turn = EvaluationContext(context.case_id, context.signal, context.deadline, index)
                reply = await invoke(session.turn, message, turn)
            context.raise_if_cancelled()
            observations[context.case_id] = snapshot(await invoke(session.observe))
            return reply
        finally:
            if callable(getattr(session, "close", None)):
                await invoke(session.close)
    def observe(context):
        return observations.pop(context.case_id)
    return await evaluate(bench, **{**options, "cases": cases}, run=run, observe=observe)
