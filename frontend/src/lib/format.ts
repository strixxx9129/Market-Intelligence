/** Formatting helpers. All dates are shown in UTC, like every timestamp in the backend. */

export const DISCLAIMER =
  'Educational software, not financial advice. Forecasts are uncertain, can be wrong, and past ' +
  'performance does not predict future results.';

const usd = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 2,
});

export function formatPrice(value: number | null | undefined, currency = 'USD'): string {
  if (value == null) return '—';
  if (currency === 'USD') return usd.format(value);
  return `${value.toLocaleString('en-US', { maximumFractionDigits: 2 })} ${currency}`;
}

export function formatPercent(fraction: number | null | undefined, digits = 1): string {
  if (fraction == null || Number.isNaN(fraction)) return '—';
  return `${(fraction * 100).toFixed(digits)}%`;
}

export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return '—';
  return value.toFixed(digits);
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-GB', {
    timeZone: 'UTC',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return `${d.toLocaleString('en-GB', {
    timeZone: 'UTC',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })} UTC`;
}

export function timeAgo(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return 'never';
  const minutes = Math.round((now.getTime() - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

export type Direction = 'up' | 'down' | 'flat';

export function directionOf(value: number | null | undefined, flatBand = 0): Direction {
  if (value == null || Math.abs(value) <= flatBand) return 'flat';
  return value > 0 ? 'up' : 'down';
}

/** 'YYYY-MM-DD' (UTC) from an ISO timestamp: the time format Lightweight Charts takes for daily data. */
export const day = (iso: string): string => iso.slice(0, 10);
