/** Per-fold comparison helpers for the leaderboard. */

export const HIGHER_IS_BETTER = new Set(['accuracy', 'roc_auc', 'directional_accuracy', 'r2']);

/** Positive = the model beat the baseline on that fold, whatever the metric's direction. */
export function foldEdges(model: number[], baseline: number[], metric: string): number[] {
  const sign = HIGHER_IS_BETTER.has(metric) ? 1 : -1;
  return model.map((m, i) => sign * (m - (baseline[i] ?? m)));
}
