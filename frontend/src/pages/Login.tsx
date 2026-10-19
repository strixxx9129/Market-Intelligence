import { useState, type FormEvent } from 'react';
import { Link, useLocation, useNavigate } from 'react-router';

import { errorMessage } from '../api/errors';
import { useAuth } from '../auth/AuthContext';
import { Card, PageTitle } from '../components/ui';

export function AuthForm({
  mode,
  onSubmit,
}: {
  mode: 'login' | 'register';
  onSubmit: (email: string, password: string) => Promise<void>;
}) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await onSubmit(email.trim(), password);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <div>
        <label htmlFor="email" className="mb-1 block text-sm font-medium">
          Email
        </label>
        <input
          id="email"
          type="email"
          autoComplete="email"
          required
          className="input"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="password" className="mb-1 block text-sm font-medium">
          Password
        </label>
        <input
          id="password"
          type="password"
          autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
          required
          minLength={mode === 'register' ? 12 : 1}
          aria-describedby={mode === 'register' ? 'password-hint' : undefined}
          className="input"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {mode === 'register' && (
          <p id="password-hint" className="mt-1 text-xs text-slate-500">
            At least 12 characters.
          </p>
        )}
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
      )}
      <button type="submit" className="btn w-full" disabled={busy}>
        {busy ? 'Please wait…' : mode === 'login' ? 'Log in' : 'Create account'}
      </button>
    </form>
  );
}

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const from = (useLocation().state as { from?: string } | null)?.from ?? '/account';
  return (
    <div className="mx-auto max-w-sm">
      <PageTitle title="Log in" />
      <Card>
        <AuthForm
          mode="login"
          onSubmit={async (email, password) => {
            await login(email, password);
            navigate(from, { replace: true });
          }}
        />
        <p className="mt-4 text-sm">
          No account?{' '}
          <Link className="link" to="/register">
            Create one
          </Link>
        </p>
      </Card>
    </div>
  );
}
