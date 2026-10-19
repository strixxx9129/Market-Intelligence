import type { Schemas } from './client';

/** An API error with the backend's single error format: {"error": {code, message, ...}}. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;

  constructor(status: number, code: string, message: string, requestId: string | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

type Result<T> = { data?: T; error?: unknown; response: Response };

/** Turn an openapi-fetch result into data or a thrown ApiError (what TanStack Query expects). */
export function unwrap<T>({ data, error, response }: Result<T>): T {
  if (response.ok && data !== undefined) return data;
  if (response.ok) return undefined as T; // 204 No Content
  const body = (error ?? {}) as Partial<Schemas['ErrorResponse']>;
  const status = response.status;
  throw new ApiError(
    status,
    body.error?.code ?? `http_${status}`,
    body.error?.message ?? defaultMessage(status),
    body.error?.request_id ?? response.headers.get('X-Request-ID'),
  );
}

function defaultMessage(status: number): string {
  if (status === 429) return 'Too many requests. Please wait a minute and try again.';
  if (status >= 500) return 'The server had a problem. Please try again shortly.';
  return `Request failed (${status}).`;
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof TypeError) return 'Cannot reach the API. Is the backend running?';
  return error instanceof Error ? error.message : 'Something went wrong.';
}
