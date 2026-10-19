import { Link } from 'react-router';

import { EmptyState, PageTitle } from '../components/ui';

export default function NotFound() {
  return (
    <>
      <PageTitle title="Page not found" />
      <EmptyState title="There’s nothing at this address.">
        <Link className="link" to="/">
          Go to the overview
        </Link>
      </EmptyState>
    </>
  );
}
