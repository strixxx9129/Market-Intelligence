import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { Schemas } from '../api/client';
import { ApiError } from '../api/errors';
import { DISCLAIMER } from '../lib/format';
import { renderWithProviders } from '../test/utils';
import { leaning } from '../lib/forecast';
import { ForecastPanel } from './ForecastPanel';

const BACKEND_DISCLAIMER = 'Educational software. Not financial advice.';

function forecast(overrides: Partial<Schemas['ForecastOut']> = {}): Schemas['ForecastOut'] {
  return {
    symbol: 'BTC-USD',
    model_version: 'btc-usd_1d_logistic v3',
    model_version_id: 24,
    model_stage: 'production',
    is_baseline_model: false,
    as_of_ts: '2026-10-18T00:00:00Z',
    target_ts: '2026-10-19T00:00:00Z',
    horizon_days: 1,
    p_up: 0.62,
    y_pred: 1,
    created_at: '2026-10-19T00:20:00Z',
    track_record: {
      window_days: 90,
      n: 45,
      accuracy: 0.53,
      log_loss: 0.69,
      brier: 0.249,
      always_up_accuracy: 0.51,
      coin_flip_log_loss: 0.6931,
      note: 'Scored once the real close is known.',
    },
    disclaimer: BACKEND_DISCLAIMER,
    ...overrides,
  };
}

/** No instruction words anywhere in the rendered panel. */
function expectNoAdvice(container: HTMLElement) {
  expect(container.textContent).not.toMatch(/\b(buy|sell|signal|target price|recommend)/i);
}

describe('ForecastPanel', () => {
  it('shows probability, model version, as-of time and the track record vs baselines', () => {
    const { container } = renderWithProviders(<ForecastPanel forecast={forecast()} />);
    expect(screen.getByText('62%')).toBeInTheDocument();
    expect(screen.getByText(/38% estimated chance it is lower/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'btc-usd_1d_logistic v3' })).toHaveAttribute(
      'href',
      '/models/24',
    );
    expect(screen.getByText(/18 Oct 2026.*UTC/)).toBeInTheDocument();
    expect(screen.getByText('53.0%')).toBeInTheDocument(); // live accuracy
    expect(screen.getByText(/51\.0% \(always guessing “up”\)/)).toBeInTheDocument();
    expect(screen.getByText(/0\.693 \(coin flip\)/)).toBeInTheDocument();
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '62');
    expectNoAdvice(container);
  });

  it('labels direction with an arrow AND words, not colour alone', () => {
    renderWithProviders(<ForecastPanel forecast={forecast({ p_up: 0.62 })} />);
    expect(screen.getByText('Leans up')).toBeInTheDocument();
    expect(screen.getByText('▲')).toBeInTheDocument();
  });

  it('calls a near-50% probability a coin flip', () => {
    renderWithProviders(<ForecastPanel forecast={forecast({ p_up: 0.52 })} />);
    expect(screen.getByText('Close to a coin flip')).toBeInTheDocument();
    expect(leaning(0.46).label).toBe('Close to a coin flip');
    expect(leaning(0.4).label).toBe('Leans down');
  });

  it('says when the track record is too short to judge', () => {
    const f = forecast();
    renderWithProviders(
      <ForecastPanel forecast={{ ...f, track_record: { ...f.track_record, n: 3 } }} />,
    );
    expect(screen.getByText(/Not enough scored forecasts yet/)).toBeInTheDocument();
    expect(screen.queryByText(/coin flip\)/)).not.toBeInTheDocument();
  });

  it('explains a baseline model in production', () => {
    renderWithProviders(<ForecastPanel forecast={forecast({ is_baseline_model: true })} />);
    expect(screen.getByText('baseline', { selector: 'span' })).toBeInTheDocument(); // badge
    expect(screen.getByText(/No machine-learning\s+model has beaten it/)).toBeInTheDocument();
  });

  it('handles a model without a probability', () => {
    const { container } = renderWithProviders(
      <ForecastPanel forecast={forecast({ p_up: null })} />,
    );
    expect(screen.getByText(/doesn’t output a probability/)).toBeInTheDocument();
    expectNoAdvice(container);
  });

  describe('always renders the disclaimer', () => {
    const cases: [string, Parameters<typeof ForecastPanel>[0], string][] = [
      ['with data (backend text)', { forecast: forecast() }, BACKEND_DISCLAIMER],
      ['while loading', { isLoading: true }, DISCLAIMER],
      ['on error', { error: new ApiError(500, 'http_500', 'boom') }, DISCLAIMER],
      ['with no forecast', {}, DISCLAIMER],
      ['with an empty backend disclaimer', { forecast: forecast({ disclaimer: '' }) }, DISCLAIMER],
      ['without a probability', { forecast: forecast({ p_up: null }) }, BACKEND_DISCLAIMER],
    ];
    it.each(cases)('%s', (_name, props, text) => {
      renderWithProviders(<ForecastPanel {...props} />);
      expect(screen.getByTestId('disclaimer')).toHaveTextContent(text);
    });
  });
});
