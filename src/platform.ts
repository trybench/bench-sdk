import { operationCatalog, type OperationId } from './operations.js';

export { operationCatalog, type OperationId } from './operations.js';
export interface PlatformOptions {
  /** API key, Bench session token, or supplier of short-lived Bench OAuth tokens. */
  credential?: string | (() => string | Promise<string>);
  endpoint?: string;
  timeoutMs?: number;
  fetch?: typeof globalThis.fetch;
}
export interface PlatformFile { name: string; content: string | Uint8Array }
export interface PlatformRequest {
  path?: Record<string, string | number>;
  query?: Record<string, string | number | boolean | undefined>;
  body?: unknown;
  form?: Record<string, unknown>;
  files?: PlatformFile[];
  signal?: AbortSignal;
}
export class PlatformError extends Error {
  constructor(readonly status: number, readonly code: string, message: string, readonly reference?: string, readonly details: Record<string, unknown> = {}) {
    super(message); this.name = 'PlatformError';
  }
  get retryable(): boolean { return this.status === 429 || this.status >= 500; }
}
interface Operation {
  id: string; method: string; path: string; auth: string;
  path_parameters: Record<string, unknown>; multipart?: boolean; file_field?: string; max_file_bytes?: number;
}
const operations = new Map<string, Operation>((operationCatalog.operations as unknown as Operation[]).map(op => [op.id, op]));
const maxResponseBytes = 16 * 1024 * 1024;

/** Headless platform management, separate from the application's tracing client.
 * Every call maps to one real API endpoint. No retries or evaluation spending are
 * implicit. Model, account and repository restrictions remain server enforced.
 */
export class BenchPlatform {
  private readonly endpoint: string;
  private readonly timeout: number;
  private readonly fetcher: typeof globalThis.fetch;
  constructor(private readonly options: PlatformOptions = {}) {
    if (typeof window !== 'undefined') throw new Error('Bench platform credentials must stay on the server.');
    const endpoint = new URL(options.endpoint ?? 'https://api.usebench.ai');
    if (endpoint.username || endpoint.password || endpoint.search || endpoint.hash || endpoint.pathname !== '/' ||
      (endpoint.protocol !== 'https:' && !(endpoint.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(endpoint.hostname)))) {
      throw new Error('Use an HTTPS API origin or loopback HTTP origin.');
    }
    this.endpoint = endpoint.origin;
    this.timeout = options.timeoutMs ?? 120000;
    if (!Number.isFinite(this.timeout) || this.timeout < 100 || this.timeout > 600000) throw new Error('timeoutMs must be between 100 and 600000.');
    this.fetcher = options.fetch ?? globalThis.fetch;
  }
  /** Inspect the pinned operation names and request schemas without a network call. */
  operations() { return operationCatalog; }

  async call<T = unknown>(operation: OperationId, input: PlatformRequest = {}): Promise<T> {
    const op = operations.get(operation);
    if (!op) throw new Error(`Unknown Bench operation: ${operation}`);
    const path = op.path.replace(/\{([^}]+)\}/g, (_, key: string) => {
      const value = input.path?.[key];
      if (value === undefined || !String(value) || /[\s/?#\\%]/.test(String(value)) || ['.', '..'].includes(String(value))) throw new Error(`Invalid or missing path parameter: ${key}`);
      return encodeURIComponent(String(value));
    });
    const url = new URL(this.endpoint + path);
    for (const [key, value] of Object.entries(input.query ?? {})) if (value !== undefined) url.searchParams.set(key, String(value));
    const credential = op.auth === 'public' ? undefined : typeof this.options.credential === 'function' ? await this.options.credential() : this.options.credential;
    if (op.auth !== 'public' && !credential?.trim()) throw new PlatformError(401, 'unauthorized', 'Provide a Bench credential or authenticate through the MCP OAuth connector.');
    const headers: Record<string, string> = { Accept: 'application/json, application/x-ndjson' };
    // Public login operations must not receive an unrelated API credential.
    if (op.auth !== 'public' && credential) headers.Authorization = `Bearer ${credential}`;
    let body: BodyInit | undefined;
    if (op.multipart) {
      if (input.body !== undefined) throw new Error('Use form and files for this multipart operation.');
      const form = new FormData(); let total = 0;
      for (const [key, value] of Object.entries(input.form ?? {})) if (value !== undefined) form.append(key, typeof value === 'object' ? JSON.stringify(value) : String(value));
      for (const file of input.files ?? []) {
        if (!file.name || /[\r\n/\\]/.test(file.name)) throw new Error('Use a file name without directories or line breaks.');
        const content = typeof file.content === 'string' ? Buffer.from(file.content, 'utf8') : Buffer.from(file.content);
        total += content.length;
        if (total > (op.max_file_bytes ?? 4 * 1024 * 1024)) throw new Error('Upload exceeds the operation file-size limit.');
        form.append(op.file_field ?? 'file', new Blob([content]), file.name);
      }
      body = form;
    } else {
      if (input.files || input.form) throw new Error('This operation does not accept multipart uploads.');
      if (input.body !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(input.body); }
    }
    const timeout = AbortSignal.timeout(this.timeout);
    const response = await this.fetcher(url, { method: op.method, headers, body, redirect: 'error', signal: input.signal ? AbortSignal.any([input.signal, timeout]) : timeout });
    const reader = response.body?.getReader(); const chunks: Uint8Array[] = []; let size = 0;
    if (reader) {
      try {
        while (true) { const chunk = await reader.read(); if (chunk.done) break; size += chunk.value.length; if (size > maxResponseBytes) { await reader.cancel(); throw new PlatformError(response.status, 'response_too_large', 'Response exceeds 16 MB; use pagination.'); } chunks.push(chunk.value); }
      } finally { reader.releaseLock(); }
    }
    const text = Buffer.concat(chunks).toString('utf8');
    if (!response.ok) {
      let error: {code?: string; message?: string; reference?: string} = {};
      try { error = (JSON.parse(text) as {error?: typeof error}).error ?? {}; } catch { /* A proxy error is still an error. */ }
      throw new PlatformError(response.status, error.code ?? `http_${response.status}`, error.message ?? 'Bench request failed.', error.reference, error);
    }
    if (response.status === 204 || !text) return undefined as T;
    if (response.headers.get('content-type')?.includes('ndjson')) {
      const events = text.split('\n').filter(line => line.trim()).map(line => JSON.parse(line) as Record<string, unknown>);
      const failure = events.find(event => event.type === 'error');
      if (failure) throw new PlatformError(200, String(failure.code ?? 'stream_error'), String(failure.message ?? failure.error ?? 'Operation failed.'));
      return events as T;
    }
    return JSON.parse(text) as T;
  }
}
