# ADR 0009: Public API: read-only and stateless, versioned cache, rotating refresh tokens, verified rate-limit identity

- Status: Accepted
- Date: 2026-10-19

## Context

The website and external developers need a documented, fast API over everything the pipeline
produces. It must never trigger computation or external calls (rule 5), must always label
forecasts (rule 6), and must scale horizontally without sticky sessions.

## Decision

1. **Read-only + stateless.** Market/model endpoints only SELECT precomputed rows. The API
   writes only its own operational data (users, tokens, keys, watchlists, alerts). Admin
   endpoints enqueue Celery tasks or flip a model stage through the promotion gate. No
   per-user state in process memory: JWT/API key on every request; cache and rate-limit
   counters in Redis. Any replica can serve any request.
2. **Layering**: routers (HTTP only) → services (logic, raise `ServiceError`) → repositories
   (SQL only). Pydantic response models for every endpoint; one error body
   `{"error": {code, message, details, request_id}}`; request-ID middleware.
3. **Caching**: Redis cache-aside per namespace with TTLs (status 30 s … models 1 h).
   Keys embed a namespace version; invalidation is `INCR` of that version (O(1), race-free),
   triggered when predictions, promotions, candles or features are written. HTTP `ETag` +
   `Cache-Control: public, max-age` on public data, 304 on `If-None-Match`; `private, no-store`
   on `/me` and auth. Redis outages degrade to DB reads.
4. **Pagination**: keyset cursor on `ts` (opaque base64) for prices, not offset.
5. **Auth**: Argon2id passwords; 15-minute HS256 access JWT (algorithm pinned, issuer checked);
   opaque refresh tokens stored as SHA-256, rotated on every use, reuse → whole family revoked;
   logout revokes the family. Roles user < contributor < maintainer < admin. API keys
   `ml_<prefix>_<secret>`, SHA-256 at rest, shown once. Prod refuses to start with the default
   JWT secret. First admin via `marketlens admin create-user`.
6. **Rate limiting** (slowapi + Redis): identity is VERIFIED first (`IdentityMiddleware`) so
   fake keys can't mint fresh buckets; per API key 1000/min, per user 300/min, per IP 60/min;
   auth endpoints 5/min per IP. Default limits are enforced by a router-level dependency
   (see Consequences); probes are exempt.
7. **Load testing**: Locust (official image, profile `loadtest`) against a separate API instance
   with rate limiting disabled.

## Consequences

- slowapi 0.1.10's middleware can't see FastAPI ≥ 0.13x nested routers and silently exempts every
  route, and its callable default limits fail open. We use a dependency (endpoint already
  resolved) plus a ContextVar for the identity; integration tests assert real 429s so a library
  upgrade can't silently disable limiting again.
- Access tokens can't be revoked before expiry (by design: 15 min).
- The forecast track record is honest and therefore empty at first (live outcomes only).
- User alerts are stored rules only; evaluation and delivery are a follow-up.
