from __future__ import annotations

import contextvars
import ipaddress
import json
import math
import random
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

_SENSITIVE = re.compile(r"authorization|cookie|password|secret|token|api.?key|email|phone|address|user.?id|(?:first|last|full).?name|card.?number", re.I)
_METADATA = re.compile(r"^(code\.(filepath|lineno)|gen_ai\.(system|operation\.name|usage\.(input_tokens|output_tokens))|bench\.(component_id|environment|prompt_version))$")
_SECRETS = re.compile(r"(?:bench_sk_|apikey_|sk-)[a-zA-Z0-9_-]{8,}|Bearer\s+[a-zA-Z0-9._~+/-]+", re.I)
_EMAIL = re.compile(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", re.I)
_PHONE = re.compile(r"\+\d[\d ()-]{8,}\d")
_IP = re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b")
_CARD = re.compile(r"\b(?:[0-9]{4}(?:[ -][0-9]{4}){3}[ -][0-9]{3}|[0-9]{4}(?:[ -][0-9]{4}){3}|[0-9]{4}[ -][0-9]{6}[ -][0-9]{5}|[0-9]{13,19})\b")
_KINDS = {"LLM", "TOOL", "CHAIN", "RETRIEVER", "AGENT", "EMBEDDING"}


def _ip(match: re.Match) -> str:
    try:
        ipaddress.IPv4Address(match[0])
        return "[REDACTED_IP]"
    except ValueError:
        return match[0]


def _card(match: re.Match) -> str:
    digits = "".join(c for c in match[0] if c.isdigit())
    candidates = [digits]
    if len(digits) == 19 and len(match[0]) > 19:
        candidates.append(digits[:16])
    for candidate in candidates:
        total = sum(n if i % 2 == 0 else n * 2 - (9 if n > 4 else 0) for i, n in enumerate(map(int, reversed(candidate))))
        if total > 0 and total % 10 == 0:
            return "[REDACTED_PAYMENT_NUMBER]"
    return match[0]


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return "[DEPTH_LIMIT]"
    if isinstance(value, str):
        # Encoded JSON must receive the same field filtering as an object.
        if value.lstrip().startswith(("{", "[")):
            try:
                return json.dumps(_redact(json.loads(value), depth + 1), ensure_ascii=False)[:16000]
            except (ValueError, RecursionError):
                pass
        value = _SECRETS.sub("[REDACTED_SECRET]", value)
        value = _EMAIL.sub("[REDACTED_EMAIL]", value)
        value = _CARD.sub(_card, _IP.sub(_ip, value))
        return _PHONE.sub("[REDACTED_PHONE]", value)[:16000]
    if isinstance(value, dict):
        return {_redact(str(k), depth + 1): "[REDACTED]" if _SENSITIVE.search(str(k)) and not (_METADATA.fullmatch(str(k)) and str(k).startswith("gen_ai.usage.") and isinstance(v, (int, float))) else _redact(v, depth + 1) for k, v in list(value.items())[:100]}
    if isinstance(value, (list, tuple)):
        return [_redact(v, depth + 1) for v in value[:100]]
    if not isinstance(value, bool) and (isinstance(value, int) or (isinstance(value, float) and math.isfinite(value) and value.is_integer())):
        normalized = str(int(value))
        if 13 <= len(normalized) <= 19:
            filtered = _CARD.sub(_card, normalized)
            return filtered if filtered != normalized else value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Span:
    """Use set_output for a request's final value; model errors are not captured."""
    def __init__(self, name: str, kind: str, input: Any, attributes: dict | None, model: str | None, component_id: int | None):
        self.name, self.kind, self.input = name, kind, input
        self.attributes, self.model, self.component_id = attributes or {}, model, component_id
        self.output: Any = None

    def set_output(self, value: Any) -> None:
        self.output = value


class Bench:
    """Thread-safe, bounded telemetry with explicit flush at lifecycle boundaries.

    trace() is a context manager usable around sync code and across await points.
    Async tasks inherit their parent's context without sharing sibling span IDs.
    """
    def __init__(self, *, api_key: str, repository: str, branch: str, system_name: str | None = None,
                 environment: str | None = None, endpoint: str = "https://api.trybench.ai",
                 capture_content: bool = False, sample_rate: float = 1, max_queue_size: int = 200,
                 timeout: float = 5, redact: Callable[[Any], Any] | None = None,
                 on_error: Callable[[str], None] | None = None,
                 transport: Callable[[str, dict[str, str], bytes, float], int] | None = None):
        if not api_key.startswith("bench_sk_") or not repository or not branch:
            raise ValueError("A Bench key, repository and branch are required.")
        parsed = urlsplit(endpoint)
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or not (parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1"))):
            raise ValueError("Use HTTPS or a loopback HTTP endpoint.")
        if environment is not None and not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", environment):
            raise ValueError("Use a short environment name.")
        if not 0 <= sample_rate <= 1 or type(max_queue_size) is not int or not 1 <= max_queue_size <= 2000 or not 0.1 <= timeout <= 30:
            raise ValueError("Sampling, queue size or timeout is out of range.")
        self._key, self._repository, self._branch = api_key, repository, branch
        self._system, self._environment = system_name or repository.rsplit("/", 1)[-1], environment
        self._endpoint, self._capture, self._sample = endpoint.rstrip("/") + "/api/traces", capture_content, sample_rate
        self._max, self._timeout, self._redactor, self._on_error = max_queue_size, timeout, redact, on_error
        self._transport = transport or self._http
        self._context: contextvars.ContextVar = contextvars.ContextVar("bench_span", default=None)
        self._lock, self._flush_lock = threading.Lock(), threading.Lock()
        self._queue: list[dict] = []
        self._closed, self._dropped = False, 0

    def _safe(self, value: Any) -> Any:
        return _redact(self._redactor(value) if self._redactor else value)

    def _report(self, message: str) -> None:
        if self._on_error:
            try:
                self._on_error(message)
            except Exception:
                pass

    @contextmanager
    def trace(self, name: str, *, kind: str = "LLM", input: Any = None, model: str | None = None,
              attributes: dict | None = None, component_id: int | None = None) -> Iterator[Span]:
        if kind not in _KINDS:
            raise ValueError("Invalid span kind.")
        parent = self._context.get()
        trace_id = parent[0] if parent else secrets.token_hex(16)
        sampled = parent[2] if parent else random.random() < self._sample
        span_id, started = secrets.token_hex(8), _now()
        token = self._context.set((trace_id, span_id, sampled))
        span, status = Span(name, kind, input, attributes, model, component_id), "error"
        try:
            yield span
            status = "ok"
        finally:
            self._context.reset(token)
            if sampled:
                self._record(span, trace_id, span_id, parent[1] if parent else None, started, status)

    def _record(self, span: Span, trace_id: str, span_id: str, parent: str | None, started: str, status: str) -> None:
        try:
            attrs = {k: v for k, v in span.attributes.items() if self._capture or _METADATA.fullmatch(k)}
            if self._environment:
                attrs["bench.environment"] = self._environment
            if span.component_id is not None:
                attrs["bench.component_id"] = span.component_id
            row = {"span_id": span_id, "name": str(self._safe(span.name))[:200], "kind": span.kind,
                   "started_at": started, "ended_at": _now(), "status": status, "attributes": self._safe(attrs)}
            if parent:
                row["parent_span_id"] = parent
            if span.model:
                row["model_name"] = str(self._safe(span.model))[:200]
            if self._capture:
                row["input_value"] = json.dumps(self._safe(span.input), ensure_ascii=False, allow_nan=False)
                row["output_value"] = json.dumps(self._safe(span.output), ensure_ascii=False, allow_nan=False)
            trace = {"trace_id": trace_id, "source": "bench_sdk", "spans": [row]}
            if len(json.dumps(trace).encode()) > 200000:
                raise ValueError("Trace too large")
            with self._lock:
                if self._closed or len(self._queue) >= self._max:
                    self._dropped += 1
                else:
                    self._queue.append(trace)
        except Exception:
            with self._lock:
                self._dropped += 1
            self._report("Trace could not be captured; dropped.")

    @staticmethod
    def _http(url: str, headers: dict[str, str], body: bytes, timeout: float) -> int:
        request = urllib.request.Request(url, body, headers, method="POST")
        try:
            with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
                return response.status
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            return status

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"queued": len(self._queue), "dropped": self._dropped}

    def flush(self) -> None:
        """Bounded network work. Delivery failures never raise into application code."""
        with self._flush_lock:
            with self._lock:
                pending, self._queue = self._queue, []
            while pending:
                batch, size = [], 0
                while pending and len(batch) < 20:
                    next_size = len(json.dumps(pending[0]).encode())
                    if size + next_size > 800000:
                        break
                    size += next_size
                    batch.append(pending.pop(0))
                body = json.dumps({"repo_full_name": self._repository, "branch": self._branch, "system_name": self._system, "capture_content": self._capture, "traces": batch}).encode()
                delivered = False
                for attempt in range(2):
                    try:
                        status = self._transport(self._endpoint, {"Content-Type": "application/json", "Authorization": "Bearer " + self._key}, body, self._timeout)
                        if 200 <= status < 300:
                            delivered = True
                            break
                        if status != 429 and status < 500:
                            break
                    except Exception:
                        pass
                    if attempt == 0:
                        time.sleep(0.25)
                if not delivered:
                    with self._lock:
                        self._dropped += len(batch)
                    self._report("Bench trace delivery failed; batch dropped.")

    async def aflush(self) -> None:
        """Flush without blocking an asyncio event loop."""
        import asyncio
        await asyncio.to_thread(self.flush)

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
        self.flush()

    def __enter__(self) -> Bench:
        return self

    def __exit__(self, *_: Any) -> None:
        self.shutdown()
