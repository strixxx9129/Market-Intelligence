import { isRouteErrorResponse, Link, useRouteError } from 'react-router';

/** Shown when a page crashes or its code chunk fails to load (e.g. after a deploy). */
export function RouteError() {
  const error = useRouteError();
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : 'Unknown error';
  return (
    <div role="alert" className="mx-auto max-w-xl p-8 text-center">
      <h1 className="text-2xl font-bold">Something went wrong</h1>
      <p className="mt-2 text-slate-600 dark:text-slate-400">{message}</p>
      <div className="mt-4 flex justify-center gap-3">
        <button type="button" className="btn" onClick={() => window.location.reload()}>
          Reload
        </button>
        <Link className="btn-secondary" to="/">
          Home
        </Link>
      </div>
    </div>
  );
}
