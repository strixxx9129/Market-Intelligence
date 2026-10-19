/**
 * Routes. Every page is a separate chunk loaded on first visit (route-level code splitting):
 * the home page doesn't download the backtest page's code, or the charting library.
 */
import type { ComponentType } from 'react';
import { createBrowserRouter, type RouteObject } from 'react-router';

import { Layout } from './components/Layout';
import { RouteError } from './pages/RouteError';

const page = (load: () => Promise<{ default: ComponentType }>) => async () => ({
  Component: (await load()).default,
});

export const routes: RouteObject[] = [
  {
    path: '/',
    Component: Layout,
    ErrorBoundary: RouteError,
    children: [
      { index: true, lazy: page(() => import('./pages/Home')) },
      { path: 'assets/:symbol', lazy: page(() => import('./pages/Asset')) },
      { path: 'models', lazy: page(() => import('./pages/Models')) },
      { path: 'models/:id', lazy: page(() => import('./pages/ModelCard')) },
      { path: 'backtests', lazy: page(() => import('./pages/Backtests')) },
      { path: 'backtests/:id', lazy: page(() => import('./pages/Backtest')) },
      { path: 'status', lazy: page(() => import('./pages/Status')) },
      { path: 'login', lazy: page(() => import('./pages/Login')) },
      { path: 'register', lazy: page(() => import('./pages/Register')) },
      { path: 'account', lazy: page(() => import('./pages/Account')) },
      { path: 'contribute', lazy: page(() => import('./pages/Contribute')) },
      { path: '*', lazy: page(() => import('./pages/NotFound')) },
    ],
  },
];

export const router = createBrowserRouter(routes);
