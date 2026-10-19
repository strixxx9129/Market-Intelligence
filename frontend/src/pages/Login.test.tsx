/** The auth flow through the real pages: log in → account page; protected page → login. */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { tokens } from '../auth/tokens';
import { json, mockFetch, renderRoutes } from '../test/utils';
import Account from './Account';
import Login from './Login';
import Register from './Register';

const ME = { id: 'u1', email: 'ada@example.org', role: 'user', created_at: '2026-10-01T00:00:00Z' };
const PAIR = { access_token: 'a1', refresh_token: 'r1', token_type: 'bearer', expires_in: 900 };

const routes = [
  { path: '/login', element: <Login /> },
  { path: '/register', element: <Register /> },
  { path: '/account', element: <Account /> },
];

function backend(opts: { password?: string } = {}) {
  return mockFetch(async (req) => {
    const path = new URL(req.url).pathname;
    if (path === '/api/v1/auth/login') {
      const body = (await req.json()) as { password: string };
      return body.password === (opts.password ?? 'correct horse battery')
        ? json(PAIR)
        : json({ error: { code: 'invalid_credentials', message: 'Wrong email or password' } }, 401);
    }
    if (path === '/api/v1/auth/register') return json(ME, 201);
    if (req.headers.get('Authorization') !== 'Bearer a1') return json({}, 401);
    if (path === '/api/v1/me') return json(ME);
    if (path === '/api/v1/assets') return json({ data: [] });
    return json([]); // watchlists, alerts, api-keys
  });
}

describe('auth flow', () => {
  it('logs in and lands on the account page', async () => {
    backend();
    const user = userEvent.setup();
    const { router } = renderRoutes(routes, '/login');

    await user.type(screen.getByLabelText('Email'), 'ada@example.org');
    await user.type(screen.getByLabelText('Password'), 'correct horse battery');
    await user.click(screen.getByRole('button', { name: 'Log in' }));

    await waitFor(() => expect(router.state.location.pathname).toBe('/account'));
    expect(await screen.findByText(/ada@example\.org/)).toBeInTheDocument();
    expect(tokens.access).toBe('a1');
    expect(await screen.findByText('No watchlists yet')).toBeInTheDocument(); // its own query
  });

  it('shows the server’s message on wrong credentials and stays logged out', async () => {
    backend();
    const user = userEvent.setup();
    renderRoutes(routes, '/login');
    await user.type(screen.getByLabelText('Email'), 'ada@example.org');
    await user.type(screen.getByLabelText('Password'), 'wrong');
    await user.click(screen.getByRole('button', { name: 'Log in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Wrong email or password');
    expect(tokens.loggedIn).toBe(false);
  });

  it('sends a logged-out visitor from /account to /login, and back after login', async () => {
    backend();
    const user = userEvent.setup();
    const { router } = renderRoutes(routes, '/account');
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'));

    await user.type(screen.getByLabelText('Email'), 'ada@example.org');
    await user.type(screen.getByLabelText('Password'), 'correct horse battery');
    await user.click(screen.getByRole('button', { name: 'Log in' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/account'));
  });

  it('restores a session after reload using only the stored refresh token', async () => {
    localStorage.setItem('marketlens.refresh_token', 'r0');
    mockFetch(async (req) => {
      const path = new URL(req.url).pathname;
      if (path === '/api/v1/auth/refresh') return json(PAIR);
      if (req.headers.get('Authorization') !== 'Bearer a1') return json({}, 401);
      if (path === '/api/v1/me') return json(ME);
      if (path === '/api/v1/assets') return json({ data: [] });
      return json([]);
    });
    renderRoutes(routes, '/account');
    expect(await screen.findByText(/ada@example\.org/)).toBeInTheDocument();
    expect(tokens.refresh).toBe('r1');
  });

  it('registers, logs in automatically and opens the account page', async () => {
    backend();
    const user = userEvent.setup();
    const { router } = renderRoutes(routes, '/register');
    await user.type(screen.getByLabelText('Email'), 'ada@example.org');
    await user.type(screen.getByLabelText('Password'), 'correct horse battery');
    await user.click(screen.getByRole('button', { name: 'Create account' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/account'));
    expect(tokens.access).toBe('a1');
  });
});
