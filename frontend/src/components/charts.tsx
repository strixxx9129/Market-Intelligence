/**
 * Thin React wrappers around Lightweight Charts (canvas, fast with thousands of candles).
 * The chart is created once per mount; data and theme changes are applied to the live chart.
 * This module is only imported by lazily loaded pages, so the library stays out of the
 * entry bundle (see vite.config.ts manualChunks).
 */
import {
  CandlestickSeries,
  ColorType,
  createChart,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type SeriesType,
  type Time,
} from 'lightweight-charts';
import { useEffect, useRef, useState } from 'react';

import { useTheme } from '../lib/theme';

export type CandlePoint = { time: string; open: number; high: number; low: number; close: number };
export type LinePoint = { time: string; value: number };
export type LineSpec = { name: string; color: string; data: LinePoint[]; dashed?: boolean };

function themeOptions(isDark: boolean) {
  return {
    layout: {
      background: { type: ColorType.Solid, color: 'transparent' },
      textColor: isDark ? '#cbd5e1' : '#334155',
      attributionLogo: true,
    },
    grid: {
      vertLines: { color: isDark ? '#1e293b' : '#e2e8f0' },
      horzLines: { color: isDark ? '#1e293b' : '#e2e8f0' },
    },
    rightPriceScale: { borderColor: isDark ? '#334155' : '#cbd5e1' },
    timeScale: { borderColor: isDark ? '#334155' : '#cbd5e1' },
  };
}

// Charts whose remove() has run: their series are already gone and must not be touched.
const removed = new WeakSet<IChartApi>();

/**
 * Owns one chart instance. The chart is STATE (not a ref) so effects that add series re-run
 * whenever the instance changes, e.g. React StrictMode mounts, unmounts and remounts every
 * component in development, which destroys the first chart and creates a second one.
 */
function useChart(height: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [chart, setChart] = useState<IChartApi | null>(null);
  const { isDark } = useTheme();

  useEffect(() => {
    if (!ref.current) return;
    const c = createChart(ref.current, { autoSize: true, height });
    setChart(c);
    return () => {
      removed.add(c);
      c.remove();
      setChart(null);
    };
  }, [height]);

  useEffect(() => {
    chart?.applyOptions(themeOptions(isDark));
  }, [chart, isDark]);

  return { ref, chart };
}

/** Add series to `chart`; returns a cleanup that removes them while the chart is alive. */
function attach(chart: IChartApi, series: ISeriesApi<SeriesType>[]): () => void {
  chart.timeScale().fitContent();
  return () => {
    if (!removed.has(chart)) series.forEach((s) => chart.removeSeries(s));
  };
}

export function CandleChart({ data, height = 360 }: { data: CandlePoint[]; height?: number }) {
  const { ref, chart } = useChart(height);

  useEffect(() => {
    if (!chart) return;
    const s = chart.addSeries(CandlestickSeries, {
      upColor: '#059669',
      downColor: '#dc2626',
      borderVisible: false,
      wickUpColor: '#059669',
      wickDownColor: '#dc2626',
    });
    s.setData(data.map((d) => ({ ...d, time: d.time as Time })));
    return attach(chart, [s]);
  }, [chart, data]);

  return <div ref={ref} style={{ height }} className="w-full" />;
}

export function LineChart({
  lines,
  height = 240,
  percent = false,
}: {
  lines: LineSpec[];
  height?: number;
  percent?: boolean;
}) {
  const { ref, chart } = useChart(height);

  useEffect(() => {
    if (!chart) return;
    const series = lines.map((line) => {
      const s = chart.addSeries(LineSeries, {
        color: line.color,
        lineWidth: 2,
        lineStyle: line.dashed ? 2 : 0,
        title: line.name,
        priceFormat: percent
          ? { type: 'custom', formatter: (v: number) => `${(v * 100).toFixed(1)}%` }
          : { type: 'price', precision: 2, minMove: 0.01 },
      });
      s.setData(line.data.map((p) => ({ ...p, time: p.time as Time })));
      return s;
    });
    return attach(chart, series);
  }, [chart, lines, percent]);

  return (
    <div>
      <div ref={ref} style={{ height }} className="w-full" />
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm" aria-label="Legend">
        {lines.map((l) => (
          <li key={l.name} className="flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="inline-block h-0.5 w-5"
              style={{
                borderTop: `3px ${l.dashed ? 'dashed' : 'solid'} ${l.color}`,
              }}
            />
            {l.name}
          </li>
        ))}
      </ul>
    </div>
  );
}
