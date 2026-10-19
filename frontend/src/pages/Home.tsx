import { Link } from 'react-router';

import type { Schemas } from '../api/client';
import { useAssets, useForecast } from '../api/hooks';
import {
  Card,
  DirectionLabel,
  Disclaimer,
  EmptyState,
  ErrorState,
  PageTitle,
  Skeleton,
} from '../components/ui';
import { leaning } from '../lib/forecast';
import { directionOf, formatDate, formatPercent, formatPrice } from '../lib/format';

export default function Home() {
  const assets = useAssets();
  return (
    <>
      <PageTitle
        title="Market overview"
        subtitle="Latest daily prices and what our models estimate for the next day, with their track record."
      />
      {assets.isPending ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Card key={i}>
              <div role="status" aria-label="Loading asset" className="space-y-3">
                <Skeleton className="h-5 w-1/3" />
                <Skeleton className="h-8 w-1/2" />
                <Skeleton className="h-4 w-2/3" />
              </div>
            </Card>
          ))}
        </div>
      ) : assets.error ? (
        <ErrorState error={assets.error} onRetry={() => void assets.refetch()} />
      ) : assets.data.length === 0 ? (
        <EmptyState title="No assets yet">
          Run the ingestion pipeline (<code>make ingest</code>) to load price history.
        </EmptyState>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {assets.data.map((a) => (
            <AssetCard key={a.symbol} asset={a} />
          ))}
        </div>
      )}
      <div className="mt-8">
        <Disclaimer />
      </div>
    </>
  );
}

function AssetCard({ asset }: { asset: Schemas['AssetOut'] }) {
  const change = asset.change_1d_pct == null ? null : asset.change_1d_pct / 100;
  return (
    <Card>
      <Link to={`/assets/${asset.symbol}`} className="group block">
        <div className="flex items-baseline justify-between">
          <h2 className="text-lg font-semibold group-hover:underline">{asset.name}</h2>
          <span className="text-sm text-slate-500 dark:text-slate-400">{asset.symbol}</span>
        </div>
        <p className="mt-2 text-2xl font-bold tabular-nums">
          {formatPrice(asset.latest_close, asset.quote_currency)}
        </p>
        <p className="mt-1 text-sm">
          {change == null ? (
            <span className="text-slate-500">No previous close</span>
          ) : (
            <>
              <DirectionLabel direction={directionOf(change)}>
                <span className="tabular-nums">{formatPercent(Math.abs(change), 2)}</span>
              </DirectionLabel>{' '}
              <span className="text-slate-500 dark:text-slate-400">vs previous daily close</span>
            </>
          )}
        </p>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          Daily close of {formatDate(asset.latest_ts)} (UTC)
        </p>
      </Link>
      <div className="mt-4 border-t border-slate-200 pt-3 dark:border-slate-800">
        <ForecastSummary symbol={asset.symbol} />
      </div>
    </Card>
  );
}

function ForecastSummary({ symbol }: { symbol: string }) {
  const f = useForecast(symbol);
  if (f.isPending) return <Skeleton className="h-10 w-full" />;
  if (f.error || !f.data)
    return <p className="text-sm text-slate-500 dark:text-slate-400">No forecast available.</p>;
  const p = f.data.p_up;
  return (
    <div className="space-y-1 text-sm">
      {p == null ? (
        <p>Latest forecast has no probability.</p>
      ) : (
        <p>
          <span className="font-semibold tabular-nums">{formatPercent(p, 0)}</span> estimated chance
          of a higher close on {formatDate(f.data.target_ts)} ·{' '}
          <DirectionLabel direction={leaning(p).direction} word={leaning(p).label} />
        </p>
      )}
      <p className="text-xs text-slate-500 dark:text-slate-400">
        {f.data.model_version}
        {f.data.is_baseline_model ? ' (baseline)' : ''} · track record: {f.data.track_record.n}{' '}
        scored
      </p>
      <Disclaimer compact text="Estimate, not advice." />
    </div>
  );
}
