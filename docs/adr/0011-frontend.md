# ADR 0011: Website: generated API client, server state in TanStack Query, honest forecast UI

- Status: Accepted
- Date: 2026-10-19

## Context

Non-developers need to see prices, forecasts, model quality, backtests and system health
without reading notebooks. The site must stay correct as the API evolves, work on a phone, and
never turn a probability into advice (rule 6).

## Decision

1. **Stack.** Vite, React 19, TypeScript (strict), Tailwind v4, React Router 7, TanStack
   Query 5 and Lightweight Charts 5, managed with pnpm (pinned via `packageManager`, lockfile
   committed). Quality tools: ESLint, Prettier, Vitest and Testing Library.
2. **Generated, typed API client.** The backend schema is exported to `frontend/openapi.json`
   (`make openapi`, script `scripts/export_openapi.py`). `pnpm gen:api` (openapi-typescript)
   turns it into `src/api/schema.d.ts`, and `openapi-fetch` checks every call against it:
   paths, params, bodies and response types. CI checks both steps:
   - the backend job fails if `openapi.json` is stale;
   - the frontend job fails if `schema.d.ts` doesn't match it.

   A renamed field becomes a compile error, not `undefined` in production.
3. **Server state vs UI state.** Everything the backend owns (prices, forecasts, models,
   account lists) lives in TanStack Query, under one hook per endpoint in `src/api/hooks.ts`
   with explicit keys and stale times. Mutations invalidate the lists they change.
   Local UI state (chart range, menu, form inputs) uses `useState`, and the theme uses a small
   context. Fetched data is never copied into `useState`.
4. **Auth.**
   - The access token (15 min) is held in memory only. The refresh token lives in localStorage.
   - A client middleware attaches the token. On a 401 it runs **one** shared refresh: refresh
     tokens are single-use, and parallel refreshes would trip the backend's reuse detection.
     It then replays the original request (the body is cloned beforehand).
   - If the refresh fails, the user is logged out.
5. **Honest forecast UI.**
   - P(up) is shown as an estimated probability ("52% estimated chance … higher"), together
     with the opposite chance.
   - Values within ±5 points of 50% are labelled "Close to a coin flip".
   - The live track record sits next to always-up and coin-flip yardsticks, or reads "not
     enough data" below 20 scored forecasts.
   - A baseline in production is called a baseline.
   - The disclaimer renders in every state.
   - No buy/sell/signal wording anywhere; tests enforce this.
6. **Accessibility and layout.**
   - Up/down is always an arrow plus a word; colour only reinforces it.
   - Responsive nav; a dark mode that follows the system or a remembered choice, applied
     before first paint.
   - Skeletons, error states with retry, and empty states that explain what to run.
   - All times are UTC.
7. **Performance.**
   - Route-level code splitting: one chunk per page via React Router `lazy`.
   - Lightweight Charts is in its own chunk, loaded only by chart pages.
   - Entry bundle: about 119 kB gzipped.
8. **New API surface for the site.** These read only Postgres, so rule 5 still holds:
   - `AssetOut.prev_close` / `change_1d_pct`;
   - `GET /backtests`;
   - `LeaderboardEntry.per_fold`;
   - `BacktestOut.equity_curve`, a weekly downsample stored in `backtests.equity_curve` (JSONB)
     when the backtest runs. Older backtests are filled by `marketlens backfill-equity-curves`.
9. **Dev runtime.** The `frontend` compose service runs node:22-alpine and the Vite dev
   server, with `node_modules` in a named volume. The host still needs only Docker and make.

## Consequences

- Refresh token in localStorage: an XSS bug could steal it. Mitigations: React escapes output,
  there is no `dangerouslySetInnerHTML`, refresh tokens rotate, and reuse revokes the family.
  **Follow-up:** move the refresh token to an httpOnly, SameSite=strict cookie scoped to
  `/api/v1/auth`. That needs backend cookie handling and CSRF protection.
- The backtest page shows a weekly curve. The daily Parquet curve stays in object storage for
  analysis.
- The features chart is limited to the API's 365-row maximum.
- `make lint` and `make test` now also run the frontend checks in the container.

## Alternatives considered

- **Hand-written fetch wrappers or types:** drift silently from the backend.
- **orval / openapi-generator** (generated hooks or classes): heavier, and they hide the
  query keys. openapi-fetch is tiny, and the hooks remain ours.
- **Redux/Zustand for server data:** re-implements caching, deduplication and invalidation
  that TanStack Query already does.
- **Recharts/Chart.js for prices:** SVG/canvas charts that struggle with 5,000+ candles and
  have no financial chart types. Lightweight Charts is made for exactly this.
- **Next.js / SSR:** no SEO or server-rendering need yet. A static SPA served by any CDN is
  simpler.
- **MSW for tests:** a stubbed `fetch` was enough, so one less dependency.
