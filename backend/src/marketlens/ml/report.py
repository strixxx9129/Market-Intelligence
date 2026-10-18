"""Render docs/experiment-report.md from stored training runs (a pure function of the DB).

The report is regenerated, never hand-edited, so it always matches what was actually run.
Conclusions are computed with the same rule for every model ("beats the baseline" = better
on average by more than 2 standard errors AND better in most folds). "No model beats the
baseline" is printed as plainly as a win would be.
"""

from datetime import UTC, datetime
from typing import Any

from marketlens.ml.evaluate import METRIC_DIRECTION
from marketlens.models import FeatureSet, TrainingRun

METRICS = {
    "regression": ["mae", "rmse", "directional_accuracy"],
    "classification": ["log_loss", "brier", "roc_auc", "accuracy"],
}
PCT_METRICS = {"directional_accuracy", "accuracy"}


def _fmt(metric: str, value: float | None, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    if metric in PCT_METRICS:
        return f"{value * 100:+.2f} pp" if signed else f"{value:.2%}"
    return f"{value:+.5f}" if signed else f"{value:.5f}"


def _verdict_text(task: str, m: dict[str, Any]) -> str:
    v = m["verdict"]
    primary = m["primary_metric"]
    best = v.get("best_model")
    if best is None:
        return "No non-baseline model was trained."
    wins = f"{v.get('wins', 0)}/{v.get('folds', 0)}"
    imp = _fmt(primary, v.get("improvement_mean"), signed=True)
    if v["beats_baseline"]:
        return (
            f"**`{best}` beats the best baseline (`{v['baseline']}`) on {primary}**: average "
            f"improvement {imp} per fold, better in {wins} folds (more than 2 standard errors). "
            "Check calibration, per-era stability and the backtest before trusting it."
        )
    return (
        f"**No model reliably beats the baseline on {primary}.** The best model, `{best}`, "
        f"differs from `{v.get('baseline')}` by {imp} on average and is better in only {wins} "
        "folds: within noise. That is a valid and common result for next-day returns, and "
        "consistent with the weak, fading autocorrelation found in the EDA."
    )


def render_task(run: TrainingRun, fs: FeatureSet) -> str:
    m = run.metrics
    task, primary = run.task, m["primary_metric"]
    better = "lower" if METRIC_DIRECTION[primary] == "min" else "higher"
    split = run.split_config
    lines = [
        f"## {task.capitalize()}: "
        + ("next-day log return" if task == "regression" else "next-day up / down"),
        "",
        f"- **Data:** {m['n_rows']:,} days, {m['date_range'][0][:10]} → {m['date_range'][1][:10]}"
        f" (up-days: {m['up_rate']:.1%})",
        f"- **Features:** set `{fs.version}` (code `{fs.code_hash[:12]}`), "
        f"dataset hash `{(run.dataset_hash or '')[:12]}`",
        f"- **Validation:** walk-forward, {split['window']} window, initial train "
        f"{split['initial_train']} days, test {split['test_size']} days, gap {split['gap']} days,"
        f" **{split['n_folds']} folds**",
        f"- **Run:** `{run.id}`, git `{run.git_sha}`, seed {run.seed}, MLflow run "
        f"`{run.mlflow_run_id}` (experiment `{run.mlflow_experiment}`)",
        "",
        "### Verdict",
        "",
        _verdict_text(task, m),
        "",
        f"### Leaderboard (mean ± std over folds; primary metric **{primary}**, {better} is "
        "better)",
        "",
    ]
    cols = METRICS[task]
    lines.append(
        "| model | family | " + " | ".join(cols) + f" | Δ {primary} vs best baseline | wins |"
    )
    lines.append("|---" * (len(cols) + 4) + "|")
    for name in m["leaderboard"]:
        e = m["models"][name]
        cells = [
            f"{_fmt(c, e['summary'][c]['mean'])} ± {_fmt(c, e['summary'][c]['std'])}"
            if e["summary"][c]["mean"] is not None
            else "n/a"
            for c in cols
        ]
        cmp = e["vs_best_baseline"].get(primary)
        delta = (
            f"{_fmt(primary, cmp['diff_mean'], signed=True)} (vs `{cmp['baseline']}`)"
            if cmp
            else "baseline"
        )
        wins = f"{cmp['wins']}/{cmp['folds']}" if cmp else "-"
        label = f"`{name}`" + (" *(baseline)*" if e["is_baseline"] else "")
        lines.append(f"| {label} | {e['family']} | " + " | ".join(cells) + f" | {delta} | {wins} |")

    # Per era
    lines += ["", f"### {primary} by market era (pooled out-of-sample predictions)", ""]
    eras = sorted({era for e in m["models"].values() for era in e["eras"]})
    lines.append("| model | " + " | ".join(eras) + " |")
    lines.append("|---" * (len(eras) + 1) + "|")
    for name in m["leaderboard"]:
        e = m["models"][name]
        lines.append(
            f"| `{name}` | "
            + " | ".join(_fmt(primary, e["eras"].get(era, {}).get(primary)) for era in eras)
            + " |"
        )

    # Per fold
    lines += ["", f"### {primary} per fold", "", "<details><summary>Show all folds</summary>", ""]
    names = m["leaderboard"]
    lines.append("| fold | test period | " + " | ".join(f"`{n}`" for n in names) + " |")
    lines.append("|---" * (len(names) + 2) + "|")
    for i, f in enumerate(m["folds"]):
        vals = [_fmt(primary, m["models"][n]["per_fold"][primary][i]) for n in names]
        lines.append(
            f"| {f['index']} | {f['test_start'][:10]} → {f['test_end'][:10]} | "
            + " | ".join(vals)
            + " |"
        )
    lines += ["", "</details>"]

    # Calibration
    if task == "classification":
        best = m["verdict"].get("best_model")
        show = [n for n in (best, "historical_up_rate") if n and "calibration" in m["models"][n]]
        lines += ["", "### Calibration (pooled out-of-sample, quantile bins)", ""]
        for name in show:
            lines += [
                f"**`{name}`**",
                "",
                "| bin | n | mean predicted P(up) | observed up-rate | gap |",
                "|---|---|---|---|---|",
            ]
            for b in m["models"][name]["calibration"]:
                lines.append(
                    f"| {b['bin']} | {b['n']} | {b['mean_pred']:.3f} | {b['observed_rate']:.3f} "
                    f"| {b['gap']:+.3f} |"
                )
            lines.append("")

    # Importance
    lines += [
        "",
        f"### Permutation importance on test folds (mean {primary} worsening when shuffled)",
        "",
    ]
    for name in m["leaderboard"]:
        imp = m["models"][name].get("importance")
        if not imp:
            continue
        # JSONB doesn't preserve key order: sort here.
        top = sorted(imp["mean"].items(), key=lambda kv: kv[1], reverse=True)[:5]
        shown = ", ".join(f"`{f}` {v:+.5f} ({imp['share'][f]:.0%})" for f, v in top)
        lines.append(f"- **`{name}`**: {shown}")
        for flag in imp["leak_flags"]:
            lines.append(
                f"  - ⚠️ **possible leak:** `{flag['feature']}` carries {flag['share']:.0%} of all "
                f"positive importance and shuffling it worsens {primary} by "
                f"{(flag['relative_to_score'] or 0):.1%}. Investigate before trusting this model."
            )
        for item in imp.get("dominant_immaterial", []):
            lines.append(
                f"  - `{item['feature']}` holds {item['share']:.0%} of the importance, but the "
                f"effect is tiny ({(item['relative_to_score'] or 0):.2%} of the score): "
                "dominant among near-zero effects, not a leak signal."
            )
        if imp["total_positive"] == 0:
            lines.append("  - no feature improves the test score when present")
    flagged = [
        n for n in m["leaderboard"] if (m["models"][n].get("importance") or {}).get("leak_flags")
    ]
    if not flagged:
        lines += [
            "",
            "No possible leak: no feature is both dominant (> 50% share) and material "
            "(≥ 1% of the score).",
        ]
    return "\n".join(lines) + "\n"


def render_report(runs: list[tuple[TrainingRun, FeatureSet]]) -> str:
    head = [
        "# Experiment report",
        "",
        "<!-- GENERATED by `marketlens experiment-report` from the training_runs table. "
        "Do not edit by hand. -->",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}. "
        "Educational analysis, **not financial advice**. Out-of-sample results only: every "
        "number below comes from days the model had not seen when it predicted them.",
        "",
    ]
    if not runs:
        return "\n".join([*head, "No successful training runs yet: run `make train`.", ""])
    body = [render_task(run, fs) for run, fs in runs]
    guide = [
        "## How to read this report",
        "",
        "- **Baselines first.** A model is only interesting if it beats the best feature-free "
        'baseline on the same folds. "Beats" here means better on average by more than 2 '
        "standard errors *and* in most folds.",
        "- **Spread across folds matters as much as the mean.** A model that wins big in two "
        "folds and loses in the rest is fitting particular regimes, not a stable pattern.",
        '- **Accuracy alone misleads.** With ~52% up-days, saying "up" every day already scores '
        "~52%. Log loss and Brier score also judge *how confident* each prediction was; ROC-AUC "
        "checks whether up-days are ranked above down-days (0.5 = no skill).",
        "- **Calibration.** In each bin, compare *mean predicted P(up)* with the *observed "
        "up-rate*. Close numbers = honest probabilities. If all bins sit near the base rate, the "
        'model has little resolution: it mostly predicts "about average".',
        "- **Permutation importance** = how much the test score drops when a feature is "
        "shuffled. A feature that is both dominant (> 50% of the importance) and material "
        "(shuffling it costs >= 1% of the score) is flagged as a possible leak to investigate.",
        "",
    ]
    return "\n".join([*head, *body, *guide])
