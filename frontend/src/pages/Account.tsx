import { useState, type FormEvent } from 'react';
import { Navigate, useLocation } from 'react-router';

import type { Schemas } from '../api/client';
import { errorMessage } from '../api/errors';
import {
  useAlerts,
  useApiKeys,
  useAssets,
  useCreateAlert,
  useCreateApiKey,
  useCreateWatchlist,
  useDeleteAlert,
  useDeleteWatchlist,
  useRevokeApiKey,
  useToggleAlert,
  useWatchlists,
} from '../api/hooks';
import { useAuth } from '../auth/AuthContext';
import { Badge, Card, EmptyState, ErrorState, LoadingBlock, PageTitle } from '../components/ui';
import { formatDate, formatDateTime } from '../lib/format';

export default function Account() {
  const { user, checking, logout } = useAuth();
  const location = useLocation();
  if (checking) return <LoadingBlock label="Checking your session" />;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;

  return (
    <>
      <PageTitle
        title="Your account"
        subtitle={
          <>
            {user.email} · role <Badge>{user.role}</Badge> · member since{' '}
            {formatDate(user.created_at)}
          </>
        }
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <Watchlists />
        <Alerts />
        <ApiKeys />
      </div>
      <button type="button" className="btn-secondary mt-8" onClick={() => void logout()}>
        Log out
      </button>
    </>
  );
}

function FormError({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p role="alert" className="text-sm text-red-700 dark:text-red-400">
      {errorMessage(error)}
    </p>
  );
}

function Watchlists() {
  const q = useWatchlists();
  const create = useCreateWatchlist();
  const remove = useDeleteWatchlist();
  const assets = useAssets().data ?? [];
  const [name, setName] = useState('');
  const [symbols, setSymbols] = useState<string[]>([]);

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate(
      { name: name.trim(), symbols },
      { onSuccess: () => (setName(''), setSymbols([])) },
    );
  }

  return (
    <Card title="Watchlists">
      {q.isPending ? (
        <LoadingBlock />
      ) : q.error ? (
        <ErrorState error={q.error} />
      ) : q.data.length === 0 ? (
        <EmptyState title="No watchlists yet" />
      ) : (
        <ul className="mb-4 space-y-2">
          {q.data.map((w) => (
            <li key={w.id} className="flex items-center justify-between gap-2 text-sm">
              <span>
                <strong>{w.name}</strong> · {w.symbols.join(', ') || 'empty'}
              </span>
              <button type="button" className="btn-danger" onClick={() => remove.mutate(w.id)}>
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
      <form
        onSubmit={submit}
        className="mt-4 space-y-2 border-t border-slate-200 pt-4 dark:border-slate-800"
      >
        <label className="block text-sm font-medium" htmlFor="wl-name">
          New watchlist
        </label>
        <input
          id="wl-name"
          className="input"
          placeholder="Name"
          required
          maxLength={100}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <fieldset className="flex flex-wrap gap-3 text-sm">
          <legend className="sr-only">Assets</legend>
          {assets.map((a) => (
            <label key={a.symbol} className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={symbols.includes(a.symbol)}
                onChange={(e) =>
                  setSymbols((s) =>
                    e.target.checked ? [...s, a.symbol] : s.filter((x) => x !== a.symbol),
                  )
                }
              />
              {a.symbol}
            </label>
          ))}
        </fieldset>
        <FormError error={create.error} />
        <button type="submit" className="btn" disabled={create.isPending || !name.trim()}>
          Add watchlist
        </button>
      </form>
    </Card>
  );
}

const CONDITIONS: Record<Schemas['AlertIn']['condition'], string> = {
  p_up_above: 'Estimated P(up) above',
  p_up_below: 'Estimated P(up) below',
  price_above: 'Price above',
  price_below: 'Price below',
};

function Alerts() {
  const q = useAlerts();
  const create = useCreateAlert();
  const toggle = useToggleAlert();
  const remove = useDeleteAlert();
  const assets = useAssets().data ?? [];
  const [symbol, setSymbol] = useState('BTC-USD');
  const [condition, setCondition] = useState<Schemas['AlertIn']['condition']>('price_above');
  const [threshold, setThreshold] = useState('');

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate(
      { symbol, condition, threshold: Number(threshold), is_active: true },
      { onSuccess: () => setThreshold('') },
    );
  }

  return (
    <Card title="Alert rules">
      <p className="mb-3 rounded-md bg-slate-100 p-2 text-xs text-slate-700 dark:bg-slate-800 dark:text-slate-300">
        Rules are saved, but not evaluated or delivered yet (planned). Nothing will be sent to you.
      </p>
      {q.isPending ? (
        <LoadingBlock />
      ) : q.error ? (
        <ErrorState error={q.error} />
      ) : q.data.length === 0 ? (
        <EmptyState title="No alert rules yet" />
      ) : (
        <ul className="space-y-2">
          {q.data.map((a) => (
            <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <span>
                {a.symbol}: {CONDITIONS[a.condition as keyof typeof CONDITIONS] ?? a.condition}{' '}
                {a.threshold} {!a.is_active && <Badge>paused</Badge>}
              </span>
              <span className="flex gap-2">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => toggle.mutate({ id: a.id, is_active: !a.is_active })}
                >
                  {a.is_active ? 'Pause' : 'Resume'}
                </button>
                <button type="button" className="btn-danger" onClick={() => remove.mutate(a.id)}>
                  Delete
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <form
        onSubmit={submit}
        className="mt-4 grid gap-2 border-t border-slate-200 pt-4 sm:grid-cols-3 dark:border-slate-800"
      >
        <label className="text-sm">
          <span className="mb-1 block font-medium">Asset</span>
          <select className="input" value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {assets.map((a) => (
              <option key={a.symbol}>{a.symbol}</option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium">Condition</span>
          <select
            className="input"
            value={condition}
            onChange={(e) => setCondition(e.target.value as typeof condition)}
          >
            {Object.entries(CONDITIONS).map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium">Threshold</span>
          <input
            className="input"
            type="number"
            step="any"
            required
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            placeholder={condition.startsWith('p_up') ? '0.6' : '90000'}
          />
        </label>
        <div className="sm:col-span-3">
          <FormError error={create.error} />
          <button
            type="submit"
            className="btn mt-1"
            disabled={create.isPending || threshold === ''}
          >
            Save rule
          </button>
        </div>
      </form>
    </Card>
  );
}

function ApiKeys() {
  const q = useApiKeys();
  const create = useCreateApiKey();
  const revoke = useRevokeApiKey();
  const [name, setName] = useState('');
  const [created, setCreated] = useState<Schemas['ApiKeyCreated'] | null>(null);

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate({ name: name.trim() }, { onSuccess: (key) => (setCreated(key), setName('')) });
  }

  return (
    <Card title="API keys" className="lg:col-span-2">
      <p className="mb-3 text-sm text-slate-600 dark:text-slate-400">
        Send a key as the <code>X-API-Key</code> header for higher rate limits.
      </p>
      {created && (
        <div
          role="status"
          className="mb-4 rounded-md border border-teal-300 bg-teal-50 p-3 text-sm dark:border-teal-800 dark:bg-teal-950"
        >
          <p className="font-medium">Copy your new key now. It won’t be shown again.</p>
          <code className="mt-1 block break-all">{created.key}</code>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => void navigator.clipboard?.writeText(created.key)}
            >
              Copy
            </button>
            <button type="button" className="btn-secondary" onClick={() => setCreated(null)}>
              I’ve saved it
            </button>
          </div>
        </div>
      )}
      {q.isPending ? (
        <LoadingBlock />
      ) : q.error ? (
        <ErrorState error={q.error} />
      ) : q.data.length === 0 ? (
        <EmptyState title="No API keys yet" />
      ) : (
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Key</th>
                <th>Created</th>
                <th>Last used</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {q.data.map((k) => (
                <tr key={k.id}>
                  <td>{k.name}</td>
                  <td>
                    <code>{k.prefix}…</code>
                  </td>
                  <td>{formatDate(k.created_at)}</td>
                  <td>{k.last_used_at ? formatDateTime(k.last_used_at) : 'never'}</td>
                  <td>
                    {k.revoked_at ? (
                      <Badge>revoked</Badge>
                    ) : (
                      <button
                        type="button"
                        className="btn-danger"
                        onClick={() => revoke.mutate(k.id)}
                      >
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <form
        onSubmit={submit}
        className="mt-4 flex flex-wrap items-end gap-2 border-t border-slate-200 pt-4 dark:border-slate-800"
      >
        <label className="flex-1 text-sm">
          <span className="mb-1 block font-medium">New key name</span>
          <input
            className="input"
            required
            maxLength={100}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. my notebook"
          />
        </label>
        <button type="submit" className="btn" disabled={create.isPending || !name.trim()}>
          Create key
        </button>
        <div className="w-full">
          <FormError error={create.error} />
        </div>
      </form>
    </Card>
  );
}
