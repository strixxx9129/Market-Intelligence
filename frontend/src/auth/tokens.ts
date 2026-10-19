/**
 * Where the tokens live.
 *
 * - Access token (15 min JWT): memory only. A script injected into the page could read
 *   localStorage, but not a module-scoped variable that's gone on reload.
 * - Refresh token (opaque, rotated on every use): localStorage, so a reload keeps you logged
 *   in. The backend rotates it on each refresh and revokes the whole family if an old one is
 *   reused, which limits the damage if it leaks. An httpOnly cookie would be safer still
 *   (JavaScript can't read it at all); that needs backend cookie + CSRF support (see ADR 0011).
 */

const REFRESH_KEY = 'marketlens.refresh_token';

type Listener = (loggedIn: boolean) => void;

let accessToken: string | null = null;
const listeners = new Set<Listener>();

function readRefresh(): string | null {
  try {
    return localStorage.getItem(REFRESH_KEY);
  } catch {
    return null; // storage disabled (private mode, tests)
  }
}

export const tokens = {
  get access(): string | null {
    return accessToken;
  },
  get refresh(): string | null {
    return readRefresh();
  },
  get loggedIn(): boolean {
    return accessToken !== null || readRefresh() !== null;
  },
  set(access: string, refresh: string): void {
    accessToken = access;
    try {
      localStorage.setItem(REFRESH_KEY, refresh);
    } catch {
      /* keep the session in memory only */
    }
    listeners.forEach((l) => l(true));
  },
  clear(): void {
    accessToken = null;
    try {
      localStorage.removeItem(REFRESH_KEY);
    } catch {
      /* nothing stored */
    }
    listeners.forEach((l) => l(false));
  },
  subscribe(listener: Listener): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};
