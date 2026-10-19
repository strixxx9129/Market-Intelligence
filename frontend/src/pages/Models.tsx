import { Link } from 'react-router';

import type { Schemas } from '../api/client';
import { useLeaderboard } from '../api/hooks';
import { FoldChart } from '../components/FoldChart';
import { HIGHER_IS_BETTER } from '../lib/folds';
import {
  Badge,
  Card,
  Disclaimer,
  EmptyState,
  ErrorState,
  LoadingBlock,
  PageTitle,
} from '../components/ui';
import { formatDateTime, formatNumber } from '../lib/format';

export default function Models() {
  const q = useLeaderboard();
  return (
    <>
      <PageTitle
        title="Model leaderboard"
        subtitle="Every model is scored on the same walk-forward test folds (trained only on the past, tested on the next period) and compared with a simple baseline."
      />
      {q.isPending ? (
        <LoadingBlock label="Loading leaderboard" lines={8} />
      ) : q.error ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : q.data.length === 0 ? (
        <EmptyState title="No training runs yet">
          Train models with <code>make train</code>.
        </EmptyState>
      ) : (
        <div className="space-y-6">
          {q.data.map((board) => (
            <Board key={board.training_run_id} board={board} />
          ))}
        </div>
      )}
      <div className="mt-6">
        <Disclaimer />
      </div>
    </>
  );
}

function Board({ board }: { board: Schemas['Leaderboard'] }) {
  const v = board.verdict as {
    beats_baseline?: boolean;
    baseline?: string;
    best_model?: string;
    wins?: number;
    folds?: number;
  };
  const metric = board.primary_metric;
  const baseline = board.entries.find((e) => e.is_baseline && e.name.endsWith(v.baseline ?? '§'));
  const better = HIGHER_IS_BETTER.has(metric) ? 'higher' : 'lower';

  return (
    <Card title={`${board.task} · ${board.folds} folds · ${metric} (${better} is better)`}>
      <p
        className={`mb-4 rounded-md p-3 text-sm ${
          v.beats_baseline
            ? 'bg-emerald-50 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200'
            : 'bg-amber-50 text-amber-900 dark:bg-amber-950 dark:text-amber-200'
        }`}
      >
        {v.beats_baseline
          ? `▲ ${v.best_model} beats the ${v.baseline} baseline reliably (on ${v.wins} of ${v.folds} folds).`
          : `■ No model beats the ${v.baseline} baseline reliably. Daily direction is very hard to predict; this is an honest result, not a bug.`}
      </p>
      <div className="overflow-x-auto">
        <table className="table">
          <thead>
            <tr>
              <th>Model</th>
              <th>Stage</th>
              <th>{metric} (mean ± std over folds)</th>
              <th>vs baseline, fold by fold</th>
            </tr>
          </thead>
          <tbody>
            {board.entries.map((e) => (
              <tr key={e.name}>
                <td>
                  {e.model_version_id ? (
                    <Link className="link" to={`/models/${e.model_version_id}`}>
                      {e.name}
                    </Link>
                  ) : (
                    e.name
                  )}{' '}
                  {e.version != null && <span className="text-slate-500">v{e.version}</span>}{' '}
                  {e.is_baseline && <Badge tone="amber">baseline</Badge>}
                  <div className="text-xs text-slate-500">{e.family}</div>
                </td>
                <td>
                  {e.stage && (
                    <Badge tone={e.stage === 'production' ? 'teal' : 'slate'}>{e.stage}</Badge>
                  )}
                </td>
                <td>
                  {formatNumber(e.score.mean, 4)} ± {formatNumber(e.score.std, 4)}
                </td>
                <td>
                  {e.is_baseline || !e.per_fold || !baseline?.per_fold ? (
                    <span className="text-slate-500">—</span>
                  ) : (
                    <FoldChart model={e.per_fold} baseline={baseline.per_fold} metric={metric} />
                  )}
                  {e.vs_best_baseline && (
                    <div className="text-xs text-slate-500">
                      {(e.vs_best_baseline as { significant?: boolean }).significant
                        ? 'Statistically significant difference'
                        : 'Difference not statistically significant'}
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
        Training run finished {formatDateTime(board.finished_at)}.
      </p>
    </Card>
  );
}
