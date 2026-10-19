import { QueryClient } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { createMemoryRouter, RouterProvider, type RouteObject } from 'react-router';
import { vi } from 'vitest';

import { Providers } from '../providers';

export type Handler = (req: Request) => Response | Promise<Response>;

/** Replace global fetch with `handler`; returns the mock so tests can inspect calls. */
export function mockFetch(handler: Handler) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(input instanceof Request ? input : new Request(input, init)),
  );
  vi.stubGlobal('fetch', fn);
  return fn;
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

export function testQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

/** Render routes inside every app provider, starting at `path`. */
export function renderRoutes(routes: RouteObject[], path = '/') {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const utils = render(
    <Providers client={testQueryClient()}>
      <RouterProvider router={router} />
    </Providers>,
  );
  return { router, ...utils };
}

export function renderWithProviders(ui: ReactElement) {
  return renderRoutes([{ path: '*', element: ui }]);
}
