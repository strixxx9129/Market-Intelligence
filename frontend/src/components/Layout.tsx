import { Suspense, useState } from 'react';
import { Link, NavLink, Outlet, useLocation } from 'react-router';

import { useAuth } from '../auth/AuthContext';
import { useTheme, type ThemeMode } from '../lib/theme';
import { LoadingBlock } from './ui';

const NAV = [
  { to: '/', label: 'Home', end: true },
  { to: '/models', label: 'Models' },
  { to: '/backtests', label: 'Backtests' },
  { to: '/status', label: 'Status' },
  { to: '/contribute', label: 'Contribute' },
];

function navClass({ isActive }: { isActive: boolean }) {
  return `block rounded-md px-3 py-2 text-sm font-medium ${
    isActive
      ? 'bg-teal-50 text-teal-800 dark:bg-teal-950 dark:text-teal-200'
      : 'text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800'
  }`;
}

function ThemeSelect() {
  const { mode, setMode } = useTheme();
  return (
    <label className="flex items-center gap-1 text-sm">
      <span className="sr-only">Colour theme</span>
      <select
        value={mode}
        onChange={(e) => setMode(e.target.value as ThemeMode)}
        className="input py-1 text-sm"
      >
        <option value="system">System theme</option>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
      </select>
    </label>
  );
}

function AccountLink() {
  const { user, checking } = useAuth();
  if (checking) return null;
  return user ? (
    <NavLink to="/account" className={navClass}>
      Account
    </NavLink>
  ) : (
    <NavLink to="/login" className={navClass}>
      Log in
    </NavLink>
  );
}

export function Layout() {
  const [open, setOpen] = useState(false);
  const location = useLocation();

  return (
    <div className="flex min-h-screen flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:p-2">
        Skip to content
      </a>
      <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/90 backdrop-blur dark:border-slate-800 dark:bg-slate-950/90">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
          <Link to="/" className="flex items-center gap-2 font-bold">
            <img src="/favicon.svg" alt="" className="h-7 w-7" />
            MarketLens
          </Link>
          <nav aria-label="Main" className="hidden items-center gap-1 md:flex">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.end} className={navClass}>
                {n.label}
              </NavLink>
            ))}
            <AccountLink />
            <ThemeSelect />
          </nav>
          <button
            type="button"
            className="btn-secondary md:hidden"
            aria-expanded={open}
            aria-controls="mobile-nav"
            onClick={() => setOpen((o) => !o)}
          >
            {open ? 'Close' : 'Menu'}
          </button>
        </div>
        {open && (
          <nav
            id="mobile-nav"
            aria-label="Main"
            className="border-t border-slate-200 px-4 py-2 md:hidden dark:border-slate-800"
            onClick={() => setOpen(false)}
          >
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.end} className={navClass}>
                {n.label}
              </NavLink>
            ))}
            <AccountLink />
            <div className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
              <ThemeSelect />
            </div>
          </nav>
        )}
      </header>

      <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:py-8">
        {/* key: reset error/suspense boundaries when the route changes */}
        <Suspense
          key={location.pathname}
          fallback={<LoadingBlock label="Loading page" lines={6} />}
        >
          <Outlet />
        </Suspense>
      </main>

      <footer className="border-t border-slate-200 py-6 text-sm text-slate-600 dark:border-slate-800 dark:text-slate-400">
        <div className="mx-auto max-w-6xl space-y-1 px-4">
          <p>
            MarketLens is open-source, educational software. Nothing here is financial advice or a
            recommendation to buy or sell anything.
          </p>
          <p>All times are UTC. Data: daily candles; forecasts are for the next daily close.</p>
          <p>
            Price data: Bitstamp BTC/USD via the Kaggle dataset{' '}
            <a
              className="link"
              href="https://www.kaggle.com/datasets/mczielinski/bitcoin-historical-data"
              target="_blank"
              rel="noreferrer"
            >
              mczielinski/bitcoin-historical-data
            </a>
            , licensed{' '}
            <a
              className="link"
              href="https://creativecommons.org/licenses/by-sa/4.0/"
              target="_blank"
              rel="noreferrer"
            >
              CC BY-SA 4.0
            </a>
            . Derived daily candles are shared under the same licence.
          </p>
        </div>
      </footer>
    </div>
  );
}
