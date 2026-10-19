import { useQueryClient } from '@tanstack/react-query';
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

import type { Schemas } from '../api/client';
import { login, logout, register, useMe } from '../api/hooks';
import { tokens } from './tokens';

type AuthValue = {
  user: Schemas['UserOut'] | null;
  /** True while we don't yet know whether a stored session is still valid. */
  checking: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [loggedIn, setLoggedIn] = useState(tokens.loggedIn);

  useEffect(
    () =>
      tokens.subscribe((isIn) => {
        setLoggedIn(isIn);
        if (!isIn) qc.removeQueries({ queryKey: ['me'] }); // drop every private query
      }),
    [qc],
  );

  // With only a refresh token (after a reload), /me returns 401 and the client refreshes.
  const me = useMe(loggedIn);

  const value = useMemo<AuthValue>(
    () => ({
      user: loggedIn ? (me.data ?? null) : null,
      checking: loggedIn && me.isPending,
      login,
      register,
      logout,
    }),
    [loggedIn, me.data, me.isPending],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>');
  return value;
}
