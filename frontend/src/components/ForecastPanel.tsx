/**
 * The forecast panel. How it stays honest about uncertainty:
 *
 * 1. It shows a PROBABILITY ("53% estimated chance the next daily close is higher"), never an
 *    instruction. There is no "buy", "sell", "signal" or "target" anywhere (tested).
 * 2. A probability near 50% is called what it is: "close to a coin flip".
 * 3. Every number sits next to its yardstick: the model's live track record vs always-up and
 *    a coin flip, and "not enough data yet" when there are too few scored forecasts.
 * 4. It says when production serves a baseline (no model beat it in validation).
 * 5. The disclaimer renders in EVERY state: loading, error, missing probability, success.
 */
import { Link } from 'react-router';

import type { Schemas } from '../api/client';
import { useForecast } from '../api/hooks';
import { leaning, MIN_TRACK_RECORD } from '../lib/forecast';
import { formatDate, formatDateTime, formatNumber, formatPercent } from '../lib/format';
import { Badge, Card, Disclaimer, ErrorState, LoadingBlock } from './ui';
import { DirectionLabel } from './ui';

type Forecast = Schemas['ForecastOut'];

export function ForecastPanel({
  forecast,
  isLoading = false,
  error = null,
  onRetry,
}: {
  forecast?: Forecast;
  isLoading?: boolean;
  error?: unknown;
  onRetry?: () => void;
}) {
  let body;
  if (isLoading) body = <LoadingBlock label="Loading forecast" lines={4} />;
  else if (error) body = <ErrorState error={error} onRetry={onRetry} />;
  else if (!forecast) body = <p className="text-slate-600 dark:text-slate-400">No forecast yet.</p>;
  else body = <ForecastBody f={forecast} />;

  return (
    <Card title="Next-day forecast" className="space-y-4">
      {body}
      <div className="mt-4 border-t border-slate-200 pt-3 dark:border-slate-800">
        <Disclaimer text={forecast?.disclaimer} />
      </div>
    </Card>
  );
}

function ForecastBody({ f }: { f: Forecast }) {
  const p = f.p_up;
  return (
    <div className="space-y-4">
      {p == null ? (
        <p>This model doesn’t output a probability, so no chance of an up day is shown.</p>
      ) : (
        <Probability p={p} target={f.target_ts} />
      )}

      <dl className="grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt className="text-slate-500 dark:text-slate-400">Model</dt>
          <dd>
            <Link className="link" to={`/models/${f.model_version_id}`}>
              {f.model_version}
            </Link>{' '}
            {f.is_baseline_model && <Badge tone="amber">baseline</Badge>}
          </dd>
        </div>
        <div>
          <dt className="text-slate-500 dark:text-slate-400">Based on data up to</dt>
          <dd>{formatDateTime(f.as_of_ts)}</dd>
        </div>
      </dl>

      {f.is_baseline_model && (
        <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">
          This is a <strong>baseline</strong>: the historical share of up days. No machine-learning
          model has beaten it in out-of-sample validation yet, so it is what we serve.
        </p>
      )}

      <TrackRecordView t={f.track_record} />
    </div>
  );
}

function Probability({ p, target }: { p: number; target: string }) {
  const lean = leaning(p);
  return (
    <div>
      <p className="text-sm text-slate-600 dark:text-slate-400">
        Estimated chance the daily close on {formatDate(target)} is higher than the day before
      </p>
      <p className="mt-1 text-4xl font-bold tabular-nums">{formatPercent(p, 0)}</p>
      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
        <div
          className="h-full rounded-full bg-teal-600"
          style={{ width: `${Math.round(p * 100)}%` }}
          role="meter"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(p * 100)}
          aria-label="Estimated probability of an up day"
        />
      </div>
      <p className="mt-2 text-sm">
        <DirectionLabel direction={lean.direction} word={lean.label} />{' '}
        <span className="text-slate-600 dark:text-slate-400">
          ({formatPercent(1 - p, 0)} estimated chance it is lower)
        </span>
      </p>
    </div>
  );
}

function TrackRecordView({ t }: { t: Schemas['TrackRecord'] }) {
  const enough = t.n >= MIN_TRACK_RECORD;
  return (
    <div className="rounded-md border border-slate-200 p-3 text-sm dark:border-slate-800">
      <h3 className="font-medium">
        Live track record (last {t.window_days} days, {t.n} scored{' '}
        {t.n === 1 ? 'forecast' : 'forecasts'})
      </h3>
      {!enough ? (
        <p className="mt-1 text-slate-600 dark:text-slate-400">
          Not enough scored forecasts yet to judge this model live (fewer than {MIN_TRACK_RECORD}).
          Each forecast is scored a day later, once the real close is known.
        </p>
      ) : (
        <table className="mt-2 w-full text-left tabular-nums">
          <thead className="text-slate-500 dark:text-slate-400">
            <tr>
              <th className="font-normal">Measure</th>
              <th className="font-normal">This model</th>
              <th className="font-normal">Yardstick</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Direction correct</td>
              <td>{formatPercent(t.accuracy)}</td>
              <td>{formatPercent(t.always_up_accuracy)} (always guessing “up”)</td>
            </tr>
            <tr>
              <td>Log loss (lower is better)</td>
              <td>{formatNumber(t.log_loss, 3)}</td>
              <td>{formatNumber(t.coin_flip_log_loss, 3)} (coin flip)</td>
            </tr>
          </tbody>
        </table>
      )}
      {t.note && <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">{t.note}</p>}
    </div>
  );
}

/** Connected version: fetches the forecast for `symbol`. */
export function ForecastPanelFor({ symbol }: { symbol: string }) {
  const q = useForecast(symbol);
  return (
    <ForecastPanel
      forecast={q.data}
      isLoading={q.isPending}
      error={q.error}
      onRetry={() => void q.refetch()}
    />
  );
}
