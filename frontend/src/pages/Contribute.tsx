import { Card, PageTitle } from '../components/ui';

const GITHUB =
  (import.meta.env.VITE_GITHUB_URL as string | undefined) ??
  'https://github.com/your-org/marketlens';

function issueUrl(title: string, labels: string): string {
  const q = new URLSearchParams({ title, labels });
  return `${GITHUB}/issues/new?${q.toString()}`;
}

const GUIDES = [
  {
    title: 'Add a feature',
    label: 'feature',
    steps: [
      'Write a function in backend/src/marketlens/features/ that takes the candle table and returns one value per day, using only data up to that day.',
      'Register it with @register_feature("name", "description", lookback).',
      'Add it to a feature set version in features/sets.py (never change an existing version: add a new one).',
      'The leakage test runs automatically: it recomputes your feature on truncated data and fails if the past changes.',
      'Run make feature-catalog to update docs/feature-catalog.md.',
    ],
  },
  {
    title: 'Add a model',
    label: 'model',
    steps: [
      'Create a class in backend/src/marketlens/ml/models/ that builds a scikit-learn Pipeline (scalers inside the pipeline, so they are fitted on each training fold only).',
      'Decorate it with @register_model (name, family, task) so training picks it up.',
      'make train scores it on the same walk-forward folds as every other model and compares it with the baselines.',
      'It reaches production only if it beats the baseline on enough folds (the promotion gate).',
    ],
  },
  {
    title: 'Add a data connector',
    label: 'connector',
    steps: [
      'Implement a connector in backend/src/marketlens/ingestion/connectors/ that returns daily candles (UTC timestamps).',
      'Decorate it with @register_connector so the ingestion pipeline can find it by name.',
      'Check the data licence: we only store and redistribute data whose licence allows it.',
      'Add tests with a small fixture file (no network calls in tests).',
    ],
  },
];

export default function Contribute() {
  return (
    <>
      <PageTitle
        title="Contribute"
        subtitle="MarketLens is open source. New features, models and data connectors plug into registries, so you don’t need to change the core."
      />
      <div className="grid gap-6 md:grid-cols-3">
        {GUIDES.map((g) => (
          <Card key={g.label} title={g.title}>
            <ol className="list-decimal space-y-2 pl-5 text-sm">
              {g.steps.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ol>
            <div className="mt-4 flex flex-wrap gap-2">
              <a
                className="btn"
                href={issueUrl(`Proposal: new ${g.label}`, g.label)}
                target="_blank"
                rel="noreferrer"
              >
                Propose a {g.label}
              </a>
              <a
                className="btn-secondary"
                href={`${GITHUB}/issues?q=is%3Aopen+label%3A${g.label}`}
                target="_blank"
                rel="noreferrer"
              >
                Open {g.label} issues
              </a>
            </div>
          </Card>
        ))}
      </div>
      <Card title="Before you start" className="mt-6">
        <ul className="list-disc space-y-1 pl-5 text-sm">
          <li>
            Read{' '}
            <a
              className="link"
              href={`${GITHUB}/blob/main/CONTRIBUTING.md`}
              target="_blank"
              rel="noreferrer"
            >
              CONTRIBUTING.md
            </a>{' '}
            and the architecture decisions in{' '}
            <a
              className="link"
              href={`${GITHUB}/tree/main/docs/adr`}
              target="_blank"
              rel="noreferrer"
            >
              docs/adr
            </a>
            .
          </li>
          <li>
            Good first issues:{' '}
            <a
              className="link"
              href={`${GITHUB}/issues?q=is%3Aopen+label%3A%22good+first+issue%22`}
              target="_blank"
              rel="noreferrer"
            >
              good first issue
            </a>
            .
          </li>
          <li>
            The rules that never bend: no look-ahead, chronological validation only, every job
            re-runnable, no buy/sell advice.
          </li>
        </ul>
      </Card>
    </>
  );
}
