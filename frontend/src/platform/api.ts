export interface ErrorContract {
  code: string;
  message: string;
  retry_policy: 'never' | 'manual' | 'automatic';
  outcome: 'not_sent' | 'known' | 'unknown';
  trace_id: string;
}

export class ApiError extends Error {
  status: number;
  contract?: ErrorContract;
  constructor(message: string, status: number, contract?: ErrorContract) { super(message); this.status = status; this.contract = contract }
}

export function csrfToken(): string {
  const value = document.cookie.split(';').map(part => part.trim()).find(part => part.startsWith('ra_csrf='));
  return value ? decodeURIComponent(value.slice(8)) : '';
}

export function requestHeaders(method: string, multipart = false): Headers {
  const headers = new Headers();
  if (!multipart) headers.set('Content-Type', 'application/json');
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method.toUpperCase())) {
    const token = csrfToken();
    if (token) headers.set('X-CSRF-Token', token);
  }
  return headers;
}

export async function checkResponse(response: Response): Promise<Response> {
  if (response.ok) return response;
  if (response.status === 401) window.dispatchEvent(new Event('meeting:session-expired'));
  const body = await response.json().catch(() => null);
  const detail = body?.message ?? body?.detail;
  const message = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(item => item.msg).filter(Boolean).join('；') : detail?.message ?? detail?.error;
  throw new ApiError(message ?? (response.status === 401 ? '登录已过期，请重新登录。' : `请求失败（${response.status}）`), response.status, body?.code ? body : undefined);
}

export async function api<T>(path: string, options: { method?: string; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const method = options.method ?? 'GET';
  const multipart = options.body instanceof FormData;
  const response = await checkResponse(await fetch(`/api${path}`, {
    method, credentials: 'include', headers: requestHeaders(method, multipart),
    body: options.body === undefined ? undefined : multipart ? options.body as FormData : JSON.stringify(options.body),
    signal: options.signal,
  }));
  return response.status === 204 ? undefined as T : response.json();
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '操作未完成，请稍后重试。';
}

export function requestId(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  // getRandomValues is available on HTTP development hosts where randomUUID is not.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function websocketUrl(path: string): string {
  return `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}${path}`;
}

// An SSE event may cross network chunks, including CRLF and UTF-8 boundaries.
export async function readSSE(body: ReadableStream<Uint8Array>, onEvent: (event: string, data: Record<string, unknown>) => void): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  const consume = (block: string) => {
    let event = 'message';
    const data: string[] = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
    }
    if (data.length) onEvent(event, JSON.parse(data.join('\n')));
  };
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let boundary: RegExpExecArray | null;
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        consume(buffer.slice(0, boundary.index));
        buffer = buffer.slice(boundary.index + boundary[0].length);
      }
      if (done) { if (buffer.trim()) consume(buffer); break }
    }
  } finally { reader.releaseLock() }
}
