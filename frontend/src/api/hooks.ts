/**
 * SERVER STATE lives here, in TanStack Query: data owned by the backend that we only borrow
 * (prices, forecasts, models...). The query cache dedupes requests, caches by key, refetches
 * stale data, retries, and gives every component the same loading/error flags. UI STATE
 * (which range is selected, is the menu open, form inputs) stays in React state where it's used.
 * Mixing them (copying fetched data into useState) is the classic source of stale screens.
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { tokens } from '../auth/tokens';
import { api, type Schemas } from './client';
import { unwrap } from './errors';

const MINUTE = 60_000;

export const keys = {
  assets: ['assets'] as const,
  prices: (symbol: string, from: string | null) => ['prices', symbol, from] as const,
  features: (symbol: string, limit: number) => ['features', symbol, limit] as const,
  forecast: (symbol: string) => ['forecast', symbol] as const,
  models: ['models'] as const,
  model: (id: number) => ['model', id] as const,
  backtests: ['backtests'] as const,
  backtest: (id: string) => ['backtest', id] as const,
  status: ['status'] as const,
  me: ['me'] as const,
  watchlists: ['me', 'watchlists'] as const,
  alerts: ['me', 'alerts'] as const,
  apiKeys: ['me', 'api-keys'] as const,
};

// ------------------------------------------------------------------ public data
export function useAssets() {
  return useQuery({
    queryKey: keys.assets,
    queryFn: async () => unwrap(await api.GET('/api/v1/assets')).data,
    staleTime: 5 * MINUTE,
  });
}

/** Every daily candle since `from` (null = all history), following the keyset cursor. */
export function usePrices(symbol: string, from: string | null) {
  return useQuery({
    queryKey: keys.prices(symbol, from),
    queryFn: async () => {
      const candles: Schemas['Candle'][] = [];
      let cursor: string | null = null;
      do {
        const page: Schemas['PricePage'] = unwrap(
          await api.GET('/api/v1/assets/{symbol}/prices', {
            params: { path: { symbol }, query: { from, cursor, limit: 1000 } },
          }),
        );
        candles.push(...page.data);
        cursor = page.page.next_cursor ?? null;
      } while (cursor);
      return candles;
    },
    staleTime: 10 * MINUTE,
    placeholderData: keepPreviousData, // keep the old chart while a new range loads
  });
}

export function useFeatures(symbol: string, limit = 365) {
  return useQuery({
    queryKey: keys.features(symbol, limit),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/assets/{symbol}/features', {
          params: { path: { symbol }, query: { limit } },
        }),
      ),
    staleTime: 10 * MINUTE,
  });
}

export function useForecast(symbol: string) {
  return useQuery({
    queryKey: keys.forecast(symbol),
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/assets/{symbol}/forecast', { params: { path: { symbol } } })),
    staleTime: 5 * MINUTE,
    retry: (count, error) => (error as { status?: number }).status !== 404 && count < 2,
  });
}

export function useLeaderboard() {
  return useQuery({
    queryKey: keys.models,
    queryFn: async () => unwrap(await api.GET('/api/v1/models')).data,
    staleTime: 30 * MINUTE,
  });
}

export function useModelCard(id: number) {
  return useQuery({
    queryKey: keys.model(id),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/models/{model_version_id}', {
          params: { path: { model_version_id: id } },
        }),
      ),
    staleTime: 30 * MINUTE,
  });
}

export function useBacktests() {
  return useQuery({
    queryKey: keys.backtests,
    queryFn: async () => unwrap(await api.GET('/api/v1/backtests')),
    staleTime: 30 * MINUTE,
  });
}

export function useBacktest(id: string) {
  return useQuery({
    queryKey: keys.backtest(id),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/backtests/{backtest_id}', {
          params: { path: { backtest_id: id } },
        }),
      ),
    staleTime: 60 * MINUTE,
  });
}

export function useStatus() {
  return useQuery({
    queryKey: keys.status,
    queryFn: async () => unwrap(await api.GET('/api/v1/status')),
    refetchInterval: MINUTE, // a status page should stay current while it's open
  });
}

// ------------------------------------------------------------------ account
export function useMe(enabled: boolean) {
  return useQuery({
    queryKey: keys.me,
    queryFn: async () => unwrap(await api.GET('/api/v1/me')),
    enabled,
    retry: false,
    staleTime: 5 * MINUTE,
  });
}

export function useWatchlists() {
  return useQuery({
    queryKey: keys.watchlists,
    queryFn: async () => unwrap(await api.GET('/api/v1/me/watchlists')),
  });
}

export function useAlerts() {
  return useQuery({
    queryKey: keys.alerts,
    queryFn: async () => unwrap(await api.GET('/api/v1/me/alerts')),
  });
}

export function useApiKeys() {
  return useQuery({
    queryKey: keys.apiKeys,
    queryFn: async () => unwrap(await api.GET('/api/v1/me/api-keys')),
  });
}

/** A mutation that refreshes `key` afterwards, so the list on screen matches the server. */
function useInvalidatingMutation<TVars, TData>(
  key: readonly unknown[],
  fn: (vars: TVars) => Promise<TData>,
) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: fn, onSettled: () => qc.invalidateQueries({ queryKey: key }) });
}

export function useCreateWatchlist() {
  return useInvalidatingMutation(keys.watchlists, async (body: Schemas['WatchlistIn']) =>
    unwrap(await api.POST('/api/v1/me/watchlists', { body })),
  );
}

export function useDeleteWatchlist() {
  return useInvalidatingMutation(keys.watchlists, async (id: number) =>
    unwrap(
      await api.DELETE('/api/v1/me/watchlists/{watchlist_id}', {
        params: { path: { watchlist_id: id } },
      }),
    ),
  );
}

export function useCreateAlert() {
  return useInvalidatingMutation(keys.alerts, async (body: Schemas['AlertIn']) =>
    unwrap(await api.POST('/api/v1/me/alerts', { body })),
  );
}

export function useToggleAlert() {
  return useInvalidatingMutation(keys.alerts, async (v: { id: number; is_active: boolean }) =>
    unwrap(
      await api.PATCH('/api/v1/me/alerts/{alert_id}', {
        params: { path: { alert_id: v.id } },
        body: { is_active: v.is_active },
      }),
    ),
  );
}

export function useDeleteAlert() {
  return useInvalidatingMutation(keys.alerts, async (id: number) =>
    unwrap(
      await api.DELETE('/api/v1/me/alerts/{alert_id}', { params: { path: { alert_id: id } } }),
    ),
  );
}

export function useCreateApiKey() {
  return useInvalidatingMutation(keys.apiKeys, async (body: Schemas['ApiKeyIn']) =>
    unwrap(await api.POST('/api/v1/me/api-keys', { body })),
  );
}

export function useRevokeApiKey() {
  return useInvalidatingMutation(keys.apiKeys, async (id: string) =>
    unwrap(await api.DELETE('/api/v1/me/api-keys/{key_id}', { params: { path: { key_id: id } } })),
  );
}

// ------------------------------------------------------------------ auth calls
export async function login(email: string, password: string): Promise<void> {
  const pair = unwrap(await api.POST('/api/v1/auth/login', { body: { email, password } }));
  tokens.set(pair.access_token, pair.refresh_token);
}

export async function register(email: string, password: string): Promise<void> {
  unwrap(await api.POST('/api/v1/auth/register', { body: { email, password } }));
  await login(email, password);
}

export async function logout(): Promise<void> {
  const refresh = tokens.refresh;
  tokens.clear(); // log out locally even if the network call fails
  if (refresh) {
    try {
      await api.POST('/api/v1/auth/logout', { body: { refresh_token: refresh } });
    } catch {
      /* the refresh token expires on its own */
    }
  }
}
