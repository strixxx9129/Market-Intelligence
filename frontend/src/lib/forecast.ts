/** Wording rules for forecasts (kept pure so they're easy to test). */

export const COIN_FLIP_BAND = 0.05; // |P(up) - 0.5| below this reads as "close to a coin flip"
export const MIN_TRACK_RECORD = 20; // fewer scored forecasts than this: "not enough data yet"

export function leaning(pUp: number): { direction: 'up' | 'down' | 'flat'; label: string } {
  if (Math.abs(pUp - 0.5) < COIN_FLIP_BAND)
    return { direction: 'flat', label: 'Close to a coin flip' };
  return pUp > 0.5
    ? { direction: 'up', label: 'Leans up' }
    : { direction: 'down', label: 'Leans down' };
}
