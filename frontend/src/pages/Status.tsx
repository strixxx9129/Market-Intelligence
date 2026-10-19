import { useStatus } from '../api/hooks';
import { Badge, Card, EmptyState, ErrorState, LoadingBlock, PageTitle } from '../components/ui';
import { formatDateTime, timeAgo } from '../lib/format';

const SEVERITY = {
  critical: { tone: 'red', icon: '✖' },
  warning: { tone: 'amber', icon: '⚠' },
  info: { tone: 'slate', icon: 'ⓘ' },
} as const;

const RUN_TONE: Record<string, 'green' | 'red' | 'amber' | 'slate'> = {
  success: 'green',
  failed: 'red',
  retrying: 'amber',
  running: 'amber',
};

export default function Status() {
  const q = useStatus();
  if (q.isPending) return <LoadingBlock label="Loading status" lines={8} />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const s = q.data;

  return (
    <>
      <PageTitle
        title={
          <>
            System status{' '}
            <Badge tone={s.status === 'ok' ? 'green' : 'amber'}>
              {s.status === 'ok' ? '✔ All good' : '⚠ Degraded'}
            </Badge>
          </>
        }
        subtitle={`Checked ${formatDateTime(s.generated_at)} · refreshes every minute · production model: ${s.production_model ?? 'none'}`}
      />
      <div className="space-y-6">
        <Card title="Active alerts">
          {s.alerts.length === 0 ? (
            <EmptyState title="No active alerts" />
          ) : (
            <ul className="space-y-2">
              {s.alerts.map((a, i) => (
                <li key={`${a.code}-${i}`} className="flex gap-2 text-sm">
                  <Badge tone={SEVERITY[a.severity].tone}>
                    {SEVERITY[a.severity].icon} {a.severity}
                  </Badge>
                  <span>
                    {a.message} <code className="text-xs text-slate-500">{a.code}</code>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Data freshness">
          <div className="overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>Asset</th>
                  <th>Newest candle</th>
                  <th>Newest features</th>
                  <th>Newest forecast</th>
                  <th>Age</th>
                </tr>
              </thead>
              <tbody>
                {s.freshness.map((f) => (
                  <tr key={f.symbol}>
                    <td>{f.symbol}</td>
                    <td>{formatDateTime(f.latest_candle_ts)}</td>
                    <td>{formatDateTime(f.latest_feature_ts)}</td>
                    <td>{formatDateTime(f.latest_forecast_as_of)}</td>
                    <td>
                      {f.stale_days == null
                        ? '—'
                        : `${f.stale_days} day${f.stale_days === 1 ? '' : 's'}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
            A daily candle opens at 00:00 UTC and closes 24 h later, so 1 day old is normal.
          </p>
        </Card>

        <Card title="Recent pipeline runs">
          {s.last_runs.length === 0 ? (
            <EmptyState title="No runs yet" />
          ) : (
            <div className="overflow-x-auto">
              <table className="table">
                <thead>
                  <tr>
                    <th>Job</th>
                    <th>Result</th>
                    <th>Started</th>
                    <th>Attempt</th>
                    <th>Error</th>
                  </tr>
                </thead>
                <tbody>
                  {s.last_runs.map((r) => (
                    <tr key={`${r.job}-${r.started_at}`}>
                      <td>{r.job}</td>
                      <td>
                        <Badge tone={RUN_TONE[r.status] ?? 'slate'}>{r.status}</Badge>
                      </td>
                      <td title={formatDateTime(r.started_at)}>{timeAgo(r.started_at)}</td>
                      <td>{r.attempt}</td>
                      <td
                        className="max-w-xs truncate text-xs text-slate-500"
                        title={r.error ?? ''}
                      >
                        {r.error ?? ''}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
