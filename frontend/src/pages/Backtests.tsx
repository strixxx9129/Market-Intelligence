import { Link } from 'react-router';

import { useBacktests } from '../api/hooks';
import {
  DirectionLabel,
  Disclaimer,
  EmptyState,
  ErrorState,
  LoadingBlock,
  PageTitle,
  Card,
} from '../components/ui';
import { formatDate, formatNumber, formatPercent } from '../lib/format';

export default function Backtests() {
  const q = useBacktests();
  return (
    <>
      <PageTitle
        title="Backtests"
        subtitle="What would have happened if a simple rule had followed a model’s out-of-sample forecasts, after trading costs, compared with just buying and holding. Hypothetical."
      />
      <Card>
        {q.isPending ? (
          <LoadingBlock label="Loading backtests" lines={6} />
        ) : q.error ? (
          <ErrorState error={q.error} onRetry={() => void q.refetch()} />
        ) : q.data.data.length === 0 ? (
          <EmptyState title="No backtests yet">
            Run <code>make backtest MV=&lt;model version id&gt;</code>.
          </EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Evaluation period</th>
                  <th>Costs</th>
                  <th>Strategy (after costs)</th>
                  <th>Buy and hold</th>
                  <th>Sharpe vs buy and hold</th>
                </tr>
              </thead>
              <tbody>
                {q.data.data.map((b) => (
                  <tr key={b.id}>
                    <td>
                      <Link className="link" to={`/backtests/${b.id}`}>
                        {b.model_name} v{b.model_version}
                      </Link>
                    </td>
                    <td>
                      {formatDate(b.evaluation_start)} → {formatDate(b.evaluation_end)}
                    </td>
                    <td>{b.cost_bps} bps per trade</td>
                    <td>{formatPercent(b.strategy_net_total_return)}</td>
                    <td>{formatPercent(b.buy_and_hold_total_return)}</td>
                    <td>
                      <DirectionLabel
                        direction={b.beats_buy_and_hold_sharpe ? 'up' : 'down'}
                        word={b.beats_buy_and_hold_sharpe ? 'Higher' : 'Lower'}
                      />{' '}
                      <span className="text-slate-500">
                        {formatNumber(b.strategy_net_sharpe)} vs{' '}
                        {formatNumber(b.buy_and_hold_sharpe)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <div className="mt-6">
        <Disclaimer text={q.data?.disclaimer} />
      </div>
    </>
  );
}
