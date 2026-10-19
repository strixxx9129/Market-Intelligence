import { describe, expect, it } from 'vitest';

import { foldEdges } from './folds';
import { directionOf, formatDateTime, formatPercent, formatPrice, timeAgo } from './format';

describe('format', () => {
  it('formats prices, percents and UTC dates', () => {
    expect(formatPrice(82001.58)).toBe('$82,001.58');
    expect(formatPrice(null)).toBe('—');
    expect(formatPercent(0.0123, 2)).toBe('1.23%');
    expect(formatPercent(undefined)).toBe('—');
    expect(formatDateTime('2026-10-18T23:30:00Z')).toBe('18 Oct 2026, 23:30 UTC');
  });

  it('describes direction with a flat band', () => {
    expect(directionOf(0.02)).toBe('up');
    expect(directionOf(-0.02)).toBe('down');
    expect(directionOf(0.001, 0.005)).toBe('flat');
    expect(directionOf(null)).toBe('flat');
  });

  it('says how long ago', () => {
    const now = new Date('2026-10-19T12:00:00Z');
    expect(timeAgo('2026-10-19T11:30:00Z', now)).toBe('30 min ago');
    expect(timeAgo('2026-10-19T02:00:00Z', now)).toBe('10 h ago');
    expect(timeAgo(null, now)).toBe('never');
  });

  it('fold edges are positive when the model beats the baseline', () => {
    expect(foldEdges([0.69, 0.7], [0.7, 0.69], 'log_loss').map((x) => +x.toFixed(2))).toEqual([
      0.01, -0.01,
    ]);
    expect(foldEdges([0.55], [0.5], 'accuracy').map((x) => +x.toFixed(2))).toEqual([0.05]);
  });
});
