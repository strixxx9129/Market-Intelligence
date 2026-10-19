/** Small shared building blocks: cards, skeletons, error/empty states, up/down labels. */
import type { ReactNode } from 'react';

import { errorMessage } from '../api/errors';
import { DISCLAIMER, type Direction } from '../lib/format';

export function Card({
  title,
  children,
  className = '',
  action,
}: {
  title?: ReactNode;
  children: ReactNode;
  className?: string;
  action?: ReactNode;
}) {
  return (
    <section
      className={`rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900 ${className}`}
    >
      {(title || action) && (
        <div className="mb-3 flex items-center justify-between gap-2">
          {title && <h2 className="text-base font-semibold">{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function PageTitle({ title, subtitle }: { title: ReactNode; subtitle?: ReactNode }) {
  return (
    <header className="mb-6">
      <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">{title}</h1>
      {subtitle && <p className="mt-1 text-slate-600 dark:text-slate-400">{subtitle}</p>}
    </header>
  );
}

export function Skeleton({ className = 'h-4 w-full' }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={`animate-pulse rounded bg-slate-200 dark:bg-slate-800 ${className}`}
    />
  );
}

export function LoadingBlock({ label = 'Loading', lines = 3 }: { label?: string; lines?: number }) {
  return (
    <div role="status" aria-label={label} className="space-y-2">
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={`h-4 ${i === lines - 1 ? 'w-2/3' : 'w-full'}`} />
      ))}
      <span className="sr-only">{label}…</span>
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-red-300 bg-red-50 p-4 text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-200"
    >
      <p className="font-medium">Couldn’t load this.</p>
      <p className="mt-1 text-sm">{errorMessage(error)}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn-secondary mt-3">
          Try again
        </button>
      )}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 p-6 text-center dark:border-slate-700">
      <p className="font-medium">{title}</p>
      {children && (
        <div className="mt-1 text-sm text-slate-600 dark:text-slate-400">{children}</div>
      )}
    </div>
  );
}

const DIRECTION = {
  up: { arrow: '▲', word: 'Up', className: 'text-emerald-700 dark:text-emerald-400' },
  down: { arrow: '▼', word: 'Down', className: 'text-red-700 dark:text-red-400' },
  flat: { arrow: '■', word: 'Flat', className: 'text-slate-600 dark:text-slate-400' },
} as const;

/** Up/down is ALWAYS an arrow + a word; colour only reinforces it (colour-blind friendly). */
export function DirectionLabel({
  direction,
  children,
  word,
}: {
  direction: Direction;
  children?: ReactNode;
  word?: string;
}) {
  const d = DIRECTION[direction];
  return (
    <span className={`inline-flex items-center gap-1 font-medium ${d.className}`}>
      <span aria-hidden="true">{d.arrow}</span>
      <span>{word ?? d.word}</span>
      {children}
    </span>
  );
}

export function Disclaimer({ text, compact = false }: { text?: string | null; compact?: boolean }) {
  return (
    <p
      data-testid="disclaimer"
      className={`text-slate-600 dark:text-slate-400 ${compact ? 'text-xs' : 'text-sm'}`}
    >
      <span aria-hidden="true">ⓘ </span>
      {text || DISCLAIMER}
    </p>
  );
}

export function Badge({
  children,
  tone = 'slate',
}: {
  children: ReactNode;
  tone?: 'slate' | 'green' | 'amber' | 'red' | 'teal';
}) {
  const tones = {
    slate: 'bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-200',
    green: 'bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200',
    amber: 'bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200',
    red: 'bg-red-100 text-red-900 dark:bg-red-950 dark:text-red-200',
    teal: 'bg-teal-100 text-teal-900 dark:bg-teal-950 dark:text-teal-200',
  };
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${tones[tone]}`}>
      {children}
    </span>
  );
}

export function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
      </dt>
      <dd className="mt-0.5 text-lg font-semibold tabular-nums">{value}</dd>
      {hint && <dd className="text-xs text-slate-500 dark:text-slate-400">{hint}</dd>}
    </div>
  );
}
