/**
 * The single fetch wrapper every request goes through.
 *
 * ## Why session cookies rather than a token
 *
 * The session cookie is HttpOnly, so a cross-site script cannot read it. A JWT
 * in `localStorage` is readable by any script that gets onto the page, and on a
 * health application that is a patient-record disclosure rather than an
 * inconvenience. The cost is CSRF handling, which is what `ensureCsrfToken`
 * does.
 *
 * ## Errors
 *
 * The server returns one envelope for every failure (see
 * `utils/exceptions/handlers.py`), so this throws one error type with the code
 * and message already extracted. Callers never parse a response body.
 */

import type { ApiError } from '../../types';

/** Same origin in production; Vite's proxy or an explicit base in development. */
const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '/api/v1').replace(/\/$/, '');

const CSRF_COOKIE = 'csrftoken';

export class ApiRequestError extends Error {
  readonly status: number;
  readonly code: string;
  /** Per-field messages from a serializer, when the failure was validation. */
  readonly fields: Record<string, string[]>;

  constructor(status: number, code: string, message: string, fields: Record<string, string[]> = {}) {
    super(message);
    this.name = 'ApiRequestError';
    this.status = status;
    this.code = code;
    this.fields = fields;
  }

  get isUnauthenticated(): boolean {
    return this.status === 401 || this.status === 403;
  }
}

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(^|;\\s*)${name}=([^;]*)`));
  return match ? decodeURIComponent(match[2]) : null;
}

/**
 * Make sure a CSRF cookie exists before the first unsafe request.
 *
 * Django only sets it on a view that asks for it, so a freshly loaded SPA whose
 * first action is a POST would otherwise be rejected.
 */
export async function ensureCsrfToken(): Promise<void> {
  if (readCookie(CSRF_COOKIE)) return;
  await fetch(`${BASE_URL}/auth/csrf/`, { credentials: 'include' });
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  body?: unknown;
  /** Query parameters; undefined and empty-string values are dropped. */
  params?: Record<string, string | number | boolean | undefined>;
  signal?: AbortSignal;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, params, signal } = options;

  const url = new URL(`${BASE_URL}${path}`, window.location.origin);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== '') url.searchParams.set(key, String(value));
    }
  }

  const unsafe = method !== 'GET';
  if (unsafe) await ensureCsrfToken();

  const headers: Record<string, string> = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (unsafe) {
    const token = readCookie(CSRF_COOKIE);
    if (token) headers['X-CSRFToken'] = token;
  }

  const response = await fetch(url.toString(), {
    method,
    headers,
    // Without this the browser sends no session cookie and every request looks
    // anonymous - the most confusing possible failure mode.
    credentials: 'include',
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload = text ? (JSON.parse(text) as unknown) : null;

  if (!response.ok) {
    const envelope = payload as ApiError | null;
    throw new ApiRequestError(
      response.status,
      envelope?.error?.code ?? 'error',
      envelope?.error?.message ?? `Request failed (${response.status})`,
      envelope?.error?.detail ?? {},
    );
  }

  return payload as T;
}
