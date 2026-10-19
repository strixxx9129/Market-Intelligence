import { Link, useParams } from 'react-router';

import { useModelCard } from '../api/hooks';
import {
  Badge,
  Card,
  Disclaimer,
  ErrorState,
  LoadingBlock,
  PageTitle,
  Stat,
} from '../components/ui';
import { formatDate, formatDateTime, formatNumber } from '../lib/format';

type Summary = Record<string, { mean: number | null; std: number | null }>;
type Calibration = {
  bin: number;
  n: number;
  mean_pred: number;
  observed_rate: number;
  gap: number;
}[];
type Importance = { mean?: Record<string, number>; leak_flags?: { feature: string }[] };

export default function ModelCard() {
  const id = Number(useParams().id);
  const q = useModelCard(id);

  if (q.isPending) return <LoadingBlock label="Loading model card" lines={10} />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const m = q.data;
  const metrics = m.metrics as {
    primary_metric?: string;
    summary?: Summary;
    calibration?: Calibration;
    importance?: Importance;
    per_fold?: Record<string, number[]>;
  };
  const split = (m.training as { split?: Record<string, unknown> }).split ?? {};
  const importance = Object.entries(metrics.importance?.mean ?? {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10);
  const maxImp = Math.max(...importance.map(([, v]) => Math.abs(v)), 1e-9);

  return (
    <>
      <PageTitle
        title={
          <>
            {m.name} <span className="text-slate-500">v{m.version}</span>{' '}
            <Badge tone={m.stage === 'production' ? 'teal' : 'slate'}>{m.stage}</Badge>
          </>
        }
        subtitle={`${m.algo} · ${m.task} · feature set ${m.feature_set}`}
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Out-of-sample performance (walk-forward)">
          <dl className="grid grid-cols-2 gap-4">
            {Object.entries(metrics.summary ?? {}).map(([name, s]) => (
              <Stat
                key={name}
                label={name === metrics.primary_metric ? `${name} (primary)` : name}
                value={formatNumber(s.mean, 4)}
                hint={`± ${formatNumber(s.std, 4)} across folds`}
              />
            ))}
          </dl>
        </Card>

        <Card title="How it was trained">
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <Stat
              label="Training window"
              value={`${formatDate(m.train_start)} → ${formatDate(m.train_end)}`}
            />
            <Stat
              label="Validation"
              value={`${String(split.method ?? '—')}, ${String(split.n_folds ?? '?')} folds`}
              hint={`gap ${String(split.gap ?? '?')} days between train and test`}
            />
            <Stat
              label="Git commit"
              value={<code>{String((m.training as { git_sha?: string }).git_sha ?? '—')}</code>}
            />
            <Stat label="Seed" value={String((m.training as { seed?: number }).seed ?? '—')} />
          </dl>
          <details className="mt-3 text-sm">
            <summary className="cursor-pointer font-medium">Parameters and hashes</summary>
            <pre className="mt-2 overflow-x-auto rounded bg-slate-100 p-2 text-xs dark:bg-slate-800">
              {JSON.stringify(
                {
                  params: m.params,
                  dataset_hash: (m.training as { dataset_hash?: string }).dataset_hash,
                  feature_set_code_hash: m.feature_set_code_hash,
                  mlflow_run_id: m.mlflow_run_id,
                },
                null,
                2,
              )}
            </pre>
          </details>
        </Card>

        {metrics.calibration && metrics.calibration.length > 0 && (
          <Card title="Calibration: does “60%” really mean 60%?">
            <table className="table">
              <thead>
                <tr>
                  <th>Predicted P(up)</th>
                  <th>Actually went up</th>
                  <th>Gap</th>
                  <th>Days</th>
                </tr>
              </thead>
              <tbody>
                {metrics.calibration.map((b) => (
                  <tr key={b.bin}>
                    <td>{(b.mean_pred * 100).toFixed(1)}%</td>
                    <td>{(b.observed_rate * 100).toFixed(1)}%</td>
                    <td>{(b.gap * 100).toFixed(1)} pts</td>
                    <td>{b.n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}

        {importance.length > 0 && (
          <Card title="Which inputs matter (permutation importance)">
            <ul className="space-y-1.5 text-sm">
              {importance.map(([name, v]) => (
                <li key={name} className="grid grid-cols-[10rem_1fr_4rem] items-center gap-2">
                  <span className="truncate">{name}</span>
                  <span className="h-2 rounded bg-slate-200 dark:bg-slate-800">
                    <span
                      className="block h-2 rounded bg-teal-600"
                      style={{ width: `${(Math.max(v, 0) / maxImp) * 100}%` }}
                    />
                  </span>
                  <span className="text-right tabular-nums">{v.toFixed(4)}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
              How much the score worsens when one input is shuffled. Near zero = the model barely
              uses it.
            </p>
          </Card>
        )}

        <Card title="Promotion history">
          {m.promotion_history.length === 0 ? (
            <p className="text-sm text-slate-500">No promotion decisions involve this model.</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {m.promotion_history.map((d, i) => (
                <li key={i}>
                  <Badge tone={d.decision === 'promoted' ? 'green' : 'slate'}>{d.decision}</Badge>{' '}
                  {formatDateTime(d.decided_at)}
                  <ul className="ml-4 list-disc text-slate-600 dark:text-slate-400">
                    {d.reasons.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Backtests">
          {m.backtests.length === 0 ? (
            <p className="text-sm text-slate-500">Not backtested yet.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {(m.backtests as { id: string; created_at: string }[]).map((b) => (
                <li key={b.id}>
                  <Link className="link" to={`/backtests/${b.id}`}>
                    Backtest of {formatDate(b.created_at)}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <div className="mt-6">
        <Disclaimer text={m.disclaimer} />
      </div>
    </>
  );
}
