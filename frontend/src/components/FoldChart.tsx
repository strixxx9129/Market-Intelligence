/**
 * Per-fold comparison with the baseline: one bar per walk-forward test fold, in time order.
 * Bar above the line = the model beat the baseline on that fold. A model that "wins on
 * average" thanks to one lucky fold is easy to spot here, and hard to spot in a mean ± std.
 */
import { foldEdges } from '../lib/folds';

export function FoldChart({
  model,
  baseline,
  metric,
  height = 48,
}: {
  model: number[];
  baseline: number[];
  metric: string;
  height?: number;
}) {
  const edges = foldEdges(model, baseline, metric);
  const max = Math.max(...edges.map(Math.abs), 1e-9);
  const wins = edges.filter((e) => e > 0).length;
  const w = 6;
  const gap = 2;
  const mid = height / 2;
  return (
    <figure className="inline-block">
      <svg
        width={edges.length * (w + gap)}
        height={height}
        role="img"
        aria-label={`Beats the baseline on ${wins} of ${edges.length} folds`}
      >
        <line
          x1={0}
          x2={edges.length * (w + gap)}
          y1={mid}
          y2={mid}
          stroke="currentColor"
          opacity={0.3}
        />
        {edges.map((e, i) => {
          const h = (Math.abs(e) / max) * (mid - 2);
          return (
            <rect
              key={i}
              x={i * (w + gap)}
              y={e > 0 ? mid - h : mid}
              width={w}
              height={Math.max(h, 1)}
              className={e > 0 ? 'fill-emerald-600' : 'fill-red-500'}
            >
              <title>{`Fold ${i + 1}: ${e > 0 ? 'better' : 'worse'} than baseline by ${Math.abs(e).toFixed(4)}`}</title>
            </rect>
          );
        })}
      </svg>
      <figcaption className="text-xs text-slate-500 dark:text-slate-400">
        ▲ better on {wins} / ▼ worse on {edges.length - wins} folds
      </figcaption>
    </figure>
  );
}
