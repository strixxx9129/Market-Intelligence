# MarketLens website

React + TypeScript + Vite + Tailwind + React Router + TanStack Query + Lightweight Charts.
Educational software, not financial advice. See [ADR 0011](../docs/adr/0011-frontend.md).

## Run

From the repo root (Docker only, no local Node needed):

```bash
make web        # API + Vite dev server on http://localhost:5173
make web-test   # Vitest + Testing Library
make lint       # includes ESLint, Prettier and tsc for the frontend
```

With a local Node 22+ and pnpm 10 instead: `pnpm install`, `pnpm dev`, `pnpm test`,
`pnpm lint`, `pnpm typecheck`, `pnpm build`.

Config (`.env.example`): `VITE_API_URL` (the API as seen from the browser) and
`VITE_GITHUB_URL`. Everything `VITE_*` ends up in the public bundle, so never put secrets in it.

## The API client is generated

```bash
make openapi    # backend → frontend/openapi.json
make web-gen    # openapi.json → src/api/schema.d.ts (pnpm gen:api)
```

Never edit `src/api/schema.d.ts` by hand. CI fails if either file is stale.

## Layout

| path                | what                                                                       |
| ------------------- | -------------------------------------------------------------------------- |
| `src/api/client.ts` | typed `openapi-fetch` client, auth header, one shared token refresh on 401 |
| `src/api/hooks.ts`  | every query/mutation (server state)                                        |
| `src/auth/`         | token storage and `AuthProvider` / `useAuth`                               |
| `src/components/`   | layout, UI primitives, `ForecastPanel`, charts                             |
| `src/pages/`        | one lazily loaded chunk per route                                          |
| `src/lib/`          | formatting, theme, forecast wording rules                                  |

## Rules for UI changes

- Never show a forecast as an instruction: no "buy", "sell", "signal" or "target". Show a
  probability, its yardsticks and the disclaimer (`ForecastPanel.test.tsx` enforces this).
- Up/down is always an arrow plus a word, never colour alone.
- Every data view handles loading (skeleton), error (with retry) and empty states.
