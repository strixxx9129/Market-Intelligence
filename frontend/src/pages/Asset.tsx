import { useMemo, useState } from 'react';
import { useParams } from 'react-router';

import { useAssets, useFeatures, usePrices } from '../api/hooks';
import { CandleChart, LineChart } from '../components/charts';
import { ForecastPanelFor } from '../components/ForecastPanel';
import { Card, EmptyState, ErrorState, PageTitle, Skeleton } from '../components/ui';
import { day, formatPrice } from '../lib/format';

const RANGES = [
  { id: '1M', label: '1 month', days: 31 },
  { id: '3M', label: '3 months', days: 92 },
  { id: '1Y', label: '1 year', days: 366 },
  { id: '5Y', label: '5 years', days: 5 * 366 },
  { id: 'ALL', label: 'All', days: null },
] as const;

type RangeId = (typeof RANGES)[number]['id'];

/** Start of the range, aligned to midnight UTC so the query key is stable during the day. */
function rangeStart(days: number | null): string | null {
  if (days == null) return null;
  const d = new Date();
  d.setUTCHours(0, 0, 0, 0);
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString();
}

export default function Asset() {
  const { symbol = '' } = useParams();
  const [range, setRange] = useState<RangeId>('1Y'); // UI state: lives here, not in the cache
  const from = rangeStart(RANGES.find((r) => r.id === range)!.days);
  const asset = useAssets().data?.find((a) => a.symbol === symbol);

  return (
    <>
      <PageTitle
        title={asset ? `${asset.name} (${symbol})` : symbol}
        subtitle={asset ? `Last daily close ${formatPrice(asset.latest_close)}` : undefined}
      />
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card
            title="Daily candles"
            action={
              <div role="group" aria-label="Chart range" className="flex flex-wrap gap-1">
                {RANGES.map((r) => (
                  <button
                    key={r.id}
                    type="button"
                    aria-pressed={range === r.id}
                    aria-label={r.label}
                    onClick={() => setRange(r.id)}
                    className={`rounded-md px-2 py-1 text-xs font-medium ${
                      range === r.id
                        ? 'bg-teal-700 text-white'
                        : 'border border-slate-300 dark:border-slate-700'
                    }`}
                  >
                    {r.id}
                  </button>
                ))}
              </div>
            }
          >
            <PriceChart symbol={symbol} from={from} />
          </Card>
          <ReturnsAndVolatility symbol={symbol} />
        </div>
        <div>
          <ForecastPanelFor symbol={symbol} />
        </div>
      </div>
    </>
  );
}

function PriceChart({ symbol, from }: { symbol: string; from: string | null }) {
  const prices = usePrices(symbol, from);
  const data = useMemo(
    () =>
      (prices.data ?? []).map((c) => ({
        time: day(c.ts),
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      })),
    [prices.data],
  );
  if (prices.isPending) return <Skeleton className="h-[360px] w-full" />;
  if (prices.error)
    return <ErrorState error={prices.error} onRetry={() => void prices.refetch()} />;
  if (data.length === 0) return <EmptyState title="No candles in this range" />;
  return (
    <div className={prices.isFetching ? 'opacity-60 transition-opacity' : ''}>
      <CandleChart data={data} />
      <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
        {data.length.toLocaleString()} daily candles (UTC). Green = close above open, red = below.
      </p>
    </div>
  );
}

function ReturnsAndVolatility({ symbol }: { symbol: string }) {
  const features = useFeatures(symbol, 365);
  const { returns, vol } = useMemo(() => {
    const rows = [...(features.data?.data ?? [])].sort((a, b) => a.ts.localeCompare(b.ts));
    const series = (name: string) =>
      rows
        .filter((r) => r.values[name] != null)
        .map((r) => ({ time: day(r.ts), value: r.values[name]! }));
    return {
      returns: series('log_return_1d'),
      vol: [
        { name: '7-day volatility', color: '#f59e0b', data: series('volatility_7d') },
        { name: '30-day volatility', color: '#0f766e', data: series('volatility_30d') },
      ],
    };
  }, [features.data]);

  return (
    <Card title="Daily returns and volatility (model inputs, last 365 days)">
      {features.isPending ? (
        <Skeleton className="h-60 w-full" />
      ) : features.error ? (
        <ErrorState error={features.error} onRetry={() => void features.refetch()} />
      ) : returns.length === 0 ? (
        <EmptyState title="No features yet">Run the feature pipeline first.</EmptyState>
      ) : (
        <div className="space-y-6">
          <div>
            <h3 className="mb-1 text-sm font-medium">Daily log return</h3>
            <LineChart
              percent
              height={180}
              lines={[{ name: 'Daily log return', color: '#6366f1', data: returns }]}
            />
          </div>
          <div>
            <h3 className="mb-1 text-sm font-medium">Volatility (std. dev. of daily returns)</h3>
            <LineChart percent height={180} lines={vol} />
          </div>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            These are the exact feature values the model sees. Each uses only data up to that day
            (no look-ahead).
          </p>
        </div>
      )}
    </Card>
  );
}
