"""Headless Bench management. Explicit operations; no implicit retries or spending."""
from __future__ import annotations
import json
from importlib.resources import files
from urllib.parse import urlsplit, quote, urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from uuid import uuid4

class PlatformError(Exception):
    def __init__(self, status, code, message, reference=None, details=None):
        super().__init__(message)
        self.status, self.code, self.reference = status, code, reference
        self.details = details or {}
    @property
    def retryable(self):
        return self.status == 429 or self.status >= 500

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class BenchPlatform:
    """Use a key for scoped data work or a Bench OAuth credential for account setup.

    credential can be a callable supplying refreshed tokens. Calls are synchronous;
    run local application evaluation with Bench separately, then publish its report.
    """
    def __init__(self, credential=None, *, endpoint="https://api.usebench.ai", timeout=120):
        url = urlsplit(endpoint)
        if url.username or url.password or url.query or url.fragment or url.path not in ("", "/") or not url.hostname or not (url.scheme == "https" or (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1"))):
            raise ValueError("Use an HTTPS API origin or loopback HTTP origin.")
        if not isinstance(timeout, (int, float)) or not 0.1 <= timeout <= 600:
            raise ValueError("timeout must be between 0.1 and 600 seconds")
        self._endpoint, self._credential, self._timeout = endpoint.rstrip("/"), credential, timeout
        self._catalog = json.loads(files("bench_sdk").joinpath("operations.json").read_text())
        self._operations = {op["id"]: op for op in self._catalog["operations"]}
        self._opener = build_opener(_NoRedirect())

    def operations(self):
        return json.loads(json.dumps(self._catalog))

    def call(self, operation, *, path=None, query=None, body=None, form=None, files=None):
        """files is a list of {name, content}; content accepts text or bytes."""
        if operation not in self._operations:
            raise ValueError("Unknown Bench operation")
        op = self._operations[operation]
        route = op["path"]
        for key in op["path_parameters"]:
            value = (path or {}).get(key)
            if value is None or not str(value) or str(value) in (".", "..") or any(c.isspace() or c in "/?#\\%" for c in str(value)):
                raise ValueError("Invalid or missing path parameter: " + key)
            route = route.replace("{" + key + "}", quote(str(value), safe=""))
        url = self._endpoint + route
        if query:
            url += "?" + urlencode({k: str(v).lower() if isinstance(v, bool) else v for k, v in query.items() if v is not None})
        credential = None if op["auth"] == "public" else (self._credential() if callable(self._credential) else self._credential)
        headers = {"Accept": "application/json, application/x-ndjson"}
        if op["auth"] != "public":
            if not isinstance(credential, str) or not credential.strip():
                raise PlatformError(401, "unauthorized", "Provide a Bench credential or authenticate through MCP OAuth.")
            headers["Authorization"] = "Bearer " + credential
        payload = None
        if op.get("multipart"):
            if body is not None:
                raise ValueError("Use form and files for multipart operations.")
            boundary = "bench-" + uuid4().hex
            parts = []
            for key, value in (form or {}).items():
                if any(c in str(key) for c in '\r\n"'):
                    raise ValueError("Invalid form field name")
                if value is None:
                    continue
                text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
                parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{text}\r\n').encode())
            total = 0
            for file in files or []:
                name, content = file["name"], file["content"]
                if not name or any(c in name for c in '\r\n/\\"'):
                    raise ValueError("Use a file name without directories or line breaks.")
                data = content.encode() if isinstance(content, str) else bytes(content)
                total += len(data)
                if total > op.get("max_file_bytes", 4 * 1024 * 1024):
                    raise ValueError("Upload exceeds the operation file-size limit.")
                parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{op.get("file_field", "file")}"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode() + data + b"\r\n")
            payload = b"".join(parts) + (f"--{boundary}--\r\n").encode()
            headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        else:
            if files is not None or form is not None:
                raise ValueError("This operation does not accept multipart uploads.")
            if body is not None:
                payload = json.dumps(body, allow_nan=False).encode()
                headers["Content-Type"] = "application/json"
        request = Request(url, data=payload, headers=headers, method=op["method"])
        try:
            response = self._opener.open(request, timeout=self._timeout)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise PlatformError(response.status, "response_too_large", "Response exceeds 16 MB; use pagination.")
            if not 200 <= response.status < 300:
                try:
                    error = json.loads(raw).get("error", {})
                except (ValueError, AttributeError):
                    error = {}
                raise PlatformError(response.status, error.get("code", "http_" + str(response.status)), error.get("message", "Bench request failed."), error.get("reference"), error)
            if not raw or response.status == 204:
                return None
            if "ndjson" in response.headers.get("Content-Type", ""):
                events = [json.loads(line) for line in raw.splitlines() if line.strip()]
                for event in events:
                    if event.get("type") == "error":
                        raise PlatformError(200, event.get("code", "stream_error"), str(event.get("message", event.get("error", "Operation failed."))))
                return events
            return json.loads(raw)
