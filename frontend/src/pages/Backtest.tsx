import { useMemo } from 'react';
import { Link, useParams } from 'react-router';

import type { Schemas } from '../api/client';
import { useBacktest } from '../api/hooks';
import { LineChart, type LineSpec } from '../components/charts';
import {
  Card,
  DirectionLabel,
  Disclaimer,
  EmptyState,
  ErrorState,
  LoadingBlock,
  PageTitle,
} from '../components/ui';
import { day, formatDate, formatNumber, formatPercent } from '../lib/format';

type Perf = {
  start?: string;
  end?: string;
  total_return?: number;
  cagr?: number;
  sharpe?: number | null;
  sharpe_se?: number | null;
  max_drawdown?: number;
  volatility?: number;
  trades?: number;
  exposure?: number;
  total_cost?: number;
};

const ROWS: { key: string; label: string }[] = [
  { key: 'strategy_net', label: 'Strategy, after costs' },
  { key: 'strategy_gross', label: 'Strategy, before costs' },
  { key: 'buy_and_hold', label: 'Buy and hold' },
  { key: 'baseline_strategy', label: 'Same rules, baseline model' },
];

function curveLines(curve: Schemas['EquityCurve']): LineSpec[] {
  const pts = (values: (number | null)[]) =>
    curve.ts.flatMap((t, i) => {
      const v = values[i];
      return v == null ? [] : [{ time: day(t), value: v }];
    });
  return [
    { name: 'Strategy after costs', color: '#0f766e', data: pts(curve.strategy_net) },
    {
      name: 'Strategy before costs',
      color: '#0f766e',
      data: pts(curve.strategy_gross),
      dashed: true,
    },
    { name: 'Buy and hold', color: '#6366f1', data: pts(curve.buy_and_hold) },
    { name: 'Baseline model', color: '#94a3b8', data: pts(curve.baseline), dashed: true },
  ];
}

export default function Backtest() {
  const { id = '' } = useParams();
  const q = useBacktest(id);
  const lines = useMemo(
    () => (q.data?.equity_curve ? curveLines(q.data.equity_curve) : []),
    [q.data],
  );

  if (q.isPending) return <LoadingBlock label="Loading backtest" lines={10} />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;

  const b = q.data;
  const ev = b.evaluation as Record<string, Perf>;
  const cfg = b.config as Record<string, unknown>;
  const model = b.model as { name?: string; version?: number };
  const net = ev.strategy_net ?? {};
  const bh = ev.buy_and_hold ?? {};
  const beats = (b.verdict as { beats_buy_and_hold_sharpe?: boolean }).beats_buy_and_hold_sharpe;
  const yearly = b.yearly as Record<string, Record<string, number>>;
  const years = Object.keys(yearly.strategy_net ?? {}).sort();

  return (
    <>
      <PageTitle
        title={`Backtest: ${model.name ?? 'model'} v${model.version ?? '?'}`}
        subtitle={`Evaluation period ${formatDate(net.start)} → ${formatDate(net.end)} (hypothetical, after costs)`}
      />

      <div className="space-y-6">
        <Card title="Growth of 1 unit invested">
          {lines.length === 0 ? (
            <EmptyState title="No equity curve stored for this backtest" />
          ) : (
            <LineChart lines={lines} height={320} />
          )}
          <p className="mt-3 text-sm">
            <DirectionLabel direction={beats ? 'up' : 'down'} word={beats ? 'Higher' : 'Lower'} />{' '}
            risk-adjusted return (Sharpe {formatNumber(net.sharpe)} ± {formatNumber(net.sharpe_se)})
            than buy and hold (Sharpe {formatNumber(bh.sharpe)}). A Sharpe difference smaller than
            about two standard errors is within luck.
          </p>
        </Card>

        <Card title="Results (evaluation period only)">
          <div className="overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th />
                  <th>Total return</th>
                  <th>Per year (CAGR)</th>
                  <th>Sharpe</th>
                  <th>Worst drawdown</th>
                  <th>Trades</th>
                  <th>Costs paid</th>
                </tr>
              </thead>
              <tbody>
                {ROWS.filter((r) => ev[r.key]).map((r) => {
                  const p = ev[r.key]!;
                  return (
                    <tr key={r.key}>
                      <th scope="row" className="font-medium">
                        {r.label}
                      </th>
                      <td>{formatPercent(p.total_return)}</td>
                      <td>{formatPercent(p.cagr)}</td>
                      <td>{formatNumber(p.sharpe)}</td>
                      <td>{formatPercent(p.max_drawdown)}</td>
                      <td>{p.trades ?? '—'}</td>
                      <td>{formatPercent(p.total_cost)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>

        <div className="grid gap-6 lg:grid-cols-2">
          <Card title="Assumptions">
            <ul className="list-disc space-y-1 pl-5 text-sm">
              <li>
                Trading costs: {String(cfg.fee_bps ?? '?')} bps fee +{' '}
                {String(cfg.slippage_bps ?? '?')} bps slippage per unit traded.
              </li>
              <li>
                Orders fill at the{' '}
                {cfg.execution === 'next_close' ? 'next day’s close' : 'next day’s open'}, after the
                forecast is known (no look-ahead).
              </li>
              <li>
                {cfg.long_short ? 'Long or short' : 'Long or cash only (no short selling)'};
                position when P(up) &gt; {String(cfg.threshold ?? '?')}.
              </li>
              <li>
                The threshold was chosen on the earlier{' '}
                {formatPercent(Number(cfg.selection_fraction ?? 0.5), 0)} of folds (selection
                period) and then frozen; results above are from the later folds only.
              </li>
              <li>
                {cfg.vol_target
                  ? `Positions scaled to ${formatPercent(Number(cfg.vol_target), 0)} annual volatility.`
                  : 'No volatility targeting or leverage.'}
              </li>
              <li>Uses the model’s out-of-sample walk-forward forecasts only.</li>
              <li>No taxes, funding costs, market impact or exchange outages are modelled.</li>
            </ul>
          </Card>

          <Card title="Year by year (after costs)">
            {years.length === 0 ? (
              <p className="text-sm text-slate-500">No yearly breakdown.</p>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Year</th>
                    <th>Strategy</th>
                    <th>Buy and hold</th>
                  </tr>
                </thead>
                <tbody>
                  {years.map((y) => (
                    <tr key={y}>
                      <td>{y}</td>
                      <td>{formatPercent(yearly.strategy_net?.[y])}</td>
                      <td>{formatPercent(yearly.buy_and_hold?.[y])}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </div>

        <p className="text-sm">
          <Link className="link" to={`/models/${b.model_version_id}`}>
            View the model card
          </Link>
        </p>
        <Disclaimer text={b.disclaimer} />
      </div>
    </>
  );
}
