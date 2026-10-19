/**
 * The typed API client.
 *
 * `paths` is GENERATED from the backend's OpenAPI schema (`pnpm gen:api`). openapi-fetch uses it
 * to type-check every call: the URL must exist, path/query params must match, and `data` has the
 * exact response type. Rename a backend field and the frontend stops compiling, instead of
 * showing `undefined` to users in production.
 *
 * Auth: an access token is attached to every request. On a 401 we refresh ONCE (all concurrent
 * 401s share one refresh request: refresh tokens are single-use, so two parallel refreshes would
 * look like token theft to the backend and revoke the session) and replay the request.
 */
import createClient, { type Middleware } from 'openapi-fetch';

import { tokens } from '../auth/tokens';
import type { components, paths } from './schema';

export type Schemas = components['schemas'];

export const API_URL =
  (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000';

const AUTH_PATHS = ['/api/v1/auth/login', '/api/v1/auth/register', '/api/v1/auth/refresh'];

let refreshing: Promise<boolean> | null = null;

/** Exchange the refresh token for a new pair. Concurrent callers share one request. */
export function refreshSession(): Promise<boolean> {
  refreshing ??= (async () => {
    const refresh = tokens.refresh;
    if (!refresh) return false;
    try {
      const res = await fetch(`${API_URL}/api/v1/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!res.ok) {
        tokens.clear();
        return false;
      }
      const pair = (await res.json()) as Schemas['TokenPair'];
      tokens.set(pair.access_token, pair.refresh_token);
      return true;
    } catch {
      return false; // network error: keep tokens, the next request can try again
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

const originals = new WeakMap<Request, Request>();

const auth: Middleware = {
  onRequest({ request }) {
    if (tokens.access) request.headers.set('Authorization', `Bearer ${tokens.access}`);
    originals.set(request, request.clone()); // body can only be read once; keep a copy to replay
    return request;
  },
  async onResponse({ request, response }) {
    const path = new URL(request.url).pathname;
    if (response.status !== 401 || AUTH_PATHS.includes(path) || !tokens.refresh) return response;
    if (!(await refreshSession())) return response;
    const retry = originals.get(request) ?? request;
    retry.headers.set('Authorization', `Bearer ${tokens.access}`);
    return fetch(retry);
  },
};

export const api = createClient<paths>({
  baseUrl: API_URL,
  // Resolve fetch at call time so tests can stub globalThis.fetch.
  fetch: (input) => globalThis.fetch(input),
});
api.use(auth);
