"""Render docs/backtest-report.md from stored backtests (a pure function of the DB)."""

from datetime import UTC, datetime
from typing import Any

from marketlens.models import Backtest

ROWS = [
    ("total_return", "Total return", "pct"),
    ("cagr", "CAGR", "pct"),
    ("volatility", "Volatility (ann.)", "pct"),
    ("sharpe", "Sharpe", "num"),
    ("sortino", "Sortino", "num"),
    ("max_drawdown", "Max drawdown", "pct"),
    ("max_drawdown_duration_days", "Max DD duration (days)", "int"),
    ("exposure", "Exposure", "pct"),
    ("turnover_per_year", "Turnover / year", "num"),
    ("hit_rate", "Hit rate (days in market)", "pct"),
    ("trades", "Trades", "int"),
    ("trade_win_rate", "Trade win rate", "pct"),
    ("total_cost", "Costs paid (sum of daily)", "pct"),
]

GLOSSARY = """## Metric glossary

- **CAGR**: the constant yearly growth rate that turns 1 into the final equity.
- **Volatility**: standard deviation of daily returns x √365 (crypto trades every day).
- **Sharpe**: mean ÷ std of daily returns x √365, risk-free rate 0. Return per unit of total
  risk. Shown ± its approximate standard error (Lo 2002): over ~6 years a Sharpe is only known to
  about ±0.4, so differences smaller than that are noise.
- **Sortino**: like Sharpe, but only downside moves count as risk.
- **Max drawdown**: the worst fall from a previous equity peak; **duration**: the longest time
  spent below a previous peak.
- **Turnover / year**: position traded per year (1 = fully in or out once). Costs scale with it.
- **Hit rate**: share of in-market days with a positive net return. **Trade win rate**: share of
  trades that made money after costs.
- **Exposure**: share of days with a position. A strategy that is out of the market half the
  time naturally has lower volatility; compare Sharpe, not just return.

## Why these choices

- **Execution timing.** A prediction for day t+1 is only known after day t closes. `next_open`
  trades at the first price after that (≈ the same moment for 24/7 crypto); `next_close` waits a
  full day and so trades the day *after* the one predicted. The engine never fills at the close
  the signal was computed from; trading there would be look-ahead.
- **Costs.** Every unit of position traded pays fee + slippage. A strategy that flips often can
  turn a positive gross edge into a net loss; buy-and-hold pays once. Compare the cost sweep.
- **No tuning on the reported period.** The threshold is chosen on the earlier folds only; the
  evaluation folds are untouched. The selection-period Sharpe is shown next to the evaluation
  Sharpe: the gap is the optimism you'd get from tuning on the period you report.
"""


def _fmt(value: Any, kind: str) -> str:
    if value is None:
        return "n/a"
    if kind == "pct":
        return f"{value:.1%}"
    if kind == "int":
        return f"{int(value):,}"
    return f"{value:.2f}"


def _sharpe(m: dict[str, Any]) -> str:
    if m.get("sharpe") is None:
        return "n/a"
    se = m.get("sharpe_se")
    return f"{m['sharpe']:.2f}" + (f" ± {se:.2f}" if se is not None else "")


def render_detail(bt: Backtest) -> str:
    m, c = bt.metrics, bt.config
    ev = m["evaluation"]
    lines = [
        f"### `{m['model']['name']}` v{m['model']['version']} (model version {m['model']['id']})",
        "",
        f"- Signal: {'long/short' if c['long_short'] else 'long/flat'}, "
        f"{'P(up)' if m['prediction_kind'] == 'proba' else 'predicted return'} > "
        f"**{m['threshold']['chosen']}** (chosen on selection folds "
        f"{m['folds']['selection'][0]}-{m['folds']['selection'][-1]}; reported on folds "
        f"{m['folds']['evaluation'][0]}-{m['folds']['evaluation'][-1]})",
        f"- Execution `{c['execution']}`, costs {c['fee_bps']:g} bps fee + {c['slippage_bps']:g} "
        f"bps slippage per unit traded"
        + (f", volatility target {c['vol_target']:.0%}" if c.get("vol_target") else ""),
        f"- Baseline strategy: `{m['baseline_model']['algo']}` through the same rule "
        f"(threshold {m['baseline_threshold']})",
        f"- Evaluation period: {ev['strategy_net']['start'][:10]} → "
        f"{ev['strategy_net']['end'][:10]}"
        f" ({ev['strategy_net']['days']:,} days). Equity curve: `{bt.equity_curve_uri}`",
        "",
        "| metric | strategy (net) | strategy (gross) | buy & hold | baseline strategy |",
        "|---|---|---|---|---|",
    ]
    for key, label, kind in ROWS:
        cells = [
            _fmt(ev[col].get(key), kind) if key != "sharpe" else _sharpe(ev[col])
            for col in ("strategy_net", "strategy_gross", "buy_and_hold", "baseline_strategy")
        ]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    sel = m["selection"]
    lines += [
        "",
        "**Selection vs evaluation (net Sharpe)**: "
        f"selection {_sharpe(sel['strategy_net'])} (buy & hold {_sharpe(sel['buy_and_hold'])}) → "
        f"evaluation {_sharpe(ev['strategy_net'])} (buy & hold {_sharpe(ev['buy_and_hold'])}).",
        "",
        "<details><summary>Yearly returns, sensitivity and threshold grid</summary>",
        "",
        "| year | strategy (net) | buy & hold | baseline strategy |",
        "|---|---|---|---|",
    ]
    y = m["yearly"]
    for year in sorted(y["strategy_net"]):
        lines.append(
            f"| {year} | {_fmt(y['strategy_net'].get(year), 'pct')} | "
            f"{_fmt(y['buy_and_hold'].get(year), 'pct')} | "
            f"{_fmt(y['baseline_strategy'].get(year), 'pct')} |"
        )
    sens = m["sensitivity"]
    lines += ["", "| execution | CAGR | Sharpe | max drawdown |", "|---|---|---|---|"]
    for name, s in sens["execution"].items():
        lines.append(
            f"| `{name}` | {_fmt(s['cagr'], 'pct')} | {_sharpe(s)} | "
            f"{_fmt(s['max_drawdown'], 'pct')} |"
        )
    lines += [
        "",
        "| total cost per unit traded | strategy CAGR | strategy Sharpe | buy & hold CAGR |"
        " buy & hold Sharpe |",
        "|---|---|---|---|---|",
    ]
    for bps, s in sens["costs_bps"].items():
        b = sens["buy_and_hold_costs_bps"][bps]
        lines.append(
            f"| {bps} bps | {_fmt(s['cagr'], 'pct')} | {_sharpe(s)} | "
            f"{_fmt(b['cagr'], 'pct')} | {_sharpe(b)} |"
        )
    lines += [
        "",
        "| threshold | selection Sharpe (net) | CAGR | exposure | turnover/yr |",
        "|---|---|---|---|---|",
    ]
    for row in m["threshold"]["selection_table"]:
        mark = " ← chosen" if row["threshold"] == m["threshold"]["chosen"] else ""
        lines.append(
            f"| {row['threshold']}{mark} | {_fmt(row['sharpe'], 'num')} "
            f"| {_fmt(row['cagr'], 'pct')} | {_fmt(row['exposure'], 'pct')} "
            f"| {_fmt(row['turnover_per_year'], 'num')} |"
        )
    lines += ["", "</details>", ""]
    return "\n".join(lines)


def render_backtest_report(backtests: list[Backtest]) -> str:
    head = [
        "# Backtest report",
        "",
        "<!-- GENERATED by `marketlens backtest-report` from the backtests table. "
        "Do not edit by hand. -->",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}.",
        "",
        "> **Hypothetical backtest on historical data. Educational only, not financial advice.** "
        "Strategies are built from out-of-sample predictions only; past performance says little "
        "about the future.",
        "",
    ]
    if not backtests:
        return "\n".join(
            [*head, "No backtests yet: run `make backtest MV=<model version id>`.", ""]
        )
    ordered = sorted(
        backtests,
        key=lambda b: (
            b.metrics["model"]["task"],
            -(b.metrics["evaluation"]["strategy_net"]["sharpe"] or -9),
        ),
    )
    summary = [
        "## Summary (evaluation folds, after costs)",
        "",
        "| model | task | threshold | CAGR | Sharpe | max DD | trades | buy & hold CAGR | "
        "buy & hold "
        "Sharpe | baseline strategy Sharpe | beats B&H Sharpe? |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for bt in ordered:
        m = bt.metrics
        s, b = m["evaluation"]["strategy_net"], m["evaluation"]["buy_and_hold"]
        base = m["evaluation"]["baseline_strategy"]
        summary.append(
            f"| `{m['model']['algo']}` v{m['model']['version']} | {m['model']['task']} | "
            f"{m['threshold']['chosen']} | {_fmt(s['cagr'], 'pct')} | {_sharpe(s)} | "
            f"{_fmt(s['max_drawdown'], 'pct')} | {s.get('trades', 0):,} | "
            f"{_fmt(b['cagr'], 'pct')} | "
            f"{_sharpe(b)} | {_sharpe(base)} | "
            f"{'yes' if m['verdict']['beats_buy_and_hold_sharpe'] else 'no'} |"
        )
    beaters = [bt for bt in ordered if bt.metrics["verdict"]["beats_buy_and_hold_sharpe"]]
    significant = [
        bt
        for bt in beaters
        if bt.metrics["verdict"]["sharpe_difference_vs_bh"]
        > 2 * (bt.metrics["verdict"]["sharpe_se"] or 0)
    ]
    verdict = (
        "**No strategy beats buy-and-hold on risk-adjusted return after costs.**"
        if not beaters
        else (
            f"{len(beaters)} strateg{'y shows' if len(beaters) == 1 else 'ies show'} "
            "a higher net Sharpe than buy-and-hold, "
            f"{'but none' if not significant else str(len(significant))} by more "
            "than two standard errors"
            + (": within the noise of a single historical path." if not significant else ".")
        )
    )
    summary += ["", verdict, ""]
    details = ["## Details", "", *[render_detail(bt) for bt in ordered]]
    return "\n".join([*head, *summary, *details, GLOSSARY])
