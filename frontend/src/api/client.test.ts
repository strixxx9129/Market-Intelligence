/**
 * Token refresh handling in the API client: the part of auth that breaks silently.
 */
import { describe, expect, it } from 'vitest';

import { tokens } from '../auth/tokens';
import { json, mockFetch } from '../test/utils';
import { api, API_URL } from './client';
import { unwrap } from './errors';
import { login, logout } from './hooks';

const ME = { id: 'u1', email: 'ada@example.org', role: 'user', created_at: '2026-10-01T00:00:00Z' };
const pair = (n: number) => ({
  access_token: `access-${n}`,
  refresh_token: `refresh-${n}`,
  token_type: 'bearer',
  expires_in: 900,
});

const path = (req: Request) => new URL(req.url).pathname;

describe('auth in the API client', () => {
  it('login stores the access token in memory and the refresh token in localStorage', async () => {
    const fetch = mockFetch(async (req) => {
      expect(path(req)).toBe('/api/v1/auth/login');
      expect(await req.json()).toEqual({ email: 'ada@example.org', password: 'pw' });
      return json(pair(1));
    });
    await login('ada@example.org', 'pw');
    expect(tokens.access).toBe('access-1');
    expect(localStorage.getItem('marketlens.refresh_token')).toBe('refresh-1');
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('attaches the bearer token', async () => {
    tokens.set('access-1', 'refresh-1');
    mockFetch((req) => {
      expect(req.headers.get('Authorization')).toBe('Bearer access-1');
      return json(ME);
    });
    expect(unwrap(await api.GET('/api/v1/me')).email).toBe('ada@example.org');
  });

  it('on 401 refreshes once and replays the request with the new token', async () => {
    tokens.set('expired', 'refresh-1');
    const seen: string[] = [];
    mockFetch(async (req) => {
      seen.push(`${path(req)} ${req.headers.get('Authorization') ?? ''}`);
      if (path(req) === '/api/v1/auth/refresh') {
        expect(await req.json()).toEqual({ refresh_token: 'refresh-1' });
        return json(pair(2));
      }
      return req.headers.get('Authorization') === 'Bearer access-2' ? json(ME) : json({}, 401);
    });

    const me = unwrap(await api.GET('/api/v1/me'));
    expect(me.email).toBe('ada@example.org');
    expect(seen).toEqual([
      '/api/v1/me Bearer expired',
      '/api/v1/auth/refresh ',
      '/api/v1/me Bearer access-2',
    ]);
    expect(tokens.refresh).toBe('refresh-2'); // rotated
  });

  it('replays a POST with its body intact', async () => {
    tokens.set('expired', 'refresh-1');
    const bodies: unknown[] = [];
    mockFetch(async (req) => {
      if (path(req) === '/api/v1/auth/refresh') return json(pair(2));
      bodies.push(await req.json());
      return req.headers.get('Authorization') === 'Bearer access-2'
        ? json({ id: 1, name: 'core', symbols: [], created_at: '', updated_at: '' }, 201)
        : json({}, 401);
    });
    await api.POST('/api/v1/me/watchlists', { body: { name: 'core', symbols: [] } });
    expect(bodies).toEqual([
      { name: 'core', symbols: [] },
      { name: 'core', symbols: [] },
    ]);
  });

  it('concurrent 401s share ONE refresh (refresh tokens are single-use)', async () => {
    tokens.set('expired', 'refresh-1');
    let refreshes = 0;
    mockFetch(async (req) => {
      if (path(req) === '/api/v1/auth/refresh') {
        refreshes += 1;
        await new Promise((r) => setTimeout(r, 10));
        return json(pair(2));
      }
      return req.headers.get('Authorization') === 'Bearer access-2' ? json([]) : json({}, 401);
    });
    await Promise.all([
      api.GET('/api/v1/me/watchlists'),
      api.GET('/api/v1/me/alerts'),
      api.GET('/api/v1/me/api-keys'),
    ]);
    expect(refreshes).toBe(1);
  });

  it('a rejected refresh logs out and surfaces the 401', async () => {
    tokens.set('expired', 'refresh-stolen');
    let loggedIn = true;
    const unsubscribe = tokens.subscribe((v) => (loggedIn = v));
    mockFetch((req) =>
      path(req) === '/api/v1/auth/refresh'
        ? json({ error: { code: 'invalid_token', message: 'Refresh token reused' } }, 401)
        : json({ error: { code: 'unauthorized', message: 'Not authenticated' } }, 401),
    );
    await expect(async () => unwrap(await api.GET('/api/v1/me'))).rejects.toMatchObject({
      status: 401,
      message: 'Not authenticated',
    });
    expect(tokens.loggedIn).toBe(false);
    expect(loggedIn).toBe(false);
    unsubscribe();
  });

  it('does not try to refresh a failed login', async () => {
    const fetch = mockFetch(() =>
      json({ error: { code: 'invalid_credentials', message: 'Wrong email or password' } }, 401),
    );
    await expect(login('ada@example.org', 'nope')).rejects.toThrow('Wrong email or password');
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('logout clears tokens locally and revokes the refresh token', async () => {
    tokens.set('access-1', 'refresh-1');
    const fetch = mockFetch(async (req) => {
      expect(path(req)).toBe('/api/v1/auth/logout');
      expect(await req.json()).toEqual({ refresh_token: 'refresh-1' });
      return new Response(null, { status: 204 });
    });
    await logout();
    expect(tokens.loggedIn).toBe(false);
    expect(fetch).toHaveBeenCalledOnce();
  });

  it('uses the env-configured base URL', () => {
    expect(API_URL).toMatch(/^https?:\/\//);
  });
});
